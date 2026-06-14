"""
redis-stats.py

统计 Memurai 缓存命中率、写盘次数、内存使用等指标。

> 改用 `memurai-cli.exe`，封装在 `memurai_client.py`。

用法:
    python redis-stats.py --group-id com.example.x
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memurai_client import Memurai, MemuraiError


def count_keys(cli: Memurai, pattern: str) -> int:
    """走 memurai-cli --scan 一次取全后 len()。"""
    return cli.count(pattern)


def get_stats(cli: Memurai, group_id: str) -> dict:
    pattern_method = f"audit:{group_id}:commit:*:method:*"
    pattern_chain = f"audit:{group_id}:commit:*:chain:*"
    pattern_finding_draft = f"audit:{group_id}:commit:*:finding:*:draft"
    pattern_finding_final = f"audit:{group_id}:commit:*:finding:*:final"
    pattern_prefetch = f"audit:{group_id}:commit:*:prefetch:*"

    stats = {
        "method_count": count_keys(cli, pattern_method),
        "chain_count": count_keys(cli, pattern_chain),
        "prefetch_count": count_keys(cli, pattern_prefetch),
        "finding_draft_count": count_keys(cli, pattern_finding_draft),
        "finding_final_count": count_keys(cli, pattern_finding_final),
    }

    # 内存使用（如果可用）
    try:
        info = cli.info("memory")
        # 解析 used_memory_human 行
        m = re.search(r"used_memory_human:\s*(\S+)", info)
        stats["used_memory_human"] = m.group(1) if m else "unknown"
    except Exception:
        stats["used_memory_human"] = "unknown"

    return stats


def main():
    parser = argparse.ArgumentParser(description="Memurai 缓存统计")
    parser.add_argument("--group-id", required=True)
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

    stats = get_stats(cli, args.group_id)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
