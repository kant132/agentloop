#!/usr/bin/env python3
"""sync_project_skills.py — 将项目 skills/ 目录同步到全局 ~/.agents/skills/。

逻辑:
1. 扫描项目 skills/ 下的所有 skill 目录
2. 对每个 skill:
   - 如果 ~/.agents/skills/{name} 已存在（普通目录或软连接），先删除
   - 创建软连接 ~/.agents/skills/{name} → 项目 skills/{name}
3. 输出同步报告

Windows 注意:
- 使用 mklink /D 创建目录软连接（需要管理员权限或开发者模式）
- 如果 mklink 失败，回退到 junction (mklink /J)

Usage:
    python scripts/sync_project_skills.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # D:\agentloop
PROJECT_SKILLS = ROOT / "skills"
AGENTS_SKILLS = Path.home() / ".agents" / "skills"


def _is_symlink(path: Path) -> bool:
    """Check if path is a symlink (not a regular directory)."""
    return path.is_symlink()


def _remove_dir_or_link(path: Path) -> bool:
    """Remove a directory, symlink, or junction. Returns True on success."""
    if not path.exists() and not path.is_symlink():
        return True
    try:
        if path.is_symlink():
            # Remove symlink itself (not target)
            if os.name == "nt":
                # Windows: rmdir removes the link, not the target
                # Must use shell rmdir, not shutil.rmtree (which follows links)
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(path)],
                    check=True, capture_output=True,
                )
            else:
                path.unlink()
        elif os.name == "nt":
            # Windows: might be a junction (not detected as symlink)
            # Use rmdir to remove junction without following it
            subprocess.run(
                ["cmd", "/c", "rmdir", str(path)],
                check=True, capture_output=True,
            )
        else:
            # Regular directory
            shutil.rmtree(path)
        return True
    except Exception as e:
        print(f"  ERROR: 删除 {path} 失败: {e}", file=sys.stderr)
        return False


def _create_symlink(link_path: Path, target_path: Path) -> tuple[bool, str]:
    """Create a directory symlink (or junction on Windows fallback).

    Returns (success, method).
    """
    target = str(target_path)
    link = str(link_path)

    # Try symlink (requires admin or developer mode on Windows)
    try:
        if os.name == "nt":
            # Windows: try mklink /D first (symbolic link)
            result = subprocess.run(
                ["mklink", "/D", link, target],
                capture_output=True, text=True, shell=True,
            )
            if result.returncode == 0:
                return True, "mklink /D"
            # Fallback: junction (doesn't require admin)
            result = subprocess.run(
                ["mklink", "/J", link, target],
                capture_output=True, text=True, shell=True,
            )
            if result.returncode == 0:
                return True, "mklink /J"
            return False, f"mklink failed: {result.stderr.strip()}"
        else:
            # Unix
            os.symlink(target, link)
            return True, "os.symlink"
    except Exception as e:
        return False, str(e)


def sync_skills(dry_run: bool = False) -> int:
    """Sync project skills to global ~/.agents/skills/ via symlinks.

    Returns 0 on success, 1 on error.
    """
    if not PROJECT_SKILLS.is_dir():
        print(f"ERROR: 项目 skills 目录不存在: {PROJECT_SKILLS}", file=sys.stderr)
        return 1

    # Ensure ~/.agents/skills/ exists
    AGENTS_SKILLS.mkdir(parents=True, exist_ok=True)

    # Scan project skills
    project_skill_names = set()
    for item in PROJECT_SKILLS.iterdir():
        if item.is_dir() and not item.name.startswith(".") and not item.name.startswith("_"):
            project_skill_names.add(item.name)

    if not project_skill_names:
        print("项目 skills/ 下没有 skill 目录", file=sys.stderr)
        return 1

    print(f"项目 skills/ 下有 {len(project_skill_names)} 个 skill:")
    for name in sorted(project_skill_names):
        print(f"  - {name}")
    print()

    # Check for SKILL.md in each
    valid_skills = []
    for name in sorted(project_skill_names):
        skill_md = PROJECT_SKILLS / name / "SKILL.md"
        if skill_md.is_file():
            valid_skills.append(name)
        else:
            print(f"  [skip] {name}: 无 SKILL.md")

    print(f"\n有效 skill (含 SKILL.md): {len(valid_skills)} 个")
    print(f"目标: {AGENTS_SKILLS}")
    print()

    if dry_run:
        print("=== DRY RUN (不执行实际操作) ===")
        for name in valid_skills:
            global_path = AGENTS_SKILLS / name
            action = "创建软连接"
            if global_path.exists() or global_path.is_symlink():
                if _is_symlink(global_path):
                    action = f"替换软连接 (当前指向: {os.readlink(global_path) if global_path.is_symlink() else '?'})"
                else:
                    action = "删除副本 + 创建软连接"
            print(f"  {name}: {action}")
            print(f"    {global_path} → {PROJECT_SKILLS / name}")
        return 0

    # Execute
    created = 0
    replaced = 0
    skipped = 0
    errors = 0

    for name in valid_skills:
        target_path = PROJECT_SKILLS / name
        link_path = AGENTS_SKILLS / name

        # Check if already a symlink pointing to the right target
        if link_path.is_symlink():
            current_target = Path(os.readlink(link_path))
            if current_target.resolve() == target_path.resolve():
                print(f"  [skip] {name}: 软连接已存在且指向正确")
                skipped += 1
                continue
            # Remove old symlink
            if _remove_dir_or_link(link_path):
                print(f"  [replace] {name}: 删除旧软连接")
            else:
                errors += 1
                continue
            replaced += 1
        elif link_path.exists():
            # Regular directory (copy) - remove it
            print(f"  [replace] {name}: 删除全局副本 (普通目录)")
            if _remove_dir_or_link(link_path):
                replaced += 1
            else:
                errors += 1
                continue
        else:
            # Doesn't exist yet
            pass

        # Create symlink
        success, method = _create_symlink(link_path, target_path)
        if success:
            print(f"  [ok] {name}: 软连接创建成功 ({method})")
            print(f"       {link_path} → {target_path}")
            created += 1
        else:
            print(f"  [FAIL] {name}: 软连接创建失败: {method}", file=sys.stderr)
            errors += 1

    print(f"\n=== 同步结果 ===")
    print(f"  新建软连接: {created}")
    print(f"  替换: {replaced}")
    print(f"  跳过(已存在): {skipped}")
    print(f"  失败: {errors}")
    print(f"  总计: {created + replaced + skipped + errors}")

    return 0 if errors == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步项目 skills/ 到 ~/.agents/skills/ (软连接)")
    parser.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = parser.parse_args(argv)
    return sync_skills(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
