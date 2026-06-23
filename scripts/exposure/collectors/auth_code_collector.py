# -*- coding: utf-8 -*-
"""auth_code_collector.py — 认证鉴权代码采集器。

单一职责：从 Java 源码识别认证鉴权注解代码（@PreAuthorize / @PostAuthorize / @Secured /
@RolesAllowed / SecurityConfig / JWT / Shiro），记录文件路径，缓存完整文件内容到 Memurai。

不做：
- 不检测 Filter/Interceptor（由 filter_collector 负责）
- 不解析注解参数细节

降级路径：ast-grep 不可用时用正则扫描 .java 文件。

输出：
- items: [{fqn, category, matched, file, line, is_custom, filter_order}]
- Memurai 缓存: {groupId}:auth_code:{relative_file_path} → 完整文件内容
- stats: {total, by_category, cached}

参考 RFC-0001 §3.2 / AC-1
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


# ── 关键字 → category 映射（不含 Filter/Interceptor，由 filter_collector 负责）──
_KW2CAT: dict[str, str] = {
    "PreAuthorize": "annotation",
    "PostAuthorize": "annotation",
    "Secured": "annotation",
    "RolesAllowed": "annotation",
    "SecurityFilterChain": "config",
    "WebSecurityConfigurerAdapter": "config",
    "HttpSecurity": "config",
    "EnableWebSecurity": "config",
    "EnableGlobalMethodSecurity": "config",
    "JwtDecoder": "jwt",
    "JwtEncoder": "jwt",
    "NimbusJwtDecoder": "jwt",
    "JwtUtil": "jwt",
    "AuthorizingRealm": "shiro",
    "ShiroFilterFactoryBean": "shiro",
}

# ── 框架包前缀 → 非自定义 ──
_FW_PKGS = (
    "javax.", "jakarta.", "org.springframework.",
    "org.apache.shiro.", "io.jsonwebtoken.", "java.",
)

# ── 正则规则（不含 Filter/Interceptor）──
_RE_RULES: list[tuple[str, str]] = [
    (r"@PreAuthorize\b", "annotation"),
    (r"@PostAuthorize\b", "annotation"),
    (r"@Secured\b", "annotation"),
    (r"@RolesAllowed\b", "annotation"),
    (r"SecurityFilterChain\b", "config"),
    (r"WebSecurityConfigurerAdapter\b", "config"),
    (r"HttpSecurity\b", "config"),
    (r"@EnableWebSecurity\b", "config"),
    (r"@EnableGlobalMethodSecurity\b", "config"),
    (r"JwtDecoder\b|JwtEncoder\b|NimbusJwtDecoder\b", "jwt"),
    (r"AuthorizingRealm\b|ShiroFilterFactoryBean\b", "shiro"),
]

# ── ast-grep 模式 ──
_AST_PATTERNS: list[tuple[str, str]] = [
    ("(annotation name: (identifier) @ann_name)", "ann"),
    ("(class_declaration name: (identifier) @class_name "
     "(superclass (type_identifier) @super_name))", "extends"),
    ("(class_declaration name: (identifier) @class_name "
     "(interfaces (type_identifier) @interface_name))", "impl"),
]

_ORDER_RE = re.compile(r"@Order\s*\(\s*(\d+)\s*\)")
_FQN_RE = re.compile(
    r"(?:src[/\\]main[/\\]java|src[/\\]test[/\\]java)[/\\](.+?)\.java$"
)


@register_collector("auth_code")
class AuthCodeCollector:
    """认证鉴权代码采集器：ast-grep 优先，正则降级，缓存文件内容。"""
    name = "auth_code_collector"
    asset_type = "auth_code"

    def is_available(self, ctx: ExposureContext) -> bool:
        """始终可用：正则降级兜底。"""
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        """主采集入口：扫描认证鉴权代码，记录路径，缓存文件内容。"""
        use_ast = shutil.which("ast-grep") is not None
        items = self._scan_ast(ctx) if use_ast else self._scan_re(ctx)

        # 缓存文件内容到 Memurai（去重：同一文件多个注解只缓存一次）
        cached_count = self._cache_files(ctx, items)

        by_cat: dict[str, int] = {}
        for i in items:
            by_cat[i["category"]] = by_cat.get(i["category"], 0) + 1

        return CollectorResult(
            asset_type=self.asset_type,
            source="ast-grep" if use_ast else "regex",
            items=items,
            stats={"total": len(items), "by_category": by_cat, "cached": cached_count},
            degraded=not use_ast,
        )

    # ── ast-grep 扫描 ──
    def _scan_ast(self, ctx: ExposureContext) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for pattern, ptype in _AST_PATTERNS:
            for e in self._run_sg(ctx, pattern):
                result = self._classify(e, ptype)
                if result:
                    items.append(self._mk(e, result[0], result[1]))
        return items

    def _run_sg(self, ctx: ExposureContext, pattern: str) -> list[dict]:
        cmd = ["ast-grep", "--lang=java", "--json", "-p",
               pattern, str(ctx.project_root)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                encoding="utf-8", errors="replace", timeout=300)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        if proc.returncode != 0:
            return []
        try:
            data = json.loads(proc.stdout.strip() or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else [data]

    def _classify(self, entry: dict, ptype: str) -> tuple[str, str] | None:
        """从 ast-grep 条目判定 (category, matched_keyword)。"""
        meta = entry.get("metaVariables", {}).get("single", {})
        if ptype == "ann":
            val = meta.get("ann_name", {})
            kw = val.get("text", "") if isinstance(val, dict) else str(val)
            cat = _KW2CAT.get(kw, "")
            return (cat, kw) if cat else None
        cls_val = meta.get("class_name", {})
        cls_kw = cls_val.get("text", "") if isinstance(cls_val, dict) else str(cls_val)
        for key in ("super_name", "interface_name"):
            v = meta.get(key, {})
            n = v.get("text", "") if isinstance(v, dict) else str(v)
            if n and _KW2CAT.get(n):
                return (_KW2CAT[n], n)
        return (_KW2CAT[cls_kw], cls_kw) if cls_kw and _KW2CAT.get(cls_kw) else None

    def _mk(self, entry: dict, category: str, matched: str) -> dict[str, Any]:
        meta = entry.get("metaVariables", {}).get("single", {})
        cls_val = meta.get("class_name", {})
        cls_hint = cls_val.get("text") if isinstance(cls_val, dict) else None
        fp = entry.get("file", "")
        rng = entry.get("range", {}).get("start", {})
        line = rng.get("line", 0) + 1
        fqn = self._fqn(fp, cls_hint)
        return {
            "fqn": fqn, "category": category, "matched": matched,
            "file": fp, "line": line,
            "is_custom": self._is_custom(fqn),
            "filter_order": self._extract_order(fp, line),
        }

    # ── 正则降级 ──
    def _scan_re(self, ctx: ExposureContext) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        root = ctx.project_root
        if not root.exists():
            return items
        for jf in root.rglob("*.java"):
            if self._should_skip(jf):
                continue
            try:
                txt = jf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fqn = self._fqn(str(jf), None)
            for rp, cat in _RE_RULES:
                for m in re.finditer(rp, txt):
                    ln = txt[:m.start()].count("\n") + 1
                    kw = m.group(0).replace("@", "").strip()
                    items.append({
                        "fqn": fqn, "category": cat, "matched": kw,
                        "file": str(jf), "line": ln,
                        "is_custom": self._is_custom(fqn),
                        "filter_order": self._extract_order(str(jf), ln),
                    })
        return items

    # ----------------------------------------------------------
    # Memurai 缓存（去重：同一文件只缓存一次）
    # ----------------------------------------------------------
    def _cache_files(
        self, ctx: ExposureContext, items: list[dict[str, Any]]
    ) -> int:
        """缓存认证鉴权文件内容到 Memurai（同一文件多个注解只缓存一次）。"""
        if not items:
            return 0

        # 去重：同一文件路径只缓存一次
        seen_files: set[str] = set()
        unique_items: list[dict[str, Any]] = []
        for item in items:
            f = item.get("file", "")
            if f not in seen_files:
                seen_files.add(f)
                unique_items.append(item)

        try:
            from scripts.redis.memurai_client import Memurai
            memurai = Memurai()
        except (ImportError, Exception):
            return 0

        cached = 0
        root = ctx.project_root
        group_id = ctx.group_id

        for item in unique_items:
            file_path_str = item.get("file", "")
            file_path = Path(file_path_str) if file_path_str else None
            if not file_path or not file_path.exists():
                # 尝试从 project_root 构建路径
                if file_path_str:
                    alt_path = root / file_path_str
                    if alt_path.exists():
                        file_path = alt_path
                    else:
                        continue
                else:
                    continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            # 使用相对路径作为 key（如果 file 是绝对路径则转相对）
            try:
                rel_key = str(file_path.relative_to(root))
            except ValueError:
                rel_key = file_path_str

            key = f"{group_id}:auth_code:{rel_key}"

            try:
                memurai.set(key, content)
                cached += 1
            except Exception:
                import logging; logging.getLogger("auth_code_collector").warning("memurai set failed for %s", key)

        return cached

    # ── 工具方法 ──
    @staticmethod
    def _fqn(fp: str, hint: str | None) -> str:
        if hint:
            return hint
        if not fp:
            return ""
        m = _FQN_RE.search(fp)
        if m:
            return m.group(1).replace("\\", ".").replace("/", ".")
        return Path(fp).stem

    @staticmethod
    def _is_custom(fqn: str) -> bool:
        if not fqn:
            return True
        return not any(fqn.startswith(p) for p in _FW_PKGS)

    @staticmethod
    def _extract_order(fp: str, near: int) -> int | None:
        if not fp or not Path(fp).exists():
            return None
        try:
            lines = Path(fp).read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        lo, hi = max(0, near - 20), min(len(lines), near + 20)
        for i in range(lo, hi):
            m = _ORDER_RE.search(lines[i])
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _should_skip(path: Path) -> bool:
        """跳过无关目录中的文件。"""
        parts = path.parts
        skip_dirs = {"node_modules", ".git", "target", "build", "__pycache__", "generated"}
        return any(p in skip_dirs for p in parts)
