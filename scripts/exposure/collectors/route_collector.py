# -*- coding: utf-8 -*-
"""route_collector.py — 路由采集器。

单一职责：从 Java 项目源码采集所有路由端点，输出标准化 route.json。

采集来源（按优先级）：
1. ast-grep 扫描 @RequestMapping/@GetMapping/... 等注解
2. （可选）codegraph nodes.id 反查富化 sig_hash

**不**做的事：
- 不读取配置文件（由 config_collector 负责）
- 不分析鉴权（由 auth_code_collector 负责）
- 不调用 javaparser（保留作为可选富化阶段）

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

# 兄弟包导入：使用相对导入，避免与 scripts.exposure.contracts 双重导入造成
# isinstance 失败（不同模块路径 = 不同类对象）。
from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


# ============================================================
# 常量：路由注解家族与 HTTP 方法映射
# ============================================================

# Spring 系路由注解 → HTTP 方法
_ROUTE_ANNOTATIONS: dict[str, str] = {
    "RequestMapping": "ANY",
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PutMapping".replace("", "") or "PUT",  # 占位防 lint
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}
# 修正（避免诡异的占位写法）
_ROUTE_ANNOTATIONS = {
    "RequestMapping": "ANY",
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

# JAX-RS 系
_JAXRS_ANNOTATIONS: dict[str, str] = {
    "Path": "ANY",
    "GET": "GET",
    "POST": "POST",
    "PUT": "PUT",
    "DELETE": "DELETE",
}

_ALL_ROUTE_ANNOTATIONS: dict[str, str] = {
    **_ROUTE_ANNOTATIONS,
    **_JAXRS_ANNOTATIONS,
}

# ast-grep pattern：匹配方法上的路由注解
_AST_GREP_PATTERN = """
(
  (method_declaration
    (modifiers
      (annotation
        name: (identifier) @annotation_name
        arguments: (annotation_argument_list)? @args
      )
    )
    name: (identifier) @method_name
  ) @method
)
""".strip()


# ============================================================
# RouteCollector 实现
# ============================================================

@register_collector("route")
class RouteCollector:
    """路由采集器：ast-grep 扫描路由注解。

    依据 Collector 协议（contracts.py），实现 name/asset_type/collect/is_available。
    """
    name = "route_collector"
    asset_type = "route"

    # ----------------------------------------------------------
    # 可用性检查
    # ----------------------------------------------------------
    def is_available(self, ctx: ExposureContext) -> bool:
        """需要 ast-grep 在 PATH。"""
        return shutil.which("ast-grep") is not None

    # ----------------------------------------------------------
    # 主采集入口
    # ----------------------------------------------------------
    def collect(self, ctx: ExposureContext) -> CollectorResult:
        raw_routes = self._run_ast_grep(ctx)
        items = [self._enrich(r, ctx) for r in raw_routes]

        degraded_count = sum(1 for it in items if it.get("nodes_id") is None)
        degraded = degraded_count > 0 and ctx.codegraph_db is None

        return CollectorResult(
            asset_type=self.asset_type,
            source="ast-grep annotation scan + codegraph nodes.id 富化",
            items=items,
            stats={
                "total": len(items),
                "degraded_md5": degraded_count if degraded else 0,
                "by_http_method": self._count_by_method(items),
            },
            degraded=degraded,
        )

    # ----------------------------------------------------------
    # ast-grep 子进程封装（可被测试 mock）
    # ----------------------------------------------------------
    def _run_ast_grep(self, ctx: ExposureContext) -> list[dict[str, Any]]:
        """调用 ast-grep 扫描路由注解。

        返回原始注解条目列表，每个 dict 含:
            annotation, args, file, line, method_name, source
        """
        src_dir = ctx.project_root
        if not src_dir.exists():
            return []

        # 走 ast-grep JSON 输出
        cmd = [
            "ast-grep",
            "--lang=java",
            "--json",
            "-p",
            _AST_GREP_PATTERN,
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
            return []

        if proc.returncode != 0:
            return []

        return self._parse_ast_grep_json(proc.stdout)

    @staticmethod
    def _parse_ast_grep_json(output: str) -> list[dict[str, Any]]:
        """解析 ast-grep 的 JSON 输出。"""
        results: list[dict[str, Any]] = []
        try:
            data = json.loads(output) if output.strip() else []
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            data = [data]

        for entry in data:
            annotation_name = entry.get("annotation_name") or ""
            if annotation_name not in _ALL_ROUTE_ANNOTATIONS:
                continue
            file_path = entry.get("file") or entry.get("path") or ""
            # FQN 推导：file 路径 → 包名.类名
            fqn = RouteCollector._derive_fqn(file_path, entry.get("class_name"))
            results.append({
                "fqn": fqn,
                "annotation": annotation_name,
                "args": entry.get("args") or [],
                "file": file_path,
                "line": int(entry.get("line") or entry.get("start_line") or 0),
                "method_name": entry.get("method_name") or "",
                "source": "annotation",
                "class_fqn": entry.get("class_name") or "",
            })
        return results

    @staticmethod
    def _derive_fqn(file_path: str, class_hint: str | None) -> str:
        """从文件路径推导 FQN，回退到 class_hint。"""
        if class_hint:
            return class_hint
        if not file_path:
            return ""
        # 取 src/main/java 之后的路径
        m = re.search(r"(?:src/main/java|src/test/java)[/\\](.+?)\.java$", file_path)
        if m:
            return m.group(1).replace("\\", ".").replace("/", ".")
        # 退化：用文件 stem
        return Path(file_path).stem

    # ----------------------------------------------------------
    # 富化：HTTP 方法、外部参数、nodes_id
    # ----------------------------------------------------------
    def _enrich(self, raw: dict[str, Any], ctx: ExposureContext) -> dict[str, Any]:
        item = dict(raw)
        # HTTP 方法
        item["http_method"] = _ALL_ROUTE_ANNOTATIONS.get(
            raw.get("annotation", ""), "ANY"
        )
        # 外部入参标记（粗判：注解参数或方法上有 @RequestParam/@PathVariable）
        args = raw.get("args") or []
        item["has_external_param"] = bool(args) or raw.get("method_name", "") != ""
        # sig_hash 与 nodes_id（codegraph 可用时）
        nodes_id = self._lookup_nodes_id(raw, ctx)
        item["nodes_id"] = nodes_id
        item["sig_hash"] = nodes_id or self._md5_legacy(raw)
        return item

    @staticmethod
    def _lookup_nodes_id(
        raw: dict[str, Any], ctx: ExposureContext
    ) -> str | None:
        """通过 codegraph SQLite 反查 nodes.id。

        缺失 codegraph_db 时返回 None（降级）。
        """
        if not ctx.codegraph_db or not ctx.codegraph_db.exists():
            return None
        import sqlite3
        fqn = raw.get("fqn") or ""
        method = raw.get("method_name") or ""
        if not fqn or not method:
            return None
        try:
            conn = sqlite3.connect(f"file:{ctx.codegraph_db}?mode=ro", uri=True)
            try:
                cur = conn.execute(
                    "SELECT id FROM nodes WHERE fqn LIKE ? AND type='method' LIMIT 1",
                    (f"%{fqn}%{method}%",),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None
            finally:
                conn.close()
        except sqlite3.Error:
            return None

    @staticmethod
    def _md5_legacy(raw: dict[str, Any]) -> str:
        """回退 hash：md5(file|line|annotation)[:16]。"""
        key = f"{raw.get('file','')}|{raw.get('line',0)}|{raw.get('annotation','')}"
        return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]

    # ----------------------------------------------------------
    # 统计
    # ----------------------------------------------------------
    @staticmethod
    def _count_by_method(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in items:
            m = it.get("http_method", "ANY")
            counts[m] = counts.get(m, 0) + 1
        return counts
