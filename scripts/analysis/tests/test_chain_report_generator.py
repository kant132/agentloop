"""test_chain_report_generator.py — 单链报告生成器测试。

覆盖：generate() 装配 / estimate_cvss_4 评分 / to_markdown 渲染 /
Finding dataclass 输入 / 排序与计数 / verdict 三态枚举校验。
"""
from __future__ import annotations

import pytest

from chain_report_generator import (
    ChainReportGenerator,
    Finding,
    VERDICT_ENUM,
    validate_verdict,
    _roundup,
    _severity_to_rank,
    _validate,
)


# =============================================================================
# fixtures
# =============================================================================

@pytest.fixture
def gen():
    return ChainReportGenerator()


@pytest.fixture
def chain_data():
    return {
        "entry_fqn": "com.app.ApiController#handle",
        "nodes": [
            {"node_id": f"n{i}", "depth": i} for i in range(3)
        ],
    }


@pytest.fixture
def analysis_results():
    return [
        {"fqn": "com.app.ApiController#handle", "taint": "source", "biz": "OK"},
        {"fqn": "com.app.Dao#query", "taint": "sink", "biz": "SQL execute"},
    ]


@pytest.fixture
def findings():
    return [
        {
            "vuln_type": "SQL Injection",
            "severity": "high",
            "description": "未参数化的 SQL 拼接",
            "root_cause": "Controller 直接把 request 参数拼到 SQL",
            "location": "Dao.java:42",
            "poc_method": "GET /api?id=1' UNION SELECT--",
            "payload": "1' UNION SELECT password FROM users--",
            "attack_vector": "N",
            "attack_complexity": "L",
            "privileges_required": "N",
            "impact": "H",
        },
    ]


# =============================================================================
# generate
# =============================================================================

def test_generate_basic_structure(gen, chain_data, analysis_results, findings):
    """generate 返回标准字段集。"""
    report = gen.generate("chain-1", chain_data, analysis_results, findings, load_count=3)

    assert report["chain_id"] == "chain-1"
    assert report["entry_fqn"] == "com.app.ApiController#handle"
    assert report["total_nodes"] == 3
    assert report["load_count"] == 3
    assert report["finding_count"] == 1
    assert len(report["method_analyses"]) == 2
    assert "cvss_4" in report["findings"][0]


def test_generate_attaches_cvss_to_each_finding(gen, chain_data, analysis_results, findings):
    """每个 finding 都被估算 cvss_4。"""
    fs = list(findings) + [{
        "vuln_type": "Low",
        "severity": "low",
        "attack_vector": "L",
        "attack_complexity": "H",
        "privileges_required": "H",
        "impact": "L",
    }]
    report = gen.generate("c", chain_data, [], fs)
    assert len(report["findings"]) == 2
    for f in report["findings"]:
        assert "base_score" in f["cvss_4"]
        assert f["cvss_4"]["vector"].startswith("CVSS:4.0/")


def test_generate_sorts_findings_by_severity_desc(gen, chain_data):
    """findings 按 base_score 降序。"""
    fs = [
        {"vuln_type": "low", "severity": "low", "impact": "L",
         "attack_vector": "L", "attack_complexity": "H", "privileges_required": "H"},
        {"vuln_type": "high", "severity": "high", "impact": "H",
         "attack_vector": "N", "attack_complexity": "L", "privileges_required": "N"},
    ]
    report = gen.generate("c", chain_data, [], fs)
    assert report["findings"][0]["vuln_type"] == "high"
    assert report["findings"][1]["vuln_type"] == "low"


def test_generate_accepts_finding_dataclass(gen, chain_data):
    """Finding dataclass 实例也能直接传入。"""
    f = Finding(
        vuln_type="RCE",
        severity="critical",
        description="...", root_cause="...", location="X.java:1",
        poc_method="...", payload="...",
        attack_vector="N", attack_complexity="L",
        privileges_required="N", impact="H",
    )
    report = gen.generate("c", chain_data, [], [f])
    assert report["findings"][0]["vuln_type"] == "RCE"
    assert report["findings"][0]["cvss_4"]["base_score"] >= 9.0


def test_generate_no_findings(gen, chain_data):
    """findings 为空时 finding_count=0。"""
    report = gen.generate("c", chain_data, [], [])
    assert report["finding_count"] == 0


# =============================================================================
# estimate_cvss_4
# =============================================================================

def test_cvss_max_score_for_network_easy_no_auth_high_impact(gen):
    """网络可达 + 低复杂度 + 无需权限 + 高影响 → 接近满分。"""
    result = gen.estimate_cvss_4({
        "attack_vector": "N", "attack_complexity": "L",
        "privileges_required": "N", "impact": "H",
    })
    assert result["base_score"] == 10.0
    assert "AV:N" in result["vector"]


def test_cvss_zero_impact_returns_zero(gen):
    """impact=N → base_score=0（CVSS 惯例）。"""
    result = gen.estimate_cvss_4({
        "attack_vector": "N", "attack_complexity": "L",
        "privileges_required": "N", "impact": "N",
    })
    assert result["base_score"] == 0.0


def test_cvss_physical_high_complexity_high_priv_low_impact_low_score(gen):
    """物理 + 高复杂度 + 高权限 + 低影响 → 低分。"""
    result = gen.estimate_cvss_4({
        "attack_vector": "P", "attack_complexity": "H",
        "privileges_required": "H", "impact": "L",
    })
    assert 0.0 < result["base_score"] <= 3.0


def test_cvss_invalid_values_fall_back_to_defaults(gen):
    """非法字段值（如 'XYZ' / 数字）回落到默认。"""
    result = gen.estimate_cvss_4({
        "attack_vector": "XYZ", "attack_complexity": 99,
        "privileges_required": None, "impact": "unknown",
    })
    # 全部回退到默认 N/L/N/H → 满分
    assert result["base_score"] == 10.0


# =============================================================================
# to_markdown
# =============================================================================

def test_to_markdown_contains_chain_id_and_findings(gen, chain_data, findings):
    """Markdown 包含 chain_id 标题、finding 类型、CVSS。"""
    report = gen.generate("chain-XYZ", chain_data, [], findings)
    md = gen.to_markdown(report)
    assert "# 调用链报告：chain-XYZ" in md
    assert "SQL Injection" in md
    assert "CVSS 4.0" in md
    assert "Dao.java:42" in md


def test_to_markdown_empty_findings(gen, chain_data):
    """无 findings → 列出「未发现漏洞」。"""
    report = gen.generate("c", chain_data, [], [])
    md = gen.to_markdown(report)
    assert "未发现漏洞" in md


def test_to_markdown_includes_method_analyses(gen, chain_data, analysis_results):
    """Markdown 包含方法体分析。"""
    report = gen.generate("c", chain_data, analysis_results, [])
    md = gen.to_markdown(report)
    assert "com.app.Dao#query" in md
    assert "SQL execute" in md


# =============================================================================
# 辅助函数
# =============================================================================

def test_roundup_rounds_up_to_one_decimal():
    """_roundup 始终向上取整到 0.1。"""
    assert _roundup(7.31) == 7.4
    assert _roundup(7.30) == 7.3
    assert _roundup(0.0) == 0.0


def test_severity_rank_ordering():
    """严重性档位排序：critical > high > medium > low > info > unknown。"""
    assert _severity_to_rank("critical") > _severity_to_rank("high")
    assert _severity_to_rank("high") > _severity_to_rank("medium")
    assert _severity_to_rank("medium") > _severity_to_rank("low")
    assert _severity_to_rank("low") > _severity_to_rank("info")
    assert _severity_to_rank("info") > _severity_to_rank("unknown")


def test_validate_falls_back_on_unknown():
    """_validate 非法值回落 default。"""
    assert _validate(None, {"N": 1, "L": 2}, "N") == "N"
    assert _validate("xyz", {"N": 1, "L": 2}, "N") == "N"
    assert _validate("n", {"N": 1, "L": 2}, "L") == "N"  # 大小写不敏感
    assert _validate("L", {"N": 1, "L": 2}, "N") == "L"


# =============================================================================
# verdict 三态枚举校验
# =============================================================================

def test_validate_verdict_legal_values():
    """verdict 合法值 vuln/safe/unknown 正常通过。"""
    assert validate_verdict("vuln") == "vuln"
    assert validate_verdict("safe") == "safe"
    assert validate_verdict("unknown") == "unknown"


def test_validate_verdict_illegal_values_downgrade():
    """非法 verdict 值降级为 "unknown"。"""
    assert validate_verdict("invalid") == "unknown"
    assert validate_verdict("") == "unknown"
    assert validate_verdict("VULN") == "unknown"  # 大小写敏感
    assert validate_verdict("maybe") == "unknown"


def test_generate_verdict_in_report(gen, chain_data):
    """generate 输出包含 verdict 字段，默认 unknown。"""
    report = gen.generate("c", chain_data, [], [])
    assert "verdict" in report
    assert report["verdict"] == "unknown"


def test_generate_verdict_vuln(gen, chain_data):
    """verdict="vuln" 正常写入。"""
    report = gen.generate("c", chain_data, [], [], verdict="vuln")
    assert report["verdict"] == "vuln"


def test_generate_verdict_safe(gen, chain_data):
    """verdict="safe" 正常写入。"""
    report = gen.generate("c", chain_data, [], [], verdict="safe")
    assert report["verdict"] == "safe"


def test_generate_verdict_illegal_downgraded(gen, chain_data):
    """非法 verdict 值在 generate 中被降级为 "unknown"。"""
    report = gen.generate("c", chain_data, [], [], verdict="invalid")
    assert report["verdict"] == "unknown"
    report2 = gen.generate("c", chain_data, [], [], verdict="")
    assert report2["verdict"] == "unknown"


def test_verdict_enum_contains_only_three_values():
    """VERDICT_ENUM 只有 vuln/safe/unknown 三个值。"""
    assert VERDICT_ENUM == {"vuln", "safe", "unknown"}
