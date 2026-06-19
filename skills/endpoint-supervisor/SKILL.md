---
name: endpoint-supervisor
description: "每端点审计协调器。对 routes.json 中的每个 route 串行执行：chain_builder 构建调用链 → analyst 5 维分析 → 派发 expert 深挖 → 汇总 findings 到 Memurai。由 java-whitebox-loop 主流程内部调用，不直接人工触发。"
---

# Endpoint Supervisor — 每端点审计协调器

## 执行流（严格串行，chain 内禁止并行）

### 1. 调用链构建

```bash
python scripts/chain/chain_builder.py \
  --project-root "$project_root" \
  --codegraph-db "$codegraph_db" \
  --group-id "$group_id" \
  --entry "$route.fqn" \
  --output "$loop_audit_dir/chains/${route.sig_hash}.json"
```

`chain_builder.py` 内部：
- CTE RECURSIVE on `codegraph.db`（nodes + edges）
- `method_calls_extractor.py` 提取 third-party 调用
- 注入 `// sink: <FQN>` 注释到方法体（`non-groupId = sink`, L102）
- 写入 Memurai: `{groupId}:audit:chain:{sig_hash}`（TTL 24h）

产物：

```json
{
  "entry_fqn": "...",
  "sig_hash": "abc123...",
  "chain": [
    {
      "fqn": "...",
      "file": "src/main/java/...",
      "start_line": 20,
      "end_line": 45,
      "body": "public AttackResult completed(@RequestParam String userid) {\n    // sink: java.sql.Statement.executeQuery\n    ...",
      "depth": 0,
      "sinks": ["java.sql.Statement.executeQuery"]
    }
  ],
  "total_nodes": 5,
  "total_edges": 4,
  "total_sinks": 6,
  "cycle_detected": false
}
```

遇 `cycle_detected: true` 停止，记 finding `vuln_type: "cycle_in_call_graph"`。

### 2. 5 维分析

派发 analyst（由 `prompts/analyst.md` 引导思考），输入 Step 1 的 chain 数据。产出 5 个维度判定：

```json
{
  "chain_id": "abc123...",
  "dimensions": {
    "input_tracing": "user_controlled | derived_from_user | internal_only",
    "sanitization": "none | partial | complete",
    "authorization": "present_and_strict | present_but_weak | missing_or_bypassable",
    "branch_logic": "normal | race_condition | state_bypass | numeric_overflow",
    "info_leakage": "low_risk | medium_risk | high_risk"
  },
  "recommended_experts": ["injection-audit", "auth-chain-audit"]
}
```

### 3. Expert 派发规则

| 条件 | Expert |
|------|--------|
| `input_tracing ∈ {user_controlled, derived_from_user}` AND `sanitization ∈ {none, partial}` AND sinks 含 SQL/CMD/RCE 等 | `injection-audit` |
| `authorization == missing_or_bypassable` | `auth-chain-audit` |
| `branch_logic ∈ {race_condition, state_bypass, numeric_overflow}` | `business-logic-audit` |
| Route path 含 `/upload`/`/download`/`/file` AND `authorization != present_and_strict` | `file-audit` |
| Route path 含 `/login`/`/register`/`/session`/`/auth` | `login-audit` |

### 4. 汇总（超时 60s）

等所有 expert，收集 `{groupId}:audit:finding:{chainId}:draft:*`，promote 到 `final`：

```python
m.set(f"{group_id}:audit:finding:{chain_id}:final", json.dumps({
    "findings": all_findings,
    "expert_count": len(experts_dispatched),
    "timestamp": datetime.now().isoformat()
}))
```

severity ∈ {critical, high} 的 finding 设 `poc_status: "pending"`，`poc-monitor.py` 接管 PoC 验证。

### 5. 经验沉淀

```python
m.set(f"{group_id}:sup:exp:{chain_id}", json.dumps({
    "chain_id": chain_id,
    "route": route_fqn,
    "analyst_dims": analyst_result["dimensions"],
    "experts_dispatched": [...],
    "findings_count": len(all_findings),
    "chain_time_sec": end - start,
    "lessons": [...]
}))
```

## 输出契约（返回 Boss）

```json
{
  "chain_id": "abc123...",
  "findings_count": 3,
  "experts_dispatched": ["injection-audit", "auth-chain-audit"],
  "chain_time_sec": 18,
  "findings": [...]
}
```

Boss 聚合所有 supervisor 返回 → `reports/{finding}.md`。

## 约束

- 一个 supervisor = 一条 route = 多条 chain × 多个 expert
- chain 内严格串行
- 所有 expert findings 通过 Memurai，不直接写盘
- 不得修改 `chain_builder.py` 或 `method_calls_extractor.py`
- expert 超时（60s）→ 标记 `expert_timeout`，继续流程
- `chain_builder` 返回 `total_nodes == 0` → 标记跳过

## 集成

### poc-monitor 集成

Supervisor 不直接派发 PoC。Severity ∈ {critical, high} 的 finding 在 Memurai 中标 `poc_status: "pending"` → `poc-monitor.py` 守护进程轮询 `{groupId}:audit:finding:*:final` → 派发 `poc-verify` → 结果写入 `{groupId}:audit:finding:{chainId}:poc_result`。

### self_evolution 集成

Supervisor 不直接调 self_evolution。写经验到 `{groupId}:sup:exp:{chain_id}` → daemon 在 Phase D 调 `self_evolution.merge_knowledge_from_memurai` 合并到 `knowledge.json` → 下轮 supervisor/expert 启动时加载，避免重复已知坑。

## 依赖文件

- `projects/_template/06-通用安全知识.md` / `07-Sink表.json` / `08-Sanitizer表.json` — 安全基线 + sink/sanitizer 判定表
- `scripts/chain/chain_builder.py` + `method_calls_extractor.py` — CLI 调用
- `prompts/analyst.md` — analyst 思考引导
- `skills/{injection,auth-chain,file,login,business-logic}-audit/SKILL.md` — expert
- `skills/poc-verify/SKILL.md` — poc-monitor 调用
