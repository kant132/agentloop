"""Helpers for loading hyphen-named scripts under 脚本/."""
import importlib.util
import os

# 从 脚本/tests/ 上一级就是 脚本/
_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..")


def load_script(subdir: str, filename: str):
    """Load a script with hyphen in its name via importlib.
    
    Example:
        mod = load_script("audit", "poc-monitor.py")
        mod.main(...)
    """
    full = os.path.join(_SCRIPT_DIR, subdir, filename)
    name = filename.replace("-", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(name, full)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load: {full}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod