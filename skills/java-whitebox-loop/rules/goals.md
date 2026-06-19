# 5 目标对齐 + 硬约束

Reference only by `java-whitebox-loop/SKILL.md`.

## 5 个根本目标

| # | 目标 | 实现机制 | 验证方式 |
|---|------|---------|----------|
| 1 | **漏洞准确 + 已验证** | 5 类 expert skill + poc-verify (CVSS 3.1) + poc-monitor.py 自动验证 high/critical | `reports/*.md` 中 `poc_status: verified` 比例 |
| 2 | **外部接口无遗漏** | Phase A `attack_surface_scanner.py` ast-grep + codegraph SQL 双重枚举 | `routes.json` 条目数 ≥ 实际端点数 |
| 3 | **调用链分析无遗漏** | Phase C `chain_builder.py` CTE RECURSIVE + `method_calls_extractor.py` + `// sink:` 内联 | `chains/*.json` 中 `total_nodes` 与 `total_edges` 比例 |
| 4 | **运行高效 + token 少** | 3 层 Agent 按需派发 + codegraph SQL 优先 + Memurai 跨轮复用 | `scoring-history.jsonl` 每轮 `compliance` 字段 |
| 5 | **工具自进化** | `self_evolution.py` 每轮写 6 类诊断 + `knowledge.json` | 跨两轮对比 knowledge 体积增长 + 分数递增 |

## 硬约束 (用户不可绕过)

- **3 个核心工具** (`codegraph` / `ast-grep` / `Memurai`) 任一不可用 → exit code 2, 不降级
- **`non-groupId` 调用 = sink** (`calledFQN` 不以 `groupId + "."` 开头即视为 sink, 在 `method_calls_extractor.py` 中硬编码)
- **输出目录** = `{loop_audit_dir}` (默认 `{project_root}/loop_audit`, 也可指向 `{agentloop_root}/projects/{group_id}/loop_audit` 避免污染源码树)
- **通用安全知识预置** = `projects/_template/06-通用安全知识.md` + `07-Sink表.json` + `08-Sanitizer表.json`
- **优先 codegraph SQL** = 调用链查询走 CTE RECURSIVE on `{codegraph_db}`, ast-grep 仅用于 Phase A 注解发现
- **4-AND 收敛** = score ≥ 85 + stddev < 3 + reconcile ≥ 10 + coverage ≥ 0.95 (同时满足才 break loop)
