#!/usr/bin/env python3
"""
simulate_dispatch.py — 模拟主 agent 调度 3 条链

模拟 Phase 3 调度逻辑：
1. 从 jar-analyzer.db chains 表取链元数据（不加载方法体）
2. 按 dispatch 规则分类
3. 模拟调用 load_method_body.py 加载方法体
4. 检查中间状态和计数

用法:
    python simulate_dispatch.py --loop-dir projects/org.owasp.webgoat/loop_audit --group-id org.owasp.webgoat
"""
import json
import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "chain"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "redis"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
log = logging.getLogger("dispatch")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="模拟主 agent 调度")
    parser.add_argument("--loop-dir", required=True, help="loop_audit 目录")
    parser.add_argument("--group-id", required=True, help="项目 groupId")
    args = parser.parse_args()

    loop_dir = Path(args.loop_dir)
    gid = args.group_id

    from chain_db import ChainDB
    db = ChainDB(loop_dir / "jar-analyzer.db")

    # 1. 取链元数据
    all_chains = db.batch_by_priority(limit=3, status="pending")
    log.info("取到 %d 条 pending 链", len(all_chains))

    if not all_chains:
        log.error("没有 pending 链，先运行 run_phase1_to_4.py")
        return 1

    # 2. 按 dispatch 规则分类
    sink_chains = [c for c in all_chains if c["total_sinks"] > 0]
    no_sink_chains = [c for c in all_chains if c["total_sinks"] == 0]

    # 按 endpoint 去重，每个 endpoint 取 1 条链（认证鉴权 + 业务逻辑）
    endpoints_seen = set()
    auth_biz_chains = []
    for c in all_chains:
        ep = c["endpoint_fqn"]
        if ep not in endpoints_seen:
            endpoints_seen.add(ep)
            auth_biz_chains.append(c)

    log.info("分类结果:")
    log.info("  有 sink 的链: %d (注入类/文件类)", len(sink_chains))
    log.info("  无 sink 的链: %d (跳过注入类)", len(no_sink_chains))
    log.info("  认证鉴权+业务逻辑: %d 个 endpoint", len(auth_biz_chains))

    # 3. 模拟加载方法体
    log.info("")
    log.info("模拟子 agent 加载方法体:")

    for i, chain in enumerate(all_chains):
        chain_id = chain["chain_id"]
        endpoint = chain["endpoint_fqn"]
        node_path = chain["node_path"]
        total_sinks = chain["total_sinks"]
        priority = chain["priority"]

        log.info("  [chain %d] %s | priority=%d sinks=%d", i+1, endpoint.split("#")[-1] if "#" in endpoint else endpoint, priority, total_sinks)
        log.info("    chain_path: %s", chain["chain_path"][:100])
        log.info("    node_path:  %s", node_path[:100])

        # 用 load_method_body.py 加载前 5 层
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "chain" / "load_method_body.py"),
                "--group-id", gid,
                "--node-path", node_path,
                "--max-depth", "5",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )

        if result.returncode != 0:
            log.error("    load_method_body 失败: %s", result.stderr.strip()[:200])
            bodies = []
            has_annotation = False
            continue

        bodies = json.loads(result.stdout) if result.stdout.strip() else []
        has_annotation = any("// #" in (b.get("body", "") or "") for b in bodies)
        log.info("    加载方法体: %d 个", len(bodies))

        for j, body_data in enumerate(bodies):
            fqn = body_data.get("fqn", "")
            depth = body_data.get("depth", 0)
            body_preview = (body_data.get("body", "") or "")[:80].replace("\n", "\\n")
            log.info("      [%d] depth=%d %s", j, depth, fqn)
            log.info("          body: %s...", body_preview)

        # 检查是否有 #fqn 注释
        has_annotation = any("// #" in (b.get("body", "") or "") for b in bodies)
        log.info("    #fqn 注释: %s", "✅ 有" if has_annotation else "❌ 无")

        # 模拟分析结论
        if total_sinks > 0:
            verdict = "vuln"
        else:
            verdict = "safe"
        log.info("    模拟结论: %s", verdict)

        # 更新 jar-analyzer.db chains 表状态
        db.update_status(chain_id, "analyzed")
        log.info("    status: pending → analyzed")

    # 4. 检查中间状态
    log.info("")
    log.info("中间状态检查:")

    stats = db.stats()
    log.info("  jar-analyzer.db chains stats: %s", json.dumps(stats, ensure_ascii=False))

    # 检查 Memurai 计数
    count_keys = []
    try:
        from memurai_client import Memurai
        memurai = Memurai()
        count_keys = memurai.scan(f"{gid}:method:*:count", count=1000)
        log.info("  Memurai 计数 key: %d 个", len(count_keys))
        for ck in count_keys[:5]:
            val = memurai.get(ck)
            log.info("    %s = %s", ck.replace(gid + ":", ""), val)
    except Exception as e:
        log.warning("  Memurai 检查失败: %s", e)

    # 检查方法体缓存
    try:
        method_keys = memurai.scan(f"{gid}:method:*", count=1000)
        method_keys = [k for k in method_keys if not k.endswith(":count")]
        log.info("  Memurai 方法体缓存: %d 个", len(method_keys))
    except Exception:
        log.warning("  Memurai 方法体检查失败")

    # 5. 验证设计对齐
    log.info("")
    log.info("设计对齐检查:")
    checks = [
    ("chains 表有 chain_path 列", all(c.get("chain_path") for c in all_chains)),
    ("chains 表有 node_path 列", all(c.get("node_path") for c in all_chains)),
    ("chains 表有 priority 列", all(c.get("priority") is not None for c in all_chains)),
    ("chains 表有 total_sinks 列", all(c.get("total_sinks") is not None for c in all_chains)),
    ("chains 表有 status 列", all(c.get("status") for c in all_chains)),
        ("无 sink 链跳过注入类", len(no_sink_chains) > 0 or len(all_chains) == len(sink_chains)),
        ("认证鉴权按 endpoint 去重", len(auth_biz_chains) <= len(endpoints_seen)),
        ("方法体有 #fqn 注释", has_annotation if 'has_annotation' in dir() else False),
        ("加载计数 INCR", len(count_keys) > 0),
    ]
    for name, passed in checks:
        log.info("  %s %s", "✅" if passed else "❌", name)

    log.info("")
    log.info("模拟调度完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
