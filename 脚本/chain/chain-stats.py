"""
chain-stats.py

对提取的调用链做统计：总数、分类、高危明细。

用法:
    python chain-stats.py --findings findings.jsonl
"""
import argparse
import json
import sys
from collections import Counter, defaultdict


def stats_from_findings(findings_path: str) -> dict:
    stats = {
        "total": 0,
        "by_severity": Counter(),
        "by_vuln_type": Counter(),
        "by_fwd_mode": Counter(),
        "by_poc_status": Counter(),
        "high_risk_chains": [],
    }

    try:
        with open(findings_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                finding = json.loads(line)
                stats["total"] += 1
                stats["by_severity"][finding.get("severity", "UNKNOWN")] += 1
                stats["by_vuln_type"][finding.get("vuln_type", "UNKNOWN")] += 1
                stats["by_fwd_mode"][finding.get("mode", "UNKNOWN")] += 1
                stats["by_poc_status"][finding.get("poc_status", "pending")] += 1

                # 致命 + 严重 → 高危明细
                if finding.get("severity") in ("CRITICAL", "HIGH"):
                    stats["high_risk_chains"].append({
                        "chain_id": finding.get("chain_id"),
                        "endpoint": finding.get("endpoint"),
                        "vuln_type": finding.get("vuln_type"),
                        "severity": finding.get("severity"),
                        "evidence": finding.get("evidence", []),
                    })
    except FileNotFoundError:
        print(f"ERROR: 文件不存在: {findings_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    # Counter 转 dict 便于 JSON 序列化
    stats["by_severity"] = dict(stats["by_severity"])
    stats["by_vuln_type"] = dict(stats["by_vuln_type"])
    stats["by_fwd_mode"] = dict(stats["by_fwd_mode"])
    stats["by_poc_status"] = dict(stats["by_poc_status"])
    return stats


def main():
    parser = argparse.ArgumentParser(description="调用链统计")
    parser.add_argument("--findings", required=True, help="findings.jsonl 路径")
    args = parser.parse_args()

    stats = stats_from_findings(args.findings)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
