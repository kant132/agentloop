"""
redis-batch-prefetch.py

启动每个调用链分析前，**先**用此脚本把整条链上所有方法体批量预取到 Memurai。
subagent 启动后全程从 Memurai 读，不再调 codegraph。

> 改用 `memurai-cli.exe`（C:\\Program Files\\Memurai\\memurai-cli.exe），
> 不再依赖 pip install redis。封装在 `memurai_client.py`。

用法:
    python redis-batch-prefetch.py --chain chain.json --group-id com.example.x --commit HEAD

输入:
    chain.json: 支持两种格式：
      1. chain_builder 输出: {"variants": [{"chain": [...], "entry_fqn": ...}]}
      2. 旧格式: [{"fqn":"...","startLine":...,"body":"...","file":"...","line":...}, ...]
    （可由 chain_builder 或 sqlite-extract-chain.py + codegraph 工具组合生成）

输出:
    写入 Memurai keys:
        {groupId}:method:{fqn}#{startline}  → method body + meta
        {groupId}:prefetch:{chainId}        → chain summary
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# 兼容从 scripts/redis/ 目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent))
from memurai_client import Memurai, MemuraiError


def make_method_key(group_id: str, fqn: str, start_line: int) -> str:
    return f"{group_id}:method:{fqn}#{start_line}"


def make_prefetch_key(group_id: str, chain_id: str) -> str:
    return f"{group_id}:prefetch:{chain_id}"


def prefetch_chain(
    cli: Memurai,
    chain: List[Dict],
    group_id: str,
    chain_id: str,
    method_ttl: int = 864000,  # 10天
    prefetch_ttl: int = 864000,  # 10天
) -> dict:
    """批量预取 chain 中所有方法到 Memurai。

    性能优化：
    - **走 --pipe** 一次发 RESP 流：N 条 `SET k v EX ttl` 命令 = 1 次子进程
    - 对比"MSET + N 次 EXPIRE" = N+1 次子进程，链 30 个方法时快 30x
    - value 含换行/中文/双引号 → RESP bulk string 二进制安全
    """
    method_keyvalues: Dict[str, str] = {}
    chain_summary = {
        "chain_id": chain_id,
        "group_id": group_id,
        "methods": [],
    }
    setex_items: List[Tuple[str, int, str]] = []  # (key, ttl, value) for --pipe

    for m in chain:
        fqn = m["fqn"]
        start_line = m.get("startLine") or m.get("line") or 0
        node_id = m.get("node_id", "")
        key = make_method_key(group_id, fqn, start_line)

        value = json.dumps({
            "fqn": fqn,
            "start_line": start_line,
            "body": m.get("body", ""),
            "file": m.get("file", ""),
            "line": m.get("line", 0),
            "class": m.get("class", ""),
            "depth": m.get("depth", 0),
            "sha256": hashlib.sha256(m.get("body", "").encode()).hexdigest(),
            "node_id": node_id,
        }, ensure_ascii=False)

        method_keyvalues[key] = value

        # 同时按 node_id 存一份（load_method_body.py 用这个 key）
        if node_id:
            node_key = f"{group_id}:method:{node_id}"
            method_keyvalues[node_key] = value
            setex_items.append((node_key, method_ttl, value))
        setex_items.append((key, method_ttl, value))
        chain_summary["methods"].append({
            "fqn": fqn,
            "start_line": start_line,
            "key": key,
        })

    # 1. **走 --pipe 一次写入 N 条 SET key value EX ttl**
    #    替代 "MSET + N 次 EXPIRE" = N+1 次子进程
    #    现在 1 次子进程搞定所有方法 + 各自 TTL
    if setex_items:
        cli.pipe_setex_batch(setex_items)

    # 3. 写入 chain summary
    prefetch_key = make_prefetch_key(group_id, chain_id)
    cli.setex(prefetch_key, prefetch_ttl, json.dumps(chain_summary, ensure_ascii=False))

    return {
        "status": "ok",
        "chain_id": chain_id,
        "methods_prefetched": len(method_keyvalues),
        "prefetch_key": prefetch_key,
        "method_ttl": method_ttl,
    }


def main():
    parser = argparse.ArgumentParser(description="批量预取调用链到 Memurai")
    parser.add_argument("--chain", required=True, help="chain.json 路径")
    parser.add_argument("--group-id", required=True, help="项目 groupId")
    parser.add_argument("--commit", default="HEAD", help="(deprecated, ignored) commit hash")
    parser.add_argument("--chain-id", help="chain ID（默认由 entry fqn 生成）")
    parser.add_argument("--redis-host", default="localhost", help="Memurai host（保留参数名兼容）")
    parser.add_argument("--redis-port", type=int, default=6379, help="Memurai port（默认 6379）")
    parser.add_argument("--cli", help="memurai-cli.exe 显式路径")
    args = parser.parse_args()

    # 读 chain.json
    try:
        with open(args.chain, "r", encoding="utf-8") as f:
            chain_raw = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: chain 文件不存在: {args.chain}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: chain JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 适配两种格式：
    # 1. chain_builder 输出: {"variants": [{"chain": [...], "entry_fqn": ...}]}
    # 2. 旧格式: [{fqn, ...}, ...] (直接列表)
    if isinstance(chain_raw, dict) and "variants" in chain_raw:
        variants = chain_raw["variants"]
        if not variants:
            print("ERROR: variants 为空", file=sys.stderr)
            sys.exit(1)
        chain_nodes = variants[0]["chain"]
        chain_id = args.chain_id or variants[0].get("entry_fqn", "unnamed")[:16]
    elif isinstance(chain_raw, list):
        chain_nodes = chain_raw
        chain_id = args.chain_id
        if not chain_id and chain_nodes:
            chain_id = hashlib.sha256(chain_nodes[0]["fqn"].encode()).hexdigest()[:16]
        elif not chain_id:
            chain_id = "empty"
    else:
        print("ERROR: 无法识别的 chain JSON 格式", file=sys.stderr)
        sys.exit(1)

    # 连接 Memurai（通过 CLI 子进程）
    try:
        cli = Memurai(
            host=args.redis_host,
            port=args.redis_port,
            cli_path=args.cli,
        )
        cli.ping()
    except MemuraiError as e:
        print(f"ERROR: Memurai 不可用: {e}", file=sys.stderr)
        sys.exit(1)

    result = prefetch_chain(cli, chain_nodes, args.group_id, chain_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
