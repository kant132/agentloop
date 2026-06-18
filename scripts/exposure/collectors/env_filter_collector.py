# -*- coding: utf-8 -*-
"""env_filter_collector.py — 运行环境 filter 采集器（SSH）。

通过 SSH 拉取远端：
1. nginx -T —— 完整 nginx 配置（含 server/filter/limit）
2. tomcat server.xml —— Tomcat 容器 Valve/Filter 定义
3. JVM 参数 -Dspring.config.location —— 实际加载的配置文件路径

无 ssh_target（ctx.is_offline=True）时降级：
    items=[]，degraded=True，stats.reason='offline_mode'

不做：
- 不真实 SSH 连接到生产（测试中 mock _ssh_exec）
- 不调 ast-grep
- ssh_target 为空不抛异常
"""
from __future__ import annotations

import re
import subprocess
from typing import Any

from ..contracts import CollectorResult, ExposureContext
from ..registry import register_collector


# ============================================================
# 命令模板 + 解析正则
# ============================================================

# (source_key, remote_command)
_COMMANDS: list[tuple[str, str]] = [
    ("nginx", "nginx -T 2>&1"),
    (
        "tomcat",
        "find / -type f -name server.xml -path '*conf*' 2>/dev/null "
        "| head -1 | xargs cat 2>/dev/null",
    ),
    ("jvm", "ps -ef | grep -i 'java.*-D' | grep -v grep"),
]

# nginx filter/include 模式（location / proxy_pass / limit_req_zone）
_NGINX_FILTER = re.compile(
    r"(?:\blocation\s+[\^~*]*[/\"]|limit_req_zone|proxy_pass|"
    r"ModSecurityEnabled|modsecurity\s+on)",
    re.IGNORECASE,
)
# Tomcat Valve 声明
_TOMCAT_VALVE = re.compile(
    r"<(?:Valve|Filter|FilterMapping|Context)[^>]*(?:className|name)=\"[^\"]+\"",
    re.IGNORECASE,
)
# JVM -Dxxx 参数
_JVM_DPARAM = re.compile(r"-D([\w.]+)(?:\s*=\s*([^\s]+))?")


@register_collector("env_filter")
class EnvFilterCollector:
    """运行环境 filter 采集器：SSH 拉取 + 离线降级。"""
    name = "env_filter_collector"
    asset_type = "env_filter"

    def is_available(self, ctx: ExposureContext) -> bool:
        """协议要求：结构上可用即可。离线场景由 collect() 处理降级。"""
        return True

    def collect(self, ctx: ExposureContext) -> CollectorResult:
        # 离线降级路径
        if ctx.is_offline:
            return CollectorResult(
                asset_type=self.asset_type,
                source="offline (no ssh_target configured)",
                items=[],
                stats={"total": 0, "reason": "offline_mode"},
                degraded=True,
            )

        items: list[dict[str, Any]] = []
        for source_key, command in _COMMANDS:
            raw = self._ssh_exec(ctx, command)
            if not raw:
                continue
            items.append({
                "source": source_key,
                "command": command,
                "filters": self._extract_filters(source_key, raw),
                "raw_length": len(raw),
            })

        degraded = len(items) < len(_COMMANDS)
        return CollectorResult(
            asset_type=self.asset_type,
            source=f"ssh exec on {ctx.ssh_target}",
            items=items,
            stats={
                "total": len(items),
                "ssh_target": ctx.ssh_target,
                "sources_found": [i["source"] for i in items],
                "expected_sources": [k for k, _ in _COMMANDS],
            },
            degraded=degraded,
        )

    # ----------------------------------------------------------
    # SSH 执行（可被测试 mock）
    # ----------------------------------------------------------
    def _ssh_exec(self, ctx: ExposureContext, command: str) -> str:
        """通过 subprocess ssh 执行命令，返回 stdout 文本。

        失败（超时、命令不存在、非零退出）返回 ""。
        """
        if not ctx.ssh_target:
            return ""
        try:
            proc = subprocess.run(
                [
                    "ssh",
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=5",
                    "-o", "StrictHostKeyChecking=accept-new",
                    ctx.ssh_target,
                    command,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""
        return proc.stdout if proc.returncode == 0 else ""

    # ----------------------------------------------------------
    # 配置解析
    # ----------------------------------------------------------
    @staticmethod
    def _extract_filters(source_key: str, raw: str) -> list[dict[str, Any]]:
        if source_key == "nginx":
            return [
                {"rule": m.group(0), "type": "nginx_directive"}
                for m in _NGINX_FILTER.finditer(raw)
            ]
        if source_key == "tomcat":
            return [
                {"rule": m.group(0), "type": "tomcat_valve"}
                for m in _TOMCAT_VALVE.finditer(raw)
            ]
        if source_key == "jvm":
            return [
                {"key": m.group(1), "value": m.group(2) or "", "type": "jvm_dparam"}
                for m in _JVM_DPARAM.finditer(raw)
            ]
        return []
