# -*- coding: utf-8 -*-
"""waf_collector.py — WAF 相关文件采集器。

单一职责：扫描项目中的安全配置文件，记录文件路径，
缓存完整文件内容到 Memurai 供后续 AI 分析。

不做：
- 不提取证据片段（snippet）
- 不做 WAF 类型分类汇总
- 不分析 Java 业务源码（由 auth_code_collector 负责）

采集来源：
1. nginx.conf
2. web.xml
3. Spring Security 配置类（含 @EnableWebSecurity / SecurityFilterChain 等）
4. application.yml / application.yaml / application.properties（含安全配置）
5. pom.xml（含安全相关依赖）

输出：
- items: [{file, type, size_bytes, sha256}]
- Memurai 缓存: {groupId}:waf:{relative_file_path} → 完整文件内容
- stats: {total_files, by_type, cached}

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
# 常量：WAF 相关文件检测模式
# ============================================================

# 安全注解标记（用于 .java 文件分类）
_SPRING_SEC_MARKER = re.compile(
    r"(?:@EnableWebSecurity|@EnableGlobalMethodSecurity|"
    r"SecurityFilterChain|WebSecurityConfigurerAdapter)"
)

# 安全依赖标记（用于 pom.xml 分类）
_POM_SEC_DEPS = re.compile(
    r"(?:spring-security|owasp-java-html-sanitizer|antisamy|owasp-encodings|\bjsoup\b|modsecurity)",
    re.IGNORECASE,
)

# WAF 头标记（用于 application.yml/properties 分类）
_WAF_HEADER = re.compile(
    r"X-(?:CDN|WAF-Response|WAF-Trace|CDN-Cache|Alibaba|Tencent-Src)",
)

# ModSecurity 标记（用于 nginx.conf/web.xml 分类）
_MODSEC_MARKER = re.compile(
    r"(?:modsecurity|ModSecurity|mod_security|\bSecRuleEngine\b|\bSecRule\b)",
    re.IGNORECASE,
)

# 文件名 → 默认类型
_FILENAME_TYPE: dict[str, str] = {
    "nginx.conf": "nginx",
    "web.xml": "web_xml",
}


@register_collector("waf")
class WafCollector:
    """WAF 相关文件采集器：扫描安全配置文件 → 记录路径 → 缓存文件内容。

    依据 Collector 协议（contracts.py），实现 name/asset_type/collect/is_available。
    """
    name = "waf_collector"
    asset_type = "waf"

    def is_available(self, ctx: ExposureContext) -> bool:
        """无外部依赖，始终可用。"""
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """主采集入口：扫描安全配置文件，记录路径，缓存文件内容。"""
        root = ctx.project_root
        if not root.exists():
            return CollectorResult(
                asset_type=self.asset_type,
                source="file scan (nginx.conf/web.xml/Spring-Security/pom.xml)",
                items=[],
                stats={"total_files": 0, "by_type": {}, "cached": 0},
            )

        # 1. 扫描 WAF 相关文件
        items = self._scan_files(root)

        # 2. 缓存文件内容到 Memurai
        cached_count = self._cache_files(ctx, items)

        # 3. 统计
        by_type: dict[str, int] = {}
        for it in items:
            t = it.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        return CollectorResult(
            asset_type=self.asset_type,
            source="file scan (nginx.conf/web.xml/Spring-Security/pom.xml)",
            items=items,
            stats={
                "total_files": len(items),
                "by_type": by_type,
                "cached": cached_count,
            },
        )

    # ----------------------------------------------------------
    # 文件扫描
    # ----------------------------------------------------------
    def _scan_files(self, root: Path) -> list[dict[str, Any]]:
        """扫描所有 WAF 相关文件，记录路径和类型。"""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 1. nginx.conf / web.xml
        for f in root.rglob("*"):
            if not f.is_file() or self._should_skip(f):
                continue
            fname = f.name
            if fname in _FILENAME_TYPE:
                item = self._make_item(f, root, _FILENAME_TYPE[fname])
                if item and item["file"] not in seen:
                    seen.add(item["file"])
                    items.append(item)

        # 2. Spring Security 配置类
        for f in root.rglob("*.java"):
            if self._should_skip(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if _SPRING_SEC_MARKER.search(text):
                item = self._make_item(f, root, "spring_security")
                if item and item["file"] not in seen:
                    seen.add(item["file"])
                    items.append(item)

        # 3. pom.xml（含安全依赖）
        for f in root.rglob("pom.xml"):
            if self._should_skip(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if _POM_SEC_DEPS.search(text):
                item = self._make_item(f, root, "pom_security_dep")
                if item and item["file"] not in seen:
                    seen.add(item["file"])
                    items.append(item)

        # 4. application.yml / yaml / properties（含 WAF 头或安全配置）
        for f in root.rglob("*.yml"):
            if self._should_skip(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if _WAF_HEADER.search(text) or _SPRING_SEC_MARKER.search(text):
                item = self._make_item(f, root, "yaml_security")
                if item and item["file"] not in seen:
                    seen.add(item["file"])
                    items.append(item)

        for f in root.rglob("*.yaml"):
            if self._should_skip(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if _WAF_HEADER.search(text) or _SPRING_SEC_MARKER.search(text):
                item = self._make_item(f, root, "yaml_security")
                if item and item["file"] not in seen:
                    seen.add(item["file"])
                    items.append(item)

        for f in root.rglob("*.properties"):
            if self._should_skip(f):
                continue
            if not f.name.lower().startswith("application"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if _WAF_HEADER.search(text) or _SPRING_SEC_MARKER.search(text):
                item = self._make_item(f, root, "properties_security")
                if item and item["file"] not in seen:
                    seen.add(item["file"])
                    items.append(item)

        return items

    def _make_item(self, f: Path, root: Path, type_tag: str) -> dict[str, Any] | None:
        """构造单个 item：记录相对路径、类型、大小、sha256。"""
        try:
            relative = f.relative_to(root)
        except ValueError:
            return None

        sha256 = self._compute_sha256(f)
        try:
            size_bytes = f.stat().st_size
        except OSError:
            size_bytes = 0

        return {
            "file": str(relative),
            "type": type_tag,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }

    # ----------------------------------------------------------
    # Memurai 缓存
    # ----------------------------------------------------------
    def _cache_files(
        self, ctx: ExposureContext, items: list[dict[str, Any]]
    ) -> int:
        """缓存 WAF 相关文件内容到 Memurai。"""
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

            key = f"{group_id}:waf:{item['file']}"

            try:
                memurai.set(key, content)
                cached += 1
            except Exception:
                continue

        return cached

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
    def _should_skip(path: Path) -> bool:
        """跳过无关目录中的文件。"""
        parts = path.parts
        skip_dirs = {"node_modules", ".git", "target", "build", "__pycache__", "test", "tests"}
        return any(p in skip_dirs for p in parts)
