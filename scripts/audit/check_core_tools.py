#!/usr/bin/env python3
"""
Startup guard: verify jar-analyzer, ast-grep, memurai are available before daemon starts.

Per requirement AR-01 from 原子需求-v2.md:
"三个核心工具（jar-analyzer/ast-grep/Memurai）任一不可用则退出，不降级"
codegraph is optional (Phase 4 PoC only).

Phase 0 还包括：把项目 skills 目录链接到 opencode 的 user skills 目录。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

MEMURAI_PATH = Path(r"C:\Program Files\Memurai\memurai-cli.exe")
TIMEOUT_SECS = 5

# 项目 skills 目录（agentloop/skills/）
_PROJECT_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
# opencode user skills 目录
_USER_SKILLS_DIR = Path(os.path.expanduser("~")) / ".agents" / "skills"

_INSTALL_HINTS: Dict[str, str] = {
    "jar_analyzer": "Ensure tools/javaparser/jar-analyzer-5.22.jar exists and Java runtime is available",
    "ast_grep": "npm install -g ast-grep",
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


# jar-analyzer 路径（核心工具，Phase 0-3 硬依赖）
JAR_ANALYZER_PATH = Path(r"D:\agentloop\tools\javaparser\jar-analyzer-5.22.jar")


def check_jar_analyzer() -> bool:
    """Check if jar-analyzer JAR exists and Java runtime is available.
    
    Core tool — Phase 0-3 hard dependency. Pipeline exits if missing.
    """
    if not JAR_ANALYZER_PATH.exists():
        return False
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECS,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_codegraph() -> bool:
    """Check if codegraph is available (Phase 4 PoC only, not a hard dependency)."""
    path = shutil.which("codegraph")
    if path:
        return _run_version(path)
    return False


def check_skills_linked() -> bool:
    """检测项目 skills 目录是否已链接到 opencode user skills 目录。
    
    如果项目有 skills/ 目录但未链接到 ~/.agents/skills/，
    创建软链接并返回 False（需要用户重启 session）。
    如果已链接或不需要链接，返回 True。
    """
    if not _PROJECT_SKILLS_DIR.is_dir():
        return True  # 项目没有 skills 目录，跳过

    _USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    need_restart = False
    for skill_dir in _PROJECT_SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        link = _USER_SKILLS_DIR / skill_dir.name
        if link.exists():
            continue  # 已存在，跳过
        # 创建 junction（Windows）或 symlink
        try:
            if os.name == "nt":
                import subprocess as _sp
                _sp.run(["cmd", "/c", "mklink", "/J", str(link), str(skill_dir)],
                         capture_output=True, check=True)
            else:
                os.symlink(str(skill_dir), str(link), target_is_directory=True)
            print(f"  [skill] 已链接: {skill_dir.name}")
            need_restart = True
        except Exception:
            pass

    if need_restart:
        print("\n=== Skills 链接已创建 ===")
        print("  请重新打开 opencode session 以加载新链接的 skills。")
        print("  退出码 2（强制停止）。")
        sys.exit(2)

    return True


def check_core_tools(exit_on_missing: bool = True) -> Dict[str, bool]:
    """
    Verify jar-analyzer, ast-grep, memurai are available + skills linked.

    Returns: {"jar_analyzer": bool, "ast_grep": bool, "memurai": bool, "skills": bool, "codegraph": bool, "all_ok": bool}

    Core tools (jar-analyzer, ast-grep, memurai, skills) — hard dependency, exit 2 if missing.
    codegraph — optional (Phase 4 PoC only), reported but not blocking.

    If exit_on_missing=True and any core check fails, prints clear error and exits with code 2.
    If False, just returns the dict.
    """
    results = {
        "jar_analyzer": check_jar_analyzer(),
        "ast_grep":     check_ast_grep(),
        "memurai":      check_memurai(),
        "skills":       check_skills_linked(),
    }
    results["all_ok"] = all(results.values())

    # codegraph: optional tool (Phase 4 PoC only), not part of hard dependency check
    results["codegraph"] = check_codegraph()

    missing = [k for k, v in results.items() if k not in ("all_ok", "codegraph") and not v]

    if missing:
        print("=== Core Tool Check FAILED ===")
        for tool in missing:
            hint = _INSTALL_HINTS.get(tool, "Check PATH and installation")
            print(f"  [{tool}] not found or not responding. Install: {hint}")
        print()

    if results["all_ok"]:
        print("=== Core Tool Check OK ===")
        print(f"  jar-analyzer  OK")
        print("  ast-grep      OK")
        print("  memurai       OK")
        print("  skills        OK")
        print(f"  codegraph     {'OK' if results['codegraph'] else 'SKIP (optional, Phase 4 PoC only)'}")

    if exit_on_missing and not results["all_ok"]:
        print("Exiting with code 2 (per AR-01: no degradation).")
        sys.exit(2)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Startup guard: verify core tools (jar-analyzer, ast-grep, memurai)."
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