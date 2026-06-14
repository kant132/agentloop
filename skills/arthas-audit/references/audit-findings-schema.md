# Audit Findings Schema (v1.0)

Arthas 运行时审计的结构化输出规范。所有审计结果**必须**写入 `output/audit-findings.json`，供人工审阅、java-security-audit 集成、skill-mother 评分以及下游自动化消费。

## 1. 文件约定

| 项目 | 约定 |
|------|------|
| 主文件 | `output/audit-findings.json`（UTF-8, 缩进 2） |
| 证据目录 | `output/evidence/` — 大型 Arthas 输出保存为文本文件，finding 引用 |
| 静态联动 | 可选输入：`output/phase4-api-audit.md` 或 `output/phase4-api-audit.json` |
| 生成工具 | `scripts/audit_finding.py`（init / add / finalize / validate / export） |
| 幂等性 | `add` 追加；`finalize` 重新计算 summary；`validate` 只读；文件已存在则读写 |

## 2. 顶层结构

```json
{
  "schema_version": "1.0",
  "metadata": { ... },
  "jvm_snapshot": { ... },
  "findings": [ ... ],
  "summary": { ... },
  "artifacts": { ... }
}
```

## 3. 各节点详解

### 3.1 metadata

记录本次审计会话的上下文信息，**不可变**（写一次后不再改）。

```json
{
  "session_id": "audit-20260602-103012-mgmt01",
  "started_at": "2026-06-02T10:30:12Z",
  "ended_at": "2026-06-02T11:23:44Z",
  "auditor_model": "claude-sonnet-4-20250514",
  "auditor_identity": "skill-mother-audit",
  "arthas_exec_version": "1.0.0",

  "target": {
    "app_name": "order-service",
    "agent_id": "order_svc_mgmt01_32656",
    "ssh_alias": "mgmt-01",
    "namespace": "prod",
    "pod_name": "order-service-7b8d9f-5xk2p",
    "container": "order-service"
  },

  "arthas_tunnel": {
    "web_endpoint": "http://10.0.1.50:8080",
    "ws_endpoint": "ws://10.0.1.50:7777/ws"
  },

  "workflows_executed": [
    "workflow_0_probe",
    "workflow_1_taint_verification",
    "workflow_2_auth_bypass",
    "workflow_4_runtime_config_audit"
  ],

  "input_static_report": "output/phase4-api-audit.json",
  "output_file": "output/audit-findings.json",
  "evidence_dir": "output/evidence/"
}
```

**字段规则**：
- `session_id` 必须唯一（推荐格式 `audit-YYYYMMDD-HHMMSS-<host_short>`）
- `target.*` 与 arthas-deploy 的 `arthas-config.json` 保持一致
- `workflows_executed` 必须是 SKILL.md 中定义的工作流标识子集

### 3.2 jvm_snapshot

审计开始时的 JVM 指纹，用于环境漂移检测（skill-mother D4）。

```json
{
  "arthas_version": "4.0.5",
  "java_version": "17.0.9+9-LTS",
  "java_vendor": "Eclipse Adoptium",
  "java_home": "/opt/java/openjdk",
  "start_arguments": [
    "-Xms512m", "-Xmx2g",
    "-Dspring.profiles.active=prod",
    "-Dserver.port=8080"
  ],
  "class_path_jars": 187,
  "memory_bytes": {
    "heap_used": 524288000,
    "heap_max": 2147483648,
    "non_heap_used": 104857600
  },
  "thread_count_active": 142,
  "gc_young_count": 3421,
  "gc_old_count": 17,
  "classloader_count": 23,
  "captured_at": "2026-06-02T10:30:44Z"
}
```

**字段规则**：
- 数据通过 `jvm` / `dashboard -n 1` / `memory` / `classloader -l` 命令采集
- 所有数值必须是整数
- `class_path_jars` 计数 `sysprop java.class.path` 分割后的条目数

### 3.3 findings[] — 核心节点

每个 finding 代表一个被审计的安全可疑点，**必须包含**以下字段：

```json
{
  "id": "F-001",
  "category": "A03:2021-Injection",
  "vulnerability_type": "SQL Injection",
  "severity": "CRITICAL",
  "status": "RUNTIME-CONFIRMED",
  "confidence": "HIGH",

  "taint_flow": { ... },
  "evidence": [ ... ],
  "call_stack": [ ... ],
  "static_reference": { ... },
  "runtime_target": { ... },
  "poc": { ... },

  "recommendation": "Use PreparedStatement with parameterized queries.",
  "detected_at": "2026-06-02T10:45:23Z",
  "detected_by": "workflow_1_taint_verification"
}
```

#### 3.3.1 核心字段定义

| 字段 | 类型 | 必填 | 取值 |
|------|------|------|------|
| `id` | string | ✔ | `F-XXX` 三位数字，自增，全局唯一 |
| `category` | string | ✔ | [OWASP Top 10 2021](#owasp-categories) 之一，或 `Custom-<Name>` |
| `vulnerability_type` | string | ✔ | 具体漏洞类型（如 `SQL Injection`、`JWT Bypass`、`Webshell`） |
| `severity` | enum | ✔ | `CRITICAL` / `HIGH` / `MEDIUM` / `LOW` / `INFO` |
| `status` | enum | ✔ | `RUNTIME-CONFIRMED` / `RUNTIME-DENIED` / `RUNTIME-DIVERGENCE` / `RUNTIME-BLOCKED` |
| `confidence` | enum | ✔ | `HIGH`(多证据+stack+poc) / `MEDIUM`(2+证据) / `LOW`(单证据或推断) |

#### 3.3.2 taint_flow（污点流）— Source → Sink 模型

```json
{
  "source": {
    "class": "com.example.web.UserController",
    "method": "login",
    "parameter": "params[0]",
    "parameter_name": "username",
    "protocol": "HTTP",
    "endpoint": "POST /api/login"
  },
  "sink": {
    "class": "java.sql.Statement",
    "method": "executeQuery",
    "call_site": "com.example.dao.UserDao.findByName:23"
  },
  "intermediate_calls": [
    "com.example.service.UserService.handleLogin(UserService.java:78)",
    "com.example.dao.UserDao.findByName(UserDao.java:23)"
  ],
  "taint_transforms": [
    "无转义直接拼接：\"SELECT * FROM users WHERE name='\" + username + \"'\""
  ]
}
```

**字段规则**：
- `source` 必须能从 `watch` / `stack` 命令复现
- `sink` 必须是 JDK 或第三方危险方法
- `intermediate_calls` 来自 `stack` 输出，按调用顺序
- `taint_transforms` 是字符串数组，描述数据如何变形

#### 3.3.3 evidence[]（证据链）

```json
[
  {
    "id": "E-001",
    "command": "watch java.sql.Statement execute* '{params}' -x 3 -n 3",
    "tool": "arthas_exec.py",
    "command_output_status": "OK",
    "captured_at": "2026-06-02T10:46:12Z",
    "duration_ms": 1240,
    "retries": 0,
    "output_excerpt": "params[0] = \"SELECT * FROM users WHERE name='admin' OR 1=1 --'\"",
    "output_hash": "sha256:a3f1e2...",
    "full_output_file": "output/evidence/F-001-E-001.txt",
    "evidence_type": "dynamic_capture",
    "significance": "HIGH",
    "interpretation": "用户输入 'admin' OR 1=1 -- 被原样拼入 SQL 语句"
  },
  {
    "id": "E-002",
    "command": "stack java.sql.Statement execute* -n 3",
    "tool": "arthas_exec.py",
    "command_output_status": "OK",
    "captured_at": "2026-06-02T10:46:15Z",
    "duration_ms": 850,
    "retries": 0,
    "output_excerpt": "ts=2026-06-02 10:46:15;thread_name=http-nio-8080-exec-3;...",
    "output_hash": "sha256:b7c2d1...",
    "full_output_file": "output/evidence/F-001-E-002.txt",
    "evidence_type": "stack_trace",
    "significance": "HIGH",
    "interpretation": "完整调用栈：UserController.login → UserService.handleLogin → UserDao.findByName"
  }
]
```

**字段规则**：
- `id` 格式 `E-XXX`，**在每个 finding 内自增**
- `evidence_type` 枚举：`dynamic_capture`(watch/tt) / `stack_trace`(stack) / `trace_timeline`(trace) / `decompiled_code`(jad) / `class_discovery`(sc) / `classloader_inspection` / `memory_analysis`(vmtool heapAnalyze) / `config_extraction`(getstatic/vmtool getInstances) / `poc_execution`(ognl/tt replay) / `other`
- `output_excerpt`：输出前 500 字符（内部系统保留原文）
- `output_hash`：完整输出的 SHA256（用于不可变性）
- `full_output_file`：完整输出文件路径（> 500 字符必须存文件）
- `significance`：`HIGH`(直接证据) / `MEDIUM`(辅助证据) / `LOW`(上下文)

#### 3.3.4 call_stack[]（调用栈）

```json
[
  "com.example.web.UserController.login(UserController.java:45)",
  "com.example.service.UserService.handleLogin(UserService.java:78)",
  "com.example.dao.UserDao.findByName(UserDao.java:23)",
  "java.sql.Statement.executeQuery(Statement.java:native)"
]
```

**字段规则**：
- 来自 `stack` 命令输出，按自上而下顺序
- 行号 `??` 或 `native` 表示无法确定
- 只保留业务相关栈帧，去掉 `java.lang.reflect.*` / `sun.reflect.*` 等 JDK 内部

#### 3.3.5 static_reference（静态分析联动）

```json
{
  "source_tool": "java-security-audit",
  "source_version": "1.2.0",
  "finding_id": "SQL-001",
  "source_file": "output/phase4-api-audit.json",
  "source_phase": "phase4-api-audit",
  "source_line_hint": 45,
  "source_excerpt": "UserController.login: String query = \"SELECT * FROM users WHERE name='\" + username + \"'\"",
  "linked_at": "2026-06-02T10:45:00Z"
}
```

**字段规则**：
- 若无静态分析联动则省略此字段
- `source_excerpt`：原文摘要（≤ 200 字符）
- `linked_at`：与静态 finding 建立关联的时间戳

#### 3.3.6 runtime_target（运行时目标）

```json
{
  "class": "com.example.dao.UserDao",
  "method": "findByName",
  "classloader_hash": "3d4eac69",
  "classloader_name": "org.springframework.boot.loader.LaunchedURLClassLoader",
  "classloader_urls": [
    "file:/app/BOOT-INF/classes/",
    "file:/app/BOOT-INF/lib/spring-jdbc-6.1.4.jar"
  ],
  "decompile_status": "MATCH",
  "decompile_diff_excerpt": "Source and bytecode identical — no divergence"
}
```

**字段规则**：
- `classloader_hash` 必须先用 `sc -d` 或 `classloader -l` 获取后再使用
- `decompile_status` 枚举：`MATCH`(一致) / `DIVERGENT`(不一致) / `PARTIAL`(部分不一致) / `FAILED`(反编译失败)
- `decompile_diff_excerpt`：若 `DIVERGENT`，给出关键差异片段

#### 3.3.7 poc（验证载荷）

```json
{
  "executed": true,
  "confirmed_by_user": true,
  "payload": "admin' OR 1=1 --",
  "method": "HTTP POST /api/login",
  "request_headers": {"Content-Type": "application/json", "Authorization": "Bearer eyJhbGc..."},
  "result": "绕过了身份验证，返回全部 3,847 条用户记录",
  "risk_demonstrated": "攻击者可一次性提取整个用户数据库",
  "executed_at": "2026-06-02T10:55:42Z",
  "tt_index": 1003
}
```

**字段规则**：
- `executed` 为 `false` 时，`payload`/`method`/`result` 必须省略
- `confirmed_by_user` 必须为 `true` 才能记录执行（强制规则）
- `request_headers`：保存完整请求头
- `tt_index`：若使用 `tt -i` 重放验证，记录 INDEX

### 3.4 summary（自动聚合）

由 `audit_finding.py finalize` 计算，**人工不应手动写**。

```json
{
  "total_findings": 12,
  "total_evidence_items": 29,
  "total_commands_executed": 47,

  "by_status": {
    "RUNTIME-CONFIRMED": 5,
    "RUNTIME-DENIED": 3,
    "RUNTIME-DIVERGENCE": 2,
    "RUNTIME-BLOCKED": 2
  },

  "by_severity": {
    "CRITICAL": 2,
    "HIGH": 3,
    "MEDIUM": 4,
    "LOW": 2,
    "INFO": 1
  },

  "by_category": {
    "A03:2021-Injection": 3,
    "A07:2021-Identification and Authentication Failures": 3,
    "A01:2021-Broken Access Control": 2,
    "A08:2021-Software and Data Integrity Failures": 2,
    "Custom-Dynamic Class Loading": 2
  },

  "unique_targets": [
    "com.example.web.UserController",
    "com.example.security.JwtFilter",
    "com.example.service.FileService"
  ],

  "static_linkage_rate": 0.67,

  "audit_duration_seconds": 382,
  "total_token_estimate": 42000
}
```

**计算规则**：
- `static_linkage_rate` = 有 `static_reference` 的 finding 数 / 总 finding 数
- `total_token_estimate` = 所有 evidence 的 `duration_ms` 总和 × 200（粗略估算）
- `unique_targets` = 所有 finding 的 `runtime_target.class` 去重

### 3.5 artifacts（收尾产物）

```json
{
  "cleanup_performed": true,
  "cleanup_commands": [
    {"command": "reset -E '.*'", "status": "OK", "at": "2026-06-02T11:23:00Z"},
    {"command": "tt --delete-all", "status": "OK", "at": "2026-06-02T11:23:05Z"}
  ],
  "agent_stopped": false,
  "tunnel_kept_alive": true
}
```

**字段规则**：
- `cleanup_performed` 必须为 `true` 才能 final 通过
- `agent_stopped` = `true` 表示执行了 `arthas("stop")`

## 4. OWASP Top 10 2021 类别表

`finding.category` 的合法枚举值：

| 编码 | 类别 | 中文 |
|------|------|------|
| A01:2021 | Broken Access Control | 失效的访问控制 |
| A02:2021 | Cryptographic Failures | 加密机制失效 |
| A03:2021 | Injection | 注入 |
| A04:2021 | Insecure Design | 不安全设计 |
| A05:2021 | Security Misconfiguration | 安全配置错误 |
| A06:2021 | Vulnerable and Outdated Components | 存在漏洞的/过时的组件 |
| A07:2021 | Identification and Authentication Failures | 身份识别与身份验证失败 |
| A08:2021 | Software and Data Integrity Failures | 软件与数据完整性故障 |
| A09:2021 | Security Logging and Monitoring Failures | 安全日志与监控失败 |
| A10:2021 | Server-Side Request Forgery (SSRF) | 服务端请求伪造 |
| Custom-* | 自定义类别（动态类加载、敏感信息泄露等） | 自定义 |

## 5. 状态语义详解

| 状态 | 含义 | 触发条件 | 典型 evidence_type |
|------|------|---------|-------------------|
| `RUNTIME-CONFIRMED` | 动态证据确凿 | watch 看到污点 / stack 看到调用路径 / PoC 成功 | dynamic_capture, stack_trace, poc_execution |
| `RUNTIME-DENIED` | 动态测试未发现该行为 | watch 未捕获（3+ 次尝试）/ 类不存在 / 方法未被调用 | dynamic_capture(空输出), class_discovery |
| `RUNTIME-DIVERGENCE` | 源码 vs 字节码不一致 | jad 输出与源文件比对有明显差异 | decompiled_code |
| `RUNTIME-BLOCKED` | 动态执行被 Filter/防御拦截 | stack 显示请求在 Filter 链被拒绝 / watch 返回拒绝响应 | stack_trace, dynamic_capture |

## 6. 完整示例

见 [`examples/audit-findings-sample.json`](examples/audit-findings-sample.json)（含 3 个典型 finding：SQL 注入、JWT 绕过、动态类发现）。

## 7. 与 java-security-audit 的集成协议

### 7.1 静态 → 动态（输入）

1. 读 `output/phase4-api-audit.json`（若存在）
2. 对每个静态 finding，创建关联的运行时 finding（`static_reference` 链接）
3. 在 runtime finding 上执行对应工作流
4. 根据结果设置 `status`：
   - 找到证据 → `RUNTIME-CONFIRMED`
   - 未找到 → `RUNTIME-DENIED`
   - 静态与动态不一致 → `RUNTIME-DIVERGENCE`
   - 被防御拦截 → `RUNTIME-BLOCKED`

### 7.2 动态 → 静态（输出）

生成 `output/phase5-arthas-audit.json`，结构与 `audit-findings.json` 相同，可作为 java-security-audit 的输入继续处理。

## 8. 敏感信息处理

**内部系统，不脱敏**。所有动态捕获的输出（数据库连接串、JWT secret、API key、明文密码、请求头、响应体）**保留原文**。

**例外（仅在以下场景启用脱敏）**：
- 报告需对外分享（客户、第三方审计公司、公开披露）
- 包含真实客户 PII（身份证号、手机号、银行卡号）的 PoC 结果

**脱敏方式（仅例外场景）**：
- 整值替换为 `[REDACTED]`
- 脱敏前在 `artifacts` 节点记录 `redaction_count` 和 `redaction_types`

## 9. 版本演进

| 版本 | 变更 |
|------|------|
| 1.0 | 初始版本（本规范）|

`schema_version` 字段允许工具校验不兼容升级。
