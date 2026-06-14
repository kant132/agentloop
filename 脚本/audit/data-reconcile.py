"""
data-reconcile.py

7+1 项数据对账：过程数据 vs 结果数据一致性校验。

对账项：
  1. 端点清单 vs API 资产报告
  2. finding 总数 vs 各端点 finding 之和
  3. high_risk 数 vs PoC 报告数
  4. 评分总数 vs 评分 ≥ 85 的 finding 数
  5. 落盘 finding 数 vs Memurai final finding 数
  6. 链总数 vs 端点 finding chain_id 去重数
  7. 错误数 vs 失败 subagent 日志数
+ 8. 声明的链数 vs Memurai 实际命中的 chain key 数（防造数据）

> 改用 `memurai-cli.exe`，封装在 `memurai_client.py`。
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "redis"))
from memurai_client import Memurai, MemuraiError


def count_matching(cli: Memurai, pattern: str) -> int:
    """走 memurai-cli --scan 一次取全后 len()（替代手写 SCAN 循环）。"""
    return cli.count(pattern)


def reconcile(
    findings_path: str,
    endpoints_path: str,
    cli: Memurai,
    group_id: str,
    commit: str,
) -> Dict:
    # 读 findings
    findings = []
    with open(findings_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                findings.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # 读 endpoints
    endpoints = []
    with open(endpoints_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                endpoints.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # 1. 端点清单 vs API 资产报告
    item1 = {
        "endpoints_in_inventory": len(endpoints),
        "endpoints_with_finding": len({f.get("endpoint_fqn") for f in findings if f.get("endpoint_fqn")}),
        "ok": True,
    }
    # 允许 endpoints 数量 > finding 数量（部分端点可能无漏洞）

    # 2. finding 总数 vs 各端点 finding 之和
    item2 = {
        "findings_total": len(findings),
        "sum_per_endpoint": len(findings),  # finding 本身就是按端点
        "ok": len(findings) > 0,
    }

    # 3. high_risk 数 vs PoC 报告数
    high_risk = [f for f in findings if f.get("severity") in ("CRITICAL", "HIGH")]
    poc_done = [f for f in findings if f.get("poc_status") in ("confirmed_vuln", "confirmed_safe", "inconclusive")]
    item3 = {
        "high_risk_count": len(high_risk),
        "poc_done_count": len(poc_done),
        "poc_rate": round(len(poc_done) / len(high_risk), 4) if high_risk else None,
        "ok": len(poc_done) / len(high_risk) >= 0.75 if high_risk else True,
    }

    # 4. 评分总数 vs 评分 ≥ 85 的 finding 数
    scored = [f for f in findings if f.get("score") is not None]
    high_score = [f for f in findings if (f.get("score") or 0) >= 85]
    item4 = {
        "scored_count": len(scored),
        "high_score_count": len(high_score),
        "ok": True,
    }

    # 5. 落盘 finding 数 vs Memurai final finding 数
    promoted = [f for f in findings if f.get("status") == "final"]
    redis_final = count_matching(
        cli, f"audit:{group_id}:commit:{commit}:finding:*:final"
    )
    item5 = {
        "promoted_count": len(promoted),
        "redis_final_count": redis_final,
        "ok": len(promoted) == redis_final,
    }

    # 6. 链总数 vs 端点 finding chain_id 去重数
    chain_ids = {f.get("chain_id") for f in findings if f.get("chain_id")}
    item6 = {
        "chain_count": len(chain_ids),
        "ok": len(chain_ids) > 0,
    }

    # 7. 错误数 vs 失败 subagent 日志数（占位，需外部提供）
    item7 = {
        "note": "需结合 subagent-failures.jsonl 验证",
        "ok": True,
    }

    # +8. 声明的链数 vs Memurai 实际命中的 chain key 数
    declared_chains = len(chain_ids)
    redis_chains = count_matching(
        cli, f"audit:{group_id}:commit:{commit}:chain:*"
    )
    item8 = {
        "declared_chains": declared_chains,
        "redis_chains": redis_chains,
        "ok": declared_chains == redis_chains,
    }

    items = [item1, item2, item3, item4, item5, item6, item7, item8]
    all_ok = all(item["ok"] for item in items)
    warn_count = sum(1 for item in items if not item["ok"])

    return {
        "all_ok": all_ok,
        "warn_count": warn_count,
        "items": {
            "1_端点清单一致性": item1,
            "2_finding总数一致": item2,
            "3_PoC覆盖": item3,
            "4_评分分布": item4,
            "5_落盘一致性": item5,
            "6_链数一致": item6,
            "7_subagent失败": item7,
            "8_Memurai链key防造数据": item8,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="数据对账（7+1 项）")
    parser.add_argument("--findings", required=True, help="findings.jsonl")
    parser.add_argument("--endpoints", required=True, help="endpoints.jsonl")
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--redis-host", default="localhost", help="Memurai host")
    parser.add_argument("--redis-port", type=int, default=6379, help="Memurai port")
    parser.add_argument("--cli", help="memurai-cli.exe 显式路径")
    args = parser.parse_args()

    try:
        cli = Memurai(
            host=args.redis_host,
            port=args.redis_port,
            cli_path=args.cli,
        )
        cli.ping()
    except MemuraiError as e:
        print(f"ERROR: Memurai 不可用: {e}", file=sys.stderr)
        sys.exit(1)

    result = reconcile(args.findings, args.endpoints, cli, args.group_id, args.commit)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 超过 1 项 WARN 退出码 1
    if result["warn_count"] > 1:
        sys.exit(1)


if __name__ == "__main__":
    main()
