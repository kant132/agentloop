"""
ast-finder.py

用 ast-grep 查找危险函数调用模式。

用法:
    python ast-finder.py --src /path/to/project --pattern sql_injection

输出:
    JSON 数组
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


# 预定义 ast-grep pattern
PATTERNS = {
    "sql_injection": {
        "lang": "java",
        "pattern": """
            $STM.executeQuery($SQL + $VAR)
        """,
        "description": "SQL 字符串拼接",
    },
    "rce_runtime": {
        "lang": "java",
        "pattern": """
            Runtime.getRuntime().exec($CMD)
        """,
        "description": "Runtime.exec 命令执行",
    },
    "deserialize_objectinput": {
        "lang": "java",
        "pattern": """
            $OIS.readObject()
        """,
        "description": "ObjectInputStream 反序列化",
    },
    "ssrf_url": {
        "lang": "java",
        "pattern": """
            new URL($URL).openConnection()
        """,
        "description": "URL.openConnection SSRF 入口",
    },
    "path_traversal_file": {
        "lang": "java",
        "pattern": """
            new File($PATH, $NAME)
        """,
        "description": "File 路径拼接",
    },
    "xss_response": {
        "lang": "java",
        "pattern": """
            $RESP.getWriter().write($VAR)
        """,
        "description": "response.write 输出未转义",
    },
    "weak_random": {
        "lang": "java",
        "pattern": """
            new java.util.Random()
        """,
        "description": "弱随机数生成（密码/Token 用）",
    },
}


def run_ast_grep(src_path: str, pattern_name: str) -> list:
    if pattern_name not in PATTERNS:
        raise ValueError(f"未知 pattern: {pattern_name}")

    p = PATTERNS[pattern_name]
    # ast-grep 命令
    cmd = [
        "ast-grep",
        "--pattern", p["pattern"].strip(),
        "--lang", p["lang"],
        "--json",
        str(src_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return []
        return json.loads(result.stdout) if result.stdout else []
    except FileNotFoundError:
        print("ERROR: ast-grep 未安装", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("ERROR: ast-grep 超时", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="ast-grep 危险模式扫描")
    parser.add_argument("--src", required=True, help="源码目录")
    parser.add_argument("--pattern", required=True, choices=list(PATTERNS.keys()))
    args = parser.parse_args()

    if not Path(args.src).exists():
        print(f"ERROR: 目录不存在: {args.src}", file=sys.stderr)
        sys.exit(1)

    try:
        results = run_ast_grep(args.src, args.pattern)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
