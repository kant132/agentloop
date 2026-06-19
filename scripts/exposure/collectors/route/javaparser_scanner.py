# -*- coding: utf-8 -*-
"""javaparser_scanner.py — javaparser RouteExtractor 封装。

调用 java -jar javaparser.jar --routes 精确解析路由注解：
- 类级基础路径 + 方法级路径拼接（full_url 由 JAR 给出）
- HTTP method 参数解析（含 {GET,POST} 数组展开为列表）
- 全限定名识别（org.springframework...GetMapping）

替代 astgrep_scanner.parse_output 的正则路径解析，避免从 ``@GetMapping("/{id}")``
用正则抽参数的不可靠行为。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# JAR 路径：route/ 文件位于 scripts/exposure/collectors/route/，
# parents[0]=route, [1]=collectors, [2]=exposure, [3]=scripts, [4]=agentloop 根
_JAR_PATH: Path = (
    Path(__file__).resolve().parents[4]
    / "tools"
    / "javaparser-service"
    / "target"
    / "java-method-call-extractor-1.0.0.jar"
)


class JavaparserScanner:
    """调用 javaparser --routes 精确解析路由。"""

    @staticmethod
    def scan(java_file: Path, source_root: Path | None = None) -> list[dict[str, Any]]:
        """对单个 Java 文件执行 --routes 解析。

        返回 RouteExtractor 输出的路由列表，每条含：
        - class_fqn, class_base_path, method_fqn, method_name
        - full_url, http_methods (list), annotation, file, start_line

        JAR 不存在、超时、非零返回码、非法 JSON 均返回空列表（不抛异常）。
        """
        if not _JAR_PATH.exists():
            return []

        cmd: list[str] = ["java", "-jar", str(_JAR_PATH), "--routes", str(java_file)]
        if source_root:
            cmd.append(str(source_root))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return []

        if proc.returncode != 0:
            return []

        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError:
            return []

        return data if isinstance(data, list) else []

    @staticmethod
    def scan_directory(
        java_files: list[Path],
        source_root: Path | None = None,
    ) -> list[dict[str, Any]]:
        """对多个 Java 文件批量解析（逐文件调用，单文件失败不影响整体）。"""
        all_routes: list[dict[str, Any]] = []
        for java_file in java_files:
            routes = JavaparserScanner.scan(java_file, source_root)
            all_routes.extend(routes)
        return all_routes

    @staticmethod
    def jar_path() -> Path:
        """暴露 JAR 路径供 is_available 等检查。"""
        return _JAR_PATH
