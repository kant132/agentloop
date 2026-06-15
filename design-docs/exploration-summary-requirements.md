# AgentLoop Exploration Summary — Design State & Implementation Gaps

> **Generated**: 2026-06-15
> **Scope**: Complete review of `D:\agentloop\design-docs\` (6 files) and `D:\agentloop\requirements\` (8 files + atomic-requirements subfolder)
> **Audience**: Future agents continuing the AgentLoop redesign for WebGoat-2025.3 end-to-end test

---

## 1. Core Design Vision

**AgentLoop is a Java whitebox security audit (SAST) agent system that uses a 3-layer agent architecture to discover, analyze, and verify exploitable vulnerabilities in Java projects — calibrated by a 4-dimensional score (coverage × PoC-rate × reconcile × compliance) and converged through self-evolving knowledge persistence.** Unlike passive code-analysis tools, it is structured as a **"security audit experience formalization engine"**: AI agents execute the audit pipeline autonomously while humans define only the strategy, atomic goals, flow, and acceptance criteria (`requirements\需求分析文档.md` §五).

The system is organized into **four phases** (A: endpoint enumeration → B: security context → C: chain analysis & vuln discovery → D: reconcile & converge) orchestrated by a Boss agent that spawns per-endpoint Supervisor agents, which in turn dispatch Analysts (5-dim thinking only) and 5 specialized Mining Experts (`injection`, `business-logic`, `file`, `auth-chain`, `login`). PoC verification runs in a **decoupled background monitor process** that consumes finished findings from Memurai and dispatches 2 concurrent Verifier agents — never blocking the main loop. Self-evolution is mandatory: each round persists knowledge to `{groupId}:knowledge:*` cache and `knowledge.json`, while effective optimizations append to `optimization-suggestions.jsonl` and supervisor experiences accumulate under `{groupId}:sup:exp:*`. The target testing ground is **WebGoat-2025.3** — an OWASP Spring Boot project chosen because its existing public vulnerability surface validates every sink path, every sanitizer counter-example, and every chain endpoint (`design-docs\chain-sql-engine.md`, `design-docs\暴露面扫描设计.md`).

---

## 2. The 18 Atomic Requirements — Status Table

Source: `requirements\ai改动日志.md`, `design-docs\会话记录-最终方案.md` §3, `design-docs\原子需求实现说明.md`, `README.md` 原子需求拆解 (19 → 18 effective after deleting #6).

| # | 需求 (Chinese) | English Summary | 状态 | 证据文件 |
|---|---|---|---|---|
| **1** | Phase 1 轻装：只做端口扫描/Filter 发现，不读文档/不做威胁建模 | Phase A only does port scan + filter discovery | ⚠️ 部分 | `requirements\实现评估.md` §1.1: "6 阶段流程标准化 ✅ (但过度复杂)" — old 6-phase SKILL.md still exists in `skills\java-whitebox-loop\SKILL.md`; new boss.md Phase A 未实现 |
| **2** | 漏洞发现与 PoC 验证解耦；5 FWD 合并为 1；PoC 后台 monitor | Decouple vuln discovery from PoC; merge 5 FWD; background monitor | ⚠️ 部分 | `scripts\audit\cross-agent-50r.py` (61KB) exists but `scripts\audit\poc-monitor.py` **NOT created**; 5 FWD templates exist in `skills\java-forward-vuln-discovery\templates\subagent-FWD-{A,B,C,D,INFO}.md` but merge to 1 **NOT done** |
| **3** | 端点枚举排序：跳过无入参/纯数字参；优先有 sink > POST > UPDATE > DELETE > GET；外部 > 内部 | Endpoint enum sort | ❌ 未实现 | No `boss.md` Phase A exists; `requirements\原子需求\分割1` only references the rule — no code |
| **4** | 结构化自检替代三哲学（if-then-else 清单 + JSON 输出） | Structured self-check replaces 3-philosophy | ❌ 未实现 | `skills\java-whitebox-loop\rules\02-scoring.md` exists (1807 bytes) but no `self-check.json` writer; old 3-philosophy still referenced |
| **5** | 假阳率量化度量（每轮抽 30 条，趋势跟踪） | FP-rate quantification | ❌ 未实现 | `requirements\codex-工程答复-回应六位大师.md` §6.2 mandates it; no `diag/fp-rate.jsonl`; no sampling script |
| **7** | 调用链方法标注（业务意义/分支逻辑/参数传递影响） | Chain method annotation | ❌ 未实现 | `skills\call-chain-audit-thinking\` **directory does not exist**; no annotations in any cache key |
| **8** | 配置风险通过 SSH 在实际环境检查 | SSH config check | ❌ 未实现 | `skills\ssh-skill\SKILL.md` exists (2052 bytes) but no integration in boss Phase B; no `env_config` field in any output |
| **9** | 硬编码凭证检查仅限影响 CIA | Hardcoded cred check — CIA only | ❌ 未实现 | No filter in `skills\injection-audit` or others; no `vuln_type=HARDCODED_CREDENTIAL` constraint |
| **10** | 项目知识跨轮次持久化（注解/消毒器/动态路由/历史 finding） | Cross-round knowledge persistence | ⚠️ 部分 | `暴露面扫描设计.md` defines `注解_public.json` + `{groupId}\注解\specific.json` schemas; `requirements\预置经验规则.md` §2.3 specifies TTLs; but **no actual `knowledge.json` writer**; `skills\java-whitebox-loop\rules\07-preset-rules.md` is closest proxy |
| **11** | 评分历史持久化（可算方差/趋势） | Scoring history persistence | ❌ 未实现 | No `loop_audit\diag\scoring-history.jsonl`; no append logic in `cross-agent-50r.py` |
| **12** | 收敛客观标准（4 AND：≥85 / std<3 / 全 PASS / 覆盖≥95%） | Objective convergence | ⚠️ 部分 | `会话记录-最终方案.md` §1.5 formalizes the 4-condition formula with `score = coverage×30% + poc_rate×30% + reconcile×25% + compliance×15%`; `skills\java-whitebox-loop\rules\02-scoring.md` is the rule; but **no convergence.json writer**, no `convergence.json` reader in daemon |
| **13** | 有效优化持久化到经验库 | Optimization persistence | ❌ 未实现 | No `loop_audit\diag\optimization-suggestions.jsonl`; no write logic |
| **14** | 主 SKILL ≤50 行（只说 AI 不知道的） | Main SKILL ≤50 lines | ⚠️ 部分 | `skills\java-whitebox-loop\SKILL.md` = 5594 bytes ≈ **120+ 行** — far exceeds 50-line target |
| **15** | rules 文件 ≤3 个（启动时一次读完） | rules ≤3 files | ❌ 不满足 | `skills\java-whitebox-loop\rules\` has **7 files** (01-phase-gates.md, 02-scoring.md, 03-subagent-dispatch.md, 04-redis-strategy.md, 05-data-reconcile.md, 06-pruning-rules.md, 07-preset-rules.md) |
| **16** | 缺失工具时主 agent 生成脚本（不是降级） | Self-generated scripts | ❌ 未实现 | No `scripts\generated\` logic in daemon |
| **17** | 三个核心工具（codegraph/memurai/ast-grep）缺失则退出，不降级 | No-degrade on missing core tools | ⚠️ 部分 | `cross-agent-50r.py` references codegraph/memurai; no `check_core_tools.py` exists (mentioned as "TBD" in `会话记录-最终方案.md` §10) |
| **18** | 每步骤明确指定使用哪个模板，不由 agent 自行选择 | Explicit template per step | ⚠️ 部分 | `skills\java-whitebox-loop\rules\03-subagent-dispatch.md` (1454 bytes) hints at this; loop_audit templates defined in `loop_audit\_template\` (12 files) but no enforcement |

> **Note**: Original 19 numbered as **#19** = endpoint sort (now folded into #3 per the README's "共19条（1条删除，实际18条有效需求）"). The 18 ≠ 19 reconciliation: docs/atomic-requirements.md enumerates **60+ raw requirements** (the original 整合版 document); the 18 are the **refined SDD atomic set** after consolidation.

**Tally**: ✅ Fully implemented: **0** | ⚠️ Partially implemented: **7** (#1, #2, #10, #12, #14, #17, #18) | ❌ Not implemented: **11** (#3, #4, #5, #7, #8, #9, #11, #13, #15, #16)

---

## 3. Missing Implementations — Detailed Gap Analysis

### 3.1 暴露面发现 (Attack Surface Discovery) — **Partial / Drafted**

**Status**: Schemas designed but scanner migration incomplete.

**What exists** (`design-docs\暴露面扫描设计.md` — 309 lines):
- Full directory schema: `项目\{groupId}\routes.json`, `chains\{sigHash}.json`, `filters.json`, `interceptors.json`, `注解_public.json`, `注解\specific.json`
- 9 key functions specified: `sig_hash`, `fqn_to_method_name`, `inject_sink_comment`, `get_body`, `detect_annotation_changes`, `classify_route`, `validate` (reconciliation), etc.
- Memurai cache: `{groupId}:method:{sigHash}` with TTL 24h
- Reconciliation rule: route count == chain count (P5.4 invariant)

**What exists in code**:
- `scripts\ast\attack_surface_scanner.py` (40KB, 40105 bytes) — **uses md5 hashkey strategy, NOT yet migrated to `nodes.id`**
- `scripts\ast\scanner_utils.py` (5743 bytes) — contains `inject_sink_comment`, `fqn_to_method_name`, `node_hash_key`, `classify_route`, `validate`
- `scripts\ast\annotation-categories.json` (2169 bytes) — Spring + JDK annotation catalog

**What's missing**:
1. `attack_surface_scanner.py` still outputs md5, must migrate to `nodes.id` hashkey (chain-sql-engine.md §2)
2. No `validate()` integration in daemon — reconciliation is hand-coded but not auto-run
3. `routes.json` schema writer absent — scanner outputs triples `(annotation_fqn, file, line)` only
4. `{groupId}\` directory structure not auto-created on first scan

### 3.2 Chain Analysis Implementation — **Partial / In Progress**

**Status**: SQL engine drafted; L1-L2 jump done but sink injection and method calls extraction still pending.

**What exists**:
- `scripts\chain\sqlite-extract-chain.py` (7266 bytes) — **CTE RECURSIVE** for layer-by-layer inner join ✅
- `scripts\chain\sqlite-multi-hop-search.py` (16847 bytes) — **legacy LEFT JOIN template**, marked as "确认有问题" in chain-sql-engine.md §3
- `scripts\chain\sqlite-pattern-search.py` (7457 bytes)
- `scripts\chain\chain-stats.py` (2545 bytes)

**What's missing** (per `design-docs\chain-sql-engine.md` §"总体工作流"):
1. `scripts/chain/method_calls_extractor.py` — **NOT created** — must wrap `tools/java-method-call-extractor-1.0.0.jar`
2. `scripts/chain/chain_builder.py` — **NOT created** — must integrate CTE + jar calls + sink extraction + Memurai write
3. Sink extraction: `calledFQN not startswith(groupId)` → all are sinks (80/20 strategy, deferred sink-pattern-table)
4. 4 open questions to clarify before plan-implementation: startLine uniqueness, codegraph 0-based vs 1-based, Memurai TTL, codegraph concurrent read, large-method body handling

### 3.3 Sink Detection (non-groupId = sink) — **Drafted, Not Implemented**

The user's exact decision (`chain-sql-engine.md` 对话5): "**sink点全路径查询**，通过 `tools/java-method-call-extractor-1.0.0.jar`，参考通过 start 和文件名,method名字,三者对应进行关联，**取到所有非groupId的方法调用作为sink点**".

**What's missing**:
1. No `method_calls_extractor.py` exists
2. No 80/20 sink filter: `r["calledFQN"] for r in method_calls if not r["calledFQN"].startswith(groupId + ".")`
3. No sink-pattern table (deferred to future iteration)
4. The `inject_sink_comment` function exists in `scanner_utils.py` but has no input pipeline

### 3.4 Self-Evolution Capability — **Designed, Not Wired**

**Status**: Schemas defined, writes missing.

**Persistent artifacts specified** (none exist on disk yet):
- `loop_audit\diag\scoring-history.jsonl` — append-only per-round scores (#11)
- `loop_audit\diag\optimization-suggestions.jsonl` — verified-effective optimizations (#13)
- `loop_audit\diag\false-positive-samples.jsonl` — manual FP labels (#5)
- `loop_audit\diag\self-check.json` — Phase D self-check JSON (#4)
- `loop_audit\diag\convergence.json` — 4-condition convergence signal (#12)
- `loop_audit\knowledge.json` — cross-round project knowledge (#10)
- `{groupId}:knowledge:*` Memurai namespace (write-first, file-merge-second)

**Self-evolution loop as designed**:
1. Boss spawns → loads `knowledge.json` + `{groupId}:knowledge:*`
2. Loop runs → writes findings to `{groupId}:audit:finding:{chainId}:*`
3. Boss Phase D → writes scoring + self-check → optimization-suggestions → knowledge cache
4. Daemon → merges `{groupId}:knowledge:*` → `knowledge.json`
5. Next round → loads from `knowledge.json`

**Blocker**: `cross-agent-50r.py` (61KB) does none of the above write operations. It's still the pre-redesign single-agent loop from earlier sessions.

### 3.5 Preset Security Knowledge — **Partial**

**What exists** (`requirements\预置经验规则.md` — 443 lines, the entire doc is preset knowledge):
- **§ 1 污点分析**: Sink ≠ Vuln (must verify source→sink path); sanitizer identification traps (PreparedStatement + string concat = no protection); cross-JAR tracking; 10-layer depth limit; L1/L2/L3 pruning tiers
- **§ 2 工具使用**: codegraph vs LSP vs ast-grep vs grep priority; codegraph init check; Memurai TTL policy (method 24h, chain 1h, annotation 7d); jadx fallback
- **§ 3 Filter/拦截器**: Filter chain order analysis; global Filter vs method-level annotations; Spring Security config audit
- **§ 4 业务逻辑**: CAS/OAuth2/SAML/LDAP protocol risks; password algorithms; session management
- **§ 5 WAF**: Early identification + bypass detection
- **§ 6 报告质量**: Data reconciliation; chain statistics; result-first report structure
- **§ 7 Agent 执行优化**: Subagent failure handling (3-strikes + 1h heartbeat); concurrency tuning (5 max); cache hit rate improvement
- **§ 8 自优化循环**: 5-dim scoring; convergence conditions; optimization persistence

**What exists in skills** (fragmented, not unified):
- `skills\java-whitebox-loop\rules\06-pruning-rules.md` — L1/L2 pruning only
- `skills\java-whitebox-loop\rules\04-redis-strategy.md` — TTLs only
- `skills\java-whitebox-loop\rules\07-preset-rules.md` (5564 bytes) — closest to unified preset

**What's missing**: A single consolidated preset knowledge file referenced by every agent, not 7 separate rule files. Target: `requirements\预置经验规则.md` content needs to become machine-loadable by all expert skills.

---

## 4. Key Constraints from Six Masters (must respect)

**Source**: `requirements\codex-工程答复-回应六位大师.md` + `requirements\codex-反思-重新审视马斯克.md` (post-reflection)

### Constraint #1 — Source/Sink/Sanitizer is **automatable, NOT a "too complex" excuse**
> "**马斯克的核心洞察'能用工具做的，不要用 LLM'基本正确。source/sink/sanitizer 的定义可以用工具解决（22 天工作量）。我之前用'工程复杂'作为借口停止了思考，这是错误的。**" — `codex-反思-重新审视马斯克.md` §8.2

**Implication**: Must build registry + extractor + annotator for source/sink/sanitizer instead of asking LLM each round. Java has ≤30 source patterns, ~120 sink patterns, 4 sanitizer categories — all finite. Per-week plan: Week 1 source registry, Week 2 sink registry, Week 3 sanitizer registry, Week 4 integration.

### Constraint #2 — False positive rate is the lifeline; **>30% = unpublishable**
> "**完全同意。这是整个分析中最重要的一个指标。** 当前系统没有假阳率的度量机制。这是最大的工程缺陷。" — 吴翰清 in `codex-工程答复-回应六位大师.md` §6.2

**Implication**: 
- FP-rate < 15% → publishable
- FP-rate > 30% → stop, fix FP patterns first
- 30 samples/round manual labeling is mandatory

### Constraint #3 — **Trust-type, not just tech-type** (吴翰清's most valuable insight)
> "当前的漏洞分类是按技术类型的（SQLi、XSS、RCE）。这个分类对开发者不友好——开发者不会想'我要防止 SQL 注入'，他们会想'我要不要信任这个输入'。" — `codex-工程答复-回应六位大师.md` §6.2

**Implication**: Each finding carries BOTH `tech_type` (SQL_INJECTION) AND `trust_type` (trusted_untrusted_input). Developer-oriented reporting. But original #6 was deleted: "原 trust_type 需求不保留" — see `README.md` "判定 6. 删除" — this is **a conflict** between master analysis and final 18-requirement list.

### Constraint #4 — Decouple, don't merge (architectural)
> "**漏洞发现与 PoC 验证解耦**... PoC 由后台 monitor 进程从缓存取 finished 调用链后调度 2 个专门 PoC agent 执行，状态回写缓存。" — `requirements\ai改动日志.md` archive, `README.md` §原子需求拆解

**Implication**: PoC never blocks main loop. poc-monitor.py polls `{groupId}:audit:finding:{chainId}:final` keys with `poc_status=pending`, dispatches 2 concurrent poc-verify agents, writes back to `{groupId}:audit:finding:{chainId}:verified`. Failure: dump state to `monitor-state.json` for daemon recovery.

### Constraint #5 — 80/20 sink detection, deferred to simplify first iteration
> "**sink 判定**: calledFQN 不以 groupId 开头 → 全是 sink | 80/20 简化, 先跑通流程, 后续可加 sink 模式表精准匹配" — `design-docs\chain-sql-engine.md` §关键设计决策

**Implication**: Don't pursue sink pattern precision in v1. All non-groupId calledFQNs become sinks. Pattern-table comes after end-to-end works.

---

## 5. Test Case Requirements for WebGoat-2025.3

The target testbed is **OWASP WebGoat 2025.3** (Spring Boot, groupId `org.owasp.webgoat`), an intentionally-vulnerable teaching application. From `暴露面扫描设计.md`, `chain-sql-engine.md`, `需求分析文档.md`, `预置经验规则.md`, `codex-工程答复-回应六位大师.md`:

### 5.1 必须验证的 Sink / Source Pairs

| 漏洞类型 | Source | Sink | WebGoat Lesson | 对应 Expert |
|---|---|---|---|---|
| **SQLi** | `@RequestParam String username` | `JdbcTemplate.query(String sql, ...)` | SqlInjection / SqlInjectionMitigations | `injection-audit` |
| **CMD Injection** | `@RequestParam String filename` | `ProcessBuilder.start()` / `Runtime.exec()` | PathTraversal / CommandInjection | `injection-audit` |
| **XXE** | `@RequestBody String xml` | `DocumentBuilderFactory.newInstance().newDocumentBuilder().parse()` | XXE lesson | `injection-audit` |
| **反序列化** | `@RequestBody byte[]` | `ObjectInputStream.readObject()` | InsecureDeserialization | `injection-audit` |
| **Path Traversal** | `@RequestParam String filename` | `new File(basePath, filename)` | PathTraversal | `file-audit` |
| **File Upload** | `MultipartFile file` | `file.transferTo(dest)` | FileUpload | `file-audit` |
| **业务逻辑越权** | `@RequestParam Integer userId` | `repository.findById(userId)` (no `@PreAuthorize`) | MissingAccessControl / IDOR | `business-logic-audit` |
| **Auth 绕过** | All routes | Filter chain missing `@WebGoatUserRequired` | Authentication bypass | `auth-chain-audit` |
| **登录弱凭证** | `/login` POST | plaintext compare / weak bcrypt cost | Password strength / login | `login-audit` |
| **WAF 绕过** | input with `UN/**/ION SEL/**/ECT` | WAF regex misses | WAF evasion lesson | `injection-audit` |

### 5.2 必须验证的 Sanitizer 反例

Per `预置经验规则.md` §1.2:
- **PreparedStatement + `setString()`** = effective sanitizer (true negative)
- **PreparedStatement + string concat in `prepareStatement()`** = no sanitizer (false negative trap)
- **`HtmlUtils.htmlEscape()` in non-HTML context** = ineffective
- **`Integer.parseInt()`** = absolute sanitizer (强类型转换)

### 5.3 必须验证的 Filter 链路径

Per `暴露面扫描设计.md` §2.4 and `预置经验规则.md` §3:
- `CSRFFilter` at order N1 → must catch CSRF on state-changing endpoints
- `AuthenticationFilter` at order N2 → must reject unauth requests
- `AuthorizationFilter` → must check role
- **WebGoatUserRequired** annotation (project-specific, groupId-prefixed) → goes into `specific.json`

### 5.4 必须通过的端到端断言

From `会话记录-最终方案.md` §验收 and `原子需求实现说明.md` §验收:

1. **Boss 真实并行派 ≥3 个端点主管** — verify via `cross-50r\round{N}.json` containing ≥3 supervisor spawns per round
2. **ralph-loop 在达标时提前终止** — read `convergence.json`, score ≥ 85, std < 3, reconcile PASS, coverage ≥ 95%
3. **Memurai 状态跨 agent 正确传递** — `{groupId}:audit:finding:{chainId}:final` → poc-verify → `{groupId}:audit:finding:{chainId}:verified` round-trip
4. **评分历史文件每轮追加** — `loop_audit\diag\scoring-history.jsonl` grows by 1 line per round, can compute std
5. **收敛判断正确触发** — kill signal via `convergence.json` file (not stdout magic string)
6. **P5.4 不变量** — 端点报告总数 == `project-context.json` 中 endpoints 总数；不等则 loop 不得终止
7. **75% 高危 PoC 通过** — per `L9` in `doc\atomic-requirements.md`

### 5.5 必须验证的 Pruning 正确性

Per `预置经验规则.md` §1.5:
- **L1 端点剪枝**: `/WebGoat/images/logo.png` (no params) → skip; `/api/user/{id}` where id is numeric → skip taint tracking, only business-logic audit
- **L2 链剪枝**: chain reaches `Integer.parseInt(input)` → mark "已消毒", skip deeper tracking
- **L3 跨轮次剪枝**: 上一轮已审计的方法 → 本轮跳过

### 5.6 必须展示的「80/20 Sink 简化策略」正确性

Per `chain-sql-engine.md` 对话5:
- 给定 `/SqlInjection/attack` → call chain ends at `JdbcTemplate.query`
- `query` 的 `calledFQN` 不以 `org.owasp.webgoat.` 开头 → 自动 sink
- 注入 `// sink: org.springframework.jdbc.core.JdbcTemplate#query` 注释后写 Memurai

### 5.7 必须满足的测试输入样本

From `chain-sql-engine.md` §2.1 (test fixture reference):
```
$ java -jar target/java-method-call-extractor-1.0.0.jar test-data/SampleService.java test-data
```
WebGoat-2025.3 should be the runtime target, with `test-data/` replaced by WebGoat's source paths under `src/main/java/org/owasp/webgoat/lessons/`.

---

## 6. Implementation Status — Bottom Line

**Designed**: 18 atomic requirements, full 3-layer agent architecture, 4-phase flow, Memurai namespace unification, convergence formula, scoring weights, sink 80/20 strategy, hashkey = nodes.id migration.

**Partially built** (~30% of plan):
- ✅ `scripts\ast\attack_surface_scanner.py` (40KB, md5 hashkey, scanner_utils.py, javaparser_bridge.py)
- ✅ `scripts\chain\sqlite-extract-chain.py` (CTE recursive)
- ✅ 7 audit skill SKILL.md files (injection/business-logic/file/auth-chain/login/poc-verify/threat-model-analyst)
- ✅ `skills\java-whitebox-loop\SKILL.md` + 7 rules/
- ✅ `scripts\redis\memurai_client.py` + batch-prefetch
- ✅ `loop_audit\_template\` (12 templates)
- ✅ `tools\java-method-call-extractor-1.0.0.jar` + `tools\attack-surface-scanner\`

**Missing entirely** (~70% of plan):
- ❌ `prompts\` directory (boss.md, supervisor.md, analyst.md, 5 expert prompts, verify.md — **none created**)
- ❌ `scripts\audit\poc-monitor.py` (159 lines per plan — **does not exist**)
- ❌ `scripts\redis\endpoint_supervisor_cache.py` (278 lines — **does not exist**)
- ❌ `skills\endpoint-supervisor\` skill — **does not exist**
- ❌ `skills\call-chain-audit-thinking\` skill — **does not exist**
- ❌ `scripts\chain\method_calls_extractor.py`, `chain_builder.py` — **do not exist**
- ❌ All `loop_audit\diag\` JSONL writers (scoring-history, optimization-suggestions, fp-rate, self-check)
- ❌ `knowledge.json` writer
- ❌ `convergence.json` reader/writer
- ❌ Source/sink/sanitizer registry + extractor (马斯克反思后判定必须做的 22-day work)
- ❌ Endpoint sort by sink-presence + POST > UPDATE > DELETE > GET
- ❌ SSH config check in Phase B
- ❌ Hardcoded credential CIA filter

**Critical path to WebGoat-2025.3 end-to-end**: The current `cross-agent-50r.py` (61KB) is still the pre-redesign monolithic loop. **None of the new 3-layer architecture is wired**. To pass any WebGoat lesson, the minimum viable path is:
1. Create `prompts\boss.md` with Phase A endpoint enumeration
2. Create `scripts\chain\chain_builder.py` + `method_calls_extractor.py` (sink 80/20)
3. Create `scripts\audit\poc-monitor.py` (background)
4. Create `scripts\redis\endpoint_supervisor_cache.py`
5. Create `skills\endpoint-supervisor\SKILL.md`
6. Migrate `attack_surface_scanner.py` to nodes.id hashkey
7. Wire `loop_audit\diag\scoring-history.jsonl` append in cross-agent-50r.py
8. Wire `convergence.json` signal in cross-agent-50r.py

---

## 7. File Path Map (quick reference)

### design-docs/
- `chain-sql-engine.md` (383 lines) — Draft for chain SQL engine (post scripts-refactor)
- `暴露面扫描设计.md` (309 lines) — Attack surface asset store/cache/reconciliation design
- `会话记录-最终方案.md` (718 lines) — Final design from ses_13e75c168ffe3BJlV80Xqaajlw
- `会话记录-人类发言.md` (379 lines) — 38 user messages from 2026-06-13/14
- `原子需求实现说明.md` (156 lines) — Per-requirement implementation guide
- `对话记录-ses_13b706bb5ffeAeyajAddb7yjIw.md` (182 lines) — Single prompt-to-file creation record

### requirements/
- `需求分析文档.md` (157 lines) — 7-layer real-need analysis from 390 sessions
- `codex-工程答复-回应六位大师.md` (504 lines) — Six-masters engineering response
- `codex-反思-重新审视马斯克.md` (533 lines) — Self-reflection correcting rejection of Musk
- `实现评估.md` (329 lines) — Current implementation evaluation (70% match, 30% gap)
- `预置经验规则.md` (443 lines) — 8-section LLM-missing-experience preset rules
- `哲学分析.md` (547 lines) — Socrates/Musk/Kant three-perspective analysis
- `ai改动日志.md` (23 lines) — AI change log; latest entry: 2026-06-15 folder Englishification
- `原子需求\分割1` (6569 bytes) — Backup of 原子需求实现说明.md (garbled encoding but identical content)

### Implementation files (partial)
- `scripts\ast\attack_surface_scanner.py` (40KB), `scanner_utils.py` (5.7KB), `javaparser_bridge.py` (14KB)
- `scripts\chain\sqlite-extract-chain.py` (7.3KB), `sqlite-multi-hop-search.py` (16.8KB), `sqlite-pattern-search.py` (7.5KB)
- `scripts\redis\memurai_client.py` (18.5KB), `redis-batch-prefetch.py`, `redis-self-check.py`, `redis-status-tracker.py`
- `scripts\audit\cross-agent-50r.py` (61KB, monolith)
- `skills\java-whitebox-loop\SKILL.md` (5.6KB) + 7 rules/
- `skills\{injection,business-logic,file,auth-chain,login,poc-verify}-audit\SKILL.md`
- `tools\java-method-call-extractor-1.0.0.jar`
- `loop_audit\_template\` (12 templates)
- `doc\atomic-requirements.md` (60+ raw requirements, source-of-truth enumeration)
- `README.md` (root doc, the project's fundamental charter)

---

**End of Summary**