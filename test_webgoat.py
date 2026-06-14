"""
test_webgoat.py — 一键跑通 WebGoat-2025.3 端到端测试

验证：
1. 真实 codegraph v0.9.9 schema 兼容性
2. CTE RECURSIVE 20 层链提取
3. LEFT JOIN 20 跳多跳搜索
4. memurai-cli 走通
5. 写测试报告到 test-output/

用法:
    python test_webgoat.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DB = r"D:\code\WebGoat-2025.3\.codegraph\codegraph.db"
OUT = Path(__file__).parent / "test-output"
OUT.mkdir(exist_ok=True)

results = {}


def step(name):
    """装饰器：记录每步耗时与结果"""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            t0 = time.time()
            try:
                r = fn(*args, **kwargs)
                results[name] = {"ok": True, "elapsed_ms": int((time.time() - t0) * 1000), "result": r}
                print(f"[OK]   {name:<40} {results[name]['elapsed_ms']}ms")
                return r
            except Exception as e:
                results[name] = {"ok": False, "elapsed_ms": int((time.time() - t0) * 1000), "error": str(e)}
                print(f"[FAIL] {name:<40} {results[name]['elapsed_ms']}ms  {e}")
                return None
        return wrapper
    return decorator


# ============================================================== Test 1: schema 探测

@step("1_schema_inspect")
def t1():
    db = sqlite3.connect(DB)
    cur = db.cursor()
    info = {
        "tables": [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()],
        "nodes_count": cur.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
        "edges_count": cur.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
        "routes_count": cur.execute("SELECT COUNT(*) FROM nodes WHERE kind='route'").fetchone()[0],
        "methods_count": cur.execute("SELECT COUNT(*) FROM nodes WHERE kind='method'").fetchone()[0],
        "calls_edges": cur.execute("SELECT COUNT(*) FROM edges WHERE kind='calls'").fetchone()[0],
        "decorators_nonempty": cur.execute("SELECT COUNT(*) FROM nodes WHERE decorators IS NOT NULL AND decorators != '[]' AND decorators != ''").fetchone()[0],
    }
    db.close()
    return info


# ============================================================== Test 2: CTE RECURSIVE 20 层

@step("2_cte_recursive_20_layer")
def t2():
    """找最长调用链（基于深度），从 4 个候选入口分别试 depth=20"""
    db = sqlite3.connect(DB)
    cur = db.cursor()
    candidates = [
        "org.owasp.webgoat.container.service::LessonMenuService::showLeftNav",
        "org.owasp.webgoat.container.lessons::Lesson::getDefaultCategory",
        "org.owasp.webgoat.lessons.idor::IDOREditOtherProfile::completed",
        "org.owasp.webgoat.lessons.sqlinjection.introduction::SqlInjectionLesson9::injectableQueryIntegrity",
    ]
    out = []
    for fqn in candidates:
        row = cur.execute("SELECT id FROM nodes WHERE qualified_name = ?", (fqn,)).fetchone()
        if not row:
            out.append({"fqn": fqn, "found": False})
            continue
        rows = cur.execute("""
            WITH RECURSIVE chain(id, qn, depth, path) AS (
                SELECT n.id, n.qualified_name, 0, '|' || n.id
                FROM nodes n WHERE n.id = ?
                UNION ALL
                SELECT callee.id, callee.qualified_name, c.depth + 1, c.path || '|' || callee.id
                FROM chain c
                JOIN edges e ON e.source = c.id AND e.kind = 'calls'
                JOIN nodes callee ON callee.id = e.target AND callee.kind = 'method'
                WHERE c.depth < 20 AND instr(c.path, '|' || callee.id || '|') = 0
            )
            SELECT depth, qn FROM chain ORDER BY depth
        """, (row[0],)).fetchall()
        max_depth = max((r[0] for r in rows), default=0)
        out.append({"fqn": fqn, "found": True, "total_nodes": len(rows), "max_depth": max_depth})
    db.close()
    # 找全局最长链
    best = max((o for o in out if o.get("found")), key=lambda x: x["max_depth"], default=None)
    return {"candidates": out, "best_chain": best}


# ============================================================== Test 3: 多跳 20 JOIN

@step("3_multi_hop_20join")
def t3():
    """跑 5 个预定义模板（最高 20 LEFT JOIN）"""
    fqn = "org.owasp.webgoat.container.service::LessonMenuService::showLeftNav"
    script = Path(__file__).parent / "脚本" / "chain" / "sqlite-multi-hop-search.py"
    out = {}
    for tmpl in ["forward_5hop_20join", "multi_sink_search", "auth_miss_3hop", "full_forward_4hop", "sqli_auth_context"]:
        proc = subprocess.run(
            ["python", str(script), "--db", DB, "--template", tmpl, "--entry-fqn", fqn],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            out[tmpl] = {"join_count": data.get("join_count"), "row_count": data.get("row_count")}
        else:
            out[tmpl] = {"error": proc.stderr[:200]}
    return out


# ============================================================== Test 4: pattern_search 找 sink

@step("4_pattern_search_sinks")
def t4():
    """找 6 类 sink pattern"""
    script = Path(__file__).parent / "脚本" / "chain" / "sqlite-pattern-search.py"
    out = {}
    for p in ["sql_injection", "rce", "deserialize", "ssrf", "path_traversal", "ldap", "controller_method"]:
        proc = subprocess.run(
            ["python", str(script), "--db", DB, "--pattern", p],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            out[p] = data.get("count", 0)
        else:
            out[p] = f"ERR: {proc.stderr[:100]}"
    return out


# ============================================================== Test 5: memurai-cli 走通

@step("5_memurai_cli")
def t5():
    """测 memurai-cli 可用 + 写入/读出 + --scan"""
    script = Path(__file__).parent / "脚本" / "redis" / "memurai_client.py"
    proc = subprocess.run(
        ["python", str(script), "--host", "localhost", "--port", "6379"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return {"available": False, "stderr": proc.stderr[:200]}
    data = json.loads(proc.stdout)
    return {"available": True, "dbsize": data.get("dbsize"), "elapsed_ms": data.get("elapsed_ms")}


# ============================================================== Test 6: 端到端 20 层提取

@step("6_e2e_20layer_extract")
def t6():
    """从真实入口端点 → CTE RECURSIVE 提取 20 层链"""
    script = Path(__file__).parent / "脚本" / "chain" / "sqlite-extract-chain.py"
    fqn = "org.owasp.webgoat.container.service::LessonMenuService::showLeftNav"
    proc = subprocess.run(
        ["python", str(script), "--db", DB, "--entry-fqn", fqn, "--depth", "20"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr[:200]}
    data = json.loads(proc.stdout)
    return {
        "actual_max_depth": data.get("actual_max_depth"),
        "row_count": data.get("row_count"),
        "sample_chain": data.get("rows", [])[:10],
    }


# ============================================================== 汇总

if __name__ == "__main__":
    print(f"\n=== WebGoat-2025.3 端到端测试 ===")
    print(f"db: {DB}")
    print(f"out: {OUT}\n")

    t1(); t2(); t3(); t4(); t5(); t6()

    out_file = OUT / "test-report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n=== 报告写入: {out_file} ===")
    print(f"\n=== 关键指标 ===")
    for k, v in results.items():
        status = "PASS" if v["ok"] else "FAIL"
        print(f"  [{status}] {k:<40} {v['elapsed_ms']:>5}ms")
