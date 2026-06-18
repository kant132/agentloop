# -*- coding: utf-8 -*-
"""test_sensitive_info_collector.py — SensitiveInfoCollector 单元测试。

8 个测试覆盖：协议、AWS key、私钥、密码、JWT、值脱敏、熵计算、跳过测试文件。
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import ExposureContext, CollectorResult, Collector
from scripts.exposure.collectors.sensitive_info_collector import (
    SensitiveInfoCollector, _shannon_entropy, _redact,
)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """构造含敏感信息的测试项目。"""
    src = tmp_path / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)

    # 正常 Java 文件（含各种敏感信息）
    (src / "Config.java").write_text(
        '''
package com.example;

public class Config {
    // AWS access key
    private static final String AWS_KEY = "AKIAIOSFODNN7EXAMPLE";
    // Hardcoded password
    private String dbPassword = "mySecretP@ss";
    // JWT secret
    private String jwtSecret = "superSecretJwtKey2024!";
    // Private key
    private String keyContent = "-----BEGIN PRIVATE KEY-----\\nMIIEvQ...";
    // Internal IP
    private String internalHost = "192.168.1.100";
    // Developer email
    private String devEmail = "dev@company.internal";
}
''',
        encoding="utf-8",
    )

    # .properties 文件（含密码和密钥）
    props = tmp_path / "config" / "application.properties"
    props.mkdir(parents=True, exist_ok=True)
    (props / "application.properties").write_text(
        "database.password=DbP@ssw0rd123\n"
        "api.key=sk-live-abc123def456ghi789\n"
        "jwt.secret=myJwtSecretValue2024abcd\n",
        encoding="utf-8",
    )

    # 测试文件（应被跳过）
    test_src = tmp_path / "src" / "test" / "java" / "com" / "example"
    test_src.mkdir(parents=True)
    (test_src / "ConfigTest.java").write_text(
        '''
package com.example;

import org.junit.Test;

public class ConfigTest {
    @Test
    public void testConfig() {
        // This password in a test file should be skipped
        String testPassword = "testPassword123";
    }
}
''',
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def ctx(tmp_project: Path, tmp_path: Path) -> ExposureContext:
    return ExposureContext(
        project_root=tmp_project,
        group_id="com.example",
        loop_audit_dir=tmp_path / "loop_audit",
    )


# ============================================================
# 测试
# ============================================================

class TestSensitiveInfoCollectorProtocol:
    """协议合规测试。"""

    def test_implements_collector_protocol(self):
        """SensitiveInfoCollector 必须能通过 Collector 协议检查。"""
        assert isinstance(SensitiveInfoCollector(), Collector)

    def test_has_correct_metadata(self):
        c = SensitiveInfoCollector()
        assert c.name == "sensitive_info_collector"
        assert c.asset_type == "sensitive_info"

    def test_is_always_available(self, ctx):
        """不需要外部工具，始终可用。"""
        assert SensitiveInfoCollector().is_available(ctx) is True


class TestAwsCredentialDetection:
    """AWS 凭证检测。"""

    def test_detect_aws_access_key(self, ctx):
        """AKIA + 16 字符应被检测为 aws_credential。"""
        result = SensitiveInfoCollector().collect(ctx)
        aws_items = [it for it in result.items if it["type"] == "aws_credential"]
        assert len(aws_items) >= 1
        # AKIA 开头的应被匹配
        akia_items = [it for it in aws_items if "AKIA" in it["value_preview"]]
        assert len(akia_items) >= 1
        assert akia_items[0]["severity"] == "high"

    def test_aws_access_key_format(self):
        """AKIA + 精确 16 字符的格式验证。"""
        from scripts.exposure.collectors.sensitive_info_collector import _PATTERN_DEFS
        aws_pattern = [p for t, p, _ in _PATTERN_DEFS if t == "aws_credential"][0]
        assert aws_pattern.search("AKIAIOSFODNN7EXAMPLE")
        # 不应匹配短于 16 字符的
        assert not aws_pattern.search("AKIAIOSFODNN7EX")


class TestPrivateKeyDetection:
    """私钥检测。"""

    def test_detect_private_key(self, ctx):
        """BEGIN PRIVATE KEY 应被检测。"""
        result = SensitiveInfoCollector().collect(ctx)
        pk_items = [it for it in result.items if it["type"] == "private_key"]
        assert len(pk_items) >= 1
        assert pk_items[0]["severity"] == "high"

    def test_detect_rsa_private_key(self):
        """BEGIN RSA PRIVATE KEY 也应被检测。"""
        from scripts.exposure.collectors.sensitive_info_collector import _PATTERN_DEFS
        pk_pattern = [p for t, p, _ in _PATTERN_DEFS if t == "private_key"][0]
        assert pk_pattern.search("-----BEGIN RSA PRIVATE KEY-----")


class TestPasswordDetection:
    """硬编码密码检测。"""

    def test_detect_hardcoded_password(self, ctx):
        """password = "..." 应被检测。"""
        result = SensitiveInfoCollector().collect(ctx)
        pwd_items = [it for it in result.items if it["type"] == "password"]
        assert len(pwd_items) >= 1

    def test_password_in_properties(self, ctx):
        """.properties 文件中的 password=xxx 应被检测。"""
        result = SensitiveInfoCollector().collect(ctx)
        pwd_items = [it for it in result.items
                     if it["type"] == "password" and "properties" in it["file"]]
        assert len(pwd_items) >= 1


class TestJwtSecretDetection:
    """JWT secret 检测。"""

    def test_detect_jwt_secret(self, ctx):
        """jwt.secret / JWT_SECRET 应被检测。"""
        result = SensitiveInfoCollector().collect(ctx)
        jwt_items = [it for it in result.items if it["type"] == "jwt_secret"]
        assert len(jwt_items) >= 1


class TestValueRedaction:
    """值脱敏测试：不泄露完整密钥值。"""

    def test_redact_value_in_output(self, ctx):
        """所有条目的 value_preview 最多前 4 字符 + ****。"""
        result = SensitiveInfoCollector().collect(ctx)
        for item in result.items:
            preview = item["value_preview"]
            assert preview.endswith("****")
            # 星号前的前缀不超过 4 字符
            prefix = preview[:-4]
            assert len(prefix) <= 4

    def test_redact_short_value(self):
        """短于 4 字符的值也应脱敏。"""
        assert _redact("ab") == "ab****"

    def test_redact_exact_four(self):
        """恰好 4 字符的值也应脱敏。"""
        assert _redact("abcd") == "abcd****"


class TestEntropyCalculation:
    """熵计算测试。"""

    def test_entropy_calculation(self):
        """验证香农熵计算正确（使用 math.log2）。"""
        # 单字符重复 → 熵为 0
        assert _shannon_entropy("aaaa") == 0.0
        # 4 种各不相同的字符 → 熵为 log2(4) = 2.0
        assert _shannon_entropy("abcd") == 2.0
        # 空字符串 → 熵为 0
        assert _shannon_entropy("") == 0.0
        # 高熵字符串
        entropy = _shannon_entropy("AKIAIOSFODNN7EXAMPLE")
        assert entropy > 3.0

    def test_entropy_uses_log2(self):
        """确认熵计算使用 math.log2 而非自然对数。"""
        # 8 种各不相同的字符 → 熵应为 log2(8) = 3.0
        result = _shannon_entropy("abcdefgh")
        assert result == 3.0  # log2(8) = 3, not ln(8) ≈ 2.08


class TestSkipTestFiles:
    """跳过测试文件。"""

    def test_skip_test_files(self, ctx):
        """Java 文件中含 @Test 注解应被跳过。"""
        result = SensitiveInfoCollector().collect(ctx)
        # 测试文件中的 testPassword 不应出现
        test_pwd_items = [
            it for it in result.items
            if it["type"] == "password"
            and "ConfigTest" in it["file"]
        ]
        assert len(test_pwd_items) == 0
        # 但正常文件中的密码应被检测
        non_test_pwd_items = [
            it for it in result.items
            if it["type"] == "password"
            and "ConfigTest" not in it["file"]
        ]
        assert len(non_test_pwd_items) >= 1

    def test_stats_include_total(self, ctx):
        """stats 应包含 total 和 by_type。"""
        result = SensitiveInfoCollector().collect(ctx)
        assert "total" in result.stats
        assert result.stats["total"] == len(result.items)
        assert "by_type" in result.stats
