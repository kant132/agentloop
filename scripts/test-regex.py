"""Debug regex"""
import re
from pathlib import Path

PAT = re.compile(
    r"\xe9\xaa\x8c\xe8\xaf\x81\xe7\x8a\xb6\xe6\x80\x81"
    r"[\xff1a:]"
    r"\s*\*?\*?\s*"
    r"([^\r\n*]+?)\*?\s*$",
    re.MULTILINE,
)

f = next(Path(r"D:\code\WebGoat-2025.3\loop_audit\routes\poc").glob("*.md"))
text = f.read_text(encoding="utf-8")
print("file:", f.name)
print("text length:", len(text))

# Find lines
for i, line in enumerate(text.split("\n")):
    if "验证状态" in line:
        print(f"line {i}: {line!r}")
        # Test regex
        m = PAT.search(line)
        print(f"  regex match: {m}")
        if m:
            print(f"  group(1): {m.group(1)!r}")
        # Try simpler
        m2 = re.search(r"验证状态", line)
        print(f"  simple 验证状态: {m2}")
        # Try escaped
        m3 = re.search(r"验证状态", line)
        print(f"  escaped unicode: {m3}")
        break
