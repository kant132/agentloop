---
name: java-whitebox-loop
description: "Java 白盒审计顶层入口。主 agent 作为调度中心，按链特征分发专家。触发词:`白盒审计` / `启动审计` / `安全审计编排`。"
---

# Java 白盒审计 — 入口契约

> 两层架构：主 agent (Boss) → 专家 agent (通过 task() 委派，subagent 加载对应 skill)

## 工作流

### Phase 0-2: 脚本执行（确定性工作）

```powershell
python {agentloop_root}/run_phase1_to_4.py --preset projects/{group_id}/preset.json --limit 100
```

产出：
- `exposure/*.json` — 9 类资产
- `chains.db` — 调用链 SQLite（chain_path + node_path + priority + status + total_sinks）
- Memurai: `{groupId}:method:{fqn}#{startline}` — 方法体缓存（含 `// #fqn` 注释）
- Memurai: `{groupId}:filter:{file}` / `{groupId}:config:{file}` / `{groupId}:waf:{file}` — 文件缓存

### Phase 3: 主 agent 分发审计

主 agent 从 chains.db 批量取链，按以下规则分发：

#### 分发规则

| 专家 skill | 触发条件 | 审计范围 |
|-----------|---------|---------|
| `injection-audit` | chain 有 sink（total_sinks > 0），sink 不涉及文件路径 | **所有**有 sink 的链 |
| `file-audit` | chain 的 sink 涉及文件路径（path_traversal/file_upload） | 所有涉及文件路径的链 |
| `auth-chain-audit` | 每个 endpoint 调 1 次 | **前 5 层**（depth < 5），**1 条链** |
| `business-logic-audit` | 每个 endpoint 调 1 次 | **前 5 层**（depth < 5），**1 条链** |

#### 判断逻辑

主 agent 判断每条链：
1. chain 的 `total_sinks > 0` → 有 sink
2. chain 的 sink 涉及文件路径 → subagent 加载 `file-audit` skill
3. chain 的 sink 不涉及文件路径 → subagent 加载 `injection-audit` skill
4. 每个 endpoint 只调 1 次 `auth-chain-audit`（取优先级最高的链，前 5 层）
5. 每个 endpoint 只调 1 次 `business-logic-audit`（取优先级最高的链，前 5 层）
6. chain 无 sink → 不调注入类 agent，只走认证鉴权 + 业务逻辑

#### 方法体加载

主 agent 提供工具给 subagent 按需加载方法体：

```python
# 主 agent 从 chains.db 取链
from chain_db import ChainDB
db = ChainDB("{loop_audit_dir}/chains.db")

# 取所有有 sink 的链（注入类 + 文件类）
all_chains = db.batch_by_priority(limit=100, status="pending")
sink_chains = [c for c in all_chains if c["total_sinks"] > 0]

# 按 endpoint 分组，每个 endpoint 取 1 条链（认证鉴权 + 业务逻辑）
endpoints_seen = set()
auth_chains = []
biz_chains = []
for c in all_chains:
    ep = c["endpoint_fqn"]
    if ep not in endpoints_seen:
        endpoints_seen.add(ep)
        auth_chains.append(c)  # 取前 5 层
        biz_chains.append(c)   # 取前 5 层
```

Subagent 加载方法体：
```python
# 从 node_path 解析 node_id
node_ids = chain["node_path"].split(" -> ")
# 从 Memurai 加载方法体
for nid in node_ids[:5]:  # 前 5 层
    body = memurai.get(f"{groupId}:method:{nid}")
```

### Phase 4: PoC 验证

取 `status=vuln` 的链 → 生成 PoC → 验证 → 写回 chains.db

## 必读 Rules

1. **`rules/entry-contract.md`** — 调用方式、preset 参数、产出清单
2. **`rules/phase-gates.md`** — 4阶段入口/出口/失败回退
3. **`rules/pruning-and-keys.md`** — L1/L2/L3 剪枝 + Memurai key schema
4. **`rules/self-evolution.md`** — scoring/convergence/FP sampling/knowledge merge
5. **`rules/goals.md`** — 5 个根本目标 + 硬约束
