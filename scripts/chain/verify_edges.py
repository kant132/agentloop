"""批量验证链边正确性。先批量取所有方法体，再验证。"""
import json, re, sqlite3, sys
from pathlib import Path

ROOT = Path(r"D:\agentloop")
sys.path.insert(0, str(ROOT / "scripts" / "redis"))
sys.path.insert(0, str(ROOT / "scripts" / "chain"))
from memurai_client import Memurai

CHAINS_DB = ROOT / "projects/org.owasp.webgoat/loop_audit/chains.db"
CODEGRAPH = ROOT / ".." / "code" / "WebGoat-2025.3" / ".codegraph" / "codegraph.db"
GID = "org.owasp.webgoat"
FQN_RE = re.compile(r"//fqn:\s*(\S+)")

conn = sqlite3.connect(CHAINS_DB)
conn.row_factory = sqlite3.Row
cg = sqlite3.connect(f"file:{CODEGRAPH}?mode=ro", uri=True)
cg.row_factory = sqlite3.Row
m = Memurai()

# Step 1: 收集所有需要验证的 node_id
chains = conn.execute("SELECT chain_id, node_path FROM chains WHERE status='pending'").fetchall()
all_node_ids = set()
for chain in chains:
    nodes = [n.strip() for n in chain["node_path"].split("->")]
    for n in nodes:
        all_node_ids.add(n)

print(f"pending chains: {len(chains)}, unique nodes: {len(all_node_ids)}")

# Step 2: 批量取方法体
body_cache = {}
for nid in all_node_ids:
    key = f"{GID}:method:{nid}"
    raw = m.get(key)
    if raw:
        data = json.loads(raw) if isinstance(raw, str) else raw
        body_cache[nid] = data.get("body", "")

print(f"bodies loaded: {len(body_cache)}/{len(all_node_ids)}")

# Step 3: 批量取 qualified_name
qn_cache = {}
placeholders = ",".join("?" * len(all_node_ids))
for r in cg.execute(f"SELECT id, qualified_name FROM nodes WHERE id IN ({placeholders})", tuple(all_node_ids)):
    qn_cache[r["id"]] = r["qualified_name"]

print(f"qualified_names loaded: {len(qn_cache)}")

# Step 4: 验证每条链的每条边
def fqn_to_class(fqn):
    if "::" in fqn:
        parts = fqn.split("::")
        return parts[-2] if len(parts) >= 2 else ""
    parts = fqn.rsplit(".", 1)
    return parts[0].rsplit(".", 1)[-1] if parts else ""

broken_chains = []
total_edges = 0
broken_edges = 0

for chain in chains:
    nodes = [n.strip() for n in chain["node_path"].split("->")]
    for i in range(len(nodes) - 1):
        total_edges += 1
        src, tgt = nodes[i], nodes[i + 1]
        body = body_cache.get(src, "")
        if not body:
            continue  # 无 body 无法验证，保守通过
        fqns = set(FQN_RE.findall(body))
        if not fqns:
            continue  # 无注释，保守通过
        tgt_qn = qn_cache.get(tgt, "")
        tgt_class = fqn_to_class(tgt_qn)
        tgt_method = tgt_qn.split("::")[-1] if "::" in tgt_qn else tgt_qn.rsplit(".", 1)[-1]
        matched = any(tgt_class in f or tgt_method in f for f in fqns)
        if not matched:
            broken_edges += 1
            src_qn = qn_cache.get(src, "?")
            print(f"  BROKEN: {chain['chain_id']}")
            print(f"    [{i}->{i+1}] {src_qn} -> {tgt_qn}")
            print(f"    body fqns: {fqns}")
            broken_chains.append(chain["chain_id"])
            break

print(f"\n=== 结果 ===")
print(f"总边: {total_edges}, 断裂边: {broken_edges}")
print(f"总链: {len(chains)}, 断裂链: {len(broken_chains)}")

if broken_chains:
    ph = ",".join("?" * len(broken_chains))
    conn.execute(f"UPDATE chains SET status='broken' WHERE chain_id IN ({ph})", broken_chains)
    conn.commit()
    print(f"已标记 {len(broken_chains)} 条链为 broken")

conn.close()
cg.close()
