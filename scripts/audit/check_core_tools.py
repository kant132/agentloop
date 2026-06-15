#!/usr/bin/env python3
"""
Startup guard: verify codegraph, ast-grep, memurai are available before daemon starts.

Per requirement #17 from 原子需求拆解.md:
"三个核心工具（codegraph/memurai/ast-grep）任一不可用则退出，不降级"
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

MEMURAI_PATH = Path(r"C:\Program Files\Memurai\memurai-cli.exe")
TIMEOUT_SECS = 5

_INSTALL_HINTS: Dict[str, str] = {
    "codegraph": "npm install -g @colbymchenry/codegraph",
    "ast-grep": "npm install -g ast-grep",
    "memurai":  "Download from https://www.memurai.com/install and run the installer",
}


def _run_version(path: str, timeout: int = TIMEOUT_SECS) -> bool:
    """Run `{path} --version` and return True if a version-like string appears in stdout."""
    if path.endswith(".ps1"):
        cmd = ["powershell", "-NoProfile", "-Command", f"& '{path}' --version"]
    elif path.endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", f'"{path}"', "--version"]
    else:
        cmd = [path, "--version"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return False

    output = (result.stdout or "") + (result.stderr or "")
    return bool(re.search(r"\d+\.\d+", output))


def check_codegraph() -> bool:
    """Check if codegraph is available via which or --version."""
    path = shutil.which("codegraph")
    if path:
        return _run_version(path)
    return False


def check_ast_grep() -> bool:
    """Check if ast-grep is available via which or --version (sg or ast-grep)."""
    for cmd in ("ast-grep", "sg"):
        path = shutil.which(cmd)
        if path:
            return _run_version(path)
    return False


def check_memurai() -> bool:
    """Check if memurai-cli.exe exists and PONGs."""
    if not MEMURAI_PATH.exists():
        return False
    try:
        result = subprocess.run(
            [str(MEMURAI_PATH), "PING"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECS,
        )
        return result.stdout.strip() == "PONG"
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_core_tools(exit_on_missing: bool = True) -> Dict[str, bool]:
    """
    Verify codegraph, ast-grep, memurai are available.

    Returns: {"codegraph": bool, "ast_grep": bool, "memurai": bool, "all_ok": bool}

    If exit_on_missing=True and any tool missing, prints clear error and exits with code 2.
    If False, just returns the dict.
    """
    results = {
        "codegraph": check_codegraph(),
        "ast_grep":  check_ast_grep(),
        "memurai":   check_memurai(),
    }
    results["all_ok"] = all(results.values())

    missing = [k for k, v in results.items() if k != "all_ok" and not v]

    if missing:
        print("=== Core Tool Check FAILED ===")
        for tool in missing:
            hint = _INSTALL_HINTS.get(tool, "Check PATH and installation")
            print(f"  [{tool}] not found or not responding. Install: {hint}")
        print()

    if results["all_ok"]:
        print("=== Core Tool Check OK ===")
        print("  codegraph  OK")
        print("  ast-grep   OK")
        print("  memurai    OK")

    if exit_on_missing and not results["all_ok"]:
        print("Exiting with code 2 (per requirement #17: no degradation).")
        sys.exit(2)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Startup guard: verify core tools (codegraph, ast-grep, memurai)."
    )
    parser.add_argument(
        "--no-exit",
        action="store_true",
        help="Do not exit on missing tools; just print status and exit 0.",
    )
    args = parser.parse_args()

    results = check_core_tools(exit_on_missing=not args.no_exit)

    # --no-exit always exits 0; otherwise exit already happened on failure
    if args.no_exit:
        sys.exit(0 if results["all_ok"] else 1)


if __name__ == "__main__":
    main()