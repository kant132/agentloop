---
name: java-whitebox-loop
description: Java 白盒审计工具的顶层入口契约。用户提供 preset.json，工具自动产出 loop_audit/ 下 8 类产物 + 跨轮 knowledge.json。触发词:`白盒审计` / `启动 loop` / `安全审计编排`。
---

# Java 白盒审计 — 入口契约

> 本 SKILL.md 是**用户视角的顶层说明**,不再描述 Boss agent 内部流程。
> 用户只关心:**"我提供什么参数 / 我得到什么产出 / 项目如何完成我定的 5 个目标"**。

## 如何调用 (用户入口)

## Phase 0 — Pre-flight: 清空上轮缓存 (MANDATORY)

> **每次启动本 SKILL 前，必须执行此步骤，不可跳过。**
> 清空原因：上轮残留的 `draft` / `final` / `chain` 缓存数据会污染本轮审计结果，导致 agent 读到过期的方法体或调用链，产出错误的 finding。

### 清空规则

| 操作 | pattern | 说明 |
|------|---------|------|
| **删除** | `{group_id}:*` | 清空该 groupId 下所有缓存 (draft/final/chain/annotated 等) |
| **保留** | `{group_id}:knowledge:*` | 跨轮沉淀知识 (Phase D 写入)，本轮 agent 需要读取，不清 |

### 执行命令

```powershell
# Step 1: 列出当前 group_id 下所有 key (除 knowledge:* 外)
$keys = memurai-cli --raw KEYS "{group_id}:*" | Where-Object { $_ -notmatch "^{group_id}:knowledge:" }

# Step 2: 批量删除
if ($keys.Count -gt 0) {
    $keys | ForEach-Object { memurai-cli DEL $_ }
    Write-Host "[Phase 0] Cleared $($keys.Count) cache keys for {group_id}"
} else {
    Write-Host "[Phase 0] No stale cache keys found for {group_id}, skip"
}

# Step 3: 验证 knowledge:* 未被误删
$knowledge_keys = memurai-cli --raw KEYS "{group_id}:knowledge:*"
Write-Host "[Phase 0] Preserved $($knowledge_keys.Count) knowledge keys"
```

> **daemon 模式 (`cross-agent-50r.py`) 自动执行上述逻辑**，用户无需手动操作。
> 若通过 Opencode 直接触发本 SKILL (方式 2)，agent 必须手动执行上述命令后再进入 Phase A。

### 方式 1 — 通过 preset.json 自动触发 (推荐)

```powershell
# 用户只需准备 preset.json 一项,然后运行 daemon
# daemon 启动后第一步:执行 Phase 0 清空 {group_id} 缓存 (除 knowledge:* 外)
python {agentloop_root}/scripts/audit/cross-agent-50r.py `
  --preset {agentloop_root}/projects/{group_id}/preset.json
```

### 方式 2 — 通过 Opencode 触发本 SKILL

```markdown
请帮我审计 {group_id} 项目，使用 `java-whitebox-loop` SKILL
```

触发词: `白盒审计` / `启动 loop` / `安全审计编排` (任一即可)。

> **⚠️ 方式 2 注意**: agent 收到触发后，**第一件事必须执行 Phase 0 清空命令**，不可直接进入 Phase A。

## 用户提供的参数 (由 preset.json 承载)

| 键 | 必需 | 说明 | 示例 |
|----|------|------|------|
| `groupId` | 是 | Java 项目 Maven groupId | `org.owasp.webgoat` |
| `projectRoot` | 是 | 源码根目录 | `<your-project-root>` (例:`D:\code\WebGoat-2025.3`) |
| `codegraphDb` | 是 | codegraph SQLite 路径 (已索引) | `<project_root>/.codegraph/codegraph.db` |
| `loopDir` | 否 (默认 `loop_audit`) | 输出目录名 (相对 `projectRoot`) 或绝对路径 | `loop_audit` 或 `<agentloop_root>/projects/{group_id}/loop_audit` |
| `appPort` | 否 (默认 8080) | 被测应用端口 | `8080` |
| `appCtxPath` | 否 (默认 `""`) | Context path | `/WebGoat` |
| `sessionCookieName` | 否 (默认 `JSESSIONID`) | 会话 Cookie 名 | `JSESSIONID` |

完整 preset 模板见 [`{agentloop_root}/projects/_template/preset.template.json`]({agentloop_root}/projects/_template/preset.template.json)。

## 用户得到的产出 (全部在 `{loop_audit_dir}/` 下)

| 目录/文件 | 内容 | 何时生成 |
|-----------|------|----------|
| `routes.json` | 暴露面端点清单 + sink 标注 | Phase A |
| `security-context.json` | Filter 链 / SecurityFilterChain beans / 注解富化 | Phase B |
| `chains/{sig_hash}.json` | 每条调用链 (含 `// sink: <FQN>` 注释内联) | Phase C |
| `reports/{finding}.md` | 每个 finding 的 Markdown 报告 (含 PoC / CVSS) | Phase C |
| `poc/{finding}_poc.json` | PoC 验证原始结果 | Phase C + poc-monitor |
| `diag/scoring-history.jsonl` | 每轮评分 (5 维: coverage/poc/reconcile/compliance) | Phase D |
| `diag/convergence.json` | 收敛判定 (4-AND: score≥85 + stddev<3 + reconcile≥10 + coverage≥0.95) | Phase D |
| `diag/self-check.json` | 每轮 7+1 项对账自检 | Phase D |
| `diag/false-positive-samples.jsonl` | 假阳率抽样 (每 5 轮,30 条) | Phase D |
| `diag/needs_human-review/*.jsonl` | 给人工标注的 finding | Phase D |
| `diag/optimization-suggestions.jsonl` | 工具自进化建议 | Phase D |
| `knowledge.json` | 跨轮知识沉淀 (findings + routes + annotations + sanitizers) | Phase D |

## 如何完成你定的 5 个根本目标

| # | 你定的目标 | 实现机制 | 验证方式 |
|---|------------|----------|----------|
| 1 | **漏洞准确 + 已验证** | 5 类 expert skill (injection/business/file/auth/login) + poc-verify (CVSS 3.1) + PoC 后台守护 (`poc-monitor.py`) 自动验证 high/critical findings | 查看 `loop_audit/reports/*.md` 中 `poc_status: verified` 比例 |
| 2 | **外部接口无遗漏** | Phase A `attack_surface_scanner.py` 用 ast-grep + codegraph SQL 双重枚举;节点标识改用(nodes.id hashkey,96.6% 命中率,md5 降级兼容) | `loop_audit/routes.json` 条目数 ≥ 实际端点数 |
| 3 | **调用链分析无遗漏** | Phase C `chain_builder.py` CTE RECURSIVE 走 codegraph.db (优先 SQL) + `method_calls_extractor.py` 提取 third-party 调用 + 内联 `// sink:` 注释 + `non-groupId = sink` 规则 (`is_sink_detection()` 函数) | `loop_audit/chains/*.json` 中 `total_nodes` 与 `total_edges` 比例合理 |
| 4 | **运行高效 + token 少** | 三层 Agent (Boss/Supervisor/Expert 按需派发,不盲目全量);codegraph SQL 比 ast-grep 快 1-2 个数量级; Memurai 缓存跨 chain 复用方法体 | `loop_audit/diag/scoring-history.jsonl` 每轮 `compliance` 字段 (越高越好) |
| 5 | **工具自进化 (越用越智能)** | 8 函数 `self_evolution.py` 每轮写 6 类诊断文件 + `knowledge.json`;下轮 supervisor/expert 加载 knowledge.json 避免已知坑 | 跨两轮对比 `knowledge.json` 体积增长 + `scoring-history.jsonl` 分数递增 |

## 必读 (≤3 rule 文件)

1. `skills/java-whitebox-loop/rules/phase-gates.md` — 4 阶段入口/出口/失败回退
2. `skills/java-whitebox-loop/rules/self-evolution.md` — scoring/convergence/FP sampling/knowledge merge
3. `skills/java-whitebox-loop/rules/pruning-and-keys.md` — L1/L2/L3 剪枝 + Memurai key schema

## 关键依赖 (脚本层)

| 脚本 | 路径 | Phase 调用处 |
|------|------|--------------|
| `attack_surface_scanner.py` | `{agentloop_root}/scripts/ast/` | A |
| `annotated-source-enricher.py` | `{agentloop_root}/scripts/ast/` | B |
| `chain_builder.py` | `{agentloop_root}/scripts/chain/` | C |
| `method_calls_extractor.py` | `{agentloop_root}/scripts/chain/` | C |
| `poc-monitor.py` | `{agentloop_root}/scripts/audit/` | C (后台守护) |
| `self_evolution.py` | `{agentloop_root}/scripts/audit/` | D |
| `cross-agent-50r.py` | `{agentloop_root}/scripts/audit/` | Daemon |
| `check_core_tools.py` | `{agentloop_root}/scripts/audit/` | Daemon 启动 |

## 硬约束 (用户不可绕过的规则)

- **3 个核心工具**(`codegraph` / `ast-grep` / `Memurai`) 任一不可用 → 直接 exit(exit code 2),不降级
- **`non-groupId` 调用 = sink** (在 `method_calls_extractor.py` 中硬编码,`calledFQN` 不以 `groupId + "."` 开头即视为 sink)
- **输出目录** = `{loop_audit_dir}`(用户预设,默认 `{project_root}/loop_audit`,也可指向 `{agentloop_root}/projects/{group_id}/loop_audit` 避免污染源码树)
- **通用安全知识预置** = `projects/_template/06-通用安全知识.md` (457 行,10 大类 sink + 6 类业务逻辑) + `07-Sink表.json` (39 sinks / 16 categories) + `08-Sanitizer表.json` (28 sanitizers / 8 categories)
- **优先 codegraph SQL** = 调用链查询走 CTE RECURSIVE on `{codegraph_db}`,ast-grep 仅用于 Phase A 注解发现 (不用于链查询)
- **4-AND 收敛** = score≥85 + stddev<3 + reconcile≥10 + coverage≥0.95 (同时满足才 break loop)

## 不再做的事

- ~~Phase 2 威胁分析~~ → `threat-model-analyst/SKILL.md` 仍可用于**手工**,不再是 mandatory
- ~~三哲学自检 (苏格拉底/康德/乔布斯)~~ → 替换为 `self_evolution.write_self_check` (结构化 7+1 项清单)
- ~~FWD 5 subagent 并行 (A/B/C/D/INFO)~~ → 替换为 endpoint-supervisor 串行分析每 chain,按需派 5 类 expert
- ~~`java-forward-vuln-discovery/SKILL.md`~~ → 已标记 DEPRECATED
- ~~7 个 rule 文件分散维护~~ → 合并为 3 个: `phase-gates.md` / `self-evolution.md` / `pruning-and-keys.md`

## 路径占位符约定

本 SKILL 中所有路径引用必须使用占位符,**禁止硬编码**。占位符对照:

| 占位符 | 含义 | preset.json 键 |
|--------|------|----------------|
| `{agentloop_root}` | agentloop 工具本身所在目录 (本 SKILL 所在 repo 根) | *(运行时从 SKILL 路径推导: `SKILL.md` 上两级)* |
| `{group_id}` | Java 项目的 Maven groupId | `groupId` |
| `{project_root}` | 被审计 Java 项目源码根目录 | `projectRoot` |
| `{codegraph_db}` | codegraph SQLite 路径 | `codegraphDb` |
| `{loop_audit_dir}` | 审计产物输出目录 (绝对或相对) | `loopDir` (与 `projectRoot` 组合后使用) |

实际执行时,占位符由 `cross-agent-50r.py` 从 preset.json 解析填充。用户在 SKILL.md 中看到的始终是占位符,不需要自己替换。