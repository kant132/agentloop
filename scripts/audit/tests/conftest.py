"""
pytest 配置: 让 scripts/audit/tests/ 下的测试能 import 兄弟模块。

- scripts/audit/  — cross-agent-50r
- scripts/chain/  — priority_calculator / auth_class_cacher / sink_registry
- scripts/redis/  — memurai_client
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # D:\\agentloop
sys.path.insert(0, str(ROOT / "scripts" / "audit"))
sys.path.insert(0, str(ROOT / "scripts" / "chain"))
sys.path.insert(0, str(ROOT / "scripts" / "redis"))
