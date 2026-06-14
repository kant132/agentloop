"""Shared paths/setup for 脚本/tests/ test suite."""
import os
import sys

# 项目根目录(脚本/tests/ 两级之上)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# 脚本 主目录 + 各子目录加入 sys.path(测试需要 import)
_PATHS_TO_ADD = [
    "脚本",
    "脚本/ast",
    "脚本/redis",
    "脚本/chain",
    "脚本/audit",
    "脚本/tests",
]
for rel in _PATHS_TO_ADD:
    full = os.path.join(ROOT, rel)
    if os.path.isdir(full) and full not in sys.path:
        sys.path.insert(0, full)

# 共享 fixture 目录
FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture_db_path() -> str:
    """返回测试用 codegraph SQLite 路径(如已存在)。"""
    return os.path.join(FIXTURE_DIR, "codegraph-fake.db")