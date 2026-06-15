"""
poc-monitor.py - 后台 PoC 验证调度进程

**职责**：独立后台 daemon，轮询 Memurai 中 finished 状态的 finding，
调度 poc-verify 子 agent 执行验证，结果回写缓存。

**关键约束**（per design-docs/会话记录-最终方案.md §7）:
- 不阻塞主流程：与 Boss / Supervisor / Expert 主循环并行
- 走 Memurai 缓存命名空间：`{groupId}:audit:finding:{chainId}:final` / `:verified`
- 并发上限 2（避免 SSH/Arthas 资源耗尽）
- 单 verifier 硬上限 300s（超时即 kill + 标记 timeout）
- 异常退出 dump 状态到 `monitor-state.json`

**用法**:
    # 后台启动（与 daemon 并行）
    python poc-monitor.py \\
        --project-root D:\\code\\WebGoat-2025.3 \\
        --group-id org.owasp.webgoat

    # 一次性 dry-run（只扫描不派发）
    python poc-monitor.py --group-id org.owasp.webgoat --once --dry-run

退出码:
    0 = 正常退出（SIGINT/SIGTERM 优雅退出）
    1 = Memurai 持续不可用
    2 = 参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 让脚本能找到 redis/memurai_client.py（同项目 scripts/ 布局）
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "redis"))

from memurai_client import Memurai, MemuraiError  # noqa: E402


# ------------------------------------------------------------------ 常量

# 命名空间（per 设计文档 §1.3 / §6.1）
NS_FINDING_FINAL = "{group}:audit:finding:{cid}:final"
NS_FINDING_VERIFIED = "{group}:audit:finding:{cid}:verified"
NS_VERIFY_DONE = "{group}:sup:chain:{cid}:verify_done"
NS_VERIFY_FAILED = "{group}:sup:chain:{cid}:verify_failed"

# 单 verifier 硬上限 5 分钟（per 设计文档 §7.3）
VERIFIER_TTL_SEC = 300
# 心跳日志间隔
HEARTBEAT_INTERVAL_SEC = 60
# 调度时 Memurai 短暂断连重试
MEMURAI_RETRY_BACKOFF_SEC = 2.0
MEMURAI_RETRY_MAX = 5


# ------------------------------------------------------------------ 数据结构

@dataclass
class VerifierJob:
    """单个 verifier 子进程追踪记录"""
    chain_id: str
    fqn: str
    proc: subprocess.Popen
    started_at: float
    state_file: Optional[Path] = None  # opencode 可能落盘的产物路径

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def elapsed(self) -> float:
        return time.time() - self.started_at

    def exceeded_ttl(self) -> bool:
        return self.elapsed() > VERIFIER_TTL_SEC


@dataclass
class MonitorState:
    """用于优雅退出 dump / 启动恢复"""
    group_id: str
    project_root: str
    poll_interval: int
    max_concurrent: int
    started_at: str
    active_jobs: list = field(default_factory=list)
    last_heartbeat: str = ""
    total_polled: int = 0
    total_dispatched: int = 0
    total_verified: int = 0
    total_failed: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ 主类

class PocMonitor:
    """后台 PoC 验证调度器。

    设计要点：
    - 走 memurai_client.Memurai（封装 memurai-cli.exe），不直接 subprocess CLI
    - 派发 verifier 走 subprocess.Popen（非阻塞），限并发 max_concurrent
    - 每 poll_interval 秒扫一次，发现 finished finding 即调度
    - reap() 回收已完成 verifier，写回 verified/failed 状态到 Memurai
    """

    def __init__(
        self,
        group_id: str,
        project_root: Path,
        poll_interval: int = 5,
        max_concurrent: int = 2,
        memurai: Optional[Memurai] = None,
        dry_run: bool = False,
        state_path: Optional[Path] = None,
        log_stderr: bool = True,
    ):
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent 必须 >= 1，当前 {max_concurrent}")
        if poll_interval < 1:
            raise ValueError(f"poll_interval 必须 >= 1，当前 {poll_interval}")

        self.group_id = group_id
        self.project_root = Path(project_root).resolve()
        self.poll_interval = poll_interval
        self.max_concurrent = max_concurrent
        self.dry_run = dry_run
        self.state_path = state_path or (self.project_root / "loop_audit" / "diag" / "monitor-state.json")
        self.log_stderr = log_stderr

        # Memurai 客户端（允许注入便于测试 / 复用现有连接）
        self.memurai = memurai or Memurai()

        # 运行期状态
        self.jobs: list[VerifierJob] = []
        self.state = MonitorState(
            group_id=group_id,
            project_root=str(self.project_root),
            poll_interval=poll_interval,
            max_concurrent=max_concurrent,
            started_at=_now_iso(),
        )
        self._stop_requested = False
        self._last_heartbeat_ts = 0.0

        # 信号处理（在 run_forever 中安装）
        self._installed_signals = False

    # -------------------------------------------------- 日志

    def _log(self, level: str, msg: str) -> None:
        """结构化 stderr 日志（避免和 stdout 抢 opencode 输出）"""
        if not self.log_stderr:
            return
        ts = _now_iso()
        line = f"[{ts}] [{level}] {msg}"
        print(line, file=sys.stderr, flush=True)

    def _info(self, msg: str) -> None:
        self._log("INFO", msg)

    def _warn(self, msg: str) -> None:
        self._log("WARN", msg)

    def _error(self, msg: str) -> None:
        self._log("ERROR", msg)

    # -------------------------------------------------- 信号

    def _install_signal_handlers(self) -> None:
        """Windows + POSIX 都用 SIGINT/SIGTERM 优雅退出"""
        if self._installed_signals:
            return
        try:
            signal.signal(signal.SIGINT, self._on_signal)
            signal.signal(signal.SIGTERM, self._on_signal)
            # Windows 下 BREAK 也会触发 SIGINT
            self._installed_signals = True
            self._info("signal handlers installed (SIGINT/SIGTERM -> graceful shutdown)")
        except (ValueError, OSError) as e:
            self._warn(f"无法安装信号处理: {e}")

    def _on_signal(self, signum, frame) -> None:
        """第一次信号：标记停止并杀子进程；第二次：硬退"""
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        if not self._stop_requested:
            self._stop_requested = True
            self._warn(f"received {sig_name}, starting graceful shutdown (kill running verifiers, dump state)")
            self._kill_all_jobs(reason="shutdown")
        else:
            self._warn(f"received second {sig_name}, forcing exit")
            self._kill_all_jobs(reason="forced-shutdown", sigkill=True)
            sys.exit(130 if signum == signal.SIGINT else 143)

    # -------------------------------------------------- Memurai 操作

    def _key_finding_final(self, chain_id: str) -> str:
        return NS_FINDING_FINAL.format(group=self.group_id, cid=chain_id)

    def _key_finding_verified(self, chain_id: str) -> str:
        return NS_FINDING_VERIFIED.format(group=self.group_id, cid=chain_id)

    def _key_verify_done(self, chain_id: str) -> str:
        return NS_VERIFY_DONE.format(group=self.group_id, cid=chain_id)

    def _key_verify_failed(self, chain_id: str) -> str:
        return NS_VERIFY_FAILED.format(group=self.group_id, cid=chain_id)

    def _scan_finding_keys(self) -> list[str]:
        """Scan 所有 finding final keys（可能含已 verified 的）"""
        pattern = f"{self.group_id}:audit:finding:*:final"
        try:
            return self.memurai.scan(pattern=pattern, count=2000)
        except MemuraiError as e:
            self._warn(f"scan '{pattern}' 失败: {e}")
            return []

    def _is_already_verified(self, chain_id: str) -> bool:
        """已存在 verify_done 标记 或 verified 数据 则跳过

        注：memurai_client.exists() 存在解析 bug（memurai-cli 输出 '1' 而非 '(integer) 1'），
        因此这里用 EXISTS 命令 raw 输出自行解析，避免误判。
        """
        try:
            for key in (self._key_verify_done(chain_id), self._key_finding_verified(chain_id)):
                out = self.memurai._run(["EXISTS", key], check_error=False).strip()
                if out == "1":
                    return True
            return False
        except MemuraiError as e:
            self._warn(f"exists 检查失败 ({chain_id}): {e} -> 假定未验证，继续尝试")
            return False

    def _read_finding(self, chain_id: str) -> Optional[dict]:
        """读 finding JSON，解析失败返回 None"""
        try:
            raw = self.memurai.get(self._key_finding_final(chain_id))
        except MemuraiError as e:
            self._warn(f"GET finding 失败 ({chain_id}): {e}")
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            self._warn(f"finding JSON 解析失败 ({chain_id}): {e}")
            return None

    def _write_verified(self, chain_id: str, payload: dict) -> bool:
        """写回 verified 状态 + 标记 verify_done"""
        try:
            self.memurai.set_json(
                self._key_finding_verified(chain_id), payload, ex=86400
            )
            self.memurai.set(
                self._key_verify_done(chain_id),
                json.dumps({"conclusion": payload.get("conclusion", "unknown"), "ts": _now_iso()}, ensure_ascii=False),
                ex=86400,
            )
            return True
        except MemuraiError as e:
            self._error(f"verified 回写失败 ({chain_id}): {e}")
            return False

    def _write_verify_failed(self, chain_id: str, reason: str, rc: Optional[int] = None) -> None:
        """标记 verify_failed（失败重试 1 次后跳过，per 设计文档 §7.3）"""
        try:
            payload = {"reason": reason, "rc": rc, "ts": _now_iso()}
            self.memurai.set_json(self._key_verify_failed(chain_id), payload, ex=86400)
        except MemuraiError as e:
            self._error(f"verify_failed 标记失败 ({chain_id}): {e}")

    # -------------------------------------------------- 核心：扫描 + 派发

    def poll(self) -> list[str]:
        """Scan Memurai，返回所有 unverified 且 poc_status==finished 的 chain_id 列表。

        过滤逻辑：
        1. scan pattern `{groupId}:audit:finding:*:final`
        2. 解析 finding JSON
        3. 仅保留 `poc_status == "finished"` 的
        4. 跳过已存在 `verify_done` / `verified` 标记的
        """
        all_keys = self._scan_finding_keys()
        if not all_keys:
            return []

        candidates: list[str] = []
        for key in all_keys:
            # 从 key 提取 chain_id: "{group}:audit:finding:{cid}:final"
            prefix = f"{self.group_id}:audit:finding:"
            suffix = ":final"
            if not (key.startswith(prefix) and key.endswith(suffix)):
                continue
            chain_id = key[len(prefix):-len(suffix)]
            if not chain_id:
                continue

            finding = self._read_finding(chain_id)
            if not finding:
                continue
            if finding.get("poc_status") != "finished":
                continue
            if self._is_already_verified(chain_id):
                continue

            candidates.append(chain_id)

        self.state.total_polled += len(candidates)
        return candidates

    def dispatch_verify(self, chain_id: str) -> Optional[VerifierJob]:
        """Spawn `opencode run --skill poc-verify` 子进程。

        命令模板：`opencode run "PoC verify: {fqn}" --skill poc-verify`
        上下文：把 project_root + chain_id + finding 通过 env 传入
        """
        finding = self._read_finding(chain_id)
        if not finding:
            self._warn(f"dispatch_verify: finding JSON 不可读，跳过: {chain_id}")
            return None
        fqn = finding.get("fqn") or finding.get("endpoint") or chain_id

        # 子进程工作目录：项目根（verifier 需要访问源码 / PoC 目录）
        cwd = str(self.project_root)
        prompt = f"PoC verify: {fqn}"

        cmd = [
            "opencode",
            "run",
            prompt,
            "--skill",
            "poc-verify",
        ]
        # 通过 env 注入上下文（让 verifier skill 拿到 chain_id / fqn）
        env = os.environ.copy()
        env["POC_MONITOR_GROUP_ID"] = self.group_id
        env["POC_MONITOR_CHAIN_ID"] = chain_id
        env["POC_MONITOR_FQN"] = fqn
        env["POC_MONITOR_PROJECT_ROOT"] = str(self.project_root)

        if self.dry_run:
            self._info(f"[DRY-RUN] would dispatch: {' '.join(shlex.quote(c) for c in cmd)} (cwd={cwd})")
            return None

        try:
            # 子进程 stdout/stderr 都打 stderr，便于 monitor 主进程查看
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # CREATE_NEW_PROCESS_GROUP: Windows 下允许我们发 CTRL_BREAK_EVENT
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
        except FileNotFoundError as e:
            self._error(f"无法启动 'opencode'（{e}）。请确认 opencode 已安装并在 PATH 中。")
            return None
        except OSError as e:
            self._error(f"Popen 失败 ({chain_id}): {e}")
            return None

        job = VerifierJob(
            chain_id=chain_id,
            fqn=fqn,
            proc=proc,
            started_at=time.time(),
        )
        self.jobs.append(job)
        self.state.total_dispatched += 1
        self._info(f"dispatched verifier: chain_id={chain_id} fqn={fqn} pid={proc.pid}")
        return job

    # -------------------------------------------------- 核心：回收

    def reap(self) -> None:
        """回收已结束 verifier；区分 rc==0 / !=0 / 超时三类处理。

        rc==0 → 读取 verifier stdout 末尾摘要，写 verified 状态
        rc!=0 → 标记 verify_failed
        超时（elapsed > 300s）→ 强杀 + 标记 verify_failed(reason=timeout)
        """
        alive_jobs: list[VerifierJob] = []

        for job in self.jobs:
            # 1) 超时检测
            if job.exceeded_ttl() and job.is_alive():
                self._warn(f"verifier 超时 ({job.elapsed():.1f}s > {VERIFIER_TTL_SEC}s)，强杀: {job.chain_id}")
                self._kill_job(job, reason="ttl-exceeded")
                self._write_verify_failed(job.chain_id, reason="timeout", rc=None)
                self.state.total_failed += 1
                continue

            # 2) 仍在运行 → 保留
            if job.is_alive():
                alive_jobs.append(job)
                continue

            # 3) 已结束 → 回收结果
            rc = job.proc.returncode
            stdout = ""
            stderr = ""
            try:
                stdout, stderr = job.proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                # communicate 也卡死（极少见）→ 强杀
                self._kill_job(job, reason="communicate-hang")
                self._write_verify_failed(job.chain_id, reason="communicate_hang", rc=rc)
                self.state.total_failed += 1
                continue

            if rc == 0:
                # verifier 成功：从 stdout 抽取最末段作为 conclusion
                conclusion = _extract_conclusion(stdout) or "verified"
                payload = {
                    "chain_id": job.chain_id,
                    "fqn": job.fqn,
                    "conclusion": conclusion,
                    "elapsed_sec": round(job.elapsed(), 2),
                    "ts": _now_iso(),
                    "stdout_tail": _tail(stdout, max_chars=2000),
                }
                if self._write_verified(job.chain_id, payload):
                    self.state.total_verified += 1
                    self._info(f"verified: chain_id={job.chain_id} conclusion={conclusion} ({job.elapsed():.1f}s)")
                else:
                    self.state.total_failed += 1
            else:
                self._warn(f"verifier rc={rc}: {job.chain_id}")
                self._write_verify_failed(
                    job.chain_id,
                    reason=f"rc={rc}",
                    rc=rc,
                )
                self.state.total_failed += 1

        self.jobs = alive_jobs

    def _kill_job(self, job: VerifierJob, reason: str) -> None:
        """杀掉单个 verifier 子进程（Windows 用 taskkill /T /F，POSIX 用 SIGTERM）"""
        try:
            if sys.platform == "win32":
                # Windows：taskkill /T /F 杀整棵进程树
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(job.proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                job.proc.terminate()
                try:
                    job.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    job.proc.kill()
        except Exception as e:
            self._warn(f"kill job ({reason}) 失败: {e}")

    def _kill_all_jobs(self, reason: str, sigkill: bool = False) -> None:
        """优雅退出时杀掉所有仍在运行的 verifier"""
        if not self.jobs:
            return
        self._info(f"killing {len(self.jobs)} running verifier(s), reason={reason}")
        for job in list(self.jobs):
            self._kill_job(job, reason=reason)

    # -------------------------------------------------- 心跳

    def _maybe_heartbeat(self) -> None:
        """每 60s 打印一条心跳到 stderr，方便确认进程还活着"""
        now = time.time()
        if now - self._last_heartbeat_ts < HEARTBEAT_INTERVAL_SEC:
            return
        self._last_heartbeat_ts = now
        self.state.last_heartbeat = _now_iso()
        self._info(
            f"heartbeat: alive_jobs={len(self.jobs)} "
            f"polled={self.state.total_polled} "
            f"dispatched={self.state.total_dispatched} "
            f"verified={self.state.total_verified} "
            f"failed={self.state.total_failed}"
        )

    # -------------------------------------------------- 状态持久化

    def dump_state(self) -> None:
        """退出前 dump 状态到 JSON 文件（供 daemon 恢复 / 调试）"""
        try:
            self.state.active_jobs = [
                {
                    "chain_id": j.chain_id,
                    "fqn": j.fqn,
                    "pid": j.proc.pid,
                    "elapsed_sec": round(j.elapsed(), 2),
                }
                for j in self.jobs
            ]
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(self.state.to_json(), encoding="utf-8")
            self._info(f"state dumped: {self.state_path}")
        except Exception as e:
            self._warn(f"dump_state 失败: {e}")

    # -------------------------------------------------- 主循环

    def run_forever(self) -> int:
        """主循环。Ctrl+C / SIGTERM 优雅退出。"""
        self._install_signal_handlers()

        # 启动健康检查
        try:
            if not self.memurai.ping():
                self._error("Memurai PING 未返回 PONG，退出")
                return 1
        except MemuraiError as e:
            self._error(f"Memurai 不可达: {e}")
            return 1

        self._info(
            f"poc-monitor started: group_id={self.group_id} "
            f"project_root={self.project_root} "
            f"poll_interval={self.poll_interval}s max_concurrent={self.max_concurrent} "
            f"dry_run={self.dry_run}"
        )

        while not self._stop_requested:
            try:
                # 1) reap 已完成的 verifier（先回收 → 释放并发槽位）
                self.reap()

                # 2) 若有空闲槽位 → poll → dispatch
                free_slots = self.max_concurrent - len(self.jobs)
                if free_slots > 0 and not self.dry_run:
                    candidates = self.poll()
                    for cid in candidates[:free_slots]:
                        if not self._stop_requested:
                            self.dispatch_verify(cid)

                # 3) 心跳
                self._maybe_heartbeat()

            except MemuraiError as e:
                # Memurai 短暂断连：log + 继续（重连由 memurai_client 内部处理）
                self._warn(f"Memurai 错误（继续）: {e}")
            except Exception as e:
                # 未知错误：log 但不退出（避免单次异常击垮 daemon）
                self._error(f"主循环异常（继续）: {type(e).__name__}: {e}")

            # sleep（分段以便快速响应信号）
            _interruptible_sleep(self.poll_interval, self._stop_requested_check)

        # ---- 退出流程 ----
        self._info("shutting down: killing remaining jobs, dumping state")
        self._kill_all_jobs(reason="run_forever-exit")
        # 回收最后一次
        try:
            self.reap()
        except Exception as e:
            self._warn(f"final reap 异常: {e}")
        self.dump_state()
        self._info("poc-monitor exited cleanly")
        return 0

    def _stop_requested_check(self) -> bool:
        return self._stop_requested

    # -------------------------------------------------- 一次性 dry-run

    def run_once(self) -> int:
        """跑一轮 poll+dispatch+reap 后退出（用于调试 / CI smoke test）"""
        try:
            if not self.memurai.ping():
                self._error("Memurai PING 未返回 PONG")
                return 1
        except MemuraiError as e:
            self._error(f"Memurai 不可达: {e}")
            return 1

        candidates = self.poll()
        self._info(f"once: polled {len(candidates)} candidate(s): {candidates}")

        if self.dry_run:
            for cid in candidates[: self.max_concurrent]:
                self.dispatch_verify(cid)  # dry_run 仅打印，不会真 Popen
            return 0

        for cid in candidates[: self.max_concurrent]:
            self.dispatch_verify(cid)

        # 等所有派发完成（或超时）
        deadline = time.time() + VERIFIER_TTL_SEC + 30
        while self.jobs and time.time() < deadline:
            self.reap()
            if not self.jobs:
                break
            _interruptible_sleep(2, self._stop_requested_check)

        # 最后再 reap 一次（处理边界）
        self.reap()
        self.dump_state()
        return 0


# ------------------------------------------------------------------ 辅助函数

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _interruptible_sleep(total_sec: int, stop_check) -> None:
    """分段 sleep，每 0.5s 检查一次 stop flag，信号可快速响应"""
    slept = 0.0
    while slept < total_sec:
        if stop_check():
            return
        chunk = min(0.5, total_sec - slept)
        time.sleep(chunk)
        slept += chunk


def _extract_conclusion(stdout: str) -> Optional[str]:
    """从 verifier stdout 末尾抽取结论。

    期望格式（per skills/poc-verify/SKILL.md §4）：
    「结论: 存在/不存在/无法确认」
    """
    if not stdout:
        return None
    # 反向扫描找最后一个「结论」行
    for line in reversed(stdout.splitlines()):
        s = line.strip()
        if not s:
            continue
        # 匹配 "结论" / "结论:" / "**结论**"
        if "结论" in s:
            return s[:200]
        # 否则只取最后非空行
        return s[:200]
    return None


def _tail(text: str, max_chars: int = 2000) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return "...(truncated)...\n" + text[-max_chars:]


# ------------------------------------------------------------------ CLI 入口

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="poc-monitor",
        description="后台 PoC 验证调度进程（per design-docs/会话记录-最终方案.md §7）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--project-root", type=Path,
                   help="项目根目录（verifier 子进程 cwd + state dump 位置）")
    p.add_argument("--group-id", required=True,
                   help="项目 groupId（来自 preset.json），决定命名空间前缀")
    p.add_argument("--poll-interval", type=int, default=5,
                   help="扫描间隔秒数（默认 5）")
    p.add_argument("--max-concurrent", type=int, default=2,
                   help="verifier 并发上限（默认 2，per 设计文档 §1.6）")
    p.add_argument("--host", default="localhost",
                   help="Memurai host（默认 localhost）")
    p.add_argument("--port", type=int, default=6379,
                   help="Memurai port（默认 6379）")
    p.add_argument("--cli", type=Path, default=None,
                   help="memurai-cli.exe 路径（覆盖 MEMURAI_CLI 环境变量）")
    p.add_argument("--once", action="store_true",
                   help="跑一轮后退出（用于 CI smoke test）")
    p.add_argument("--dry-run", action="store_true",
                   help="只扫描不真正 Popen verifier（用于调试）")
    p.add_argument("--state-path", type=Path, default=None,
                   help="monitor-state.json 输出路径（默认 {project_root}/loop_audit/diag/）")
    p.add_argument("--quiet", action="store_true",
                   help="不打印 stderr 日志")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)

    # 默认 project_root：当前工作目录（daemon 一般从项目根启动）
    if args.project_root is None:
        args.project_root = Path.cwd()

    if not args.group_id:
        print("[ERROR] --group-id 必填", file=sys.stderr)
        return 2

    # 构建 Memurai（允许自定义 cli 路径 / host / port）
    memurai_kwargs = {"host": args.host, "port": args.port}
    if args.cli:
        memurai_kwargs["cli_path"] = args.cli

    try:
        memurai = Memurai(**memurai_kwargs)
    except MemuraiError as e:
        print(f"[ERROR] Memurai 初始化失败: {e}", file=sys.stderr)
        return 2

    monitor = PocMonitor(
        group_id=args.group_id,
        project_root=args.project_root,
        poll_interval=args.poll_interval,
        max_concurrent=args.max_concurrent,
        memurai=memurai,
        dry_run=args.dry_run,
        state_path=args.state_path,
        log_stderr=not args.quiet,
    )

    if args.once:
        return monitor.run_once()
    return monitor.run_forever()


if __name__ == "__main__":
    sys.exit(main())
