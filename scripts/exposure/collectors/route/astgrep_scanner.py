# -*- coding: utf-8 -*-
"""astgrep_scanner.py — ast-grep 子进程封装 + 输出解析。

单一职责：执行 ast-grep scan，把 JSON 输出转为标准化注解条目列表。
不做规则加载（由 RuleLoader 负责）；不做富化（由 RouteEnricher 负责）。

返回的每条 dict 含 annotation/args/file/line/method_name/source/class_fqn
字段；fqn 字段由 RouteEnricher 在富化时推导（缺失时）。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ...contracts import ExposureContext


def _cleanup_rule_file(rule_file: str) -> None:
    """删除临时 ast-grep 规则文件，忽略不存在错误。"""
    try:
        Path(rule_file).unlink()
    except OSError:
        pass


class AstGrepScanner:
    """调用 ast-grep 扫描路由注解。"""

    @staticmethod
    def scan(
        ctx: ExposureContext,
        rule_yaml: str,
        http_method_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """执行 ast-grep scan，返回原始注解条目列表。

        把 ``rule_yaml`` 写入临时规则文件，调用 ``ast-grep scan --rule``，
        解析 stdout 为条目列表。``http_method_map`` 提供时仅保留映射表
        中的注解（路由过滤）；不提供时不过滤。

        缺失项目目录、空规则、超时、非零返回码均返回空列表（不抛异常）。
        """
        src_dir = ctx.project_root
        if not src_dir.exists() or not rule_yaml:
            return []

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(rule_yaml)
            rule_file = tf.name

        cmd = [
            "ast-grep",
            "scan",
            "--rule", rule_file,
            "--json=compact",
            str(src_dir),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            _cleanup_rule_file(rule_file)
            return []
        finally:
            _cleanup_rule_file(rule_file)

        if proc.returncode != 0:
            return []

        return AstGrepScanner.parse_output(proc.stdout, http_method_map)

    @staticmethod
    def parse_output(
        output: str,
        http_method_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """解析 ast-grep scan 的 JSON 输出（any-rule 风格）。

        ast-grep scan 输出的每条 match 含:
            text, file, range.start.line, lines, ...

        ``text`` 形如 ``@GetMapping("/{id}")``，从中抽取注解名与参数列表。
        ``http_method_map`` 提供时仅保留映射表中的注解名（旧风格兼容
        ``annotation_name`` 字段）。
        """
        results: list[dict[str, Any]] = []
        try:
            data = json.loads(output) if output.strip() else []
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            data = [data]

        for entry in data:
            text = entry.get("text") or entry.get("lines") or ""
            # 兼容旧 -p 风格输出（仍带 annotation_name 字段）
            annotation_name = entry.get("annotation_name") or ""
            if not annotation_name:
                m = re.match(r"@(\w+)", text)
                if not m:
                    continue
                annotation_name = m.group(1)
            if http_method_map is not None and annotation_name not in http_method_map:
                continue

            file_path = entry.get("file") or entry.get("path") or ""
            # ast-grep scan 的 range.start.line 是 0-indexed；旧 -p 风格用 line/start_line
            range_info = entry.get("range") or {}
            start = range_info.get("start") or range_info.get("startpoint") or {}
            line_raw = (
                start.get("line")
                if start.get("line") is not None
                else start.get("row")
            )
            if line_raw is not None:
                line = int(line_raw) + 1
            else:
                line = int(entry.get("line") or entry.get("start_line") or 0)

            # 参数列表：优先用旧风格的 args 字段，否则从 text 抽取括号内容
            args = entry.get("args")
            if args is None:
                args_match = re.search(r"\((.*)\)", text, re.DOTALL)
                if args_match:
                    inner = args_match.group(1).strip()
                    args = [inner] if inner else []
                else:
                    args = []

            results.append({
                "annotation": annotation_name,
                "args": args,
                "file": file_path,
                "line": line,
                "method_name": entry.get("method_name") or "",
                "source": "annotation",
                "class_fqn": entry.get("class_name") or "",
            })
        return results
