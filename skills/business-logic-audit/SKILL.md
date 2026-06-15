---
name: business-logic-audit
description: "业务逻辑漏洞审计子 agent 专用 skill。审计竞态条件、流程绕过、状态篡改、数值边界、批量赋值、越权操作等业务层漏洞。触发词：'业务逻辑审计'、'business logic audit'、'逻辑漏洞'、'审计业务流程'、'流程绕过'。"
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

### Expert-Specific: business-logic-audit

Specializes in finding vulnerabilities with no technical sink, but flawed business rules:
- IDOR (using user-supplied ID without ownership check)
- Race conditions (double-spend, TOCTOU)
- State bypass (changing order state without proper checks)
- Numeric overflow (negative quantity, int overflow, currency precision)
- Mass assignment (exposing internal fields via reflection or binding)

Reference `06-通用安全知识.md` §5 (业务逻辑漏洞模板) for common patterns. Analyst result's `branch_logic` field (race_condition | state_bypass | numeric_overflow) directly signals which pattern to look for.

# 业务逻辑漏洞审计

## 输入

上游提供的业务流程描述或接口调用链。

## 审计流程（3 步强制执行，不可跳过）

### 1. 状态流还原（arthas 强制）

用 arthas 追踪业务流程的实际执行路径，确认：
- 每一步状态变更是否有对应的服务端校验
- 业务步骤是否有被跳过的可能（直接调用后续步骤接口）
- 幂等操作：多次执行结果是否一致

用 `watch`/`trace` 观察关键业务方法的调用链、参数、异常处理。

### 2. 边界与约束审计

检查每个业务约束的实际执行情况，用 arthas 验证实际执行：
- 数值边界：最小值、最大值、精度、单位是否一致
- 频率限制：是否有效、是否可被绕过（并发/重试）
- 业务规则：后端是否做了与前端相同的校验（不能只前端校验）
- 状态机：状态转换是否有非法跳跃的可能
- 并发：多个实例同时操作同一资源时是否一致
- 时间窗口：活动时间是否在前后端一致校验

### 3. 构造与验证

针对发现的约束薄弱点，构造最小 payload：
- curl 构造并发请求 / 边界数据 / 越权 ID
- arthas watch 业务关键方法
- 确认业务约束确实被绕过
- 失败时：记录尝试和失败原因

## 常见坑（业务逻辑类，逐一排查）

### 竞态条件（Race Condition / TOCTOU）
- 下单/查价到扣款之间的时间窗
- 并发请求的"先查后补"重复执行
- 幂等性缺失：重复提交导致重复扣款/重复发货
- 分布式锁的粒度问题或漏用

### 流程绕过
- 支付步骤被绕过，直接访问发货接口
- 多步流程中某一环节失败后的未回滚/跳过
- 通过非业务入口（直接调用内部接口）绕过审核
- 状态机不严谨：直接跳跃式更新状态（"待支付"→"已发货"）

### 状态篡改
- 客户端携带状态参数且服务端未校验
- 状态字段无鉴权保护，任意用户可修改他人订单状态
- 历史状态可被重新触发（已取消的订单重新支付/记录重新激活）

### 数值计算
- 数量、价格、汇率、积分等数值字段的数据类型和范围
- 浮点精度问题（0.1 + 0.2 != 0.3，用 BigDecimal 还是 float）
- 金额边界：负数、零、超大值
- 优惠券/折扣叠加规则漏洞
- 金额拆分：总额 = 分项之和，是否一致
- 不同货币单位（分 vs 元）未统一

### 批量赋值 / 越权
- 批量接口写了不该由客户端控制的字段（价格、角色、会员等级）
- IDOR 遍历：通过递增/猜测 ID 访问其他用户/租户资源
- 管理接口的鉴权验证缺失或不完整

### 频率 / 额度
- 频率限制只做了单 IP，未考虑用户维度
- 额度/积分/代金券限制只做了单实例，跨实例无效
- 充值、消费、提现的金额上下限控制
- 跨用户/跨租户的独立性（A 用户的行为不影响 B 用户的额度）

### 时间类
- 活动时间校验可被绕过（客户端时间、服务器时区不一致）
- 时间窗口错位：活动已结束但缓存未失效
- 跨时区 / 夏令时 处理冲突

## 禁止行为
- 只看前端校验，验证后端实际执行的约束
- 只测正常流程（必须测试异常/并发/边界）
- 假设频率控制/额度限制生效（必须验证实际执行）
- 忽略任何字段的客户端可控性（客户端可发送任意字段，服务端必须拒绝或忽略）
- 假设状态机只有合法转移路径（任意两个状态间都可能有非法转移）
```
<!-- OMO_INTERNAL_INITIATOR -->