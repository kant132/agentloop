---
name: injection-audit
description: "注入类漏洞审计。追踪用户输入到危险 sink 的完整数据流，审计每层防御的实际效果。触发词：'注入审计'、'injection audit'。"
---

# 注入类漏洞审计

专注：用户输入流入危险 sink（SQL/CMD/XXE/表达式/SSRF/反序列化/LDAP/NoSQL/SSTI）。

## 思路

输入已是完整调用链（入口 → 中间处理 → sink）。

1. **追踪数据流的每一层处理**：经过几层转换？每层做了什么？是否真的消除了注入可能？
2. **审视每一层防御**：不假设"用了框架就安全"，看具体实现够不够。

## 步骤（3 步强制）

### 1. 防御审计（arthas）

逐层检查调用链上每层处理的实际效果：
- 过滤是否完整
- 转义是否在正确上下文
- 参数化是否真的用了
- WAF 规则是否能绕过

用 `watch` 观察每层处理方法的输入输出，确认数据被正确处理而非只是被声明处理。

### 2. 构造与验证

针对防御薄弱点构造最小 payload → curl 触发 → arthas watch sink 方法：
- 确认 sink 收到的参数含未经处理的用户输入
- 确认 payload 改变了执行逻辑
- 失败时：记录绕过尝试和失败原因，trace 定位防御生效的具体层

### 3. 结论

一行：注入类型 + 是否注入 + 防御缺陷 + 证据位置。

## 常见坑

### SQL 注入
- 无法参数化的子句：ORDER BY、LIMIT、IN 列表
- 存储过程内部动态拼接
- 二阶注入：数据先存储，再次查询时拼入 SQL
- ORM 动态查询（hibernate createQuery、MyBatis `${}`）≠ 参数化
- LIKE 通配符未转义

### 命令注入
- 引号内仍可注入
- 分隔符：`;` `&&` `||` `` ` `` `$()`
- 空格绕过：`$IFS`、`{cmd,arg1,arg2}`

### XXE
- 默认不禁用 external entities，需显式禁用
- 只在某个 parser 实例禁了，漏了另一个
- 嵌套 XML（SOAP/SAML）内层 parser 未保护
- Blind XXE 需带外验证

### 反序列化
- 不只是 Java 原生：JSON（fastjson）、YAML（snakeyaml）、Protobuf、XStream 都有攻击面
- `@type`/`@class` 可加载任意类
- 过滤了特定 gadget 类名但新依赖引入新 gadget

### 表达式注入（SpEL/OGNL）
- 注解中用户可控数据、标签属性中表达式求值

### SSTI
- 用户输入直接 render = 表达式求值，不是参数替换

### LDAP/NoSQL
- LDAP `(uid=*)` 型绕过
- NoSQL 查询条件构造为对象导致操作符绕过

## 禁止行为

- 假设框架自动防御（必须验证实际代码）
- 只看过滤逻辑不看调用链上下文
- 忽略二阶注入和存储型注入
