# -*- coding: utf-8 -*-
"""cli.py — 暴露面管线统一命令行入口。

用法::

    # 采集全部可用 collector
    python -m scripts.exposure.cli collect \\
        --project /path/to/java \\
        --group-id com.example \\
        --output /path/to/loop_audit

    # 采集单个 asset_type
    python -m scripts.exposure.cli collect --asset-type route ...

    # 综合阶段
    python -m scripts.exposure.cli synthesize --input-dir ... --output ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .contracts import ExposureContext
from .registry import autodiscover, get_all_collectors


def _build_ctx(args: argparse.Namespace) -> ExposureContext:
    jar_analyzer_db = Path(args.jar_analyzer_db) if args.jar_analyzer_db else None
    return ExposureContext(
        project_root=Path(args.project).resolve(),
        group_id=args.group_id,
        loop_audit_dir=Path(args.output).resolve(),
        jar_analyzer_db=jar_analyzer_db,
        ssh_target=args.ssh_target,
        commit_hash=args.commit_hash or "",
    )


def cmd_collect(args: argparse.Namespace) -> int:
    """执行 collector 采集，每个 collector 写一个 JSON。"""
    autodiscover()
    ctx = _build_ctx(args)
    collectors = get_all_collectors()

    if args.asset_type:
        if args.asset_type not in collectors:
            print(
                f"[error] 未知 asset_type: {args.asset_type}；"
                f"可用: {', '.join(sorted(collectors))}",
                file=sys.stderr,
            )
            return 2
        collectors = {args.asset_type: collectors[args.asset_type]}

    if not collectors:
        print("[error] 无任何 collector 已注册", file=sys.stderr)
        return 3

    out_dir = ctx.exposure_dir
    summary = []
    for asset_type, cls in sorted(collectors.items()):
        instance = cls()
        if not instance.is_available(ctx):
            print(f"[skip] {asset_type}: 不可用（离线/工具缺失）")
            summary.append({"asset_type": asset_type, "status": "unavailable"})
            continue

        print(f"[collect] {asset_type} ...")
        try:
            result = instance.collect(ctx)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {asset_type} 采集失败: {exc}", file=sys.stderr)
            summary.append(
                {"asset_type": asset_type, "status": "error", "error": str(exc)}
            )
            continue

        out_file = out_dir / f"{asset_type}.json"
        out_file.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        total = len(result.items)
        degraded = " (降级)" if result.degraded else ""
        print(f"[ok] {asset_type}: {total} 条{degraded} → {out_file}")
        summary.append(
            {
                "asset_type": asset_type,
                "status": "ok",
                "count": total,
                "cached": result.stats.get("cached", result.stats.get("cached_to_memurai", 0)),
                "degraded": result.degraded,
                "errors": len(result.errors),
            }
        )

    summary_file = out_dir / "_summary.json"
    
    # 构建综合统计（按资源类型汇总）
    overview = {}
    total_assets = 0
    total_cached = 0
    for entry in summary:
        if entry.get("status") != "ok":
            continue
        at = entry["asset_type"]
        count = entry["count"]
        total_assets += count
        cached = entry.get("cached", 0)
        total_cached += cached
        overview[at] = {
            "count": count,
            "cached": cached,
            "degraded": entry.get("degraded", False),
        }
    
    full_summary = {
        "overview": {
            "total_assets": total_assets,
            "total_cached": total_cached,
            "by_type": overview,
        },
        "collectors": summary,
    }
    summary_file.write_text(
        json.dumps(full_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[done] 汇总 → {summary_file}")
    print(f"       总资产: {total_assets} | 缓存: {total_cached}")
    for at, info in sorted(overview.items()):
        print(f"       {at}: {info['count']} 条 (缓存 {info['cached']})")
    return 0


def cmd_synthesize(args: argparse.Namespace) -> int:
    """综合阶段：调用 synthesizer。"""
    from .synthesizer import DefaultSynthesizer

    input_dir = Path(args.input_dir).resolve()
    results = []
    for json_file in sorted(input_dir.glob("*.json")):
        if json_file.name == "_summary.json":
            continue
        data = json.loads(json_file.read_text(encoding="utf-8"))
        results.append(data)

    ctx = ExposureContext(
        project_root=Path(args.project).resolve(),
        group_id=args.group_id,
        loop_audit_dir=Path(args.output).resolve(),
    )
    synth = DefaultSynthesizer()
    assets = synth.synthesize(results, ctx)

    out_file = Path(args.output).resolve() / "exposure_assets.json"
    out_file.write_text(
        json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[ok] 综合资产 → {out_file}（{assets.get('total_assets', 0)} 条）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.exposure",
        description="暴露面采集与决策管线 (RFC-0001)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    # 公共参数
    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", required=True, help="Java 项目根目录")
        p.add_argument("--group-id", required=True, help="项目 groupId")
        p.add_argument("--output", required=True, help="loop_audit 输出目录")

    # collect
    p_collect = sub.add_parser("collect", help="执行 collector 采集")
    _add_common(p_collect)
    p_collect.add_argument("--asset-type", help="只跑某一个 collector")
    p_collect.add_argument("--jar-analyzer-db", help="jar-analyzer SQLite 路径")
    p_collect.add_argument("--ssh-target", help="SSH 目标 host:port")
    p_collect.add_argument("--commit-hash", default="", help="commit hash（缓存隔离）")
    p_collect.set_defaults(func=cmd_collect)

    # synthesize
    p_synth = sub.add_parser("synthesize", help="综合阶段（数据清洗）")
    _add_common(p_synth)
    p_synth.add_argument(
        "--input-dir", required=True, help="collector 输出目录（exposure/）"
    )
    p_synth.set_defaults(func=cmd_synthesize)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
