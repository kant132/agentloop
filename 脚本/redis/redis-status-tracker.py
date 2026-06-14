"""redis-status-tracker.py

**Memurai 状态标记 schema**（用户 2026-06-13 硬性规定）

3 类键：
1. **文件状态** (per-route-method) - finished/analyzing/pending
2. **source 点统计** - chain count + fatal/critical/high
3. **轮次状态** - per-round progress

用法:
    python redis-status-tracker.py init --group-id com.example
    python redis-status-tracker.py mark-file --fqn com.example.UserController --method search --status finished
    python redis-status-tracker.py mark-sink --chain-id abc123 --type SQLI --cvss 9.3 --line 42
    python redis-status-tracker.py mark-round --round 1 --finished 269 --total 269
    python redis-status-tracker.py summary --round 1
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memurai_client import Memurai, MemuraiError

DEFAULT_GROUP = "org.owasp.webgoat"
DEFAULT_COMMIT = "HEAD"


def _key_file_status(group: str, commit: str, fqn: str, method: str, sig_hash: str) -> str:
    return f"audit:{group}:commit:{commit}:file:{fqn}.{method}#{sig_hash}:status"


def _key_file_chain_count(group: str, commit: str, fqn: str, method: str, sig_hash: str) -> str:
    return f"audit:{group}:commit:{commit}:file:{fqn}.{method}#{sig_hash}:chain_count"


def _key_file_sink_count(group: str, commit: str, fqn: str, method: str, sig_hash: str, sev: str) -> str:
    return f"audit:{group}:commit:{commit}:file:{fqn}.{method}#{sig_hash}:sink_{sev}"


def _key_sink_info(group: str, commit: str, chain_id: str) -> str:
    return f"audit:{group}:commit:{commit}:sink:{chain_id}"


def _key_round_status(group: str, commit: str, round_n: int) -> str:
    return f"audit:{group}:commit:{commit}:round:{round_n}:status"


def _key_round_counters(group: str, commit: str, round_n: int) -> str:
    return f"audit:{group}:commit:{commit}:round:{round_n}:counters"


def _key_global_stats(group: str, commit: str, stat: str) -> str:
    return f"audit:{group}:commit:{commit}:stats:{stat}"


def cmd_init(args):
    """初始化一组键（写空 marker）"""
    cli = Memurai(host=args.host, port=args.port)
    if not cli.ping():
        print("[FAIL] Memurai not reachable", file=sys.stderr)
        return 1
    # 写 env:reachability
    cli.set_json(
        f"audit:{args.group_id}:commit:{args.commit}:env:reachability",
        {
            "ssh": "reachable",
            "http": "reachable",
            "codegraph": "ready",
            "ts": datetime.now().isoformat(timespec="seconds"),
        },
        ex=3600,
    )
    # 初始化全局统计键（= 0）
    for stat in ["fatal", "critical", "high", "medium", "low", "none", "total_endpoints", "total_reports", "poc_verified", "poc_fake"]:
        cli.set(f"audit:{args.group_id}:commit:{args.commit}:stats:{stat}", "0")
    print(f"[OK] Initialized audit:{args.group_id}:commit:{args.commit}:*")
    return 0


def cmd_mark_file(args):
    """标记单个 route-method 文件状态 + 链长 + sink 数"""
    cli = Memurai(host=args.host, port=args.port)
    if not cli.ping():
        return 1
    # 状态
    cli.set(
        _key_file_status(args.group_id, args.commit, args.fqn, args.method, args.sig_hash),
        args.status,
        ex=86400,
    )
    # 链长
    if args.chain_count is not None:
        cli.set(
            _key_file_chain_count(args.group_id, args.commit, args.fqn, args.method, args.sig_hash),
            str(args.chain_count),
            ex=86400,
        )
    # sink 计数（致命/严重/中危等）
    if args.sink_counts:
        for sev, n in args.sink_counts.items():
            cli.set(
                _key_file_sink_count(args.group_id, args.commit, args.fqn, args.method, args.sig_hash, sev),
                str(n),
                ex=86400,
            )
    print(f"[OK] file {args.fqn}.{args.method}#{args.sig_hash} -> {args.status}")
    return 0


def cmd_mark_sink(args):
    """标记 source 点（chain 起点）信息"""
    cli = Memurai(host=args.host, port=args.port)
    if not cli.ping():
        return 1
    info = {
        "type": args.type,
        "cvss": args.cvss,
        "line": args.line,
        "file": args.file,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    cli.set_json(_key_sink_info(args.group_id, args.commit, args.chain_id), info, ex=86400)
    # 更新全局统计
    sev_map = {"致命": "fatal", "严重": "critical", "中": "medium", "低": "low", "无": "none"}
    sev = args.severity or "fatal"
    sev_key = sev_map.get(sev, sev)
    cli.incr(_key_global_stats(args.group_id, args.commit, sev_key), 1)
    cli.incr(_key_global_stats(args.group_id, args.commit, "total_reports"), 1)
    print(f"[OK] sink {args.chain_id} -> {args.type} CVSS={args.cvss}")
    return 0


def cmd_mark_round(args):
    """标记本轮结束 + 计数"""
    cli = Memurai(host=args.host, port=args.port)
    if not cli.ping():
        return 1
    # 轮次状态
    cli.set(
        _key_round_status(args.group_id, args.commit, args.round),
        "finished" if args.finished == args.total else "in_progress",
        ex=86400,
    )
    # 轮次计数
    counters = {
        "finished": args.finished,
        "total": args.total,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    cli.set_json(
        _key_round_counters(args.group_id, args.commit, args.round),
        counters,
        ex=86400,
    )
    # 更新全局统计
    cli.set(_key_global_stats(args.group_id, args.commit, "total_reports"), str(args.finished))
    print(f"[OK] round {args.round} {args.finished}/{args.total}")
    return 0


def cmd_summary(args):
    """汇总：所有 finished / sink / round 状态"""
    cli = Memurai(host=args.host, port=args.port)
    if not cli.ping():
        return 1
    print(f"=== Summary: audit:{args.group_id}:commit:{args.commit} ===\n")

    # 全局统计
    print("--- global stats ---")
    for stat in ["fatal", "critical", "high", "medium", "low", "none", "total_endpoints", "total_reports", "poc_verified", "poc_fake"]:
        val = cli.get(f"audit:{args.group_id}:commit:{args.commit}:stats:{stat}") or "0"
        print(f"  {stat}: {val}")

    # 文件状态
    print("\n--- file statuses (sample) ---")
    file_keys = list(cli.scan_iter(f"audit:{args.group_id}:commit:{args.commit}:file:*:status", count=2000))
    statuses = {}
    for k in file_keys[:50]:
        v = cli.get(k)
        statuses[v] = statuses.get(v, 0) + 1
    for s, c in statuses.items():
        print(f"  {s}: {c}")
    if len(file_keys) > 50:
        print(f"  ... (total {len(file_keys)} files)")

    # 轮次
    print("\n--- round statuses ---")
    round_keys = list(cli.scan_iter(f"audit:{args.group_id}:commit:{args.commit}:round:*:status", count=100))
    for k in sorted(round_keys):
        v = cli.get(k)
        print(f"  {k}: {v}")

    return 0


def cmd_mark_finished_in_file(args):
    """在 PoC .md 文件中加 status: finished 标记 (用户 2026-06-13 硬性规定)"""
    from pathlib import Path
    p = Path(args.path)
    if not p.exists():
        print(f"[FAIL] {p} not found", file=sys.stderr)
        return 1
    text = p.read_text(encoding="utf-8", errors="replace")
    marker = "<!-- status: finished -->"
    if marker in text:
        print(f"[OK] {p.name} already has finished marker")
    else:
        # 在文件末尾加 marker
        new_text = text.rstrip() + f"\n\n{marker}\n"
        p.write_text(new_text, encoding="utf-8")
        print(f"[OK] {p.name} marked finished")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Memurai status tracker for audit loop")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=6379)
    ap.add_argument("--group-id", default=DEFAULT_GROUP)
    ap.add_argument("--commit", default=DEFAULT_COMMIT)

    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize status keys")
    p_mark_file = sub.add_parser("mark-file", help="mark file status")
    p_mark_file.add_argument("--fqn", required=True)
    p_mark_file.add_argument("--method", required=True)
    p_mark_file.add_argument("--sig-hash", required=True)
    p_mark_file.add_argument("--status", choices=["pending", "analyzing", "finished", "failed"], required=True)
    p_mark_file.add_argument("--chain-count", type=int)
    p_mark_file.add_argument("--sink-counts", type=json.loads,
                            help='JSON, e.g. \'{"致命": 1, "严重": 2}\'')

    p_mark_sink = sub.add_parser("mark-sink", help="mark source point")
    p_mark_sink.add_argument("--chain-id", required=True)
    p_mark_sink.add_argument("--type", required=True)
    p_mark_sink.add_argument("--cvss", type=float, required=True)
    p_mark_sink.add_argument("--line", type=int, required=True)
    p_mark_sink.add_argument("--file", required=True)
    p_mark_sink.add_argument("--severity", default="fatal")

    p_mark_round = sub.add_parser("mark-round", help="mark round end")
    p_mark_round.add_argument("--round", type=int, required=True)
    p_mark_round.add_argument("--finished", type=int, required=True)
    p_mark_round.add_argument("--total", type=int, required=True)

    p_mark_fin = sub.add_parser("mark-finished-in-file",
                                  help="add '<!-- status: finished -->' marker to a PoC .md file")
    p_mark_fin.add_argument("--path", required=True)

    sub.add_parser("summary", help="show summary")

    args = ap.parse_args()
    return globals()[f"cmd_{args.cmd.replace('-', '_')}"](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
