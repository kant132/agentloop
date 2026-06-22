---
name: java-whitebox-loop
description: "Java 白盒审计顶层入口。主 agent 作为调度中心，按链特征分发专家。触发词:`白盒审计` / `启动审计` / `安全审计编排`。"
---

# Java 白盒审计 — 入口契约

> 两层架构：主 agent (Boss) → 专家 agent (通过 task() 委派，subagent 加载对应 skill)

## 工作流

### Phase 0-2: 脚本执行（确定性工作）

```powershell
python {agentloop_root}/run_phase1_to_4.py --preset projects/{group_id}/preset.json --limit 100 --phase 2
```

**注意：`--phase 2` 只执行 Phase 1+2，不执行 Phase 3/4**（Phase 3/4 需要 `task()` 全局函数，只能在 agent 会话里执行，不能在 subprocess 里调用）。

产出：
- `exposure/*.json` — 9 类资产
- `chains.db` — 调用链 SQLite（chain_path + node_path + priority + status + total_sinks + node_count）
- Memurai: `{groupId}:method:{node_id}` — 方法体缓存（含 `//fqn:` 注释）

### Phase 2.5: 链边验证（jar-analyzer 批量验证）

```powershell
python {agentloop_root}/scripts/chain/verify_edges.py --jar-analyzer-db {jar_analyzer_db} --chains-db {loop_audit_dir}/chains.db
```

jar-analyzer 模式下，链的 node_id 是 jar-analyzer 的 method_id，边信息来自 `method_call_table`（已在链构建时使用）。此步骤：
1. 对每条 chain 的每对连续节点 `(node[i], node[i+1])`，检查 `method_call_table` 或 `method_impl_table` 中是否存在对应边
2. 边缺失则标记链为 `status='broken'`，并丢弃所有相同前缀的后续链
3. 批量验证，耗时 <1s（2422 条链）

**Phase 3 选链时自动排除 broken 链**（`status='pending'` 过滤）。

### Phase 3: 主 agent 分发审计

**禁止自己写临时脚本。** 用以下固定步骤执行，代码已写好，直接调用。

#### 步骤 1: 选链（从 chains.db 读取，按分发规则筛选）

```python
import sys, json
sys.path.insert(0, r"{agentloop_root}/scripts/chain")
sys.path.insert(0, r"{agentloop_root}/scripts/redis")
from chain_db import ChainDB
from memurai_client import Memurai
from load_method_body import load_chain

db = ChainDB(r"{loop_audit_dir}/chains.db")
m = Memurai()
GID = "{group_id}"

all_chains = db.batch_by_priority(limit=999, status="pending")

# 注入类/文件类：is_sink=1, priority>0, 按优先级排序
sink_chains = db.top_sink_chains()

# 认证鉴权/业务逻辑：前 25% 端点
all_endpoints = list(dict.fromkeys(c["endpoint_fqn"] for c in all_chains))
top_25 = all_endpoints[:max(1, len(all_endpoints) // 4)]
```

每条链单独审计，**不合并多条链**。每批 4 个并行分发。

#### 步骤 2: 加载方法体（从 Memurai，禁止读源文件）

```python
bodies = load_chain(m, GID, chain["node_path"], 0, 0)  # 全部加载
# 格式化为纯文本
lines = []
for b in bodies:
    is_last = b["depth"] == len(bodies) - 1
    tag = "  # last method" if is_last else ""
    lines.append(f"=== depth={b['depth']}: {b['fqn']} ==={tag}")
    lines.append(b["body"])
    lines.append("")
method_bodies = "\n".join(lines)
```

#### 步骤 3: 从固定模板读取 prompt，只填充方法体

```python
template = open(r"{agentloop_root}/prompts/expert-injection.md", encoding="utf-8").read()
# 找到模板正文（--- 之后的 内容）
template_body = template.split("---", 1)[1] if "---" in template else template
prompt = template_body
prompt = prompt.replace("{endpoint_method}", chain["endpoint_fqn"].split("#")[-1])
prompt = prompt.replace("{class_fqn}", chain["endpoint_fqn"])
prompt = prompt.replace("{method_bodies}", method_bodies)
```

#### 步骤 4: task() 派发（每条链单独一个 task）

```python
result = task(category="deep", description=f"Phase3 {chain['chain_id']}", prompt=prompt)
```

#### 步骤 5: 写回 chains.db

```python
import re, json
match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', result, re.DOTALL)
data = json.loads(match.group()) if match else {"verdict":"inconclusive","analysis":result[:200],"vulnerabilities":[]}
db.update_agent_result(chain["chain_id"], "injection", data)
db.update_status(chain["chain_id"], "safe" if data["verdict"]=="safe" else "vuln")
```

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

- **审计阶段**：同时启动 4 个审计 agent（task() 并行，category="quick"）
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

**主 agent 预加载方法体，直接传给子 agent，不让子 agent 自己调脚本。**

```python
from chain_db import ChainDB
from memurai_client import Memurai

db = ChainDB("{loop_audit_dir}/chains.db")
m = Memurai()
GID = "{group_id}"

# 取 is_sink=1 的链
chains = db.batch_by_priority(limit=100, is_sink=1)

def load_front4_tail2(node_path: str) -> tuple[int, list[dict]]:
    """加载前4层+后2层的方法体，返回(total_nodes, bodies)。"""
    nodes = [x.strip() for x in (node_path or "").split("->") if x.strip()]
    total = len(nodes)
    if total <= 6:
        selected = nodes
        indices = list(range(total))  # 全部加载
    else:
        selected = nodes[:4] + nodes[-2:]
        indices = [0, 1, 2, 3, total - 2, total - 1]  # 真实链位置
    bodies = []
    for nid in selected:
        key = f"{GID}:method:{nid}"
        raw = m.get(key)
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            bodies.append(data)
    return total, bodies, indices

def bodies_to_prompt(total: int, bodies: list[dict], indices: list[int]) -> str:
    """格式化方法体为纯文本，depth 从 indices 取，最后标记 # last method。"""
    lines = []
    for i, b in enumerate(bodies):
        fqn = b.get("fqn", "")
        body = b.get("body", "")
        depth = indices[i] if i < len(indices) else i
        marker = "  # last method" if depth == total - 1 else ""
        lines.append(f"=== depth={depth}: {fqn} ==={marker}")
        lines.append(body)
    return "\n".join(lines)

for c in chains:
    total, bodies, indices = load_front4_tail2(c["node_path"])
    prompt_body = bodies_to_prompt(total, bodies, indices)
    # task(..., prompt=f"方法体数据:\n{prompt_body}")
```

**子 agent 逆向分析方法**：
1. **先看最后一层** — 分析最后一个方法体的 `// #fqn` 注释，判断存在什么类型的漏洞（SQL注入/RCE/SSRF/路径遍历等）
2. **再看上层** — 从最后一个节点向上层回溯，检查污点是否可达（用户输入是否能传递到这个 sink）
3. **组合判定** — sink 存在 + 污点可达 = vuln

**主 agent 提供给子 agent 的数据**：
- `chain_id` — 链 ID
- `endpoint_fqn` — 入口方法  
- `entry_has_params` — 入口方法是否接收用户参数
- `chain_path` — 人可读链路径（每层含 sink num）
- `method_bodies` — 预加载的前4+后2层方法体（JSON 字符串）
- `group_id` — 项目 groupId

**子 agent 约束**：
1. 子 agent 不调任何脚本（方法体已预加载）
2. 不读源文件，不探索项目目录
3. 逆向分析：先看最后一层漏洞类型，再回溯污点
4. 返回结论文本（小，不传方法体回主 agent）

### Phase 3.5: 主 agent 生成静态报告（Markdown）

汇总 agent_results，生成 Markdown 报告文档。

#### 报告内容

**对每条链，需要包含**：

1. **调用链污点传播分析**（注入类/文件类）：
   - 从入口到 sink 的每层数据流
   - 标注每一层的污点状态：**中间层默认未做消毒处理**
   - 如果有消毒处理，明确指出消毒函数和位置
   - 格式：`入口 method1(param) → method2(t) [未消毒] → ... → methodN(sink) [触发漏洞]`

2. **漏洞根因**（所有 agent）：
   - 不是简单指出来个地方有问题
   - 必须说明**污点在这 6 层中的传播路径**
   - 必须说明**为什么中间层没有做消毒处理**
   - 认证鉴权/业务逻辑类，需要给出**导致漏洞的完整漏洞链说明**

3. **不确定项标注**（inconclusive）：
   - 明确标注哪些环节无法确定
   - 需要什么额外信息才能确认
   - **inconclusive 也需要 PoC 验证**（尽量通过动态验证消除不确定）

#### 输出格式

Markdown 文件，写入 `loop_audit/routes/` 目录，按端点分文件：

```markdown
# 端点: GET /api/users/{id}
## 调用链路径
org.owasp.webgoat.lesson.UserController#getUser(sink num: 0)
  → org.owasp.webgoat.lesson.UserService#findById(sink num: 1) [未消毒]
  → org.owasp.webgoat.lesson.UserDao#query(sink num: 2) [未消毒]
  → java.sql.Statement.executeQuery [SQL注入 sink]

## 污点传播分析
1. entry: getUser(String id) — id 参数来自 HTTP 请求
2. depth=1: findById(id) — 参数直接透传，未做校验 [未消毒]
3. depth=2: query(sql) — 参数拼接到 SQL 字符串，未做参数化 [触发漏洞]

## 漏洞根因
用户输入的 id 参数经过 2 层调用，全程未做任何消毒处理（白名单校验/参数化查询），
最终在 UserDao.query() 中直接拼接到 SQL 语句，导致 SQL 注入。

## 注入类审计
- verdict: vuln
- 漏洞类型: SQL注入
- root_cause: 污点从 HTTP 参数 → UserController.getUser() → UserService.findById() → UserDao.query() 全程未消毒，最终拼入 SQL
- poc_status: pending

## 认证鉴权类审计
- verdict: safe
- 说明: 该端点有 @PreAuthorize 注解，Spring Security 在 dispatcher 层已做鉴权

## 业务逻辑类审计
- verdict: inconclusive
- 不确定: 无法确定 findById 是否对 id 做了权限校验（是否校验了当前用户有权查看该 id）
- 需要: 运行时确认 UserService.findById 的鉴权逻辑
```

#### 不确定项验证

inconclusive 的结论也需要 PoC 验证：
- 通过运行时动态分析（arthas/curl）尝试确认不确定项
- PoC agent 从 `agent_results` 中取 `verdict=vuln OR verdict=inconclusive` 的链
- 验证后写回：confirmed（确认漏洞）/ denied（证伪）/ inconclusive（受环境限制仍无法确认）

### Phase 4: PoC 动态验证

PoC agent 行为：
- **不重新分析** — 从 `agent_results` JSON 取审计结果
- **vuln + inconclusive 都验证** — 尽量通过动态验证消除不确定
- **逐一验证** — 从 `get_pending_vulns()` 取待验证列表，逐一验证
- **按优先级** — `batch_for_poc()` 按 priority DESC 排序
- **并发** — 同时启动 4 个 PoC agent

```python
# 取待 PoC 的链（vuln + inconclusive 都验证）
vuln_chains = db.batch_for_poc(limit=4)
for chain in vuln_chains:
    pending_vulns = db.get_pending_vulns(chain["agent_results"])
    for vuln in pending_vulns:
        # task(poc-verify, chain_id=vuln["agent_key"], vuln=vuln)
        # PoC agent:
        #   1. 读 vuln.root_cause → 理解污点传播路径
        #   2. load_method_body.py 加载方法体
        #   3. codegraph SQLite 查调用关系
        #   4. 根据 root_cause 生成针对性 PoC
        #   5. 验证 → db.update_vuln_poc_status(chain_id, agent_key, vuln_index, "confirmed")
```

**PoC 三态结论**：
- `confirmed`: 验证确认漏洞存在
- `denied`: 验证证明不是漏洞
- `inconclusive`: 受环境限制无法验证（非代码问题）

**PoC agent 代码信息来源**（禁止直接读源文件）：
- 方法体：`load_method_body.py --node-id "method:xxx"`
- 调用关系：`codegraph SQLite SELECT FROM edges`（**Phase 4 PoC 专用**，Phase 0-3 用 jar-analyzer）
- 参数类型：`codegraph SQLite SELECT FROM nodes`
- 配置文件：`Memurai GET {groupId}:config:{file}`

## 必读 Rules

1. **`rules/entry-contract.md`** — 调用方式、preset 参数、产出清单
2. **`rules/phase-gates.md`** — 4阶段入口/出口/失败回退
3. **`rules/pruning-and-keys.md`** — L1/L2/L3 剪枝 + Memurai key schema
4. **`rules/self-evolution.md`** — scoring/convergence/FP sampling/knowledge merge
5. **`rules/goals.md`** — 5 个根本目标 + 硬约束
