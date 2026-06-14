---
name: arthas-audit
version: 1.0.0
description: "Arthas 远程 Java 安全审计与 PoC 验证 skill。通过 arthas-tunnel-server 连接远程 Arthas agent，在运行时对 Java 应用进行动态分析：反编译字节码、追踪调用栈、观察方法执行、查看变量、寻找动态调用、发现动态加载类、验证 PoC 漏洞。本 skill 是 java-security-audit 的运行时伴侣，用于把静态分析可疑点转化为动态可执行证据。Triggers: 'arthas 审计', 'arthas poc', '运行时验证', '动态分析', '反编译 jad', '观察 watch', '调用栈 stack', '时间隧道 tt', 'ognl 验证', '动态类', '运行时审计'."
tools: [Read, Bash, Write, Glob]
depends: [ssh-skill]
---

# Arthas 远程安全审计

通过 arthas-tunnel-server（由 arthas-deploy 建立）连接远程 Java 进程上的 Arthas agent，在运行时执行动态安全审计。

```
本 skill            tunnel-server         Java 进程
arthas-audit  ───►  arthas-deploy  ───►  + Arthas agent
  HTTP/API          :8080 /proxy/<aid>/    + 业务代码
                    (WS cmd)
```

## 前置条件

1. `output/arthas-config.json` 存在且 `status == "active"`（arthas-deploy 产出）
2. 目标 agent 的 `connected == true`
3. 已有目标 Java 进程的上下文（来自 java-security-audit 静态分析更佳）

**必须先跑 probe 才能进入审计**：`python {skill_dir}/scripts/arthas_exec.py probe`

## 调用方式

**唯一入口**：`scripts/arthas_exec.py`（封装了认证、重试、超时、结构化错误码）。

```bash
# 单条命令（原始文本输出）
python {skill_dir}/scripts/arthas_exec.py exec "version"
python {skill_dir}/scripts/arthas_exec.py exec "jad com.example.JwtFilter doFilter" --timeout 60

# JSON 输出（下游消费）
python {skill_dir}/scripts/arthas_exec.py exec "sc com.example.*" --json

# 批量（JSON 数组文件）
python {skill_dir}/scripts/arthas_exec.py batch audit-plan.json

# Workflow 0 前置探测
python {skill_dir}/scripts/arthas_exec.py probe
```

| exit code | 含义 | 处理 |
|-----------|------|------|
| 0 | 成功 | 解析 stdout 作为 Arthas 输出 |
| 10 | tunnel 不通 | 重跑 arthas-deploy |
| 11 | 命令超时 | 加 `--timeout` 或缩 `-n` |
| 12 | 认证失败 | 用 `arthas-deploy/scripts/arthas_auth.py login` 重新登录 |
| 13 | agent 掉线 | 联系 arthas-deploy 重连 |
| 20 | Arthas 报错 | 改命令（类名/方法名/ClassLoader hash） |
| 30 | config 损坏 | 重跑 arthas-deploy |

## 命令语义（按审计意图）

> 完整语法查 `references/commands-quickref.md`；OGNL 表达式查 `references/ognl-cheatsheet.md`。

| 意图 | 命令 | 代表性示例 |
|------|------|-----------|
| **运行时侦察** | `version` / `dashboard -n 1` / `jvm` / `thread -n 3` / `sysprop` / `sysenv` | `sysenv \| grep -iE 'password\|secret\|key'` |
| **动态类发现** | `sc <pattern>` / `sc -d -f <class>` / `classloader -l` / `classloader -c <hash>` | `sc -E '.*\$[\w]+\$[\w]+'`（找内部类/动态代理） |
| **反编译运行时字节码** | `jad <class>` / `jad <class> <method>` / `jad --source-only <class>` | `jad -c <hash> com.example.JwtFilter doFilter` |
| **调用栈追踪** | `stack <class> <method>` / `trace <class> <method>` / `trace --skipJDKMethod false` | `stack java.sql.Statement execute* -n 3` |
| **观察方法执行** | `watch <class> <method> '{params,returnObj,throwExp}' -x <N> -n <M>` | `watch C.login '{params,returnObj}' 'returnObj == null' -x 3 -n 10` |
| **时空隧道** | `tt -t <class> <method>` / `tt -i <IDX>` / `tt -i <IDX> -p` / `tt --delete-all` | `tt -i 1003 -p --replay-times 5` |
| **对象访问** | `ognl '<expr>'` / `getstatic <class> <field>` / `vmtool --action getInstances` | `vmtool --action getInstances --className ApplicationContext --express 'instances[0].getBean("userService")'` |
| **热加载（慎）** | `mc <file> -d <dir>` / `retransform <classfile>` / `reset -E '.*'` | `retransform /tmp/com/example/X.class` |

### watch 表达式变量（速查）

`params`（入参数组） / `returnObj`（返回值） / `throwExp`（异常） / `target`（this） / `clazz`（类引用） / `loader`（ClassLoader） / `#cost`（耗时 ms）。标志：`-b`(before) `-s`(success) `-e`(exception) `-f`(finally) `-x N`(深度)。

## 核心工作流

> 每条工作流都要求**先 probe 再执行**。状态标记见下节。

### Workflow 0：前置侦察（每次审计必跑）

```bash
python {skill_dir}/scripts/arthas_exec.py probe
# 任一失败 → 先转 arthas-deploy 修，不要边修边审
```

探针通过后追跑：
```bash
python {skill_dir}/scripts/arthas_exec.py exec "jvm"
python {skill_dir}/scripts/arthas_exec.py exec "sysenv | grep -iE 'password|secret|key'"
python {skill_dir}/scripts/arthas_exec.py exec "sc javax.servlet.Filter"
```

### Workflow 1：Source→Sink 污点传播

静态报告给出 `UserController.login → Statement.execute` 可疑：
1. Source 观察：`watch UserController login '{params}' -x 3 -n 3 -b`
2. Sink 观察：`watch java.sql.Statement execute* '{params}' -x 3 -n 3`
3. 调用栈确认路径：`stack java.sql.Statement execute* -n 3`
4. tt 录制完整现场：`tt -t UserController login -n 1`，然后 `tt -i 1000` 详查

### Workflow 2：身份绕过 / JWT

```bash
jad JwtFilter doFilter                                    # 看真实逻辑
watch JwtFilter doFilter '{params[0].getHeader("Authorization"), returnObj, throwExp}' -x 4 -n 5
getstatic com.example.util.JwtUtil SECRET_KEY -x 2        # 拿 secret
vmtool --action getInstances --className SecurityFilterChain \
    --express 'instances[0].getFilters()' -x 4            # 看 Spring Security 实际配置
```

### Workflow 3：动态加载恶意类（Webshell / 后门）

```bash
classloader -l                                             # 找异常 ClassLoader（/tmp/、非标准）
classloader -c <hash>                                      # 列出该 loader 的 URLs
sc -c <hash> *                                             # 该 loader 加载了哪些类
jad -c <hash> com.evil.Backdoor                            # 反编译可疑类
stack java.lang.Runtime exec -n 10                         # Runtime.exec 的所有调用路径
vmtool --action getInstances --className ClassLoader --limit -1 \
    --express 'instances.{? #this.getClass().getName().contains("URLClassLoader")}' -x 2
```

### Workflow 4：运行时安全配置审计

```bash
vmtool --action getInstances --className ApplicationContext \
    --express 'instances[0].getBean("securityFilterChain").getFilters()' -x 4
vmtool --action getInstances --className javax.servlet.Filter --limit -1 \
    --express 'instances.{#this.getClass().getName()}'
vmtool --action getInstances --className javax.sql.DataSource --limit -1 \
    --express 'instances.{#this.getUrl() + " | " + #this.getUsername()}'
sysprop | grep -iE 'password|secret'
sysenv  | grep -iE 'password|access_key'
thread -all                                                # 找可疑线程（挖矿/回连）
```

### Workflow 5：PoC 验证

```bash
ognl '@java.lang.Runtime@getRuntime().exec("whoami")'                       # RCE
ognl '#f=new java.io.File("/etc/passwd"), #is=new java.io.FileInputStream(#f), #buf=new byte[1024], #is.read(#buf), new java.lang.String(#buf)'    # 文件读取
ognl '@javax.naming.InitialContext@lookup("dns://log.example.com/test")'     # JNDI 注入（探测性）
```

> **PoC 命令会实际执行**。执行前必须向用户说明意图并获得确认。

## 输出：audit-findings.json

> 所有审计结果**必须**写入 `output/audit-findings.json`，schema 详见 `references/audit-findings-schema.md`。

核心字段：

```json
{
  "schema_version": "1.0",
  "metadata": { "session_id": "...", "target": {...}, "workflows_executed": [...] },
  "jvm_snapshot": { "arthas_version": "...", "java_version": "...", "thread_count_active": 142 },
  "findings": [
    {
      "id": "F-001",
      "category": "A03:2021-Injection",
      "vulnerability_type": "SQL Injection",
      "severity": "CRITICAL",
      "status": "RUNTIME-CONFIRMED",
      "confidence": "HIGH",
      "taint_flow": { "source": {...}, "sink": {...}, "intermediate_calls": [...], "taint_transforms": [...] },
      "evidence": [
        { "id": "E-001", "command": "watch ...", "command_output_status": "OK", "evidence_type": "dynamic_capture",
          "output_excerpt": "...", "full_output_file": "output/evidence/F-001-E-001.txt", "significance": "HIGH" }
      ],
      "call_stack": ["..."],
      "static_reference": { "source_tool": "java-security-audit", "finding_id": "SQL-001", "source_file": "..." },
      "runtime_target": { "class": "...", "method": "...", "classloader_hash": "3d4eac69", "decompile_status": "MATCH" },
      "poc": { "executed": true, "confirmed_by_user": true, "payload": "...", "result": "..." }
    }
  ],
  "summary": { "by_status": {...}, "by_severity": {...}, "by_category": {...} },
  "artifacts": { "cleanup_performed": true, "cleanup_commands": [...], "agent_stopped": false }
}
```

### 状态标记（与 java-security-audit 集成用）

| 状态 | 含义 | 触发 |
|------|------|------|
| `RUNTIME-CONFIRMED` | 动态证据确凿 | watch 看到污点 / stack 看到路径 / PoC 成功 |
| `RUNTIME-DENIED` | 动态测试未发现 | watch 未捕获（3+ 次）/ 类不存在 / 方法未调用 |
| `RUNTIME-DIVERGENCE` | 源码 vs 字节码不一致 | jad 输出与 source 比对有差异 |
| `RUNTIME-BLOCKED` | 被 Filter/防御拦截 | stack 显示请求在 Filter 链被拒 |

### 集成链路

- **输入**：`output/phase4-api-audit.json`（静态可疑点 → 创建关联 finding → 执行对应 workflow → 设 status）
- **输出**：`output/phase5-arthas-audit.json`（schema 与 audit-findings.json 相同）

### 内部系统不脱敏

所有动态捕获的输出（DB 连接串、JWT secret、API key、明文密码、请求头、响应体）**保留原文**。仅在以下场景启用脱敏：报告需对外分享，或包含真实客户 PII 时，整值替换为 `[REDACTED]` 并在 `artifacts.redaction_count` / `redaction_types` 中记录。

## 强制规则

- **Workflow 0 必须先过**：probe 失败必须转 arthas-deploy，不得边修边审
- **PoC 前必须显式确认**：`Runtime.exec` / 文件读取 / 网络请求类命令必须先向用户说明
- **优先只读命令**：watch/stack/jad/sc 不改运行状态
- **tt 用完清理**：`tt --delete-all` 防 OOM
- **trace/watch 用完 reset**：`reset -E '.*'` 防字节码增强堆积
- **ClassLoader hash 必须先查后用**：`-c <hash>` 必须先用 `sc -d` 或 `classloader -l` 获取
- **所有命令经 tunnel**：不要 telnet 3658（那是本地后门）
- **敏感信息保留**：内部系统不脱敏；仅对外分享时启用 `[REDACTED]`
- **所有结果进 audit-findings.json**：禁止散写 markdown 表格

## 参考文档（按需读取）

| 文件 | 何时读 |
|------|--------|
| `references/commands-quickref.md` | 需完整命令语法 |
| `references/audit-recipes.md` | 按 OWASP Top 10 查命令配方 |
| `references/ognl-cheatsheet.md` | 写复杂 OGNL 表达式 |
| `references/audit-findings-schema.md` | 写 audit-findings.json |

## 清理

```bash
python {skill_dir}/scripts/arthas_exec.py exec "reset -E '.*'"
python {skill_dir}/scripts/arthas_exec.py exec "tt --delete-all"
# arthas-deploy 负责 stop agent
```
