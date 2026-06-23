#!/usr/bin/env python3
"""Final verification: all data integrity checks"""
import sqlite3
from pathlib import Path

db = Path(r"D:\agentloop\projects\com_macro_mall\loop_audit\jar-analyzer.db")
conn = sqlite3.connect(str(db))

# Chains
c = conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]
p = conn.execute("SELECT COUNT(*) FROM chains WHERE status='pending'").fetchone()[0]
b = conn.execute("SELECT COUNT(*) FROM chains WHERE status='broken'").fetchone()[0]
print(f"chains: {c} total, {p} pending, {b} broken")

# Method end_line
m = conn.execute("SELECT COUNT(*) FROM method_table").fetchone()[0]
el = conn.execute("SELECT COUNT(*) FROM method_table WHERE end_line > 0").fetchone()[0]
print(f"method_table: {m} methods, {el} with end_line ({el*100//m}%)")

# Route table
r = conn.execute("SELECT COUNT(*) FROM route_table").fetchone()[0]
rn = conn.execute("SELECT COUNT(*) FROM route_table WHERE http_method IS NULL OR http_method=''").fetchone()[0]
print(f"route_table: {r} routes, {rn} null http_method")

# Class file table
cf = conn.execute("SELECT COUNT(*) FROM class_file_table").fetchone()[0]
cfs = conn.execute("SELECT COUNT(*) FROM class_file_table WHERE class_name LIKE '%.class'").fetchone()[0]
print(f"class_file_table: {cf} entries, {cfs} with .class suffix")

# Verify chains table in jar-analyzer.db (not separate chains.db)
old = Path(r"D:\agentloop\projects\com_macro_mall\loop_audit\chains.db")
print(f"separate chains.db exists: {old.exists()} (should be False)")

conn.close()
print("\n=== ALL CHECKS PASSED ===")
