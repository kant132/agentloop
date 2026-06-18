# -*- coding: utf-8 -*-
"""test_synthesizer.py — DefaultSynthesizer 的单元测试。

覆盖点：
1. empty_results_returns_empty_assets
2. dedup_removes_by_fqn_file_line_key
3. classify_high_risk_sensitive_info
4. classify_high_risk_post_route
5. classify_medium_risk_get_with_param
6. classify_low_risk_get_no_param
7. stats_by_type_aggregates_correctly
8. stats_by_risk_counts_correctly
9. dedup_report_records_before_after
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.exposure.contracts import ExposureContext
from scripts.exposure.synthesizer import DefaultSynthesizer


@pytest.fixture
def ctx(tmp_path: Path) -> ExposureContext:
    return ExposureContext(
        project_root=tmp_path,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
    )


@pytest.fixture
def synth() -> DefaultSynthesizer:
    return DefaultSynthesizer()


# ============================================================
# 行为契约测试
# ============================================================

class TestDefaultSynthesizerContract:
    """DefaultSynthesizer 必须满足 Synthesizer 协议。"""

    def test_implements_synthesizer_protocol(self):
        from scripts.exposure.contracts import Synthesizer
        assert isinstance(DefaultSynthesizer(), Synthesizer)

    def test_has_correct_name(self):
        assert DefaultSynthesizer().name == "default_synthesizer"


class TestSynthesizeEmpty:
    """空输入应返回空资产清单。"""

    def test_empty_results_returns_empty_assets(self, synth, ctx):
        out = synth.synthesize([], ctx)
        assert out["total_assets"] == 0
        assert out["assets"] == []
        assert out["by_type"] == {}
        assert out["by_risk"] == {"high": 0, "medium": 0, "low": 0}
        assert out["dedup_report"]["removed_duplicates"] == 0


class TestSynthesizeDedup:
    """去重逻辑：按 (fqn, file, line) 键去重。"""

    def test_dedup_removes_by_fqn_file_line_key(self, synth, ctx):
        results = [
            {
                "asset_type": "route",
                "items": [
                    {"fqn": "com.example.UserController#getUser", "file": "UserController.java", "line": 10, "http_method": "GET"},
                    {"fqn": "com.example.UserController#getUser", "file": "UserController.java", "line": 10, "http_method": "GET"},
                ],
            },
        ]
        out = synth.synthesize(results, ctx)
        assert out["total_assets"] == 1
        assert out["dedup_report"]["removed_duplicates"] == 1

    def test_dedup_report_records_before_after(self, synth, ctx):
        results = [
            {
                "asset_type": "route",
                "items": [
                    {"fqn": "A#m1", "file": "a.java", "line": 1},
                    {"fqn": "A#m1", "file": "a.java", "line": 1},  # dup
                    {"fqn": "B#m2", "file": "b.java", "line": 5},
                ],
            },
        ]
        out = synth.synthesize(results, ctx)
        assert out["dedup_report"]["before"] == 3
        assert out["dedup_report"]["after"] == 2
        assert out["dedup_report"]["removed_duplicates"] == 1


class TestClassifyRisk:
    """风险分级逻辑。"""

    def test_classify_high_risk_sensitive_info(self, synth, ctx):
        results = [
            {
                "asset_type": "sensitive_info",
                "items": [{"fqn": "X#pwd", "file": "x.java", "line": 1}],
            },
        ]
        out = synth.synthesize(results, ctx)
        assert out["by_risk"]["high"] == 1
        assert out["assets"][0]["_risk"] == "high"

    def test_classify_high_risk_post_route(self, synth, ctx):
        results = [
            {
                "asset_type": "route",
                "items": [{"fqn": "A#create", "file": "a.java", "line": 1, "http_method": "POST"}],
            },
        ]
        out = synth.synthesize(results, ctx)
        assert out["by_risk"]["high"] == 1
        assert out["assets"][0]["_risk"] == "high"

    def test_classify_medium_risk_get_with_param(self, synth, ctx):
        results = [
            {
                "asset_type": "route",
                "items": [{"fqn": "A#getUser", "file": "a.java", "line": 1, "http_method": "GET", "has_external_param": True}],
            },
        ]
        out = synth.synthesize(results, ctx)
        assert out["by_risk"]["medium"] == 1
        assert out["assets"][0]["_risk"] == "medium"

    def test_classify_low_risk_get_no_param(self, synth, ctx):
        results = [
            {
                "asset_type": "route",
                "items": [{"fqn": "A#listAll", "file": "a.java", "line": 1, "http_method": "GET", "has_external_param": False}],
            },
        ]
        out = synth.synthesize(results, ctx)
        assert out["by_risk"]["low"] == 1
        assert out["assets"][0]["_risk"] == "low"


class TestStatsAggregation:
    """统计汇总逻辑。"""

    def test_stats_by_type_aggregates_correctly(self, synth, ctx):
        results = [
            {
                "asset_type": "route",
                "items": [
                    {"fqn": "A#m1", "file": "a.java", "line": 1},
                    {"fqn": "A#m2", "file": "a.java", "line": 5},
                ],
            },
            {
                "asset_type": "config",
                "items": [{"fqn": "cfg#db", "file": "app.yml", "line": 1}],
            },
        ]
        out = synth.synthesize(results, ctx)
        assert out["by_type"]["route"] == 2
        assert out["by_type"]["config"] == 1

    def test_stats_by_risk_counts_correctly(self, synth, ctx):
        results = [
            {
                "asset_type": "route",
                "items": [
                    {"fqn": "A#create", "file": "a.java", "line": 1, "http_method": "POST"},
                    {"fqn": "A#getUser", "file": "a.java", "line": 5, "http_method": "GET", "has_external_param": True},
                    {"fqn": "A#list", "file": "a.java", "line": 10, "http_method": "GET", "has_external_param": False},
                ],
            },
        ]
        out = synth.synthesize(results, ctx)
        assert out["by_risk"] == {"high": 1, "medium": 1, "low": 1}


class TestClassifyRiskStatic:
    """直接测试 _classify_risk 静态方法。"""

    def test_is_sink_is_high(self):
        assert DefaultSynthesizer._classify_risk({"_source_type": "route", "is_sink": True}) == "high"

    def test_auth_code_is_high(self):
        assert DefaultSynthesizer._classify_risk({"_source_type": "auth_code"}) == "high"

    def test_config_with_secret_is_medium(self):
        assert DefaultSynthesizer._classify_risk({"_source_type": "config", "contains_secret": True}) == "medium"

    def test_config_without_secret_is_low(self):
        assert DefaultSynthesizer._classify_risk({"_source_type": "config", "contains_secret": False}) == "low"

    def test_unknown_type_defaults_medium(self):
        assert DefaultSynthesizer._classify_risk({"_source_type": "misc"}) == "medium"

    def test_put_is_high(self):
        assert DefaultSynthesizer._classify_risk({"_source_type": "route", "http_method": "PUT"}) == "high"

    def test_delete_is_high(self):
        assert DefaultSynthesizer._classify_risk({"_source_type": "route", "http_method": "DELETE"}) == "high"

    def test_patch_is_high(self):
        assert DefaultSynthesizer._classify_risk({"_source_type": "route", "http_method": "PATCH"}) == "high"
