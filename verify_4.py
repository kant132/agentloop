#!/usr/bin/env python3
"""验证 4 个修复"""
import sqlite3, json, subprocess
from pathlib import Path

db = Path(r"D:\agentloop\projects\com_macro_mall\loop_audit\jar-analyzer.db")
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

# Problem 1: route_table
print("=== Problem 1: route_table ===")
rows = conn.execute(
    "SELECT class_name, method_name, http_method, path, base_path, method_path, line_number FROM route_table LIMIT 10"
).fetchall()
for r in rows:
    print(f"  {r['class_name']}#{r['method_name']} http={r['http_method']} path={r['path']} base={r['base_path']} mpath={r['method_path']} line={r['line_number']}")

total = conn.execute("SELECT COUNT(*) FROM route_table").fetchone()[0]
null_http = conn.execute("SELECT COUNT(*) FROM route_table WHERE http_method='REQUEST'").fetchone()[0]
null_path = conn.execute("SELECT COUNT(*) FROM route_table WHERE path='none' OR path='/'").fetchone()[0]
null_mpath = conn.execute("SELECT COUNT(*) FROM route_table WHERE method_path IS NULL").fetchone()[0]
null_line = conn.execute("SELECT COUNT(*) FROM route_table WHERE line_number IS NULL").fetchone()[0]
print(f"\n  total={total}, REQUEST={null_http}, none_path={null_path}, null_mpath={null_mpath}, null_line={null_line}")

# Problem 2: chains table created by jar-analyzer-engine
print("\n=== Problem 2: chains table ===")
count = conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]
print(f"  chains: {count} rows")

# Problem 3: method_table UNIQUE constraint
print("\n=== Problem 3: method_table UNIQUE ===")
total = conn.execute("SELECT COUNT(*) FROM method_table").fetchone()[0]
dupes = conn.execute(
    "SELECT class_name, method_name, method_desc, COUNT(*) as c FROM method_table GROUP BY class_name, method_name, method_desc HAVING c > 1 LIMIT 5"
).fetchall()
print(f"  total={total}, duplicates={len(dupes)}")

# Check if UNIQUE index exists
idx = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='method_table'").fetchone()
has_unique = "UNIQUE" in idx[0] if idx else False
print(f"  UNIQUE constraint in schema: {has_unique}")

# Problem 4: redis body with method signature
print("\n=== Problem 4: redis body ===")
MEMURAI = r"C:\Program Files\Memurai\memurai-cli.exe"
# Get a sample method from chains
chain = conn.execute("SELECT node_path FROM chains WHERE node_path IS NOT NULL LIMIT 1").fetchone()
if chain:
    first_node = chain[0].split(" -> ")[0].strip()
    key = f"com.macro.mall:method:{first_node}"
    raw = subprocess.run([MEMURAI, "GET", key], capture_output=True, text=True, timeout=5).stdout.strip()
    if raw and raw != "(nil)":
        data = json.loads(raw)
        body = data.get("body", "")
        body_lines = body.splitlines()
        print(f"  node_id={first_node}")
        print(f"  body lines={len(body_lines)}, chars={len(body)}")
        print(f"  first 5 lines:")
        for line in body_lines[:5]:
            print(f"    | {line.rstrip()}")
        has_annotation = any(l.strip().startswith("@") for l in body_lines)
        has_signature = any("(" in l and ")" in l for l in body_lines[:5])
        print(f"  has_annotation={has_annotation}, has_signature={has_signature}")
    else:
        print(f"  key not found: {key}")
else:
    print("  no chains found")

conn.close()
