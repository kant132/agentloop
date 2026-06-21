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

#### 分发规则（加速版）

| 专家 skill | 触发条件 | 审计范围 | 方法体加载策略 |
|-----------|---------|---------|-------------|
| `injection-audit` | chain 有 sink，sink 不涉及文件路径 | 每个 endpoint 只判断 1 条链 | 前4层+后2层（<6层全部） |
| `file-audit` | chain 的 sink 涉及文件路径 | 每个 endpoint 只判断 1 条链 | 前4层+后2层（<6层全部） |
| `auth-chain-audit` | 前 25% 端点 | 每 endpoint 1 条链，前 5 层 | 前 5 层 |
| `business-logic-audit` | 前 25% 端点 | 每 endpoint 1 条链，前 5 层 | 前 5 层 |

**加速策略**：
- 注入类/文件类：每个 endpoint 只判断 1 条链（按优先级最高），不再审计所有有 sink 的链
- 认证鉴权/业务逻辑：只校验前 25% 端点（按优先级排序后取前 1/4）
- 方法体加载：注入类/文件类用前4层+后2层（<6层全部），认证鉴权/业务逻辑用前5层
- 所有 agent 禁止直接读源文件，只通过 `load_method_body.py` 加载缓存

#### 判断逻辑

主 agent 判断每条链：
1. chain 的 `total_sinks > 0` → 有 sink
2. chain 的 sink 涉及文件路径 → subagent 加载 `file-audit` skill
3. chain 的 sink 不涉及文件路径 → subagent 加载 `injection-audit` skill
4. 注入类/文件类：每个 endpoint 只取优先级最高的 1 条链（不再所有链都审）
5. 认证鉴权/业务逻辑：按优先级排序端点，只取前 25% 端点，每 endpoint 1 条链
6. chain 无 sink → 不调注入类 agent，只走认证鉴权 + 业务逻辑（前25%端点）

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
# 注入类/文件类：默认前4层+后2层（链 <6 层时全部加载，无需指定参数）
python scripts/chain/load_method_body.py --group-id {groupId} --node-path "..."

# 认证鉴权/业务逻辑：前 5 层，不要尾部
python scripts/chain/load_method_body.py --group-id {groupId} --node-path "..." --max-depth 5 --tail-depth 0

# 加载全部（PoC agent 需要完整上下文时）
python scripts/chain/load_method_body.py --group-id {groupId} --node-path "..." --max-depth 0 --tail-depth 0

# 加载单个方法体（PoC agent 用）
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

# 1. 注入类/文件类：每个 endpoint 只取 1 条优先级最高的链
sink_endpoints = {}
for c in all_chains:
    if c["total_sinks"] <= 0:
        continue
    ep = c["endpoint_fqn"]
    if ep not in sink_endpoints:
        sink_endpoints[ep] = c  # 取优先级最高的（batch 已按 priority DESC 排序）
# 分发：涉及文件路径 → file-audit，其他 → injection-audit

# 2. 认证鉴权/业务逻辑：只校验前 25% 端点
all_endpoints = list(dict.fromkeys(c["endpoint_fqn"] for c in all_chains))  # 按优先级去重
top_25pct = all_endpoints[:max(1, len(all_endpoints) // 4)]
for ep in top_25pct:
    # 取该 endpoint 优先级最高的 1 条链
    # task(auth-chain-audit, chain=c, max_depth=5)
    # task(business-logic-audit, chain=c, max_depth=5)

# 3. 专家返回结论 → 写回 chains.db
# db.update_status(chain_id, "vuln")  # 或 "safe"

# 4. 主 agent 生成静态报告（汇总所有专家结论）
```

**上下文隔离**：
- 主 agent 上下文：只含链元数据（轻量，不污染）
- 子 agent 上下文：独立，含方法体（通过 load_method_body.py 加载）
- 子 agent 返回：结论文本（小，不污染主 agent）

### Phase 3.5: 主 agent 生成静态报告

主 agent 收集所有专家结论后，生成静态审计报告：

```python
# 汇总所有专家结论
analyzed = db.batch_by_priority(limit=100, status="analyzed")
vuln_chains = db.batch_by_priority(limit=100, status="vuln")

# 生成静态报告 JSON
report = {
    "total_chains": db.total_chains(),
    "analyzed": len(analyzed),
    "vuln": len(vuln_chains),
    "safe": len(db.batch_by_priority(limit=100, status="safe")),
    "vulnerabilities": [
        {
            "chain_id": c["chain_id"],
            "endpoint": c["endpoint_fqn"],
            "sinks": c["total_sinks"],
            "chain_path": c["chain_path"],
        }
        for c in vuln_chains
    ],
}
# 写入 loop_audit/diag/static_report.json
```

### Phase 4: PoC 动态验证

**静态报告生成后**，对 `status=vuln` 的链，使用 poc agent 进行动态漏洞验证。

PoC agent 通过以下方式获取代码信息（禁止直接读源文件）：
1. **Memurai 缓存**：`load_method_body.py` 加载方法体（含 `// #fqn` 注释）
2. **codegraph SQLite**：查询方法的调用关系、参数类型、类继承关系
3. **chains.db**：获取 chain_path 和 node_path

```python
vuln_chains = db.batch_by_priority(limit=100, status="vuln")
for chain in vuln_chains:
    # task(poc-verify, chain=chain)
    # PoC agent:
    #   1. load_method_body.py 加载方法体 → 理解漏洞上下文
    #   2. codegraph 查询 → 获取调用参数类型、类关系
    #   3. 生成 PoC payload
    #   4. 验证（curl / arthas / SSH）
    #   5. 写回最终状态: confirmed / denied / inconclusive
```

**PoC agent 代码信息获取方式**：
- 方法体：`python scripts/chain/load_method_body.py --group-id {gid} --node-id "method:xxx"`
- 调用关系：`codegraph SQLite SELECT ... FROM edges WHERE source=?`
- 参数类型：`codegraph SQLite SELECT ... FROM nodes WHERE id=?`
- 文件缓存：`memurai GET {groupId}:config:{file}` / `{groupId}:filter:{file}`

## 必读 Rules

1. **`rules/entry-contract.md`** — 调用方式、preset 参数、产出清单
2. **`rules/phase-gates.md`** — 4阶段入口/出口/失败回退
3. **`rules/pruning-and-keys.md`** — L1/L2/L3 剪枝 + Memurai key schema
4. **`rules/self-evolution.md`** — scoring/convergence/FP sampling/knowledge merge
5. **`rules/goals.md`** — 5 个根本目标 + 硬约束
