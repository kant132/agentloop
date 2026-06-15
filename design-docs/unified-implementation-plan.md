# AgentLoop 统一实施计划(针对 WebGoat-2025.3 端到端测试)

> **生成日期**:2026-06-15
> **输入依据**:
> - `design-docs/exploration-summary-requirements.md`(318 行,18 条原子需求评估)
> - `design-docs/exploration-summary-tools.md`(305 行,工具/script/JAR 清单)
> - `design-docs/exploration-summary-testclone.md`(514 行,`D:\test-clone` JAR 集成)
> - `requirements/ai改动日志.md`(2026-06-15 文件夹英文化)
> - `design-docs/path-reference-fix-report.md`(175 处路径修复)
> - `design-docs/会话记录-最终方案.md`(718 行,ses_13e75c168ffe3BJlV80Xqaajlw 最终方案)
>
> **目标读者**:今晚/明早启动的 Sisyphus 子任务,需要把 AgentLoop 改造到能在 WebGoat-2025.3 跑通端到端审计的程度。
>
> **核心结论**(节省读者时间):
> 1. 18 条原子需求中 **0 条完全实现 / 7 条部分 / 11 条未实现**
> 2. 计划中"先 mock 后实现"的占位 = `cross-agent-50r.py`(1253 行单体)、`boss.md`(78 行精简稿)、所有 `loop_audit\diag\` JSONL 写入器
> 3. 本计划采取**双轨策略**:Wave 1-4 是结构性骨架(优先可观测),Wave 5-6 是真实端到端验证,Wave 7 是 Oracle 复审
> 4. `D:\test-clone\target\java-method-call-extractor-1.0.0.jar` 已编译、可用、需迁入 `tools\java-method-call-extractor\`

---

## § 1 现状诊断(Current State Diagnosis)

### 1.1 总体健康度(用数字说话)

| 维度 | 数字 | 来源 |
|---|---|---|
| 18 条原子需求 | ✅0 / ⚠️7 / ❌11 | exploration-summary-requirements.md §2 |
| 顶层目录已完成英文化 | 7/7 | ai改动日志.md |
| 路径引用修复 | 175 处 / 40 文件 | path-reference-fix-report.md |
| 设计文档 | 6 篇 = 1983 行 | `design-docs\*.md` 总和 |
| 已编译工具 JAR | 4 个 = 106 MB | `tools\` (arthas×2, javaparser×2) |
| Python 脚本 | 18 个 + 1 `__init__.py` ≈ 248 KB | `scripts\` 5 个子目录 |
| Skills 完整度 | 14/14,无 TODO 占位 | exploration-summary-tools.md §4 |
| TDD 测试覆盖 | 1 套(pytest for scanner_utils),规划 39 套,实际 ~10 套 | `scripts/tests/test_scanner_utils.py` 291 行 |
| 端到端跑通 | ❌ 未在 WebGoat-2025.3 上验证任何新架构 | "试点"状态 |

### 1.2 已完成 ✅(不要重复造轮子)

| 模块 | 文件 | 行数 | 状态 |
|---|---|---|---|
| 14 个 skill | `skills\*\SKILL.md` | ~80 KB | 无 TODO,生产级 |
| Memurai 客户端 | `scripts/redis/memurai_client.py` | 482 | `pipe_setex_batch` 1 子进程 N SETs |
| 攻击面扫描 | `scripts/ast/attack_surface_scanner.py` | 1216 | 2 阶段,需 md5 → nodes.id 迁移 |
| 调用链 CTE | `scripts/chain/sqlite-extract-chain.py` | 186 | RECURSIVE + 循环检测 |
| 5 个 SQL 模板 | `scripts/chain/sqlite-multi-hop-search.py` | 348 | 上限 20 JOIN |
| Boss daemon | `scripts/audit/cross-agent-50r.py` | 1253 | 50 轮 + 13 硬约束,但**仍是单体** |
| 验证脚本(3 套) | `verify-{endpoint,opencode,audit-poc}` | 181+275+95 | P5.4 / 合规 / PoC 质量 |
| 方法调用提取 JAR | `D:\test-clone\target\java-method-call-extractor-1.0.0.jar` | 6.1 MB fat-jar | 已编译,可用,但**未集成** |
| `webgoat-tools/` | 7 个文件 ≈ 245 KB | WebGoat 专属 | 5 个 .py + 2 个 JSON fixture |

### 1.3 Top 10 待重构(Broken / Incomplete / 必须改)

| # | 路径 | 行数 | 问题 | 影响 |
|---|---|---|---|---|
| 1 | `scripts/audit/cross-agent-50r.py` | 1253 | 单体老 6 阶段 loop,**目标精简到 ~200 行薄 wrapper**;计划删除大量手工代码,改用 skill + prompt 调度 | 阻塞 #2 / #12 全部收敛判定 |
| 2 | `skills/java-whitebox-loop/SKILL.md` | 78 行 | 超过 #14 的 ≤50 行目标;SSH 配置检查 / 端点排序挤占空间 | 阻塞 #14 |
| 3 | `skills/java-whitebox-loop/rules/` | 7 个文件 | 超过 #15 的 ≤3 文件目标;`01-phase-gates.md`、`04-redis-strategy.md`、`05-data-reconcile.md`、`06-pruning-rules.md` 应合并到主 SKILL 或外部 | 阻塞 #15 |
| 4 | `scripts/ast/attack_surface_scanner.py` | 1216 | 仍用 md5 hashkey,未迁移到 `nodes.id`(per chain-sql-engine.md §2);`validate()` 未被 daemon 自动调用 | 阻塞 #10 路由/注解持久化 |
| 5 | `scripts/chain/sqlite-multi-hop-search.py` | 348 | 设计文档明确标记 "**确认有问题**"(chain-sql-engine.md §3),LEFT JOIN 模板不可靠 | 阻塞 Phase C 调用链抽取 |
| 6 | `scripts/audit/poc-monitor.py` | **未创建** | 159 行(per 最终方案)→ 实际 0 文件;monitor 是独立后台进程,**当前不存在** | 阻塞 #2 解耦 |
| 7 | `scripts/redis/endpoint_supervisor_cache.py` | **未创建** | 278 行(per 最终方案)→ 实际 0 文件;主管缓存表全靠设想 | 阻塞 #2 / #10 / #13 |
| 8 | `scripts/audit/check_core_tools.py` | **未创建** | "TBD" 状态(最终方案 §10);不创建 → #17 不降级约束无法落地 | 阻塞 #17 |
| 9 | `loop_audit\diag\` 所有 JSONL | **目录可能不存在** | `scoring-history.jsonl` / `optimization-suggestions.jsonl` / `false-positive-samples.jsonl` / `self-check.json` / `convergence.json` / `knowledge.json` **全部缺** | 阻塞 #4 / #5 / #11 / #12 / #13 |
| 10 | `prompts/` 目录 | **整个目录不存在** | `boss.md` / `supervisor.md` / `analyst.md` / 5 个 expert prompt / `verify.md` 均无文件 | 阻塞整个三层 agent 架构落地 |

### 1.4 Top 10 待删除/合并(Redundant / Stale / 必须清理)

| # | 项 | 位置 | 删除/合并理由 |
|---|---|---|---|
| 1 | 老 `audit:{groupId}:*` 缓存命名空间 | `skills/java-forward-vuln-discovery/` 多文件、`tests/test_*` 脚本 | 已统一为 `{groupId}:audit:*`(最终方案 §1.3),旧引用全删 |
| 2 | `webgoat-tools/` 与 `scripts/ast/attack_surface_scanner.py` 重复 | `webgoat-tools/` 与 `scripts/ast/` | WebGoat 工具集应并入 `scripts/`,统一管理 |
| 3 | 6 阶段流程 SKILL 残留 | `skills/java-whitebox-loop/SKILL.md` 内仍有"Phase 1 doc/env → 2 threat → 3 filter → 4 endpoint → 5 chain → 6 self-opt" 字样 | 已重写为 4 Phase(端点枚举 / 安全上下文 / 漏洞发现 / 对账收敛) |
| 4 | 7 个 rules 子文件 | `skills/java-whitebox-loop/rules/01..07.md` | 合并为 ≤3(per #15);`07-preset-rules.md` 应挪到 `requirements/` 或独立 skill |
| 5 | `scripts/audit/three-philosophy-check.py` | 引用方 `boss-experience.md` §12.3 | 不创建 — 已被 #4 结构化自检替代 |
| 6 | `scripts/audit/batch-generate-route-reports.py` | 引用方 `boss-experience.md` §10 | 不创建 — 直接由 supervisor 生成,无需批量器 |
| 7 | `scripts/audit/finding-promoter.py` | 引用方 `java-forward-vuln-discovery` §4.1 step 7 | 不创建 — 5d×85 阈值取消(per "消失的 15 条"),文件直接落盘 |
| 8 | `D:\test-clone\` 整个目录 | `D:\test-clone\`(独立仓库) | JAR 已编译,源项目不必保留;迁 JAR 到 `tools\java-method-call-extractor\` 后删除 |
| 9 | 老 6 阶段 logging 模板 | `loop_audit/_template/` 部分 `.example` | 模板应反映 4 Phase 命名,与最终方案 §1.2 一致 |
| 10 | `requirements/原子需求/分割1` | `requirements\原子需求\分割1` (6569 字节) | 备份 + 乱码,与 `design-docs/原子需求实现说明.md` 重复;删除 |

### 1.5 命名空间不一致清单(全部归一化)

| 当前字符串 | 应改为 | 出现位置 |
|---|---|---|
| `audit:{groupId}:*` | `{groupId}:audit:*` | `skills/java-forward-vuln-discovery/*` |
| `audit:{groupId}:commit:{c}:method:*` | `{groupId}:audit:commit:{c}:method:*` | `scripts/redis/redis-batch-prefetch.py` |
| `audit:{groupId}:commit:{c}:prefetch:*` | `{groupId}:audit:commit:{c}:prefetch:*` | 同上 |
| `sup:{groupId}:*` | `{groupId}:sup:*` | 设想中,落地时直接用新 |

> **风险**:`exploration-summary-requirements.md` §3.4 描述的最终方案里所有 key 都是 `{groupId}:audit:*` 格式,但工具里仍按旧 `audit:{groupId}:*` 写入。**改格式前先全量 grep**,改完用 `redis-self-check.py` 验证 50 个 key。

---

## § 2 核心缺失的实现(Core Missing Implementations)

下面 5 个模块是从"用户目标"反推必须实现的核心。每条:当前状态 → 目标状态 → 实施计划。

### 2.1 暴露面发现(Attack Surface Discovery)

**当前状态**(Partial):
- `scripts\ast\attack_surface_scanner.py`(1216 行) — 2 阶段 OK,产出 `route_annotations.json`
- `scripts\ast\scanner_utils.py`(152 行) — 含 `inject_sink_comment` / `fqn_to_method_name` / `node_hash_key` / `classify_route` / `validate`
- `scripts\ast\annotation-categories.json`(62 行) — Spring + Jakarta + Micronaut + Dubbo 注解目录
- 设计完整:`design-docs/暴露面扫描设计.md`(309 行)— 规定 `项目\{groupId}\routes.json` 等目录 schema

**缺什么**:
1. `node_hash_key` 仍返回 md5,**未**改成 `nodes.id`(per chain-sql-engine.md §2)
2. `validate()` 函数有定义,**未被** daemon 自动调用
3. `routes.json` schema writer 缺,scanner 只输出 triples `(annotation_fqn, file, line)`
4. `项目\{groupId}\` 目录首次扫描时不自动创建
5. 端点排序逻辑(POST > UPDATE > DELETE > GET + 有 sink 优先)未在 scanner 内

**目标状态**(Target):
- `attack_surface_scanner.py` 改为返回 `nodes.id` 作为 hashkey
- 输出 schema 与 `暴露面扫描设计.md` §"目录结构" 完全对齐
- 自动创建 `项目\{groupId}\routes.json` / `chains\{sigHash}.json` / `filters.json` / `interceptors.json`
- 端点按 HTTP method + sink presence 双重排序

**实施计划**:

| 步骤 | 文件 | 函数 | 工时 |
|---|---|---|---|
| 2.1.a | `scripts/ast/scanner_utils.py` | `node_hash_key(file, line, sig) -> str` 改为 `nodes.id` lookup | 0.5h |
| 2.1.b | `scripts/ast/attack_surface_scanner.py` | 新增 `write_routes_json(groupId, triples, method, sink_score)` | 1h |
| 2.1.c | `scripts/ast/attack_surface_scanner.py` | 新增 `sort_endpoints(routes) -> routes` — POST > UPDATE > DELETE > GET,有 sink 优先 | 0.5h |
| 2.1.d | `scripts/ast/scanner_utils.py` | 暴露 `validate(routes, chains) -> bool` 给 daemon 调用 | 0.5h |
| 2.1.e | `projects/{groupId}/` | 首次扫描自动 mkdir(`groupId` 来自 `projects/{groupId}/preset.json`) | 0.25h |
| 2.1.f | 测试 | `scripts/tests/test_attack_surface_scanner.py` — WebGoat `SqlInjectionLesson` 端点应有 `priority=1` | 1h |

### 2.2 Chain Analysis(调用链分析)

**当前状态**(Partial):
- `scripts/chain/sqlite-extract-chain.py`(186 行)— CTE RECURSIVE,深度 ≤20,循环检测 ✅
- `scripts/chain/sqlite-multi-hop-search.py`(348 行)— 5 个预制模板,但**确认有问题**
- `scripts/chain/sqlite-pattern-search.py`(198 行)— 8 个 LIKE 模式
- `scripts/chain/chain-stats.py`(72 行)— 聚合统计

**缺什么**:
1. `scripts/chain/method_calls_extractor.py` — **未创建**;必须封装 `java-method-call-extractor-1.0.0.jar`(per `D:\test-clone\`)提供 `extract_method_calls_for_node(node_id, db_path, jar_path) -> list[str]`
2. `scripts/chain/chain_builder.py` — **未创建**;必须集成 CTE + jar 调用 + sink 提取 + Memurai 写入
3. sink 提取未接:每个 chain 节点应附带 `sinks: list[str]`(per 最终方案 §5.9)
4. chain ↔ route 自动关联:`{groupId}:audit:commit:{chainId}:prefetch` 写入逻辑缺

**目标状态**(Target):
- `method_calls_extractor.py` 封装 JAR(默认走 JPype 快路径,JAR 作为 fallback)
- `chain_builder.py` 实现主流程:输入 `(route, groupId) → 输出 (chain, sinks, written_keys)`
- `chain-stats.py` 增加 sink 统计维度
- chain ID = SHA256(`route_fqn` + `method_name` + `sig` 前 8 字节)

**实施计划**:

| 步骤 | 文件 | 函数 | 工时 |
|---|---|---|---|
| 2.2.a | `scripts/chain/method_calls_extractor.py`(新) | `extract_method_calls_for_file(file, source_root=None) -> list[dict]` | 0.5h |
| 2.2.b | 同上 | `extract_method_calls_for_node(node_id, db_path) -> list[str]` — 含 file 级缓存 | 0.5h |
| 2.2.c | 同上 | `filter_sinks(called_fqns, group_id) -> list[str]` — `not startswith(groupId + ".")` | 0.25h |
| 2.2.d | `scripts/chain/chain_builder.py`(新) | `build_chain(route, groupId, db_path) -> ChainResult` — 主流程编排 | 1h |
| 2.2.e | 同上 | `write_chain_to_memurai(chain, groupId) -> keys_written` | 0.5h |
| 2.2.f | `scripts/ast/scanner_utils.py` | `inject_sink_comment(method_body, sinks)` — 把 `// sink: FQN#sig` 注释注入 | 0.5h(已有函数,需管道) |
| 2.2.g | 修复 | `chain-sql-engine.md` L68 注释方向写反(JAR 0-based,codegraph 1-based,SQL `start_line - 1` 实际正确) | 0.1h |
| 2.2.h | 测试 | `scripts/tests/test_chain_builder.py` — WebGoat `VulnerableTaskHolder.readObject` chain 应输出 `Runtime.exec` 为 sink | 1h |

> **细节提示**:`exploration-summary-testclone.md` §5.1 验证 JAR 输出 `startLine=47` 对应源码 line 48(0-based)。SQL 写法是 `start_line - 1 = :startLine`,减 1 正好对齐,但注释方向反了,修文档即可,SQL 不动。

### 2.3 Sink Detection("非 groupId 就是 sink")

**当前状态**(Drafted, Not Implemented):
- 设计在 `design-docs/chain-sql-engine.md` 对话5: "calledFQN 不以 groupId 开头 → 全是 sink | 80/20 简化,先跑通流程,后续可加 sink 模式表精准匹配"
- `scanner_utils.py:inject_sink_comment()` 存在但无输入管道

**缺什么**:
1. 没有调用 JAR 的 wrapper(归 2.2)
2. 没有 80/20 过滤函数
3. 没有 sink-pattern 表(deferred 到 v2)

**目标状态**(Target):
- `filter_sinks(called_fqns, group_id) -> list[str]` 实现并被 `chain_builder.py` 调用
- 输出格式:`{"fqn": "...", "category": "SQLI|RCE|DESER|SSRF|...", "confidence": "high|medium"}`
- 注释注入:`// sink: org.springframework.jdbc.core.JdbcTemplate#query` 写入 method body 顶部

**实施计划**:

| 步骤 | 文件 | 函数 | 工时 |
|---|---|---|---|
| 2.3.a | `scripts/chain/method_calls_extractor.py` | `filter_sinks(called_fqns, group_id) -> list[str]`(同 2.2.c) | — |
| 2.3.b | `scripts/chain/chain_builder.py` | 主流程中调用 `filter_sinks` 并把 sinks 写入 `{groupId}:audit:commit:{chainId}:sinks` | 0.5h |
| 2.3.c | `scripts/ast/scanner_utils.py` | `inject_sink_comment` 改造为接 `chain.sinks` 列表,而非单 sink | 0.25h |
| 2.3.d | 文档 | `chain-sql-engine.md` 标注 "v1 80/20 策略已落地;sink 模式表 = v2 任务" | 0.1h |

> **不要做的事**:v1 不实现 sink 模式表(`requirements/预置经验规则.md` §1.1 的"已知 sink 字典"),避免拖延端到端跑通。

### 2.4 Self-Evolution Capability(自进化能力)

**当前状态**(Designed, Not Wired):
- 设计完整:每轮写 `loop_audit\diag\scoring-history.jsonl` / `optimization-suggestions.jsonl` / `false-positive-samples.jsonl` / `self-check.json` / `convergence.json`
- 跨轮次:`loop_audit\knowledge.json` + `{groupId}:knowledge:*` Memurai 命名空间

**缺什么**:
1. 所有 5 个 JSONL / JSON **写入器不存在**
2. `cross-agent-50r.py` 完全不调用任何写入器
3. 没有从 `{groupId}:knowledge:*` 合并到 `knowledge.json` 的脚本

**目标状态**(Target):
- 每轮结束:`scoring-history.jsonl` 追加 1 行,含 `round / coverage / poc_rate / reconcile / compliance / total`
- Phase D 末尾:`self-check.json` 写入结构化检查结果
- 收敛判断:`convergence.json` 写入 `{satisfied: true/false, conditions: {…}, score: N}`
- 经验持久化:有效的优化建议 → `optimization-suggestions.jsonl`;标注样本 → `false-positive-samples.jsonl`
- 跨轮次:`scripts/audit/merge_knowledge.py` 从 `{groupId}:knowledge:*` 合并到 `knowledge.json`

**实施计划**:

| 步骤 | 文件 | 函数 | 工时 |
|---|---|---|---|
| 2.4.a | `scripts/audit/append_scoring.py`(新) | `append_scoring(round, score_obj)` → `loop_audit/diag/scoring-history.jsonl` | 0.5h |
| 2.4.b | `scripts/audit/write_self_check.py`(新) | `write_self_check(phase_results)` → `loop_audit/diag/self-check.json` | 0.5h |
| 2.4.c | `scripts/audit/write_convergence.py`(新) | `evaluate_convergence(score_obj, prev_scores) -> {satisfied, conditions, score}` | 1h |
| 2.4.d | `scripts/audit/merge_knowledge.py`(新) | 从 `{groupId}:knowledge:*` 拉取,合并到 `loop_audit/knowledge.json` | 1h |
| 2.4.e | `scripts/audit/check_core_tools.py`(新) | 检查 codegraph / memurai / ast-grep,缺失 → 抛 `CoreToolMissing` | 0.5h |
| 2.4.f | `scripts/audit/cross-agent-50r.py` | 改造为薄 wrapper:在 Phase D 调用上述函数 | 1h |
| 2.4.g | `cross-agent-50r.py` | 主循环前调 `check_core_tools`,缺失即 `sys.exit(2)` | 0.25h |
| 2.4.h | 测试 | `scripts/tests/test_convergence.py` — 4 条件全过 → `satisfied=true` | 0.5h |

### 2.5 Preset Security Knowledge(通用安全知识)

**当前状态**(Partial / Fragmented):
- `requirements/预置经验规则.md`(443 行)— **整个文档就是 preset 知识**:8 节(污点分析 / 工具使用 / Filter / 业务逻辑 / WAF / 报告质量 / Agent 优化 / 自优化循环)
- 但散落在 7 个 rules/ 文件:`01-phase-gates.md` / `02-scoring.md` / `03-subagent-dispatch.md` / `04-redis-strategy.md` / `05-data-reconcile.md` / `06-pruning-rules.md` / `07-preset-rules.md`

**缺什么**:
1. **没有一个统一加载点**:每个 expert skill 启动时不知道读哪个文件
2. 7 个 rules 文件违反 #15(≤3 个)
3. 内容有重叠(`07-preset-rules.md` 跟 `预置经验规则.md` §1-7 重复)

**目标状态**(Target):
- 单一权威:`requirements/预置经验规则.md` 不动(已写完)
- `skills/java-whitebox-loop/rules/` 合并到 3 个:`00-core-rules.md`(污点 + 工具)/ `01-runtime-rules.md`(Filter + 业务逻辑 + WAF)/ `02-quality-rules.md`(报告 + Agent + 自优化)
- 每个 expert skill 启动时显式 `Read requirements/预置经验规则.md` + 对应 rules

**实施计划**:

| 步骤 | 文件 | 操作 | 工时 |
|---|---|---|---|
| 2.5.a | `skills/java-whitebox-loop/rules/00-core-rules.md`(新) | 合并 `01-phase-gates.md` + `04-redis-strategy.md` + `06-pruning-rules.md`(删除后 3 个) | 0.5h |
| 2.5.b | `skills/java-whitebox-loop/rules/01-runtime-rules.md`(新) | 合并 `03-subagent-dispatch.md` + `05-data-reconcile.md` | 0.5h |
| 2.5.c | `skills/java-whitebox-loop/rules/02-quality-rules.md`(新) | 改写自 `02-scoring.md` + `07-preset-rules.md`,引入 `requirements/预置经验规则.md` 完整引用 | 0.5h |
| 2.5.d | 删除 | `01..06,07` rules 文件 | 0.1h |
| 2.5.e | `skills/{injection,business-logic,file,auth-chain,login}-audit/SKILL.md` | 每个 SKILL.md 顶部加 "## 必读:`requirements/预置经验规则.md`" | 0.5h |
| 2.5.f | 文档 | `requirements/预置经验规则.md` 顶部加版本号 + "若改了请同步 `02-quality-rules.md` 引用" | 0.1h |

> **不要做的事**:不要重新写 preset 内容,直接复用 `requirements/预置经验规则.md`,只重组 rules 文件。

---

## § 3 测试 Case 设计(针对 WebGoat-2025.3 端到端)

### 3.1 测试目标

> "在 OWASP WebGoat 2025.3(Spring Boot, groupId=`org.owasp.webgoat`)上跑一遍 AgentLoop,产出包含真实漏洞的 `loop_audit/`,并由 Oracle 复审通过。"

### 3.2 端到端流程

```
[输入] D:\code\WebGoat-2025.3\
       ↓
[Step 1] bootstrap: cp projects/_template/preset.template.json → projects/org.owasp.webgoat/preset.json
         填字段:groupId=org.owasp.webgoat, project_root=D:\code\WebGoat-2025.3, max_rounds=10, appPort=8080
       ↓
[Step 2] 工具准备:
         - codegraph init && codegraph index (目标路径)
         - memurai-cli ping
         - ast-grep --version
         - check_core_tools.py 全部 PASS
       ↓
[Step 3] Daemon 启动 (cross-agent-50r.py --groupId org.owasp.webgoat --max-rounds 10)
         - 清空 Memurai: DEL org.owasp.webgoat:audit:*; DEL org.owasp.webgoat:sup:*
         - 启动 poc-monitor.py 后台
         - spawn Boss (java-whitebox-loop skill)
       ↓
[Step 4] Boss Phase A — 端点枚举:
         - 调 attack_surface_scanner.py 扫描 org.owasp.webgoat.* 路由
         - 输出 projects/org.owasp.webgoat/routes.json (含 priority 排序)
         - 产出 loop_audit/project-context.json
       ↓
[Step 5] Boss Phase B — 安全上下文:
         - SSH 到容器 (docker exec webgoat-local sh -c "cat /etc/tomcat/server.xml")
         - 调 codegraph SQL 找所有 Filter / Interceptor
         - 产出 loop_audit/security-context.json (含 ssh_config 块)
       ↓
[Step 6] Boss Phase C — 派主管 (并行, 每个高危端点 1 个):
         - spawn endpoint-supervisor (per endpoint)
         - supervisor 调 call-chain-audit-thinking → injection-audit / business-logic-audit / ...
         - findings 写 {groupId}:audit:finding:{chainId}:final (poc_status=pending)
       ↓
[Step 7] poc-monitor 独立轮询:
         - 每 5s 扫 org.owasp.webgoat:audit:finding:*:final (filter poc_status=pending)
         - 并发 2 个 poc-verify agent
         - 结果回写 {groupId}:audit:finding:{chainId}:verified
       ↓
[Step 8] Boss Phase D — 对账 + 收敛:
         - 计算 4 维 score = coverage×30% + poc_rate×30% + reconcile×25% + compliance×15%
         - 检查 4 个 AND 条件
         - 写 loop_audit/diag/convergence.json
         - 写 loop_audit/diag/scoring-history.jsonl (追加)
         - 写 loop_audit/diag/self-check.json
         - 合并 {groupId}:knowledge:* → loop_audit/knowledge.json
       ↓
[Step 9] Daemon wrapper:
         - 读 convergence.json
         - 4 条件全过 → break 循环
         - 否则 next round (max 10)
       ↓
[Step 10] Daemon 退出:
         - kill poc-monitor
         - 输出 loop_audit/reports/summary.md (1 页执行摘要)
```

### 3.3 预期产出结构(D:\agentloop\projects\org.owasp.webgoat\loop_audit\)

```
loop_audit/
├── project-context.json           ← Phase A: 端点总数 + priority 排序
├── security-context.json          ← Phase B: Filter 链 + ssh_config
├── findings/
│   └── {chainId}.json             ← 机器可读 finding
├── routes/
│   ├── 高风险端点/
│   │   └── {severity}_{fqn}__{method}__{sigHash}.md
│   ├── 中低险端点/
│   │   └── (同上命名)
│   └── poc/
│       └── {状态}_{等级}_{fqn.端点method-sink点-roundNNN}.md
├── reports/
│   ├── summary.md                 ← 1 页执行摘要
│   ├── api-audit/                 ← 按 API 方法展开
│   └── vuln-report/               ← 按 finding 展开
├── diag/                          ← 过程遥测
│   ├── findings.jsonl
│   ├── scoring-history.jsonl      ← 每轮 1 行
│   ├── pruning-log.jsonl
│   ├── false-positive-samples.jsonl ← 30 抽样本
│   ├── self-check.json            ← Phase D JSON
│   └── convergence.json           ← 4 条件判断结果
├── needs_human/                   ← 不可达 / 需人工确认
└── knowledge.json                 ← 跨轮次知识沉淀
```

### 3.4 必须验证的 Sink / Source 对(per exploration-summary-requirements.md §5.1)

| 漏洞类型 | WebGoat 端点 | Sink | Expert |
|---|---|---|---|
| SQLi | `/SqlInjection/attack` | `JdbcTemplate.query(String sql, ...)` | injection-audit |
| CMD Injection | `/CommandInjection/attack` | `ProcessBuilder.start()` / `Runtime.exec()` | injection-audit |
| XXE | `/XXE/attack` | `DocumentBuilderFactory.newInstance().newDocumentBuilder().parse()` | injection-audit |
| 反序列化 | `/InsecureDeserialization/attack` | `ObjectInputStream.readObject()` | injection-audit |
| Path Traversal | `/PathTraversal/attack` | `new File(basePath, filename)` | file-audit |
| File Upload | `/FileUpload/attack` | `file.transferTo(dest)` | file-audit |
| 业务逻辑越权 | `/IDOR/profile/{userId}` | `repository.findById(userId)` 无 `@PreAuthorize` | business-logic-audit |
| Auth 绕过 | 全部路由 | Filter 链缺 `@WebGoatUserRequired` | auth-chain-audit |
| 登录弱凭证 | `/login` POST | plaintext compare / weak bcrypt cost | login-audit |
| WAF 绕过 | WAF lesson | 拦截规则漏掉 `UN/**/ION SEL/**/ECT` | injection-audit |

### 3.5 必须通过的 7 项端到端断言

| # | 断言 | 验证方法 |
|---|---|---|
| 1 | Boss 真实并行派 ≥3 个 endpoint-supervisor | 读 `cross-50r/round{N}.json`,统计 `supervisor_spawn_count >= 3` |
| 2 | ralph-loop 在达标时提前终止 | 读 `convergence.json`,4 条件全过 → Daemon break |
| 3 | Memurai 状态跨 agent 正确传递 | `{groupId}:audit:finding:{chainId}:final` → poc-verify → `:verified` |
| 4 | 评分历史文件每轮追加 | `diag/scoring-history.jsonl` 每轮 +1 行 |
| 5 | 收敛判断正确触发 | `convergence.json` 字段含 `satisfied` / `conditions` / `score` |
| 6 | P5.4 不变量 | 端点报告总数 == `project-context.json` 中 endpoints 总数 |
| 7 | 75% 高危 PoC 通过 | `routes/poc/*.md` 中"是问题"占比 ≥ 75% |

### 3.6 失败回退

| 失败点 | 回退策略 |
|---|---|
| `check_core_tools.py` 缺 | 不跑,直接退(per #17) |
| `attack_surface_scanner` md5 → nodes.id 迁移卡 | 回滚 md5,但加 `TODO: 迁移 nodes.id` 标记 |
| chain_builder 输出空 sinks | 走 ast-grep 模式兜底(`scripts/ast/ast-finder.py` 7 模式) |
| poc-monitor 启动失败 | 走 inline PoC(同一进程内 verify,临时降级) |
| scoring-history.jsonl 写不出 | 内存保留,Phase D 输出后强制 flush |
| 收敛不满足 4 条件 | 自然进入下一轮(最多 10 轮,max_rounds=10) |

---

## § 4 自进化机制设计(Self-Evolution Mechanism)

### 4.1 学什么(What Learns)

每轮 AgentLoop 结束后,系统从三类信号中学习:

| 信号类型 | 来源 | 写入位置 | 用途 |
|---|---|---|---|
| **跨轮次项目知识** | Phase B 收集的注解 / sanitizer / 路由模式 | `loop_audit/knowledge.json` + `{groupId}:knowledge:*` | 下轮直接加载,避免重复扫描 |
| **有效优化** | Boss 反思中"被验证有效的"改进 | `loop_audit/diag/optimization-suggestions.jsonl` | 累积成"经验库",跨项目参考 |
| **假阳反例** | 30 抽样人工标注的真/假阳 | `loop_audit/diag/false-positive-samples.jsonl` | 训练下一轮的 sink 过滤精度 |
| **主管经验** | 协调判断后漏判 / 错误决策 | `{groupId}:sup:exp:*` | 同 groupId 下轮主管加载 |

### 4.2 持久化位置(Where)

```
loop_audit/                              ← 项目级,跨 groupId 不共享
├── knowledge.json                       ← 合并缓存的最终形态
├── diag/
│   ├── scoring-history.jsonl            ← 每轮追加 1 行 (round, score, dimensions)
│   ├── optimization-suggestions.jsonl   ← 每条 1 行 (timestamp, suggestion, verified)
│   ├── false-positive-samples.jsonl     ← 每条 1 行 (round, finding_id, label, reason)
│   └── self-check.json                  ← 每轮覆盖 (phase_results)
└── convergence.json                     ← 每轮覆盖 (4 条件判断)

Memurai:
{groupId}:knowledge:annotations          ← 注解清单
{groupId}:knowledge:sanitizers           ← 消毒器清单
{groupId}:knowledge:routes               ← 路由模式
{groupId}:knowledge:findings             ← 历史 finding 模式
{groupId}:sup:exp:{decision_id}          ← 主管经验(普遍性 vs 项目特有)
```

### 4.3 学习条目格式(Format)

**A. 知识条目**(写到 `knowledge.json`):
```json
{
  "annotations": [
    {"fqn": "org.springframework.web.bind.annotation.RestController", "kind": "route", "first_seen_round": 1}
  ],
  "sanitizers": [
    {"fqn": "org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder.matches", "effectiveness": "strong", "trap": null}
  ],
  "routes": [
    {"fqn": "org.owasp.webgoat.lessons.SqlInjectionLesson", "method": "attack", "http_method": "POST", "params": ["username"], "priority": 1}
  ],
  "findings": [
    {"chain_id": "abc123", "vuln_type": "SQL_INJECTION", "sink": "JdbcTemplate.query", "evidence_pattern": "string_concat_in_sql"}
  ]
}
```

**B. 优化条目**(写到 `optimization-suggestions.jsonl`):
```json
{"timestamp": "2026-06-15T10:23:00Z", "round": 3, "category": "performance", "suggestion": "用 batch-prefetch 替代单 key 预取", "verified_effective": true, "evidence": "p50 从 800ms 降到 120ms"}
```

**C. 假阳样本**(写到 `false-positive-samples.jsonl`):
```json
{"round": 3, "finding_id": "abc123", "label": "FP", "reason": "PreparedStatement + setString 正确使用", "screenshot": "poc/是问题_高_org...md"}
```

**D. 主管经验**(写到 `{groupId}:sup:exp:*`):
```json
{"decision_id": "sup-exp-001", "kind": "universal", "lesson": "派 injection-audit 前必须先确认参数类型;若 Integer.parseInt 已生效则跳过 injection 检查", "applicable_to": ["all Spring endpoints"]}
{"decision_id": "sup-exp-002", "kind": "project_specific", "lesson": "WebGoat 的 SqlInjectionMitigations 端点故意保留漏洞做对比,不要标 FP", "applicable_to": ["org.owasp.webgoat"]}
```

### 4.4 反馈回路(How Feeds Back)

```
[轮 N 启动]
  ↓
Boss spawn → load knowledge.json + {groupId}:knowledge:* + {groupId}:sup:exp:*
  ↓
Boss Phase A → 用 routes 模式直接命中已知路由(不再全量扫)
Boss Phase B → 用 sanitizers 清单跳过已确认强消毒
  ↓
[轮 N 运行] → findings 写 {groupId}:audit:finding:*:final
  ↓
poc-monitor → 验证 → 回写 {groupId}:audit:finding:*:verified
  ↓
[轮 N 结束]
  ↓
Boss Phase D → 蒸馏:
  - 新发现的注解/sanitizer/路由 → 写 {groupId}:knowledge:*
  - 验证有效的优化 → 写 diag/optimization-suggestions.jsonl
  - 抽 30 条 finding 标 FP/TP → 写 diag/false-positive-samples.jsonl
  - 主管漏判的经验 → 写 {groupId}:sup:exp:*
  ↓
Daemon wrapper → merge_knowledge.py:
  - 把 {groupId}:knowledge:* 合并到 loop_audit/knowledge.json
  - 检查 {groupId}:sup:exp:* universal 部分是否提升到 conduct/经验/
```

### 4.5 关键约束(per README §三)

- **只有老板可以修改执行路径**:新增脚本必须经老板派"工具子 agent",不能过拟合当前项目
- **每轮清空 `audit:` / `sup:` 但保留 `knowledge:`**:保证跨轮次学习不被污染
- **普遍性 vs 项目特有**:`sup:exp:*` 必须标注 `kind: universal | project_specific`,universal 部分才进 `conduct/经验/`

---

## § 5 通用性设计(Generality Design)

### 5.1 当前 AgentLoop 是项目无关还是 WebGoat 专用?

**结论**:骨架是通用的(`memurai_client.py` / `sqlite-extract-chain.py` / `attack_surface_scanner.py` 都不含 WebGoat 字样),但有几个地方硬编码:

| 硬编码点 | 位置 | 影响 | 必须抽象 |
|---|---|---|---|
| WebGoat 工具独立目录 | `webgoat-tools/`(7 个文件) | 与 `scripts/` 双目录,认知分裂 | 合并到 `scripts/` |
| Sink 模式表是 WebGoat 7 个 lesson | `scripts/ast/ast-finder.py` | 换项目就用不到 | 改为加载 `types/vuln-type/*.md` |
| 7 个 FWD 模板名字 FWD-A/B/C/D/INFO | `skills/java-forward-vuln-discovery/templates/` | 名字是模式,不是项目 | 保留,模式本身是通用 |
| `cross-agent-50r.py` 内的 50 轮硬编码 | `scripts/audit/cross-agent-50r.py` | 不通规模项目 | 改为 `--max-rounds N` 参数 |
| preset.template.json 17 字段 | `projects/_template/preset.template.json` | 每个项目必填 | 改为可继承 default |

### 5.2 必须存在的 `projects/_template/` 结构

```
projects/_template/
├── preset.template.json           ← 17 字段模板(groupId / project_root / max_rounds / appPort / …)
├── preset.template.json.example   ← 含 3 个示例值的版本,方便 cp
├── preset.schema.json             ← JSON Schema,daemon 启动时校验
├── audit-defaults.json            ← 通用默认:webMethod 排序、sink 表启用清单
├── vuln-type-mapping.json         ← groupId → 哪些 vuln-type 适用(框架类型识别)
└── README.md                      ← "如何为一个新项目准备 preset.json"
```

**字段标准化(per `cross-agent-50r.py` §load_preset)**:

| 字段 | 类型 | 必填 | 默认值 |
|---|---|---|---|
| `groupId` | string | ✅ | — |
| `project_root` | string | ✅ | — |
| `source_root` | string | ❌ | `{project_root}/src/main/java` |
| `web_method_port` | int | ❌ | 8080 |
| `max_rounds` | int | ❌ | 50 |
| `global_timeout_sec` | int | ❌ | 180000 |
| `per_round_timeout_sec` | int | ❌ | 3600 |
| `container_name` | string | ❌ | null |
| `external_prefix` | string | ❌ | null(默认自动探测) |
| `framework` | enum[spring, jakarta, micronaut, dubbo] | ❌ | spring |
| `enable_sink_table_v2` | bool | ❌ | false(80/20) |
| `fp_sample_size` | int | ❌ | 30 |
| `min_poc_pass_rate` | float | ❌ | 0.75 |
| `compliance_required` | bool | ❌ | true |
| `parallel_supervisors` | int | ❌ | 5 |
| `poc_concurrency` | int | ❌ | 2 |
| `knowledge_namespace` | string | ❌ | `{groupId}:knowledge` |

### 5.3 必须从硬编码中抽出的逻辑

| 当前硬编码 | 抽象位置 | 抽象后行为 |
|---|---|---|
| `sqlite-pattern-search.py` 8 个 LIKE 模式 | `types/vuln-type/*.md`(24 个 .md) | daemon 启动加载全部,允许 preset 关闭某些 |
| `ast-finder.py` 7 个 pattern | 同上 | 同上 |
| `attack_surface_scanner.py` annotation-categories.json | `types/framework-detect.json`(框架×注解目录二维表) | preset.framework=micronaut 时切换目录 |
| `cross-agent-50r.py` 50 轮硬编码 | preset.max_rounds 字段 | 小项目 max_rounds=5 |
| `memurai_client.py` 默认 host/port | preset.memurai_host / preset.memurai_port | 多租户隔离 |
| FWD-X 5 个 subagent 模板 | `skills/java-forward-vuln-discovery/templates/` | 名字保留,内容参数化 |

### 5.4 通用安全知识目录结构(`types/vuln-type/`)

`types/` 现有 24 个 .md,应重组为以下结构(本次不强制重写,但 wave 6 重构阶段对齐):

```
types/
├── vuln-type/
│   ├── SQL_INJECTION.md           ← 主条目:定义 / sink / sanitizer / 检测规则 / 反例
│   ├── CMD_INJECTION.md
│   ├── XXE.md
│   ├── INSECURE_DESERIALIZATION.md
│   ├── PATH_TRAVERSAL.md
│   ├── SSRF.md
│   ├── XSS.md
│   ├── WEAK_RANDOM.md
│   ├── HARDCODED_CREDENTIAL.md    ← 含 #9 CIA 限定逻辑
│   ├── AUTH_BYPASS.md
│   ├── IDOR.md
│   └── …(24 个)
├── framework-detect.json          ← 框架路由识别 pattern(Spring / Jakarta / Micronaut / Dubbo)
├── vuln-type-mapping.json         ← preset.framework → 启用的 vuln-type 子集
└── chain-template.json            ← 通用 chain 起点(sink + 关联算法 + sanitizer 注入位置)
```

### 5.5 通用 vs 项目特有的边界判定原则

| 判定 | 写到 |
|---|---|
| 任何框架(Spring/Quarkus/Micronaut)都适用 | `requirements/预置经验规则.md` 或 `conduct/经验/` |
| 仅当前 groupId 适用 | `{groupId}:sup:exp:*` 的 `kind: project_specific` 部分 |
| 一个项目教会另一个项目 | `conduct/经验/` 提炼 + `optimization-suggestions.jsonl` |
| 工具本身的改进(算法 / 性能) | `loop_audit/diag/optimization-suggestions.jsonl` + 经过若干项目验证后 → 沉淀到主 SKILL.md |

> **不要做的事**:不要在 `requirements/预置经验规则.md` 里写项目特有的内容(避免污染)。

---

## § 6 执行顺序(Wave-Based Plan)

> 总工时估算:**约 7 小时**(纯人工时间;AI 加速后约 3-4 小时)
> 每波独立可中断,Wave 间无需串行等待(除 Wave 6 → Wave 7)

### Wave 1 — 结构修复(30 分钟)

**目的**:消除 Wave 2+ 的执行阻塞

| 步骤 | 任务 | 工时 |
|---|---|---|
| 1.1 | 迁移 `D:\test-clone\target\java-method-call-extractor-1.0.0.jar` → `D:\agentloop\tools\java-method-call-extractor\java-method-call-extractor-1.0.0.jar` | 5 min |
| 1.2 | 删除 `D:\test-clone\` 整个目录 | 2 min |
| 1.3 | 删除 `D:\agentloop\requirements\原子需求\分割1`(备份+乱码) | 1 min |
| 1.4 | 合并 `webgoat-tools/` 5 个 .py 到 `scripts/audit/webgoat_helpers/`(新建子目录) | 10 min |
| 1.5 | 把 2 个 WebGoat JSON fixture 挪到 `scripts/audit/webgoat_helpers/fixtures/` | 2 min |
| 1.6 | 全量 grep `audit:{groupId}:*` → 替换为 `{groupId}:audit:*` | 10 min |

**验证**:`grep -r "audit:{groupId}" D:\agentloop\scripts\ D:\agentloop\skills\` 应返回 0 行

### Wave 2 — 核心基础设施(2 小时)

**目的**:让攻击面 + 调用链 + sink 提取跑通

| 步骤 | 任务 | 工时 |
|---|---|---|
| 2.1 | 改 `scanner_utils.py:node_hash_key` 为 `nodes.id` 查询 | 15 min |
| 2.2 | `scripts/ast/attack_surface_scanner.py` 新增 `write_routes_json()` + `sort_endpoints()` | 30 min |
| 2.3 | 创建 `scripts/chain/method_calls_extractor.py`(extract_method_calls_for_file + extract_method_calls_for_node + filter_sinks) | 45 min |
| 2.4 | 创建 `scripts/chain/chain_builder.py`(build_chain + write_chain_to_memurai) | 30 min |

**验证**:
- 在 WebGoat `VulnerableTaskHolder.java` 上跑 `method_calls_extractor.py`,应输出 20 条记录(per exploration-summary-testclone.md §4.2)
- `chain_builder.py --route org.owasp.webgoat.lessons.SqlInjectionLesson.attack --groupId org.owasp.webgoat` 应输出 chain 含 sink `JdbcTemplate.query`

### Wave 3 — 暴露面发现 + Preset 知识(1 小时)

**目的**:让"知识加载"和"端点排序"可工作

| 步骤 | 任务 | 工时 |
|---|---|---|
| 3.1 | `projects/_template/` 加 `preset.schema.json` + `audit-defaults.json` + `vuln-type-mapping.json` + `README.md` | 20 min |
| 3.2 | 合并 `skills/java-whitebox-loop/rules/` 到 3 个(per #15) | 15 min |
| 3.3 | 每个 expert skill SKILL.md 顶部加 "## 必读:`requirements/预置经验规则.md`" | 15 min |
| 3.4 | 修 `chain-sql-engine.md` L68 注释方向(0-based vs 1-based 文档反向) | 5 min |
| 3.5 | 创建 `scripts/audit/check_core_tools.py`(per #17) | 15 min |

**验证**:`ls skills/java-whitebox-loop/rules/ | wc -l` 应返回 3;`python scripts/audit/check_core_tools.py` 在缺 ast-grep 时应退出码 2

### Wave 4 — 自进化机制(1 小时)

**目的**:让 scoring / convergence / knowledge 全部可写可读

| 步骤 | 任务 | 工时 |
|---|---|---|
| 4.1 | 创建 `scripts/audit/append_scoring.py`(scoring-history.jsonl 追加) | 10 min |
| 4.2 | 创建 `scripts/audit/write_self_check.py`(self-check.json) | 10 min |
| 4.3 | 创建 `scripts/audit/write_convergence.py`(4 条件判断) | 20 min |
| 4.4 | 创建 `scripts/audit/merge_knowledge.py`(缓存 → knowledge.json) | 15 min |
| 4.5 | 创建 `scripts/audit/sample_for_fp.py`(每轮 30 抽 + 人工标注 → false-positive-samples.jsonl) | 15 min |

**验证**:
- `python write_convergence.py --fake '{"score":{"total":85,"std":2,"reconcile":10,"coverage":0.96}}'` 应输出 `{"satisfied": true, …}`
- `python merge_knowledge.py --groupId test` 应生成空 `knowledge.json` 含所有 schema 字段

### Wave 5 — WebGoat 端到端测试(1 小时)

**目的**:真实跑一遍,产出 `loop_audit/`

| 步骤 | 任务 | 工时 |
|---|---|---|
| 5.1 | bootstrap: `cp projects/_template/preset.template.json projects/org.owasp.webgoat/preset.json` + 填字段 | 5 min |
| 5.2 | `codegraph init && codegraph index` 在 `D:\code\WebGoat-2025.3\` | 10 min |
| 5.3 | 跑 `attack_surface_scanner.py` + `chain_builder.py` 验证产出 | 10 min |
| 5.4 | 启动 daemon 跑 1 轮(round=1),检查 `loop_audit/diag/` 全部 JSONL/JSON 是否产出 | 25 min |
| 5.5 | 检查 §3.5 的 7 项端到端断言 | 10 min |

**验证**:Wave 5 完成后,`D:\agentloop\projects\org.owasp.webgoat\loop_audit\` 应包含:
- `project-context.json`(routes 数 ~80)
- `security-context.json`(含 ssh_config)
- `findings/`(至少 3 个 JSON)
- `routes/高风险端点/`(至少 5 个 .md)
- `routes/poc/`(至少 1 个 .md)
- `diag/scoring-history.jsonl`(1 行)
- `diag/convergence.json`(`satisfied: false` 因 1 轮)
- `knowledge.json`(含 annotations / sanitizers / routes / findings 4 个数组)

### Wave 6 — Refactor + Cleanup(1 小时)

**目的**:删除冗余,精简到目标行数

| 步骤 | 任务 | 工时 |
|---|---|---|
| 6.1 | 精简 `cross-agent-50r.py`(1253 → ~200 行薄 wrapper) | 30 min |
| 6.2 | 精简 `skills/java-whitebox-loop/SKILL.md`(78 → ≤50 行) | 15 min |
| 6.3 | 合并 `attack_surface_scanner.py`(1216 → ~800 行,提取 helper) | 15 min |

**验证**:`wc -l scripts/audit/cross-agent-50r.py skills/java-whitebox-loop/SKILL.md` 应返回 ≈200, ≤50

### Wave 7 — Oracle 复审

**目的**:第三方独立审计,确认所有 18 条原子需求实现

| 步骤 | 任务 | 工时 |
|---|---|---|
| 7.1 | 派 1 个 oracle agent 读 `design-docs/unified-implementation-plan.md` + `requirements/ai改动日志.md` + `loop_audit/`,逐条核对 18 条原子需求 | 30 min |
| 7.2 | 输出 `design-docs/oracle-review-2026-06-15.md`,列出 ✅ / ⚠️ / ❌ | 10 min |
| 7.3 | 若有 ❌,回滚到对应 Wave 修补 | 由 oracle 报告决定 |

**验证**:oracle 输出"全 18 条至少部分实现,7 项端到端断言全过"

---

## § 7 风险与应对(Risks & Mitigations)

### Risk 1 — `cross-agent-50r.py` 重构引入回归(概率:高 / 影响:高)

**描述**:精简 1253 → 200 行过程中,可能丢失 50 轮稳定性实验积累的边界情况处理(如超时嵌套、kill 子进程、状态恢复)。

**缓解**:
- 6.1 步分两步:先 git commit 当前状态 → 创建 `cross-agent-50r.py.new` → diff 验证不丢 13 硬约束 → 替换
- 不在精简过程中加新功能(only 删除 / 提取)
- 测试:`python cross-agent-50r.py --dry-run --groupId test --max-rounds 1` 跑通即可

### Risk 2 — `attack_surface_scanner.py` md5 → nodes.id 迁移破坏现有调用方(概率:中 / 影响:中)

**描述**:当前 md5 hashkey 被 `scanner_utils.py:validate()` / `inject_sink_comment()` / cache key 等多处引用,改了之后格式不兼容。

**缓解**:
- 2.1 步保留旧函数为 `node_hash_key_legacy()`,加 `@deprecated`,默认走新 `node_hash_key()`
- 旧调用方在 wave 6 一并迁移
- 加 unit test:`scripts/tests/test_attack_surface_scanner.py` 锁定 hashkey 格式

### Risk 3 — `method_calls_extractor.py` JAR 启动开销影响批量场景(概率:高 / 影响:中)

**描述**:WebGoat 全项目 ~500 个 .java 文件,JAR 启动开销 ~1.5s/文件 → 总计 ~750s(12.5 分钟)。

**缓解**:
- 默认走 `javaparser_bridge.py`(JPype,一次启动,批量 ~10s/100 文件)10x+ 快
- JAR 作为 `--backend=jar` fallback
- 加 `--parallel N` 参数(`concurrent.futures.ThreadPoolExecutor`),JPype 模式下安全

### Risk 4 — `poc-monitor.py` 独立后台进程与 daemon 生命周期不同步(概率:中 / 影响:中)

**描述**:daemon 退出时 monitor 可能残留;反之 monitor 崩溃后 daemon 不知情,继续写入 `:verified` 失败。

**缓解**:
- monitor 启动时写 PID 到 `loop_audit/diag/monitor.pid`
- daemon 退出前读 PID + `taskkill /T /F /PID`(per `bfs-taint-tracer-v3.3.4-practice.md`)
- monitor 启动时检测 `monitor.pid` 已存在 → 提示 + 复用 or 拒绝
- monitor 异常退出时 dump 状态到 `monitor-state.json`

### Risk 5 — 收敛判断误触发导致循环过早终止(概率:中 / 影响:高)

**描述**:若 `convergence.json` 写错(例如 `satisfied: true` 但 `coverage: 0.5`),daemon 会立即 break,丢失后续审计。

**缓解**:
- `write_convergence.py` 内部加 invariant assertion:`coverage >= min_coverage` 必须显式配置
- daemon 读 `convergence.json` 后必须再独立重算(交叉验证)
- 加 `--force-max-rounds N` 强制参数:即使收敛,也要跑满 N 轮才退出(debug 模式)

### Risk 6 — `requirements/预置经验规则.md` 内容老化(概率:低 / 影响:中)

**描述**:443 行内容是 2026-06-13 写的,工具演进 6 周后可能与现状脱节(如 #14 SKILL ≤50 行实际做不到)。

**缓解**:
- 每季度人工 review 一次(写在 `ai改动日志.md` 顶部)
- `02-quality-rules.md` 顶部加 "本文件基于 `requirements/预置经验规则.md` v{date},过期请更新源"
- 不在 preset / loop_audit 里复制该文件内容,只引用

### Risk 7 — WebGoat 容器起不来 / 网络隔离(概率:中 / 影响:高)

**描述**:Wave 5 需要真实 WebGoat 2025.3 容器 + 网络通,若环境不可用 → 整个端到端失败。

**缓解**:
- 5.1 步前先验证 `docker ps | grep webgoat-local`
- 若无容器 → 走 `webgoat-tools/` 内 `setup-webgoat-container.sh` 启动
- 实在不行 → 跑 "静态审计模式":只产 `project-context.json` + `routes/*.md`,跳过 `routes/poc/`,7 项断言降到 5 项

---

## § 8 过程反思模板(Process Reflection Template)

> **使用时机**:今晚/明早 wave 7 完成后,由 Sisyphus 子任务填写,作为给用户的"执行摘要"。
> **目标读者**:用户(明早阅读)+ 下一次 AgentLoop 迭代
> **预期长度**:300-500 行 markdown

### 8.1 模板结构

```markdown
# AgentLoop WebGoat-2025.3 实施摘要

> **执行日期**:2026-06-15
> **执行者**:Sisyphus 子任务 ID(ses_XXX)
> **目标**:在 WebGoat-2025.3 上跑通端到端审计
> **输入文档**:design-docs/unified-implementation-plan.md

## § 1 执行过程(What Was Done)

### 1.1 Wave 完成情况(用表)

| Wave | 计划工时 | 实际工时 | 完成度 | 关键产出 |
|---|---|---|---|---|
| Wave 1: 结构修复 | 30 min | __ min | ✅/⚠️/❌ | __ |
| Wave 2: 核心基础设施 | 2h | __ h | ✅/⚠️/❌ | __ |
| Wave 3: 暴露面 + Preset | 1h | __ h | ✅/⚠️/❌ | __ |
| Wave 4: 自进化机制 | 1h | __ h | ✅/⚠️/❌ | __ |
| Wave 5: 端到端测试 | 1h | __ h | ✅/⚠️/❌ | __ |
| Wave 6: Refactor | 1h | __ h | ✅/⚠️/❌ | __ |
| Wave 7: Oracle 复审 | 30 min | __ min | ✅/⚠️/❌ | oracle-review-2026-06-15.md |

### 1.2 关键文件清单(按改动分类)

**新增**:
- D:\agentloop\tools\java-method-call-extractor\java-method-call-extractor-1.0.0.jar(从 D:\test-clone\ 迁移)
- D:\agentloop\scripts\chain\method_calls_extractor.py
- D:\agentloop\scripts\chain\chain_builder.py
- D:\agentloop\scripts\audit\check_core_tools.py
- D:\agentloop\scripts\audit\append_scoring.py
- D:\agentloop\scripts\audit\write_self_check.py
- D:\agentloop\scripts\audit\write_convergence.py
- D:\agentloop\scripts\audit\merge_knowledge.py
- D:\agentloop\scripts\audit\sample_for_fp.py
- D:\agentloop\scripts\audit\webgoat_helpers\(从 webgoat-tools/ 合并)
- D:\agentloop\projects\_template\preset.schema.json
- D:\agentloop\projects\_template\audit-defaults.json
- D:\agentloop\projects\_template\vuln-type-mapping.json
- D:\agentloop\projects\_template\README.md

**修改**:
- D:\agentloop\scripts\ast\scanner_utils.py: node_hash_key 改为 nodes.id 查询
- D:\agentloop\scripts\ast\attack_surface_scanner.py: 新增 write_routes_json + sort_endpoints
- D:\agentloop\scripts\audit\cross-agent-50r.py: 1253 → ~200 行
- D:\agentloop\skills\java-whitebox-loop\SKILL.md: 78 → ≤50 行
- D:\agentloop\skills\java-whitebox-loop\rules\: 7 → 3 文件

**删除**:
- D:\test-clone\(整个目录)
- D:\agentloop\requirements\原子需求\分割1
- D:\agentloop\webgoat-tools\(合并到 scripts/audit/webgoat_helpers/)
- D:\agentloop\skills\java-whitebox-loop\rules\01..06,07.md(7 个)
- 全量替换 `audit:{groupId}:*` → `{groupId}:audit:*`

## § 2 决策记录(Decisions Made + Reasoning)

### 2.1 关键技术决策

| # | 决策 | 选项 | 选定 | 理由 |
|---|---|---|---|---|
| 1 | JAR vs JPype | JAR(1.5s/文件)/ JPype(0.1s/文件) | **JPype 默认,JAR fallback** | 批量场景 10x+ 快(Risk 3) |
| 2 | hashkey 迁移 | md5 → nodes.id | **nodes.id** | chain-sql-engine.md §2 要求 |
| 3 | sink 模式表 | v1 80/20 vs v2 模式表 | **80/20** | 先跑通流程,模式表 deferred |
| 4 | preset 字段数 | 17 字段 vs 精简到 10 | **17 字段 + schema** | 兼容现有 `cross-agent-50r.py §load_preset` |
| 5 | knowledge.json 合并时机 | Phase D 内 vs Daemon 末尾 | **Daemon 末尾** | 避免 Phase C 读未完整 knowledge |

### 2.2 偏离原计划的地方(如有)

| # | 计划 | 实际 | 原因 |
|---|---|---|---|
| __ | __ | __ | __ |

## § 3 遇到的障碍(Obstacles Encountered)

### 3.1 技术障碍(可记录在文档里)

| # | 障碍 | 临时解决方案 | 是否彻底解决 |
|---|---|---|---|
| __ | __ | __ | ✅/⚠️/❌ |

### 3.2 流程障碍(经验教训)

| # | 障碍 | 应对 |
|---|---|---|
| __ | __ | __ |

## § 4 反思(What Should Have Been Different)

### 4.1 哪些 Wave 估算不准?

### 4.2 哪些决策应该早做 / 晚做?

### 4.3 哪些文档应该先写后写?

### 4.4 与"如果重新开始"的差异

```markdown
如果重新开始,我会:
1. ___
2. ___
3. ___
```

## § 5 给未来的建议(Suggestions for Next Iteration)

### 5.1 阻塞性建议(P0)

1. **sink 模式表**:v1 80/20 误报较多(预计 30% FP),建议 6 周后启动 v2 模式表
2. **FWD-X accuracy regression**:`bfs-taint-tracer-v3.3.5-practice.md` 记录 v3.3.5 从 6/10 跌到 1/10,需回归 raw API 调用方式
3. **P5.4 不变量 + Daemon 退出**:`cross-agent-50r.py` 退出前必须 cross-check routes/*.md 数量

### 5.2 体验性建议(P1)

1. preset.template.json 应提供 `--interactive` 模式,daemon 启动时交互式填充字段
2. `loop_audit/reports/summary.md` 应自动生成(目前是手动)
3. oracle agent 应并行复审 + 写报告(目前是顺序)

### 5.3 长期演进建议(P2)

1. 考虑迁移到 Go 实现 daemon(Python 启动开销 200-500ms 累积可观)
2. `types/vuln-type/` 24 个 .md 应转 JSON Schema(便于 preset.framework 校验)
3. 探索 Memurai → KeyDB / DragonflyDB 替代(开源、跨平台)

## § 6 数字汇总(Numbers)

| 指标 | 数值 |
|---|---|
| 新增文件数 | __ |
| 修改文件数 | __ |
| 删除文件数 | __ |
| 新增代码行数 | __ |
| 删除代码行数 | __ |
| Wave 5 跑出的 finding 数 | __ |
| 端到端 7 项断言通过数 | __/7 |
| Oracle 复审 18 条原子需求 | __✅/__⚠️/__❌ |
| 自进化写入的 `loop_audit/diag/` 文件数 | __ |
| 单次 daemon run 耗时 | __ min |
| Token 消耗(估算) | __ K |

## § 7 附录

### 7.1 关键文件链接

- D:\agentloop\design-docs\unified-implementation-plan.md(本计划)
- D:\agentloop\requirements\ai改动日志.md(本次新增条目)
- D:\agentloop\projects\org.owasp.webgoat\loop_audit\(WebGoat 端到端产出)
- D:\agentloop\design-docs\oracle-review-2026-06-15.md(Wave 7 输出)

### 7.2 下一次 AgentLoop 迭代入口

- D:\agentloop\README.md §三 18 条原子需求(下次重点:8/9/11/12/13/16 这 6 条仍部分实现)
- D:\agentloop\requirements\预置经验规则.md(下季度 review)
```

### 8.2 使用说明

1. **必填**:`§1.2 关键文件清单`、`§2.1 关键技术决策`、`§3 遇到的障碍`、`§4.4 如果重新开始`、`§5.1 阻塞性建议`、`§6 数字汇总`
2. **可选填**:`§2.2 偏离原计划`、`§4.1-4.3 反思细节`(仅当偏离大时填)
3. **强制**:`§6 数字汇总` 必须有具体数字,**不可写 "TBD"**
4. **完成后**:
   - 把摘要写入 `D:\agentloop\design-docs\execution-summary-2026-06-15.md`
   - 在 `D:\agentloop\requirements\ai改动日志.md` 末尾追加本次条目
   - 通知用户

---

## § 附录 A — 关键文件总览(实施时必读)

| 文件 | 行数 | 用途 |
|---|---|---|
| `README.md` | ~120 | 项目根本 + 18 条原子需求 + 输出模板约束 |
| `design-docs/会话记录-最终方案.md` | 718 | 718 行权威设计 |
| `design-docs/chain-sql-engine.md` | 383 | 调用链 SQL 引擎 + sink 80/20 决策 |
| `design-docs/暴露面扫描设计.md` | 309 | 攻击面资产存储 |
| `design-docs/exploration-summary-requirements.md` | 318 | 本次需求维度评估 |
| `design-docs/exploration-summary-tools.md` | 305 | 本次工具维度盘点 |
| `design-docs/exploration-summary-testclone.md` | 514 | 本次 JAR 集成探索 |
| `requirements/预置经验规则.md` | 443 | 8 节 preset 知识(权威) |
| `requirements/codex-工程答复-回应六位大师.md` | 504 | 六位大师工程答复 |
| `requirements/codex-反思-重新审视马斯克.md` | 533 | 反思 + 22 天 source/sink/sanitizer 工作 |
| `doc/atomic-requirements.md` | 139 | 60+ 原始需求(最终精炼为 18) |
| `doc/SDD.md` | 208 | 软件设计文档(3 目标 / 11 约束 / 4 阈值) |
| `doc/boss-experience.md` | 783 | Boss 自经验(13 硬约束) |
| `skills/java-whitebox-loop/SKILL.md` | 78 | 老板 skill(目标 ≤50 行) |
| `scripts/audit/cross-agent-50r.py` | 1253 | Daemon(目标 ~200 行) |

## § 附录 B — 与 README §三"输出模板约束"对齐检查

| README 要求 | 当前 | 目标 | 实施 Wave |
|---|---|---|---|
| `project-context.json` | ❌ 未产出 | ✅ Wave 2.1.b | 2 |
| `security-context.json` | ❌ 未产出 | ✅ Wave 5.1 | 5 |
| `findings/{chainId}.json` | ❌ | ✅ Wave 2.2 | 2 |
| `routes/高风险端点/*.md` | ❌ | ✅ Wave 5.4 | 5 |
| `poc/*.md` | ❌ | ✅ Wave 4.4 | 4 |
| `diag/findings.jsonl` | ❌ | ✅ Wave 5.4 | 5 |
| `diag/scoring-history.jsonl` | ❌ | ✅ Wave 4.1 | 4 |
| `diag/pruning-log.jsonl` | ❌ | ✅ Wave 2 | 2 |
| `diag/false-positive-samples.jsonl` | ❌ | ✅ Wave 4.5 | 4 |
| `needs_human/` | ❌ | ✅ Wave 5 | 5 |
| `knowledge.json` | ❌ | ✅ Wave 4.4 | 4 |
| 文件命名 `{sev}_{fqn}__{method}__{sigHash}.md` | ⚠️ 部分 | ✅ Wave 5.4 | 5 |
| P5.4 不变量 | ⚠️ verify-endpoint-coverage.py 有 | ✅ Wave 6.1 | 6 |

---

**End of Unified Implementation Plan**
