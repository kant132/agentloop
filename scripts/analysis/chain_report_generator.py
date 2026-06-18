"""chain_report_generator.py — 单条调用链的详尽报告生成器。

职责：
- 将 chain 结构 + analysis_results + findings 装配为可序列化的 dict
- 为每个 finding 估算 CVSS 4.0 简化分数
- 提供 ``to_markdown`` 渲染为人类可读的中文报告

**范围合规**：本模块只输出「漏洞位置 / 调用链 / 根因 / PoC 构造 / payload /
严重性」，**不输出**任何修复建议或加固代码（符合 AGENTS.md 的 IN/OUT scope）。

CVSS 4.0 简化评分基于以下宏观维度（取值 0~1）：
- AV（Attack Vector）：N=0.85, A=0.62, L=0.55, P=0.20
- AC（Attack Complexity）：L=0.77, H=0.44
- PR（Privileges Required）：N=0.85, L=0.62, H=0.27
- Impact（综合 C/I/A）：H=0.56, L=0.22, N=0.0

公式：``base = round_up(min(10, impact*10 + exploitability*10))``，
其中 ``exploitability = AV × AC × PR``。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Mapping, Optional

# ------------------------------------------------------------------ CVSS 权重表

_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}
_AC = {"L": 0.77, "H": 0.44}
_PR = {"N": 0.85, "L": 0.62, "H": 0.27}
_IMPACT = {"H": 0.56, "L": 0.22, "N": 0.0}

# 严重性档位阈值（CVSS 4.0 标准划分）
SEVERITY_NONE = 0.0
SEVERITY_LOW = 0.1
SEVERITY_MEDIUM = 4.0
SEVERITY_HIGH = 7.0
SEVERITY_CRITICAL = 9.0


# ------------------------------------------------------------------ 数据结构

@dataclass
class Finding:
    """漏洞发现的结构化记录（不含修复建议）。"""
    vuln_type: str
    severity: str = "info"  # critical/high/medium/low/info
    description: str = ""
    root_cause: str = ""
    location: str = ""          # file:line
    poc_method: str = ""
    payload: str = ""
    # CVSS 4.0 输入（默认网络可达、低复杂度、无需权限、高影响）
    attack_vector: str = "N"
    attack_complexity: str = "L"
    privileges_required: str = "N"
    impact: str = "H"


# ------------------------------------------------------------------ 报告生成器

class ChainReportGenerator:
    """单链报告生成器（无状态，可复用实例）。"""

    # -------------------------------------------------- 报告装配

    def generate(
        self,
        chain_id: str,
        chain_data: Mapping[str, Any],
        analysis_results: List[Mapping[str, Any]],
        findings: Iterable[Mapping[str, Any]],
        load_count: int = 0,
    ) -> dict:
        """装配可序列化报告 dict。

        Args:
            chain_id: 调用链 ID
            chain_data: 至少含 ``entry_fqn``；可选 ``nodes``（list）
            analysis_results: 每个方法体的分析结果（污点追踪/业务逻辑/漏洞判定）
            findings: 漏洞发现列表（dict 或 Finding）
            load_count: 该链的方法体加载次数

        Returns:
            报告 dict，可直接 ``json.dumps`` 或喂给 ``to_markdown``
        """
        nodes = chain_data.get("nodes") or []
        entry_fqn = chain_data.get("entry_fqn", "")

        # 每个 finding 加 CVSS 4.0 评分
        scored_findings: List[dict] = []
        for f in findings:
            f_dict = dict(f) if isinstance(f, Mapping) else _finding_to_dict(f)
            f_dict["cvss_4"] = self.estimate_cvss_4(f_dict)
            f_dict["severity_rank"] = _severity_to_rank(f_dict.get("severity", "info"))
            scored_findings.append(f_dict)

        # 按严重性降序
        scored_findings.sort(key=lambda x: x["cvss_4"]["base_score"], reverse=True)

        return {
            "chain_id": chain_id,
            "entry_fqn": entry_fqn,
            "total_nodes": len(nodes) if isinstance(nodes, list) else 0,
            "method_analyses": list(analysis_results),
            "findings": scored_findings,
            "finding_count": len(scored_findings),
            "load_count": int(load_count),
        }

    # -------------------------------------------------- CVSS 4.0

    def estimate_cvss_4(self, finding: Any) -> dict:
        """简化版 CVSS 4.0 评分。

        Args:
            finding: dict（含 attack_vector/attack_complexity/privileges_required/impact）
                     或 Finding 实例

        Returns:
            ``{"base_score": float（0.0-10.0）, "vector": str}``
        """
        f = dict(finding) if isinstance(finding, Mapping) else _finding_to_dict(finding)

        av = _validate(f.get("attack_vector", "N"), _AV, "N")
        ac = _validate(f.get("attack_complexity", "L"), _AC, "L")
        pr = _validate(f.get("privileges_required", "N"), _PR, "N")
        imp = _validate(f.get("impact", "H"), _IMPACT, "H")

        exploitability = _AV[av] * _AC[ac] * _PR[pr]
        # impact=0 → 总分 = 0（CVSS 惯例）
        if _IMPACT[imp] == 0.0:
            base = 0.0
        else:
            raw = _IMPACT[imp] * 10.0 + exploitability * 10.0
            base = _roundup(min(10.0, raw))

        vector = f"CVSS:4.0/AV:{av}/AC:{ac}/PR:{pr}/VC:{imp}"
        return {"base_score": base, "vector": vector}

    # -------------------------------------------------- Markdown 渲染

    def to_markdown(self, report: Mapping[str, Any]) -> str:
        """渲染报告为 Markdown 文本（中文）。"""
        lines: List[str] = []
        lines.append(f"# 调用链报告：{report['chain_id']}")
        lines.append("")
        lines.append(f"- 入口方法：`{report['entry_fqn']}`")
        lines.append(f"- 节点总数：{report['total_nodes']}")
        lines.append(f"- 加载次数：{report['load_count']}")
        lines.append(f"- 发现数量：{report['finding_count']}")
        lines.append("")

        # 方法分析
        lines.append("## 方法体分析")
        mas = report.get("method_analyses") or []
        if not mas:
            lines.append("_（无）_")
        for i, ma in enumerate(mas, 1):
            lines.append(f"### {i}. {ma.get('fqn', '(unknown)')}")
            for k, v in ma.items():
                if k == "fqn":
                    continue
                lines.append(f"- **{k}**：{v}")
            lines.append("")

        # 漏洞列表
        lines.append("## 漏洞列表")
        fs = report.get("findings") or []
        if not fs:
            lines.append("_（未发现漏洞）_")
        for i, f in enumerate(fs, 1):
            cvss = f.get("cvss_4", {})
            lines.append(f"### {i}. [{f.get('severity', 'info').upper()}] "
                         f"{f.get('vuln_type', '(unknown)')}")
            lines.append(f"- 位置：`{f.get('location', '')}`")
            lines.append(f"- 描述：{f.get('description', '')}")
            lines.append(f"- 根因：{f.get('root_cause', '')}")
            lines.append(f"- PoC 构造：{f.get('poc_method', '')}")
            payload = f.get('payload', '')
            if payload:
                lines.append(f"- payload：`{payload}`")
            lines.append(f"- CVSS 4.0：**{cvss.get('base_score', 0.0)}** "
                         f"({cvss.get('vector', '')})")
            lines.append("")

        return "\n".join(lines)


# ------------------------------------------------------------------ 辅助

def _roundup(x: float) -> float:
    """CVSS 标准向上取整到 0.1。"""
    return math.ceil(x * 10) / 10


def _validate(value: Any, table: dict, default: str) -> str:
    """字段值规范化：表外值回落 default。"""
    if value is None:
        return default
    s = str(value).strip().upper()[:1]
    return s if s in table else default


def _severity_to_rank(severity: str) -> int:
    """严重性档位排序（高 → 低 = 5 → 0）。"""
    s = str(severity).strip().lower()
    return {
        "critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1,
    }.get(s, 0)


def _finding_to_dict(obj: Any) -> dict:
    """Finding dataclass → dict。"""
    if isinstance(obj, Finding):
        return {
            "vuln_type": obj.vuln_type,
            "severity": obj.severity,
            "description": obj.description,
            "root_cause": obj.root_cause,
            "location": obj.location,
            "poc_method": obj.poc_method,
            "payload": obj.payload,
            "attack_vector": obj.attack_vector,
            "attack_complexity": obj.attack_complexity,
            "privileges_required": obj.privileges_required,
            "impact": obj.impact,
        }
    # 兜底：尝试 __dict__
    return dict(getattr(obj, "__dict__", {}))
