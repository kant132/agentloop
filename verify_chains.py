import sqlite3
conn = sqlite3.connect(r"D:\agentloop\jar-analyzer.db")
conn.row_factory = sqlite3.Row
total = conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]
pending = conn.execute("SELECT COUNT(*) FROM chains WHERE status='pending'").fetchone()[0]
sinks = conn.execute("SELECT COUNT(*) FROM chains WHERE is_sink=1").fetchone()[0]
endpoints = conn.execute("SELECT COUNT(DISTINCT endpoint_fqn) FROM chains").fetchone()[0]
total_sinks = conn.execute("SELECT SUM(total_sinks) FROM chains").fetchone()[0]
print(f"chains: {total} total, {pending} pending, {sinks} with sinks, {endpoints} endpoints, {total_sinks} total sinks")
rows = conn.execute("SELECT endpoint_fqn, priority, total_sinks, is_sink, node_count, chain_path FROM chains ORDER BY priority DESC LIMIT 5").fetchall()
for r in rows:
    cp = r["chain_path"][:80] if r["chain_path"] else "N/A"
    print(f"  {r['endpoint_fqn']} pri={r['priority']} sinks={r['total_sinks']} is_sink={r['is_sink']} nodes={r['node_count']}")
    print(f"    chain: {cp}...")
# Check route_table too
rt = conn.execute("SELECT COUNT(*) FROM route_table").fetchone()[0]
rt_get = conn.execute("SELECT COUNT(*) FROM route_table WHERE http_method='GET'").fetchone()[0]
rt_post = conn.execute("SELECT COUNT(*) FROM route_table WHERE http_method='POST'").fetchone()[0]
print(f"\nroute_table: {rt} total, GET={rt_get}, POST={rt_post}")
conn.close()
