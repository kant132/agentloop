"""
force-rescan.py

人工复审入口：对某个被 L1 剪枝的端点强制重扫。

来自原始要求："如果人工强调继续分析，才分析这个文档下的外部端口，且只分析业务、逻辑漏洞"。

行为：
- **必跑** FWD-C（业务）+ FWD-D（状态）
- **不**跑 FWD-A（注入）/ FWD-B（鉴权）/ FWD-INFO
- 即便该端点被 P-L1-001（无参数）/ P-L1-002（数字参数）剪过

> 改用 `memurai-cli.exe`，封装在 `memurai_client.py`。
> 不再使用 pip install redis。

用法:
    python force-rescan.py --endpoint "GET /api/foo" --modes "C,D" --group-id com.example.x
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "redis"))
from memurai_client import Memurai, MemuraiError


def force_rescan(
    cli: Memurai,
    group_id: str,
    commit: str,
    endpoint: str,
    endpoint_fqn: str,
    modes: list,
    audit_root: str = "loop_audit",
) -> dict:
    """标记强制重扫请求，供 orchestrator 调度"""
    chain_id = hashlib.sha256(endpoint_fqn.encode()).hexdigest()[:16]
    key = f"audit:{group_id}:commit:{commit}:force-rescan:{chain_id}"

    payload = {
        "endpoint": endpoint,
        "endpoint_fqn": endpoint_fqn,
        "chain_id": chain_id,
        "modes": modes,  # 必须是 ["C", "D"]
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "reason": "human_override_l1_prune",
    }

    cli.set_json(key, payload)

    # 写重扫清单
    rescan_list_path = Path(audit_root) / "force-rescan-list.jsonl"
    rescan_list_path.parent.mkdir(parents=True, exist_ok=True)
    with open(rescan_list_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return {
        "status": "queued",
        "chain_id": chain_id,
        "redis_key": key,
        "rescan_list": str(rescan_list_path),
        "modes_will_run": modes,
        "modes_will_skip": ["A", "B", "INFO"],
        "note": "重扫只跑指定 modes（业务+状态）。注入/鉴权/信息泄露不重跑（与原始要求一致）",
    }


def main():
    parser = argparse.ArgumentParser(description="强制重扫被 L1 剪枝的端点")
    parser.add_argument("--endpoint", required=True, help='端点描述，如 "GET /api/foo"')
    parser.add_argument("--endpoint-fqn", required=True, help="端点方法 fqn")
    parser.add_argument("--modes", default="C,D", help="要跑的模式（默认 C,D；不应包含 A/B/INFO）")
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--audit-root", default="loop_audit")
    parser.add_argument("--redis-host", default="localhost", help="Memurai host")
    parser.add_argument("--redis-port", type=int, default=6379, help="Memurai port")
    parser.add_argument("--cli", help="memurai-cli.exe 显式路径")
    args = parser.parse_args()

    modes = [m.strip().upper() for m in args.modes.split(",")]

    # 校验：强制重扫必须只跑 C/D
    allowed_modes = {"C", "D"}
    forbidden = set(modes) - allowed_modes
    if forbidden:
        print(f"ERROR: 强制重扫只允许跑 C/D，包含了不允许的: {forbidden}", file=sys.stderr)
        sys.exit(1)

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

    result = force_rescan(
        cli, args.group_id, args.commit, args.endpoint, args.endpoint_fqn, modes, args.audit_root
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
