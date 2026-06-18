# -*- coding: utf-8 -*-
"""config_collector.py — 配置文件采集器。

单一职责：扫描 Java 项目配置文件，输出标准化 config 条目。

采集来源：
1. application.yml / application.yaml / application-*.yml
2. application.properties / application-*.properties
3. bootstrap.yml / bootstrap.properties
4. *.xml（spring 配置、web.xml）

敏感值自动检测并脱敏，不写入原始密码/密钥。

参考 RFC-0001 §3.2 / AC-1
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


# ============================================================
# 常量：文件 glob 与敏感模式
# ============================================================

# 配置文件 glob 列表（按类型分组）
_YAML_GLOBS = ["application.yml", "application.yaml", "application-*.yml", "application-*.yaml",
               "bootstrap.yml", "bootstrap.yaml"]
_PROPS_GLOBS = ["application.properties", "application-*.properties",
                "bootstrap.properties"]
_XML_GLOBS = ["*.xml"]

# 敏感值正则模式
_SECRET_PATTERNS = re.compile(
    r"(password|passwd|pwd|secret|key|token|credential|private[_-]?key|access[_-]?key|jdbc:.*password=)",
    re.IGNORECASE,
)

# YAML key-value 行模式（简化解析，不依赖 PyYAML）
_YAML_KV_RE = re.compile(r"^(\s{0,})([a-zA-Z0._-]+)\s*:\s*(.+?)\s*$")
# Properties 行模式
_PROPS_KV_RE = re.compile(r"^([a-zA-Z0._-]+)\s*[=:]\s*(.+?)\s*$")


# ============================================================
# ConfigCollector 实现
# ============================================================

@register_collector("config")
class ConfigCollector:
    """配置文件采集器：扫描 yaml/properties/xml 配置项。

    依据 Collector 协议（contracts.py），实现 name/asset_type/collect/is_available。
    """
    name = "config_collector"
    asset_type = "config"

    def is_available(self, ctx: ExposureContext) -> bool:
        """无外部依赖，始终可用。"""
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """主采集入口：扫描项目根下所有配置文件。"""
        root = ctx.project_root
        if not root.exists():
            return CollectorResult(
                asset_type=self.asset_type, source="config file scan",
                items=[], stats={"total": 0},
            )

        items: list[dict[str, Any]] = []
        # 递归扫描所有配置文件
        items.extend(self._scan_yaml(root))
        items.extend(self._scan_properties(root))
        items.extend(self._scan_xml(root))

        secret_count = sum(1 for it in items if it.get("contains_secret"))
        return CollectorResult(
            asset_type=self.asset_type,
            source="config file scan (yaml/properties/xml)",
            items=items,
            stats={
                "total": len(items),
                "secrets_detected": secret_count,
                "by_source": self._count_by_source(items),
            },
        )

    # ----------------------------------------------------------
    # YAML 解析（简化版，不依赖第三方库）
    # ----------------------------------------------------------
    def _scan_yaml(self, root: Path) -> list[dict[str, Any]]:
        """扫描 YAML 配置文件，解析 key-value 条目。"""
        items: list[dict[str, Any]] = []
        for glob_pat in _YAML_GLOBS:
            for fp in root.rglob(glob_pat):
                # 跳过 node_modules 等无关目录
                if self._should_skip(fp):
                    continue
                items.extend(self._parse_yaml_file(fp))
        return items

    @staticmethod
    def _parse_yaml_file(fp: Path) -> list[dict[str, Any]]:
        """简化 YAML 解析：只提取扁平 key-value 行。

        不处理嵌套结构（多级 key 用点号拼接需 yaml 库，此处仅提取 leaf 行）。
        """
        items: list[dict[str, Any]] = []
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return items

        prefix_parts: list[str] = []  # 当前嵌套层级 key 拼接
        prev_indent = 0

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = _YAML_KV_RE.match(line)
            if not m:
                continue
            indent_str, key, value = m.group(1), m.group(2), m.group(3)
            indent = len(indent_str)

            # 缩进变化时调整层级
            if indent > prev_indent:
                prefix_parts.append(key)
            elif indent < prev_indent and prefix_parts:
                # 弹出层级直到对齐
                depth = indent // 2
                prefix_parts = prefix_parts[:depth]
                prefix_parts.append(key)
            else:
                if prefix_parts:
                    prefix_parts[-1] = key
                else:
                    prefix_parts = [key]

            prev_indent = indent

            # 仅收集 leaf value（非空且非嵌套指示）
            if value and not value.startswith("|") and not value.startswith(">"):
                full_key = ".".join(prefix_parts)
                contains_secret = bool(_SECRET_PATTERNS.search(key) or _SECRET_PATTERNS.search(value))
                display_value = ConfigCollector._redact(value) if contains_secret else value
                items.append({
                    "file": str(fp),
                    "key": full_key,
                    "value": display_value,
                    "line": i,
                    "contains_secret": contains_secret,
                    "source": "yaml",
                })

        return items

    # ----------------------------------------------------------
    # Properties 解析
    # ----------------------------------------------------------
    def _scan_properties(self, root: Path) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for glob_pat in _PROPS_GLOBS:
            for fp in root.rglob(glob_pat):
                if self._should_skip(fp):
                    continue
                items.extend(self._parse_properties_file(fp))
        return items

    @staticmethod
    def _parse_properties_file(fp: Path) -> list[dict[str, Any]]:
        """解析 .properties 文件，提取 key=value 条目。"""
        items: list[dict[str, Any]] = []
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return items

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("!"):
                continue
            m = _PROPS_KV_RE.match(stripped)
            if not m:
                continue
            key, value = m.group(1), m.group(2)
            contains_secret = bool(_SECRET_PATTERNS.search(key) or _SECRET_PATTERNS.search(value))
            display_value = ConfigCollector._redact(value) if contains_secret else value
            items.append({
                "file": str(fp),
                "key": key,
                "value": display_value,
                "line": i,
                "contains_secret": contains_secret,
                "source": "properties",
            })
        return items

    # ----------------------------------------------------------
    # XML 解析（简化版，仅扫描属性值）
    # ----------------------------------------------------------
    def _scan_xml(self, root: Path) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for glob_pat in _XML_GLOBS:
            for fp in root.rglob(glob_pat):
                if self._should_skip(fp):
                    continue
                items.extend(self._parse_xml_file(fp))
        return items

    @staticmethod
    def _parse_xml_file(fp: Path) -> list[dict[str, Any]]:
        """简化 XML 解析：提取属性 name=value 条目。"""
        items: list[dict[str, Any]] = []
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return items

        # 扫描 XML 属性（name="value" 模式）
        attr_re = re.compile(r'([a-zA-Z0._-]+)\s*=\s*"(.*?)"')
        for i, line in enumerate(text.splitlines(), 1):
            for m in attr_re.finditer(line):
                attr_name, attr_val = m.group(1), m.group(2)
                if not attr_val:
                    continue
                # key 用标签路径+属性名（简化：直接用属性名）
                contains_secret = bool(_SECRET_PATTERNS.search(attr_name) or _SECRET_PATTERNS.search(attr_val))
                display_value = ConfigCollector._redact(attr_val) if contains_secret else attr_val
                items.append({
                    "file": str(fp),
                    "key": attr_name,
                    "value": display_value,
                    "line": i,
                    "contains_secret": contains_secret,
                    "source": "xml",
                })
        return items

    # ----------------------------------------------------------
    # 敏感检测与脱敏
    # ----------------------------------------------------------
    @staticmethod
    def detect_secret(value: str) -> bool:
        """检测值是否包含敏感信息模式。"""
        return bool(_SECRET_PATTERNS.search(value))

    @staticmethod
    def _redact(value: str) -> str:
        """脱敏：将敏感值替换为 ****。"""
        return "****"

    # ----------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------
    @staticmethod
    def _should_skip(fp: Path) -> bool:
        """跳过无关目录中的文件。"""
        parts = fp.parts
        skip_dirs = {"node_modules", ".git", "target", "build", "__pycache__"}
        return any(p in skip_dirs for p in parts)

    @staticmethod
    def _count_by_source(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in items:
            s = it.get("source", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return counts
