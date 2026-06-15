---
name: auth-chain-audit
description: "Filter/Interceptor 认证鉴权链审计子 agent 专用 skill。通过 arthas 审计运行时 filter 链加载顺序、路径归一化差异、校验逻辑缺陷。支持认证类型：JWT、OAuth2、SSO、Session、Basic Auth、自定义认证。触发词：'审计 filter'、'检查拦截器'、'filter chain audit'、'interceptor 审计'、'authentication audit'、'鉴权链检查'、'路径绕过审计'。"
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

### Expert-Specific: auth-chain-audit

Specializes in authorization/authentication bypass:
- Filter ordering attacks (authentication after authorization)
- JWT weaknesses (alg=none, weak secret, no expiry validation)
- OAuth2 misconfig (wrong redirect_uri validation, token reuse)
- Session fixation (session ID from URL parameter)
- SSO bypass (missing validation of external identity provider)

Reference `security-context.json` (from Phase B) for the actual `SecurityFilterChain` beans. Analyst's `authorization` field of `missing_or_bypassable` is a high-priority signal.

# Filter/Interceptor 认证鉴权链审计

## 审计流程（5 步，不可跳过）

### 1. 运行时 Filter Chain 发现（arthas 强制）

通过 arthas 获取**运行时真实**的 filter/interceptor 加载顺序：
- `sm` / `sc` 命令定位 `FilterChainProxy` / `FilterRegistrationBean`
- `watch` 获取 `getFilters()` / `getInterceptors()` 的运行时返回值
- 对比声明顺序与实际执行顺序

关注点：
- auth filter 是否在 CORS filter 之前执行？
- logging/error filter 是否吞掉了 auth 异常导致绕过？
- Spring Security 内置 filter 位置是否正确？

### 2. 路径归一化差异审计

逐层检查路径处理差异：

**Nginx 层：**
- `location` 匹配模式（前缀/正则/精确）
- `rewrite` 规则是否会吃掉路径特殊字符
- `proxy_pass` 是否带 trailing slash（路径拼接差异）

**Tomcat 层：**
- `/..;/` 处理（CVE-2018-11759 类问题）
- `;jsessionid=xxx` 分号参数是否从路径剥离后再匹配
- URL 编码处理（`%2f` → `/`，`%5c` → `\`）

**Spring 层：**
- `ant_path_matcher` vs `path_pattern_parser`（Spring 5.3+）的行为差异
- `/**` 与 `/*` 匹配范围差异
- trailing slash 默认行为（`/api/user` vs `/api/user/`）

**跨层对比（必做）：**
用 curl 发出不同路径变体请求，arthas watch filter 的 `doFilter`/`preHandle` 是否真的被触发。

### 3. 校验逻辑审计

逐个检查每个 auth/authz filter 的校验实现：

**JWT：**
- 算法：是否允许 `alg=none`？alg 与 key 是否匹配？
- 密钥：硬编码？强度？环境变量可能为空？
- 有效期：是否检查 exp/iat/nbf claim？
- 签名：真的 verify 了还是只 decode 不 verify？

**OAuth2：**
- token 校验用的是本地验证还是内省（introspection）降级？
- audience/issuer 校验是否完整？
- PKCE 是否强制？（针对 public client）
- refresh token 是否可被重放？
- state 参数是否验证？（防 CSRF）

**Session：**
- 登录成功后是否 regenerate session ID？（session fixation）
- session 超时/登出/多设备互踢是否完整？

**Basic Auth / 自定义认证：**
- 是否使用时间安全的比较函数（constant-time comparison）？
- 对比密文/签名/哈希：是否校验完整长度？
- 密码存储用的是 bcrypt/scrypt/argon2 还是裸 hash？
- 隐式放行问题：没有配置 = 默认允许？

**SSO（OIDC/SAML）：**
- OIDC 的 state/nonce 是否校验？
- SAML 签名是否验证完整（还是只验证部分 XML）？
- SAML 的 XML Signature Wrapping 攻击面？

### 4. 路径绕过构造（如前 3 步发现可疑点）

针对路径归一化差异：
- 用 arthas `watch` 目标 filter 的 `doFilter`/`preHandle`，记录实际 requestURI
- 构造多个路径变体，对比 filter 是否被触发
- 如有绕过：记录构造的 payload 和 arthas 捕获的证据

### 5. 审计结论

每个发现项：
- 风险点一句话描述
- 技术证据（arthas 输出 / 代码片段）
- 严重程度：高 / 中 / 低
- 修复方向一句话

## 输出格式

### 运行时 Filter 链
[实际加载顺序，声明顺序的差异]

### 路径归一化差异
| 层 | 发现 |
|----|------|
| Nginx | [结论] |
| Tomcat | [结论] |
| Spring | [结论] |
| 跨层风险 | [结论] |

### 校验逻辑发现
[逐个 filter 的审计结果]

### 发现汇总
| # | 风险点 | 证据 | 严重度 | 修复方向 |
|---|-------|------|--------|---------|

## 禁止行为
- 不通过 arthas 获取运行时状态，只看代码声明
- 只检查一个路径变体
- 跳过任何一种认证类型的校验逻辑审计
```
<!-- OMO_INTERNAL_INITIATOR -->