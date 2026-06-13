"""Helpers for loading hyphen-named scripts."""
import importlib.util
import os

_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "脚本")


def load_script(subdir, filename):
    """Load a script with hyphen in its name via importlib."""
    full = os.path.join(_SCRIPT_DIR, subdir, filename)
    name = filename.replace("-", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(name, full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
