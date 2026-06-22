# AGENTS.md — agentloop

## What This Is

Automated Java whitebox security audit orchestrator. Two-layer agent architecture (Boss → Expert agents) that discovers and verifies vulnerabilities in Java JAR/source code using jar-analyzer, ast-grep, and Memurai. Phase 4 PoC additionally uses codegraph for call-topology queries.

主 agent（Boss）作为调度中心，对收集到的资源进行调度审计，直接发放给各个专家 agent。不再启动独立 opencode 会话，而是作为工具把数据整理好后由主 agent 直接消费。

**Language**: All agent output, reports, and narrative content must be **Chinese**. Code, JSON keys, and enum values stay English.

## Engineering Mindset (第一性原理 + Karpathy 铁律)

所有决策从需求本质出发，不盲从惯例或模板。与现有硬规则不冲突的增量：

- **动机不清则停**：目标或意图模糊时必须先讨论明确，不做假设性执行。
- **编码前先思考**：改代码前陈述假设、暴露权衡、列多方案优缺点；有更简路径必须指出。
- **外科手术式修改**：只触碰必须修改的地方，不顺便"优化"无关代码/注释/格式，严格匹配现有代码风格。惯例优先于新颖——即使自认为更好，也遵从代码库现有命名和架构惯例。
- **先读再写**：添加代码前必须阅读当前文件及其导入关系，检查是否已存在功能相同的实现，严禁创建重复版本。
- **暴露冲突不折中**：代码库存在两种矛盾模式时，明确指出冲突并等待决策，绝不混合两种模式或自行选择。
- **长任务检查点**：>3 步或 >3 文件的修改，每步总结进度；某步失败时回滚到上一个检查点，不在错误状态上继续。

## Key Commands

```powershell
# 方式1: 只给 JAR 包，自动生成 preset + route，跑 Phase 0-2
python {agentloop_root}/run_phase1_to_4.py --jar D:/path/to/app.jar --phase 2

# 方式2: 已有 preset.json
python {agentloop_root}/run_phase1_to_4.py --preset projects/{group_id}/preset.json --phase 2

# Phase 3-4: 主 agent 直接消费（不启动 opencode 子进程）
# 主 agent 加载 java-whitebox-loop skill，从 chains.db 取 batch，分析+验证

# 单独执行各阶段：
# Phase 0+1: 自动从 JAR 生成 preset.json + route.json + 暴露面采集
python {agentloop_root}/scripts/auto_preset.py --jar D:/path/to/app.jar

# Phase 2: 调用链构建（写入 chains.db）— jar-analyzer 模式
python scripts/chain/chain_builder.py --project-root {projectRoot} --group-id {groupId} --entry "{fqn}" --depth 20 --loop-dir {loopDir} --jar-analyzer-db {jar_analyzer_db}

# Phase 2.5: 链边验证（jar-analyzer 批量验证）
python scripts/chain/verify_edges.py --jar-analyzer-db {jar_analyzer_db} --chains-db {loopDir}/chains.db

# 查询 chains.db 统计
python -c "from chain_db import ChainDB; db = ChainDB('{loopDir}/chains.db'); print(db.stats())"

# Memurai (Redis-compatible) — NOT pip redis, uses native CLI
# Path: C:\Program Files\Memurai\memurai-cli.exe
memurai-cli GET "{groupId}:method:{fqn}#{startline}"
memurai-cli KEYS "{groupId}:*"  # list keys; session 结束 hook DEL all except knowledge:* + errors 合并到 knowledge
```

## Pre-Run Checklist (Phase 0 — MANDATORY)

Before starting any audit:
1. **Session 结束 hook**: Delete `{groupId}:*` keys from Memurai（保留 `{groupId}:knowledge:*`，并把 `{groupId}:errors:log` 高频错误合并到 `{groupId}:knowledge:errors`）。方法体等缓存**不设 TTL**，只在活跃审计期间有效。
2. **Verify 3 core tools available**: `jar-analyzer` (JAR at `tools/javaparser/jar-analyzer-5.22.jar`), `ast-grep`, `Memurai`. If any is missing → exit code 2, no degradation. Phase 1-3 do NOT use codegraph.
3. **Sync project skills**: Phase 0 creates symlinks from `~/.agents/skills/{name}` → project `skills/{name}` for all project skills.
4. **Check preset.json** exists at `projects/{group_id}/preset.json` with valid `projectRoot`, `groupId`, `loopDir`, `targetJarPath`, `jarAnalyzerDb`.

## Tool Selection (Hard Rule — Do Not Mix)

| Task | Tool | Never |
|------|------|-------|
| Config file values (xml/yml/properties) | `grep` | jar-analyzer |
| Class/method relationships, call chains (Phase 0-3) | `jar-analyzer.db` SQLite (`method_table`, `method_call_table`, `method_impl_table`) | ast-grep |
| Call-topology queries (Phase 4 PoC only) | `codegraph` SQLite (max 20 LEFT JOINs) | jar-analyzer |
| Dangerous function patterns (SQL/RCE/...) | `ast-grep` | grep |
| Read method bodies | Memurai cache (pre-fetched via JAR + source files) | Direct `Read` of whole files; querying jar-analyzer/codegraph for method bodies |
| Statistics/reports | Python scripts | ad-hoc code |

**Subagents never call jar-analyzer or codegraph directly** — method bodies are pre-fetched to Memurai before subagent launch. 主 agent 从 chains.db 取 batch，从 Memurai 加载方法体，直接分发给专家 agent。

## Scope Boundary (Hard Constraint)

**IN scope**: Vulnerability location (file:line), call chain tracing, PoC payload, business impact description, severity rating, trigger conditions.

**OUT of scope (FORBIDDEN)**: Fix suggestions, remediation code, "should use PreparedStatement" style advice, patch recommendations. Finding JSON with `remediation`/`fix_suggestion`/`secure_alternative` fields → auto-scored 0.

## Output Conventions

All audit output goes to `{project_root}/loop_audit/` (or configured `loopDir`). Key invariant:

```
|高风险端点/*.md| + |中低险端点/*.md| ≡ |project-context.json endpoints|
```

Any deviation = incomplete audit (loop cannot terminate).

**File naming (Windows-safe)**: Endpoint reports: `{severity}_{fqn}_{method}_{sigHash}.md` (dots → `__`). PoC reports: `{status}_{severity}_{fqn.method-sink-roundNNN}.md`.

## Directory Map

| Directory | Purpose |
|-----------|---------|
| `skills/` | OpenCode skill definitions (java-whitebox-loop, injection-audit, poc-verify, etc.) |
| `scripts/ast/` | Attack surface scanner, AST finders, annotation enrichment |
| `scripts/chain/` | Call chain builder, chain_db.py (SQLite), method extractor, sink registry |
| `scripts/audit/` | PoC monitor, coverage verifiers, self-evolution (cross-agent-50r.py deprecated) |
| `scripts/redis/` | Memurai client wrapper, batch prefetch, stats |
| `scripts/exposure/` | Phase 1 暴露面采集（9 collectors + synthesizer + cli） |
| `conduct/必读/` | 6 hard-constraint docs (MUST READ before any audit work) |
| `conduct/经验/` | Learned patterns (PoC failures, false positive cases, pruning mistakes) |
| `types/` | Global vulnerability pattern library (cross-project, deduplicated) |
| `projects/{groupId}/` | Per-project preset.json, knowledge docs, loop_audit output |
| `projects/_template/` | Template files for new projects (copy these, don't edit) |
| `prompts/` | Expert agent prompt stubs (boss.md, expert-*.md; supervisor.md removed) |
| `checker/` | Multi-perspective review skills (Socrates, Feynman, Musk, etc.) — 触发于大版本改动前，非审计流程内置 |
| `tools/` | External: arthas, javaparser-service (预编译 JAR) |
| `conduct/优化路径/` | 4 份性能优化方向 (Token/并发/缓存/失败率) |
| `design-docs/` | 会话记录、探索总结、实施方案 |
| `design-docs/_archive/` | 归档目录 — 禁止 AI 默认加载 |
| `requirements/` | 原子需求拆解 + ai改动日志 |
| `proposals/` | 改进提案：本质系列 + 哲学分析 + 专家分析 |
| `doc/archive/` | 归档目录 — 禁止 AI 默认加载 |
| `doc/` | codegraph/ast-grep 使用指南、Boss 经验沉淀、版本实践记录 |

## Pruning Logic (3 Levels)

- **L1 (Endpoint)**: Skip no-param, numeric-only, health/actuator, swagger endpoints → log to `diagnostics/pruning-log.jsonl`
- **L2 (Chain)**: At each chain node check sanitizer list (PreparedStatement, MyBatis #{}, HtmlUtils.escape, etc.) → PRUNE if sanitized
- **L3 (Cross-round)**: Skip findings already `final` from prior rounds; continue `draft` findings

## Path Placeholders (Never Hardcode)

| Placeholder | Source |
|-------------|--------|
| `{agentloop_root}` | Derived from SKILL.md location (2 levels up) |
| `{group_id}` | `preset.json → groupId` |
| `{project_root}` | `preset.json → projectRoot` |
| `{codegraph_db}` | `preset.json → codegraphDb` (Phase 4 PoC only) |
| `{jar_analyzer_db}` | `preset.json → jarAnalyzerDb` |
| `{loop_audit_dir}` | `preset.json → loopDir` + `projectRoot` |

## Convergence Criteria (4-AND)

Audit terminates when ALL are true simultaneously:
1. Average score ≥ 85
2. Standard deviation < 3
3. Data reconcile passes ≥ 10
4. Endpoint coverage ≥ 0.95

## Common Pitfalls

- **Do NOT `Read` entire Java files** for method bodies — method bodies are pre-fetched to Memurai via `tools/javaparser/java-method-call-extractor-1.0.0.jar` + source files during chain build. AI only `GET`s from cache; jar-analyzer is for call-topology only, never method bodies.
- **Do NOT suggest fixes** — this tool discovers vulnerabilities, never remediates.
- **Do NOT use `pip install redis`** — use native `memurai-cli.exe` at `C:\Program Files\Memurai\`.
- **Do NOT skip Phase 0** — stale cache from prior rounds pollutes results.
- **Do NOT write narrative content in English** — reports and summaries must be Chinese.
- **Do NOT create new project knowledge in shared `types/`** without dedup check (similarity > 0.7 → reuse).
- **Do NOT modify tool flow/scripts except via a dedicated tool sub-agent** — only the Boss agent authorizes modifications, and they must not overfit to one project.
- **Do NOT use codegraph for Phase 0-3** — jar-analyzer is the primary tool for class/method relationships and call chains. codegraph is reserved for Phase 4 PoC call-topology queries only.

## Agent Architecture (Two-Layer, Skill-Based, Accelerated)

```
主 agent (Boss) — 调度中心，加载 java-whitebox-loop skill
  │
  ├── 脚本工具: Phase 0-2（确定性工作，不需要 AI）
  │     ├── Phase 0: check_core_tools.py + Memurai cleanup + skills 软连接同步 + auto_preset (JAR→preset+route)
  │     ├── Phase 1: exposure/cli.py collect（9 collectors → exposure/*.json）
  │     ├── Phase 2: chain_builder.py → chains.db + Memurai 方法体缓存
  │     └── Phase 2.5: verify_edges.py → 链边批量验证
  │
  ├── 专家 agent: Phase 3（通过 task() 委派，subagent 加载对应 skill）
  │     主 agent 从 chains.db 取链，按链特征分发：
  │
  │     ┌──────────────────────────────────────────────────────────────────┐
  │     │ 注入类/文件类（chain 有 sink）:                                    │
  │     │   每个 endpoint 只取 1 条优先级最高的链（不再所有链都审）              │
  │     │   涉及文件路径 → file-audit skill                                │
  │     │   不涉及文件路径 → injection-audit skill                           │
  │     │   方法体加载: 前4层+后2层（<6层全部）                               │
  │     │   写回: db.update_agent_result(chain_id, "injection", {...})      │
  │     │                                                                  │
  │     │ 认证鉴权/业务逻辑（所有 chain）:                                   │
  │     │   只校验前 25% 端点（按优先级排序后取前 1/4）                       │
  │     │   每 endpoint 1 条链，前 5 层                                     │
  │     │   写回: db.update_agent_result(chain_id, "auth", {...})           │
  │     │                                                                  │
  │     │ chain 无 sink → 不调注入/文件类                                    │
  │     │ 并发: 同时 4 个 task() 并行                                       │
  │     └──────────────────────────────────────────────────────────────────┘
  │
  ├── Phase 3.5: 主 agent 生成静态报告
  │     汇总所有 agent_results → diag/static_report.json
  │
  ├── Phase 3.5: codegraph 构建（Phase 4 PoC 专用，Phase 1-3 不需要）
  │     └── codegraph init + index → codegraph.db
  │
  └── 验证 agent: Phase 4（PoC 动态验证）
        取 agent_results 中 verdict=vuln & poc_status=pending 的链
        → PoC agent 逐一验证（不重新分析，直接读 agent_results JSON）
        → 并发: 同时 4 个 task() 并行
        → 写回: db.update_vuln_poc_status(chain_id, agent_key, idx, status)
        PoC agent 通过 Memurai 缓存 + codegraph SQLite 获取代码信息（禁止直接读源文件）
```

**agent_results JSON 列**（chains.db 新增列）:
```json
{
  "injection": {"verdict": "vuln", "vulnerabilities": [{"type": "...", "root_cause": "...", "poc_status": "pending"}]},
  "file": {"verdict": "safe"},
  "auth": {"verdict": "vuln", "vulnerabilities": [...]},
  "biz": {"verdict": "inconclusive", "reason": "...", "extra_info_needed": "..."}
}
```

- vuln → 必须给 root_cause + poc_status=pending
  - root_cause 必须包含污点传播路径分析（从入口到 sink 的每层，默认中间层未消毒）
  - 认证鉴权/业务逻辑类必须说明漏洞链（如何导致的安全问题）
- safe → 说明原因
- inconclusive → 标注哪里无法判断 + 需要什么额外信息，**也需要 PoC 验证**
- poc_status: pending → PoC agent 验证后 → confirmed/denied/inconclusive
