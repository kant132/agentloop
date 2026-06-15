#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attack_surface_scanner.py — 通用 Java 攻击面扫描器

三阶段流程:
  Phase 1 — ast-grep 扫所有注解 → ``all_annotations.json`` (含 fqn / name / args /
            file / line / method_name / method_body / source 类型)
  Phase 2 — route 模式过滤 + javaparser-service 富化 → ``route_annotations.json``
            (含 source / third_party_calls / annotation_source)
  Phase 3 (NEW) — codegraph ``nodes.id`` 反查 → 给每个 route 打 sig_hash + nodes_id

hashkey 策略 (2026-06-15 迁移):
  - 默认 (use_nodes_id=True) : sig_hash = codegraph nodes.id (稳定,跨 refactor 不变)
  - 回退 (DB 不可用或查不到): sig_hash = md5(file|line|annotation)[:16] (legacy,行号移动会变)
  - 输出 schema 新增 ``nodes_id`` 字段,与 ``sig_hash`` 并存,便于回溯

外部依赖:
  - ``ast-grep`` (CLI, v0.43.0+) 在 PATH
  - ``javaparser-service.jar`` 默认 ``<repo>/tools/javaparser-service/target/javaparser-service.jar``
    可通过 ``JAVAPARSER_SERVICE_JAR`` 环境变量覆盖
  - Java 17+ (执行 javaparser-service 需要)
  - ``codegraph`` CLI + ``<project>/.codegraph/codegraph.db`` (可选;缺失时降级 md5)

CLI 用法::

    python attack_surface_scanner.py \\
        --project /path/to/java/repo \\
        --group-id com.example.app \\
        --output /path/to/results \\
        [--codegraph-db PATH] [--no-nodes-id]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from scanner_utils import (
        lookup_nodes_id,
        md5_legacy_hash,
        node_hash_key,
        resolve_hashkey,
    )
except ImportError:  # pragma: no cover - allow direct execution from another cwd
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scanner_utils import (
        lookup_nodes_id,
        md5_legacy_hash,
        node_hash_key,
        resolve_hashkey,
    )


# ============================================================
# Constants & default paths
# ============================================================

_HERE = Path(__file__).resolve().parent
DEFAULT_CATEGORIES_PATH = _HERE / "annotation-categories.json"
DEFAULT_CODEGRAPH_DB = _HERE.parent.parent / ".codegraph" / "codegraph.db"
# If the above path is missing, scanner will look at <project_root>/.codegraph/codegraph.db.

# javaparser-service.jar 解析优先级:
#   1) 环境变量 JAVAPARSER_SERVICE_JAR
#   2) 相对于本文件的推导路径: ../../tools/javaparser-service/target/javaparser-service.jar
_JPS_JAR_ENV = os.environ.get("JAVAPARSER_SERVICE_JAR")
_DEFAULT_JPS_JAR = (
    Path(_JPS_JAR_ENV) if _JPS_JAR_ENV
    else _HERE.parent.parent / "tools" / "javaparser-service" / "target" / "javaparser-service.jar"
)
JAVAPARSER_JAR: Path = _DEFAULT_JPS_JAR

# ast-grep 可执行名 (可通过 AST_GREP_BIN 覆盖)
AST_GREP_BIN: str = os.environ.get("AST_GREP_BIN", "ast-grep")

# Phase 2 调参常量
JAVAPARSER_TIMEOUT_SEC: int = 120          # 单文件解析超时
CALL_ATTRIBUTION_WINDOW: int = 200         # 注解往下多少行视为该方法的调用
METHOD_OWNERSHIP_GAP: int = 50             # 注解离最近方法头的最大距离
MAX_METHOD_SOURCE_BYTES: int = 64 * 1024   # 单方法源码上限 (截断保护)

# AST grep node kinds
ANNOTATION_NODE_KIND: str = "annotation"
METHOD_DECL_NODE_KIND: str = "method_declaration"

# CLI / 日志前缀
_LOG_PREFIX = "[attack-surface-scanner]"


# ============================================================
# Lightweight logging helpers
# ============================================================

def log_info(message: str) -> None:
    """Print an info-level message to stderr (does not pollute JSON stdout)."""
    print(f"{_LOG_PREFIX} {message}", file=sys.stderr, flush=True)


def log_warn(message: str) -> None:
    """Print a warning to stderr."""
    print(f"{_LOG_PREFIX} [warn] {message}", file=sys.stderr, flush=True)


def log_error(message: str) -> None:
    """Print an error to stderr."""
    print(f"{_LOG_PREFIX} [error] {message}", file=sys.stderr, flush=True)


def log_debug(message: str) -> None:
    """Print a debug message (only when ATTACK_SURFACE_DEBUG=1)."""
    if os.environ.get("ATTACK_SURFACE_DEBUG") == "1":
        print(f"{_LOG_PREFIX} [debug] {message}", file=sys.stderr, flush=True)


# ============================================================
# Phase 1: ast-grep wrappers
# ============================================================

def _which_ast_grep() -> Optional[str]:
    """Best-effort lookup of ast-grep binary; returns path or None."""
    from shutil import which
    return which(AST_GREP_BIN)


def run_ast_grep_annotations(project_root: Path) -> List[Dict[str, Any]]:
    """Invoke ``ast-grep run --kind annotation --lang java --json=compact .``.

    Returns raw match records (``file`` / ``range`` / ``text``). Returns
    ``[]`` on binary missing, non-zero exit, or malformed output.
    """
    if _which_ast_grep() is None:
        log_error(f"ast-grep binary not found on PATH (AST_GREP_BIN={AST_GREP_BIN!r})")
        return []

    cmd = [
        AST_GREP_BIN, "run",
        "--kind", ANNOTATION_NODE_KIND,
        "--lang", "java",
        "--json=compact", ".",
    ]
    log_debug(f"exec: {' '.join(cmd)} (cwd={project_root})")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        log_error(f"ast-grep launch failed: {exc}")
        return []

    if proc.returncode != 0 and not proc.stdout.strip():
        msg = (proc.stderr or "").strip() or f"ast-grep exited with {proc.returncode}"
        log_warn(msg)
        return []

    hits = parse_ast_grep_output(proc.stdout)
    log_debug(f"parsed {len(hits)} annotation hits from ast-grep")
    return hits


def parse_ast_grep_output(raw: str) -> List[Dict[str, Any]]:
    """Parse ast-grep ``--json=compact`` output.

    Accepts a single JSON array, an NDJSON stream (one object per line), or
    a single bare object (defensive).
    """
    blob = (raw or "").strip()
    if not blob:
        return []

    # Fast path: try the entire blob as JSON
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return [h for h in parsed if isinstance(h, dict)]
    if isinstance(parsed, dict):
        return [parsed]

    # Fallback: NDJSON stream (one JSON object per line)
    out: List[Dict[str, Any]] = []
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            out.extend(h for h in value if isinstance(h, dict))
        elif isinstance(value, dict):
            out.append(value)
    return out


# ============================================================
# Imports & FQN resolution
# ============================================================

_IMPORT_RE = re.compile(r"^\s*import\s+(static\s+)?([\w.]+)(?:\.\*)?\s*;\s*$")


def collect_imports(project_root: Path) -> Dict[str, Dict[str, str]]:
    """Build ``file_path -> { simple_name -> fqn }`` from Java sources.

    Handles wildcard imports (``import x.y.*``) via the special key ``"*"``.
    Skips ``import static``.
    """
    imports: Dict[str, Dict[str, str]] = {}
    files = list(project_root.rglob("*.java"))
    log_debug(f"collect_imports: {len(files)} java files under {project_root}")

    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log_warn(f"cannot read {fp}: {exc}")
            continue

        per_file: Dict[str, str] = {}
        for raw_line in text.splitlines():
            m = _IMPORT_RE.match(raw_line)
            if not m:
                continue
            is_static = bool(m.group(1))
            if is_static:
                continue
            fqn = m.group(2)
            stripped = raw_line.strip()
            if stripped.endswith(".*"):
                per_file["*"] = fqn
                continue
            simple = fqn.rsplit(".", 1)[-1]
            per_file[simple] = fqn
        if per_file:
            imports[str(fp)] = per_file
    return imports


def resolve_fqn(annotation_name: str, file_imports: Dict[str, str]) -> str:
    """Resolve an annotation short / partially-qualified name to its FQN.

    Lookup order: (1) exact simple-name match in imports, (2) wildcard import,
    (3) name-as-is if already dotted.
    """
    if not annotation_name:
        return annotation_name
    bare = annotation_name.split(".")[-1]
    if bare in file_imports and file_imports[bare] != "*":
        return file_imports[bare]
    if "*" in file_imports:
        return f"{file_imports['*']}.{bare}"
    if "." in annotation_name:
        return annotation_name
    return annotation_name


def _imports_for_file(
    imports_map: Dict[str, Dict[str, str]],
    abs_path: Path,
    file_rel: str,
    file_rel_raw: str,
) -> Dict[str, str]:
    """Robustly look up per-file imports, tolerating different path forms."""
    candidates = (
        str(abs_path),
        file_rel,
        file_rel_raw,
        str(Path(file_rel)),
        str(Path(file_rel_raw)),
    )
    for key in candidates:
        if key in imports_map:
            return imports_map[key]
    return {}


# ============================================================
# Phase 1: lightweight method finder
# ============================================================

_METHOD_HEAD_RE = re.compile(
    r"^\s*(?:public|protected|private)\s+"
    r"(?:static\s+|final\s+|abstract\s+|synchronized\s+|native\s+|default\s+|async\s+|"
    r"@?\w+\s+)*"
    r"[\w<>,\[\]\s.?&]+?\s+"
    r"([A-Za-z_]\w*)\s*[(]",
    re.MULTILINE,
)

_TYPE_DECL_RE = re.compile(
    r"^(?:(?:public|protected|private|static|final|abstract|sealed)\s+)*"
    r"(?:@\w+(?:\([^)]*\))?\s+)*"
    r"(?:class|interface|enum|record|@interface)\b"
)


def _is_blank(s: str) -> bool:
    return not s.strip()


def _is_annotation_line(s: str) -> bool:
    return s.lstrip().startswith("@")


def _is_type_decl(s: str) -> bool:
    return bool(_TYPE_DECL_RE.match(s.strip()))


def _paren_delta(line: str) -> int:
    """Compute the (open - close) balance of ``()[]{}<>`` on a single line."""
    depth = 0
    in_str = None
    in_chr = None
    in_line_comment = False
    in_block_comment = False
    i = 0
    while i < len(line):
        ch = line[i]
        nxt = line[i + 1] if i + 1 < len(line) else ""
        if in_line_comment:
            break
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if in_chr is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_chr:
                in_chr = None
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            break
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == '"':
            in_str = '"'
            i += 1
            continue
        if ch == "'":
            in_chr = "'"
            i += 1
            continue
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        i += 1
    return depth


def _scan_braces_outside_strings(line: str, initial_depth: int = 0) -> Tuple[int, bool]:
    """Walk a line tracking brace depth outside strings/comments.

    Returns ``(final_depth, closed_at_end)``. ``closed_at_end`` is True if the
    function reached ``depth == 0`` after seeing ``{`` on this line (only
    meaningful when caller is tracking method bodies).
    """
    depth = initial_depth
    in_str = None
    in_chr = None
    in_line_comment = False
    in_block_comment = False
    closed = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ""
        if in_line_comment:
            break
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if in_chr is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_chr:
                in_chr = None
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            break
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == '"':
            in_str = '"'
            i += 1
            continue
        if ch == "'":
            in_chr = "'"
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                closed = True
                break
        i += 1
    return (depth, closed)


def find_enclosing_method(source_lines: List[str], anno_line: int) -> Tuple[str, str, int, int]:
    """Find the method whose annotation sits at ``anno_line`` (0-indexed).

    Returns ``(method_name, body, start_line, end_line)`` (1-indexed). If no
    enclosing method is found, returns ``("", "", anno_line+1, anno_line+1)``.
    """
    n = len(source_lines)

    # Skip past blank / annotation lines and any multi-line annotation args.
    i = anno_line
    paren_depth = 0
    while i < n:
        line = source_lines[i]
        if paren_depth > 0:
            paren_depth += _paren_delta(line)
            if paren_depth < 0:
                paren_depth = 0
            i += 1
            continue
        if _is_blank(line) or _is_annotation_line(line):
            opens, closes = line.count("("), line.count(")")
            if opens > closes:
                paren_depth = opens - closes
            i += 1
            continue
        break

    if i >= n or _is_type_decl(source_lines[i]):
        return ("", "", anno_line + 1, anno_line + 1)

    m = _METHOD_HEAD_RE.match(source_lines[i])
    if not m:
        return ("", "", anno_line + 1, anno_line + 1)

    start_idx = i
    method_name = m.group(1)

    # Walk forward to find the matching closing brace (string/comment-aware).
    depth = 0
    end_idx = start_idx
    for j in range(start_idx, n):
        depth, closed = _scan_braces_outside_strings(source_lines[j], depth)
        if closed:
            end_idx = j
            break
    else:
        end_idx = n - 1

    body = "\n".join(source_lines[start_idx:end_idx + 1])
    return (method_name, body, start_idx + 1, end_idx + 1)


def _detect_source_kind(source_lines: Sequence[str]) -> str:
    """Detect whether the file declares a class / interface / enum / record."""
    upper = "\n".join(source_lines[:200])  # check top-of-file, cheap
    if re.search(r"\binterface\b\s+\w+", upper):
        return "interface"
    if re.search(r"\benum\b\s+\w+", upper):
        return "enum"
    if re.search(r"\brecord\b\s+\w+", upper):
        return "record"
    if re.search(r"\bclass\b\s+\w+", upper):
        return "class"
    return "unknown"


def extract_args(text: str) -> List[str]:
    """Best-effort extraction of annotation arguments as strings.

    Handles:
        - empty ``()``
        - single string / numeric / enum literal
        - comma-separated arguments (balanced parens)
        - quoted string stripping ("" or '')

    Does NOT attempt to evaluate annotation expressions — purely textual.
    """
    m = re.search(r"@\w+(?:\.\w+)?\s*\((.*)\)\s*$", text.strip(), re.DOTALL)
    if not m:
        return []
    inner = m.group(1).strip()
    if not inner:
        return []
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    in_str = None
    for ch in inner:
        if in_str is not None:
            cur.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in "([{<":
            depth += 1
            cur.append(ch)
        elif ch in ")]}>":
            depth -= 1
            cur.append(ch)
        elif ch in ('"', "'") and depth == 0:
            in_str = ch
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    cleaned: List[str] = []
    for p in parts:
        if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'"):
            p = p[1:-1]
        cleaned.append(p)
    return cleaned


# ============================================================
# Phase 1: build annotation records
# ============================================================

def build_annotation_records(
    raw_hits: List[Dict[str, Any]],
    imports_map: Dict[str, Dict[str, str]],
    project_root: Path,
) -> List[Dict[str, Any]]:
    """Turn ast-grep hits into structured annotation records with method context.

    Each record contains: ``fqn``, ``name``, ``text``, ``args``, ``file``,
    ``line``, ``method_name``, ``method_body``, ``source`` (class/interface/...).
    """
    records: List[Dict[str, Any]] = []
    name_re = re.compile(r"@([A-Za-z_][\w.]*)")

    for hit in raw_hits:
        file_rel_raw = (hit.get("file") or "").replace("\\", "/")
        try:
            line_1idx = int(hit.get("range", {}).get("start", {}).get("line", 0)) + 1
        except (TypeError, ValueError):
            line_1idx = 0
        text = (hit.get("text") or "").strip()

        m = name_re.match(text)
        name = m.group(1) if m else text.lstrip("@").split("(", 1)[0].strip()

        abs_path = (project_root / file_rel_raw).resolve()
        file_imports = _imports_for_file(imports_map, abs_path, file_rel_raw, file_rel_raw)
        fqn = resolve_fqn(name, file_imports)

        method_name, method_body = "", ""
        source_kind = "unknown"
        try:
            source_text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log_debug(f"cannot read {abs_path}: {exc}")
            source_text = ""

        if source_text:
            source_lines = source_text.splitlines()
            anno_idx = max(line_1idx - 1, 0)
            if anno_idx < len(source_lines):
                method_name, method_body, _, _ = find_enclosing_method(source_lines, anno_idx)
                source_kind = _detect_source_kind(source_lines)

        records.append({
            "fqn": fqn,
            "name": name,
            "text": text,
            "args": extract_args(text),
            "file": file_rel_raw,
            "line": line_1idx,
            "method_name": method_name,
            "method_body": method_body,
            "source": source_kind,
        })
    return records


# ============================================================
# Phase 2: javaparser-service enrichment
# ============================================================

def _call_javaparser_service(
    project_root: Path, file_rel_path: str
) -> Optional[Dict[str, Any]]:
    """Invoke ``javaparser-service`` on a single Java file.

    Writes a temp JSON, reads it back, returns parsed payload or ``None`` on
    any failure (jar missing, non-zero exit, timeout, malformed JSON).
    """
    if not JAVAPARSER_JAR.is_file():
        log_warn(f"javaparser-service not found at {JAVAPARSER_JAR} — skipping enrichment")
        return None

    fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="jps-")
    os.close(fd)
    try:
        cmd = [
            "java", "-jar", str(JAVAPARSER_JAR),
            "--project-root", str(project_root),
            "--files", file_rel_path,
            "--output", tmp_path,
        ]
        log_debug(f"javaparser-service: {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=JAVAPARSER_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            log_warn(f"javaparser-service timed out for {file_rel_path}")
            return None
        except OSError as exc:
            log_warn(f"javaparser-service launch failed for {file_rel_path}: {exc}")
            return None

        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            err_tail = err[-1] if err else f"exit {proc.returncode}"
            log_warn(f"javaparser-service failed for {file_rel_path}: {err_tail}")
            return None

        try:
            with open(tmp_path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except (OSError, json.JSONDecodeError) as exc:
            log_warn(f"javaparser-service output unreadable for {file_rel_path}: {exc}")
            return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _format_third_party_calls(
    calls: List[Dict[str, Any]],
    ann_line_1idx: int,
    group_id: str,
) -> List[str]:
    """Filter calls inside ``[ann_line, ann_line + WINDOW)`` excluding project-internal.

    Returns ``"<declaring_class>.<selector>(<args>)"`` strings. Excludes calls whose
    ``declaring_class`` starts with ``group_id + "."`` (project-internal, not 3rd-party).
    """
    if not calls:
        return []
    out: List[str] = []
    lo, hi = ann_line_1idx, ann_line_1idx + CALL_ATTRIBUTION_WINDOW
    group_prefix = f"{group_id}." if group_id else ""
    for c in calls:
        try:
            call_line = int(c.get("line", 0) or 0)
        except (TypeError, ValueError):
            continue
        if call_line < lo or call_line >= hi:
            continue
        dc = (c.get("declaring_class", "") or "").strip()
        if not dc:
            continue
        if group_prefix and dc.startswith(group_prefix):
            continue
        selector = (c.get("selector", "") or "").strip()
        if not selector:
            continue
        args_text = (c.get("args_text", "") or "").strip()
        if not args_text:
            args_text = "()"
        elif args_text.startswith("[") and args_text.endswith("]"):
            args_text = f"({args_text[1:-1]})"
        elif not (args_text.startswith("(") and args_text.endswith(")")):
            args_text = f"({args_text})"
        out.append(f"{dc}.{selector}{args_text}")
    return out


def extract_full_method_with_annotations(
    project_root: Path, file_rel_path: str, annotation_line: int
) -> Tuple[str, str]:
    """Extract complete method source (incl. preceding annotations / Javadoc).

    Uses ``ast-grep run --kind method_declaration`` to locate the owning method,
    then walks upward from its start line to absorb leading annotations /
    Javadoc / blank lines. Returns ``(source, method_name)``; both empty on failure.
    """
    abs_path = Path(project_root) / file_rel_path
    if not abs_path.is_file():
        return ("", "")

    if _which_ast_grep() is None:
        log_warn("ast-grep not available — method body extraction will fall back to text scan")
        return _fallback_method_extraction(abs_path, annotation_line)

    cmd = [
        AST_GREP_BIN, "run",
        "--kind", METHOD_DECL_NODE_KIND,
        "--lang", "java",
        "--json=compact", str(abs_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        log_warn(f"ast-grep method scan failed: {exc}")
        return _fallback_method_extraction(abs_path, annotation_line)

    if proc.returncode != 0 and not proc.stdout.strip():
        return ("", "")

    raw = (proc.stdout or "").strip()
    if not raw:
        return ("", "")
    try:
        methods = json.loads(raw)
    except json.JSONDecodeError:
        return ("", "")
    if not isinstance(methods, list) or not methods:
        return ("", "")

    target = _find_method_for_annotation(methods, annotation_line - 1)
    if not target:
        return _fallback_method_extraction(abs_path, annotation_line)

    try:
        start_0 = int(target["range"]["start"]["line"])
        end_0 = int(target["range"]["end"]["line"])
    except (KeyError, TypeError, ValueError):
        return ("", "")

    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fp:
            lines = fp.readlines()
    except OSError:
        return ("", "")

    if start_0 >= len(lines):
        return ("", "")

    # Walk up to absorb leading annotations / Javadoc.
    actual_start = start_0
    i = start_0 - 1
    while i >= 0:
        line = lines[i].rstrip("\n").strip()
        if not line:
            actual_start = i
            i -= 1
            continue
        if line.startswith("@") or line.startswith("//"):
            actual_start = i
            i -= 1
            continue
        if line.endswith("*/"):
            j = i
            while j >= 0 and "/*" not in lines[j]:
                j -= 1
            if j < 0:
                break
            # If the line *after* the block is itself an annotation, fold it in
            if (i + 1 < len(lines) and lines[i + 1].lstrip().startswith("@")):
                i = j - 1
                continue
            actual_start = j
            i = j - 1
            continue
        break

    safe_end = min(end_0, len(lines) - 1)
    source = "".join(lines[actual_start:safe_end + 1]).lstrip("\n").lstrip()
    if len(source.encode("utf-8", errors="replace")) > MAX_METHOD_SOURCE_BYTES:
        log_debug(f"truncating oversized method body for {abs_path}:{annotation_line}")
        source = source[:MAX_METHOD_SOURCE_BYTES]
    method_name = _extract_method_name_from_source(source)
    return (source, method_name)


def _fallback_method_extraction(abs_path: Path, annotation_line: int) -> Tuple[str, str]:
    """Text-scan fallback when ast-grep method_declaration is unavailable."""
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fp:
            lines = fp.readlines()
    except OSError:
        return ("", "")
    if annotation_line < 1 or annotation_line > len(lines):
        return ("", "")
    anno_idx = annotation_line - 1
    method_name, body, _, _ = find_enclosing_method(lines, anno_idx)
    return (body, method_name)


def _extract_method_name_from_source(source: str) -> str:
    """Strip leading annotations / Javadoc and return the method name."""
    if not source:
        return ""
    # Remove line comments and block comments first.
    stripped = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    stripped = re.sub(r"//[^\n]*", "", stripped)
    # Remove @Annotation(...) tokens (with balanced parens).
    cleaned = re.sub(
        r"@\w+(?:\.\w+)?\s*(?:\((?:[^()]|\([^()]*\))*\))?",
        "", stripped,
    )
    # Find the LAST identifier followed by '(' (handles constructors & overloads).
    matches = list(re.finditer(r"\b([A-Za-z_]\w*)\s*\(", cleaned))
    if not matches:
        return ""
    return matches[-1].group(1)


def _find_method_for_annotation(
    methods: List[Dict[str, Any]],
    annotation_line_0idx: int,
) -> Optional[Dict[str, Any]]:
    """Locate the method_declaration node that owns ``annotation_line_0idx``."""
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for m in methods:
        rng = m.get("range") or {}
        start = rng.get("start") or {}
        try:
            line = int(start.get("line", -1))
        except (TypeError, ValueError):
            continue
        if line < 0:
            continue
        if line >= annotation_line_0idx:
            candidates.append((line, m))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    if candidates[0][0] - annotation_line_0idx > METHOD_OWNERSHIP_GAP:
        return None
    return candidates[0][1]


def phase2_extract_methods(
    project_root: Path,
    route_annotations: List[Dict[str, Any]],
    group_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Phase 2 main enrichment.

    For each file with route annotations: (1) call javaparser-service to get
    the full call list; (2) attach full method source via ast-grep; (3) attach
    ``method_full_info`` containing source + 3rd-party calls in the
    attribution window after the annotation line.
    """
    if not route_annotations:
        return route_annotations

    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for ann in route_annotations:
        by_file.setdefault(ann.get("file", ""), []).append(ann)

    for file_rel_path, anns in by_file.items():
        if not file_rel_path:
            for ann in anns:
                ann.setdefault("method_full_info", {"source": "", "third_party_calls": []})
            continue

        jp_result = _call_javaparser_service(project_root, file_rel_path)
        calls_in_file: List[Dict[str, Any]] = []
        if jp_result and isinstance(jp_result, dict):
            calls_in_file = jp_result.get("calls", []) or []

        for ann in anns:
            ann_line = int(ann.get("line", 0) or 0)
            src, method_name = extract_full_method_with_annotations(
                project_root, file_rel_path, ann_line
            )
            if method_name:
                ann["method_name"] = method_name
            ann["method_body"] = src
            ann["method_full_info"] = {
                "source": src,
                "third_party_calls": _format_third_party_calls(
                    calls_in_file, ann_line, group_id or ""
                ),
            }
    return route_annotations


# ============================================================
# Categorization
# ============================================================

def _wildcard_to_re(pattern: str) -> re.Pattern:
    """Compile a glob-style annotation pattern (only ``.*`` wildcard supported)."""
    if pattern.endswith(".*"):
        base = re.escape(pattern[:-2])
        return re.compile(base + r"(?:[.$][\w$]+)?$")
    return re.compile(r"^" + re.escape(pattern) + r"$")


def _match_any(fqn: str, patterns: Iterable[str]) -> bool:
    """Return True if ``fqn`` matches any of the supplied patterns."""
    if not fqn:
        return False
    return any(_wildcard_to_re(p).match(fqn) for p in patterns)


def load_categories(path: Path) -> Dict[str, Any]:
    """Load annotation-categories.json. Exits with code 2 if missing."""
    if not path.is_file():
        log_error(f"categories not found: {path}")
        sys.exit(2)
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        log_error(f"cannot parse categories file {path}: {exc}")
        sys.exit(2)
    if not isinstance(data, dict):
        log_error(f"categories file must be a JSON object, got {type(data).__name__}")
        sys.exit(2)
    return data


def filter_route_annotations(
    all_annotations: List[Dict[str, Any]],
    categories: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Keep only annotations that match the ``route`` patterns and not ``ignore``."""
    route_patterns = (categories.get("route") or {}).get("patterns", []) or []
    ignore_patterns = (categories.get("ignore") or {}).get("patterns", []) or []

    out: List[Dict[str, Any]] = []
    for ann in all_annotations:
        fqn = ann.get("fqn") or ""
        if not fqn:
            continue
        if not _match_any(fqn, route_patterns):
            continue
        if _match_any(fqn, ignore_patterns):
            continue
        out.append(ann)
    return out


def classify_annotations_by_group(
    annotations: List[Dict[str, Any]],
    group_id: str,
) -> None:
    """Tag each annotation in-place: ``project_specific`` (FQN starts with ``group_id.``) else ``public``."""
    prefix = f"{group_id}." if group_id else ""
    for ann in annotations:
        fqn = ann.get("fqn", "") or ""
        ann["annotation_source"] = "project_specific" if (prefix and fqn.startswith(prefix)) else "public"
        ann["classification"] = ann["annotation_source"]


def phase2_filter_and_enrich(
    project_root: Path,
    all_annotations: List[Dict[str, Any]],
    categories: Dict[str, Any],
    group_id: str,
    *,
    codegraph_db: Optional[Path] = None,
    use_nodes_id: bool = True,
) -> List[Dict[str, Any]]:
    """Phase 2 orchestrator: filter → enrich → classify → resolve hashkey."""
    routes = filter_route_annotations(all_annotations, categories)
    log_info(f"phase 2: {len(routes)} route candidates before enrichment")
    phase2_extract_methods(project_root, routes, group_id=group_id)
    classify_annotations_by_group(routes, group_id)
    annotate_routes_with_hashkey(
        routes,
        project_root=project_root,
        codegraph_db=codegraph_db,
        use_nodes_id=use_nodes_id,
    )
    return routes


def annotate_routes_with_hashkey(
    routes: List[Dict[str, Any]],
    *,
    project_root: Path,
    codegraph_db: Optional[Path] = None,
    use_nodes_id: bool = True,
) -> None:
    """为每条 route 注入 sig_hash + nodes_id (就地修改)。

    Strategy:
      - use_nodes_id=True + codegraph_db 可读 → resolve_hashkey() 优先走 nodes.id
      - DB 缺失或查不到 → md5 fallback (warning,统计计数)
      - use_nodes_id=False → 强制 md5 (调试 / 回归对比用)

    Counts are written to module-level counters (mutated), readable by callers
    via :func:`pop_hashkey_stats`.
    """
    global _hashkey_stats
    db_arg: Optional[Path] = None
    if use_nodes_id and codegraph_db is not None and codegraph_db.is_file():
        db_arg = codegraph_db
        log_info(f"phase 2.5: resolving nodes.id from {codegraph_db}")
    elif use_nodes_id:
        log_warn(
            f"codegraph SQLite not found at {codegraph_db} — "
            "falling back to md5 hashkey (line shifts will break stability)"
        )

    nodes_id_hits = 0
    md5_fallbacks = 0
    for r in routes:
        sig, nid = resolve_hashkey(
            file_path=str(r.get("file", "") or ""),
            annotation_line=int(r.get("line", 0) or 0),
            annotation_text=str(r.get("text", "") or ""),
            codegraph_db=db_arg,
            project_root=project_root,
            use_nodes_id=use_nodes_id,
        )
        r["sig_hash"] = sig
        r["nodes_id"] = nid
        if nid:
            nodes_id_hits += 1
        else:
            md5_fallbacks += 1
    _hashkey_stats = {
        "nodes_id_hits": nodes_id_hits,
        "md5_fallbacks": md5_fallbacks,
        "use_nodes_id": use_nodes_id,
        "codegraph_db": str(codegraph_db) if codegraph_db else None,
    }
    log_info(
        f"phase 2.5: hashkey resolved — {nodes_id_hits} nodes.id, "
        f"{md5_fallbacks} md5 fallback"
    )


_hashkey_stats: Dict[str, Any] = {
    "nodes_id_hits": 0,
    "md5_fallbacks": 0,
    "use_nodes_id": True,
    "codegraph_db": None,
}


def pop_hashkey_stats() -> Dict[str, Any]:
    """返回并清空本次扫描的 hashkey 统计 (供 JSON 输出使用)。"""
    global _hashkey_stats
    out = dict(_hashkey_stats)
    _hashkey_stats = {
        "nodes_id_hits": 0,
        "md5_fallbacks": 0,
        "use_nodes_id": True,
        "codegraph_db": None,
    }
    return out


def _resolve_codegraph_db(
    project_root: Path,
    codegraph_db: Optional[Path],
) -> Path:
    """Pick the effective codegraph DB path (CLI flag → project_root/.codegraph → default)."""
    if codegraph_db is not None:
        return Path(codegraph_db)
    candidate = project_root / ".codegraph" / "codegraph.db"
    if candidate.is_file():
        return candidate
    # Last-resort default; resolver treats missing file as "fall back to md5".
    return DEFAULT_CODEGRAPH_DB


# ============================================================
# Pipeline + IO
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_phase1_output(
    path: Path,
    project: Path,
    group_id: str,
    annots: List[Dict[str, Any]],
) -> None:
    """Write ``all_annotations.json``."""
    payload = {
        "project": str(project),
        "group_id": group_id,
        "scan_time": _now_iso(),
        "annotations": annots,
        "summary": {"total_annotations": len(annots)},
    }
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def write_phase2_output(
    path: Path,
    project: Path,
    group_id: str,
    routes: List[Dict[str, Any]],
    hashkey_stats: Optional[Dict[str, Any]] = None,
) -> None:
    """Write ``route_annotations.json`` with classification + hashkey summary."""
    proj_count = sum(1 for r in routes if r.get("annotation_source") == "project_specific")
    pub_count = sum(1 for r in routes if r.get("annotation_source") == "public")
    nodes_id_count = sum(1 for r in routes if r.get("nodes_id"))
    md5_count = sum(1 for r in routes if not r.get("nodes_id"))
    payload = {
        "project": str(project),
        "group_id": group_id,
        "scan_time": _now_iso(),
        "route_annotations": routes,
        "summary": {
            "route_annotations": len(routes),
            "project_specific": proj_count,
            "public": pub_count,
            "nodes_id_hashes": nodes_id_count,
            "md5_hashes": md5_count,
        },
    }
    if hashkey_stats:
        payload["hashkey_stats"] = hashkey_stats
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def _safe_path_label(path: Path) -> str:
    return str(path).replace("\\", "/")


def scan(
    project_root: Path,
    group_id: str,
    categories: Dict[str, Any],
    *,
    codegraph_db: Optional[Path] = None,
    use_nodes_id: bool = True,
) -> Dict[str, Any]:
    """Run the full 3-phase pipeline and return an in-memory report.

    Phases:
      1. ast-grep → annotation records (file/line/fqn/method_name/method_body/source)
      2. filter route patterns → javaparser-service enrichment → classification
      3. codegraph nodes.id resolution → sig_hash + nodes_id per route
    """
    log_info(f"project: {_safe_path_label(project_root)}")
    log_info(f"group_id: {group_id}")

    raw_hits = run_ast_grep_annotations(project_root)
    log_info(f"phase 1 raw hits: {len(raw_hits)}")

    log_info("collecting Java imports…")
    imports_map = collect_imports(project_root)
    log_info(f"indexed {len(imports_map)} java files for imports")

    log_info("resolving FQNs and extracting method context…")
    all_records = build_annotation_records(raw_hits, imports_map, project_root)
    log_info(f"phase 1 annotations: {len(all_records)}")

    db_path = _resolve_codegraph_db(project_root, codegraph_db)
    log_info("phase 2: filter + javaparser-service enrichment…")
    route_records = phase2_filter_and_enrich(
        project_root, all_records, categories, group_id,
        codegraph_db=db_path,
        use_nodes_id=use_nodes_id,
    )
    log_info(f"phase 2 route annotations: {len(route_records)}")

    proj_count = sum(1 for r in route_records if r.get("annotation_source") == "project_specific")
    pub_count = sum(1 for r in route_records if r.get("annotation_source") == "public")
    hk_stats = pop_hashkey_stats()
    return {
        "project": _safe_path_label(project_root),
        "group_id": group_id,
        "scan_time": _now_iso(),
        "all_annotations": all_records,
        "route_annotations": route_records,
        "hashkey_stats": hk_stats,
        "summary": {
            "total_annotations": len(all_records),
            "route_annotations": len(route_records),
            "project_specific": proj_count,
            "public": pub_count,
            "nodes_id_hashes": hk_stats["nodes_id_hits"],
            "md5_hashes": hk_stats["md5_fallbacks"],
        },
    }


def scan_attack_surface(
    project_root: Path,
    db_path: Path = DEFAULT_CODEGRAPH_DB,
    use_nodes_id: bool = True,
    *,
    group_id: str = "",
    categories: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Top-level attack-surface scan entry point.

    Returns:
        {
            "routes":     [{"fqn", "http_method", "file", "line",
                            "sig_hash", "nodes_id", "annotations": [...]}],
            "filters":    [...],   # reserved for future Phase B
            "interceptors": [...], # reserved for future Phase B
            "summary":    {...},
            "hashkey_stats": {...},
        }

    Backward compat: if ``db_path`` is missing or ``use_nodes_id=False``,
    ``sig_hash`` falls back to legacy md5 and ``nodes_id`` is ``None``.
    """
    if categories is None:
        categories = load_categories(DEFAULT_CATEGORIES_PATH)

    project_root = Path(project_root).resolve()
    db_path = Path(db_path)
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()

    if not db_path.is_file():
        log_warn(
            f"codegraph DB not found at {db_path}; "
            "sig_hash will fall back to md5 (line-shift unstable)"
        )

    raw_hits = run_ast_grep_annotations(project_root)
    imports_map = collect_imports(project_root)
    all_records = build_annotation_records(raw_hits, imports_map, project_root)
    routes = phase2_filter_and_enrich(
        project_root, all_records, categories, group_id,
        codegraph_db=db_path,
        use_nodes_id=use_nodes_id,
    )
    hk_stats = pop_hashkey_stats()

    proj_count = sum(1 for r in routes if r.get("annotation_source") == "project_specific")
    pub_count = sum(1 for r in routes if r.get("annotation_source") == "public")
    nodes_id_count = hk_stats["nodes_id_hits"]
    md5_count = hk_stats["md5_fallbacks"]

    return {
        "project": _safe_path_label(project_root),
        "group_id": group_id,
        "scan_time": _now_iso(),
        "routes": routes,
        "filters": [],      # reserved for future Phase B
        "interceptors": [],  # reserved for future Phase B
        "hashkey_stats": hk_stats,
        "summary": {
            "routes": len(routes),
            "project_specific": proj_count,
            "public": pub_count,
            "nodes_id_hashes": nodes_id_count,
            "md5_hashes": md5_count,
        },
    }


# ============================================================
# Validation helpers
# ============================================================

def _validate_group_id(group_id: str) -> Optional[str]:
    """A reasonable Maven groupId contains '.' and only valid Java id chars."""
    if not group_id:
        return "group-id is empty"
    if not re.match(r"^[A-Za-z_][\w.]*$", group_id):
        return f"group-id contains invalid characters: {group_id!r}"
    return None


def _validate_output_dir(output_dir: Path) -> Optional[str]:
    """Check whether ``output_dir`` can be created and written to."""
    parent = output_dir.parent if output_dir.parent != output_dir else output_dir
    if not parent.is_dir():
        return f"output parent directory does not exist: {parent}"
    return None


# ============================================================
# CLI
# ============================================================

def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser (kept separate for testability)."""
    parser = argparse.ArgumentParser(
        prog="attack_surface_scanner",
        description=(
            "Generic Java attack-surface scanner. Three-phase pipeline: "
            "(1) ast-grep annotation scan, (2) javaparser-service enrichment, "
            "(3) codegraph nodes.id hashkey resolution. Produces "
            "all_annotations.json + route_annotations.json."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python attack_surface_scanner.py \\\n"
            "      --project /path/to/repo --group-id com.example.app \\\n"
            "      --output ./out\n"
            "\n"
            "  # Force legacy md5 hashkey (skip codegraph lookup):\n"
            "  python attack_surface_scanner.py --project ... --no-nodes-id\n"
            "\n"
            "  # Use a custom codegraph SQLite path:\n"
            "  python attack_surface_scanner.py \\\n"
            "      --project ... --codegraph-db /path/to/codegraph.db\n"
        ),
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Java project root directory (containing .java files).",
    )
    parser.add_argument(
        "--group-id",
        required=True,
        help="Maven groupId, used to separate project-specific vs public annotations.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory. Will be created if missing.",
    )
    parser.add_argument(
        "--categories",
        default=str(DEFAULT_CATEGORIES_PATH),
        help="Path to annotation-categories.json.",
    )
    parser.add_argument(
        "--codegraph-db",
        default=None,
        help=(
            "Path to codegraph SQLite (default: <project>/.codegraph/codegraph.db). "
            "Used as source of nodes.id hashkeys. Missing DB → md5 fallback."
        ),
    )
    parser.add_argument(
        "--no-nodes-id",
        dest="use_nodes_id",
        action="store_false",
        help="Disable codegraph nodes.id lookup; force md5 hashkey (legacy mode).",
    )
    parser.set_defaults(use_nodes_id=True)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages to stderr (errors still printed).",
    )
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.quiet:
        # Replace log helpers with no-ops.
        global log_info, log_warn, log_error, log_debug
        log_info = lambda *a, **k: None        # type: ignore[assignment]
        log_warn = lambda *a, **k: None        # type: ignore[assignment]
        log_debug = lambda *a, **k: None       # type: ignore[assignment]

    project_root = Path(args.project).resolve()
    if not project_root.is_dir():
        log_error(f"--project not a directory: {project_root}")
        return 2

    output_dir = Path(args.output).resolve()
    if err := _validate_output_dir(output_dir):
        log_error(err)
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)

    if err := _validate_group_id(args.group_id):
        log_error(err)
        return 2

    codegraph_db = Path(args.codegraph_db) if args.codegraph_db else None
    categories = load_categories(Path(args.categories))
    report = scan(
        project_root, args.group_id, categories,
        codegraph_db=codegraph_db,
        use_nodes_id=args.use_nodes_id,
    )

    all_path = output_dir / "all_annotations.json"
    route_path = output_dir / "route_annotations.json"
    write_phase1_output(all_path, project_root, args.group_id, report["all_annotations"])
    write_phase2_output(
        route_path, project_root, args.group_id,
        report["route_annotations"],
        hashkey_stats=report.get("hashkey_stats"),
    )

    s = report["summary"]
    log_info(
        f"done: {s['total_annotations']} annotations total, "
        f"{s['route_annotations']} routes "
        f"({s['project_specific']} project-specific, {s['public']} public); "
        f"hashkeys: {s['nodes_id_hashes']} nodes.id, "
        f"{s['md5_hashes']} md5 fallback"
    )
    log_info(f"wrote {all_path}")
    log_info(f"wrote {route_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())