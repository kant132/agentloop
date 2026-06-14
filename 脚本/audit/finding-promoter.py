"""
finding-promoter.py

3x85 写盘门槛：finding 评分连续 3 次 > 85 后才落盘到文件。

> 改用 `memurai-cli.exe`，封装在 `memurai_client.py`。
> 不再使用 pip install redis。

用法:
    # 评分（外部 agent 调用）
    python finding-promoter.py --chain-id <id> --score 88 --round 1

    # 检查并落盘
    python finding-promoter.py --check --chain-id <id>
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "redis"))
from memurai_client import Memurai, MemuraiError


SCORE_THRESHOLD = 85
PROMOTE_AFTER = 3  # 连续 3 次 > 85 才落盘


def make_score_key(group_id: str, commit: str, chain_id: str, round_n: int) -> str:
    return f"audit:{group_id}:commit:{commit}:score:{chain_id}:r{round_n}"


def make_finding_key(group_id: str, commit: str, chain_id: str, status: str) -> str:
    return f"audit:{group_id}:commit:{commit}:finding:{chain_id}:{status}"


def record_score(
    cli: Memurai,
    group_id: str,
    commit: str,
    chain_id: str,
    round_n: int,
    score: int,
) -> dict:
    """记录本轮评分"""
    key = make_score_key(group_id, commit, chain_id, round_n)
    cli.set(key, str(int(score)))
    return {"key": key, "score": score}


def check_and_promote(
    cli: Memurai,
    group_id: str,
    commit: str,
    chain_id: str,
    output_dir: str,
) -> dict:
    """检查最近 3 轮评分，全部 > 85 则落盘"""
    # 取最近 PROMOTE_AFTER 轮评分
    scores = []
    for r_n in range(max(0, 30 - PROMOTE_AFTER), 30):  # 检查最近 30 轮
        key = make_score_key(group_id, commit, chain_id, r_n)
        val = cli.get(key)
        if val:
            try:
                scores.append(int(val))
            except ValueError:
                continue

    if len(scores) < PROMOTE_AFTER:
        return {
            "promoted": False,
            "reason": f"评分记录不足 {PROMOTE_AFTER} 轮（当前 {len(scores)}）",
            "scores": scores,
        }

    last_n = scores[-PROMOTE_AFTER:]
    if all(s > SCORE_THRESHOLD for s in last_n):
        # 落盘
        draft_key = make_finding_key(group_id, commit, chain_id, "draft")
        finding_data = cli.get(draft_key)
        if not finding_data:
            return {"promoted": False, "reason": "no draft finding", "scores": last_n}

        # 写文件
        finding = json.loads(finding_data)
        finding["status"] = "final"
        finding["promoted_at_round"] = scores.index(last_n[-1]) + 1

        output_path = Path(output_dir) / f"{chain_id}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(finding, f, ensure_ascii=False, indent=2)

        # 标记 Memurai final
        final_key = make_finding_key(group_id, commit, chain_id, "final")
        cli.set(final_key, json.dumps(finding, ensure_ascii=False))

        return {
            "promoted": True,
            "output_file": str(output_path),
            "scores": last_n,
        }

    return {
        "promoted": False,
        "reason": f"最近 {PROMOTE_AFTER} 轮评分未全部 > {SCORE_THRESHOLD}",
        "scores": last_n,
    }


def main():
    parser = argparse.ArgumentParser(description="finding 评分与 3x85 落盘决策")
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--chain-id", help="chain ID")
    parser.add_argument("--round", type=int, help="轮次（评分时必填）")
    parser.add_argument("--score", type=int, help="评分（评分时必填）")
    parser.add_argument("--check", action="store_true", help="检查并落盘")
    parser.add_argument("--output-dir", default="./findings", help="落盘目录")
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

    if args.check:
        if not args.chain_id:
            print("ERROR: --check 必须指定 --chain-id", file=sys.stderr)
            sys.exit(1)
        result = check_and_promote(cli, args.group_id, args.commit, args.chain_id, args.output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["promoted"] else 0)
    elif args.round is not None and args.score is not None and args.chain_id:
        result = record_score(cli, args.group_id, args.commit, args.chain_id, args.round, args.score)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("ERROR: 必须指定 --check 或 --chain-id/--round/--score", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
