# -*- coding: utf-8 -*-
"""scripts.exposure.collectors.route — 路由采集子模块。

拆分自 route_collector.py，三个单一职责组件：
- rule_loader.RuleLoader    : YAML 规则加载 + pattern-either 展开
- astgrep_scanner.AstGrepScanner : ast-grep 子进程封装 + 输出解析
- enricher.RouteEnricher     : 路由条目富化（HTTP方法/nodes_id/sig_hash/params）
"""
