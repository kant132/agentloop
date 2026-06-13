#!/usr/bin/env python3
"""
批量给 269 个 route reports 补"验证状态"前缀 + PoC 验证段。
§ 19.4 强约束。

用法: python 脚本/audit/rename_reports_add_status.py
"""

import re
import os
import sys
from pathlib import Path
from datetime import datetime

# ── 路径常量 ──────────────────────────────────────────────
BASE = Path(r"D:\code\WebGoat-2025.3\loop_audit\routes")
HIGH_DIR = BASE / "高风险端点"
LOW_DIR = BASE / "中低险端点"
POC_DIR = BASE / "poc"
LOG_DIR = Path(r"D:\code\WebGoat-2025.3\loop_audit\loop-log\cross-50r")

# ── 字段提取正则 ──────────────────────────────────────────
RE_LEVEL = re.compile(r"\*\*危险等级\*\*:\s*(.+)")
RE_CVSS = re.compile(r"\*\*CVSS 4\.0\*\*:\s*(.+)")
RE_VTYPE = re.compile(r"\*\*漏洞类型\*\*:\s*(.+)")
RE_FQN = re.compile(r"\*\*全限定名\*\*:\s*`(.+?)`")
RE_METHOD = re.compile(r"\*\*方法\*\*:\s*`(.+?)`")
RE_SIGHASH = re.compile(r"\*\*签名哈希\*\*:\s*`(.+?)`")


def extract_fields(text: str) -> dict:
    """从 report .md 内容提取 6 个关键字段。"""
    m = {
        "level": (RE_LEVEL.search(text) or [None, None])[1],
        "cvss": (RE_CVSS.search(text) or [None, None])[1],
        "vtype": (RE_VTYPE.search(text) or [None, None])[1],
        "fqn": (RE_FQN.search(text) or [None, None])[1],
        "method": (RE_METHOD.search(text) or [None, None])[1],
        "sighash": (RE_SIGHASH.search(text) or [None, None])[1],
    }
    # 清理尾部空白
    for k in m:
        if m[k]:
            m[k] = m[k].strip()
    return m


def build_poc_lookup(poc_dir: Path) -> dict:
    """
    构建 PoC 查找表: (fqn, method, vtype) → poc_filename
    PoC 文件名格式: 是问题_{等级}_{FQN}.{method}-{漏洞类型}-round008.md
    """
    lookup = {}
    if not poc_dir.exists():
        return lookup
    for fn in poc_dir.iterdir():
        if not fn.suffix == ".md":
            continue
        name = fn.stem  # 去掉 .md
        # 解析: 是问题_{等级}_{rest}
        # rest = FQN.method-漏洞类型-round008
        parts = name.split("_", 2)  # [是问题, 等级, rest]
        if len(parts) < 3:
            continue
        rest = parts[2]  # e.g. org.owasp...Assignment.completed-AccessControl-round008
        # 从 rest 提取: FQN.method-漏洞类型-roundXXX
        # 最后一个 - 分隔 round，倒数第二个 - 分隔漏洞类型
        m = re.match(r"(.+)-(\w+)-round\d+$", rest)
        if not m:
            continue
        fqn_method = m.group(1)  # e.g. org.owasp...Assignment.completed
        vtype = m.group(2)  # e.g. AccessControl
        # fqn_method = FQN.method → split on last dot
        dot_idx = fqn_method.rfind(".")
        if dot_idx < 0:
            continue
        fqn = fqn_method[:dot_idx]
        method = fqn_method[dot_idx + 1:]
        lookup[(fqn, method, vtype)] = fn.name
    return lookup


def add_poc_section(text: str, status: str, poc_path: str) -> str:
    """在文件末尾 --- 之前插入 ## PoC 验证 段。"""
    # 如果已有 PoC 验证段，跳过
    if "## PoC 验证" in text:
        return text

    poc_block = (
        f"\n## PoC 验证\n"
        f"- **验证状态**: {status}\n"
        f"- **PoC 文件**: {poc_path}\n"
    )

    # 在最后的 --- 之前插入
    # 找到最后一个 --- 的位置
    last_hr = text.rfind("\n---\n")
    if last_hr >= 0:
        return text[:last_hr] + poc_block + text[last_hr:]
    else:
        # 没有 ---，追加到末尾
        return text + poc_block


def build_new_filename(fields: dict, status: str) -> str:
    """构造 7 段新文件名。"""
    fqn_escaped = fields["fqn"].replace(".", "__")
    return (
        f"{status}_{fields['level']}_{fields['cvss']}_{fields['vtype']}"
        f"_{fqn_escaped}_{fields['method']}_{fields['sighash']}.md"
    )


def process_directory(
    dir_path: Path,
    status: str,
    poc_lookup: dict,
    log_entries: list,
) -> tuple[int, int]:
    """处理一个目录下所有 .md 文件。返回 (成功数, 跳过数)。"""
    ok = 0
    skip = 0

    md_files = sorted(dir_path.glob("*.md"))
    for fp in md_files:
        try:
            text = fp.read_text(encoding="utf-8")
            fields = extract_fields(text)

            # 检查必需字段
            missing = [k for k, v in fields.items() if not v]
            if missing:
                log_entries.append(
                    f"| SKIP | `{fp.name}` | 缺字段: {missing} |"
                )
                skip += 1
                continue

            # 查找 PoC
            key = (fields["fqn"], fields["method"], fields["vtype"])
            if status == "是问题" and key in poc_lookup:
                poc_path = f"routes/poc/{poc_lookup[key]}"
            else:
                poc_path = "无（未触发）"

            # 补 PoC 验证段
            new_text = add_poc_section(text, status, poc_path)
            if new_text != text:
                fp.write_text(new_text, encoding="utf-8")

            # 构造新文件名
            new_name = build_new_filename(fields, status)
            new_fp = fp.parent / new_name

            if fp.name != new_name:
                if new_fp.exists():
                    log_entries.append(
                        f"| SKIP | `{fp.name}` | 目标已存在: `{new_name}` |"
                    )
                    skip += 1
                    continue
                fp.rename(new_fp)

            log_entries.append(f"| OK | `{fp.name}` | `{new_name}` |")
            ok += 1

        except Exception as e:
            log_entries.append(f"| ERROR | `{fp.name}` | {e} |")
            skip += 1

    return ok, skip


def main():
    print(f"[{datetime.now():%H:%M:%S}] Starting rename...")

    # 构建 PoC 查找表
    poc_lookup = build_poc_lookup(POC_DIR)
    print(f"  PoC lookup: {len(poc_lookup)} entries")

    log_entries = []

    # 处理高风险端点
    print("  Processing 高风险端点...")
    h_ok, h_skip = process_directory(HIGH_DIR, "是问题", poc_lookup, log_entries)
    print(f"    {h_ok} OK, {h_skip} skip")

    # 处理中低险端点
    print("  Processing 中低险端点...")
    l_ok, l_skip = process_directory(LOW_DIR, "非问题", poc_lookup, log_entries)
    print(f"    {l_ok} OK, {l_skip} skip")

    total = h_ok + l_ok
    total_skip = h_skip + l_skip

    # 写日志
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # rename_log.md
    log_path = LOG_DIR / "rename_log.md"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("# Rename Log\n\n")
        f.write(f"时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        f.write(f"| 状态 | 旧文件名 | 新文件名 |\n")
        f.write(f"|------|----------|----------|\n")
        for entry in log_entries:
            f.write(f"{entry}\n")
    print(f"  Wrote {log_path}")

    # rename_summary.md
    summary_path = LOG_DIR / "rename_summary.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(
            f"rename done: {total} reports "
            f"({h_ok} 是问题 + {l_ok} 非问题)\n"
        )
    print(f"  Wrote {summary_path}")

    print(
        f"\n[{datetime.now():%H:%M:%S}] "
        f"rename done: {total} reports ({h_ok} 是问题 + {l_ok} 非问题), "
        f"skipped: {total_skip}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
