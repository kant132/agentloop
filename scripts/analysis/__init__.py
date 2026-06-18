"""analysis 子包 — 调用链方法体加载、加载计数、单链报告、最终评价。

提供四个职责单一的模块：

- ``load_counter``：基于 sqlite3 的方法体加载次数追踪（loads.db）
- ``method_body_loader``：分前 5 层预加载 + 后续按需加载的策略
- ``chain_report_generator``：单条调用链的详尽报告 + CVSS 4.0 简化评分
- ``metric_simplifier``：跨链汇总指标，输出 verdict

设计原则：外部依赖（Memurai、文件系统路径）通过构造函数注入，
所有模块可被单元测试在临时目录下完整运行。
"""
