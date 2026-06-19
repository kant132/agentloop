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
import tempfile
from pathlib import Path
from typing import Any

import yaml

# 兄弟包导入：使用相对导入，避免与 scripts.exposure.contracts 双重导入造成
# isinstance 失败（不同模块路径 = 不同类对象）。
from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


# ============================================================
# 规则加载：从 rules/*.yaml 读取，避免硬编码注解字典
# ============================================================

# 规则目录：collectors/rules/
_RULES_DIR: Path = Path(__file__).parent / "rules"


def _load_rules(rules_dir: Path = _RULES_DIR) -> list[dict]:
    """加载 rules/ 目录下所有 .yaml 文件，返回合并的规则列表。

    每条 rule 标准化为：
        {"pattern": "@GetMapping($$$)", "http_method": "GET", "_framework": "spring"}

    rule 中可用 `pattern` 或 `annotation` 字段（JAX-RS 无参注解历史兼容）。
    规则文件按文件名排序加载，保证跨平台稳定。
    """
    rules: list[dict] = []
    if not rules_dir.exists():
        return rules
    for yaml_file in sorted(rules_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        framework = data.get("framework", "unknown")
        for rule in data.get("rules", []) or []:
            if not isinstance(rule, dict):
                continue
            # 兼容 annotation 字段（JAX-RS @GET 无参数场景）
            pattern = rule.get("pattern") or rule.get("annotation")
            if not pattern:
                continue
            rules.append({
                "pattern": pattern,
                "http_method": rule.get("http_method", "ANY"),
                "_framework": framework,
            })
    return rules


def _build_http_method_map(rules: list[dict]) -> dict[str, str]:
    """从规则列表构建 注解名 → HTTP 方法 的映射。

    pattern 形如 ``@GetMapping($$$)`` → 提取 ``GetMapping``。
    多个框架命中同一注解时，后加载的覆盖前者（保持稳定排序，无歧义）。
    """
    mapping: dict[str, str] = {}
    for r in rules:
        m = re.match(r"@(\w+)", r.get("pattern", ""))
        if m:
            mapping[m.group(1)] = r.get("http_method", "ANY")
    return mapping


def _build_ast_grep_rule_yaml(rules: list[dict]) -> str:
    """把多条 pattern 合并为 ast-grep ``any:`` 语法规则文件内容。

    生成形如::

        language: java
        rule:
          any:
            - pattern: "@GetMapping($$$)"
            - pattern: "@PostMapping($$$)"
            ...

    所有 pattern 合并后单次 ``ast-grep scan`` 调用即可命中全部框架的注解，
    避免 N 次 subprocess 开销。
    """
    pattern_lines = [
        f'    - pattern: "{r["pattern"]}"' for r in rules if r.get("pattern")
    ]
    if not pattern_lines:
        return ""
    return (
        "language: java\n"
        "rule:\n"
        "  any:\n"
        + "\n".join(pattern_lines)
        + "\n"
    )


def _cleanup_rule_file(rule_file: str) -> None:
    """删除临时 ast-grep 规则文件，忽略不存在错误。"""
    try:
        Path(rule_file).unlink()
    except OSError:
        pass


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

        把 ``rules/*.yaml`` 中所有 pattern 合并为单一 ``any:`` 规则文件，
        一次 ``ast-grep scan`` 调用覆盖全部框架（Spring/JAX-RS/Struts2/...）。

        返回原始注解条目列表，每个 dict 含:
            annotation, args, file, line, method_name, source
        """
        src_dir = ctx.project_root
        if not src_dir.exists():
            return []

        rules = _load_rules()
        if not rules:
            return []

        rule_yaml = _build_ast_grep_rule_yaml(rules)
        if not rule_yaml:
            return []
        http_method_map = _build_http_method_map(rules)

        # 写入临时规则文件，scan 结束后清理
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

        return self._parse_ast_grep_json(proc.stdout, http_method_map)

    @staticmethod
    def _parse_ast_grep_json(
        output: str,
        http_method_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """解析 ast-grep scan 的 JSON 输出（any-rule 风格）。

        ast-grep scan 输出的每条 match 含:
            text, file, range.start.line, lines, ...

        ``text`` 形如 ``@GetMapping("/{id}")``，从中抽取注解名与参数列表。
        """
        if http_method_map is None:
            http_method_map = _build_http_method_map(_load_rules())

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
            if annotation_name not in http_method_map:
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

            fqn = RouteCollector._derive_fqn(file_path, entry.get("class_name"))
            results.append({
                "fqn": fqn,
                "annotation": annotation_name,
                "args": args,
                "file": file_path,
                "line": line,
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
        # HTTP 方法：从 rules/*.yaml 推导（不再硬编码）
        http_method_map = _build_http_method_map(_load_rules())
        item["http_method"] = http_method_map.get(
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
