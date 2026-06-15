---
name: injection-audit
description: "注入类漏洞审计子 agent 专用 skill。规范注入审计的思考路径，指出各类注入的易遗漏点。给定完整调用链，审计防御实现和实际执行效果。使用 arthas 动态验证。触发词：'注入审计'、'injection audit'、'检查注入'、'审计注入点'。"
---

## Integration with New Architecture (2026-06-15)

### Dispatch Context

Experts are now called by **endpoint-supervisor** (not boss). Supervisor provides:

```json
{
  "chain_data": {
    "fqn": "...",
    "chain": [{"fqn", "file", "start_line", "end_line", "body", "sinks": [...]}],
    "sig_hash": "abc123..."
  },
  "analyst_result": {
    "dimensions": {
      "input_tracing": "user_controlled|derived_from_user|internal_only",
      "sanitization": "none|partial|complete",
      "authorization": "present_and_strict|present_but_weak|missing_or_bypassable",
      "branch_logic": "normal|race_condition|state_bypass|numeric_overflow",
      "info_leakage": "low_risk|medium_risk|high_risk"
    }
  },
  "sink_table": {"categories": [{"name", "fqns", "severity"}]},
  "sanitizer_table": {"categories": [{"name", "fqns", "effectiveness"}]},
  "loop_audit_dir": "D:\\agentloop\\projects\\{groupId}\\loop_audit",
  "group_id": "org.owasp.webgoat",
  "chain_id": "abc123..."
}
```

### Preset Knowledge (read at start)

- `projects/_template/06-通用安全知识.md` — 10 大类 sink + 6 类业务逻辑
- `projects/_template/07-Sink表.json` — 39 sinks (16 categories), each with `cwe_id`, `owasp`, `dangerous_args`, `applies_to_sinks`
- `projects/_template/08-Sanitizer表.json` — 28 sanitizers (8 categories), each with `effectiveness`, `confidence`, `applies_to_sinks`

### Chain Body Annotations

Method bodies in `chain_data.chain[*].body` have inline comments:
```java
public AttackResult completed(@RequestParam String userid) {
    // sink: java.sql.Statement.executeQuery
    Statement s = conn.createStatement(ResultSet.TYPE_SCROLL_INSENSITIVE);
    ...
}
```

`// sink: <FQN>` markers indicate **non-groupId third-party calls** (per `method_calls_extractor.py` L102). Prioritize analysis of sink-annotated lines.

### Findings Output Contract

Write to `Memurai {group_id}:audit:finding:{chain_id}:draft`:

```json
{
  "finding_id": "auto-uuid",
  "chain_id": "abc123...",
  "fqn": "org.owasp.webgoat.lessons.sqlinjection.advanced.SqlInjectionLesson6b.completed",
  "vuln_type": "SQL_INJECTION",
  "vuln_subtype": "UNION_BASED",
  "severity": "high",
  "method": "completed",
  "endpoint": "/SqlInjection/attack6b",
  "evidence": {
    "source_line": 24,
    "sink_line": 30,
    "taint_flow": "userid → stmt.executeQuery(userid)",
    "payload": "' UNION SELECT username, password FROM users --"
  },
  "cvss_estimate": 8.6,
  "poc_status": "pending",
  "expert_skill": "injection-audit",
  "timestamp": "2026-06-15T..."
}
```

Severity enum: `critical | high | medium | low | info`
poc_status enum: `pending | verified | rejected | skipped`

Critical/High findings must set `poc_status: "pending"` so `poc-monitor.py` picks them up.

### Self-Evolution Integration

Expert doesn't call self_evolution directly. Experience flows:
1. Expert writes findings to Memurai `{groupId}:audit:finding:{chainId}:draft`
2. Supervisor promotes to `final` and writes `{groupId}:sup:exp:{chain_id}`
3. Daemon's `self_evolution.merge_knowledge_from_memurai` pulls into `knowledge.json`
4. Next round's expert loads `knowledge.json` at startup via:

```python
import json
k = json.load(open(f"{loop_audit_dir}/knowledge.json"))
for past_finding in k.get("findings", []):
    if past_finding["vuln_type"] == my_vuln_type:
        add_to_context(past_finding["evidence"])  # learn from past
```

### Tool Priority

- **Codegraph SQL** for all chain/edge queries (NEVER ast-grep for call graph walking)
- **ast-grep** only for initial annotation discovery (Phase A)
- **Memurai** for cross-agent state sharing
- **JavaParser service** (via javaparser-service.jar) for third-party call resolution

### Hard Constraint: non-groupId = sink

`method_calls_extractor.py` L102: `return not called_fqn.startswith(group_id + ".")`. All findings must identify at least one sink matching this rule. If the vulnerability is purely internal (no third-party sink), expert must explicitly set `sinks: []` and mark `vuln_type` as `internal_logic_flaw` or `business_rule_violation`.

### Expert-Specific: injection-audit

Specializes in finding vulnerabilities where user input flows into dangerous sinks:
- SQL injection (JDBC, MyBatis, JPA criteria with string concat, ORDER BY dynamic)
- Command injection (Runtime.exec, ProcessBuilder with unescaped args)
- XXE (XML parsers with external entities enabled)
- Expression injection (SpEL, MVEL, EL with user-controlled expressions)
- SSRF (URL/URI constructor with user-controlled host/path)
- Deserialization (ObjectInputStream, JSON with type info)

Prioritize sinks from `07-Sink表.json` by severity (critical first), then walk taint flow backward through `chain_data.chain` to find where user input enters.

# 注入类漏洞审计

## 思考规范

输入已经是完整调用链（入口 → 中间处理 → sink），重点是：

1. **追踪数据流的每一层处理**：调用链上经过了几层转换？每一层做了什么？处理是否真的消除了注入可能？
2. **审视每一层防御**：开发者一定做了处理，问题是够不够。不要假设"用了框架就安全"，看具体实现。

## 审计流程（3 步强制执行，不可跳过）

### 1. 防御审计（arthas 强制）

逐层检查调用链上每一层处理的实际效果，用 arthas 验证：
- 过滤是否完整
- 转义是否在正确上下文
- 参数化是否真的用了
- WAF 规则是否能被绕过

用 `watch` 观察每层处理方法的输入输出，确认数据被正确处理而非只是被声明处理。

```bash
# 追踪调用链上每个处理方法的实际参数
trace 目标类 目标方法

# 观察处理方法的输入输出
watch 目标类 处理方法 '{params, returnObj}'
```

### 2. 构造与验证

针对调用链中的防御薄弱点，构造最小 payload：
- curl 触发，arthas watch sink 方法
- 确认 sink 收到的参数中包含未经处理的用户输入
- 确认 payload 确实改变了执行逻辑
- 失败时：记录绕过尝试和失败原因，用 arthas trace 定位防御生效的具体层

### 3. 结论一行

注入类型、是否有注入、防御缺陷、证据位置。

## 常见坑（按注入类型，必须逐一检查）

### SQL 注入
- 无法参数化的子句：ORDER BY、LIMIT、IN 列表
- 存储过程内部动态拼接
- 二阶注入：数据先存储，再次查询时拼入 SQL
- ORM 动态查询（hibernate createQuery、MyBatis `${}`）与参数化查询的区别
- LIKE 查询通配符未转义

### 命令注入
- 引号内注入：参数在双引号内仍可注入
- 命令分隔符：`;` `&&` `||` `` ` `` `$()` 管道
- 空格绕过替代语法：`$IFS`、`{cmd,arg1,arg2}`
- 路径绕过：编码、相对路径

### XXE
- 默认配置不禁用 external entities，需显式禁用
- 只在某个 parser 实例禁了，漏了另一个
- 嵌套 XML 处理（SOAP、SAML）内层 parser 未被保护
- Blind XXE 需要带外验证（dnslog / HTTP 回连）

### 反序列化
- 不只是 Java 原生序列化：JSON（fastjson）、YAML（snakeyaml）、Protobuf、XML（XStream）都有攻击面
- 类型标识字段（`@type`、`@class`）可被用于加载任意类
- 过滤了特定 gadget 类名，但新依赖引入了新 gadget

### 表达式注入（SpEL / OGNL）
- 注解中包含用户可控数据
- 标签属性中表达式求值
- Spring Security 安全表达式中方法参数来自用户输入

### 模板注入（SSTI）
- 用户输入直接传入模板 render——是表达式求值，不只是参数替换
- 开发者误以为是字符串插值
- 不同引擎安全沙箱能力差异大（Jinja2 沙箱 vs 无沙箱 FreeMarker）

### LDAP 注入
- 搜索条件拼接：`(uid=*)` 型绕过
- 特殊字符（`*`、`(`、`)`、`\`、NUL）未转义
- base DN 注入

### NoSQL 注入
- 查询条件构造为对象时，输入是操作符对象（如 `{$gt: ""}`）导致条件绕过
- HTTP JSON 参数直接传给查询方法未扁平化

## 禁止行为
- 假设框架自动防御（必须验证实际代码，不能凭框架名声称安全）
- 只看过滤/转义逻辑不看调用链上下文（同一转义函数在不同位置效果不同）
- 忽略二阶注入和存储型注入
```
<!-- OMO_INTERNAL_INITIATOR -->