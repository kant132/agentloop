"""
multiloop.py

**10 轮 WebGoat + 2 轮 yudao-cloud**（用户 2026-06-13 指令）。

每轮：opencode session 续上 + 派发窄任务 + 5min 超时 + 校验产物。

策略：
- 优先用 opencode 续 session（用户要求）
- 单轮 5min 硬上限（含 subprocess）
- 每轮完检查 P5.4 不变量
- 失败轮次：记 NACK，但**不**重试（让主 agent 看见失败信号）
- 全部跑完输出汇总
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ===== 硬上限：单次工具调用 5 分钟（用户 2026-06-13 规定）=====
TIMEOUT_PER_ROUND = 300  # 5 min

# ===== 目标项目配置 =====
TARGETS = [
    {
        "name": "WebGoat-2025.3",
        "project_root": r"D:\code\WebGoat-2025.3",
        "db": r"D:\code\WebGoat-2025.3\.codegraph\codegraph.db",
        "group_id": "org.owasp.webgoat",
        "rounds": 10,
        "opencode_session": "ses_1431cc1e8ffeqhGMCdBv541S1X",  # 已存在的 session
    },
    {
        "name": "yudao-cloud",
        "project_root": r"D:\code\yudao-cloud",
        "db": r"D:\code\yudao-cloud\.codegraph\codegraph.db",  # 可能不存在，需要先 init
        "group_id": "cn.iocoder.yudao",
        "rounds": 2,
        "opencode_session": None,  # 新 session
    },
]


def check_codegraph(target: dict) -> dict:
    """验证 codegraph 索引就绪。"""
    db = target["db"]
    if not Path(db).exists():
        return {"ready": False, "reason": f"codegraph.db 不存在: {db}"}
    import sqlite3
    try:
        conn = sqlite3.connect(db)
        n_routes = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='route'").fetchone()[0]
        n_methods = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='method'").fetchone()[0]
        conn.close()
        return {"ready": True, "routes": n_routes, "methods": n_methods}
    except Exception as e:
        return {"ready": False, "reason": str(e)}


def check_p5_4_invariant(target: dict) -> dict:
    """P5.4 校验：高+中低 = 端点数。"""
    if not target.get("last_run_ok"):
        return {"verified": False}
    import subprocess
    proj = target["project_root"]
    audit_dir = Path(proj) / "loop_audit"
    ep_jsonl = audit_dir / "external_endpoints" / "端点.jsonl"
    if not ep_jsonl.exists():
        return {"verified": False, "reason": f"端点 jsonl 不存在: {ep_jsonl}"}
    verifier = Path(r"D:\wiki\good-skill\agentloop\脚本\audit\verify-endpoint-coverage.py")
    if not verifier.exists():
        return {"verified": False, "reason": "verifier 不存在"}
    try:
        proc = subprocess.run(
            ["python", str(verifier),
             "--endpoints", str(ep_jsonl),
             "--high-risk-dir", str(audit_dir / "routes" / "高风险端点"),
             "--mid-low-risk-dir", str(audit_dir / "routes" / "中低险端点")],
            capture_output=True, text=True, timeout=30,
        )
        out = json.loads(proc.stdout) if proc.stdout else {}
        return {
            "verified": out.get("ok", False),
            "invariant_ok": out.get("invariant_ok"),
            "endpoint_count": out.get("total_endpoints"),
            "report_count": out.get("total_reports"),
            "high_risk_count": out.get("high_risk_count"),
            "mid_low_risk_count": out.get("mid_low_risk_count"),
        }
    except Exception as e:
        return {"verified": False, "reason": str(e)}


def run_one_round(target: dict, round_n: int, prior_failures: int) -> dict:
    """跑一轮：opencode (有 session 时) 或直接 batch generator。**5 min 硬上限**。"""
    print(f"\n{'='*70}")
    print(f"[{target['name']}] Round {round_n}/{target['rounds']}")
    print(f"{'='*70}")

    t0 = time.time()
    elapsed_remaining = TIMEOUT_PER_ROUND

    if target.get("opencode_session") and prior_failures == 0:
        # 第一次用 opencode，后续失败用直接
        session = target["opencode_session"]
        instruction = build_opencode_prompt(target, round_n)
        cmd = [
            "opencode", "run", "-s", session,
            "--model", "alibaba-cn/qwen3.7-max",
            "--agent", "Sisyphus - ultraworker",
            "--title", f"{target['name']} round {round_n}",
            instruction,
        ]
        print(f"[opencode] running with 5min cap...")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=TIMEOUT_PER_ROUND,
            )
            print(f"  rc={proc.returncode} ({time.time()-t0:.1f}s)")
            if proc.stdout:
                # 截前 500 字符看
                print(f"  stdout head: {proc.stdout[:500]}")
        except subprocess.TimeoutExpired:
            print(f"  [TIMEOUT] opencode exceeded 5min, killing")
            return {"round": round_n, "ok": False, "reason": "opencode timeout", "elapsed": time.time()-t0}
        except Exception as e:
            print(f"  [ERR] {e}")
            return {"round": round_n, "ok": False, "reason": str(e), "elapsed": time.time()-t0}
    else:
        # 直接用 batch generator（已经在 ≤ 1s 完成）
        print(f"[direct] running batch generator (5min cap)...")
        gen = Path(r"D:\wiki\good-skill\agentloop\脚本\audit\batch-generate-route-reports.py")
        try:
            proc = subprocess.run(
                ["python", str(gen)],
                capture_output=True, text=True, timeout=TIMEOUT_PER_ROUND,
                env={**os.environ, "DB_PATH": target["db"], "OUT_ROOT": target["project_root"] + "/loop_audit"},
            )
            print(f"  rc={proc.returncode} ({time.time()-t0:.1f}s)")
        except subprocess.TimeoutExpired:
            return {"round": round_n, "ok": False, "reason": "batch timeout", "elapsed": time.time()-t0}

    # 校验 P5.4
    print(f"[verify] P5.4 不变量校验...")
    target["last_run_ok"] = True
    inv = check_p5_4_invariant(target)
    print(f"  verified={inv.get('verified')}  endpoint_count={inv.get('endpoint_count')}  report_count={inv.get('report_count')}")
    return {
        "round": round_n,
        "elapsed": time.time() - t0,
        "ok": inv.get("verified", False),
        "invariant": inv,
    }


def build_opencode_prompt(target: dict, round_n: int) -> str:
    """构造 opencode 的窄任务 prompt。**必须**让它真干活。"""
    return f"""# 任务：Java 白盒审计 round {round_n}

**项目**: {target['project_root']}
**codegraph db**: {target['db']}
**groupId**: {target['group_id']}

## 硬性产出要求（P5.4 不变量）

codegraph SQLite 中全部 routes 都必须**有对应 .md 报告**。本轮是 round {round_n}，**必须真干活**。

## 步骤（每步都要执行）

1. **不要先读规则文件**。所有规则已总结在下面：

   - 端点路由 → `loop_audit/external_endpoints/端点.jsonl`
   - 高危（致命+严重）→ `loop_audit/routes/高风险端点/`
   - 中低危（中+低+无）→ `loop_audit/routes/中低险端点/`
   - PoC → `loop_audit/routes/poc/`
   - 文件名: `{{等级}}_{{CVSS4.0}}_{{类型}}_{{fqn}}_{{method}}_{{sigHash}}.md`
   - 5 等级：致命(≥9.0) / 严重(7-8.9) / 中(4-6.9) / 低(0.1-3.9) / 无(0.0)

2. **运行现有脚本生成**（不要自己写）：
   ```bash
   python D:\\wiki\\good-skill\\agentloop\\脚本\\audit\\batch-generate-route-reports.py
   ```

3. **校验 P5.4**：
   ```bash
   python D:\\wiki\\good-skill\\agentloop\\脚本\\audit\\verify-endpoint-coverage.py \\
     --endpoints "{target['project_root']}\\loop_audit\\external_endpoints\\端点.jsonl" \\
     --high-risk-dir "{target['project_root']}\\loop_audit\\routes\\高风险端点" \\
     --mid-low-risk-dir "{target['project_root']}\\loop_audit\\routes\\中低险端点"
   ```
   **退出码必须 = 0**。若不是 0，你**没完成任务**。

4. **写 1 句话**到本轮日志：`loop_audit/loop-log/round{round_n}.md` 写"round {round_n} done: N reports"。

## **不要做**

- **不要读 12 个 rules 文件**（已总结在上面）
- **不要先 codegraph_explore 12 次**（浪费 token）
- **不要写"终态汇总报告"**（不是你的任务）
- **不要解释你能做什么**（直接做）

## 时间预算

5 分钟硬上限。**超时 = 失败**。

## 完成后报告

1. `verify-endpoint-coverage.py` 退出码
2. 报告文件总数
3. 任何阻塞问题

开始！"""


def main():
    parser = argparse.ArgumentParser(description="多轮多目标 loop runner")
    parser.add_argument("--skip-opencode", action="store_true",
                        help="跳过 opencode（直接用 batch generator）")
    args = parser.parse_args()

    if args.skip_opencode:
        for t in TARGETS:
            t["opencode_session"] = None

    overall_log = {"started_at": datetime.now().isoformat(timespec="seconds"), "targets": []}
    for t in TARGETS:
        print(f"\n{'#'*70}")
        print(f"# 目标: {t['name']} (rounds={t['rounds']})")
        print(f"{'#'*70}")

        # 0. codegraph 校验
        cg = check_codegraph(t)
        print(f"[init] codegraph: {cg}")
        if not cg["ready"]:
            print(f"[init] 跳过 {t['name']}: {cg['reason']}")
            overall_log["targets"].append({"name": t["name"], "skipped": cg["reason"]})
            continue

        # 1. 跑 N 轮
        rounds_log = []
        prior_failures = 0
        for n in range(1, t["rounds"] + 1):
            r = run_one_round(t, n, prior_failures)
            rounds_log.append(r)
            if not r["ok"]:
                prior_failures += 1
                print(f"\n[FAIL] round {n} 不通过 (累计 {prior_failures} 次失败)")
                if prior_failures >= 3:
                    print(f"[ABORT] {t['name']} 连续 3 次失败，停止")
                    break
            else:
                prior_failures = 0  # 重置连续失败计数
                print(f"\n[OK] round {n} 通过")

        overall_log["targets"].append({
            "name": t["name"],
            "codegraph": cg,
            "rounds": rounds_log,
        })

    overall_log["ended_at"] = datetime.now().isoformat(timespec="seconds")
    out = Path(r"D:\code\WebGoat-2025.3\loop_audit\loop-log\multiloop-summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(overall_log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[summary] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
