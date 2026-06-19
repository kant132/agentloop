# -*- coding: utf-8 -*-
"""rule_loader.py — YAML 规则加载 + pattern-either 展开。

单一职责：从 rules/*.yaml 读取规则，提供：
- 分类字典 (class_rules / method_rules / param_rules)
- pattern 展开（pattern-either → 扁平列表）
- ast-grep any: 语法规则文件内容生成
- 注解名 → http_method 映射构建

不依赖其他 route 模块，不依赖 contracts（纯数据变换）。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml


# 三类规则的标准 key
_CATEGORIES: tuple[str, ...] = ("class_rules", "method_rules", "param_rules")


class RuleLoader:
    """加载 YAML 规则文件，展开 pattern-either。"""

    @staticmethod
    def load(rules_dir: Path) -> dict[str, list[dict]]:
        """加载 rules_dir 下所有 .yaml 文件，返回分类规则字典。

        返回结构 ``{class_rules, method_rules, param_rules}``。
        每条 rule 标准化为原样字段 + ``_framework`` 来源标识；规则文件
        按文件名排序加载，保证跨平台稳定。无 pattern/pattern-either/
        annotation 的规则跳过。
        """
        result: dict[str, list[dict]] = {k: [] for k in _CATEGORIES}
        if not rules_dir.exists():
            return result
        for yaml_file in sorted(rules_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            framework = data.get("framework", "unknown")
            for key in _CATEGORIES:
                for rule in data.get(key, []) or []:
                    if not isinstance(rule, dict):
                        continue
                    if not RuleLoader.expand_patterns(rule):
                        continue
                    enriched = dict(rule)
                    enriched["_framework"] = framework
                    result[key].append(enriched)
        return result

    @staticmethod
    def expand_patterns(rule: dict) -> list[str]:
        """展开单条 rule 为 ast-grep pattern 字符串列表。

        - ``pattern`` → 1 个 pattern
        - ``pattern-either`` → N 个 pattern（每个变体各一）
        - 都没有 → 空列表
        兼容 JAX-RS ``annotation`` 字段（无参数注解历史用法）。
        """
        if "pattern" in rule:
            return [str(rule["pattern"])]
        if "pattern-either" in rule and rule["pattern-either"]:
            return [str(v.get("pattern", "")) for v in rule["pattern-either"]]
        if "annotation" in rule:
            return [str(rule["annotation"])]
        return []

    @staticmethod
    def normalize_pattern(rule: dict) -> str:
        """取第一条 pattern（兼容 pattern 和 pattern-either）。

        用于需要单一字符串的场景（如 log）。无 pattern 返回空串。
        """
        if "pattern" in rule:
            return str(rule["pattern"])
        if "pattern-either" in rule:
            variants = rule["pattern-either"] or []
            return variants[0].get("pattern", "") if variants else ""
        return ""

    @staticmethod
    def build_http_method_map(categorized: dict | list[dict]) -> dict[str, str]:
        """从规则构建 注解名 → HTTP 方法 的映射。

        接受 ``load()`` 的 dict（遍历 3 组）或旧式 flat list（向后兼容）。
        pattern 形如 ``@GetMapping($$$)`` → 提取 ``GetMapping``。
        pattern-either 的所有变体都会注册到映射表，后加载的覆盖前者。
        """
        rules: list[dict]
        if isinstance(categorized, dict):
            rules = []
            for key in _CATEGORIES:
                rules.extend(categorized.get(key, []))
        else:
            rules = list(categorized)

        mapping: dict[str, str] = {}
        for r in rules:
            for pattern in RuleLoader.expand_patterns(r):
                m = re.match(r"@(\w+)", pattern)
                if m:
                    mapping[m.group(1)] = r.get("http_method", "ANY")
        return mapping

    @staticmethod
    def build_ast_grep_rule_yaml(categorized: dict | list[dict]) -> str:
        """把多条 pattern 合并为 ast-grep ``any:`` 语法规则文件内容。

        接受 ``load()`` 的 dict 或旧式 flat list。pattern-either 的所有
        变体展开为独立的 ast-grep pattern 行。无 pattern 返回空串。

        生成形如::

            language: java
            rule:
              any:
                - pattern: "@GetMapping($$$)"
                - pattern: "@PostMapping($$$)"
                ...
        """
        rules: list[dict]
        if isinstance(categorized, dict):
            rules = []
            for key in _CATEGORIES:
                rules.extend(categorized.get(key, []))
        else:
            rules = list(categorized)

        pattern_lines: list[str] = []
        for r in rules:
            for pattern in RuleLoader.expand_patterns(r):
                pattern_lines.append(f'    - pattern: "{pattern}"')
        if not pattern_lines:
            return ""
        return (
            "language: java\n"
            "rule:\n"
            "  any:\n"
            + "\n".join(pattern_lines)
            + "\n"
        )
