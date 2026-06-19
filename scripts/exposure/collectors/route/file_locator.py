# -*- coding: utf-8 -*-
"""file_locator.py — 用 ast-grep 快速定位含路由注解的 Java 文件。

只返回文件路径列表，不做参数解析（路径拼接、HTTP method 展开由
javaparser_scanner 负责）。

ast-grep 不可用时降级为 glob 全量扫描 .java 文件。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

# 快速定位 pattern（只匹配注解名，不解析参数；规则名短，扫描快）
# 不复用 RuleLoader 的复杂 any: 规则——这里只需快速过滤候选文件
_LOCATE_PATTERN = """language: java
rule:
  any:
    - pattern: "@RestController"
    - pattern: "@Controller"
    - pattern: "@GetMapping"
    - pattern: "@PostMapping"
    - pattern: "@PutMapping"
    - pattern: "@DeleteMapping"
    - pattern: "@PatchMapping"
    - pattern: "@RequestMapping"
    - pattern: "@Path"
    - pattern: "@GET"
    - pattern: "@POST"
    - pattern: "@PUT"
    - pattern: "@DELETE"
"""


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
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(_LOCATE_PATTERN)
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
