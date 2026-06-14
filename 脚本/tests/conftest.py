"""
pytest 配置和 fixtures

自动加载模块路径,让测试能 import 到 scanner_utils 等模块
"""
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent.parent

# 添加到 sys.path
sys.path.insert(0, str(ROOT / "脚本"))
sys.path.insert(0, str(ROOT / "脚本" / "ast"))
sys.path.insert(0, str(ROOT / "脚本" / "redis"))
sys.path.insert(0, str(ROOT / "脚本" / "chain"))