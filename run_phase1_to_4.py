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
    parser.add_argument("--phase", type=int, default=4, help="执行到哪个 Phase (1/2/4, 4=全流程)")
    parser.add_argument("--jar-analyzer-db", help="jar-analyzer.db 路径 (覆盖 preset 中的 jarAnalyzerDb)")
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

    # 连接 Memurai
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "redis"))
    try:
        from memurai_client import Memurai
        memurai = Memurai(timeout=30.0)
        if memurai.ping():
            log.info("Memurai: ✅ 连接成功")
        else:
            log.warning("Memurai: PING 失败，方法体缓存不可用")
            memurai = None
    except Exception as e:
        log.warning("Memurai 连接失败: %s", e)
        memurai = None

    # ============================================================
    # Phase 0: 项目类型检测 + JADX 反编译 + jar-analyzer 构建
    # ============================================================
    log.info("")
    log.info(">>> Phase 0: 项目类型检测 + jar-analyzer 构建")
    t0 = time.time()

    import shutil as _shutil
    import subprocess as _sp

    # 读取 preset 新字段
    jar_analyzer_db = args.jar_analyzer_db or preset.get("jarAnalyzerDb", str(loop_dir / "jar-analyzer.db"))
    target_jar_path = preset.get("targetJarPath", "")

    # 检测项目类型
    src_main_java = proj / "src" / "main" / "java"
    sources_dir = proj / "sources"
    if src_main_java.exists():
        project_type = "source"
        log.info("  项目类型: source (src/main/java 存在)")
    elif sources_dir.exists():
        project_type = "decompiled"
        log.info("  项目类型: decompiled (sources/ 已存在，跳过 JADX)")
    else:
        project_type = "jar-only"
        log.info("  项目类型: jar-only (无源码，需要 JADX 反编译)")

    # JAR-only 项目：JADX 反编译 + codegraph 索引
    if project_type == "jar-only":
        jadx_bin = _shutil.which("jadx")
        if not jadx_bin:
            log.warning("  jadx 未安装，跳过反编译（codegraph-only 模式）")
        else:
            # 确定 target JAR 路径
            if target_jar_path:
                target_jar = Path(target_jar_path)
            else:
                # 自动检测 target/*.jar 或 build/libs/*.jar
                target_jar = None
                for pattern in ("target/*.jar", "build/libs/*.jar"):
                    candidates = list(proj.glob(pattern))
                    if candidates:
                        target_jar = candidates[0]
                        break
            if not target_jar or not target_jar.exists():
                log.warning("  未找到 target JAR，跳过 JADX 反编译")
            else:
                log.info("  JADX 反编译: %s → %s", target_jar, sources_dir)
                try:
                    _jadx_result = _sp.run(
                        [jadx_bin, "-d", str(sources_dir), str(target_jar)],
                        capture_output=True, text=True, timeout=300,
                    )
                    if _jadx_result.returncode != 0:
                        log.warning("  JADX 反编译失败 (exit %d): %s",
                                    _jadx_result.returncode,
                                    (_jadx_result.stderr or "")[:200])
                    else:
                        log.info("  JADX 反编译完成")
                        # codegraph init + index 反编译后的源码
                        _cg_init = _sp.run(
                            ["codegraph", "init", str(sources_dir)],
                            capture_output=True, text=True, timeout=300,
                        )
                        if _cg_init.returncode != 0:
                            log.warning("  codegraph init 失败: %s",
                                        (_cg_init.stderr or "")[:200])
                        _cg_index = _sp.run(
                            ["codegraph", "index"],
                            capture_output=True, text=True, timeout=300,
                            cwd=str(sources_dir),
                        )
                        if _cg_index.returncode != 0:
                            log.warning("  codegraph index 失败: %s",
                                        (_cg_index.stderr or "")[:200])
                except _sp.TimeoutExpired:
                    log.warning("  JADX 反编译超时（300s），跳过")
                except Exception as e:
                    log.warning("  JADX 反编译异常: %s", e)

    # 所有项目：jar-analyzer 构建（可选，失败不阻断）
    jar_analyzer_jar = REPO_ROOT / "tools" / "javaparser" / "jar-analyzer-5.22.jar"
    if target_jar_path and jar_analyzer_jar.exists():
        target_jar = Path(target_jar_path)
        if target_jar.exists():
            log.info("  jar-analyzer 构建: %s", target_jar)
            try:
                _ja_result = _sp.run(
                    ["java", "-jar", str(jar_analyzer_jar), "build", "-j", str(target_jar)],
                    capture_output=True, text=True, timeout=300,
                )
                if _ja_result.returncode != 0:
                    log.warning("  jar-analyzer 构建失败 (exit %d): %s",
                                _ja_result.returncode,
                                (_ja_result.stderr or "")[:200])
                else:
                    # jar-analyzer.db 默认输出到 CWD，复制到 loopDir
                    cwd_db = Path("jar-analyzer.db")
                    target_db = Path(jar_analyzer_db)
                    if cwd_db.exists() and cwd_db.resolve() != target_db.resolve():
                        target_db.parent.mkdir(parents=True, exist_ok=True)
                        _shutil.copy2(str(cwd_db), str(target_db))
                        log.info("  jar-analyzer.db → %s", target_db)
                    else:
                        log.info("  jar-analyzer 构建完成")
            except _sp.TimeoutExpired:
                log.warning("  jar-analyzer 构建超时（300s），跳过")
            except Exception as e:
                log.warning("  jar-analyzer 构建异常: %s", e)
        else:
            log.warning("  targetJarPath 指向的 JAR 不存在: %s", target_jar)
    elif not target_jar_path:
        log.info("  preset 未配置 targetJarPath，跳过 jar-analyzer")
    elif not jar_analyzer_jar.exists():
        log.warning("  jar-analyzer JAR 不存在: %s", jar_analyzer_jar)

    log.info("  Phase 0 完成: %.1fs", time.time() - t0)

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

    # Phase 1 资产摘要（人类验证用）
    log.info("  === Phase 1 资产摘要 ===")
    log.info("  路由数: %d", phase1_summary.get("route", 0))
    log.info("  Filter数: %d", phase1_summary.get("filter", 0))
    log.info("  配置文件数: %d", phase1_summary.get("config", 0))
    log.info("  WAF规则数: %d", phase1_summary.get("waf", 0))
    log.info("  敏感信息数: %d", phase1_summary.get("sensitive_info", 0))

    # ============================================================
    # Phase 2: 调用链构建 + SQLite 存储
    # ============================================================
    log.info("")
    log.info(">>> Phase 2: 调用链构建 (写入 chains.db)")
    t0 = time.time()

    from chain_db import ChainDB
    from chain_builder import build_all_chains_for_endpoint

    # 清空旧链
    chains_db = loop_dir / "chains.db"
    db = ChainDB(chains_db)
    db.clear_all()
    log.info("  清空旧 chains.db")

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
            paths = build_all_chains_for_endpoint(
                entry_fqn=fqn,
                group_id=gid,
                project_root=proj,
                db_path=db_path,
                max_depth=20,
                loop_audit_dir=loop_dir,
                memurai_client=memurai,
                ttl=864000,
                jar_analyzer_db_path=Path(jar_analyzer_db) if jar_analyzer_db else None,
            )
            elapsed = time.time() - t1
            total_nodes = sum(p.get("total_nodes", 0) for p in paths)
            total_sinks = sum(p.get("total_sinks", 0) for p in paths)
            log.info("  [chain %d] %s | paths=%d nodes=%d sinks=%d (%.1fs)",
                     built_count + 1, endpoint_label, len(paths), total_nodes, total_sinks, elapsed)
            if paths:
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
    
    # Phase 2 调用链摘要（人类验证用）
    chain_stats = db.stats()
    top3_longest = db.top_by_node_count(3)
    top3_priority = db.batch_by_priority(3)
    last3 = db.bottom_by_priority(3)
    log.info("  === Phase 2 调用链摘要 ===")
    log.info("  总链数: %d", chain_stats["total_chains"])
    log.info("  端点数: %d", chain_stats["total_endpoints"])
    log.info("  Top 3 最长链:")
    for i, chain in enumerate(top3_longest):
        log.info("    [%d] %s (nodes=%d, sinks=%d)", i + 1, chain["endpoint_fqn"][:50], chain.get("node_count", 1), chain["total_sinks"])
    log.info("  Top 3 最高优先级:")
    for i, chain in enumerate(top3_priority):
        log.info("    [%d] %s (priority=%d, sinks=%d)", i + 1, chain["endpoint_fqn"][:50], chain["priority"], chain["total_sinks"])
    log.info("  Last 3 最低优先级:")
    for i, chain in enumerate(last3):
        log.info("    [%d] %s (priority=%d, sinks=%d)", i + 1, chain["endpoint_fqn"][:50], chain["priority"], chain["total_sinks"])

    # ============================================================
    # Phase 2.5: 链边验证（codegraph 误匹配检测）
    # ============================================================
    log.info("")
    log.info(">>> Phase 2.5: 链边验证")
    t0 = time.time()
    import subprocess as _sp
    _verify_script = REPO_ROOT / "scripts" / "chain" / "verify_edges.py"
    if _verify_script.exists():
        _verify_cmd = [sys.executable, str(_verify_script)]
        if jar_analyzer_db:
            _verify_cmd.extend(["--jar-analyzer-db", str(jar_analyzer_db)])
        _verify_result = _sp.run(
            _verify_cmd,
            capture_output=True, text=True, timeout=300,
        )
        for line in _verify_result.stdout.splitlines():
            if "结果" in line or "总边" in line or "断裂" in line or "标记" in line:
                log.info("  %s", line)
        if _verify_result.returncode != 0:
            log.warning("  验证脚本异常退出: %s", _verify_result.stderr[:200] if _verify_result.stderr else "unknown")
    log.info("  Phase 2.5 完成: %.1fs", time.time() - t0)

    # 重新统计（排除 broken 链）
    chain_stats = db.stats()
    pending_count = len(db.top_sink_chains())
    log.info("  验证后 pending 链: %d", pending_count)

    # Phase 2.5 边验证摘要（人类验证用）
    _mismatch = db.mismatch_stats()
    log.info("  === Phase 2.5 边验证摘要 ===")
    log.info("  高置信链: %d", _mismatch["high_confidence"])
    log.info("  低置信链: %d", _mismatch["low_confidence"])
    log.info("  平均 mismatch_score: %.3f", _mismatch["avg_mismatch_score"])

    # ============================================================
    # Phase 3: AI 分析（主 agent 执行, task() 委派）
    # 注意: task() 是 opencode 全局函数, 只在 agent 会话中可用
    # subprocess 调用时用 --phase 2 跳过 Phase 3/4
    # ============================================================
    if args.phase < 3:
        log.info("  --phase=%d, 跳过 Phase 3/4", args.phase)
        return 0

    log.info("")
    log.info(">>> Phase 3: 调用链分析 (v4 flash, task 委派)")
    t0 = time.time()

    # 选 3 条链：最长 / 最高优先级 / 随机
    import random as _random
    all_pending = db.batch_by_priority(limit=999, status="pending")
    if len(all_pending) >= 3:
        _longest = max(all_pending, key=lambda c: c.get("node_count", 1))
        _highest = max(all_pending, key=lambda c: c.get("priority", 0))
        _remaining = [c for c in all_pending if c["chain_id"] not in (_longest["chain_id"], _highest["chain_id"])]
        _random.seed(42)
        _rand = _random.choice(_remaining) if _remaining else all_pending[2]
        pending_chains = [_longest, _highest, _rand]
    else:
        pending_chains = all_pending
    log.info("  选中 %d 条链: %s", len(pending_chains), [c["chain_id"][:20] for c in pending_chains])

    # 预加载方法体 + 派发审计
    from memurai_client import Memurai as _Memurai
    from load_method_body import load_chain as _load_chain
    _memurai = _Memurai()

    # 加载固定 prompt 模板
    _prompt_template = (REPO_ROOT / "prompts" / "expert-injection.md").read_text(encoding="utf-8")
    # 找到模板正文（--- 之后的 内容）
    _template_body = _prompt_template.split("---", 1)[1] if "---" in _prompt_template else _prompt_template

    for k, chain in enumerate(pending_chains):
        chain_id = chain["chain_id"]
        endpoint = chain["endpoint_fqn"]
        node_path = chain["node_path"]
        node_count = chain.get("node_count", 1)
        log.info("  [analyze %d] chain_id=%s endpoint=%s nodes=%d", k + 1, chain_id, endpoint[:50], node_count)

        # 从 Memurai 加载方法体
        try:
            bodies = _load_chain(_memurai, gid, node_path, 0, 0)
        except LookupError as e:
            log.error("    方法体加载失败: %s", e)
            db.update_status(chain_id, "safe")
            continue

        # 格式化方法体（只填充这部分）
        lines = []
        for b in bodies:
            is_last = b["depth"] == len(bodies) - 1
            tag = "  # last method" if is_last else ""
            lines.append(f"=== depth={b['depth']}: {b['fqn']} ==={tag}")
            lines.append(b["body"])
            lines.append("")
        method_bodies = "\n".join(lines)

        # 端点信息
        endpoint_method = endpoint.split("#")[-1] if "#" in endpoint else endpoint
        class_fqn = endpoint.split("#")[0] if "#" in endpoint else endpoint

        # 从模板填充
        prompt = _template_body
        prompt = prompt.replace("{endpoint_method}", endpoint_method)
        prompt = prompt.replace("{http_method}", "POST")
        prompt = prompt.replace("{path}", endpoint_method)
        prompt = prompt.replace("{class_fqn}", class_fqn)
        prompt = prompt.replace("{auth_required}", "Yes")
        prompt = prompt.replace("{method_bodies}", method_bodies)

        # 派发 task 审计
        task_result = task(
            category="deep",
            description=f"Phase3 audit {chain_id}",
            prompt=prompt,
        )

        # 解析结果
        import re as _re
        _json_match = _re.search(r'\{[^{}]*"verdict"[^{}]*\}', task_result, _re.DOTALL)
        if _json_match:
            result_data = json.loads(_json_match.group())
        else:
            result_data = {"verdict": "inconclusive", "analysis": task_result[:200], "vulnerabilities": []}

        verdict = result_data.get("verdict", "inconclusive")
        log.info("    结论: %s", verdict)

        # 写 agent_results
        db.update_agent_result(chain_id, "injection", result_data)
        status = "safe" if verdict == "safe" else "vuln" if verdict == "vuln" else "analyzed"
        db.update_status(chain_id, status)
        log.info("    状态: pending → %s", status)

    # Phase 3 统计
    after_stats = db.stats()
    log.info("  Phase 3 完成: %.1fs", time.time() - t0)
    log.info("  链状态: %s", json.dumps(after_stats["by_status"], ensure_ascii=False))

    if args.phase < 4:
        log.info("  --phase=%d, 跳过 Phase 4", args.phase)
        # 打印最终统计
        final_stats = db.stats()
        log.info("")
        log.info("=" * 60)
        log.info("Phase 1→%d 完成", args.phase)
        log.info("  链统计: %s", json.dumps(final_stats, ensure_ascii=False))
        log.info("  chains.db: %s", chains_db)
        log.info("=" * 60)
        return 0

    # ============================================================
    # Phase 4: 动态利用（HTTP PoC 对运行中的 WebGoat 发请求）
    # 注意: Phase 4 也需要 task() 上下文, subprocess 调用时用 --phase 2
    # ============================================================
    log.info("")
    log.info(">>> Phase 4: 动态利用 (HTTP PoC)")
    t0 = time.time()

    # 登录 WebGoat 获取 session
    import subprocess as _subprocess
    import urllib.parse as _urlparse
    app_base = preset.get("appBaseUrl", "http://localhost:8080")
    login_url = preset.get("loginUrl", f"{app_base}/login")
    test_user = preset.get("testUser", "guest")
    test_pass = preset.get("testPass", "guest")

    # curl 登录
    _login_cmd = ["curl", "-s", "-D", "-", "-X", "POST", login_url,
                  "-d", f"username={test_user}&password={test_pass}"]
    _login_out = _subprocess.run(_login_cmd, capture_output=True, text=True, timeout=10)
    _cookie = ""
    for line in _login_out.stdout.split("\n"):
        if "Set-Cookie:" in line:
            _cookie = line.split("Set-Cookie: ")[1].split(";")[0]
            break
    log.info("  登录: %s, cookie=%s", login_url, _cookie[:40] + "..." if _cookie else "FAILED")

    # 对 Phase 3 审计过的链发 HTTP 请求
    audited_chains = pending_chains  # Phase 3 选中并审计的 3 条链
    poc_results = []
    for k, chain in enumerate(audited_chains):
        chain_id = chain["chain_id"]
        endpoint = chain["endpoint_fqn"]
        status = chain.get("status", "pending")
        poc_status = "not_applicable"

        # 从 route.json 找对应的 HTTP path
        _route_match = None
        for rt in routes:
            if rt.get("fqn") == endpoint:
                _route_match = rt
                break

        if _route_match and _cookie:
            _path = _route_match.get("path", "")
            _method = _route_match.get("http_method", "GET")
            if _path:
                _url = f"{app_base}{_path}"
                _cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                        "-X", _method, _url, "-H", f"Cookie: {_cookie}"]
                if _method == "POST":
                    _cmd += ["-d", "token=test&secretKey=test&userid_6b=test"]
                try:
                    _resp = _subprocess.run(_cmd, capture_output=True, text=True, timeout=10)
                    http_code = _resp.stdout.strip()
                    poc_status = f"http_{http_code}"
                    log.info("  [poc %d] %s %s → %s", k + 1, _method, _url[:60], http_code)
                except Exception as e:
                    poc_status = f"error: {e}"
                    log.info("  [poc %d] ERROR: %s", k + 1, e)
        else:
            log.info("  [poc %d] %s → 跳过 (无路由或无cookie)", k + 1, endpoint[:40])

        poc_results.append({
            "chain_id": chain_id,
            "endpoint": endpoint,
            "poc_status": poc_status,
        })

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
