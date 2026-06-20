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
import tempfile
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


def _is_external_sink_fqn(called_fqn: str, group_id: str | None) -> bool:
    """--config 模式专用 sink 重判定: 修正 JAR ``determineSink`` 的链式调用假阳性。

    JAR 的 ``determineSink`` 把整个 calledFQN (含实参文本) 按 ``.`` 切分计段数,
    导致 ``success(this).feedback("idor.edit.profile.success1")`` 被切成 5 段 →
    误判为 sink。实际上 ``success`` 是项目内部方法 (只是符号解析失败)。

    本函数先剥离实参 (第一个 ``(`` 之前的部分), 再按 ``.`` 计段数:

    - 以 ``"<group_id>."`` 开头 → 内部, False
    - ≥3 段且不以 groupId 开头 → 真外部 sink (如 ``java.lang.String.equals``)
    - <3 段 → 短名/链式调用, 符号解析失败, 保守 False (避免假阳性)

    这与 JAR ``determineSink`` 的设计意图一致 (短名 → false), 只修正了
    "实参里的点号被误计" 这一个缺陷。
    """
    if not group_id:
        return False
    if not called_fqn:
        return True
    # 剥离实参: success(this).feedback("a.b.c") → success(this).feedback
    #           java.lang.String.equals(authUserId) → java.lang.String.equals
    paren_idx = called_fqn.find("(")
    fqn_no_args = called_fqn[:paren_idx] if paren_idx >= 0 else called_fqn
    if fqn_no_args.startswith(group_id + "."):
        return False
    # 计段数: 真正的 FQN (java.lang.String.equals) ≥3 段;
    # 短名/链式 (success.feedback, req.getUri) <3 段
    segments = fqn_no_args.split(".")
    return len(segments) >= 3


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

    # 多文件优先用 --config 方式 (符号跨文件解析更准确, 短名不会被误判为 sink)
    if len(files) > 1:
        if log:
            _log(f"使用 --config 方式处理 {len(files)} 个文件")
        return extract_method_calls_via_config(
            files=files,
            source_root=source_root,
            group_id=group_id,
            jar_path=jar_path,
            workers=max_workers,
            log=log,
        )

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


# ============================================================== --config 批量入口

def extract_method_calls_via_config(
    files: list[Path],
    source_root: Path | None = None,
    group_id: str | None = None,
    jar_path: Path | None = None,
    timeout: int = 300,
    workers: int = 4,
    log: bool = True,
) -> list[dict[str, Any]]:
    """用 ``--config`` 方式一次性把所有文件交给 JAR。

    生成临时 properties 文件, 调 ``java -jar extractor.jar --config tmp.properties``。
    JAR 内部多线程解析所有文件 + 自动推导 groupId, 符号跨文件解析更准确,
    短名不会被误判为 sink (旧 per-file 模式有此问题)。

    输出记录字段名与 :func:`extract_method_calls` 一致 (标准化由本函数完成):
    ``file`` / ``method_start_line`` / ``method_signature`` / ``called_fqn`` / ``is_sink``。

    Parameters
    ----------
    files:
        待处理的 ``.java`` 文件列表 (已展平, 不再递归)。
    source_root:
        可选, 传给 JAR 的 ``sourceRoot`` (跨文件 FQN 解析根)。
    group_id:
        可选, 项目 groupId。给出后 JAR 自行判定 sink; 省略时 JAR 从首文件
        package 自动推导 (取前 3 段)。
    jar_path:
        可选, 覆盖 ``DEFAULT_JAR_PATH``。
    timeout:
        整批 JAR 调用超时 (秒), 默认 300。多文件批量应给足时间。
    workers:
        JAR 内部并行线程数, 默认 4。
    log:
        是否往 stderr 输出进度, 默认 True。

    Returns
    -------
    list[dict], 每条记录字段同 :func:`extract_method_calls`。
    """
    _jar = Path(jar_path) if jar_path else DEFAULT_JAR_PATH
    if not _jar.exists():
        raise FileNotFoundError(f"JAR 不存在: {_jar}")
    if not files:
        return []

    # 生成临时 config 文件 (JAR --config 模式按 file.N 读取)
    # 注意: Java Properties.load() 把反斜杠当转义字符, Windows 路径 D:\code\...
    # 会被吞成 D:code... → 必须用正斜杠 (Java Path.of 接受正斜杠)
    config_lines: list[str] = ["mode=calls", f"workers={workers}"]
    if source_root:
        config_lines.append(f"sourceRoot={Path(source_root).as_posix()}")
    if group_id:
        config_lines.append(f"groupId={group_id}")
    for i, f in enumerate(files, 1):
        config_lines.append(f"file.{i}={Path(f).as_posix()}")

    config_content = "\n".join(config_lines) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".properties", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(config_content)
        config_file = tf.name

    if log:
        _log(
            f"--config 模式: {len(files)} 个文件, group_id={group_id!r}, "
            f"workers={workers}, timeout={timeout}s"
        )

    try:
        cmd: list[str] = ["java", "-jar", str(_jar), "--config", config_file]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        try:
            Path(config_file).unlink()
        except OSError:
            pass

    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-5:]
        raise RuntimeError(
            f"JAR 退出码 {proc.returncode}: " + " | ".join(stderr_tail)[:300]
        )

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"JAR --config 输出非 JSON (line {e.lineno} col {e.colno}): "
            f"{stdout[:200]!r}"
        ) from e

    if not isinstance(data, list):
        raise RuntimeError(
            f"JAR --config 输出不是 JSON 数组 (got {type(data).__name__})"
        )

    # 标准化字段名: JAR 输出 startLine/methodSignature/calledFQN/isSink/file
    #              → 统一为 method_start_line/method_signature/called_fqn/is_sink/file
    results: list[dict[str, Any]] = []
    sink_count = 0
    for rec in data:
        try:
            line = int(rec.get("startLine", 0))
        except (TypeError, ValueError):
            line = 0
        fqn = rec.get("calledFQN", "") or ""
        is_sink = bool(rec.get("isSink", False))
        # 修正 JAR determineSink 的链式调用假阳性 (实参里的点号被误计为段数)。
        # 单向过滤: 只移除假阳性 (JAR=true→False), 不新增 sink。
        if is_sink and group_id:
            is_sink = _is_external_sink_fqn(fqn, group_id)
        if is_sink:
            sink_count += 1
        results.append({
            "file": (rec.get("file", "") or "").replace("\\", "/"),
            "method_start_line": line,
            "method_signature": rec.get("methodSignature", "") or "",
            "called_fqn": fqn,
            "is_sink": is_sink,
        })

    if log:
        _log(f"--config 完成: {len(results)} 条记录, sinks={sink_count}")

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
