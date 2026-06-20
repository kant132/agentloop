# AGENTS.md — agentloop

## What This Is

Automated Java whitebox security audit orchestrator. Two-layer agent architecture (Boss → Expert agents) that discovers and verifies vulnerabilities in Java source code using codegraph, ast-grep, and Memurai.

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
# Phase 0-2: 脚本执行（确定性工作，产 chains.db + Memurai 缓存）
python {agentloop_root}/run_phase1_to_4.py --preset projects/{group_id}/preset.json --limit 100

# Phase 3-4: 主 agent 直接消费（不启动 opencode 子进程）
# 主 agent 加载 java-whitebox-loop skill，从 chains.db 取 batch，分析+验证

# 单独执行各阶段：
# Phase 1: 暴露面采集
python -m scripts.exposure.cli collect --project {projectRoot} --group-id {groupId} --output {loopDir} --codegraph-db {codegraphDb}

# Phase 2: 调用链构建（写入 chains.db）
python scripts/chain/chain_builder.py --project-root {projectRoot} --db {codegraphDb} --group-id {groupId} --entry "{fqn}" --depth 20 --loop-dir {loopDir}

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
2. **Verify 3 core tools available**: `codegraph`, `ast-grep`, `Memurai`. If any is missing → exit code 2, no degradation.
3. **Check preset.json** exists at `projects/{group_id}/preset.json` with valid `projectRoot`, `codegraphDb`, `groupId`.

## Tool Selection (Hard Rule — Do Not Mix)

| Task | Tool | Never |
|------|------|-------|
| Config file values (xml/yml/properties) | `grep` | codegraph |
| Class/method relationships, call chains | `codegraph` SQLite (max 20 LEFT JOINs) | ast-grep |
| Dangerous function patterns (SQL/RCE/...) | `ast-grep` | grep |
| Read method bodies | Memurai cache (pre-fetched via JAR + source files) | Direct `Read` of whole files; querying codegraph for method bodies |
| Statistics/reports | Python scripts | ad-hoc code |

**Subagents never call codegraph directly** — method bodies are pre-fetched to Memurai before subagent launch. 主 agent 从 chains.db 取 batch，从 Memurai 加载方法体，直接分发给专家 agent。

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
| `{codegraph_db}` | `preset.json → codegraphDb` |
| `{loop_audit_dir}` | `preset.json → loopDir` + `projectRoot` |

## Convergence Criteria (4-AND)

Audit terminates when ALL are true simultaneously:
1. Average score ≥ 85
2. Standard deviation < 3
3. Data reconcile passes ≥ 10
4. Endpoint coverage ≥ 0.95

## Common Pitfalls

- **Do NOT `Read` entire Java files** for method bodies — method bodies are pre-fetched to Memurai via `tools/javaparser/java-method-call-extractor-1.0.0.jar` + source files during chain build. AI only `GET`s from cache; codegraph is for call-topology only, never method bodies.
- **Do NOT suggest fixes** — this tool discovers vulnerabilities, never remediates.
- **Do NOT use `pip install redis`** — use native `memurai-cli.exe` at `C:\Program Files\Memurai\`.
- **Do NOT skip Phase 0** — stale cache from prior rounds pollutes results.
- **Do NOT write narrative content in English** — reports and summaries must be Chinese.
- **Do NOT create new project knowledge in shared `types/`** without dedup check (similarity > 0.7 → reuse).
- **Do NOT modify tool flow/scripts except via a dedicated tool sub-agent** — only the Boss agent authorizes modifications, and they must not overfit to one project.

## Agent Architecture (Two-Layer, Skill-Based)

```
主 agent (Boss) — 调度中心，加载 java-whitebox-loop skill
  │
  ├── 脚本工具: Phase 0-2（确定性工作，不需要 AI）
  │     ├── Phase 0: check_core_tools.py + Memurai cleanup
  │     ├── Phase 1: exposure/cli.py collect（9 collectors → exposure/*.json）
  │     └── Phase 2: chain_builder.py → chains.db + Memurai 方法体缓存
  │
  ├── 专家 agent: Phase 3（通过 task() 委派，subagent 加载对应 skill）
  │     主 agent 从 chains.db 取链，按链特征分发：
  │
  │     ┌─────────────────────────────────────────────────────────┐
  │     │ chain 有 sink?                                          │
  │     │   ├── 涉及文件路径 → file-audit skill (所有有 sink 链)  │
  │     │   └── 不涉及文件路径 → injection-audit skill (所有有 sink 链) │
  │     │ chain 无 sink → 不调注入/文件类                          │
  │     │                                                         │
  │     │ 每个 endpoint:                                          │
  │     │   ├── auth-chain-audit (前 5 层, 1 条链)                │
  │     │   └── business-logic-audit (前 5 层, 1 条链)            │
  │     └─────────────────────────────────────────────────────────┘
  │
  └── 验证 agent: Phase 4（PoC）
        取 status=vuln 的链 → 生成 PoC → 验证 → 写回 chains.db status
```

**关键设计**：
- **skill 机制**，不是 prompt 机制 — 主 agent 根据需要加载 skill，subagent 加载对应专家 skill
- 不启动独立 opencode 子进程 — 主 agent 直接消费 chains.db + Memurai 数据
- 主 agent 保持完整上下文 — 知道 Phase 1 发现了什么，Phase 2 构建了什么
- 专家 agent 通过 task() 委派 — 主 agent 整理好数据后发放
- 专家返回结论后主 agent 写回 chains.db — status: pending → analyzed → vuln/safe
- **注入类**：审计所有有 sink 的链
- **文件类**：涉及文件路径的链，subagent 加载 file-audit skill
- **认证鉴权 + 业务逻辑**：每个 endpoint 只调 1 次，只审计前 5 层，只审计 1 条链
- **无 sink 的链**：不调注入类 agent，只走认证鉴权 + 业务逻辑
