# -*- coding: utf-8 -*-
"""scripts.exposure — 暴露面采集与决策管线包。

基于 RFC-0001: docs/specs/rfcs/exposure-pipeline-redesign.md

设计原则：
- SRP：每个 collector 只产出一类资产
- OCP/LSP：通过 contracts.Collector 协议扩展，不改主流程
- DIP：主流程依赖抽象协议，不依赖具体实现
- AI 边界：确定性工作 100% 脚本化，AI 只做分类/判定/PoC

子模块：
- contracts:  抽象协议（Collector/Synthesizer）
- registry:  collector 注册表与发现机制
- cli:        统一命令行入口
- collectors: 8 个具体采集器
- synthesizer: 综合阶段（数据清洗）
"""
__version__ = "0.1.0"
