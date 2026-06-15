---
name: login-audit
description: "登录接口漏洞审计子 agent 专用 skill。当目标 API 是登录接口（业务中也涉及登录验证时触发）。审计凭证校验、会话管理、暴力破解防御、响应差异等登录类风险。触发词：'登录接口审计'、'login audit'、'暴力登录'、'login api audit'。"
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

### Expert-Specific: login-audit

Specializes in authentication endpoint vulnerabilities:
- Brute force (no rate limiting, no account lockout)
- Credential leakage (password in URL, in logs, in response)
- Session management (predictable session IDs, no Secure flag on cookie)
- MFA bypass (MFA not required for sensitive operations)
- Password reset flaws (weak token, no expiry, user enumeration)

For login endpoints, `analyst_result.dimensions.input_tracing` is almost always `user_controlled` — focus analysis on output side (error messages revealing user existence, password in error response).

# 登录接口漏洞审计

## 适用范围

- 登录接口（/login、/auth、/signin、/token 及凭证验证端点）
- 业务中也涉及登录验证的环节（支付前的身份验证、操作步骤前的确认、SSO 回调验证）

## 审计流程（4 步强制执行，不可跳过）

### 1. 登录流观察（arthas 强制）

用 arthas `watch` 登录处理方法，观察：
- 实际接收到的参数（用户名、密码、验证码、其他字段）
- 凭证校验的完整执行路径（解析、比对、二次验证）
- 成功/失败时的返回差异（响应体、状态码、响应时间）

### 2. 凭证校验审计

从凭证接收到验证完成的完整路径：
- 密码比对方式：明文 / 哈希 / 加密？
- 是否 constant-time 比较？
- 校验失败时是否暴露失败原因（用户名不存在 vs 密码错误）？
- 是否存在默认凭证据/测试账号？

### 3. 反暴力攻击机制

用 arthas 验证机制是否真实有效：
- 频率限制：是否跨 IP / 跨用户 / 跨设备，是否可用代理绕过
- 验证码：是否有效校验（后端校验 vs 仅前端、验证码复用、验证码绕过、参数篡改）
- 账号锁定：临时锁定 vs 永久锁定？锁定范围（账号 vs IP vs 设备）？
- 找回密码：是否能被绕过（直接调用登录成功接口）？

### 4. 会话与凭证管理

登录成功后：
- 是否 regenerate session ID（防 session fixation）？
- token/session 的生成方式：随机性、强度、有效期、安全属性（Secure/HttpOnly/SameSite）
- 多设备登录是否互踢？
- 登录凭证是否在响应中泄露（token 在 URL 中、敏感信息在日志中）？

## 常见坑（登录类，逐一检查）

### 响应差异导致用户枚举
- 用户名存在/不存在时返回不同的错误信息 → 用户枚举
- 用户名存在时响应时间更长 → 用户枚举（timing attack）
- 用户名存在时 HTTP 状态码不同（404 vs 401）

### 凭证传输与存储
- 密码传输过 TLS 之外的通道
- 密码的存储方式不安全（明文、MD5/SHA 无 salt）
- 只比对哈希前 N 位
- 密码长度或字符集限制不合理（拒绝攻击）

### 反暴力机制失效
- 频率限制只统计失败次数，成功登录后清零 → 每次成功可重放
- IP 白名单可通过 X-Forwarded-For 伪造绕过
- 分布式环境下各节点计数不同步
- 验证码可被 OCR 自动化绕过

### 会话漏洞
- 登录后不刷新 session ID → session fixation
- 旧有效 session 未失效机制
- token 有效期过长或无过期
- 登出使 token 失效的功能未真正实现

### MFA 绕过
- 验证码 MFA 可被绕过（通过直接调用"验证通过"接口跳过 MFA）
- MFA 验证码长度太短（4-6 位）可被暴力破解
- MFA 验证码有效期过长
- 备用恢复渠道频率限制缺失

### 其他
- "记住我"功能的 token 可被预测
- OAuth 登录的回调未验证 state/nonce
- 登录接口的响应中包含敏感信息（用户 ID、角色、内部字段等）
- 日志中记录了密码

## 禁止行为
- 不实际触发登录流就下结论（必须 arthas 验证实际执行路径）
- 只测正常路径不测异常路径（频率限制、锁定、验证码失效等）
- 忽略响应中的时间和状态码差异（信息泄露的重要来源）
- 只验证登录成功的接口，不测试失败路径（失败路径的信息泄露更常见）
```
<!-- OMO_INTERNAL_INITIATOR -->