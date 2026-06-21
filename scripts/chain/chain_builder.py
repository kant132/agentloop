"""
chain_builder.py
================

调用链引擎主入口: **CTE 前向遍历 + sink 提取 + 体内注释注入 + Memurai 缓存**
一体化编排器。

设计依据: ``D:\\agentloop\\design-docs\\chain-sql-engine.md`` §"总体工作流"。

Pipeline
--------
1. ``sqlite-extract-chain.py:resolve_entry()`` 把 entry fqn → nodes.id
2. ``sqlite-extract-chain.py:extract_recursive()`` 走 CTE RECURSIVE 拉出 depth-≤N 的链
3. 对每个链节点:
   a. 反查 codegraph 拿 ``end_line``、签名
   b. 读源文件 slice 出方法体 (start_line, end_line 均为 1-based)
   c. 调 ``method_calls_extractor.extract_method_calls()`` 拿本文件全部 method 调用
      (按文件级 cache,避免重复 JAR 调用)
   d. 用 ``method_start_line == start_line`` 过滤出本方法的出向调用
   e. ``is_sink=True`` 视为 sink(默认策略: 非 groupId 命名空间)
   f. 调 ``scanner_utils.inject_sink_comment`` 把 ``// sink: <FQN>`` 注入 body
4. 整链算 total_nodes / total_edges / total_sinks / cycle_detected
5. ``Memurai.set(key, json, ex=ttl)`` 写 ``{groupId}:audit:chain:{sigHash}`` 缓存 (可选)

API
---
- ``build_chain(entry_fqn, group_id, project_root, ...)`` — 单链, 深度优先顺序遍历结果
- ``build_all_chains_for_endpoint(entry_fqn, ...)`` — 同一 entry 的**所有**根到叶路径变体

CLI
---
    python chain_builder.py \\
        --project-root D:\\code\\WebGoat-2025.3 \\
        --db D:\\code\\WebGoat-2025.3\\codegraph.db \\
        --group-id org.owasp.webgoat \\
        --entry "org.owasp.webgoat.lessons.sqlinjection.advanced.SqlInjectionLesson6b#completed" \\
        --depth 20 \\
        --output chain.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ============================================================== 路径常量

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent          # D:\agentloop
_DEFAULT_DB = _REPO_ROOT / "codegraph.db"
_DEFAULT_PROJECT_ROOT = _REPO_ROOT / "projects" / "_template"


# ============================================================== logger

logger = logging.getLogger("chain_builder")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[%(name)s %(levelname)s] %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


# ============================================================== 兄弟模块导入
#
# 兄弟模块文件名带连字符 (sqlite-extract-chain.py / method-calls-extractor.py),
# 不是合法 Python module name, 必须用 importlib 按文件路径加载。
# scanner_utils.py 是合法名, 可以用普通 import。

import importlib.util as _il_util
from types import ModuleType as _ModuleType


def _load_module_from_file(modname: str, filepath: Path) -> _ModuleType:
    """从文件路径加载一个 Python 模块并以 ``modname`` 注册到 sys.modules。"""
    spec = _il_util.spec_from_file_location(modname, filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法构造 spec: {filepath}")
    mod = _il_util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


# scripts/chain/ 下的连字符兄弟
_sec = _load_module_from_file(
    "_chain_builder_sqlite_extract_chain",
    _HERE / "sqlite-extract-chain.py",
)
_mce = _load_module_from_file(
    "_chain_builder_method_calls_extractor",
    _HERE / "method_calls_extractor.py",
)
_sr = _load_module_from_file(
    "_chain_builder_sink_registry",
    _HERE / "sink_registry.py",
)

# scripts/chain/jar_analyzer_cte.py — jar-analyzer.db CTE 支持 (可选; 文件不存在时跳过)
try:
    _jac = _load_module_from_file(
        "_chain_builder_jar_analyzer_cte",
        _HERE / "jar_analyzer_cte.py",
    )
except (ImportError, OSError):
    _jac = None

# scripts/redis/redis-batch-prefetch.py — 跨兄弟目录导入 (连字符文件名)
_rbp = _load_module_from_file(
    "_chain_builder_redis_batch_prefetch",
    _HERE.parent / "redis" / "redis-batch-prefetch.py",
)

# scripts/chain/chain_file_writer.py — 同目录模块 (合法模块名, 但用 importlib 保持一致性)
_cfw = _load_module_from_file(
    "_chain_builder_chain_file_writer",
    _HERE / "chain_file_writer.py",
)

# scripts/ast/scanner_utils.py — 通过 sys.path 走普通 import
_ast_dir = _REPO_ROOT / "scripts" / "ast"
if str(_ast_dir) not in sys.path:
    sys.path.insert(0, str(_ast_dir))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
try:
    import scanner_utils as _su  # type: ignore
except ImportError:
    try:
        from scripts.ast import scanner_utils as _su  # type: ignore
    except ImportError as _e:
        raise ImportError(
            f"无法 import scanner_utils (paths tried: scanner_utils, "
            f"scripts.ast.scanner_utils, {_ast_dir}): {_e}"
        )


# ============================================================== 数据结构

@dataclass
class ChainNode:
    """单个链节点的最终展示形态。"""
    fqn: str
    node_id: str
    file: Optional[str]
    start_line: int
    end_line: Optional[int] = None
    body: Optional[str] = None          # 注入 sink 注释后的方法体
    depth: int = 0
    sinks: List[str] = field(default_factory=list)        # 体内已识别的 sink FQN
    edges: List[str] = field(default_factory=list)        # 该节点出向 calls 边的 target id


# ============================================================== 内部工具函数

def _log(msg: str, *args: Any) -> None:
    logger.info(msg, *args)


def _sig_hash_for_entry(node_id: str) -> str:
    """entry 的 sigHash = sha256(nodes.id)[:16]。

    注: 不再使用 md5(fqn+params) (设计文档已决议改用 nodes.id 体系)。
    """
    return hashlib.sha256(str(node_id).encode("utf-8")).hexdigest()[:16]


def _open_db(db_path: Path) -> sqlite3.Connection:
    """打开 codegraph SQLite (只读 + 短超时, 避免与 codegraph-init 写锁竞争)。"""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"codegraph.db 不存在: {db_path}")
    # 5 秒 busy timeout, 避免短暂持锁的 init 进程冲突
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_node_meta(
    conn: sqlite3.Connection,
    node_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """批量反查 nodes 表拿 end_line / signature / decorators / qualified_name。"""
    if not node_ids:
        return {}
    placeholders = ",".join("?" * len(node_ids))
    cur = conn.execute(
        f"SELECT id, qualified_name, end_line, signature, decorators, kind "
        f"FROM nodes WHERE id IN ({placeholders})",
        tuple(node_ids),
    )
    return {row["id"]: dict(row) for row in cur.fetchall()}


def _fetch_outgoing_edges(
    conn: sqlite3.Connection,
    node_ids: Sequence[str],
) -> Dict[str, List[str]]:
    """批量反查 edges 拿每个节点的出向 calls 边 target id 列表。"""
    if not node_ids:
        return {}
    placeholders = ",".join("?" * len(node_ids))
    cur = conn.execute(
        f"SELECT source, target FROM edges "
        f"WHERE source IN ({placeholders}) AND kind = 'calls'",
        tuple(node_ids),
    )
    out: Dict[str, List[str]] = {nid: [] for nid in node_ids}
    for row in cur.fetchall():
        out.setdefault(row["source"], []).append(row["target"])
    return out


def _resolve_codegraph_meta_for_jar_nodes(
    conn: sqlite3.Connection,
    jar_rows: List[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Cross-reference jar-analyzer chain nodes with codegraph for file metadata.

    Converts jar-analyzer qualified_name ('pkg.Cls::method') to codegraph format
    ('pkg.Cls#method') and batch-queries codegraph nodes table.  Returns dict
    keyed by jar-analyzer method_id:

    - matching codegraph node found → dict with codegraph_node_id, file_path,
      start_line, end_line, qualified_name, signature
    - not found → None
    """
    if not jar_rows:
        return {}

    # Convert qualified_names: jar-analyzer '::' → codegraph '#'
    codegraph_fqns: List[str] = []
    mid_to_cg_fqn: Dict[str, str] = {}
    for r in jar_rows:
        qname = r.get("qualified_name", "")
        cg_fqn = qname.replace("::", "#") if "::" in qname else qname
        codegraph_fqns.append(cg_fqn)
        mid_to_cg_fqn[r["id"]] = cg_fqn

    # Deduplicate for batch query
    unique_fqns = list(set(codegraph_fqns))
    if not unique_fqns:
        return {mid: None for mid in mid_to_cg_fqn}

    placeholders = ",".join("?" * len(unique_fqns))
    cur = conn.execute(
        f"SELECT id, qualified_name, file_path, start_line, end_line, signature "
        f"FROM nodes WHERE qualified_name IN ({placeholders})",
        tuple(unique_fqns),
    )
    cg_fqn_to_meta: Dict[str, Dict[str, Any]] = {row["qualified_name"]: dict(row) for row in cur.fetchall()}

    result: Dict[str, Optional[Dict[str, Any]]] = {}
    for mid, cg_fqn in mid_to_cg_fqn.items():
        cg_meta = cg_fqn_to_meta.get(cg_fqn)
        if cg_meta:
            result[mid] = {
                "codegraph_node_id": cg_meta["id"],
                "file_path": cg_meta.get("file_path"),
                "start_line": cg_meta.get("start_line"),
                "end_line": cg_meta.get("end_line"),
                "qualified_name": cg_meta.get("qualified_name"),
                "signature": cg_meta.get("signature"),
            }
        else:
            result[mid] = None
    return result


def _resolve_file_path(
    file_path: Optional[str],
    project_root: Path,
) -> Optional[Path]:
    """codegraph 存的 file_path 可能是绝对或相对;都尝试一次。"""
    if not file_path:
        return None
    p = Path(file_path)
    if p.is_file():
        return p
    candidate = (project_root / file_path).resolve()
    if candidate.is_file():
        return candidate
    # Windows 路径分隔符混用兜底
    candidate2 = (project_root / file_path.replace("/", "\\")).resolve()
    if candidate2.is_file():
        return candidate2
    return None


def _read_method_body(
    file_path: Path,
    start_line: int,
    end_line: Optional[int],
) -> Optional[str]:
    """读源文件 [start_line, end_line] 切片 (两边均 1-based, 包含)。

    end_line=None 时, 从 start_line 扫描到方法体闭合大括号。
    任意边界缺失 → 返回 None (调用方决定降级)。
    """
    if not file_path.is_file():
        return None
    if start_line <= 0:
        return None
    if end_line is None:
        # Scan from start_line to find method closing brace
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            if start_line > len(lines):
                return None
            brace_depth = 0
            found_open = False
            end = start_line
            for i in range(start_line - 1, len(lines)):
                line = lines[i]
                for ch in line:
                    if ch == '{':
                        brace_depth += 1
                        found_open = True
                    elif ch == '}':
                        brace_depth -= 1
                if found_open and brace_depth <= 0:
                    end = i + 1
                    break
            if found_open and brace_depth <= 0:
                return "".join(lines[start_line - 1:end])
            return None
        except OSError:
            return None
    if end_line < start_line:
        return None
    try:
        # 1-based → 0-based 切片
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        if end_line > len(lines):
            end_line = len(lines)
        return "".join(lines[start_line - 1: end_line])
    except OSError as e:
        _log("read body fail: %s [%d-%d]: %s", file_path, start_line, end_line, e)
        return None


def _annotate_body_with_sinks(
    body: str,
    sinks: Sequence[str],
) -> str:
    """对 body 每一行,若包含 sink FQN 的方法名 + '(', 在行前注入 ``// sink: <FQN>``。

    复用 ``scanner_utils.inject_sink_comment`` (设计文档 §8)。

    注: JAR 输出的 calledFQN 形如 ``"java.sql.Statement.executeQuery(query)"`` (含实参文本),
    而 ``inject_sink_comment`` 内部用 ``fqn_to_method_name`` 提取方法名后, 会再拼一个
    ``(`` 去原行匹配。本函数先做一次**实参剥离 + 尾括号剥离**, 把
    ``"pkg.Cls.method(args)"`` 归一为 ``"pkg.Cls.method"`` —— 这样
    ``fqn_to_method_name`` 返回 ``"method"``, 拼出 ``"method("`` 才能在源码行匹配上。
    (之所以不能保留尾括号, 是因为 ``inject_sink_comment`` 会拼出 ``method((`` 这种
    永远不匹配的字符串。)
    """
    if not body or not sinks:
        return body

    def _normalize(fqn: str) -> str:
        i = fqn.find("(")
        if i >= 0:
            return fqn[:i]
        return fqn

    norm_set = {_normalize(s) for s in sinks if s}
    annotated: List[str] = []
    for line in body.splitlines(keepends=True):
        new_line = _su.inject_sink_comment(line, list(norm_set), norm_set)
        annotated.append(new_line)
    return "".join(annotated)


# ============================================================== 批量 JAR 调用 (--config 方式)
#
# 旧的 per-file 调用 (extract_method_calls 每文件 1 次 subprocess) 有两个问题:
#   1. 符号无法跨文件解析 → 短名 (e.g. "execute(query)") 被误判为 sink
#   2. N 次 subprocess 启动开销
# --config 方式一次性把所有文件交给 JAR, JAR 内部多线程 + 跨文件符号解析。

def _batch_fetch_file_calls(
    file_paths: List[Path],
    source_root: Optional[Path],
    group_id: str,
    jar_path: Optional[Path],
    log: bool = True,
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """一次性调 ``extract_method_calls_via_config`` 处理所有文件。

    Returns
    -------
    (cache, failures):
        cache 的 key = ``str(fp.resolve())`` (Windows 绝对路径, 反斜杠)。
        调用方用 :func:`_get_file_calls_with_cache` 查; miss 时自动回退到
        旧 per-file 模式。
        failures 仅记录回退仍失败 (raise) 的文件, 不记录 batch miss (miss 会
        触发回退, 回退成功就不算失败)。
    """
    cache: Dict[str, List[Dict[str, Any]]] = {}
    failures: List[str] = []
    if not file_paths:
        return cache, failures

    try:
        all_records = _mce.extract_method_calls_via_config(
            files=file_paths,
            source_root=source_root,
            group_id=group_id,
            jar_path=jar_path,
            workers=4,
            timeout=300,
            log=log,
        )
    except Exception as e:  # noqa: BLE001 - 整批失败, 全部回退到单文件
        _log("batch extract FAIL (将逐文件回退): %s", e)
        return cache, failures

    # JAR 返回的 file 字段 = str(fp).replace("\\", "/") (forward-slash 绝对路径)
    # 构建 forward-slash → input fp 映射, 把 records 挂到 resolved abs key 下
    fs_to_fp: Dict[str, Path] = {
        str(fp).replace("\\", "/"): fp for fp in file_paths
    }
    matched = 0
    for rec in all_records:
        rec_file = rec.get("file", "")
        fp = fs_to_fp.get(rec_file)
        if fp is not None:
            key = str(fp.resolve())
            cache.setdefault(key, []).append(rec)
            matched += 1
        else:
            # 未匹配的 record (JAR 可能对配置外的文件输出) — 按 rec_file 索引兜底
            cache.setdefault(rec_file, []).append(rec)

    _log(
        "batch extract OK: %d records, %d/%d files matched",
        len(all_records), len({k for k, v in cache.items() if v}), len(file_paths),
    )
    return cache, failures


def _get_file_calls_with_cache(
    file_path: Path,
    cache: Dict[str, List[Dict[str, Any]]],
    source_root: Optional[Path],
    group_id: str,
    jar_path: Optional[Path],
    failures: List[str],
) -> List[Dict[str, Any]]:
    """从 cache 查 ``file_path`` 的 records, miss 时回退到单文件 JAR 调用。

    cache 由 :func:`_batch_fetch_file_calls` 预填, key = resolved abs 路径。
    miss (文件未在 batch 列表, 或 JAR 没输出该文件) 时调旧 per-file
    :func:`extract_method_calls`, 结果也写回 cache 避免重复回退。
    """
    # 主 key: resolved abs (Windows 反斜杠) — batch fetch 用这个
    key = str(file_path.resolve())
    if key in cache:
        return cache[key]
    # 备选 key: forward-slash abs
    fs_key = key.replace("\\", "/")
    if fs_key in cache:
        return cache[fs_key]
    # 备选 key: relative POSIX (旧 extract_method_calls 返回这个)
    if source_root:
        try:
            rel = file_path.resolve().relative_to(
                Path(source_root).resolve()
            ).as_posix()
            if rel in cache:
                return cache[rel]
        except ValueError:
            pass

    # Miss → 用 --config 模式回退（单文件但带 sourceRoot，符号解析更准）
    try:
        records = _mce.extract_method_calls_via_config(
            files=[file_path],
            source_root=source_root,
            group_id=group_id,
            jar_path=jar_path,
            workers=1,
            timeout=30,
            log=False,
        )
    except Exception as e:  # noqa: BLE001
        _log("file calls extract FAIL: %s : %s", file_path, e)
        records = []
        failures.append(str(file_path))
    cache[key] = records  # 写回 cache 避免同一文件重复回退
    return records


# ============================================================== 主 API

def build_chain(
    entry_fqn: str,
    group_id: str,
    project_root: Path,
    db_path: Path = _DEFAULT_DB,
    max_depth: int = 20,
    memurai_client: Any = None,
    ttl: int = 86400,
    jar_path: Optional[Path] = None,
    source_root: Optional[Path] = None,
    loop_audit_dir: Optional[Path] = None,
    jar_analyzer_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """构建**单条**调用链 (entry → 所有 reachable 节点, depth-ordered)。

    Parameters
    ----------
    entry_fqn:
        入口 method 的 qualified_name, 形如
        ``org.owasp.webgoat.lessons.sqlinjection.advanced.SqlInjectionLesson6b#completed``
        或 ``pkg::Class::method`` (codegraph 双格式皆接受)。
    group_id:
        项目 groupId, 例如 ``"org.owasp.webgoat"``。**所有不以它开头的 calledFQN
        视为 sink** (设计文档 §"关键设计决策")。
    project_root:
        项目根目录, 用于把 codegraph 里的 file_path (相对或绝对) 解析为可读实体。
    db_path:
        codegraph SQLite 路径。
    max_depth:
        CTE 递归深度上限, 默认 20。
    memurai_client:
        可选。``scripts.redis.memurai_client.Memurai`` 实例。
        提供时, 把整链结果以 ``{groupId}:audit:chain:{sigHash}`` 为 key 写入缓存。
    ttl:
        缓存过期秒数, 默认 86400 (24h, 与设计文档一致)。
    jar_path:
        ``java-method-call-extractor-1.0.0.jar`` 路径; 省略走 method_calls_extractor
        的默认路径。
    source_root:
        传给 JAR 的 sourceRoot 参数; 默认与 project_root 同。

    Returns
    -------
    dict::

        {
          "entry_fqn": str,
          "sig_hash": str,           # sha256(entry_id)[:16]
          "chain": [{fqn, file, start_line, end_line, body, depth, sinks, edges}, ...],
          "total_nodes": int,
          "total_edges": int,
          "total_sinks": int,
          "cycle_detected": bool,
        }
    """
    project_root = Path(project_root)
    db_path = Path(db_path)
    if source_root is None:
        # 按优先级探测源码根目录:
        # 1. src/main/java (Maven/Gradle 标准项目)
        # 2. sources (JADX 反编译输出)
        # 3. project_root (兜底)
        for candidate in [
            project_root / "src" / "main" / "java",
            project_root / "sources",
        ]:
            if candidate.is_dir():
                source_root = candidate
                break
        else:
            source_root = project_root

    # 1. entry resolution: try jar-analyzer first, fall back to codegraph
    used_jar_analyzer = False
    entry_id: str = ""  # will be set by one of the branches below

    if jar_analyzer_db_path is not None:
        if _jac is None:
            raise ImportError(
                "jar_analyzer_cte.py 未加载, 无法使用 --jar-analyzer-db; "
                "请确保 scripts/chain/jar_analyzer_cte.py 存在"
            )
        ja_entry_id = _jac.resolve_entry_jar_analyzer(  # type: ignore[union-attr]
            str(jar_analyzer_db_path), entry_fqn,
        )
        if ja_entry_id:
            entry_id = ja_entry_id
            used_jar_analyzer = True
            _log("jar-analyzer entry resolved: %s → %s", entry_fqn, ja_entry_id)

    if not used_jar_analyzer:
        cg_entry_id = _sec.resolve_entry(str(db_path), entry_fqn)
        if not cg_entry_id:
            raise LookupError(
                f"entry_fqn 在 codegraph 中找不到 method 节点: {entry_fqn!r} "
                f"(db={db_path})"
            )
        entry_id = cg_entry_id

    assert entry_id, "entry_id 未被设置 (逻辑错误)"

    # 检查入口方法是否有参数
    #   jar-analyzer 没有 signature 列, 需从 codegraph 查 (按 qualified_name 匹配)
    entry_has_params = True
    if used_jar_analyzer:
        # jar-analyzer qualified_name 用 '::', codegraph 用 '#'
        cg_entry_fqn = entry_fqn  # 用户传入的 entry_fqn 已是 '#' 格式
        with _open_db(db_path) as conn:
            row = conn.execute(
                "SELECT id, signature FROM nodes WHERE qualified_name = ? LIMIT 1",
                (cg_entry_fqn,),
            ).fetchone()
        if row:
            sig = row["signature"] or ""
            entry_has_params = (
                "()" not in sig
                or len(sig) > sig.find(")") + 1 > sig.find("(") + 1
            )
        else:
            # 无 codegraph 匹配 → 保守假设有参数
            entry_has_params = True
    else:
        with _open_db(db_path) as conn:
            row = conn.execute(
                "SELECT signature FROM nodes WHERE id = ?", (entry_id,)
            ).fetchone()
        if row:
            sig = row["signature"] or ""
            entry_has_params = (
                "()" not in sig
                or len(sig) > sig.find(")") + 1 > sig.find("(") + 1
            )

    # 2. CTE 递归拿链 (depth-ordered)
    #   used_jar_analyzer=True 意味着 _jac 已通过上方 None 检查 (类型窄化不传导, 加 ignore)
    if used_jar_analyzer:
        raw_rows = _jac.extract_recursive_with_impl(  # type: ignore[union-attr]
            str(jar_analyzer_db_path), entry_id, max_depth,
        )
    else:
        raw_rows = _sec.extract_recursive(str(db_path), entry_id, max_depth)

    if not raw_rows:
        _log("entry %s → CTE 返回空链 (depth=%d, db=%s)",
             entry_id, max_depth,
             jar_analyzer_db_path if used_jar_analyzer else db_path)
        return _empty_chain_result(entry_fqn, entry_id)

    node_ids = [r["id"] for r in raw_rows]

    # 3. 反查 node 元信息 + 出向边
    #   jar-analyzer 模式: 交叉引用 codegraph 拿 file_path/start_line/end_line/edges
    #   codegraph 模式: 直接查 codegraph (现有逻辑)
    jar_cg_meta: Dict[str, Optional[Dict[str, Any]]] = {}
    resolved_meta: Dict[str, Dict[str, Any]] = {}
    resolved_edges_map: Dict[str, List[str]] = {}
    meta_map: Dict[str, Dict[str, Any]] = {}
    edges_map: Dict[str, List[str]] = {}

    if used_jar_analyzer:
        with _open_db(db_path) as conn:
            jar_cg_meta = _resolve_codegraph_meta_for_jar_nodes(conn, raw_rows)
            # 收集所有匹配到的 codegraph node_id, 批量查 edges
            cg_node_ids = [
                m["codegraph_node_id"]
                for m in jar_cg_meta.values()
                if m and m.get("codegraph_node_id")
            ]
            cg_edges = _fetch_outgoing_edges(conn, cg_node_ids) if cg_node_ids else {}

        # 构建每个 jar-analyzer method_id 的综合元信息 + edges 映射
        for r in raw_rows:
            mid = r["id"]
            cg_info = jar_cg_meta.get(mid)
            if cg_info:
                resolved_meta[mid] = {
                    "node_id": cg_info["codegraph_node_id"],
                    "fqn": cg_info.get("qualified_name")
                           or r.get("qualified_name", "").replace("::", "#"),
                    "file_path": cg_info.get("file_path"),
                    "start_line": cg_info.get("start_line") or r.get("start_line", 0),
                    "end_line": cg_info.get("end_line"),
                }
                # edges 用 codegraph node_id 查
                resolved_edges_map[mid] = cg_edges.get(
                    cg_info["codegraph_node_id"], [],
                )
            else:
                resolved_meta[mid] = {
                    "node_id": mid,
                    "fqn": r.get("qualified_name", "").replace("::", "#"),
                    "file_path": None,
                    "start_line": r.get("start_line", 0) or 0,
                    "end_line": None,
                }
                resolved_edges_map[mid] = []
    else:
        with _open_db(db_path) as conn:
            meta_map = _fetch_node_meta(conn, node_ids)
            edges_map = _fetch_outgoing_edges(conn, node_ids)

    # 4. 收集链上所有唯一文件路径, 一次性调 JAR --config (符号跨文件解析更准,
    #    短名不会被误判为 sink; 旧 per-file 模式有此问题)
    #    jar-analyzer 节点的 file_path=None, 需从 resolved_meta 取 codegraph 的 file_path
    chain_file_paths: List[Path] = []
    seen_files: set[str] = set()
    for r in raw_rows:
        nid = r["id"]
        if used_jar_analyzer:
            file_path_str = resolved_meta.get(nid, {}).get("file_path")
        else:
            file_path_str = r.get("file_path")
        fp = _resolve_file_path(file_path_str, project_root)
        if fp is not None and str(fp) not in seen_files:
            seen_files.add(str(fp))
            chain_file_paths.append(fp)

    file_calls_cache, fetch_failures = _batch_fetch_file_calls(
        chain_file_paths, source_root, group_id, jar_path, log=True,
    )

    def _get_file_calls(file_path: Path) -> List[Dict[str, Any]]:
        return _get_file_calls_with_cache(
            file_path, file_calls_cache, source_root,
            group_id, jar_path, fetch_failures,
        )

    # 5. 构建 ChainNode 列表 (按 depth 升序, 同 depth 维持 CTE 顺序)
    #    去重 node_id（CTE 可能返回同节点不同深度，保留首次出现 = 最长路径）
    #    jar-analyzer 模式用 resolved_meta/resolved_edges_map, codegraph 用 meta_map/edges_map
    chain_nodes: List[ChainNode] = []
    total_sinks = 0
    all_dynamic_sinks: List[Dict[str, Any]] = []
    seen_node_ids: set[str] = set()
    for r in raw_rows:
        nid = r["id"]
        if nid in seen_node_ids:  # 已处理过此节点，跳过（兄弟节点）
            continue
        seen_node_ids.add(nid)

        if used_jar_analyzer:
            rm = resolved_meta.get(nid, {})
            fqn = rm.get("fqn", "")
            node_id_for_chain = rm.get("node_id", nid)
            file_path_str = rm.get("file_path")
            start_line = int(rm.get("start_line", 0) or 0)
            end_line = rm.get("end_line")
            node_edges = resolved_edges_map.get(nid, [])
        else:
            meta = meta_map.get(nid, {})
            fqn = meta.get("qualified_name") or r.get("qualified_name") or ""
            node_id_for_chain = nid
            file_path_str = r.get("file_path")
            start_line = int(r["start_line"] or 0)
            end_line = meta.get("end_line")
            node_edges = edges_map.get(nid, [])

        depth = int(r["depth"])
        file_p = _resolve_file_path(file_path_str, project_root)

        # body slice
        body: Optional[str] = None
        if file_p is not None and start_line > 0:
            body = _read_method_body(file_p, start_line, end_line)

        # 该 method 的 sink FQN 列表 + 所有非 groupId 调用
        sinks: List[str] = []
        all_external_calls: List[str] = []
        if file_p is not None and start_line > 0:
            all_calls = _get_file_calls(file_p)
            # JAR startLine 是 0-based, codegraph.start_line 是 1-based
            jar_start_line = start_line - 1
            method_calls = [
                c for c in all_calls
                if int(c.get("method_start_line") or -1) == jar_start_line
            ]
            all_called_fqns = [c["called_fqn"] for c in method_calls]
            dynamic_sinks = _sr.identify_dynamic_sinks(group_id, all_called_fqns)
            sinks = [s["fqn"] for s in dynamic_sinks]
            all_dynamic_sinks.extend(dynamic_sinks)
            total_sinks += len(sinks)
            # 所有非 groupId 调用都标注（不只是 sink）
            # 过滤掉 this./super. 开头的短名（JAR 无法解析类型时输出，不是 FQN）
            all_external_calls = [
                c["called_fqn"] for c in method_calls
                if not c["called_fqn"].startswith(group_id + ".")
                and not c["called_fqn"].startswith(group_id + "#")
                and not c["called_fqn"].startswith("this.")
                and not c["called_fqn"].startswith("super.")
                and "." in c["called_fqn"]
            ]

        # body 注入外部调用注释 (#fqn 格式, 不区分 sink 类型)
        annotated_body = _annotate_body_with_sinks(body or "", all_external_calls)

        chain_nodes.append(ChainNode(
            fqn=fqn,
            node_id=node_id_for_chain,
            file=file_path_str,
            start_line=start_line,
            end_line=end_line,
            body=annotated_body or None,
            depth=depth,
            sinks=sinks,
            edges=node_edges,
        ))

    # 6. 统计 + cycle detection
    total_nodes = len(chain_nodes)
    total_edges = sum(len(n.edges) for n in chain_nodes)

    # cycle: 任何 node 被本链上游节点再次引用即视为环 (CTE 已用 path 防环,
    # 因此此处"环"含义=实际代码里存在的递归调用, 但 chain 中只展示一次)
    # jar-analyzer 模式用 resolved_edges_map (key=jar method_id), codegraph 用 edges_map
    effective_edges_map = resolved_edges_map if used_jar_analyzer else edges_map
    cycle_detected = _detect_cycle_in_chain(chain_nodes, effective_edges_map)

    sig_hash = _sig_hash_for_entry(entry_id)

    result: Dict[str, Any] = {
        "entry_fqn": entry_fqn,
        "entry_id": entry_id,
        "sig_hash": sig_hash,
        "group_id": group_id,
        "depth_limit": max_depth,
        "chain": [_chain_node_to_dict(n) for n in chain_nodes],
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "total_sinks": total_sinks,
        "sink_categories": {s["fqn"]: s["category"] for s in all_dynamic_sinks},
        "preset_sink_count": _sr.match_preset_sinks([_chain_node_to_dict(n) for n in chain_nodes]),
        "cycle_detected": cycle_detected,
        "entry_has_params": entry_has_params,
        "file_calls_cache_size": len(file_calls_cache),
        "file_calls_failures": fetch_failures,
        "cte_source": "jar-analyzer" if used_jar_analyzer else "codegraph",
    }

    # 6a. Write chain to SQLite if loop_audit_dir is provided
    if loop_audit_dir is not None:
        try:
            from chain_db import ChainDB
            db = ChainDB(loop_audit_dir / "chains.db")
            # 构建 chain_path: fqn(sink num: N) -> fqn(sink num: N) -> ...
            chain_path_parts = []
            node_path_parts = []
            for n in chain_nodes:
                sink_num = len(n.sinks)
                chain_path_parts.append(f"{n.fqn}(sink num: {sink_num})")
                node_path_parts.append(n.node_id)

            # 只计算最后一个节点的 sink + preset 匹配
            last_sinks = len(chain_nodes[-1].sinks) if chain_nodes else 0
            last_preset = _sr.match_preset_sinks([_chain_node_to_dict(chain_nodes[-1])]) if chain_nodes else 0
            # 入口无用户参数时降权
            entry_has_params = result.get("entry_has_params", True)
            penalty = 0 if entry_has_params else 100
            priority = max(0, last_preset * 10 + last_sinks - penalty)

            chain_path_str = " -> ".join(chain_path_parts)
            node_path_str = " -> ".join(node_path_parts)
            db.insert_chain(
                chain_id=sig_hash,
                endpoint_fqn=entry_fqn,
                priority=priority,
                total_sinks=total_sinks,
                preset_sinks=result.get("preset_sink_count", 0),
                cycle_detected=cycle_detected,
                chain_path=chain_path_str,
                node_path=node_path_str,
                last_sinks=last_sinks,
                is_sink=last_sinks > 0,
                node_count=len(chain_nodes),
            )
            result["chain_db"] = str(loop_audit_dir / "chains.db")
            result["last_sinks_priority"] = priority
        except Exception as e:  # noqa: BLE001
            _log("chain_db write failed: %s", e)
            result["chain_db_error"] = str(e)

    # 6b. redis-batch-prefetch: 批量预取方法体到 Memurai (可选, 在写链缓存之前)
    if memurai_client is not None:
        chain_for_prefetch = [
            {
                "fqn": n.fqn,
                "startLine": n.start_line,
                "line": n.start_line,
                "body": n.body,
                "file": n.file,
                "depth": n.depth,
                "node_id": n.node_id,
            }
            for n in chain_nodes
        ]
        try:
            prefetch_result = _rbp.prefetch_chain(
                memurai_client, chain_for_prefetch, group_id,
                sig_hash, method_ttl=ttl, prefetch_ttl=3600,
            )
            result["prefetch_stats"] = prefetch_result
        except Exception as e:  # noqa: BLE001
            _log("redis-batch-prefetch failed: %s", e)
            result["prefetch_error"] = str(e)

    # 7. Memurai 缓存 (可选)
    if memurai_client is not None:
        cache_key = f"{group_id}:audit:chain:{sig_hash}"
        try:
            ok = memurai_client.set_json(
                cache_key, result, ex=ttl,
            )
            result["cache_key"] = cache_key
            result["cache_written"] = bool(ok)
        except Exception as e:  # noqa: BLE001
            _log("Memurai 写缓存失败 (%s): %s", cache_key, e)
            result["cache_key"] = cache_key
            result["cache_written"] = False
            result["cache_error"] = str(e)

    return result


def build_all_chains_for_endpoint(
    entry_fqn: str,
    group_id: str,
    project_root: Path,
    db_path: Path = _DEFAULT_DB,
    max_depth: int = 20,
    memurai_client: Any = None,
    ttl: int = 86400,
    jar_path: Optional[Path] = None,
    source_root: Optional[Path] = None,
    loop_audit_dir: Optional[Path] = None,
    jar_analyzer_db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """同一 entry 的**所有**根→叶路径变体。

    实现: 用 CTE RECURSIVE 拉出 ``(path, leaf_node)`` 的全集, 然后按 path 聚类,
    每个独立路径 = 一条 chain 变体。每条变体再走一遍 build_chain 的节点注释逻辑
    (复用 file_calls_cache, 整 endpoint 只解析一次 JAR)。

    Parameters
    ----------
    (同 build_chain)

    Returns
    -------
    list[dict]: 每个 dict 是单条路径变体的完整结果 (结构同 ``build_chain()``)。
    """
    project_root = Path(project_root)
    db_path = Path(db_path)
    if source_root is None:
        # 按优先级探测源码根目录:
        # 1. src/main/java (Maven/Gradle 标准项目)
        # 2. sources (JADX 反编译输出)
        # 3. project_root (兜底)
        for candidate in [
            project_root / "src" / "main" / "java",
            project_root / "sources",
        ]:
            if candidate.is_dir():
                source_root = candidate
                break
        else:
            source_root = project_root

    # entry resolution: try jar-analyzer first, fall back to codegraph
    used_jar_analyzer_all = False
    entry_id: str = ""

    if jar_analyzer_db_path is not None:
        if _jac is None:
            raise ImportError(
                "jar_analyzer_cte.py 未加载, 无法使用 --jar-analyzer-db; "
                "请确保 scripts/chain/jar_analyzer_cte.py 存在"
            )
        ja_entry_id = _jac.resolve_entry_jar_analyzer(  # type: ignore[union-attr]
            str(jar_analyzer_db_path), entry_fqn,
        )
        if ja_entry_id:
            entry_id = ja_entry_id
            used_jar_analyzer_all = True
            _log("jar-analyzer entry resolved (all_paths): %s → %s",
                 entry_fqn, ja_entry_id)

    if not used_jar_analyzer_all:
        cg_entry_id = _sec.resolve_entry(str(db_path), entry_fqn)
        if not cg_entry_id:
            raise LookupError(
                f"entry_fqn 在 codegraph 中找不到 method 节点: {entry_fqn!r}"
            )
        entry_id = cg_entry_id

    assert entry_id, "entry_id 未被设置 (逻辑错误)"

    # path extraction: jar-analyzer uses its own CTE; codegraph uses existing logic
    # jar-analyzer CTE also provides a 'path' column, parse unique paths from it
    ja_all_rows: List[Dict[str, Any]] = []
    if used_jar_analyzer_all:
        ja_all_rows = _jac.extract_recursive_with_impl(  # type: ignore[union-attr]
            str(jar_analyzer_db_path), entry_id, max_depth,
        )
        if not ja_all_rows:
            _log("entry %s → jar-analyzer CTE 返回空链", entry_id)
            return []
        # Extract unique paths from jar-analyzer CTE output
        seen_paths: set[str] = set()
        paths: List[Dict[str, Any]] = []
        for r in ja_all_rows:
            path_str = r.get("path", "")
            if path_str in seen_paths:
                continue
            seen_paths.add(path_str)
            node_ids = [n for n in path_str.split("|") if n]
            paths.append({
                "nodes": node_ids,
                "cycle_in_cte": False,
            })
    else:
        paths = _extract_paths_with_cycle_flag(str(db_path), entry_id, max_depth)
        if not paths:
            _log("entry %s → 无可达路径", entry_id)
            return []

    # 检查入口方法是否有参数
    entry_has_params = True
    if used_jar_analyzer_all:
        # jar-analyzer 没有 signature, 按 qualified_name 查 codegraph
        with _open_db(db_path) as conn:
            row = conn.execute(
                "SELECT id, signature FROM nodes WHERE qualified_name = ? LIMIT 1",
                (entry_fqn,),
            ).fetchone()
        if row:
            sig = row["signature"] or ""
            entry_has_params = (
                "()" not in sig
                or len(sig) > sig.find(")") + 1 > sig.find("(") + 1
            )
    else:
        with _open_db(db_path) as conn:
            row = conn.execute(
                "SELECT signature FROM nodes WHERE id = ?", (entry_id,)
            ).fetchone()
        if row:
            sig = row["signature"] or ""
            entry_has_params = "()" not in sig or len(sig) > sig.find(")") + 1 > sig.find("(") + 1

    # 收集所有出现过的 node id (去重) → 一次性反查 meta + edges + file_path
    all_ids: set[str] = set()
    for p in paths:
        for nid in p["nodes"]:
            all_ids.add(nid)
    all_ids_list = list(all_ids)

    # jar-analyzer 模式: 交叉引用 codegraph; codegraph 模式: 直接查
    all_jar_cg_meta: Dict[str, Optional[Dict[str, Any]]] = {}
    all_resolved_meta: Dict[str, Dict[str, Any]] = {}
    all_resolved_edges_map: Dict[str, List[str]] = {}
    meta_map: Dict[str, Dict[str, Any]] = {}
    edges_map: Dict[str, List[str]] = {}
    node_file_paths: Dict[str, Optional[str]] = {}

    if used_jar_analyzer_all:
        # Build jar-analyzer rows lookup by method_id for cross-ref
        ja_rows_by_id = {r["id"]: r for r in ja_all_rows}
        with _open_db(db_path) as conn:
            all_jar_cg_meta = _resolve_codegraph_meta_for_jar_nodes(
                conn, ja_all_rows,
            )
            cg_node_ids = [
                m["codegraph_node_id"]
                for m in all_jar_cg_meta.values()
                if m and m.get("codegraph_node_id")
            ]
            cg_edges = _fetch_outgoing_edges(conn, cg_node_ids) if cg_node_ids else {}

        for mid in all_ids_list:
            r = ja_rows_by_id.get(mid)
            cg_info = all_jar_cg_meta.get(mid)
            if cg_info:
                all_resolved_meta[mid] = {
                    "node_id": cg_info["codegraph_node_id"],
                    "fqn": cg_info.get("qualified_name")
                           or (r.get("qualified_name", "").replace("::", "#") if r else ""),
                    "file_path": cg_info.get("file_path"),
                    "start_line": cg_info.get("start_line") or (r.get("start_line", 0) if r else 0),
                    "end_line": cg_info.get("end_line"),
                }
                all_resolved_edges_map[mid] = cg_edges.get(
                    cg_info["codegraph_node_id"], [],
                )
                node_file_paths[mid] = cg_info.get("file_path")
            else:
                all_resolved_meta[mid] = {
                    "node_id": mid,
                    "fqn": (r.get("qualified_name", "").replace("::", "#") if r else ""),
                    "file_path": None,
                    "start_line": (r.get("start_line", 0) or 0) if r else 0,
                    "end_line": None,
                }
                all_resolved_edges_map[mid] = []
                node_file_paths[mid] = None
    else:
        with _open_db(db_path) as conn:
            meta_map = _fetch_node_meta(conn, all_ids_list)
            edges_map = _fetch_outgoing_edges(conn, all_ids_list)
            if all_ids_list:
                placeholders = ",".join("?" * len(all_ids_list))
                cur = conn.execute(
                    f"SELECT id, file_path FROM nodes WHERE id IN ({placeholders})",
                    tuple(all_ids_list),
                )
                node_file_paths = {row["id"]: row["file_path"] for row in cur.fetchall()}

    # 收集所有唯一文件路径, 一次性调 JAR --config (整个 endpoint 只解析一次)
    chain_file_paths: List[Path] = []
    seen_files: set[str] = set()
    for nid in all_ids_list:
        fp = _resolve_file_path(node_file_paths.get(nid), project_root)
        if fp is not None and str(fp) not in seen_files:
            seen_files.add(str(fp))
            chain_file_paths.append(fp)

    file_calls_cache, _fetch_failures = _batch_fetch_file_calls(
        chain_file_paths, source_root, group_id, jar_path, log=True,
    )

    def _get_file_calls(file_path: Path) -> List[Dict[str, Any]]:
        return _get_file_calls_with_cache(
            file_path, file_calls_cache, source_root,
            group_id, jar_path, _fetch_failures,
        )

    sig_hash = _sig_hash_for_entry(entry_id)
    cache_key = f"{group_id}:audit:chain:{sig_hash}"
    results: List[Dict[str, Any]] = []

    for path_idx, p in enumerate(paths):
        node_ids = p["nodes"]
        chain_nodes: List[ChainNode] = []
        total_sinks = 0
        all_dynamic_sinks: List[Dict[str, Any]] = []
        cycle_in_path = False
        seen: set[str] = set()

        for depth, nid in enumerate(node_ids):
            if nid in seen:
                cycle_in_path = True
            seen.add(nid)

            if used_jar_analyzer_all:
                rm = all_resolved_meta.get(nid, {})
                fqn = rm.get("fqn", "")
                node_id_for_chain = rm.get("node_id", nid)
                r_start = rm.get("start_line") if isinstance(rm.get("start_line"), int) else None
                r_end = rm.get("end_line")
                file_path_str = rm.get("file_path")
                node_edges = all_resolved_edges_map.get(nid, [])
            else:
                meta = meta_map.get(nid, {})
                fqn = meta.get("qualified_name") or ""
                node_id_for_chain = nid
                r_start = meta.get("start_line") if isinstance(meta.get("start_line"), int) else None
                r_end = meta.get("end_line")

                # 查 start_line (CTE path 没有带, 需另查)
                if r_start is None:
                    with _open_db(db_path) as conn:
                        row = conn.execute(
                            "SELECT start_line, file_path FROM nodes WHERE id=?",
                            (nid,),
                        ).fetchone()
                    r_start = int(row["start_line"]) if row else 0
                    file_path_str = row["file_path"] if row else None
                else:
                    file_path_str = node_file_paths.get(nid)
                node_edges = edges_map.get(nid, [])

            file_p = _resolve_file_path(file_path_str, project_root)
            body: Optional[str] = None
            if file_p is not None and r_start and r_start > 0:
                body = _read_method_body(file_p, r_start, r_end)

            sinks: List[str] = []
            ext_calls: List[str] = []
            if file_p is not None and r_start and r_start > 0:
                all_calls = _get_file_calls(file_p)
                jar_start = r_start - 1
                method_calls = [
                    c for c in all_calls
                    if int(c.get("method_start_line") or -1) == jar_start
                ]
                all_called_fqns = [c["called_fqn"] for c in method_calls]
                dynamic_sinks = _sr.identify_dynamic_sinks(group_id, all_called_fqns)
                sinks = [s["fqn"] for s in dynamic_sinks]
                all_dynamic_sinks.extend(dynamic_sinks)
                total_sinks += len(sinks)
                ext_calls = [
                    c["called_fqn"] for c in method_calls
                    if not c["called_fqn"].startswith(group_id + ".")
                    and not c["called_fqn"].startswith(group_id + "#")
                    and not c["called_fqn"].startswith("this.")
                    and not c["called_fqn"].startswith("super.")
                    and "." in c["called_fqn"]
                ]

            chain_nodes.append(ChainNode(
                fqn=fqn,
                node_id=node_id_for_chain,
                file=file_path_str,
                start_line=r_start or 0,
                end_line=r_end,
                body=_annotate_body_with_sinks(body or "", ext_calls) or None,
                depth=depth,
                sinks=sinks,
                edges=node_edges,
            ))

        total_edges = sum(len(n.edges) for n in chain_nodes)
        result = {
            "entry_fqn": entry_fqn,
            "entry_id": entry_id,
            "sig_hash": sig_hash,
            "group_id": group_id,
            "depth_limit": max_depth,
            "path_index": path_idx,
            "path_node_ids": node_ids,
            "cycle_detected": cycle_in_path or p.get("cycle_in_cte", False),
            "chain": [_chain_node_to_dict(n) for n in chain_nodes],
            "total_nodes": len(chain_nodes),
            "total_edges": total_edges,
            "total_sinks": total_sinks,
            "sink_categories": {s["fqn"]: s["category"] for s in all_dynamic_sinks},
            "preset_sink_count": _sr.match_preset_sinks([_chain_node_to_dict(n) for n in chain_nodes]),
            "file_calls_cache_size": len(file_calls_cache),
            "cte_source": "jar-analyzer" if used_jar_analyzer_all else "codegraph",
        }
        results.append(result)

    # Write chains to SQLite if loop_audit_dir is provided
    if loop_audit_dir is not None and results:
        try:
            from chain_db import ChainDB
            db = ChainDB(loop_audit_dir / "chains.db")
            chains_to_insert = []
            for r in results:
                chain_path_parts = []
                node_path_parts = []
                for n in r["chain"]:
                    sink_num = len(n.get("sinks", []))
                    chain_path_parts.append(f"{n['fqn']}(sink num: {sink_num})")
                    node_path_parts.append(n['node_id'])
                # 只计算最后一个节点的 sink + preset 匹配
                last_node = r["chain"][-1] if r["chain"] else None
                last_sinks = len(last_node.get("sinks", [])) if last_node else 0
                last_preset = _sr.match_preset_sinks([last_node]) if last_node else 0
                penalty = 0 if entry_has_params else 100
                priority = max(0, last_preset * 10 + last_sinks - penalty)
                chains_to_insert.append({
                    "chain_id": f"{sig_hash}_{r.get('path_index', 0)}",
                    "endpoint_fqn": entry_fqn,
                    "priority": priority,
                    "total_sinks": r.get("total_sinks", 0),
                    "preset_sinks": r.get("preset_sink_count", 0),
                    "cycle_detected": r.get("cycle_detected", False),
                    "chain_path": " -> ".join(chain_path_parts),
                    "node_path": " -> ".join(node_path_parts),
                    "last_sinks": last_sinks,
                    "is_sink": last_sinks > 0,
                    "node_count": len(r["chain"]),
                })
            db.insert_chains_batch(chains_to_insert)
            for r in results:
                r["chain_db"] = str(loop_audit_dir / "chains.db")
        except Exception as e:  # noqa: BLE001
            _log("chain_db write failed: %s", e)
            for r in results:
                r["chain_db_error"] = str(e)

    # Prefetch: 批量预取所有变体的方法体到 Memurai (可选, 在写链缓存之前)
    if memurai_client is not None and results:
        all_chain_nodes = []
        for r in results:
            for node_dict in r["chain"]:
                all_chain_nodes.append(node_dict)

        chain_for_prefetch = [
            {
                "fqn": n.get("fqn", ""),
                "startLine": n.get("start_line", 0),
                "line": n.get("start_line", 0),
                "body": n.get("body"),
                "file": n.get("file"),
                "depth": n.get("depth", 0),
                "node_id": n.get("node_id", ""),
            }
            for n in all_chain_nodes
        ]
        try:
            prefetch_result = _rbp.prefetch_chain(
                memurai_client, chain_for_prefetch, group_id,
                sig_hash, method_ttl=ttl, prefetch_ttl=3600,
            )
            for r in results:
                r["prefetch_stats"] = prefetch_result
        except Exception as e:  # noqa: BLE001
            _log("redis-batch-prefetch failed: %s", e)
            for r in results:
                r["prefetch_error"] = str(e)

    # 写一次 Memurai (覆盖同 sigHash), 包含所有变体
    if memurai_client is not None and results:
        try:
            ok = memurai_client.set_json(
                cache_key,
                {"paths": results, "path_count": len(results)},
                ex=ttl,
            )
            for r in results:
                r["cache_key"] = cache_key
                r["cache_written"] = bool(ok)
        except Exception as e:  # noqa: BLE001
            _log("Memurai 写缓存失败 (%s): %s", cache_key, e)

    return results


# ============================================================== jar-analyzer only (无 codegraph)

def build_chain_jar_analyzer(
    entry_fqn: str,
    group_id: str,
    project_root: Path,
    jar_analyzer_db_path: Path,
    max_depth: int = 20,
    memurai_client: Any = None,
    ttl: int = 864000,
    jar_path: Optional[Path] = None,
    source_root: Optional[Path] = None,
    loop_audit_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """纯 jar-analyzer 模式: 不使用 codegraph, 所有数据来自 jar-analyzer.db.

    与 build_chain() 输出格式兼容, 但 cte_source='jar-analyzer', node_id=jar method_id,
    缓存 key 用 jar method_id.
    """
    if _jac is None:
        raise ImportError(
            "jar_analyzer_cte.py 未加载, 无法使用 jar-analyzer-only 模式; "
            "请确保 scripts/chain/jar_analyzer_cte.py 存在"
        )

    project_root = Path(project_root)
    jar_analyzer_db_path = Path(jar_analyzer_db_path)
    if source_root is None:
        for candidate in [project_root / "src" / "main" / "java", project_root / "sources"]:
            if candidate.is_dir():
                source_root = candidate
                break
        else:
            source_root = project_root

    # 1. 入口解析: jar-analyzer only
    entry_id = _jac.resolve_entry_jar_analyzer(str(jar_analyzer_db_path), entry_fqn)
    if not entry_id:
        _log("entry %s → jar-analyzer 找不到方法", entry_fqn)
        return _empty_chain_result(entry_fqn, "")

    _log("jar-analyzer-only entry resolved: %s → %s", entry_fqn, entry_id)

    # 2. CTE 递归: jar-analyzer only
    raw_rows = _jac.extract_recursive_with_impl(
        str(jar_analyzer_db_path), entry_id, max_depth,
    )
    if not raw_rows:
        _log("entry %s → jar-analyzer CTE 返回空链", entry_id)
        return _empty_chain_result(entry_fqn, entry_id)

    node_ids = [r["id"] for r in raw_rows]

    # 3. 元信息: jar-analyzer only
    # 3a. file paths from class_file_table
    class_names_in_chain = [r["class_name"] for r in raw_rows if r.get("class_name")]
    class_path_map = _jac.get_file_paths(str(jar_analyzer_db_path), class_names_in_chain)

    # 3b. line numbers from method_table
    line_number_map = _jac.get_method_line_numbers(str(jar_analyzer_db_path), node_ids)

    # 3c. method metadata for fqn construction
    method_meta_map = _jac.get_method_meta(str(jar_analyzer_db_path), node_ids)

    # 3d. edges from method_call_table
    edges_map = _jac.get_method_call_edges(str(jar_analyzer_db_path), node_ids)

    # 3e. entry_has_params: check method_desc for params
    entry_meta = method_meta_map.get(entry_id, {})
    entry_method_desc = entry_meta.get("method_desc", "")
    # method_desc contains parameter info; non-empty desc with types means has params
    entry_has_params = bool(entry_method_desc and entry_method_desc != "()")

    # 4. 收集所有唯一 java 文件路径, 一次性调 JAR
    chain_java_files: List[Path] = []
    seen_java_files: set[str] = set()
    for r in raw_rows:
        cn = r.get("class_name", "")
        if cn:
            java_p = _jac.class_name_to_java_source_path(cn, project_root)
            if java_p.is_file() and str(java_p) not in seen_java_files:
                seen_java_files.add(str(java_p))
                chain_java_files.append(java_p)

    file_calls_cache, fetch_failures = _batch_fetch_file_calls(
        chain_java_files, source_root, group_id, jar_path, log=True,
    )

    def _get_file_calls(file_path: Path) -> List[Dict[str, Any]]:
        return _get_file_calls_with_cache(
            file_path, file_calls_cache, source_root,
            group_id, jar_path, fetch_failures,
        )

    # 5. 构建 ChainNode 列表
    chain_nodes: List[ChainNode] = []
    total_sinks = 0
    all_dynamic_sinks: List[Dict[str, Any]] = []
    seen_node_ids: set[str] = set()

    for r in raw_rows:
        nid = r["id"]
        if nid in seen_node_ids:
            continue
        seen_node_ids.add(nid)

        meta = method_meta_map.get(nid, {})
        cn = r.get("class_name", meta.get("class_name", ""))
        mn = r.get("method_name", meta.get("method_name", ""))

        # fqn: org/owasp/...ClassName::methodName (dot format)
        fqn = f"{_jac._jar_class_to_dot(cn)}::{mn}" if cn and mn else ""

        # node_id = jar method_id (integer, used as string)
        node_id_for_chain = nid

        # file path: convert class_name → java source path
        java_file_path = None
        java_file_p = None
        if cn:
            java_file_p = _jac.class_name_to_java_source_path(cn, project_root)
            if java_file_p.is_file():
                java_file_path = str(java_file_p)

        # start_line: from method_table
        start_line = line_number_map.get(nid, 0) or 0
        end_line = None  # jar-analyzer 没有 end_line

        # edges
        node_edges = edges_map.get(nid, [])

        # body: read from java source file
        body: Optional[str] = None
        if java_file_p is not None and java_file_p.is_file() and start_line > 0:
            body = _read_method_body(java_file_p, start_line, end_line)

        depth = int(r.get("depth", 0))

        # sinks: 从 JAR 解析的调用中识别
        sinks: List[str] = []
        ext_calls: List[str] = []
        if java_file_p is not None and java_file_p.is_file() and start_line > 0:
            all_calls = _get_file_calls(java_file_p)
            jar_start = start_line - 1
            method_calls = [
                c for c in all_calls
                if int(c.get("method_start_line") or -1) == jar_start
            ]
            all_called_fqns = [c["called_fqn"] for c in method_calls]
            dynamic_sinks = _sr.identify_dynamic_sinks(group_id, all_called_fqns)
            sinks = [s["fqn"] for s in dynamic_sinks]
            all_dynamic_sinks.extend(dynamic_sinks)
            total_sinks += len(sinks)
            ext_calls = [
                c["called_fqn"] for c in method_calls
                if not c["called_fqn"].startswith(group_id + ".")
                and not c["called_fqn"].startswith(group_id + "#")
                and not c["called_fqn"].startswith("this.")
                and not c["called_fqn"].startswith("super.")
                and "." in c["called_fqn"]
            ]

        annotated_body = _annotate_body_with_sinks(body or "", ext_calls) if body else None

        chain_nodes.append(ChainNode(
            fqn=fqn,
            node_id=node_id_for_chain,
            file=java_file_path,
            start_line=start_line,
            end_line=end_line,
            body=annotated_body or None,
            depth=depth,
            sinks=sinks,
            edges=node_edges,
        ))

    # 6. cycle detection
    total_nodes = len(chain_nodes)
    total_edges = sum(len(n.edges) for n in chain_nodes)
    cycle_detected = _detect_cycle_in_chain(chain_nodes, edges_map)

    sig_hash = _sig_hash_for_entry(entry_id)

    result: Dict[str, Any] = {
        "entry_fqn": entry_fqn,
        "entry_id": entry_id,
        "sig_hash": sig_hash,
        "group_id": group_id,
        "depth_limit": max_depth,
        "chain": [_chain_node_to_dict(n) for n in chain_nodes],
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "total_sinks": total_sinks,
        "sink_categories": {s["fqn"]: s["category"] for s in all_dynamic_sinks},
        "preset_sink_count": _sr.match_preset_sinks([_chain_node_to_dict(n) for n in chain_nodes]),
        "cycle_detected": cycle_detected,
        "entry_has_params": entry_has_params,
        "file_calls_cache_size": len(file_calls_cache),
        "file_calls_failures": fetch_failures,
        "cte_source": "jar-analyzer",
    }

    # 6a. Write chain to SQLite if loop_audit_dir provided
    if loop_audit_dir is not None:
        try:
            from chain_db import ChainDB
            db = ChainDB(loop_audit_dir / "chains.db")
            chain_path_parts = []
            node_path_parts = []
            for n in chain_nodes:
                sink_num = len(n.sinks)
                chain_path_parts.append(f"{n.fqn}(sink num: {sink_num})")
                node_path_parts.append(n.node_id)

            last_sinks = len(chain_nodes[-1].sinks) if chain_nodes else 0
            last_preset = _sr.match_preset_sinks([_chain_node_to_dict(chain_nodes[-1])]) if chain_nodes else 0
            penalty = 0 if entry_has_params else 100
            priority = max(0, last_preset * 10 + last_sinks - penalty)

            chain_path_str = " -> ".join(chain_path_parts)
            node_path_str = " -> ".join(node_path_parts)
            db.insert_chain(
                chain_id=sig_hash,
                endpoint_fqn=entry_fqn,
                priority=priority,
                total_sinks=total_sinks,
                preset_sinks=result.get("preset_sink_count", 0),
                cycle_detected=cycle_detected,
                chain_path=chain_path_str,
                node_path=node_path_str,
                last_sinks=last_sinks,
                is_sink=last_sinks > 0,
                node_count=len(chain_nodes),
            )
            result["chain_db"] = str(loop_audit_dir / "chains.db")
            result["last_sinks_priority"] = priority
        except Exception as e:  # noqa: BLE001
            _log("chain_db write failed: %s", e)
            result["chain_db_error"] = str(e)

    # 6b. redis-batch-prefetch: cache key uses jar method_id
    if memurai_client is not None:
        chain_for_prefetch = [
            {
                "fqn": n.fqn,
                "startLine": n.start_line,
                "line": n.start_line,
                "body": n.body,
                "file": n.file,
                "depth": n.depth,
                "node_id": n.node_id,
            }
            for n in chain_nodes
        ]
        try:
            prefetch_result = _rbp.prefetch_chain(
                memurai_client, chain_for_prefetch, group_id,
                sig_hash, method_ttl=ttl, prefetch_ttl=3600,
            )
            result["prefetch_stats"] = prefetch_result
        except Exception as e:  # noqa: BLE001
            _log("redis-batch-prefetch failed: %s", e)
            result["prefetch_error"] = str(e)

    # 7. Memurai 缓存 (可选) — 缓存 key 用 jar method_id
    if memurai_client is not None:
        cache_key = f"{group_id}:audit:chain:{sig_hash}"
        try:
            ok = memurai_client.set_json(
                cache_key, result, ex=ttl,
            )
            result["cache_key"] = cache_key
            result["cache_written"] = bool(ok)
        except Exception as e:  # noqa: BLE001
            _log("Memurai 写缓存失败 (%s): %s", cache_key, e)
            result["cache_key"] = cache_key
            result["cache_written"] = False
            result["cache_error"] = str(e)

    return result


def build_all_chains_for_endpoint_jar_analyzer(
    entry_fqn: str,
    group_id: str,
    project_root: Path,
    jar_analyzer_db_path: Path,
    max_depth: int = 20,
    memurai_client: Any = None,
    ttl: int = 864000,
    jar_path: Optional[Path] = None,
    source_root: Optional[Path] = None,
    loop_audit_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """纯 jar-analyzer 模式的多路径变体版本.

    与 build_all_chains_for_endpoint 输出格式兼容, 但 cte_source='jar-analyzer'.
    """
    if _jac is None:
        raise ImportError(
            "jar_analyzer_cte.py 未加载; "
            "请确保 scripts/chain/jar_analyzer_cte.py 存在"
        )

    project_root = Path(project_root)
    jar_analyzer_db_path = Path(jar_analyzer_db_path)
    if source_root is None:
        for candidate in [project_root / "src" / "main" / "java", project_root / "sources"]:
            if candidate.is_dir():
                source_root = candidate
                break
        else:
            source_root = project_root

    # 入口解析
    entry_id = _jac.resolve_entry_jar_analyzer(str(jar_analyzer_db_path), entry_fqn)
    if not entry_id:
        _log("entry %s → jar-analyzer 找不到方法", entry_fqn)
        return []

    # CTE: all rows
    ja_all_rows = _jac.extract_recursive_with_impl(
        str(jar_analyzer_db_path), entry_id, max_depth,
    )
    if not ja_all_rows:
        _log("entry %s → jar-analyzer CTE 返回空链", entry_id)
        return []

    # Extract unique paths from CTE output
    seen_paths: set[str] = set()
    paths: List[Dict[str, Any]] = []
    for r in ja_all_rows:
        path_str = r.get("path", "")
        if path_str in seen_paths:
            continue
        seen_paths.add(path_str)
        node_ids = [n for n in path_str.split("|") if n]
        paths.append({
            "nodes": node_ids,
            "cycle_in_cte": False,
        })

    # 元信息: batch load
    all_ids = set()
    for p in paths:
        for nid in p["nodes"]:
            all_ids.add(nid)
    all_ids_list = list(all_ids)

    class_names_in_chain = []
    for r in ja_all_rows:
        cn = r.get("class_name", "")
        if cn and cn not in class_names_in_chain:
            class_names_in_chain.append(cn)
    class_path_map = _jac.get_file_paths(str(jar_analyzer_db_path), class_names_in_chain)
    line_number_map = _jac.get_method_line_numbers(str(jar_analyzer_db_path), all_ids_list)
    method_meta_map = _jac.get_method_meta(str(jar_analyzer_db_path), all_ids_list)
    edges_map_all = _jac.get_method_call_edges(str(jar_analyzer_db_path), all_ids_list)

    # entry_has_params
    entry_meta = method_meta_map.get(entry_id, {})
    entry_method_desc = entry_meta.get("method_desc", "")
    entry_has_params = bool(entry_method_desc and entry_method_desc != "()")

    # 收集所有唯一 java 文件路径
    chain_java_files: List[Path] = []
    seen_java_files: set[str] = set()
    for nid in all_ids_list:
        meta = method_meta_map.get(nid, {})
        cn = meta.get("class_name", "")
        if cn:
            java_p = _jac.class_name_to_java_source_path(cn, project_root)
            if java_p.is_file() and str(java_p) not in seen_java_files:
                seen_java_files.add(str(java_p))
                chain_java_files.append(java_p)

    file_calls_cache, fetch_failures = _batch_fetch_file_calls(
        chain_java_files, source_root, group_id, jar_path, log=True,
    )

    def _get_file_calls(file_path: Path) -> List[Dict[str, Any]]:
        return _get_file_calls_with_cache(
            file_path, file_calls_cache, source_root,
            group_id, jar_path, fetch_failures,
        )

    sig_hash = _sig_hash_for_entry(entry_id)
    cache_key = f"{group_id}:audit:chain:{sig_hash}"
    results: List[Dict[str, Any]] = []

    for path_idx, p in enumerate(paths):
        node_ids = p["nodes"]
        chain_nodes: List[ChainNode] = []
        total_sinks = 0
        all_dynamic_sinks: List[Dict[str, Any]] = []
        cycle_in_path = False
        seen: set[str] = set()

        for depth, nid in enumerate(node_ids):
            if nid in seen:
                cycle_in_path = True
            seen.add(nid)

            meta = method_meta_map.get(nid, {})
            cn = meta.get("class_name", "")
            mn = meta.get("method_name", "")
            fqn = f"{_jac._jar_class_to_dot(cn)}::{mn}" if cn and mn else ""

            node_id_for_chain = nid

            # file path
            java_file_p = None
            java_file_path = None
            if cn:
                java_file_p = _jac.class_name_to_java_source_path(cn, project_root)
                if java_file_p.is_file():
                    java_file_path = str(java_file_p)

            start_line = line_number_map.get(nid, 0) or 0
            end_line = None

            node_edges = edges_map_all.get(nid, [])

            # body
            body: Optional[str] = None
            if java_file_p is not None and java_file_p.is_file() and start_line > 0:
                body = _read_method_body(java_file_p, start_line, end_line)

            # sinks
            sinks: List[str] = []
            ext_calls: List[str] = []
            if java_file_p is not None and java_file_p.is_file() and start_line > 0:
                all_calls = _get_file_calls(java_file_p)
                jar_start = start_line - 1
                method_calls = [
                    c for c in all_calls
                    if int(c.get("method_start_line") or -1) == jar_start
                ]
                all_called_fqns = [c["called_fqn"] for c in method_calls]
                dynamic_sinks = _sr.identify_dynamic_sinks(group_id, all_called_fqns)
                sinks = [s["fqn"] for s in dynamic_sinks]
                all_dynamic_sinks.extend(dynamic_sinks)
                total_sinks += len(sinks)
                ext_calls = [
                    c["called_fqn"] for c in method_calls
                    if not c["called_fqn"].startswith(group_id + ".")
                    and not c["called_fqn"].startswith(group_id + "#")
                    and not c["called_fqn"].startswith("this.")
                    and not c["called_fqn"].startswith("super.")
                    and "." in c["called_fqn"]
                ]

            annotated_body = _annotate_body_with_sinks(body or "", ext_calls) if body else None

            chain_nodes.append(ChainNode(
                fqn=fqn,
                node_id=node_id_for_chain,
                file=java_file_path,
                start_line=start_line,
                end_line=end_line,
                body=annotated_body or None,
                depth=depth,
                sinks=sinks,
                edges=node_edges,
            ))

        total_edges = sum(len(n.edges) for n in chain_nodes)
        result = {
            "entry_fqn": entry_fqn,
            "entry_id": entry_id,
            "sig_hash": sig_hash,
            "group_id": group_id,
            "depth_limit": max_depth,
            "path_index": path_idx,
            "path_node_ids": node_ids,
            "cycle_detected": cycle_in_path or p.get("cycle_in_cte", False),
            "chain": [_chain_node_to_dict(n) for n in chain_nodes],
            "total_nodes": len(chain_nodes),
            "total_edges": total_edges,
            "total_sinks": total_sinks,
            "sink_categories": {s["fqn"]: s["category"] for s in all_dynamic_sinks},
            "preset_sink_count": _sr.match_preset_sinks([_chain_node_to_dict(n) for n in chain_nodes]),
            "file_calls_cache_size": len(file_calls_cache),
            "cte_source": "jar-analyzer",
        }
        results.append(result)

    # Write chains to SQLite
    if loop_audit_dir is not None and results:
        try:
            from chain_db import ChainDB
            db = ChainDB(loop_audit_dir / "chains.db")
            chains_to_insert = []
            for r in results:
                chain_path_parts = []
                node_path_parts = []
                for n in r["chain"]:
                    sink_num = len(n.get("sinks", []))
                    chain_path_parts.append(f"{n['fqn']}(sink num: {sink_num})")
                    node_path_parts.append(n['node_id'])
                last_node = r["chain"][-1] if r["chain"] else None
                last_sinks = len(last_node.get("sinks", [])) if last_node else 0
                last_preset = _sr.match_preset_sinks([last_node]) if last_node else 0
                penalty = 0 if entry_has_params else 100
                priority = max(0, last_preset * 10 + last_sinks - penalty)
                chains_to_insert.append({
                    "chain_id": f"{sig_hash}_{r.get('path_index', 0)}",
                    "endpoint_fqn": entry_fqn,
                    "priority": priority,
                    "total_sinks": r.get("total_sinks", 0),
                    "preset_sinks": r.get("preset_sink_count", 0),
                    "cycle_detected": r.get("cycle_detected", False),
                    "chain_path": " -> ".join(chain_path_parts),
                    "node_path": " -> ".join(node_path_parts),
                    "last_sinks": last_sinks,
                    "is_sink": last_sinks > 0,
                    "node_count": len(r["chain"]),
                })
            db.insert_chains_batch(chains_to_insert)
            for r in results:
                r["chain_db"] = str(loop_audit_dir / "chains.db")
        except Exception as e:  # noqa: BLE001
            _log("chain_db write failed: %s", e)
            for r in results:
                r["chain_db_error"] = str(e)

    # Prefetch: batch prefetch all chain nodes to Memurai
    if memurai_client is not None and results:
        all_chain_nodes = []
        for r in results:
            for node_dict in r["chain"]:
                all_chain_nodes.append(node_dict)
        chain_for_prefetch = [
            {
                "fqn": n.get("fqn", ""),
                "startLine": n.get("start_line", 0),
                "line": n.get("start_line", 0),
                "body": n.get("body"),
                "file": n.get("file"),
                "depth": n.get("depth", 0),
                "node_id": n.get("node_id", ""),
            }
            for n in all_chain_nodes
        ]
        try:
            prefetch_result = _rbp.prefetch_chain(
                memurai_client, chain_for_prefetch, group_id,
                sig_hash, method_ttl=ttl, prefetch_ttl=3600,
            )
            for r in results:
                r["prefetch_stats"] = prefetch_result
        except Exception as e:  # noqa: BLE001
            _log("redis-batch-prefetch failed: %s", e)
            for r in results:
                r["prefetch_error"] = str(e)

    # Write Memurai cache (覆盖同 sigHash), 包含所有变体
    if memurai_client is not None and results:
        try:
            ok = memurai_client.set_json(
                cache_key,
                {"paths": results, "path_count": len(results)},
                ex=ttl,
            )
            for r in results:
                r["cache_key"] = cache_key
                r["cache_written"] = bool(ok)
        except Exception as e:  # noqa: BLE001
            _log("Memurai 写缓存失败 (%s): %s", cache_key, e)

    return results

# 与 sqlite-extract-chain.py 的 RECURSIVE_SQL 同结构, 但额外返回 path 列
# 纯粹的 INNER JOIN 递归展开: 每跳 JOIN edges ON kind='calls', 不加额外计算
_RECURSIVE_WITH_PATH_SQL = """
WITH RECURSIVE chain(id, qualified_name, depth, path, file_path, start_line) AS (
    SELECT n.id, n.qualified_name, 0, '|' || n.id || '|',
           n.file_path, n.start_line
    FROM nodes n
    WHERE n.id = :entry_id AND n.kind = 'method'

    UNION ALL

    SELECT callee.id, callee.qualified_name, c.depth + 1,
           c.path || callee.id || '|',
           callee.file_path, callee.start_line
    FROM chain c
    JOIN edges e ON e.source = c.id AND e.kind = 'calls'
    JOIN nodes callee ON callee.id = e.target AND callee.kind = 'method'
    WHERE c.depth < :max_depth
      AND instr(c.path, '|' || callee.id || '|') = 0
)
SELECT id, qualified_name, depth, path, file_path, start_line FROM chain
"""


def _extract_paths_with_cycle_flag(
    db_path: str,
    entry_id: str,
    max_depth: int,
) -> List[Dict[str, Any]]:
    """CTE 输出 (id, path, ...) → 按 path 字符串去重 → 每条 path = 一个变体。

    Returns
    -------
    list of {"nodes": [id, id, ...], "cycle_in_cte": bool}
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            _RECURSIVE_WITH_PATH_SQL,
            {"entry_id": entry_id, "max_depth": max_depth},
        )
        seen_paths: set[str] = set()
        out: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            path = row["path"]
            if path in seen_paths:
                continue
            seen_paths.add(path)
            node_ids = [n for n in path.split("|") if n]
            out.append({
                "nodes": node_ids,
                "cycle_in_cte": False,    # CTE 已防环
            })
        return out
    finally:
        conn.close()


# ============================================================== cycle + 内部小工具

def _detect_cycle_in_chain(
    chain_nodes: Sequence[ChainNode],
    edges_map: Dict[str, List[str]],
) -> bool:
    """检测 chain 中是否存在 back-edge (下游节点引用上游节点)。

    注意: CTE 已经用 path 列防环, 所以本函数捕获的是**原图里**确实存在的递归调用
    (例如 m:foo → m:bar → m:foo), 即使在 CTE 输出里只出现一次也依然记录。
    """
    id_to_depth = {n.node_id: n.depth for n in chain_nodes}
    for src, tgts in edges_map.items():
        if src not in id_to_depth:
            continue
        src_d = id_to_depth[src]
        for tgt in tgts:
            if tgt in id_to_depth and id_to_depth[tgt] <= src_d:
                return True
    return False


def _chain_node_to_dict(n: ChainNode) -> Dict[str, Any]:
    """ChainNode → 可 JSON 序列化的 dict。"""
    return {
        "fqn": n.fqn,
        "node_id": n.node_id,
        "file": n.file,
        "start_line": n.start_line,
        "end_line": n.end_line,
        "body": n.body,
        "depth": n.depth,
        "sinks": n.sinks,
        "edges": n.edges,
    }


def _empty_chain_result(entry_fqn: str, entry_id: str) -> Dict[str, Any]:
    """空链兜底结构 (与 build_chain 返回 schema 一致)。"""
    return {
        "entry_fqn": entry_fqn,
        "entry_id": entry_id,
        "sig_hash": _sig_hash_for_entry(entry_id),
        "group_id": None,
        "depth_limit": 0,
        "chain": [],
        "total_nodes": 0,
        "total_edges": 0,
        "total_sinks": 0,
        "cycle_detected": False,
        "file_calls_cache_size": 0,
        "file_calls_failures": [],
    }


# ============================================================== CLI

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="chain_builder: CTE + sink 提取 + Memurai 缓存一体化",
    )
    p.add_argument("--project-root", required=True,
                   help="项目根目录 (用于解析 codegraph 内的 file_path)")
    p.add_argument("--db", default=str(_DEFAULT_DB),
                   help="codegraph SQLite 路径")
    p.add_argument("--group-id", required=True,
                   help="项目 groupId, 例如 org.owasp.webgoat")
    p.add_argument("--entry", required=True,
                   help="入口 method 的 qualified_name")
    p.add_argument("--depth", type=int, default=20,
                   help="CTE 递归深度上限 (默认 20)")
    p.add_argument("--source-root", default=None,
                   help="JAR 的 sourceRoot 参数 (默认 = project_root)")
    p.add_argument("--jar", default=None,
                   help="java-method-call-extractor-1.0.0.jar 路径")
    p.add_argument("--jar-analyzer-db", default=None,
                   help="jar-analyzer SQLite DB path (preferred over codegraph for CTE traversal)")
    p.add_argument("--output", "-o", default=None,
                   help="输出 JSON 文件路径 (省略则打印到 stdout)")
    p.add_argument("--memurai", action="store_true",
                   help="尝试连接 Memurai 并写缓存 (失败不阻塞)")
    p.add_argument("--memurai-host", default="localhost")
    p.add_argument("--memurai-port", type=int, default=6379)
    p.add_argument("--ttl", type=int, default=864000,
                   help="缓存 TTL 秒数 (默认 864000 = 10天)")
    p.add_argument("--all-paths", action="store_true",
                   help="调用 build_all_chains_for_endpoint 而非 build_chain")
    p.add_argument("--loop-dir",
                   type=Path,
                   default=None,
                   help="Loop audit directory for chain file output (optional)")
    p.add_argument("--quiet", action="store_true",
                   help="降低日志输出")
    return p


def _maybe_connect_memurai(args: argparse.Namespace) -> Any:
    """若 --memurai 给出, 尝试连接; 失败返回 None 不抛。"""
    if not args.memurai:
        return None
    try:
        # 把 scripts/redis 加进 sys.path 后再 import, 避免 sys.path 不在 repo root
        _redis_dir = _REPO_ROOT / "scripts" / "redis"
        if str(_redis_dir) not in sys.path:
            sys.path.insert(0, str(_redis_dir))
        from memurai_client import Memurai  # type: ignore
        cli = Memurai(host=args.memurai_host, port=args.memurai_port, timeout=10.0)
        if not cli.ping():
            _log("Memurai PING 失败, 跳过缓存写入")
            return None
        return cli
    except Exception as e:  # noqa: BLE001
        _log("Memurai 连接失败: %s", e)
        return None


def _summarize(result: Dict[str, Any]) -> str:
    """简短文本摘要, 打印到 stderr 便于人工核对。"""
    lines = [
        f"entry_fqn:    {result.get('entry_fqn')}",
        f"entry_id:     {result.get('entry_id')}",
        f"sig_hash:     {result.get('sig_hash')}",
        f"total_nodes:  {result.get('total_nodes')}",
        f"total_edges:  {result.get('total_edges')}",
        f"total_sinks:  {result.get('total_sinks')}",
        f"cycle:        {result.get('cycle_detected')}",
    ]
    if "cache_key" in result:
        lines.append(f"cache_key:    {result['cache_key']}")
        lines.append(f"cache_written:{result.get('cache_written')}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)
    if args.quiet:
        logger.setLevel(logging.WARNING)

    project_root = Path(args.project_root)
    db_path = Path(args.db)
    source_root = Path(args.source_root) if args.source_root else None
    jar_path = Path(args.jar) if args.jar else None
    jar_analyzer_db_path = Path(args.jar_analyzer_db) if args.jar_analyzer_db else None

    memurai = _maybe_connect_memurai(args)

    # 当 jar_analyzer_db_path 存在且 db_path (codegraph) 不存在时, 使用纯 jar-analyzer 模式
    use_jar_analyzer_only = (
        jar_analyzer_db_path is not None
        and jar_analyzer_db_path.is_file()
        and not db_path.is_file()
    )

    try:
        if use_jar_analyzer_only:
            # 纯 jar-analyzer 模式: 不需要 codegraph
            ja_common_kwargs = dict(
                entry_fqn=args.entry,
                group_id=args.group_id,
                project_root=project_root,
                jar_analyzer_db_path=jar_analyzer_db_path,
                max_depth=args.depth,
                memurai_client=memurai,
                ttl=args.ttl,
                jar_path=jar_path,
                source_root=source_root,
                loop_audit_dir=args.loop_dir,
            )
            if args.all_paths:
                results = build_all_chains_for_endpoint_jar_analyzer(**ja_common_kwargs)
            else:
                single = build_chain_jar_analyzer(**ja_common_kwargs)
                results = [single]
        else:
            common_kwargs = dict(
                entry_fqn=args.entry,
                group_id=args.group_id,
                project_root=project_root,
                db_path=db_path,
                max_depth=args.depth,
                memurai_client=memurai,
                ttl=args.ttl,
                jar_path=jar_path,
                source_root=source_root,
                loop_audit_dir=args.loop_dir,
                jar_analyzer_db_path=jar_analyzer_db_path,
            )
            if args.all_paths:
                results = build_all_chains_for_endpoint(**common_kwargs)
            else:
                single = build_chain(**common_kwargs)
                results = [single]
    except LookupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    payload = {
        "path_count": len(results),
        "paths": results,
        "summary": {
            "total_nodes": sum(r["total_nodes"] for r in results),
            "total_edges": sum(r["total_edges"] for r in results),
            "total_sinks": sum(r["total_sinks"] for r in results),
        },
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        _log("已写入 %s (paths=%d, total_nodes=%d, total_sinks=%d)",
             out, len(results),
             payload["summary"]["total_nodes"],
             payload["summary"]["total_sinks"])
    else:
        print(text)

    # 顺便把第一条路径的摘要打到 stderr, 便于人工快速核对
    if results:
        _log("\n--- path[0] summary ---\n%s", _summarize(results[0]))

    return 0


if __name__ == "__main__":
    sys.exit(main())