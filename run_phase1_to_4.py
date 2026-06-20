#!/usr/bin/env python3
"""
run_phase1_to_4.py — 完整执行 Phase 1 → 2 → 3 → 4，只跑前 3 条链。

用法:
    python run_phase1_to_4.py --preset projects/org.owasp.webgoat/preset.json

关键节点加日志，输出到 stderr。
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

# ============================================================
# 路径设置
# ============================================================
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "chain"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "redis"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ast"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("phase1to4")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="完整执行 Phase 1→4")
    parser.add_argument("--preset", required=True, help="preset.json 路径")
    parser.add_argument("--limit", type=int, default=3, help="只跑前 N 条链 (默认 3)")
    args = parser.parse_args()

    # 加载 preset
    with open(args.preset, encoding="utf-8") as f:
        preset = json.load(f)

    gid = preset["groupId"]
    proj = Path(preset["projectRoot"])
    db_path = Path(preset["codegraphDb"])
    loop_dir = Path(preset.get("loopDir", str(proj / "loop_audit")))
    loop_dir.mkdir(parents=True, exist_ok=True)
    exposure_dir = loop_dir / "exposure"
    exposure_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Phase 1→4 完整流程启动")
    log.info("项目: %s", proj)
    log.info("groupId: %s", gid)
    log.info("codegraph: %s", db_path)
    log.info("loop_dir: %s", loop_dir)
    log.info("limit: %d chains", args.limit)
    log.info("=" * 60)

    # ============================================================
    # Phase 1: 暴露面采集
    # ============================================================
    log.info("")
    log.info(">>> Phase 1: 暴露面采集 (9 collectors)")
    t0 = time.time()

    from scripts.exposure.registry import autodiscover, get_all_collectors
    from scripts.exposure.contracts import ExposureContext

    autodiscover()
    collectors = get_all_collectors()
    ctx = ExposureContext(
        project_root=proj,
        group_id=gid,
        loop_audit_dir=loop_dir,
        codegraph_db=db_path,
    )

    phase1_summary = {}
    for asset_type, cls in sorted(collectors.items()):
        instance = cls()
        if not instance.is_available(ctx):
            log.info("  [skip] %s: 不可用", asset_type)
            phase1_summary[asset_type] = 0
            continue
        try:
            t1 = time.time()
            result = instance.collect(ctx)
            elapsed = time.time() - t1
            total = len(result.items)
            cached = result.stats.get("cached", result.stats.get("cached_to_memurai", 0))
            phase1_summary[asset_type] = total
            # 写 JSON
            out_file = exposure_dir / f"{asset_type}.json"
            out_file.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("  [ok] %s: %d 条 (缓存 %d, %.1fs)", asset_type, total, cached, elapsed)
        except Exception as e:
            log.error("  [error] %s: %s", asset_type, e)
            phase1_summary[asset_type] = 0

    # 写 _summary.json
    summary_file = exposure_dir / "_summary.json"
    summary_file.write_text(
        json.dumps(phase1_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("  Phase 1 完成: %.1fs", time.time() - t0)
    log.info("  资产统计: %s", json.dumps(phase1_summary, ensure_ascii=False))

    # ============================================================
    # Phase 2: 调用链构建 + SQLite 存储
    # ============================================================
    log.info("")
    log.info(">>> Phase 2: 调用链构建 (写入 chains.db)")
    t0 = time.time()

    from chain_db import ChainDB
    from chain_builder import build_chain

    # 清空旧链
    chains_db = loop_dir / "chains.db"
    if chains_db.exists():
        chains_db.unlink()
        log.info("  清空旧 chains.db")

    db = ChainDB(chains_db)

    # 从 route.json 取前 N 个端点
    route_file = exposure_dir / "route.json"
    if not route_file.exists():
        log.error("  route.json 不存在，无法构建调用链")
        return 1

    route_data = json.loads(route_file.read_text(encoding="utf-8"))
    routes = route_data.get("items", [])
    log.info("  共 %d 个路由端点，取前 %d 个", len(routes), args.limit)

    built_count = 0
    tried = 0
    for i, route in enumerate(routes):
        if built_count >= args.limit:
            break
        fqn = route.get("fqn", "")
        if not fqn or fqn == "null":
            continue

        tried += 1
        http_method = route.get("http_method", route.get("http_methods", ["ANY"])[0] if isinstance(route.get("http_methods"), list) else "ANY")
        has_params = route.get("has_external_param", False)
        endpoint_label = fqn.split("#")[-1] if "#" in fqn else fqn

        t1 = time.time()
        try:
            result = build_chain(
                entry_fqn=fqn,
                group_id=gid,
                project_root=proj,
                db_path=db_path,
                max_depth=20,
                loop_audit_dir=loop_dir,
            )
            elapsed = time.time() - t1
            total_nodes = result.get("total_nodes", 0)
            total_sinks = result.get("total_sinks", 0)
            preset_sinks = result.get("preset_sink_count", 0)
            cycle = result.get("cycle_detected", False)
            log.info("  [chain %d] %s | nodes=%d sinks=%d preset=%d cycle=%s (%.1fs)",
                     built_count + 1, endpoint_label, total_nodes, total_sinks, preset_sinks, cycle, elapsed)
            if total_nodes > 0:
                built_count += 1
        except LookupError as e:
            log.info("  [skip] %s: codegraph 中找不到", endpoint_label)
        except Exception as e:
            log.error("  [chain] %s: %s", endpoint_label, e)

    log.info("  尝试 %d 个端点，成功构建 %d 条链", tried, built_count)

    # Phase 2 统计
    chain_stats = db.stats()
    log.info("  Phase 2 完成: %.1fs", time.time() - t0)
    log.info("  链统计: %s", json.dumps(chain_stats, ensure_ascii=False))

    # 打印前 3 条链（按优先级）
    batch = db.batch_by_priority(limit=3)
    log.info("  --- 按优先级排序的前 3 条链 ---")
    for j, chain in enumerate(batch):
        log.info("  [%d] priority=%d sinks=%d status=%s",
                 j + 1, chain["priority"], chain["total_sinks"], chain["status"])
        log.info("      chain_path: %s", chain["chain_path"][:120] + "..." if len(chain["chain_path"]) > 120 else chain["chain_path"])
        log.info("      node_path:  %s", chain["node_path"][:120] + "..." if len(chain["node_path"]) > 120 else chain["node_path"])

    # ============================================================
    # Phase 3: AI 分析（模拟）
    # ============================================================
    log.info("")
    log.info(">>> Phase 3: 调用链分析 (模拟 AI 加载方法体)")
    t0 = time.time()

    # 从 chains.db 取前 3 条 pending 链
    pending_chains = db.batch_by_priority(limit=3, status="pending")
    log.info("  待分析链: %d 条", len(pending_chains))

    for k, chain in enumerate(pending_chains):
        chain_id = chain["chain_id"]
        endpoint = chain["endpoint_fqn"]
        node_path = chain["node_path"]
        log.info("  [analyze %d] chain_id=%s endpoint=%s", k + 1, chain_id, endpoint)

        # 模拟 Phase 3: 从 node_path 解析 node_id，从 Memurai 加载方法体
        node_ids = [p.strip() for p in node_path.split("->")]
        log.info("    节点数: %d", len(node_ids))
        log.info("    node_path: %s", node_path[:100] + "..." if len(node_path) > 100 else node_path)

        # 模拟 AI 分析结论
        verdicts = ["vuln", "safe", "inconclusive"]
        verdict = verdicts[k % len(verdicts)]
        log.info("    结论: %s", verdict)

        # 更新链状态
        db.update_status(chain_id, "analyzed")
        log.info("    状态更新: pending → analyzed")

    # Phase 3 统计
    after_stats = db.stats()
    log.info("  Phase 3 完成: %.1fs", time.time() - t0)
    log.info("  链状态: %s", json.dumps(after_stats["by_status"], ensure_ascii=False))

    # ============================================================
    # Phase 4: PoC 验证 + 收敛
    # ============================================================
    log.info("")
    log.info(">>> Phase 4: PoC 验证 (模拟) + 收敛判定")
    t0 = time.time()

    # 模拟 PoC 验证
    analyzed_chains = db.batch_by_priority(limit=3, status="analyzed")
    log.info("  待验证链: %d 条", len(analyzed_chains))

    poc_results = []
    for k, chain in enumerate(analyzed_chains):
        chain_id = chain["chain_id"]
        endpoint = chain["endpoint_fqn"]
        sinks = chain["total_sinks"]

        # 模拟 PoC 结果三态
        if sinks > 0:
            poc_status = "confirmed" if k == 0 else "inconclusive"
        else:
            poc_status = "denied"

        poc_results.append({
            "chain_id": chain_id,
            "endpoint": endpoint,
            "sinks": sinks,
            "poc_status": poc_status,
        })

        # 更新链最终状态
        final_status = "vuln" if poc_status == "confirmed" else "safe" if poc_status == "denied" else "pending"
        db.update_status(chain_id, final_status)
        log.info("  [poc %d] chain_id=%s sinks=%d → %s → status=%s",
                 k + 1, chain_id, sinks, poc_status, final_status)

    # 收敛判定 (4-AND)
    final_stats = db.stats()
    total = final_stats["total_chains"]
    by_status = final_stats["by_status"]
    vuln_count = by_status.get("vuln", 0)
    safe_count = by_status.get("safe", 0)
    analyzed = vuln_count + safe_count
    coverage = analyzed / total if total > 0 else 0

    log.info("  Phase 4 完成: %.1fs", time.time() - t0)
    log.info("")
    log.info("=" * 60)
    log.info("完整流程完成")
    log.info("  Phase 1 资产: %s", json.dumps(phase1_summary, ensure_ascii=False))
    log.info("  Phase 2 链: %d total, %s", total, json.dumps(by_status, ensure_ascii=False))
    log.info("  Phase 3 分析: %d analyzed", analyzed)
    log.info("  Phase 4 PoC: %s", json.dumps(poc_results, ensure_ascii=False))
    log.info("  收敛: coverage=%.1f%% (需 ≥95%%)", coverage * 100)
    log.info("  chains.db: %s", chains_db)
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
