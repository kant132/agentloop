"""scanner_utils.py — 暴露面扫描核心函数

7 个纯函数, 设计文档 `设计文档/暴露面扫描设计.md` §6-8 的实现。
hashkey 策略已按用户决策调整为 nodes.id (非 md5)。
"""

from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union


def node_hash_key(node_id: Any) -> str:
    """把 codegraph nodes.id 规范化为字符串 hashkey (无 md5)。"""
    return str(node_id).strip()


def fqn_to_method_name(fqn: str) -> str:
    """从 FQN (# 分隔或 . 分隔) 提取方法名。"""
    return fqn.rsplit("#", 1)[-1] if "#" in fqn else fqn.rsplit(".", 1)[-1]


def inject_sink_comment(
    line: str,
    third_party_calls: List[str],
    fqn_sink_set: Set[str],
) -> str:
    """若行包含任何 third_party_calls 且该 FQN 在 fqn_sink_set 中,返回带插入 sink 注释的字符串,否则原样返回。已有注释开头行跳过。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        return line
    indent = line[:len(line) - len(line.lstrip())]
    for call in third_party_calls:
        if call not in fqn_sink_set:
            continue
        method_name = fqn_to_method_name(call)
        if method_name and method_name + "(" in line:
            return f"{indent}// sink: {call}\n" + line
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