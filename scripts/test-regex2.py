"""Debug regex 2"""
import re
from pathlib import Path

# Try absolute simplest
text = "- **验证状态**: 暂时无法确认"
print("test line:", text)

# Try multiple regexes
for pat in [
    r"验证状态",
    r"验证状态:",
    r"验证状态：",
    r"验证状态\*\*:",
    r"验证状态\*\*:",
    r"验证状态\*:",
    r"\*验证状态\*",
    r"\*\*验证状态\*\*:",
    r"\*\*验证状态\*\*",
]:
    m = re.search(pat, text)
    print(f"  {pat!r:30}  match={m is not None}")

# Now try on file
print()
f = next(Path(r"D:\code\WebGoat-2025.3\loop_audit\routes\poc").glob("*.md"))
file_text = f.read_text(encoding="utf-8")
for line in file_text.split("\n"):
    if "验证状态" in line:
        print(f"file line: {line!r}")
        for pat in [r"\*\*验证状态\*\*: (.+?)$", r"\*\*验证状态\*\*[:：]\s*(.+?)$"]:
            m = re.search(pat, line)
            print(f"  pat {pat!r:50}  match={m}")
            if m:
                print(f"    group(1)={m.group(1)!r}")
        break
