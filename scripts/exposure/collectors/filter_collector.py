# -*- coding: utf-8 -*-
"""filter_collector.py — Filter/Interceptor 扫描器。

单一职责：扫描 Java 项目中的 Filter/Interceptor 类，记录文件路径，
缓存完整文件内容到 Memurai 供后续 AI 分析。

检测目标：
- javax.servlet.Filter / jakarta.servlet.Filter 实现类
- OncePerRequestFilter / GenericFilterBean 子类
- HandlerInterceptor / HandlerInterceptorAdapter 实现类
- @WebFilter 注解类

输出：
- {exposure_dir}/filters.json: filter 清单（file, fqn, type, line）
- Memurai 缓存: {groupId}:filter:{relative_file_path} → 完整文件内容

参考 RFC-0001 §3.2 / AC-1
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


# ============================================================
# 常量：Filter/Interceptor 检测模式
# ============================================================

# ast-grep tree-sitter 模式
_AST_PATTERNS = [
    # Pattern 1: implements Filter / HandlerInterceptor
    '(class_declaration name: (identifier) @class_name (interfaces (type_identifier) @interface_name))',
    # Pattern 2: extends OncePerRequestFilter / GenericFilterBean
    '(class_declaration name: (identifier) @class_name (superclass (type_identifier) @super_name))',
    # Pattern 3: @WebFilter annotation
    '(annotation name: (identifier) @ann_name)',
]

# 关键词 → 类型映射
_KW2TYPE = {
    'Filter': 'servlet_filter',
    'OncePerRequestFilter': 'spring_filter',
    'GenericFilterBean': 'spring_filter',
    'HandlerInterceptor': 'interceptor',
    'HandlerInterceptorAdapter': 'interceptor',
    'WebFilter': 'webfilter',
}

# 框架包前缀（排除非自定义代码）
_FW_PKGS = (
    'javax.', 'jakarta.', 'org.springframework.',
    'org.apache.shiro.', 'org.apache.catalina.',
)

# Regex fallback（ast-grep 不可用时）
_RE_RULES = [
    (re.compile(r'\bimplements\s+\w*Filter\b'), 'servlet_filter'),
    (re.compile(r'\bextends\s+OncePerRequestFilter\b'), 'spring_filter'),
    (re.compile(r'\bextends\s+GenericFilterBean\b'), 'spring_filter'),
    (re.compile(r'\bimplements\s+HandlerInterceptor\b'), 'interceptor'),
    (re.compile(r'\bextends\s+HandlerInterceptorAdapter\b'), 'interceptor'),
    (re.compile(r'@WebFilter\b'), 'webfilter'),
]


# ============================================================
# FilterCollector 实现
# ============================================================

@register_collector('filter')
class FilterCollector:
    """Filter/Interceptor 扫描器：检测 + 记录路径 + 缓存文件内容。

    依据 Collector 协议（contracts.py），实现 name/asset_type/collect/is_available。
    """

    name = 'filter_collector'
    asset_type = 'filter'

    def is_available(self, ctx: ExposureContext) -> bool:
        """无外部依赖，始终可用。"""
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """主采集入口：扫描 Filter/Interceptor，记录路径，缓存文件内容。"""
        root = ctx.project_root
        if not root.exists():
            return CollectorResult(
                asset_type=self.asset_type,
                source='filter scan',
                items=[],
                stats={'total': 0, 'by_type': {}, 'cached': 0},
            )

        # 1. 扫描 Filter/Interceptor 类
        items = self._scan_filters(root)

        # 2. 缓存文件内容到 Memurai
        cached_count = self._cache_files(ctx, items)

        # 3. 统计
        by_type: dict[str, int] = {}
        for it in items:
            t = it.get('type', 'unknown')
            by_type[t] = by_type.get(t, 0) + 1

        return CollectorResult(
            asset_type=self.asset_type,
            source='filter scan (ast-grep + regex)',
            items=items,
            stats={
                'total': len(items),
                'by_type': by_type,
                'cached': cached_count,
            },
        )

    # ----------------------------------------------------------
    # Filter 扫描
    # ----------------------------------------------------------
    def _scan_filters(self, root: Path) -> list[dict[str, Any]]:
        """扫描所有 Java 文件，检测 Filter/Interceptor 类。"""
        items: list[dict[str, Any]] = []

        # 尝试 ast-grep
        if self._has_ast_grep():
            items.extend(self._scan_ast(root))
        else:
            # Fallback to regex
            items.extend(self._scan_regex(root))

        return items

    def _has_ast_grep(self) -> bool:
        """检查 ast-grep 是否可用。"""
        try:
            subprocess.run(
                ['ast-grep', '--version'],
                capture_output=True,
                timeout=5,
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _scan_ast(self, root: Path) -> list[dict[str, Any]]:
        """使用 ast-grep 扫描 Filter/Interceptor。"""
        items: list[dict[str, Any]] = []

        for pattern in _AST_PATTERNS:
            try:
                result = subprocess.run(
                    ['ast-grep', '--pattern', pattern, '--json', str(root)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    continue

                for match in json.loads(result.stdout):
                    item = self._parse_ast_match(match, root)
                    if item:
                        items.append(item)
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                continue

        return items

    def _parse_ast_match(
        self, match: dict[str, Any], root: Path
    ) -> dict[str, Any] | None:
        """解析 ast-grep 匹配结果，提取 filter 信息。"""
        file_path = Path(match.get('file', ''))
        if not file_path.exists():
            return None

        # 提取类名和接口/父类名
        class_name = match.get('class_name', '')
        interface_name = match.get('interface_name', '')
        super_name = match.get('super_name', '')
        ann_name = match.get('ann_name', '')

        # 判断类型
        filter_type = None
        matched_kw = None

        if interface_name in _KW2TYPE:
            filter_type = _KW2TYPE[interface_name]
            matched_kw = interface_name
        elif super_name in _KW2TYPE:
            filter_type = _KW2TYPE[super_name]
            matched_kw = super_name
        elif ann_name in _KW2TYPE:
            filter_type = _KW2TYPE[ann_name]
            matched_kw = ann_name

        if not filter_type:
            return None

        # 排除框架代码
        try:
            rel_path = file_path.relative_to(root)
            text = file_path.read_text(encoding='utf-8', errors='ignore')
            fqn = self._extract_fqn(text, class_name)
        except (ValueError, OSError):
            return None

        if any(fqn.startswith(pkg) for pkg in _FW_PKGS):
            return None

        return {
            'file': str(rel_path),
            'fqn': fqn,
            'type': filter_type,
            'matched': matched_kw,
            'line': match.get('range', {}).get('start', {}).get('line', 0) + 1,
        }

    def _scan_regex(self, root: Path) -> list[dict[str, Any]]:
        """使用 regex 扫描 Filter/Interceptor（ast-grep 不可用时的 fallback）。"""
        items: list[dict[str, Any]] = []

        for java_file in root.rglob('*.java'):
            if self._should_skip(java_file):
                continue

            try:
                text = java_file.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue

            for pattern, filter_type in _RE_RULES:
                match = pattern.search(text)
                if not match:
                    continue

                # 提取类名
                class_match = re.search(
                    r'\b(?:public\s+)?class\s+(\w+)', text
                )
                if not class_match:
                    continue

                class_name = class_match.group(1)
                fqn = self._extract_fqn(text, class_name)

                # 排除框架代码
                if any(fqn.startswith(pkg) for pkg in _FW_PKGS):
                    continue

                try:
                    rel_path = java_file.relative_to(root)
                except ValueError:
                    continue

                items.append({
                    'file': str(rel_path),
                    'fqn': fqn,
                    'type': filter_type,
                    'matched': match.group(0),
                    'line': text[:match.start()].count('\n') + 1,
                })

        return items

    # ----------------------------------------------------------
    # Memurai 缓存
    # ----------------------------------------------------------
    def _cache_files(
        self, ctx: ExposureContext, items: list[dict[str, Any]]
    ) -> int:
        """缓存 filter 文件内容到 Memurai。"""
        if not items:
            return 0

        # 导入 Memurai client
        try:
            from scripts.redis.memurai_client import Memurai
            memurai = Memurai()
        except (ImportError, Exception):
            return 0

        cached = 0
        root = ctx.project_root
        group_id = ctx.group_id

        for item in items:
            file_path = root / item['file']
            if not file_path.exists():
                continue

            try:
                content = file_path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue

            # Memurai key: {groupId}:filter:{relative_file_path}
            key = f"{group_id}:filter:{item['file']}"

            try:
                memurai.set(key, content)
                cached += 1
            except Exception:
                continue

        return cached

    # ----------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------
    @staticmethod
    def _extract_fqn(text: str, class_name: str) -> str:
        """从 Java 源码提取 FQN（package + class_name）。"""
        pkg_match = re.search(r'^\s*package\s+([\w.]+)\s*;', text, re.MULTILINE)
        pkg = pkg_match.group(1) if pkg_match else ''
        return f"{pkg}.{class_name}" if pkg else class_name

    @staticmethod
    def _should_skip(path: Path) -> bool:
        """跳过测试文件和生成代码。"""
        parts = path.parts
        skip_dirs = {'test', 'tests', 'target', 'build', 'generated'}
        return any(part in skip_dirs for part in parts)
