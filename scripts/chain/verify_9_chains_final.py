"""选择9条测试链(top3长+中间3+最短3)，逐条验证边正确性 + 读源码确认."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "chain"))

CHAINS_DB = ROOT / "projects" / "org.owasp.webgoat" / "loop_audit" / "jar-analyzer.db"
JAR_DB = ROOT / "projects" / "org.owasp.webgoat" / "loop_audit" / "jar-analyzer.db"
PROJECT_ROOT = Path("D:/code/WebGoat-2025.3")

chains = sqlite3.connect(str(CHAINS_DB))
chains.row_factory = sqlite3.Row

jar = sqlite3.connect(str(JAR_DB))
jar.row_factory = sqlite3.Row

# 按 node_count 排序获取所有链
all_chains = chains.execute(
    "SELECT chain_id, endpoint_fqn, node_path, node_count, priority, total_sinks "
    "FROM chains WHERE status='pending' ORDER BY node_count DESC, chain_id"
).fetchall()
total = len(all_chains)
print(f"总链数: {total}")

# 选择 9 条: top3 最长 + 中间3 + 最短3
mid = total // 2
indices = [0, 1, 2, mid - 1, mid, mid + 1, total - 3, total - 2, total - 1]
indices = [i for i in indices if 0 <= i < total]
# 去重
seen = set()
selected = []
for idx in indices:
    if idx not in seen:
        seen.add(idx)
        selected.append((idx, all_chains[idx]))

print(f"选中 {len(selected)} 条链:\n")

# 预加载所有 method 元数据
all_method_ids = set()
for _, c in selected:
    for nid in c["node_path"].split(" -> "):
        all_method_ids.add(nid.strip())

method_meta = {}
placeholders = ",".join("?" * len(all_method_ids))
for r in jar.execute(
    f"SELECT method_id, class_name, method_name, method_desc, line_number "
    f"FROM method_table WHERE CAST(method_id AS TEXT) IN ({placeholders})",
    list(all_method_ids),
).fetchall():
    method_meta[str(r["method_id"])] = dict(r)

# 预加载边集合
call_edges = set()
for r in jar.execute(
    "SELECT caller_class_name, caller_method_name, caller_method_desc, "
    "callee_class_name, callee_method_name, callee_method_desc "
    "FROM method_call_table"
).fetchall():
    call_edges.add((
        r["caller_class_name"], r["caller_method_name"], r["caller_method_desc"],
        r["callee_class_name"], r["callee_method_name"], r["callee_method_desc"],
    ))

impl_edges = set()
for r in jar.execute(
    "SELECT class_name, method_name, method_desc, impl_class_name "
    "FROM method_impl_table"
).fetchall():
    impl_edges.add((
        r["class_name"], r["method_name"], r["method_desc"],
        r["impl_class_name"],
    ))

all_ok = True
for seq, (idx, chain) in enumerate(selected):
    cid = chain["chain_id"]
    nodes = [n.strip() for n in chain["node_path"].split(" -> ")]
    nc = chain["node_count"]

    print(f"{'='*70}")
    print(f"[{seq+1}/{len(selected)}] idx={idx} {cid} (nodes={nc}, priority={chain['priority']}, sinks={chain['total_sinks']})")
    print(f"{'='*70}")

    chain_ok = True
    for i, nid in enumerate(nodes):
        meta = method_meta.get(nid, {})
        cn = meta.get("class_name", "?")
        mn = meta.get("method_name", "?")
        ln = meta.get("line_number", 0)
        # Convert JVM internal class name to Java source path
        outer = cn.split("$")[0] if cn else ""
        java_rel = outer.replace("/", ".") if outer else ""
        java_file = PROJECT_ROOT / "src" / "main" / "java" / Path(outer + ".java") if outer else None

        print(f"  [{i}] method_id={nid}: {cn.replace('/', '.')}::{mn} (line {ln or '?'})")
        if java_file and java_file.is_file():
            print(f"      source: {java_file.relative_to(PROJECT_ROOT)}")

        if i > 0:
            prev_nid = nodes[i - 1].strip()
            prev_meta = method_meta.get(prev_nid, {})
            pcn = prev_meta.get("class_name", "")
            pmn = prev_meta.get("method_name", "")
            pdesc = prev_meta.get("method_desc", "")

            # Check call edge
            call_key = (
                pcn, pmn, pdesc,
                cn, mn, meta.get("method_desc", ""),
            )
            # Check impl edge (same method name, impl_class_name matches)
            impl_key = (pcn, pmn, pdesc, cn)

            edge_found = call_key in call_edges
            impl_found = any(
                ie in impl_edges for ie in [
                    (pcn, pmn, pdesc, cn),
                ]
            )

            if edge_found:
                print(f"      edge [{i-1}->{i}]: OK (call)")
            elif impl_found:
                print(f"      edge [{i-1}->{i}]: OK (impl)")
            else:
                print(f"      edge [{i-1}->{i}]: *** MISSING *** ({pcn}::{pmn} -> {cn}::{mn})")
                chain_ok = False
                all_ok = False

    print(f"  => {'ALL EDGES OK' if chain_ok else 'CHAIN BROKEN'}\n")

print(f"{'='*70}")
print(f"总结: {'全部 9 条链通过验证' if all_ok else '有断裂链!'}")
print(f"{'='*70}")

chains.close()
jar.close()
