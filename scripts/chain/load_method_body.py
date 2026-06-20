#!/usr/bin/env python3
"""
load_method_body.py — 子 agent 方法体加载工具

子 agent 用此脚本从 Memurai 加载调用链上指定节点的方法体。
方法体已包含 `// #fqn` 注释（标注所有非 groupId 调用）。

用法:
    # 加载单个方法体
    python load_method_body.py --group-id org.owasp.webgoat --node-id "method:abc123"

    # 加载一条链的前 5 层方法体
    python load_method_body.py --group-id org.owasp.webgoat --node-path "method:abc -> method:def -> method:ghi" --max-depth 5

    # 加载一条链的所有方法体
    python load_method_body.py --group-id org.owasp.webgoat --node-path "method:abc -> method:def"

输出: JSON 数组，每个元素含 fqn, node_id, body, depth
"""
import argparse
import json
import sys
from pathlib import Path

# 路径设置
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "redis"))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "chain"))

from memurai_client import Memurai


def load_single(memurai: Memurai, group_id: str, node_id: str) -> dict | None:
    """加载单个 node 的方法体。加载时 INCR 计数。"""
    key = f"{group_id}:method:{node_id}"
    raw = memurai.get(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        # INCR 计数（记录方法体被加载的次数）
        count_key = f"{key}:count"
        try:
            memurai._run(["INCR", count_key], check_error=False)
        except Exception:
            pass
        return {
            "node_id": node_id,
            "fqn": data.get("fqn", ""),
            "body": data.get("body", ""),
            "file": data.get("file", ""),
            "start_line": data.get("start_line") or data.get("startLine", 0),
            "depth": data.get("depth", 0),
        }
    except (json.JSONDecodeError, TypeError):
        return None


def load_chain(
    memurai: Memurai,
    group_id: str,
    node_path: str,
    max_depth: int = 0,
) -> list[dict]:
    """加载一条链的方法体。

    Args:
        node_path: "method:id1 -> method:id2 -> method:id3"
        max_depth: 最多加载前 N 层（0 = 全部）
    """
    node_ids = [nid.strip() for nid in node_path.split("->") if nid.strip()]

    if max_depth > 0:
        node_ids = node_ids[:max_depth]

    results = []
    for depth, nid in enumerate(node_ids):
        body_data = load_single(memurai, group_id, nid)
        if body_data:
            body_data["depth"] = depth
            results.append(body_data)

    return results


def main():
    parser = argparse.ArgumentParser(description="从 Memurai 加载方法体")
    parser.add_argument("--group-id", required=True, help="项目 groupId")
    parser.add_argument("--node-id", help="单个 node_id")
    parser.add_argument("--node-path", help="node_path (method:id1 -> method:id2 -> ...)")
    parser.add_argument("--max-depth", type=int, default=0, help="最多加载前 N 层（0=全部）")
    args = parser.parse_args()

    memurai = Memurai()

    if args.node_id:
        result = load_single(memurai, args.group_id, args.node_id)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("null")
    elif args.node_path:
        results = load_chain(memurai, args.group_id, args.node_path, args.max_depth)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("ERROR: 需要 --node-id 或 --node-path", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
