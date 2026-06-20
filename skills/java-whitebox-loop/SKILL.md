---
name: java-whitebox-loop
description: "Java 白盒审计顶层入口。主 agent 作为调度中心，脚本产数据 + AI 直接消费。触发词:`白盒审计` / `启动审计` / `安全审计编排`。"
---

# Java 白盒审计 — 入口契约

> 两层架构：主 agent (Boss) → 专家 agent。不启动独立 opencode 子进程。

## 调用 & 触发

- **脚本执行 Phase 0-2**: `python {agentloop_root}/run_phase1_to_4.py --preset projects/{group_id}/preset.json --limit 100`
- **主 agent 执行 Phase 3-4**: 加载本 skill 后，从 chains.db 取 batch，分析+验证

## Phase 0 — Pre-flight: 清空上轮缓存 (MANDATORY)

```powershell
$keys = memurai-cli --raw KEYS "{group_id}:*" | Where-Object { $_ -notmatch "^{group_id}:knowledge:" }
if ($keys.Count -gt 0) {
    $keys | ForEach-Object { memurai-cli DEL $_ }
    Write-Host "[Phase 0] Cleared $($keys.Count) cache keys for {group_id}"
}
```

## 工作流

### Phase 0-2: 脚本执行（确定性工作）

主 agent 调用脚本，产 chains.db + Memurai 缓存：

```powershell
python {agentloop_root}/run_phase1_to_4.py --preset projects/{group_id}/preset.json --limit 100
```

产出：
- `exposure/*.json` — 9 类资产（route/config/filter/db_schema/waf/auth_code/sensitive_info/codegraph/env_filter）
- `chains.db` — 调用链 SQLite（chain_path + node_path + priority + status）
- Memurai: `{groupId}:method:{fqn}#{startline}` — 方法体缓存（含 `// #fqn` 注释）
- Memurai: `{groupId}:config:{file}` / `{groupId}:filter:{file}` / `{groupId}:waf:{file}` — 文件缓存

### Phase 3: AI 分析（主 agent 直接消费）

主 agent 从 chains.db 批量取链，按链特征分发给专家 agent：

```python
from chain_db import ChainDB
db = ChainDB("{loop_audit_dir}/chains.db")
batch = db.batch_by_priority(limit=100, status="pending")

for chain in batch:
    # 从 node_path 解析 node_id，从 Memurai 加载方法体
    node_ids = chain["node_path"].split(" -> ")
    # 每个 node_id 对应 Memurai key: {groupId}:method:{node_id}
    
    # 按链特征分发专家：
    # - sink_categories 含 injection.sql → injection-audit
    # - sink_categories 含 path_traversal → file-audit
    # - filter 链 → auth-chain-audit
    # - 无明显 sink → business-logic-audit
    
    # 专家返回结论后写回 chains.db
    db.update_status(chain["chain_id"], "analyzed")  # 或 "vuln" / "safe"
```

### Phase 4: PoC 验证（主 agent 直接调度）

主 agent 取已分析链，生成 PoC，验证：

```python
vuln_chains = db.batch_by_priority(limit=100, status="vuln")
for chain in vuln_chains:
    # 生成 PoC payload
    # 验证（curl / arthas / SSH）
    # 写回最终状态
    db.update_status(chain["chain_id"], "safe")  # 或保持 "vuln"
```

### 收敛判定

```python
stats = db.stats()
# 4-AND: avg_score≥85, stddev<3, reconcile≥10, coverage≥0.95
```

## 必读 Rules (≤5 个文件，启动时一次读完)

1. **`rules/entry-contract.md`** — 调用方式、preset 参数、产出清单
2. **`rules/phase-gates.md`** — 4阶段入口/出口/失败回退
3. **`rules/pruning-and-keys.md`** — L1/L2/L3 剪枝 + Memurai key schema
4. **`rules/self-evolution.md`** — scoring/convergence/FP sampling/knowledge merge
5. **`rules/goals.md`** — 5 个根本目标 + 硬约束
