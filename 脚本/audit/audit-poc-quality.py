"""audit-poc-quality.py - scan PoC files for filename vs content contradiction.
Uses chr() to avoid GBK/UTF-8 heredoc encoding issues on Windows.
"""
import re
from pathlib import Path
from collections import Counter

# Build patterns using chr() to avoid Windows console encoding
CN_VERIFY_STATUS = chr(0x9A8C) + chr(0x8BC1) + chr(0x72B6) + chr(0x6001)  # 验证状态
CN_COLON_FULL = chr(0xFF1A)  # ：
CN_FAKE_ROOT = "缺乏充分的输入验证和输出编码"
CN_FAILED = "失败"
CN_ISSUE = "是问题"
CN_NOT_ISSUE = "非问题"
CN_UNCERTAIN = "暂时无法确认"

# Build regex pattern using string concatenation
# Format: **验证状态**: <content>
PATTERN = (
    r"\*\*"  # opening **
    + re.escape(CN_VERIFY_STATUS)  # 验证状态
    + r"\*\*"  # closing **
    + r"\s*[" + re.escape(CN_COLON_FULL) + r":]"  # colon
    + r"\s*(.+?)$"
)
PAT_STATUS_LINE = re.compile(PATTERN, re.MULTILINE)
print(f"PATTERN = {PATTERN!r}")

POC_DIR = Path(r"D:\code\WebGoat-2025.3\loop_audit\routes\poc")


def main():
    files = list(POC_DIR.glob("*.md"))
    print(f"\n[+] Total PoC files: {len(files)}")

    statuses = Counter()
    filename_prefixes = Counter()
    contradicoes = []
    fake_root = 0
    get_only = 0

    for f in files:
        name = f.stem
        if name.startswith(CN_ISSUE + "_") or name.startswith("成功_"):
            expected = CN_ISSUE
            filename_prefixes[CN_ISSUE] += 1
        elif name.startswith(CN_NOT_ISSUE + "_"):
            expected = CN_NOT_ISSUE
            filename_prefixes[CN_NOT_ISSUE] += 1
        elif name.startswith(CN_UNCERTAIN + "_") or name.startswith(CN_FAILED + "_") or name.startswith("failed_"):
            expected = CN_UNCERTAIN
            filename_prefixes[CN_UNCERTAIN] += 1
        else:
            expected = None
            filename_prefixes["其他"] += 1

        text = f.read_text(encoding="utf-8")
        m = PAT_STATUS_LINE.search(text)
        actual = m.group(1).strip() if m else "未填"
        statuses[actual] += 1

        if expected and actual != "未填" and actual != expected:
            contradicoes.append((f.name, expected, actual))

        if CN_FAKE_ROOT in text:
            fake_root += 1
        if "curl -X GET" in text:
            get_only += 1

    n = len(files)
    print("\n=== filename prefixes ===")
    for k, v in filename_prefixes.most_common():
        print(f"  {k}: {v}")
    print("\n=== content status (验证状态行) ===")
    for k, v in statuses.most_common():
        print(f"  {k}: {v}")
    print("\n=== quality ===")
    print(f"  filename vs content CONTRADICTION: {len(contradicoes)}/{n} = {len(contradicoes)/n*100:.1f}%")
    print(f"  fake root cause (template text): {fake_root}/{n} = {fake_root/n*100:.1f}%")
    print(f"  GET-only PoC: {get_only}/{n} = {get_only/n*100:.1f}%")
    print("\n=== first 15 contradictions ===")
    for name, exp, act in contradicoes[:15]:
        print(f"  {name[:80]}")
        print(f"    filename_says={exp}  but content_says={act}")
    print("\n=== first 3 files 验证状态 line ===")
    for f in files[:3]:
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            if CN_VERIFY_STATUS in line:
                print(f"  {f.name}: {line.strip()[:120]}")
                break


if __name__ == "__main__":
    main()
