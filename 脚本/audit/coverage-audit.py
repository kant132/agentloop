"""
coverage-audit.py

计算外部端点覆盖率：discovered / (discovered + missed)。

用法:
    python coverage-audit.py --endpoints endpoints.jsonl --ground-truth ground_truth.json
"""
import argparse
import json
import sys


def compute_coverage(endpoints_path: str, ground_truth_path: str = None) -> dict:
    discovered = set()
    with open(endpoints_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ep = json.loads(line)
                fqn = ep.get("handler_fqn", "")
                if fqn:
                    discovered.add(fqn)
            except json.JSONDecodeError:
                continue

    if not ground_truth_path:
        # 无 ground truth，仅返回 discovered 数量
        return {
            "discovered_count": len(discovered),
            "ground_truth_count": None,
            "coverage_rate": None,
            "warning": "no ground truth provided",
        }

    with open(ground_truth_path, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    if isinstance(ground_truth, list):
        truth_set = set(ground_truth)
    else:
        truth_set = set(ground_truth.get("endpoints", []))

    missed = truth_set - discovered
    extra = discovered - truth_set  # 假阳

    total_relevant = len(truth_set | discovered)
    coverage = len(discovered & truth_set) / total_relevant if total_relevant > 0 else 0

    return {
        "discovered_count": len(discovered),
        "ground_truth_count": len(truth_set),
        "missed_count": len(missed),
        "extra_count": len(extra),
        "coverage_rate": round(coverage, 4),
        "missed": list(missed)[:20],  # 仅列前 20
        "extra": list(extra)[:20],
    }


def main():
    parser = argparse.ArgumentParser(description="端点覆盖率审计")
    parser.add_argument("--endpoints", required=True, help="endpoints.jsonl 路径")
    parser.add_argument("--ground-truth", help="ground truth JSON 路径（可选）")
    args = parser.parse_args()

    try:
        result = compute_coverage(args.endpoints, args.ground_truth)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
