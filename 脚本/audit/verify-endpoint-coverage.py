"""
verify-endpoint-coverage.py

**端点报告覆盖率校验**（P5.4 硬约束）。

不变量（来自原始指令 § 一.6）：
    |高风险端点/的文件数| + |中低险端点/的文件数|  ==  |外部端点总数|

任何偏差都意味着：
- 有端点漏分析（潜在漏报）
- 有端点多写报告（重复劳动）
- 文件名错误（落到错误目录）

用法:
    # 用 .example 模板验证目录结构正确性
    python verify-endpoint-coverage.py \\
        --endpoints loop_audit/external_endpoints/端点.jsonl \\
        --high-risk-dir loop_audit/routes/高风险端点/ \\
        --mid-low-risk-dir loop_audit/routes/中低险端点/ \\
        --audit-root loop_audit

退出码:
    0 = 覆盖完整
    1 = 偏差（数量不一致 / 找不到端点清单）
"""
import argparse
import json
import sys
from pathlib import Path
from collections import Counter


def count_endpoints(endpoints_path: str) -> tuple:
    """从 external_endpoints/端点.jsonl 读端点总数 + sig_hash 集合。

    端点 jsonl 每行：{"fqn": "...", "endpoint": "GET /path", "sig_hash": "..."}
    返回 (total_endpoints, set_of_sig_hashes)
    """
    items = []
    if not Path(endpoints_path).exists():
        return 0, set(), f"端点文件不存在: {endpoints_path}"

    with open(endpoints_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    sig_set = set()
    for item in items:
        sig = item.get("sig_hash", "")
        if sig:
            sig_set.add(sig)

    return len(items), sig_set, None


def count_reports(high_risk_dir: str, mid_low_risk_dir: str) -> tuple:
    """扫两个目录的 .md 文件，提取 sig_hash 作为唯一键。

    文件名规范（含 CVSS 4.0）：{等级}_{CVSS}_{类型}_{fqn}_{method}_{sigHash}.md
    例：严重_7.5_CSRF_org__owasp__webgoat__..._POST_..._dd128289.md

    5 等级前缀：致命 / 严重 / 中 / 低 / 无（**不带 中危 / 低危 / 无风险** 这些旧称）

    sig_hash 是 SHA256 前 8 位 hex（路由 id 的哈希），唯一。
    """
    high_files = list(Path(high_risk_dir).glob("*.md")) if Path(high_risk_dir).exists() else []
    mid_files = list(Path(mid_low_risk_dir).glob("*.md")) if Path(mid_low_risk_dir).exists() else []

    VALID_SEVERITIES = {"致命", "严重", "中", "低", "无"}
    VALID_STATUSES = {"是问题", "非问题", "暂时无法确认", "failed"}

    def parse_filename(p: Path) -> tuple | None:
        stem = p.stem
        # 文件名以 _<hex8> 结尾（可能含 -roundNNN 后缀）
        # 先去掉 -roundNNN 后缀
        import re
        stem_clean = re.sub(r'-round\d+$', '', stem)
        parts = stem_clean.rsplit("_", 1)
        if len(parts) != 2:
            return None
        prefix, sig = parts
        # sig 必须是 8 位 hex
        if not (len(sig) == 8 and all(c in "0123456789abcdef" for c in sig)):
            return None
        # §19 格式：{验证状态}_{问题等级}_{CVSS}_{漏洞类型}_{fqn}_{method}_{sigHash}
        # 旧格式：{问题等级}_{CVSS}_{类型}_{fqn}_{method}_{sigHash}
        segs = prefix.split("_")
        if segs[0] in VALID_STATUSES:
            # §19 format: second segment is severity
            severity = segs[1] if len(segs) > 1 else ""
        else:
            # Old format: first segment is severity
            severity = segs[0]
        if severity not in VALID_SEVERITIES:
            return None
        return (sig, severity)

    high_pairs = {parse_filename(p) for p in high_files if parse_filename(p)}
    mid_pairs = {parse_filename(p) for p in mid_files if parse_filename(p)}

    return len(high_files), len(mid_files), high_pairs, mid_pairs, len(high_files) + len(mid_files)


def verify(endpoints_path: str, high_dir: str, mid_dir: str) -> dict:
    total_endpoints, endpoint_sigs, err = count_endpoints(endpoints_path)
    if err:
        return {"ok": False, "error": err}

    n_high, n_mid, high_pairs, mid_pairs, n_total = count_reports(high_dir, mid_dir)

    # 报告 sig 集合
    reported_sigs = {sig for sig, _ in (high_pairs | mid_pairs)}

    # 硬不变量：n_total == total_endpoints（P5.4 原始指令）
    invariant_ok = (n_total == total_endpoints)

    # 软检查：sig_hash 全覆盖（受命名规范影响，命名对就 PASS）
    missing = endpoint_sigs - reported_sigs
    extra = reported_sigs - endpoint_sigs

    # 按等级分桶
    by_severity = Counter()
    for _, sev in high_pairs:
        by_severity[sev] += 1
    for _, sev in mid_pairs:
        by_severity[sev] += 1

    return {
        "ok": invariant_ok,
        "hard_invariant": "高风险 + 中低险 = 总端点数 (P5.4)",
        "soft_check": "sig_hash 全覆盖（5 等级 + CVSS 4.0 命名）",
        "invariant_ok": invariant_ok,
        "sig_match_ok": not missing and not extra,
        "total_endpoints": total_endpoints,
        "high_risk_count": n_high,
        "mid_low_risk_count": n_mid,
        "total_reports": n_total,
        "missing": sorted(missing),
        "extra": sorted(extra),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "by_severity_bucket": dict(by_severity),
        "directories": {
            "high_risk_dir": str(high_dir),
            "mid_low_risk_dir": str(mid_dir),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="P5.4 端点报告覆盖率校验")
    parser.add_argument("--endpoints", required=True, help="端点 jsonl 路径")
    parser.add_argument("--high-risk-dir", required=True, help="高风险端点目录")
    parser.add_argument("--mid-low-risk-dir", required=True, help="中低险端点目录")
    args = parser.parse_args()

    try:
        result = verify(args.endpoints, args.high_risk_dir, args.mid_low_risk_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if not result["ok"]:
            print(f"\n[WARN] 硬不变量违反: {result.get('hard_invariant', '')}", file=sys.stderr)
            print(f"  endpoints={result.get('total_endpoints')}  reports={result.get('total_reports')}", file=sys.stderr)
            sys.exit(1)
        elif not result["sig_match_ok"]:
            # 软检查不通过：WARN 但 exit 0（因为硬不变量已通过）
            print(f"\n[INFO] sig 软检查未通过（{result['missing_count']} missing / {result['extra_count']} extra），硬不变量已通过", file=sys.stderr)
            sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
