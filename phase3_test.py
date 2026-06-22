#!/usr/bin/env python3
"""Phase 3 test on longest chain (decode, 9 nodes)"""
import sys, json
from pathlib import Path

sys.path.insert(0, 'scripts/chain')
sys.path.insert(0, 'scripts/redis')
from chain_db import ChainDB
from memurai_client import Memurai

GID = "org.owasp.webgoat"
LOOP = Path("projects/org.owasp.webgoat/loop_audit")
db = ChainDB(LOOP / "jar-analyzer.db")
m = Memurai()

c = db.get_chain("b45a10a0b9486325")
nodes = [x.strip() for x in (c["node_path"] or "").split("->") if x.strip()]
total = len(nodes)
selected = nodes if total <= 6 else nodes[:4] + nodes[-2:]

print(f"Chain: {c['endpoint_fqn']}")
print(f"Total nodes: {total}, Loaded: {len(selected)}")
print()

# Load method bodies with correct depth indices
selected = nodes
indices = list(range(total))
if total > 6:
    selected = nodes[:4] + nodes[-2:]
    indices = [0, 1, 2, 3, total - 2, total - 1]

bodies = []
for nid in selected:
    raw = m.get(f"{GID}:method:{nid}")
    if raw:
        data = json.loads(raw) if isinstance(raw, str) else raw
        bodies.append(data)

# Format as plain text with correct depths
prompt_lines = ["方法体数据（共{}层，加载{}层）：".format(total, len(bodies)), ""]
for i, b in enumerate(bodies):
    fqn = b.get("fqn", "?")
    body = b.get("body", "")
    depth = indices[i]
    marker = "  # last method" if depth == total - 1 else ""
    prompt_lines.append(f"=== depth={depth}: {fqn} ==={marker}")
    prompt_lines.append(body)
    prompt_lines.append("")

prompt_text = "\n".join(prompt_lines)
print(prompt_text)
print(f"\nTotal chars: {len(prompt_text)}")
