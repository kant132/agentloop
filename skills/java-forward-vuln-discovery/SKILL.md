> ⚠️ **DEPRECATED** (2026-06-15, refactored into 4-phase + 3-layer architecture)
>
> This skill is NO LONGER called by the new `java-whitebox-loop/SKILL.md`.
>
> **Replacement mappings:**
> - **For Phase 5 chain analysis** (old FWD 5 modes A/B/C/D/INFO) → now done by:
>   - `endpoint-supervisor/SKILL.md` (per-endpoint dispatcher)
>   - `call-chain-audit-thinking/SKILL.md` (analyst 5-dim analysis)
>   - `injection-audit/SKILL.md` (replaces FWD-A data flow)
>   - `auth-chain-audit/SKILL.md` (replaces FWD-B auth)
>   - `business-logic-audit/SKILL.md` (replaces FWD-C business)
>   - `login-audit/SKILL.md` (replaces FWD-D state)
>   - `poc-verify/SKILL.md` (replaces FWD PoC verification)
> - **For Phase 2 threat modeling** (was mandatory) → now **optional / manual-only**.
>   - Can still be invoked by human operators: `python ... threat-model-analyst ...`
>   - No longer part of automated Boss dispatch
>
> **Kept for historical reference only.** New work should go through the new architecture.
>
> Reference doc: `D:\agentloop\design-docs\unified-implementation-plan.md`
>

---
name: java-forward-vuln-discovery
description: Java 项目前向漏洞发现子 skill。从外部端点出发，沿调用链前向追踪，发现所有漏洞类型（注入/业务逻辑/鉴权/信息泄露）。本轮焦点：覆盖四类漏洞（注入、业务、鉴权、信息泄露）。触发：Phase 5 调用链分析、FWD-A/B/C/D 调度。
---

# Java 前向漏洞发现（forward vuln discovery）

> 本 skill 是 `java-whitebox-loop` 的 Phase 5 子任务。**本轮焦点**：从前向追踪入手，发现所有漏洞（含无明确 sink 的业务逻辑、鉴权、信息泄露）。

## 一、核心边界

| 项 | 本 skill 范围 |
|---|--------------|
| **起点** | 外部调用点（端点） |
| **方向** | 顺调用链向下游扩展 |
| **目标** | 发现所有漏洞 |
| **排除** | 逆向追踪（从 sink 倒推） |
| **不做** | 修复建议、修复代码、加固建议 |

## 二、必读（启动前）

- `conduct/必读/01-避免重复劳动.md` — Memurai 批预取（走 memurai-cli.exe）
- `conduct/必读/03-工具选择边界.md` — grep / codegraph SQL / ast-grep / Memurai
- `conduct/必读/04-中文输出硬约束.md`
- `conduct/必读/05-不写修复硬约束.md`
- `conduct/必读/07-剪枝逻辑硬约束.md` — **L1 端点级 + L2 链级每层消毒 + L3 跨轮次**
- `types/攻击模式模板.json` — 业务×攻击模式映射
- `sql-cheatsheet.md` — codegraph SQLite 语法
- `finding-schema.json` — 输出 JSON schema
- `redis-key-schema.json` — Memurai key 设计
- `projects/{groupId}/preset.json` — 预置规则（groupId / 框架 / 注解）

## 三、4 种 FWD 模式（每端点并行启动 4 个 subagent）

| 模式 | 焦点 | 加载类型 |
|------|------|----------|
| **FWD-A** | 数据流顺推（找危险 sink） | 注入类、反序列化、文件操作、SSRF |
| **FWD-B** | 鉴权顺推（找鉴权缺失/绕过） | 鉴权类 |
| **FWD-C** | 业务顺推（找业务规则缺失） | 业务逻辑 |
| **FWD-D** | 状态机顺推（找状态绕过） | 状态机、支付 |
| **FWD-INFO** | 信息泄露（异常/日志/响应） | 信息泄露类 |

P0 端点 = 4 + 1 = 5 subagent 并行
P1 端点 = 必跑 A + B，C/D/INFO 抽样
P2 端点 = 必跑 A

## 四、Orchestrator 工作流

### 4.1 启动每个端点分析

```python
def analyze_endpoint(endpoint_fqn, group_id, commit, priority):
    # 0. L1 端点级剪枝（来自 conduct/必读/07）
    prune = l1_prune(endpoint_fqn)
    if prune and not human_override:
        # 记录剪枝日志，跳过整个端点
        write_pruning_log(level="L1", reason=prune.reason, ...)
        return None

    # 1. SQLite 一次查整条调用链（CTE RECURSIVE + 环检测）
    chain = run_script("scripts/chain/sqlite-extract-chain.py",
                       db="codegraph.db", entry=endpoint_fqn, depth=20)

    # 2. 启动级 Memurai 自检（一次性）
    if not already_checked(group_id, commit):
        run_script("scripts/redis/redis-self-check.py", ...)

    # 3. 批量预取 chain 方法到 Memurai
    chain_id = sha256(endpoint_fqn)[:16]
    run_script("scripts/redis/redis-batch-prefetch.py",
               chain=chain, group_id=group_id, commit=commit, chain_id=chain_id)

    # 4. 启动 4 个 FWD subagent（A+B+C+D）+ FWD-INFO（5 个）
    agents = []
    for mode in ["A", "B", "C", "D", "INFO"]:
        if priority == "P0" or (priority == "P1" and mode in ["A", "B"]) or (priority == "P2" and mode == "A"):
            agent = start_subagent(
                role=f"FWD-{mode}",
                prompt=load_template(f"templates/subagent-FWD-{mode}.md"),
                input={
                    "endpoint_fqn": endpoint_fqn,
                    "chain": chain,
                    "chain_id": chain_id,
                    "prefetch_key": f"audit:{group_id}:commit:{commit}:prefetch:{chain_id}",
                    "rule_files": load_relevant_types(mode),  # 从 types/ 加载
                    "pruning": prune,  # 告知 subagent 已做 L1 剪枝
                }
            )
            agents.append(agent)

    # 5. 等所有 subagent 完成
    findings = await_gather(agents, timeout=3600)

    # 6. 评分（REFLECT subagent 独立评分）
    for finding in findings:
        score = start_subagent(role="SCORER", input=finding)
        record_score(chain_id, round_n, score)

    # 7. 3x85 检查 + 落盘
    run_script("scripts/audit/finding-promoter.py", chain_id, check=True)
```

### 4.2 L1 端点级剪枝（前置）

```python
def l1_prune(endpoint_fqn):
    """应用 7 条 L1 剪枝规则"""
    meta = get_endpoint_meta(endpoint_fqn)
    preset = load_preset(meta.group_id)  # 从 preset.json 加载

    # P-L1-001 无参数
    if has_no_params(meta):
        return PruneDecision(reason="no_params", fwd_modes_skipped=["A","B","C","D","INFO"], reversible=True)

    # P-L1-002 仅数字参数
    if all_numeric_params(meta):
        return PruneDecision(reason="numeric_only", fwd_modes_skipped=["A","B","INFO"],
                             reversible_if_override=["C","D"])  # 复审仅跑 C+D

    # P-L1-003 Filter 全覆盖
    if filter_chain_full_coverage(meta):
        return PruneDecision(reason="filter_coverage", fwd_modes_skipped=["B"], reversible=True)

    # P-L1-004 内部接口 + 内网
    if is_internal_endpoint(meta) and env.internal_network:
        return PruneDecision(reason="internal_network", fwd_modes_skipped=["A"], reversible=True)

    # P-L1-005 健康检查
    if is_health_check(meta):
        return PruneDecision(reason="health_check", fwd_modes_skipped=["A","B","C","D","INFO"], reversible=False)

    # P-L1-006 API 文档
    if is_api_doc(meta):
        return PruneDecision(reason="api_doc", fwd_modes_skipped=["A","B","C","D","INFO"], reversible=False)

    return None  # 不剪
```

### 4.3 强制重扫（人工复审入口）

当人工标注"该端点需要复审" → 调 `force-rescan.py`：
- **必跑** FWD-C（业务）+ FWD-D（状态）
- **不**跑 FWD-A（注入）/ FWD-B（鉴权）/ FWD-INFO
- 即便该端点被 P-L1-001 / P-L1-002 剪过

```bash
python scripts/audit/force-rescan.py \
  --endpoint "GET /api/foo" \
  --modes "C,D" \
  --group-id com.example.x
```

### 4.2 关键决策

| 决策 | 判断依据 |
|------|---------|
| 启动 FWD-X 数量 | 端点优先级（P0/P1/P2） |
| 深度限制 | 默认 20，深度 > 15 警告 |
| 链是否剪枝 | 命中 `剪枝规则.md` 立即停 |
| finding 落盘 | 连续 3 轮评分 > 85 |
| PoC 触发 | 评级 ≥ 严重 + 环境可达 |

## 五、4 模式详细说明

### 5.1 FWD-A（数据流）
- 加载：`types/注入类/` + `types/反序列化/` + `types/文件操作/` 部分
- 判定：每节点过 D1（危险 sink）+ sanitizer 检测
- 终止：命中 sink / 剪枝 / 深度 20

### 5.2 FWD-B（鉴权）
- 加载：`types/鉴权类/`
- 判定：每节点过 D2（鉴权状态）
- 终止：完整覆盖 / 找到所有条件分支 / 深度 20

### 5.3 FWD-C（业务）
- 加载：`types/业务逻辑/`（9 类业务域）
- 判定：每节点过 D3（业务规则强制）
- 终止：业务流结束 / 深度 20

### 5.4 FWD-D（状态）
- 加载：`types/业务逻辑/状态机绕过.md` + `支付绕过.md`
- 判定：状态转换合法性
- 终止：状态闭环 / 深度 20

### 5.5 FWD-INFO
- 加载：`types/信息泄露/`
- 判定：返回值 / 日志 / 异常
- 终止：找到所有出口 / 深度 20

## 六、subagent prompt 模板

每个 subagent 的 prompt 在 `templates/subagent-FWD-{A,B,C,D,INFO}.md`。
**关键**：
- 不再包含"如 Memurai miss 则读 codegraph"（已预取过）
- 不再包含"如 codegraph 慢则用 ast-grep"（不会调 codegraph）
- 只包含：当前模式 + chain + 加载的规则 + 输出 schema

## 七、Memurai 批预取（**关键**）

启动 subagent **之前**必做：
1. SQLite 一次查整条链
2. 一次 `memurai-cli MSET k1 v1 k2 v2 ...` 写入所有方法
3. 写 `:prefetch:{chainId}` summary

subagent 启动后**不直接调 codegraph**，全程读 Memurai。

> 工具封装在 `scripts/redis/memurai_client.py`，统一通过 `C:\Program Files\Memurai\memurai-cli.exe` 调用；
> 不依赖 `pip install redis`。

详见 `rules/04-redis-strategy.md`（父 skill）。

## 八、失败兜底

详见 `rules/03-subagent-dispatch.md`（父 skill）。
- 失败 2 次 → 记 `subagent-failures.jsonl`
- 1h 无回复 → kill + dump 进度

## 九、产物

| 产物 | 路径 | 触发 |
|------|------|------|
| findings 草稿 | Memurai `:finding:*:draft`（`memurai-cli SET`） | FWD-X 完成 |
| findings 落盘 | `loop_audit/findings/{chainId}.json` | 3x85 评分后 |
| PoC 报告 | `loop_audit/poc/{chainId}.json` | 评级 ≥ 严重 + 环境可达 |
| 待人工 | `loop_audit/needs_human/{chainId}.md` | 不可达 / 工具失败 |

## 十、引用

- `sql-cheatsheet.md` — codegraph SQLite 用法
- `finding-schema.json` — finding 输出 schema
- `redis-key-schema.json` — Memurai key 设计（保留文件名兼容）
- `templates/` — 4 + 1 = 5 个 subagent prompt 模板
- `../../../types/` — 漏洞类型库
- `../../../scripts/` — Python 工具
- `../../../conduct/必读/` — 硬约束
