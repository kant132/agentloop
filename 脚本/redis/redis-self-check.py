"""
redis-self-check.py

启动时一致性自检：随机抽 50 个 Memurai 中的 method，与 codegraph 真实值比对。
发现不匹配 → 自动失效 + 报告。

> 改用 `memurai-cli.exe`，封装在 `memurai_client.py`。

用法:
    python redis-self-check.py --group-id com.example.x --commit HEAD --codegraph-db codegraph.db

输出:
    {"mismatches": [...], "checked": 50, "passed": N, "failed": M}
"""
import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memurai_client import Memurai, MemuraiError


def self_check(
    cli: Memurai,
    group_id: str,
    commit: str,
    codegraph_db: str,
    sample_size: int = 50,
) -> dict:
    """抽样比对 Memurai 缓存与 codegraph 真实值"""
    # 模式匹配所有 method key
    pattern = f"audit:{group_id}:commit:{commit}:method:*"
    all_keys = list(cli.scan_iter(match=pattern, count=1000))

    if not all_keys:
        return {"checked": 0, "passed": 0, "failed": 0, "mismatches": [], "warning": "no keys found"}

    # 随机抽样
    sample_keys = random.sample(all_keys, min(sample_size, len(all_keys)))

    # 连接 codegraph
    cg_conn = sqlite3.connect(codegraph_db)
    cg_cur = cg_conn.cursor()

    mismatches = []
    passed = 0

    for key in sample_keys:
        cached_raw = cli.get(key)
        if not cached_raw:
            continue

        try:
            cached_data = json.loads(cached_raw)
        except json.JSONDecodeError:
            mismatches.append({"key": key, "fqn": None, "reason": "cache_corrupt_json"})
            cli.delete(key)
            continue

        fqn = cached_data.get("fqn")

        # 从 codegraph 取真实值
        cg_cur.execute("SELECT body, file, line FROM method WHERE fqn = ?", (fqn,))
        row = cg_cur.fetchone()
        if not row:
            mismatches.append({"key": key, "fqn": fqn, "reason": "not_in_codegraph"})
            # 自动失效
            cli.delete(key)
            continue

        truth_body, truth_file, truth_line = row
        cached_body = cached_data.get("body", "")

        if cached_body.strip() != (truth_body or "").strip():
            mismatches.append({
                "key": key,
                "fqn": fqn,
                "reason": "body_mismatch",
                "cached_sha": hash(cached_body),
                "truth_sha": hash(truth_body or ""),
            })
            # 自动失效
            cli.delete(key)
        else:
            passed += 1

    cg_conn.close()

    return {
        "checked": len(sample_keys),
        "passed": passed,
        "failed": len(mismatches),
        "mismatches": mismatches,
    }


def main():
    parser = argparse.ArgumentParser(description="Memurai 缓存一致性自检")
    parser.add_argument("--group-id", required=True, help="项目 groupId")
    parser.add_argument("--commit", default="HEAD", help="commit hash")
    parser.add_argument("--codegraph-db", required=True, help="codegraph SQLite 路径")
    parser.add_argument("--redis-host", default="localhost", help="Memurai host")
    parser.add_argument("--redis-port", type=int, default=6379, help="Memurai port")
    parser.add_argument("--cli", help="memurai-cli.exe 显式路径")
    parser.add_argument("--sample-size", type=int, default=50)
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

    result = self_check(cli, args.group_id, args.commit, args.codegraph_db, args.sample_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 不匹配 > 0 退出码 1，触发编排器警告
    if result["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
