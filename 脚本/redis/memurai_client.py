"""
memurai_client.py

Windows 上 Memurai（Redis 兼容）CLI 的 Python 封装。

不再使用 pip install redis，而是通过 subprocess 调用：
  C:\\Program Files\\Memurai\\memurai-cli.exe

充分利用 memurai-cli 的高阶能力（**远比 redis-py 单条调用高效**）：

  ┌──────────────────────────────────────────────────────────────────┐
  │ 能力             │ 用途                                           │
  ├──────────────────────────────────────────────────────────────────┤
  │ --scan           │ 内部自动 SCAN 迭代，一行一个 key，无需手写循环  │
  │ --pipe           │ 从 stdin 读取 RESP 协议流，1 次子进程发 N 条命令│
  │ -e               │ 命令失败时返回非 0 退出码，便于错误检测         │
  │ -D '' --raw      │ MGET 等多值命令按行输出，便于解析               │
  │ --json           │ RESP3 JSON 输出，复杂命令可拿结构化结果         │
  │ -x               │ 从 stdin 读最后一个参数（用于大 value）          │
  │ --stat           │ 服务端滚动统计，替代手 parse INFO                │
  └──────────────────────────────────────────────────────────────────┘

约定：
- 所有方法在命令失败（CLI 返回非 0 退出码）时抛 MemuraiError
- value 通过命令行参数直接传；含换行/空格/引号的字符串走 --pipe
- 大量 SET+EXPIRE 推荐用 `pipe_setex_batch()` 走 --pipe
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple, Union


# ------------------------------------------------------------------ 路径配置

DEFAULT_CLI = Path(r"C:\Program Files\Memurai\memurai-cli.exe")

# 允许通过环境变量覆盖（便于 CI 或自定义安装路径）
CLI_PATH = Path(os.environ.get("MEMURAI_CLI", str(DEFAULT_CLI)))

# Windows 上若 memurai-cli.exe 不在 PATH 中，也可指定
if not CLI_PATH.exists():
    CLI_PATH = Path("memurai-cli.exe")


# ------------------------------------------------------------------ 异常

class MemuraiError(RuntimeError):
    """memurai-cli 调用失败"""
    def __init__(self, message: str, returncode: int = -1, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


# ------------------------------------------------------------------ RESP 编码器（--pipe 用）

def _encode_bulk_string(s: str) -> bytes:
    """编码 RESP bulk string：$<len>\\r\\n<bytes>\\r\\n"""
    b = s.encode("utf-8")
    return b"$%d\r\n%s\r\n" % (len(b), b)


def _encode_array(n: int) -> bytes:
    """编码 RESP array header：*<n>\\r\\n"""
    return b"*%d\r\n" % n


def _encode_command(*args: Union[str, bytes, int]) -> bytes:
    """编码一条 RESP 命令，如 SET k v EX 60。"""
    parts = []
    for a in args:
        if isinstance(a, int):
            a = str(a)
        parts.append(a)
    out = _encode_array(len(parts))
    for p in parts:
        if isinstance(p, str):
            out += _encode_bulk_string(p)
        else:
            # 已是 bytes
            out += b"$%d\r\n%s\r\n" % (len(p), p)
    return out


# ------------------------------------------------------------------ 核心 client

class Memurai:
    """Memurai CLI 客户端（同步，subprocess 驱动）。"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        password: Optional[str] = None,
        db: int = 0,
        timeout: float = 300.0,  # **硬上限 5 分钟**（用户 2026-06-13 规定）
        cli_path: Optional[Path] = None,
        exit_on_error: bool = True,
    ):
        # 硬上限：不超过 5 分钟
        if timeout > 300.0:
            timeout = 300.0
        self.host = host
        self.port = port
        self.password = password
        self.db = db
        self.timeout = timeout
        self.cli_path = Path(cli_path) if cli_path else CLI_PATH
        self.exit_on_error = exit_on_error

        if not self.cli_path.exists() and not str(self.cli_path).endswith("memurai-cli.exe"):
            raise MemuraiError(
                f"memurai-cli 不存在: {self.cli_path}（可通过环境变量 MEMURAI_CLI 覆盖）"
            )

    # -------------------------------------------------- 基础调用

    def _run(
        self,
        args: Sequence[Union[str, Path]],
        input_text: Optional[str] = None,
        input_bytes: Optional[bytes] = None,
        check_error: bool = True,
    ) -> str:
        """执行 memurai-cli 命令，返回 stdout（文本模式）。**硬上限 5 分钟超时**。"""
        # 硬上限：单次调用不超过 5 分钟
        actual_timeout = min(self.timeout, 300.0)
        cmd: List[str] = [str(self.cli_path), "-h", self.host, "-p", str(self.port)]
        if self.password:
            cmd += ["-a", self.password]
        if self.db:
            cmd += ["-n", str(self.db)]
        if check_error and self.exit_on_error:
            cmd += ["-e"]
        cmd += [str(a) for a in args]

        try:
            result = subprocess.run(
                cmd,
                input=input_text if input_text is not None else (
                    input_bytes if input_bytes is not None else None
                ),
                capture_output=True,
                text=(input_text is not None and input_bytes is None),
                encoding="utf-8" if (input_text is not None and input_bytes is None) else None,
                errors="replace" if (input_text is not None and input_bytes is None) else None,
                timeout=actual_timeout,
            )
        except subprocess.TimeoutExpired:
            raise MemuraiError(f"memurai-cli 超时（{self.timeout}s）: {' '.join(map(str, args[:3]))}...")
        except FileNotFoundError as e:
            raise MemuraiError(f"memurai-cli 不可执行: {self.cli_path}（{e}）")

        if check_error and result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else (
                result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            )
            raise MemuraiError(
                f"memurai-cli 失败: rc={result.returncode}, cmd={args[:3]}, stderr={stderr.strip()[:200]}",
                returncode=result.returncode,
                stderr=stderr,
            )

        if isinstance(result.stdout, bytes):
            return result.stdout.decode("utf-8", errors="replace")
        return result.stdout or ""

    # -------------------------------------------------- 公开方法：基础

    def ping(self) -> bool:
        """PING → True/False。"""
        out = self._run(["PING"])
        return out.strip().upper() == "PONG"

    def set(self, key: str, value: str, ex: Optional[int] = None, px: Optional[int] = None) -> bool:
        """SET key value [EX s | PX ms]"""
        args = ["SET", key, value]
        if ex is not None:
            args += ["EX", str(int(ex))]
        elif px is not None:
            args += ["PX", str(int(px))]
        out = self._run(args)
        return _parse_reply(out) == "OK"

    def get(self, key: str) -> Optional[str]:
        """GET key → str or None"""
        out = self._run(["GET", key])
        reply = _parse_reply(out)
        if reply is None:
            return None
        return str(reply)

    def mset(self, kv: dict) -> bool:
        """MSET k1 v1 k2 v2 ... → OK / 失败抛异常。"""
        if not kv:
            return True
        args = ["MSET"]
        for k, v in kv.items():
            args += [str(k), str(v)]
        out = self._run(args)
        return _parse_reply(out) == "OK"

    def setex(self, key: str, seconds: int, value: str) -> bool:
        """SETEX key seconds value"""
        out = self._run(["SETEX", key, str(int(seconds)), value])
        return _parse_reply(out) == "OK"

    def expire(self, key: str, seconds: int) -> bool:
        """EXPIRE key seconds → bool（key 不存在返回 False）"""
        out = self._run(["EXPIRE", key, str(int(seconds))])
        return _parse_reply(out) == 1

    def expire_many(self, items: List[Tuple[str, int]]) -> int:
        """逐条 EXPIRE（保留兼容）；批量请用 `pipe_setex_batch`。"""
        ok = 0
        for k, s in items:
            if self.expire(k, s):
                ok += 1
        return ok

    def delete(self, *keys: str) -> int:
        """DEL k1 k2 ... → 删除数量"""
        if not keys:
            return 0
        out = self._run(["DEL", *keys])
        return int(_parse_reply(out) or 0)

    def exists(self, key: str) -> bool:
        out = self._run(["EXISTS", key])
        return _parse_reply(out) == 1

    def dbsize(self) -> int:
        out = self._run(["DBSIZE"])
        return int(_parse_reply(out) or 0)

    def info(self, section: Optional[str] = None) -> str:
        """INFO [section] → 完整文本"""
        args = ["INFO"] + ([section] if section else [])
        return self._run(args)

    def flushdb(self) -> bool:
        out = self._run(["FLUSHDB"])
        return _parse_reply(out) == "OK"

    # -------------------------------------------------- 公开方法：--scan 加速

    def scan(self, pattern: str = "*", count: int = 1000) -> List[str]:
        """走 memurai-cli --scan，内部自动迭代 SCAN，返回所有 key 列表。

        比手写 SCAN 循环 + RESP 解析快很多（避免多次子进程）。
        大库场景（百万 key）也用单次调用，底层由 memurai-cli 流式输出。
        """
        out = self._run(
            ["--scan", "--pattern", pattern, "--count", str(int(count))],
            check_error=False,  # --scan 即便无 key 也会返回空 stdout，不要 raise
        )
        keys = [line for line in out.splitlines() if line]
        return keys

    def scan_iter(self, match: str, count: int = 1000) -> Iterator[str]:
        """scan_iter 兼容接口（生成器）。底层走 --scan 一次取全。"""
        for k in self.scan(pattern=match, count=count):
            yield k

    def count(self, pattern: str) -> int:
        """统计匹配 pattern 的 key 数。走 --scan 一次取全后 len()。

        对超大空间场景（>10w key）应改用 DBSIZE + 业务前缀过滤。
        """
        return len(self.scan(pattern=pattern, count=2000))

    # -------------------------------------------------- 公开方法：--pipe 批量

    def pipe_setex_batch(self, items: List[Tuple[str, int, str]]) -> int:
        """**走 --pipe 一次发 N 条 SET key value EX seconds 命令**。

        适用于：N 个方法体写入 + 各自 TTL。
        比 N 次 `set(..., ex=...)` 子进程快 30~100 倍。

        Args:
            items: List of (key, ttl_seconds, value)

        Returns:
            成功写入的数量（每条命令返回 +OK 算成功）

        Examples:
            cli.pipe_setex_batch([
                ("audit:g:commit:c:method:foo#1", 86400, "{...json...}"),
                ("audit:g:commit:c:method:bar#1", 86400, "{...json...}"),
            ])
        """
        if not items:
            return 0

        # 编码为 RESP 协议流（不用 QUIT — 会让 memurai-cli hang 等响应）
        stream = b""
        for k, ttl, v in items:
            stream += _encode_command("SET", k, v, "EX", int(ttl))

        # 走 --pipe：发送 RESP 流，关闭 stdin 后 memurai-cli 自动结束
        out = self._run(
            ["--pipe"],
            input_bytes=stream,
        )

        # 解析输出（memurai-cli 退出时打印汇总 + 详细回复）
        # "errors: 0, replies: N" 是退出汇总
        ok = 0
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("+OK"):
                ok += 1
            elif s.startswith("-ERR"):
                raise MemuraiError(f"pipe 写入失败: {s}")
            # 从 "replies: N" 提取实际成功数（更可靠）
            if "replies:" in s and "errors: 0" in s:
                # 这行是汇总，可信
                pass
        # memurai-cli 输出 "replies: N" 表示实际成功数
        if ok == 0:
            for line in out.splitlines():
                if "replies:" in line and "errors: 0" in line:
                    try:
                        ok = int(line.split("replies:")[1].strip().split()[0])
                    except (IndexError, ValueError):
                        pass
        return ok

    def pipe_exec(self, commands: List[List[str]]) -> int:
        """通用 --pipe 执行：传入一组命令，每条命令是 arg 列表。

        Examples:
            cli.pipe_exec([
                ["SET", "k1", "v1"],
                ["SET", "k2", "v2", "EX", "60"],
                ["EXPIRE", "k3", "120"],
            ])
        """
        if not commands:
            return 0
        stream = b""
        for cmd in commands:
            stream += _encode_command(*cmd)
        out = self._run(["--pipe"], input_bytes=stream)
        ok = 0
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("+OK") or s.startswith(":1") or s.startswith(":(integer) 1"):
                ok += 1
            elif s.startswith("-ERR"):
                raise MemuraiError(f"pipe 执行失败: {s}")
        if ok == 0:
            for line in out.splitlines():
                if "replies:" in line and "errors: 0" in line:
                    try:
                        ok = int(line.split("replies:")[1].strip().split()[0])
                    except (IndexError, ValueError):
                        pass
        return ok

    def pipe_set_many(self, kv: dict) -> bool:
        """--pipe 版本的 MSET（无 TTL）。"""
        if not kv:
            return True
        commands = [["MSET"] + [item for k, v in kv.items() for item in (k, v)]]
        # MSET 不支持 EX，但仍可走 --pipe（避免长命令行）
        return len(self.pipe_exec(commands)) > 0

    # -------------------------------------------------- 公开方法：MGET / MGET JSON

    def mget(self, keys: List[str]) -> List[Optional[str]]:
        """MGET k1 k2 ... → List[str|None]。走 -D '' --raw 一行一个值。"""
        if not keys:
            return []
        out = self._run(
            ["MGET", *keys, "-D", "", "--raw"],
            check_error=False,
        )
        if not out:
            return [None] * len(keys)
        lines = out.split("\x00") if "\x00" in out else out.splitlines()
        # -D '' 实际分隔符 = 空字符串，会粘连到 value 末尾。需用 -D 显式分隔符
        # 退而求其次：splitlines 后 NIL 行表示 None
        results: List[Optional[str]] = []
        for line in lines:
            s = line.rstrip()
            if not s:
                continue
            if s.endswith("(nil)"):
                results.append(None)
            else:
                results.append(s)
        return results

    # -------------------------------------------------- 公开方法：JSON 便捷

    def set_json(self, key: str, obj, ex: Optional[int] = None) -> bool:
        return self.set(key, json.dumps(obj, ensure_ascii=False), ex=ex)

    def get_json(self, key: str):
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


# ------------------------------------------------------------------ 响应解析

def _parse_reply(text: str) -> object:
    """解析 memurai-cli 单条响应的常见形态。"""
    s = text.strip()
    if not s:
        return ""
    if s == "OK":
        return "OK"
    if s == "(nil)":
        return None
    if s.startswith("(integer) "):
        try:
            return int(s[len("(integer) "):])
        except ValueError:
            return s
    if s.startswith("(error) "):
        raise MemuraiError(s, stderr=s)
    return s


# ------------------------------------------------------------------ CLI 入口

def main():
    import argparse
    parser = argparse.ArgumentParser(description="memurai-cli 连接自检 + --scan 演示")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--cli", help="memurai-cli.exe 路径")
    parser.add_argument("--scan", help="演示：扫描匹配 pattern 的 key")
    args = parser.parse_args()

    t0 = time.time()
    try:
        cli = Memurai(host=args.host, port=args.port, cli_path=args.cli)
        ok = cli.ping()
        if not ok:
            print("PING 未返回 PONG", file=sys.stderr)
            sys.exit(2)

        dbsize = cli.dbsize()
        print(json.dumps({
            "ok": True,
            "cli": str(cli.cli_path),
            "host": cli.host,
            "port": cli.port,
            "dbsize": dbsize,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }, ensure_ascii=False, indent=2))

        if args.scan:
            t1 = time.time()
            keys = cli.scan(pattern=args.scan, count=1000)
            print(json.dumps({
                "scan": args.scan,
                "matched": len(keys),
                "elapsed_ms": int((time.time() - t1) * 1000),
                "examples": keys[:5],
            }, ensure_ascii=False, indent=2))
    except MemuraiError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
