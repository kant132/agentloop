# -*- coding: utf-8 -*-
"""file_locator.py — 用 ast-grep 快速定位含路由注解的 Java 文件。

只返回文件路径列表，不做参数解析（路径拼接、HTTP method 展开由
javaparser_scanner 负责）。

ast-grep 不可用时降级为 glob 全量扫描 .java 文件。

定位 pattern 从 YAML 规则文件动态加载（class_rules + method_rules），
不再硬编码注解名列表。新增框架只需添加 YAML 规则文件即可自动覆盖。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .rule_loader import RuleLoader

# 规则目录：与 route 模块同级
_RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


def _build_locate_pattern() -> str:
    """从 YAML 规则文件动态构建 ast-grep 定位 pattern。

    加载所有 class_rules + method_rules 的 pattern（含 pattern-either 展开），
    生成 ast-grep any: 语法规则文件内容。空规则集返回 fallback 硬编码 pattern。
    """
    categorized = RuleLoader.load(_RULES_DIR)
    yaml_content = RuleLoader.build_ast_grep_rule_yaml(categorized)
    if yaml_content:
        return yaml_content
    # Fallback：无规则文件时使用最小注解集合
    return (
        "language: java\n"
        "rule:\n"
        "  any:\n"
        "    - pattern: '@RestController'\n"
        "    - pattern: '@Controller'\n"
        "    - pattern: '@RequestMapping'\n"
        "    - pattern: '@Path'\n"
    )


class FileLocator:
    """定位含路由注解的 Java 文件。"""

    @staticmethod
    def locate(project_root: Path) -> list[Path]:
        """返回含路由注解的 .java 文件路径列表（去重，按发现顺序）。

        ast-grep 不可用、超时、非零返回码均降级为 glob 全量扫描 .java。
        """
        if not project_root.exists():
            return []
        if shutil.which("ast-grep"):
            files = FileLocator._locate_with_astgrep(project_root)
            if files:
                return files
        # ast-grep 不可用或没命中时降级
        return FileLocator._locate_with_glob(project_root)

    @staticmethod
    def _locate_with_astgrep(project_root: Path) -> list[Path]:
        """用 ast-grep scan 定位文件（只取文件路径，不解析参数）。"""
        locate_pattern = _build_locate_pattern()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(locate_pattern)
            rule_file = tf.name

        try:
            proc = subprocess.run(
                [
                    "ast-grep", "scan",
                    "--rule", rule_file,
                    "--json=compact",
                    str(project_root),
                ],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return []
        finally:
            try:
                Path(rule_file).unlink()
            except OSError:
                pass

        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return []

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            data = [data] if isinstance(data, dict) else []

        files: list[Path] = []
        seen: set[str] = set()
        for entry in data:
            f = entry.get("file") or entry.get("path") or ""
            if f and f not in seen:
                seen.add(f)
                files.append(Path(f))
        return files

    @staticmethod
    def _locate_with_glob(project_root: Path) -> list[Path]:
        """降级：glob 全量扫描 .java 文件（不依赖 ast-grep）。"""
        return list(project_root.rglob("*.java"))
