# -*- coding: utf-8 -*-
"""sensitive_info_collector.py — 硬编码敏感信息采集器。

单一职责：扫描源码中硬编码的敏感信息（密钥/密码/凭证/私钥/IP/邮箱），
输出标准化条目列表。

**不**做的事：
- 不修复/建议修复（仅发现）
- 不调 ast-grep（纯 re 实现）
- 不在输出中泄露完整密钥值（value_preview 最多前 4 字符）
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector

# ============================================================
# 文件扩展名与跳过规则
# ============================================================
_SCAN_EXTENSIONS: tuple[str, ...] = (
    ".java", ".properties", ".yml", ".yaml", ".xml",
)
_TEST_FILE_PATTERN = re.compile(r"@Test\b", re.IGNORECASE)

# ============================================================
# 正则模式库：type -> (pattern, severity, requires_entropy_check)
# ============================================================
_PATTERN_DEFS: list[tuple[str, re.Pattern, str]] = [
    # AWS Access Key ID: AKIA + 16 alphanumeric
    ("aws_credential",
     re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE), "high"),
    # AWS Secret Access Key assignment
    ("aws_credential",
     re.compile(r"(?:aws_secret_access_key|AwsSecretAccessKey)\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?", re.IGNORECASE), "high"),
    # Private key markers
    ("private_key",
     re.compile(r"BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY", re.IGNORECASE), "high"),
    # API / Access / Secret key assignments
    ("api_key",
     re.compile(r"(?:api[_-]?key|access[_-]?key|secret[_-]?key|apikey)\s*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?", re.IGNORECASE), "medium"),
    # Database password / connection strings with password
    ("password",
     re.compile(r"(?:password|passwd|pwd|db[_-]?password)\s*[=:]\s*['\"]?([^\s'\"]{4,})['\"]?", re.IGNORECASE), "medium"),
    # JDBC/连接字符串含密码
    ("password",
     re.compile(r"jdbc:[^;]*;(?:password|pwd)\s*=\s*([^\s;]{4,})", re.IGNORECASE), "medium"),
    # JWT secret
    ("jwt_secret",
     re.compile(r"(?:jwt[_-]?secret|jwt[_-]?key|JWT_SECRET)\s*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?", re.IGNORECASE), "medium"),
    # Internal IPs (private ranges)
    ("internal_ip",
     re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b"), "low"),
    # Internal domain patterns (.internal, .local, .corp, .intranet)
    ("internal_ip",
     re.compile(r"\b[a-zA-Z0-9-]+\.(?:internal|local|corp|intranet|lan|test)\b", re.IGNORECASE), "low"),
    # Email addresses (potential developer info leak)
    ("email",
     re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,6}\b"), "low"),
]


# ============================================================
# 辅助函数
# ============================================================

def _shannon_entropy(text: str) -> float:
    """计算字符串的香农熵（使用 math.log2）。"""
    if not text:
        return 0.0
    freq: dict[str, int] = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(text)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in freq.values()
    )


def _redact(value: str, max_preview: int = 4) -> str:
    """截取前 max_preview 字符 + ****，不泄露完整值。"""
    if len(value) <= max_preview:
        return value + "****"
    return value[:max_preview] + "****"


# ============================================================
# SensitiveInfoCollector 实现
# ============================================================

@register_collector("sensitive_info")
class SensitiveInfoCollector:
    """硬编码敏感信息采集器：纯 re 模式扫描。"""

    name = "sensitive_info_collector"
    asset_type = "sensitive_info"

    def is_available(self, ctx: ExposureContext) -> bool:
        """无需外部工具，始终可用。"""
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """扫描项目源码中的硬编码敏感信息。"""
        items: list[dict[str, Any]] = []
        root = ctx.project_root
        if not root.exists():
            return CollectorResult(
                asset_type=self.asset_type,
                source="regex pattern scan",
                items=items,
                stats={"total": 0},
            )

        for fpath in self._walk(root):
            lines = self._read_file(fpath)
            if lines is None:
                continue
            # 跳过测试文件：Java 文件含 @Test 注解
            if fpath.suffix == ".java" and self._is_test_file(lines):
                continue
            rel = str(fpath.relative_to(root))
            for lineno, line in enumerate(lines, start=1):
                for item in self._scan_line(line, rel, lineno):
                    items.append(item)

        return CollectorResult(
            asset_type=self.asset_type,
            source="regex pattern scan",
            items=items,
            stats={
                "total": len(items),
                "by_type": self._count_by_type(items),
            },
        )

    # ----------------------------------------------------------
    # 文件遍历
    # ----------------------------------------------------------
    @staticmethod
    def _walk(root: Path) -> list[Path]:
        """递归收集所有目标扩展名的文件。"""
        result: list[Path] = []
        for p in root.rglob("*"):
            if p.suffix in _SCAN_EXTENSIONS and p.is_file():
                result.append(p)
        return sorted(result)

    @staticmethod
    def _read_file(fpath: Path) -> list[str] | None:
        """读文件行列表，编码失败返回 None。"""
        try:
            return fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

    @staticmethod
    def _is_test_file(lines: list[str]) -> bool:
        """Java 文件含 @Test 注解则视为测试文件，跳过。"""
        return any(_TEST_FILE_PATTERN.search(ln) for ln in lines)

    # ----------------------------------------------------------
    # 单行扫描
    # ----------------------------------------------------------
    @staticmethod
    def _scan_line(line: str, file: str, lineno: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for type_name, pattern, default_sev in _PATTERN_DEFS:
            for m in pattern.finditer(line):
                # 提取匹配值：优先用捕获组(1)，否则用整体匹配
                value = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                col = m.start() + 1  # 1-based column
                entropy = _shannon_entropy(value)
                # 高熵值提升 severity
                severity = default_sev
                if type_name in ("api_key", "password", "jwt_secret") and entropy >= 3.5:
                    severity = "high"
                results.append({
                    "file": file,
                    "line": lineno,
                    "column": col,
                    "type": type_name,
                    "value_preview": _redact(value),
                    "entropy": round(entropy, 2),
                    "severity": severity,
                })
        return results

    # ----------------------------------------------------------
    # 统计
    # ----------------------------------------------------------
    @staticmethod
    def _count_by_type(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in items:
            t = it.get("type", "")
            counts[t] = counts.get(t, 0) + 1
        return counts
