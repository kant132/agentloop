# -*- coding: utf-8 -*-
"""config_collector.py — 配置文件采集器。

单一职责：扫描 Java 项目配置文件，记录文件路径和元信息到 JSON，
缓存完整文件内容到 Memurai 供后续 AI 分析。

采集来源：
1. application.yml / application.yaml / application-*.yml / application-*.yaml
2. application.properties / application-*.properties
3. bootstrap.yml / bootstrap.yaml / bootstrap.properties
4. *.xml（spring 配置、web.xml）

输出：
- {exposure_dir}/config.json: 配置文件清单（路径、类型、大小、sha256、敏感标记）
- Memurai 缓存: {groupId}:config:{relative_file_path} → 完整文件内容

参考 RFC-0001 §3.2 / AC-1
"""
from __future__ import annotations

import hashlib
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

# 文件扩展名 → 类型标签映射
_EXT_TYPE_MAP: dict[str, str] = {
    ".yml": "yaml",
    ".yaml": "yaml",
    ".properties": "properties",
    ".xml": "xml",
}

# 敏感值正则模式（文件级检测：扫描文件全部内容）
_SECRET_PATTERNS = re.compile(
    r"(password|passwd|pwd|secret|key|token|credential|private[_-]?key|access[_-]?key|jdbc:.*password=)",
    re.IGNORECASE,
)


# ============================================================
# ConfigCollector 实现
# ============================================================

@register_collector("config")
class ConfigCollector:
    """配置文件采集器：扫描配置文件，记录路径和元信息，缓存文件内容。

    依据 Collector 协议（contracts.py），实现 name/asset_type/collect/is_available。
    """
    name = "config_collector"
    asset_type = "config"

    def is_available(self, ctx: ExposureContext) -> bool:
        """无外部依赖，始终可用。"""
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """主采集入口：扫描项目根下所有配置文件，记录路径和元信息，缓存文件内容。"""
        root = ctx.project_root
        if not root.exists():
            return CollectorResult(
                asset_type=self.asset_type, source="config file scan",
                items=[], stats={"total_files": 0, "by_type": {}, "secrets_detected": 0, "cached": 0},
            )

        # 收集所有配置文件路径
        all_files: list[Path] = []
        all_files.extend(self._find_yaml(root))
        all_files.extend(self._find_properties(root))
        all_files.extend(self._find_xml(root))

        items: list[dict[str, Any]] = []
        for fp in all_files:
            item = self._process_file(fp, root)
            if item is not None:
                items.append(item)

        # 缓存文件内容到 Memurai
        cached_count = self._cache_files(ctx, items)

        # 统计
        secret_count = sum(1 for it in items if it.get("contains_secret"))
        by_type: dict[str, int] = {}
        for it in items:
            t = it.get("file_type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        return CollectorResult(
            asset_type=self.asset_type,
            source="config file scan",
            items=items,
            stats={
                "total_files": len(items),
                "by_type": by_type,
                "secrets_detected": secret_count,
                "cached": cached_count,
            },
        )

    # ----------------------------------------------------------
    # 文件发现（保持原有 glob 不变）
    # ----------------------------------------------------------
    def _find_yaml(self, root: Path) -> list[Path]:
        """发现 YAML 配置文件。"""
        found: list[Path] = []
        for glob_pat in _YAML_GLOBS:
            for fp in root.rglob(glob_pat):
                if self._should_skip(fp):
                    continue
                found.append(fp)
        return found

    def _find_properties(self, root: Path) -> list[Path]:
        """发现 Properties 配置文件。"""
        found: list[Path] = []
        for glob_pat in _PROPS_GLOBS:
            for fp in root.rglob(glob_pat):
                if self._should_skip(fp):
                    continue
                found.append(fp)
        return found

    def _find_xml(self, root: Path) -> list[Path]:
        """发现 XML 配置文件。"""
        found: list[Path] = []
        for glob_pat in _XML_GLOBS:
            for fp in root.rglob(glob_pat):
                if self._should_skip(fp):
                    continue
                found.append(fp)
        return found

    # ----------------------------------------------------------
    # 单文件处理：记录路径 + 元信息（不复制文件）
    # ----------------------------------------------------------
    def _process_file(self, fp: Path, root: Path) -> dict[str, Any] | None:
        """处理单个配置文件：记录路径、计算 sha256、检测敏感内容。不复制文件。"""
        try:
            relative = fp.relative_to(root)
        except ValueError:
            return None

        # 确定文件类型
        ext = fp.suffix.lower()
        file_type = _EXT_TYPE_MAP.get(ext, "unknown")

        # 计算 sha256
        sha256 = self._compute_sha256(fp)

        # 文件大小
        try:
            size_bytes = fp.stat().st_size
        except OSError:
            size_bytes = 0

        # 文件级敏感检测
        contains_secret = self._detect_secret_in_file(fp)

        return {
            "file": str(relative),
            "file_type": file_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "contains_secret": contains_secret,
        }

    # ----------------------------------------------------------
    # Memurai 缓存
    # ----------------------------------------------------------
    def _cache_files(
        self, ctx: ExposureContext, items: list[dict[str, Any]]
    ) -> int:
        """缓存配置文件内容到 Memurai。"""
        if not items:
            return 0

        try:
            from scripts.redis.memurai_client import Memurai
            memurai = Memurai()
        except (ImportError, Exception):
            return 0

        cached = 0
        root = ctx.project_root
        group_id = ctx.group_id

        for item in items:
            file_path = root / item["file"]
            if not file_path.exists():
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            key = f"{group_id}:config:{item['file']}"

            try:
                memurai.set(key, content)
                cached += 1
            except Exception:
                continue

        return cached

    # ----------------------------------------------------------
    # 敏感检测（文件级）
    # ----------------------------------------------------------
    @staticmethod
    def detect_secret(value: str) -> bool:
        """检测字符串是否包含敏感信息模式。保持原有接口兼容。"""
        return bool(_SECRET_PATTERNS.search(value))

    @staticmethod
    def _detect_secret_in_file(fp: Path) -> bool:
        """文件级敏感检测：扫描文件全部内容判断是否含敏感信息。"""
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return bool(_SECRET_PATTERNS.search(text))

    # ----------------------------------------------------------
    # sha256 计算
    # ----------------------------------------------------------
    @staticmethod
    def _compute_sha256(fp: Path) -> str:
        """计算文件 sha256 哈希值（hex digest）。"""
        h = hashlib.sha256()
        try:
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    # ----------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------
    @staticmethod
    def _should_skip(fp: Path) -> bool:
        """跳过无关目录中的文件。"""
        parts = fp.parts
        skip_dirs = {"node_modules", ".git", "target", "build", "__pycache__"}
        return any(p in skip_dirs for p in parts)
