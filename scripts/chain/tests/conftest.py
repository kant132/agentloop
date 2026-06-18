"""
pytest 配置: 让 scripts/chain/tests/ 下的测试能 import 兄弟模块。

- scripts/chain/   — chain_file_writer / sink_registry / priority_calculator / auth_class_cacher
- scripts/redis/   — memurai_client (auth_class_cacher 依赖)
- scripts/ast/     — scanner_utils (链构建辅助)

注: 现有 scripts/tests/conftest.py 路径含中文 "脚本", 本文件独立设置正确路径。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # D:\\agentloop
sys.path.insert(0, str(ROOT / "scripts" / "chain"))
sys.path.insert(0, str(ROOT / "scripts" / "redis"))
sys.path.insert(0, str(ROOT / "scripts" / "ast"))
