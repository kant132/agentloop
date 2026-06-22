"""
ast-sanitizer.py

识别代码中的消毒器（sanitizer）。FWD-A 命中 sink 时必查 sanitizer。

用法:
    python ast-sanitizer.py --src /path/to/project

输出:
    JSON 数组 [{"fqn": "...", "sanitizers": ["PreparedStatement", "..."]}]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


# 消毒器模式
SANITIZER_PATTERNS = {
    "PreparedStatement": {
        "lang": "java",
        "indicators": ["prepareStatement", "setString", "setInt", "setLong", "setObject"],
    },
    "ParameterizedQuery": {
        "lang": "java",
        "indicators": ["setParameter", "createQuery.setParameter", "TypedQuery.setParameter"],
    },
    "WhitelistValidation": {
        "lang": "java",
        "indicators": ["Pattern.matches", "matches(\"[a-zA-Z0-9]+\")", "isAlphanumeric"],
    },
    "HTMLEncode": {
        "lang": "java",
        "indicators": ["HtmlUtils.htmlEscape", "StringEscapeUtils.escapeHtml", "Encode.forHtml"],
    },
    "PathCanonicalize": {
        "lang": "java",
        "indicators": ["Path.normalize", "FilenameUtils.getName", "Paths.get(...).normalize"],
    },
    "URLWhitelist": {
        "lang": "java",
        "indicators": ["WHITELIST_DOMAINS.contains", "URLValidator.isValid", "isAllowedHost"],
    },
    "AuthAnnotation": {
        "lang": "java",
        "indicators": ["@PreAuthorize", "@Secured", "@RolesAllowed", "@RequiresPermissions"],
    },
}


def detect_sanitizers_in_method(method_body: str) -> list:
    """在单个方法体中检测消毒器"""
    found = []
    for name, info in SANITIZER_PATTERNS.items():
        for indicator in info["indicators"]:
            if indicator in method_body:
                found.append(name)
                break
    return found


def find_methods_with_sanitizers(src_path: str) -> list:
    """扫描源码目录，找出含消毒器的方法"""
    # 用 ast-grep 找所有方法定义
    cmd = [
        "ast-grep",
        "--pattern", "$$$BODY",
        "--lang", "java",
        "--json",
        str(src_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not result.stdout:
            return []
        matches = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return []

    # 对每个方法体检测消毒器
    results = []
    for m in matches:
        body = m.get("text", "")
        sanitizers = detect_sanitizers_in_method(body)
        if sanitizers:
            results.append({
                "file": m.get("file", ""),
                "range": m.get("range", {}),
                "sanitizers": sanitizers,
            })

    return results


def main():
    parser = argparse.ArgumentParser(description="消毒器识别")
    parser.add_argument("--src", required=True, help="源码目录")
    args = parser.parse_args()

    if not Path(args.src).exists():
        print(f"ERROR: 目录不存在: {args.src}", file=sys.stderr)
        sys.exit(1)

    results = find_methods_with_sanitizers(args.src)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
