#!/usr/bin/env python3
"""
load_method_body.py — 子 agent 方法体加载工具

子 agent 用此脚本从 Memurai 加载调用链上指定节点的方法体。
方法体已包含 `// #fqn` 注释（标注所有非 groupId 调用）。

加载策略：
  --max-depth N: 前 N 层
  --tail-depth M: 后 M 层
  --max-depth 4 --tail-depth 2: 前4层 + 后2层（链 ≥6 层时）
  链 <6 层时全部加载

用法:
    # 加载前4层+后2层（注入类/文件类用）
    python load_method_body.py --group-id {gid} --node-path "..." --max-depth 4 --tail-depth 2

    # 加载前5层（认证鉴权/业务逻辑用）
    python load_method_body.py --group-id {gid} --node-path "..." --max-depth 5

    # 加载全部
    python load_method_body.py --group-id {gid} --node-path "..."

    # 加载单个
    python load_method_body.py --group-id {gid} --node-id "method:abc123"

输出: JSON 数组，每个元素含 fqn, node_id, body, depth
"""
import argparse
import json
import sys
from pathlib import Path

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
        count_key = f"{key}:count"
        try:
            memurai._run(["INCR", count_key], check_error=False)
        except Exception as e:
            print(f"WARN: INCR count failed for {count_key}: {e}", file=__import__('sys').stderr)
        return {
            "node_id": node_id,
            "fqn": data.get("fqn", ""),
            "body": data.get("body", ""),
            "file": data.get("file", ""),
            "start_line": data.get("start_line") or data.get("startLine", 0),
            "depth": data.get("depth", 0),
        }
    except (json.JSONDecodeError, TypeError) as e:
        print(f"WARN: corrupted cache data for {key}: {e}", file=__import__('sys').stderr)
        return None


def select_node_ids(
    node_ids: list[str],
    max_depth: int = 0,
    tail_depth: int = 0,
) -> list[tuple[int, str]]:
    """按策略选择 node_id 子集，返回 (depth, node_id) 列表。

    - max_depth > 0 and tail_depth > 0: 前 max_depth + 后 tail_depth（链 ≥ max_depth+tail_depth 时截断）
    - 链 < max_depth+tail_depth 时全部加载
    - max_depth > 0 and tail_depth == 0: 前 max_depth 层
    - max_depth == 0 and tail_depth == 0: 全部
    """
    total = len(node_ids)
    if max_depth == 0 and tail_depth == 0:
        return list(enumerate(node_ids))

    threshold = max_depth + tail_depth
    if total <= threshold:
        return list(enumerate(node_ids))

    head = list(enumerate(node_ids[:max_depth]))
    tail_start = total - tail_depth
    tail = [(i, node_ids[i]) for i in range(tail_start, total)]
    return head + tail


def load_chain(
    memurai: Memurai,
    group_id: str,
    node_path: str,
    max_depth: int = 0,
    tail_depth: int = 0,
) -> list[dict]:
    """加载一条链的方法体。

    Args:
        node_path: "method:id1 -> method:id2 -> method:id3"
        max_depth: 前 N 层（0 = 不限）
        tail_depth: 后 N 层（0 = 不限）
    """
    node_ids = [nid.strip() for nid in node_path.split("->") if nid.strip()]
    selected = select_node_ids(node_ids, max_depth, tail_depth)

    results = []
    for depth, nid in selected:
        body_data = load_single(memurai, group_id, nid)
        if body_data is None:
            raise LookupError(
                f"Memurai 缓存未命中: {group_id}:method:{nid} (depth={depth})。"
                f"请先运行 Phase 2 完成方法体预取。"
            )
        body_data["depth"] = depth
        results.append(body_data)

    return results


def main():
    parser = argparse.ArgumentParser(description="从 Memurai 加载方法体")
    parser.add_argument("--group-id", required=True, help="项目 groupId")
    parser.add_argument("--chain-id", help="chain_id（从 jar-analyzer.db chains 表自动读取 node_path，取最后一个 node）")
    parser.add_argument("--node-id", help="单个 node_id")
    parser.add_argument("--node-path", help="node_path (method:id1 -> method:id2 -> ...)")
    parser.add_argument("--max-depth", type=int, default=4, help="前 N 层（默认 4）")
    parser.add_argument("--tail-depth", type=int, default=2, help="后 N 层（默认 2，链<6层时全部加载）")
    parser.add_argument("--jar-analyzer-db", default="", help="jar-analyzer.db 路径（含 chains 表，与 --chain-id 配合使用）")
    parser.add_argument("--loop-dir", default="", help="loop_audit 目录（回退: 查找 jar-analyzer.db）")
    args = parser.parse_args()

    memurai = Memurai()

    # --chain-id: 从 jar-analyzer.db chains 表自动取 node_path 最后一个 node
    if args.chain_id:
        chain_db_path = None
        # 优先使用 --jar-analyzer-db
        if args.jar_analyzer_db:
            p = Path(args.jar_analyzer_db)
            if p.is_file():
                chain_db_path = p
        # 回退: loop_dir 或常见位置的 jar-analyzer.db
        if chain_db_path is None:
            loop_dir = Path(args.loop_dir) if args.loop_dir else Path.cwd()
            for guess in [
                loop_dir / "jar-analyzer.db",
                Path.cwd() / "projects" / args.group_id / "loop_audit" / "jar-analyzer.db",
                Path(__file__).resolve().parent.parent.parent / "projects" / args.group_id / "loop_audit" / "jar-analyzer.db",
                loop_dir / "chains.db",
                Path(__file__).resolve().parent.parent.parent / "projects" / args.group_id / "loop_audit" / "chains.db",
            ]:
                if guess.exists():
                    chain_db_path = guess
                    break
        if chain_db_path and chain_db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(chain_db_path))
            row = conn.execute(
                "SELECT node_path FROM chains WHERE chain_id = ?", (args.chain_id,)
            ).fetchone()
            conn.close()
            if row and row[0]:
                node_ids = [n.strip() for n in row[0].split("->") if n.strip()]
                last_nid = node_ids[-1]
                result = load_single(memurai, args.group_id, last_nid)
                if result:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print("null")
                return 0
        print("null", file=sys.stderr)
        return 1

    if args.node_id:
        result = load_single(memurai, args.group_id, args.node_id)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("null")
    elif args.node_path:
        results = load_chain(memurai, args.group_id, args.node_path, args.max_depth, args.tail_depth)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("ERROR: 需要 --node-id 或 --node-path", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
