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

主 agent 从 chains.db 批量取链，按以下规则分发。

#### 分发规则（加速版）

| 专家 skill | 触发条件 | 审计范围 | 方法体加载 |
|-----------|---------|---------|-----------|
| `injection-audit` | chain 有 sink，sink 不涉及文件路径 | 每 endpoint 1 条链（最高优先级） | 前4+后2（默认） |
| `file-audit` | chain 的 sink 涉及文件路径 | 每 endpoint 1 条链（最高优先级） | 前4+后2（默认） |
| `auth-chain-audit` | 前 25% 端点 | 每 endpoint 1 条链 | 前5层（--max-depth 5 --tail-depth 0） |
| `business-logic-audit` | 前 25% 端点 | 每 endpoint 1 条链 | 前5层（--max-depth 5 --tail-depth 0） |

#### agent_results JSON 格式

每条链的 `agent_results` 列存储所有 audit agent 的结论：

```json
{
  "injection": {
    "verdict": "vuln" | "safe" | "inconclusive",
    "vulnerabilities": [
      {
        "type": "SQL注入",
        "root_cause": "用户输入的id参数直接拼接到SQL语句，未做参数化",
        "poc_status": "pending"
      }
    ],
    "if_inconclusive": "无法确定参数来源，需要查看调用方代码"
  },
  "file": {"verdict": "safe"},
  "auth": {"verdict": "vuln", "vulnerabilities": [
    {"type": "JWT alg:none绕过", "root_cause": "setSkipAllValidators()", "poc_status": "pending"}
  ]},
  "biz": {
    "verdict": "inconclusive",
    "reason": "无法判断业务流程是否完整",
    "extra_info_needed": "需要确认订单状态机"
  }
}
```

**审计 agent 结论要求**：
- `verdict: vuln` → 必须给 root_cause，每个 vuln 的 poc_status 初始为 "pending"
- `verdict: safe` → 说明为什么安全
- `verdict: inconclusive` → 必须标注哪里无法判断 + 需要什么额外信息
- 审计 agent 写回：`db.update_agent_result(chain_id, "injection", result_json)`

#### 并发策略

- **审计阶段**：同时启动 4 个审计 agent（task() 并行）
- **PoC 阶段**：同时启动 4 个 PoC agent（task() 并行）

#### 判断逻辑

```python
from chain_db import ChainDB
db = ChainDB("{loop_audit_dir}/chains.db")
all_chains = db.batch_by_priority(limit=100, status="pending")

# 1. 注入类/文件类：每 endpoint 1 条链，优先级最高
sink_endpoints = {}
for c in all_chains:
    if c["total_sinks"] <= 0: continue
    ep = c["endpoint_fqn"]
    if ep not in sink_endpoints:
        sink_endpoints[ep] = c

# 每批 4 个并行分发
# for batch in chunks(list(sink_endpoints.values()), 4):
#     tasks = [task(load_skills=[...], prompt="审计 chain_id=xxx") for c in batch]
#     wait for all results
#     for c, result in zip(batch, results):
#         db.update_agent_result(c["chain_id"], "injection", result)

# 2. 认证鉴权/业务逻辑：只校验前 25% 端点
all_endpoints = list(dict.fromkeys(c["endpoint_fqn"] for c in all_chains))
top_25 = all_endpoints[:max(1, len(all_endpoints) // 4)]
# 每 endpoint 1 条链，分发给 auth-chain-audit + business-logic-audit
```

#### 方法体加载

子 agent 加载方法体工具：load_method_body.py（默认前4+后2，链<6层全部）

### Phase 3.5: 主 agent 生成静态报告

汇总 agent_results → diag/static_report.json

### Phase 4: PoC 动态验证

PoC agent 行为：
- **不重新分析** — 从 `agent_results` JSON 取审计结果
- **逐一验证** — 从 `get_pending_vulns()` 取待验证漏洞列表，逐一验证
- **按优先级** — `batch_for_poc()` 按 priority DESC 排序
- **并发** — 同时启动 4 个 PoC agent

```python
# 取待 PoC 的链
vuln_chains = db.batch_for_poc(limit=4)
for chain in vuln_chains:
    pending_vulns = db.get_pending_vulns(chain["agent_results"])
    for vuln in pending_vulns:
        # task(poc-verify, chain_id=chain["chain_id"], vuln=vuln)
        # PoC agent:
        #   1. load_method_body.py 加载方法体
        #   2. codegraph SQLite 查调用关系
        #   3. 根据 root_cause 生成针对性 PoC
        #   4. 验证 → db.update_vuln_poc_status(chain_id, agent_key, vuln_index, poc_status)
```

**PoC agent 代码信息来源**（禁止直接读源文件）：
- 方法体：`load_method_body.py --node-id "method:xxx"`
- 调用关系：`codegraph SQLite SELECT FROM edges`
- 参数类型：`codegraph SQLite SELECT FROM nodes`
- 配置文件：`Memurai GET {groupId}:config:{file}`

## 必读 Rules

1. **`rules/entry-contract.md`** — 调用方式、preset 参数、产出清单
2. **`rules/phase-gates.md`** — 4阶段入口/出口/失败回退
3. **`rules/pruning-and-keys.md`** — L1/L2/L3 剪枝 + Memurai key schema
4. **`rules/self-evolution.md`** — scoring/convergence/FP sampling/knowledge merge
5. **`rules/goals.md`** — 5 个根本目标 + 硬约束
