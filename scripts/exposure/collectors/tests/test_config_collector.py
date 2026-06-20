# -*- coding: utf-8 -*-
"""test_config_collector.py — config_collector 的单元测试。

测试 ConfigCollector 的行为契约（路径记录模式）：
1. implements_collector_protocol
2. has_correct_metadata
3. is_available（始终 True）
4. file_path_recorded
5. sha256_computed
6. detect_secret_in_file
7. detect_secret_string_api（保持兼容）
8. stats_total_files_correct
9. stats_by_type_correct
10. collect_no_project_dir
11. file_type_mapping
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import sys

# 注入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.exposure.contracts import ExposureContext, CollectorResult, Collector
from scripts.exposure.collectors.config_collector import ConfigCollector


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """构造一个含配置文件的测试项目。"""
    src = tmp_path / "src" / "main" / "resources"
    src.mkdir(parents=True)

    # application.yml（含 password）
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

    # application.properties（含 password）
    (src / "application.properties").write_text(
        "spring.datasource.url=jdbc:mysql://localhost/db\n"
        "spring.datasource.password=myP@ss\n"
        "spring.datasource.username=root\n"
        "server.port=8080\n",
        encoding="utf-8",
    )

    # bootstrap.yml（不含敏感）
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
# 4. 文件路径记录
# ============================================================

class TestFilePath:

    def test_file_path_recorded(self, ctx):
        """每个 item 应记录相对路径（file 字段）。"""
        result = ConfigCollector().collect(ctx)
        assert len(result.items) > 0
        for item in result.items:
            assert "file" in item
            # 兼容 Windows (\) 和 Unix (/) 路径分隔符
            normalized = item["file"].replace("\\", "/")
            assert normalized.startswith("src/main/resources/")

    def test_all_file_types_found(self, ctx):
        """yml、properties、bootstrap.yml 都应被发现。"""
        result = ConfigCollector().collect(ctx)
        files = [it["file"] for it in result.items]
        assert any("application.yml" in f for f in files)
        assert any("application.properties" in f for f in files)
        assert any("bootstrap.yml" in f for f in files)


# ============================================================
# 5. sha256 计算
# ============================================================

class TestSha256:

    def test_sha256_computed(self, ctx, tmp_project):
        """sha256 应正确计算。"""
        result = ConfigCollector().collect(ctx)
        # 手动计算 application.yml 的 sha256
        yml_path = tmp_project / "src" / "main" / "resources" / "application.yml"
        expected_hash = hashlib.sha256(yml_path.read_bytes()).hexdigest()
        yml_item = [it for it in result.items if "application.yml" in it["file"]][0]
        assert yml_item["sha256"] == expected_hash


# ============================================================
# 7-8. 敏感检测
# ============================================================

class TestSecretDetection:
    """敏感值检测。"""

    def test_detect_secret_string_api(self):
        """detect_secret() 保持原有字符串级接口兼容。"""
        c = ConfigCollector()
        assert c.detect_secret("jdbc:mysql://db?password=abc") is True
        assert c.detect_secret("my-secret-key") is True
        assert c.detect_secret("token123") is True
        assert c.detect_secret("just-a-normal-value") is False

    def test_detect_secret_in_file(self, ctx):
        """含 password 的文件应标记 contains_secret=True。"""
        result = ConfigCollector().collect(ctx)
        secret_items = [it for it in result.items if it["contains_secret"]]
        # application.yml 和 application.properties 都含 password
        assert len(secret_items) == 2

    def test_no_secret_file(self, tmp_path: Path):
        """不含敏感信息的文件应标记 contains_secret=False。"""
        src = tmp_path / "src" / "main" / "resources"
        src.mkdir(parents=True)
        (src / "application.yml").write_text(
            "server:\n  port: 8080\n", encoding="utf-8",
        )
        ctx = ExposureContext(
            project_root=tmp_path,
            group_id="test",
            loop_audit_dir=tmp_path / "loop_audit",
        )
        result = ConfigCollector().collect(ctx)
        assert all(it["contains_secret"] is False for it in result.items)


# ============================================================
# 9-10. 统计正确性
# ============================================================

class TestStatsCorrectness:

    def test_stats_total_files_correct(self, ctx):
        """collect 后 stats.total_files 应等于 items 数量。"""
        result = ConfigCollector().collect(ctx)
        assert isinstance(result, CollectorResult)
        assert result.asset_type == "config"
        assert result.stats["total_files"] == len(result.items)

    def test_stats_by_type_correct(self, ctx):
        """stats.by_type 应正确分类。"""
        result = ConfigCollector().collect(ctx)
        by_type = result.stats["by_type"]
        assert "yaml" in by_type
        assert "properties" in by_type
        assert by_type["yaml"] == 2  # application.yml + bootstrap.yml
        assert by_type["properties"] == 1

    def test_stats_secrets_detected(self, ctx):
        """stats.secrets_detected 应与 contains_secret 计数一致。"""
        result = ConfigCollector().collect(ctx)
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
        assert result.stats["total_files"] == 0
        assert result.items == []


# ============================================================
# 12. 文件类型映射
# ============================================================

class TestFileTypeMapping:

    def test_yml_type_is_yaml(self, tmp_path: Path):
        """yml 扩展名应映射为 yaml 类型。"""
        src = tmp_path / "src" / "main" / "resources"
        src.mkdir(parents=True)
        (src / "application.yml").write_text("server:\n  port: 8080\n", encoding="utf-8")
        ctx = ExposureContext(
            project_root=tmp_path,
            group_id="test",
            loop_audit_dir=tmp_path / "loop_audit",
        )
        result = ConfigCollector().collect(ctx)
        assert result.items[0]["file_type"] == "yaml"

    def test_properties_type(self, tmp_path: Path):
        """properties 扩展名应映射为 properties 类型。"""
        src = tmp_path / "src" / "main" / "resources"
        src.mkdir(parents=True)
        (src / "application.properties").write_text("server.port=8080\n", encoding="utf-8")
        ctx = ExposureContext(
            project_root=tmp_path,
            group_id="test",
            loop_audit_dir=tmp_path / "loop_audit",
        )
        result = ConfigCollector().collect(ctx)
        assert result.items[0]["file_type"] == "properties"
