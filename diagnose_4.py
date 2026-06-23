#!/usr/bin/env python3
"""诊断 4 个问题的根因"""
import sqlite3
from pathlib import Path

db = Path(r"D:\agentloop\projects\com_macro_mall\loop_audit\jar-analyzer.db")
if not db.exists():
    print(f"DB not found: {db}")
    # Try root
    db = Path(r"D:\agentloop\jar-analyzer.db")
    if not db.exists():
        print(f"DB not found: {db}")
        exit(1)

conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

# Problem 1: route_table
print("=== Problem 1: route_table ===")
rows = conn.execute(
    "SELECT class_name, method_name, framework, http_method, path, base_path, method_path, line_number FROM route_table LIMIT 10"
).fetchall()
for r in rows:
    print(f"  {r['class_name']}#{r['method_name']} fw={r['framework']} http={r['http_method']} path={r['path']} base={r['base_path']} mpath={r['method_path']} line={r['line_number']}")

# Count nulls
total = conn.execute("SELECT COUNT(*) FROM route_table").fetchone()[0]
null_http = conn.execute("SELECT COUNT(*) FROM route_table WHERE http_method IS NULL OR http_method=''").fetchone()[0]
null_path = conn.execute("SELECT COUNT(*) FROM route_table WHERE path IS NULL OR path=''").fetchone()[0]
null_base = conn.execute("SELECT COUNT(*) FROM route_table WHERE base_path IS NULL OR base_path=''").fetchone()[0]
null_mpath = conn.execute("SELECT COUNT(*) FROM route_table WHERE method_path IS NULL OR method_path=''").fetchone()[0]
null_line = conn.execute("SELECT COUNT(*) FROM route_table WHERE line_number IS NULL OR line_number=0").fetchone()[0]
print(f"\n  total={total}, null_http={null_http}, null_path={null_path}, null_base={null_base}, null_mpath={null_mpath}, null_line={null_line}")

# Problem 2: chains table
print("\n=== Problem 2: chains table ===")
try:
    count = conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]
    print(f"  chains: {count} rows")
except Exception as e:
    print(f"  chains table error: {e}")

# Problem 3: method_table duplicates
print("\n=== Problem 3: method_table duplicates ===")
total = conn.execute("SELECT COUNT(*) FROM method_table").fetchone()[0]
dupes = conn.execute(
    "SELECT class_name, method_name, method_desc, COUNT(*) as c FROM method_table GROUP BY class_name, method_name, method_desc HAVING c > 1 LIMIT 5"
).fetchall()
for d in dupes:
    print(f"  {d[0]}#{d[1]} desc={d[2]} count={d[3]}")
unique = conn.execute("SELECT COUNT(DISTINCT class_name || '|' || method_name || '|' || method_desc) FROM method_table").fetchone()[0]
print(f"  total={total}, unique_combos={unique}, duplicates={total - unique}")

# Problem 4: check what's in redis (just check if method_table has line_number for body reading)
print("\n=== Problem 4: method_table line_number for body ===")
with_line = conn.execute("SELECT COUNT(*) FROM method_table WHERE line_number > 0").fetchone()[0]
with_end = conn.execute("SELECT COUNT(*) FROM method_table WHERE end_line > 0").fetchone()[0]
print(f"  with line_number>0: {with_line}/{total}, with end_line>0: {with_end}/{total}")

# Check what _read_method_body returns (sample)
print("\n  Sample method_table with line_number:")
rows = conn.execute(
    "SELECT method_id, class_name, method_name, method_desc, line_number, end_line FROM method_table WHERE line_number > 0 LIMIT 5"
).fetchall()
for r in rows:
    print(f"  id={r[0]} {r[1]}#{r[2]} desc={r[3]} line={r[4]} end={r[5]}")

conn.close()
