# 入口契约 (Invocation / Params / Outputs / Scripts / Paths)

Reference only by `java-whitebox-loop/SKILL.md`.

## 调用方式

### 方式 1 — preset.json 自动触发 (推荐)

```powershell
python {agentloop_root}/scripts/audit/cross-agent-50r.py \
  --preset {agentloop_root}/projects/{group_id}/preset.json
```

### 方式 2 — Opencode SKILL 触发

触发词: `白盒审计` / `启动 loop` / `安全审计编排` (任一即可)。

## Preset 参数 (由 preset.json 承载)

| 键 | 必需 | 说明 | 示例 |
|----|------|------|------|
| `groupId` | 是 | Java 项目 Maven groupId | `org.owasp.webgoat` |
| `projectRoot` | 是 | 源码根目录 | `D:\code\WebGoat-2025.3` |
| `codegraphDb` | 是 | codegraph SQLite 路径 (已索引) | `<project_root>/.codegraph/codegraph.db` |
| `loopDir` | 否 (默认 `loop_audit`) | 输出目录名 (相对 `projectRoot`) 或绝对路径 | `loop_audit` |
| `appPort` | 否 (默认 8080) | 被测应用端口 | `8080` |
| `appCtxPath` | 否 (默认 `""`) | Context path | `/WebGoat` |
| `sessionCookieName` | 否 (默认 `JSESSIONID`) | 会话 Cookie 名 | `JSESSIONID` |

模板见 `{agentloop_root}/projects/_template/preset.template.json`。

## 产出 (全部在 `{loop_audit_dir}/` 下)

| 产物 | 内容 | 阶段 |
|------|------|------|
| `routes.json` | 暴露面端点清单 + sink 标注 | A |
| `security-context.json` | Filter 链 / SecurityFilterChain beans / 注解富化 | B |
| `chains/{sig_hash}.json` | 每条调用链 (含 `// sink: <FQN>` 注释内联) | C |
| `reports/{finding}.md` | 每个 finding 的 Markdown 报告 (含 PoC / CVSS) | C |
| `poc/{finding}_poc.json` | PoC 验证原始结果 | C + poc-monitor |
| `diag/scoring-history.jsonl` | 每轮评分 | D |
| `diag/convergence.json` | 收敛判定 (4-AND) | D |
| `diag/self-check.json` | 每轮 7+1 项对账自检 | D |
| `diag/false-positive-samples.jsonl` | 假阳率抽样 (每 5 轮, 30 条) | D |
| `diag/needs_human-review/*.jsonl` | 给人工标注的 finding | D |
| `diag/optimization-suggestions.jsonl` | 工具自进化建议 | D |
| `knowledge.json` | 跨轮知识沉淀 | D |

## 关键脚本依赖

| 脚本 | 路径 | Phase |
|------|------|-------|
| `attack_surface_scanner.py` | `{agentloop_root}/scripts/ast/` | A |
| `annotated-source-enricher.py` | `{agentloop_root}/scripts/ast/` | B |
| `chain_builder.py` | `{agentloop_root}/scripts/chain/` | C |
| `method_calls_extractor.py` | `{agentloop_root}/scripts/chain/` | C |
| `poc-monitor.py` | `{agentloop_root}/scripts/audit/` | C (后台守护) |
| `self_evolution.py` | `{agentloop_root}/scripts/audit/` | D |
| `cross-agent-50r.py` | `{agentloop_root}/scripts/audit/` | Daemon |
| `check_core_tools.py` | `{agentloop_root}/scripts/audit/` | Daemon 启动 |

## 路径占位符约定

| 占位符 | 含义 | preset.json 键 |
|--------|------|----------------|
| `{agentloop_root}` | agentloop 工具本身所在目录 | *(运行时从 SKILL 路径推导: `SKILL.md` 上两级)* |
| `{group_id}` | Java 项目 Maven groupId | `groupId` |
| `{project_root}` | 被审计 Java 项目源码根目录 | `projectRoot` |
| `{codegraph_db}` | codegraph SQLite 路径 | `codegraphDb` |
| `{loop_audit_dir}` | 审计产物输出目录 | `loopDir` + `projectRoot` |

占位符由 `cross-agent-50r.py` 从 preset.json 解析填充。用户在 SKILL.md 中看到的始终是占位符。
