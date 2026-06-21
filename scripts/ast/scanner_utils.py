"""scanner_utils.py — 暴露面扫描核心函数

8 个纯函数, 设计文档 `design-docs/暴露面扫描设计.md` §6-8 的实现。
hashkey 策略已按用户决策调整为 nodes.id (非 md5),保留 md5 兼容回退。
"""

from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


# Default codegraph SQLite location (relative to project root).
_DEFAULT_CODEGRAPH_DB = Path(".codegraph") / "codegraph.db"


def node_hash_key(node_id: Any) -> str:
    """把 codegraph nodes.id 规范化为字符串 hashkey (无 md5)。"""
    if node_id is None:
        return ""
    return str(node_id).strip()


def md5_legacy_hash(file_path: str, line: int, annotation_text: str) -> str:
    """Legacy md5 fallback hashkey (16 hex)。

    仅在 codegraph 不可用 / nodes.id 查不到时使用,与旧 attack_surface_scanner
    输出一致(包含 file / line / annotation,行号移动会改变 hash)。
    """
    blob = f"{file_path}|{int(line)}|{annotation_text}".encode("utf-8", errors="replace")
    return hashlib.md5(blob).hexdigest()[:16]


def _candidate_file_paths(
    file_path: str,
    project_root: Optional[Union[str, Path]],
) -> List[str]:
    """返回 codegraph ``nodes.file_path`` 可能存储的所有形态 (去重保序)。

    codegraph 存储相对 ``project_root`` 的 forward-slash 路径,但历史数据 /
    绝对路径 / Windows 大小写都可能存在,扫描时多形态都试一遍。
    """
    raw = str(file_path or "").replace("\\", "/")
    out: List[str] = []
    seen: Set[str] = set()

    def _push(c: str) -> None:
        c = c.strip()
        if not c or c in seen:
            return
        seen.add(c)
        out.append(c)

    _push(raw)
    _push(raw.lstrip("/"))
    if project_root:
        root = Path(project_root).resolve()
        try:
            abs_path = (root / raw) if not Path(raw).is_absolute() else Path(raw)
            rel = abs_path.resolve().relative_to(root)
            _push(str(rel).replace("\\", "/"))
        except (OSError, ValueError):
            pass
    return out


def lookup_nodes_id(
    codegraph_db: Union[str, Path],
    file_path: str,
    annotation_line: int,
    project_root: Optional[Union[str, Path]] = None,
) -> Optional[str]:
    """Query codegraph SQLite for the ``nodes.id`` owning ``annotation_line``.

    Returns the string ``nodes.id`` (e.g. ``"method:bd0ef3f040b4b1e8a649098d210ffd26"``)
    or ``None`` if the DB is missing / unreadable / no match.

    Line numbering:
    - ``annotation_line`` is 1-based (matches ast-grep ``range.start.line + 1``)
    - codegraph ``nodes.start_line / end_line`` are 0-based
    - codegraph 把方法前的注解纳入 ``start_line``(例如 ``@RequestMapping`` 在
      1-based 行 56,方法体在 1-based 行 61,但 ``start_line=56`` 0-based = 57 1-based,
      已包含前导注解)

    SQL 策略来自 ``design-docs/chain-sql-engine.md``:
        ``start_line - 1 <= annotation_line AND end_line >= annotation_line``
    其中 ``annotation_line`` 仍为 1-based,这样 0-based 方法起点减 1 后可与 1-based
    注解行对齐,允许注解位于方法起始的前 1 行内。

    Returns the INNERMOST method when multiple overlap (ORDER BY smallest
    span wins), which matches what the scanner's ``find_enclosing_method``
    would pick.
    """
    db_path = Path(codegraph_db)
    if not db_path.is_file():
        return None
    try:
        anno_1based = int(annotation_line)
    except (TypeError, ValueError):
        return None
    if anno_1based < 1:
        return None

    candidates = _candidate_file_paths(file_path, project_root)
    if not candidates:
        return None

    # Per chain-sql-engine.md §4:
    #   start_line - 1 <= annotation_line_1based
    #   end_line      >= annotation_line_1based
    sql = (
        "SELECT id, start_line, end_line FROM nodes "
        "WHERE kind = 'method' "
        "  AND file_path = ? "
        "  AND (start_line - 1) <= ? AND end_line >= ? "
        "ORDER BY (end_line - start_line) ASC "
        "LIMIT 1"
    )
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            for candidate in candidates:
                row = conn.execute(
                    sql, (candidate, anno_1based, anno_1based),
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return None


def resolve_hashkey(
    file_path: str,
    annotation_line: int,
    annotation_text: str,
    *,
    codegraph_db: Optional[Union[str, Path]] = None,
    project_root: Optional[Union[str, Path]] = None,
    use_nodes_id: bool = True,
) -> Tuple[str, Optional[str]]:
    """Resolve the hashkey for a (file, line, annotation) tuple。

    Returns ``(sig_hash, nodes_id_or_None)``。

    行为契约(向后兼容):
    - ``use_nodes_id=True`` 且 codegraph 可读 + 命中节点 → 返回 nodes.id
    - 否则降级到 ``md5_legacy_hash``,``nodes_id=None``
    - ``use_nodes_id=False`` 强制 md5 路径(便于回归对比与 debug)
    """
    nodes_id: Optional[str] = None
    if use_nodes_id and codegraph_db:
        nodes_id = lookup_nodes_id(
            codegraph_db, file_path, annotation_line, project_root=project_root,
        )
        if nodes_id is not None:
            return (node_hash_key(nodes_id), nodes_id)
    sig = md5_legacy_hash(file_path, annotation_line, annotation_text)
    return (sig, None)


def fqn_to_method_name(fqn: str) -> str:
    """从 FQN (# 分隔或 . 分隔) 提取方法名。"""
    return fqn.rsplit("#", 1)[-1] if "#" in fqn else fqn.rsplit(".", 1)[-1]


def inject_sink_comment(
    line: str,
    third_party_calls: List[str],
    fqn_sink_set: Set[str],
) -> str:
    """若行包含任何 third_party_calls 的方法名 + '(', 返回带 ``//fqn:`` 注释的行。

    标注所有 third_party_calls（非 groupId 调用），不区分是否为 sink。
    已有注释开头的行跳过。
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        return line
    indent = line[:len(line) - len(line.lstrip())]
    for call in third_party_calls:
        method_name = fqn_to_method_name(call)
        if method_name and method_name + "(" in line:
            return f"{indent}//fqn: {call}\n" + line
    return line


def get_body(groupId: str, node_id: str, memurai: Any) -> Optional[str]:
    """从 mock memurai 缓存取方法体。key 格式 `{groupId}:method:{node_id}`。"""
    try:
        key = f"{groupId}:method:{node_id}"
        raw = memurai.get(key)
        if raw is None:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data.get("body")
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
        return None


def detect_annotation_changes(
    project_dir: Union[str, Path],
    groupId: str,
) -> List[Dict[str, str]]:
    """读 {project_dir}/{groupId}/注解/specific.json,返回 body_hash 变更列表。"""
    specific_path = Path(project_dir) / groupId / "注解" / "specific.json"
    if not specific_path.is_file():
        return []
    try:
        with open(specific_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    changes: List[Dict[str, str]] = []
    for a in data.get("annotations", []):
        body = a.get("body")
        if body is None:
            continue
        # 用 hashlib md5 的前 16 hex (设计文档 §8 规则)
        current_hash = hashlib.md5(body.encode("utf-8")).hexdigest()[:16]
        recorded_hash = a.get("body_hash")
        if recorded_hash is None:
            continue
        if current_hash != recorded_hash:
            changes.append({
                "fqn": a.get("fqn", ""),
                "old_hash": recorded_hash,
                "new_hash": current_hash,
            })
    return changes


def classify_route(
    annotation_fqn: str,
    global_public: Dict[str, Any],
    project_specific: Dict[str, Any],
) -> str:
    """按设计文档 §6 分类: public / specific / unknown。"""
    public_fqns = {a.get("fqn") for a in global_public.get("annotations", [])}
    if annotation_fqn in public_fqns:
        return "public"

    specific_fqns = {a.get("fqn") for a in project_specific.get("annotations", [])}
    if annotation_fqn in specific_fqns:
        return "specific"

    project_group_id = project_specific.get("groupId", "")
    if project_group_id and annotation_fqn.startswith(project_group_id + "."):
        return "specific"

    return "unknown"


def validate(project_root: Union[str, Path], groupId: str) -> None:
    """按设计文档 §7 执行 3 条断言。失败抛 AssertionError。"""
    project_root = Path(project_root)
    group_dir = project_root / groupId

    # 断言 1: 路由数 == chain 文件数
    routes_path = group_dir / "routes.json"
    with open(routes_path, "r", encoding="utf-8") as f:
        routes_data = json.load(f)
    routes = routes_data.get("routes", [])

    chains_dir = group_dir / "chains"
    if chains_dir.is_dir():
        chain_files = list(chains_dir.glob("*.json"))
    else:
        chain_files = []
    assert len(routes) == len(chain_files), \
        f"路由数 {len(routes)} != chain 文件数 {len(chain_files)}"

    # 断言 2: routes 的 signature_hash 与 chains/ 文件名 一一对应
    route_hashes = {r.get("signature_hash") for r in routes}
    chain_hashes = {f.stem for f in chain_files}
    assert route_hashes == chain_hashes, \
        f"hash 不匹配: 路由独有 {route_hashes - chain_hashes}, chain 独有 {chain_hashes - route_hashes}"

    # 断言 3: routes 中所有 annotation_fqn 在全局/项目注解表中
    public_path = project_root / "注解_public.json"
    specific_path = group_dir / "注解" / "specific.json"

    with open(public_path, "r", encoding="utf-8") as f:
        public_annotations = json.load(f).get("annotations", [])
    with open(specific_path, "r", encoding="utf-8") as f:
        specific_annotations = json.load(f).get("annotations", [])

    all_annotation_fqns = (
        {a.get("fqn") for a in public_annotations}
        | {a.get("fqn") for a in specific_annotations}
    )
    route_annotation_fqns = {r.get("annotation_fqn") for r in routes}
    missing = route_annotation_fqns - all_annotation_fqns
    assert not missing, f"路由引用未定义注解: {missing}"