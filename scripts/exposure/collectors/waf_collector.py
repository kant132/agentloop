# -*- coding: utf-8 -*-
"""waf_collector.py — WAF 识别采集器。

单一职责：扫描配置文件与依赖，识别 WAF 是否存在及其类型。

采集来源：
1. nginx.conf（ModSecurity 模块加载）
2. web.xml filter（ModSecurity / OWASP CRS）
3. Spring Security 配置（HttpSecurity / CSRF / CORS）
4. 云 WAF 痕迹（X-CDN / X-WAF-Response 头）
5. pom.xml 依赖（owasp-java-html-sanitizer / jsoup）

不做：
- 不调 ast-grep（仅 re + 文件读取）
- 不分析 Java 业务源码（由 auth_code_collector 负责）
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


# ============================================================
# 模式：每个 (waf_type, file_name 规则, content regex)
# ============================================================

_NGINX_MODSEC = re.compile(
    r"(?:modsecurity|ModSecurity|mod_security|\bSecRuleEngine\b|\bSecRule\b)",
)
_WEBXML_MODSEC = re.compile(
    r"(?:ModSecurity|OWASP\s*CRS|mod_security)",
    re.IGNORECASE,
)
_SPRING_SEC_MARKER = re.compile(
    r"(?:@EnableWebSecurity|@EnableGlobalMethodSecurity|"
    r"SecurityFilterChain|WebSecurityConfigurerAdapter)"
)
_SPRING_HTTP_SEC = re.compile(r"\bHttpSecurity\b")
_CLOUD_WAF_HEADER = re.compile(
    r"X-(?:CDN|WAF-Response|WAF-Trace|CDN-Cache|Alibaba|Tencent-Src)",
)
_POM_DEPS = re.compile(
    r"(?:owasp-java-html-sanitizer|antisamy|owasp-encodings|\bjsoup\b)",
)

# 文件名 → (waf_type, regex) 列表
_FILE_MATCHERS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "nginx.conf": [("nginx_modsec", _NGINX_MODSEC)],
    "web.xml": [("nginx_modsec", _WEBXML_MODSEC)],
    "pom.xml": [("library", _POM_DEPS)],
    "application.yml": [("cloud", _CLOUD_WAF_HEADER)],
    "application.yaml": [("cloud", _CLOUD_WAF_HEADER)],
    "application.properties": [("cloud", _CLOUD_WAF_HEADER)],
}


@register_collector("waf")
class WafCollector:
    """WAF 识别采集器：仅依赖文件系统，永远可用。"""
    name = "waf_collector"
    asset_type = "waf"

    def is_available(self, ctx: ExposureContext) -> bool:
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        evidence: list[dict[str, Any]] = []
        waf_types: set[str] = set()

        for f in self._iter_files(ctx.project_root):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for waf_type, regex in self._matchers_for(f.name, text):
                hit_line = self._first_match_line(text, regex)
                if hit_line is None:
                    continue
                waf_types.add(waf_type)
                evidence.append({
                    "type": waf_type,
                    "file": str(f),
                    "line": hit_line[0],
                    "snippet": hit_line[1],
                })

        if not waf_types:
            waf_types.add("unknown")
        primary = sorted(waf_types)[0]
        presence = primary != "unknown"

        return CollectorResult(
            asset_type=self.asset_type,
            source="file scan (nginx.conf/web.xml/Spring-Security/cloud-header/pom.xml)",
            items=[{
                "waf_type": primary,
                "presence": presence,
                "all_types": sorted(waf_types),
                "evidence": evidence,
            }],
            stats={
                "total": 1,
                "waf_present": int(presence),
                "by_type": {t: 1 for t in sorted(waf_types)},
                "evidence_count": len(evidence),
            },
            degraded=False,
        )

    # ----------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------
    @staticmethod
    def _iter_files(root: Path) -> Iterator[Path]:
        if not root.exists():
            return
        for f in root.rglob("*"):
            if f.is_file():
                yield f

    @staticmethod
    def _matchers_for(name: str, content: str) -> list[tuple[str, re.Pattern[str]]]:
        """根据文件名 + 内容返回适用的 (waf_type, regex) 列表。"""
        matchers: list[tuple[str, re.Pattern[str]]] = []
        if name in _FILE_MATCHERS:
            matchers.extend(_FILE_MATCHERS[name])
        # .java 文件：必须同时含 @EnableWebSecurity/SecurityFilterChain 与 HttpSecurity
        if name.endswith(".java") and _SPRING_SEC_MARKER.search(content):
            matchers.append(("spring_security", _SPRING_HTTP_SEC))
        return matchers

    @staticmethod
    def _first_match_line(text: str, regex: re.Pattern[str]) -> tuple[int, str] | None:
        for ln, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                return ln, line.strip()[:200]
        return None
