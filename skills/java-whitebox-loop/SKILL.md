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

**主 agent 只读链元数据**（chain_path, node_path, priority, total_sinks, status），不加载方法体。
**子 agent 自己加载方法体**，通过 task() 独立上下文，不与主 agent 共享。

**子 agent 只分析方法体，不额外读源文件。** 方法体已含 `// #fqn` 注释标注所有非 groupId 调用，足够判断漏洞。禁止子 agent 用 Read/grep 探索项目源码目录。

主 agent 提供给子 agent的数据：
- `chain_id` — 链 ID
- `endpoint_fqn` — 入口方法
- `chain_path` — 人可读链路径（含 sink num）
- `node_path` — node_id 序列（`method:id1 -> method:id2 -> ...`）
- `total_sinks` — sink 总数
- `group_id` — 项目 groupId（用于 Memurai key）

子 agent 加载方法体工具：

```powershell
# 加载一条链的前 5 层方法体（认证鉴权 / 业务逻辑用）
python scripts/chain/load_method_body.py --group-id {groupId} --node-path "method:id1 -> method:id2 -> ..." --max-depth 5

# 加载一条链的所有方法体（注入类用）
python scripts/chain/load_method_body.py --group-id {groupId} --node-path "method:id1 -> method:id2 -> ..."

# 加载单个方法体
python scripts/chain/load_method_body.py --group-id {groupId} --node-id "method:abc123"
```

输出 JSON 数组，每个元素含 `fqn`, `node_id`, `body`（含 `// #fqn` 注释）, `depth`。

**子 agent 约束**：
1. 只用 `load_method_body.py` 加载方法体，不读源文件
2. 只分析方法体内的 `// #fqn` 注释和数据流
3. 基于方法体内容判断漏洞，不探索项目目录
4. 返回结论文本（小，不传方法体回主 agent）

#### 主 agent 调度逻辑

```python
from chain_db import ChainDB
db = ChainDB("{loop_audit_dir}/chains.db")

all_chains = db.batch_by_priority(limit=100, status="pending")

# 1. 注入类 + 文件类：所有有 sink 的链
sink_chains = [c for c in all_chains if c["total_sinks"] > 0]
# 主 agent 判断 sink 是否涉及文件路径 → 决定加载 file-audit 还是 injection-audit skill

# 2. 认证鉴权 + 业务逻辑：每个 endpoint 只取 1 条链，前 5 层
endpoints_seen = set()
for c in all_chains:
    ep = c["endpoint_fqn"]
    if ep not in endpoints_seen:
        endpoints_seen.add(ep)
        # task(auth-chain-audit, chain=c, max_depth=5)
        # task(business-logic-audit, chain=c, max_depth=5)

# 3. 无 sink 的链：跳过注入/文件类，只走认证鉴权 + 业务逻辑

# 4. 专家返回结论 → 写回 chains.db
# db.update_status(chain_id, "vuln")  # 或 "safe"
```

**上下文隔离**：
- 主 agent 上下文：只含链元数据（轻量，不污染）
- 子 agent 上下文：独立，含方法体（通过 load_method_body.py 加载）
- 子 agent 返回：结论文本（小，不污染主 agent）

### Phase 4: PoC 验证

取 `status=vuln` 的链 → 生成 PoC → 验证 → 写回 chains.db

## 必读 Rules

1. **`rules/entry-contract.md`** — 调用方式、preset 参数、产出清单
2. **`rules/phase-gates.md`** — 4阶段入口/出口/失败回退
3. **`rules/pruning-and-keys.md`** — L1/L2/L3 剪枝 + Memurai key schema
4. **`rules/self-evolution.md`** — scoring/convergence/FP sampling/knowledge merge
5. **`rules/goals.md`** — 5 个根本目标 + 硬约束
