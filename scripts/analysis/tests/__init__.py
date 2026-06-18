"""analysis 子包测试集。

覆盖：

- ``test_load_counter.py``：sqlite 计数与 ratio 指标
- ``test_method_body_loader.py``：分前 5 层 / 延迟加载策略（Memurai 已 mock）
- ``test_chain_report_generator.py``：报告生成 + CVSS 4.0 简化评分
- ``test_metric_simplifier.py``：跨链聚合与 verdict
"""
