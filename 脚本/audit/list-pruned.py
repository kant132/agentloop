"""
list-pruned.py

列出本轮 Loop 被剪枝的端点（含 L1 + L2），供人工复审。

用法:
    python list-pruned.py --pruning-log loop_audit/pruning-log.jsonl
"""
import argparse
import json
import sys
from collections import Counter


def list_pruned(pruning_log_path: str) -> dict:
    if not __import__("os").path.exists(pruning_log_path):
        return {"pruned_count": 0, "warning": "no pruning log", "items": []}

    items = []
    rule_counter = Counter()
    level_counter = Counter()

    try:
        with open(pruning_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    items.append(entry)
                    rule_counter[entry.get("rule_id", "unknown")] += 1
                    level_counter[entry.get("level", "unknown")] += 1
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        return {"error": str(e), "items": []}

    # 按规则分组
    by_rule = {}
    for item in items:
        rule = item.get("rule_id", "unknown")
        by_rule.setdefault(rule, []).append(item)

    return {
        "pruned_count": len(items),
        "by_level": dict(level_counter),
        "by_rule": dict(rule_counter),
        "rules_summary": {
            rule: {
                "count": len(v),
                "examples": v[:3],  # 每规则给 3 个例子
            }
            for rule, v in by_rule.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description="列出被剪枝的端点")
    parser.add_argument("--pruning-log", required=True, help="pruning-log.jsonl 路径")
    args = parser.parse_args()

    result = list_pruned(args.pruning_log)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
