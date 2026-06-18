# -*- coding: utf-8 -*-
"""scripts.exposure.collectors — 8 个暴露面采集器子包。

每个 collector 一个文件，遵循单一职责（SRP）：
- route_collector      : 路由（注解/XML/编程式）
- config_collector     : 配置文件（yaml/properties/xml）
- codegraph_collector  : codegraph 查询（SQL/auth 代码等）
- env_filter_collector : 运行环境 filter（SSH）
- auth_code_collector  : 认证鉴权代码
- waf_collector        : WAF 识别
- db_schema_collector  : 数据库结构
- sensitive_info_collector : 敏感信息（密钥/密码/token）
"""
