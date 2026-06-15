"""
method_calls_extractor.py
=========================

Python wrapper around ``java-method-call-extractor-1.0.0.jar``
(``D:\\agentloop\\tools\\javaparser\\``).

对 Java 源文件做 **出向方法调用抽取**:
  - 调用 JAR 得到 JSON 数组 ``[{"startLine","methodSignature","calledFQN"}, ...]``
  - 为每条记录补充 ``file`` (相对路径) + ``is_sink`` 标记
  - **Sink 判定规则**: 给定 ``group_id`` 后, ``calledFQN`` **不** 以 ``"<group_id>."`` 开头
    即视为 sink(即项目边界外的依赖,可能成为漏洞落点)
  - 单文件 / 目录两种入口, ``concurrent.futures.ThreadPoolExecutor`` 并行提速

用法 (CLI):
    # 整个目录
    python method_calls_extractor.py --src D:\\code\\WebGoat-2025.3\\src\\main\\java \\
        --source-root D:\\code\\WebGoat-2025.3\\src\\main\\java \\
        --group-id org.owasp.webgoat \\
        --output calls.json

    # 单文件
    python method_calls_extractor.py --src path\\to\\Foo.java \\
        --group-id com.example --timeout 30

用法 (Python):
    from pathlib import Path
    from method_calls_extractor import extract_method_calls

    records = extract_method_calls(
        java_path=Path("D:/code/WebGoat-2025.3/src/main/java"),
        source_root=Path("D:/code/WebGoat-2025.3/src/main/java"),
        group_id="org.owasp.webgoat",
    )
    sinks = [r for r in records if r["is_sink"]]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


# ============================================================== 默认配置

DEFAULT_JAR_PATH = Path(
    r"D:\agentloop\tools\javaparser\java-method-call-extractor-1.0.0.jar"
)
DEFAULT_TIMEOUT = 30          # 单文件 JAR 调用超时 (秒)
DEFAULT_WORKERS = 4           # 并行线程数
DEFAULT_LOG_PREFIX = "[method-calls]"   # stderr 日志前缀


# ============================================================== 工具函数

def _log(msg: str) -> None:
    """统一日志: 全部走 stderr, 不污染 stdout 的 JSON 结果。"""
    print(f"{DEFAULT_LOG_PREFIX} {msg}", file=sys.stderr, flush=True)


def _resolve_files(
    java_path: Path | list[Path],
    recursive: bool = True,
) -> list[Path]:
    """把 ``Path | list[Path]`` 拍平为 ``.java`` 文件列表。

    - 单文件: 直接返回 ``[java_path]``
    - 目录: ``glob("**/*.java")`` (recursive=True) 或 ``glob("*.java")``
    - 列表: 逐项递归展平
    """
    if isinstance(java_path, (list, tuple)):
        out: list[Path] = []
        for p in java_path:
            out.extend(_resolve_files(Path(p), recursive=recursive))
        return out

    p = Path(java_path)
    if p.is_file():
        return [p]
    if p.is_dir():
        pattern = "**/*.java" if recursive else "*.java"
        return sorted(p.glob(pattern))
    raise FileNotFoundError(f"java_path 不存在或不是文件/目录: {p}")


def _is_sink(called_fqn: str, group_id: str | None) -> bool:
    """Sink 判定: ``group_id`` 给定且 calledFQN 不在 group_id 命名空间下。

    规则: 以 ``"<group_id>."`` 开头 → 内部调用, 非 sink; 其余 → sink。
    空 calledFQN 也算 sink (JAR 偶发输出, 安全起见按 sink 处理)。
    """
    if not group_id:
        return False
    if not called_fqn:
        return True
    return not called_fqn.startswith(group_id + ".")


def _relative_file(file_path: Path, source_root: Path | None) -> str:
    """记录里的 ``file`` 字段: 优先用相对 source_root 的 POSIX 路径。"""
    fp = Path(file_path)
    if source_root:
        try:
            return fp.resolve().relative_to(Path(source_root).resolve()).as_posix()
        except ValueError:
            # 跳出 source_root 时回退到原绝对路径
            return fp.as_posix()
    return fp.as_posix()


# ============================================================== JAR 调用

def _run_jar_for_file(
    file_path: Path,
    source_root: Path | None,
    jar_path: Path,
    timeout: int,
) -> list[dict[str, Any]]:
    """对单文件调用 JAR, 返回原始 JSON 记录列表。

    失败行为: 抛 ``RuntimeError`` (上层 catch 后 log + skip)。
    """
    if not jar_path.is_file():
        raise FileNotFoundError(f"JAR 不存在: {jar_path}")

    cmd: list[str] = ["java", "-jar", str(jar_path), str(file_path)]
    if source_root:
        cmd.append(str(source_root))

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )

    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(
            f"JAR 退出码 {proc.returncode}: {file_path} | "
            + " | ".join(stderr_tail)
        )

    stdout = (proc.stdout or "").strip()
    if not stdout:
        # JAR 对无方法体的类可能输出空, 合法情况, 返回空列表
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"JAR 输出非 JSON: {file_path} (line {e.lineno} col {e.colno})"
        ) from e

    if not isinstance(data, list):
        raise RuntimeError(
            f"JAR 输出不是 JSON 数组: {file_path} (got {type(data).__name__})"
        )
    return data


# ============================================================== 主入口

def extract_method_calls(
    java_path: Path | list[Path],
    source_root: Path | None = None,
    group_id: str | None = None,
    jar_path: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_workers: int = DEFAULT_WORKERS,
    recursive: bool = True,
    log: bool = True,
) -> list[dict[str, Any]]:
    """抽取 Java 文件中每个方法的出向调用, 并标记 sink。

    Parameters
    ----------
    java_path:
        单个 ``.java`` 文件 / Java 源码目录, 或它们的列表。
    source_root:
        可选。用于 ``file`` 字段计算相对路径, 也作为 JAR 第二参数 (帮助符号解析)。
    group_id:
        可选。项目 groupId (如 ``"org.owasp.webgoat"``)。给出后,
        ``calledFQN`` 不以 ``"<group_id>."`` 开头的会被标 ``is_sink=True``。
    jar_path:
        可选, 覆盖 ``DEFAULT_JAR_PATH``。
    timeout:
        单文件 JAR 调用的超时 (秒), 默认 30。
    max_workers:
        并行线程数, 默认 4。
    recursive:
        目录模式下是否递归, 默认 True。
    log:
        是否往 stderr 输出进度, 默认 True。

    Returns
    -------
    list[dict], 每条记录形如::

        {
          "file": "org/owasp/webgoat/.../Foo.java",
          "method_start_line": 42,
          "method_signature": "public void foo(String bar)",
          "called_fqn": "java.sql.PreparedStatement.executeQuery(String)",
          "is_sink": True
        }
    """
    _jar = Path(jar_path) if jar_path else DEFAULT_JAR_PATH
    files = _resolve_files(java_path, recursive=recursive)

    if not files:
        if log:
            _log(f"未找到 .java 文件: {java_path}")
        return []

    if log:
        _log(
            f"待处理: {len(files)} 个文件, group_id={group_id!r}, "
            f"workers={max_workers}, timeout={timeout}s"
        )

    sink_prefix = f"{group_id}." if group_id else None
    results: list[dict[str, Any]] = []
    failed: list[tuple[Path, str]] = []
    t0 = time.perf_counter()

    def _process_one(fp: Path) -> list[dict[str, Any]]:
        raw = _run_jar_for_file(fp, source_root, _jar, timeout)
        rel = _relative_file(fp, source_root)
        out: list[dict[str, Any]] = []
        for rec in raw:
            try:
                line = int(rec.get("startLine", 0))
            except (TypeError, ValueError):
                line = 0
            sig = rec.get("methodSignature", "") or ""
            fqn = rec.get("calledFQN", "") or ""
            out.append({
                "file": rel,
                "method_start_line": line,
                "method_signature": sig,
                "called_fqn": fqn,
                # 显式布尔 (无 group_id 时统一 False, 不留 None)
                "is_sink": bool(sink_prefix and not fqn.startswith(sink_prefix)),
            })
        return out

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        futures = {ex.submit(_process_one, f): f for f in files}
        done = 0
        for fut in as_completed(futures):
            f = futures[fut]
            done += 1
            try:
                results.extend(fut.result())
            except Exception as e:  # noqa: BLE001 - 我们要兜底所有错误
                failed.append((f, str(e)))
                if log:
                    _log(f"  [{done}/{len(files)}] FAIL {f.name}: {e}")
                continue
            if log and (done % 25 == 0 or done == len(files)):
                _log(f"  [{done}/{len(files)}] OK {f.name}")

    elapsed = time.perf_counter() - t0
    if log:
        sink_count = sum(1 for r in results if r["is_sink"])
        _log(
            f"完成: {len(results)} 条记录, sinks={sink_count}, "
            f"失败={len(failed)}, 耗时 {elapsed:.2f}s"
        )
        if failed:
            for f, msg in failed[:5]:
                _log(f"  失败样本: {f} -> {msg}")
            if len(failed) > 5:
                _log(f"  ... 还有 {len(failed) - 5} 个失败未列出")
    return results


# ============================================================== CLI

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="调用 java-method-call-extractor.jar 抽取方法调用, 标记 sink",
    )
    p.add_argument(
        "--src",
        required=True,
        help="Java 源文件 或 目录 (可重复: --src a --src b)",
    )
    p.add_argument(
        "--source-root",
        default=None,
        help="源根目录 (用于 file 相对路径; 也传给 JAR)",
    )
    p.add_argument(
        "--group-id",
        default=None,
        help="项目 groupId, 用于 sink 判定 (例: org.owasp.webgoat)",
    )
    p.add_argument(
        "--jar",
        default=None,
        help=f"覆盖默认 JAR 路径 (默认: {DEFAULT_JAR_PATH})",
    )
    p.add_argument(
        "--output", "-o",
        default=None,
        help="输出 JSON 文件路径 (省略则打印到 stdout)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"单文件超时秒数 (默认 {DEFAULT_TIMEOUT})",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"并行线程数 (默认 {DEFAULT_WORKERS})",
    )
    p.add_argument(
        "--no-recursive",
        action="store_true",
        help="目录模式下不递归子目录",
    )
    p.add_argument(
        "--sinks-only",
        action="store_true",
        help="只输出 is_sink=true 的记录",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    # --src 允许多次, 这里用 nargs='*' 简化: 用逗号/分号分隔也行
    raw = args.src
    if "," in raw or ";" in raw:
        paths = [Path(s.strip()) for s in raw.replace(";", ",").split(",") if s.strip()]
    else:
        paths = [Path(raw)]

    try:
        records = extract_method_calls(
            java_path=paths,
            source_root=Path(args.source_root) if args.source_root else None,
            group_id=args.group_id,
            jar_path=Path(args.jar) if args.jar else None,
            timeout=args.timeout,
            max_workers=args.workers,
            recursive=not args.no_recursive,
        )
    except FileNotFoundError as e:
        _log(f"ERROR: {e}")
        return 2

    if args.sinks_only and args.group_id:
        records = [r for r in records if r["is_sink"]]

    payload = {
        "group_id": args.group_id,
        "source_root": args.source_root,
        "count": len(records),
        "sink_count": sum(1 for r in records if r["is_sink"]),
        "records": records,
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        _log(f"已写入 {out} ({len(records)} 条记录)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
