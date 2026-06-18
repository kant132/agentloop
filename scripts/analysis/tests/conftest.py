"""pytest 配置：让 analysis 子包及其测试 import 正常工作。

注意：scripts/tests/conftest.py 沿用了废弃的中文路径 "脚本"，
此处独立注入 ``scripts/`` 与 ``scripts/analysis/``，
保证 ``from scripts.analysis.X import Y`` 与 ``from load_counter import LoadCounter``
两种风格都能解析。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # D:\agentloop
SCRIPTS = ROOT / "scripts"
ANALYSIS = SCRIPTS / "analysis"

for p in (str(SCRIPTS), str(ANALYSIS), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
