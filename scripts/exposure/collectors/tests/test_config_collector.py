# -*- coding: utf-8 -*-
"""test_config_collector.py — config_collector 的单元测试。

测试 ConfigCollector 的行为契约：
1. implements_collector_protocol
2. has_correct_metadata
3. is_available（始终 True）
4. parse_yaml_file
5. parse_properties_file
6. detect_secret_in_value
7. redact_secret_value
8. stats_total_correct
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sys

# 注入路径（与 route_collector 测试一致）
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import ExposureContext, CollectorResult, Collector
from scripts.exposure.collectors.config_collector import ConfigCollector


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """构造一个含配置文件的测试项目。"""
    src = tmp_path / "src" / "main" / "resources"
    src.mkdir(parents=True)

    # application.yml
    (src / "application.yml").write_text(
        "server:\n"
        "  port: 8080\n"
        "spring:\n"
        "  datasource:\n"
        "    url: jdbc:mysql://localhost/db\n"
        "    password: s3cret!\n"
        "    username: admin\n"
        "logging:\n"
        "  level: INFO\n",
        encoding="utf-8",
    )

    # application.properties
    (src / "application.properties").write_text(
        "spring.datasource.url=jdbc:mysql://localhost/db\n"
        "spring.datasource.password=myP@ss\n"
        "spring.datasource.username=root\n"
        "server.port=8080\n",
        encoding="utf-8",
    )

    # bootstrap.yml
    (src / "bootstrap.yml").write_text(
        "spring:\n"
        "  cloud:\n"
        "    config:\n"
        "      uri: http://config-server\n",
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
# 1. 协议实现
# ============================================================

class TestConfigCollectorContract:
    """ConfigCollector 必须满足 Collector 协议。"""

    def test_implements_collector_protocol(self):
        """ConfigCollector 必须能通过 Collector 协议检查。"""
        assert isinstance(ConfigCollector(), Collector)

    def test_has_correct_metadata(self):
        c = ConfigCollector()
        assert c.name == "config_collector"
        assert c.asset_type == "config"

    def test_is_available(self, ctx):
        """无外部依赖，始终 True。"""
        assert ConfigCollector().is_available(ctx) is True


# ============================================================
# 4. YAML 解析
# ============================================================

class TestYamlParsing:
    """YAML 文件解析测试。"""

    def test_parse_yaml_file(self, tmp_path: Path):
        """最小 YAML 解析：提取 key-value 条目。"""
        fp = tmp_path / "app.yml"
        fp.write_text(
            "server:\n"
            "  port: 8080\n"
            "  host: localhost\n",
            encoding="utf-8",
        )
        items = ConfigCollector._parse_yaml_file(fp)
        assert len(items) >= 2
        # 检查至少有一个 port 条目
        port_items = [it for it in items if "port" in it["key"]]
        assert len(port_items) >= 1
        assert port_items[0]["source"] == "yaml"

    def test_yaml_secret_detected_and_redacted(self, tmp_path: Path):
        """YAML 中 password 字段应标记 contains_secret 并脱敏。"""
        fp = tmp_path / "secret.yml"
        fp.write_text("db:\n  password: hunter2\n", encoding="utf-8")
        items = ConfigCollector._parse_yaml_file(fp)
        pwd_items = [it for it in items if "password" in it["key"]]
        assert len(pwd_items) == 1
        assert pwd_items[0]["contains_secret"] is True
        assert pwd_items[0]["value"] == "****"


# ============================================================
# 5. Properties 解析
# ============================================================

class TestPropertiesParsing:

    def test_parse_properties_file(self, tmp_path: Path):
        """Properties 文件解析：提取 key=value 条目。"""
        fp = tmp_path / "app.properties"
        fp.write_text(
            "server.port=8080\n"
            "spring.datasource.url=jdbc:mysql://db\n"
            "# comment line\n"
            "spring.datasource.username=admin\n",
            encoding="utf-8",
        )
        items = ConfigCollector._parse_properties_file(fp)
        assert len(items) == 3  # comment 被跳过
        assert items[0]["key"] == "server.port"
        assert items[0]["source"] == "properties"


# ============================================================
# 6-7. 敏感检测与脱敏
# ============================================================

class TestSecretDetection:
    """敏感值检测与脱敏。"""

    def test_detect_secret_in_value(self):
        """包含 password/key/token 的值应检测为敏感。"""
        c = ConfigCollector()
        assert c.detect_secret("jdbc:mysql://db?password=abc") is True
        assert c.detect_secret("my-secret-key") is True
        assert c.detect_secret("token123") is True
        assert c.detect_secret("credential_store") is True
        assert c.detect_secret("private_key_path") is True
        assert c.detect_secret("access_key_id") is True
        assert c.detect_secret("just-a-normal-value") is False

    def test_detect_secret_in_key(self):
        """key 名包含敏感词也应检测。"""
        c = ConfigCollector()
        assert c.detect_secret("spring.datasource.password") is True
        assert c.detect_secret("spring.datasource.username") is False

    def test_redact_secret_value(self):
        """敏感值应被脱敏为 ****。"""
        # _redact 是静态方法，测试其行为
        assert ConfigCollector._redact("hunter2") == "****"
        assert ConfigCollector._redact("anything") == "****"


# ============================================================
# 8. 统计正确性
# ============================================================

class TestStatsCorrectness:

    def test_stats_total_correct(self, ctx):
        """collect 后 stats.total 应等于 items 数量。"""
        result = ConfigCollector().collect(ctx)
        assert isinstance(result, CollectorResult)
        assert result.asset_type == "config"
        assert result.stats["total"] == len(result.items)
        assert "by_source" in result.stats
        assert result.stats["secrets_detected"] == sum(
            1 for it in result.items if it.get("contains_secret")
        )

    def test_collect_no_project_dir(self, tmp_path: Path):
        """项目根不存在时返回空结果。"""
        bad_ctx = ExposureContext(
            project_root=tmp_path / "nonexistent",
            group_id="x",
            loop_audit_dir=tmp_path,
        )
        result = ConfigCollector().collect(bad_ctx)
        assert result.stats["total"] == 0
        assert result.items == []
