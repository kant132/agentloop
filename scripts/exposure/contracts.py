# -*- coding: utf-8 -*-
"""contracts.py — 暴露面管线的抽象协议。

依据 SOLID 中的 DIP/ISP：主流程依赖此处定义的抽象，不依赖具体实现。
所有 collector/synthesizer/ranker 必须实现对应协议。

引用：RFC-0001 §3.3 Collector 协议
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ============================================================
# ExposureContext — 所有 collector 共享的上下文
# ============================================================

@dataclass
class ExposureContext:
    """一次暴露面采集任务的共享上下文。

    Attributes:
        project_root: 被审计 Java 项目根目录
        group_id:     项目 groupId（如 org.owasp.webgoat）
        loop_audit_dir: 产出目录（preset.loopDir + project_root）
        jar_analyzer_db: jar-analyzer SQLite 路径（必选，Phase 1-3 核心工具）
        ssh_target:   SSH 目标（host:port），可空表示离线模式
        commit_hash:  当前 commit，用于缓存 key 隔离
    """
    project_root: Path
    group_id: str
    loop_audit_dir: Path
    jar_analyzer_db: Path | None = None
    ssh_target: str | None = None
    commit_hash: str = ""

    @property
    def exposure_dir(self) -> Path:
        """所有 collector 的统一输出目录: {loop_audit_dir}/exposure/"""
        d = self.loop_audit_dir / "exposure"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def is_offline(self) -> bool:
        """是否离线模式（无 SSH 环境）。"""
        return self.ssh_target is None


# ============================================================
# CollectorResult — 单个 collector 的输出
# ============================================================

@dataclass
class CollectorResult:
    """采集结果。

    Attributes:
        asset_type:   资产类型（route/config/auth_code/...）
        source:       采集来源描述（如 "ast-grep annotation scan"）
        items:        采集到的资产条目列表
        stats:        统计信息（total、degraded_count 等）
        errors:       采集过程中的错误（不阻塞主流程）
        degraded:     是否发生降级
    """
    asset_type: str
    source: str
    items: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    degraded: bool = False
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_type": self.asset_type,
            "source": self.source,
            "stats": self.stats,
            "errors": self.errors,
            "degraded": self.degraded,
            "collected_at": self.collected_at,
            "items": self.items,
        }


# ============================================================
# Collector 协议 — 所有采集器必须实现
# ============================================================

@runtime_checkable
class Collector(Protocol):
    """暴露面采集器协议。

    实现者需提供类属性：
        name:        采集器名称（如 "route_collector"）
        asset_type:  资产类型（如 "route"）

    以及方法：
        collect(ctx) -> CollectorResult
        is_available(ctx) -> bool
    """
    name: str
    asset_type: str

    def collect(self, ctx: ExposureContext) -> CollectorResult: ...
    def is_available(self, ctx: ExposureContext) -> bool: ...


# ============================================================
# Synthesizer 协议 — 综合阶段
# ============================================================

@runtime_checkable
class Synthesizer(Protocol):
    """综合阶段协议：将多个 collector 结果清洗为高可用资产清单。

    输入是 CollectorResult 序列化后的 dict（从 JSON 读回），保持与 CLI 的兼容性。
    """
    name: str

    def synthesize(
        self, results: list[dict[str, Any]], ctx: ExposureContext
    ) -> dict[str, Any]:
        """输入所有 collector 结果（dict 形式），输出清洗后的资产 JSON。"""
        ...
