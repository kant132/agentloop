---
name: java-whitebox-loop
description: Java 代码白盒安全审计 Agent Loop 主入口。负责整体编排、6 阶段流转、Memurai 批预取协调（走 memurai-cli.exe）、subagent 调度、评分与自优化循环。调用子 skill `java-forward-vuln-discovery`、`threat-model-analyst` 等。触发关键词：'白盒审计'、'启动 loop'、'安全审计编排'。
---

# Java 白盒安全审计 Agent Loop — 主 skill

> 本文件是 loop 的**主入口与编排规范**。所有 Phase 门控、调度规则、评分细则都引用子 skill 或 `行为准则/` 下的独立文档。

## 一、7 大硬约束（必读）

启动本 skill 之前，**必须**先读 `行为准则/必读/` 7 篇：

1. `01-避免重复劳动.md` — Memurai 批预取（走 memurai-cli）+ sha256
2. `02-环境感知.md` — PoC 三态门控
3. `03-工具选择边界.md` — grep / codegraph SQL / ast-grep / Memurai
4. `04-中文输出硬约束.md`
5. `05-不写修复硬约束.md`
6. `06-三哲学自检模板.md`
7. `07-剪枝逻辑硬约束.md` — L1 端点级（无参数/数字参数默认不分析）+ L2 链级每层消毒 + L3 跨轮次

## 二、Loop 6 阶段总览

```
Phase 1 文档与环境识别
  ↓
Phase 2 威胁分析（必加 threat-model-analyst skill）
  ↓
Phase 3 Filter/Interceptor/Config 深度分析
  ↓
Phase 4 外部端点枚举
  ↓
Phase 5 调用链分析与漏洞验证（调 java-forward-vuln-discovery）
  ↓
Phase 6 汇总与自优化循环
```

每个 Phase 的**入口条件**、**产出物**、**流转规则**见 `rules/01-phase-gates.md`。

## 三、加载清单（启动前）

### 3.1 子 skills（必加）
- `java-forward-vuln-discovery` — Phase 5 主体（**本轮焦点**）
- `threat-model-analyst` — Phase 2 必加，缺失则 Phase 2 启动失败

### 3.2 子 skills（按需）
- `jadx-python-decompile` — jar 项目反编译
- `ssh-skill` — Phase 1 拓扑发现 + Phase 5 PoC 后端
- `playwright-skill` — Phase 5 PoC 前端

### 3.3 MCP 服务
- `codegraph` — 核心，缺失则降级 LSP
- `playwright` — 浏览器 PoC
- `memory` — 跨项目记忆
- `sequential-thinking` — 多步推理

### 3.4 工具链
- **Memurai**（Windows Redis 兼容，CLI 路径 `C:\Program Files\Memurai\memurai-cli.exe`）— 批预取 + 跨 subagent 共享
- Python 3 + `脚本/` 下的工具（封装在 `脚本/redis/memurai_client.py`）
- `codegraph` CLI（SQLite 直查）

## 四、Orchestrator 工作流（agent 控制，非脚本）

### 4.1 启动检查清单

```python
def start_loop(project_root, group_id):
    # 1. 启动检查
    assert read("行为准则/必读/")  # 6 篇必读
    
    # 2. codegraph 初始化
    run("codegraph init && codegraph index", cwd=project_root)
    
    # 3. Memurai 启动（Windows Redis 兼容服务）
    #    服务名为 "Memurai"（默认安装后随系统启动）
    #    也可通过 `memurai-cli ping` 验证；底层不需要 Python redis 库
    assert memurai_ping()  # 走 memurai-cli.exe -h localhost -p 6379 PING
    
    # 4. 自检
    run("python 脚本/redis/redis-self-check.py --group-id {group_id} ...")
    
    # 5. 加载项目特有知识
    project_knowledge = read("项目/{group_id}/")  # 如不存在则从 _template 复制
    
    # 6. 进入 Phase 1
    phase1(project_root, group_id)
```

### 4.2 Phase 流转决策（agent 而非脚本）

| 决策 | 判断依据 |
|------|---------|
| 进入下一 Phase | 当前 Phase 产出物全部 finished 标记 |
| 重启 subagent | subagent 失败 2 次且根因未明 |
| 改写规则 | 反思评分连续 3 轮 < 85 |
| 跳到 PoC | finding 评级 ≥ 严重 |
| 跳过 PoC | 环境不可达（读 Memurai `:env:reachability`，`memurai-cli GET`） |
| 终止 loop | 5 项结束条件全部满足 |

### 4.3 自优化循环（每轮 Loop 结束）

```
本轮反思 → 评分（30+30+30+10+10）
  │
  ├→ 任一项 < 27/9 → 优化后重跑
  │
  ├→ 连续 3 轮 > 85 → 标记 finished
  │
  └→ 三哲学自检（马斯克/康德/苏格拉底）必含
```

详见 `rules/02-scoring.md`（细化） + `行为准则/必读/06-三哲学自检模板.md`。

## 五、5 项结束条件

**全部满足**才可终止 loop：

1. 所有报告标记 finished
2. **致命/严重漏洞 PoC 验证率 ≥ 75%**
3. 调用链分析批判打分连续 3 轮 > 85
4. 本轮反思评分连续 3 轮所有项 > 85
5. 7+1 项数据对账 ≤ 1 项 WARN

## 六、引用文档

| 文档 | 路径 | 用途 |
|------|------|------|
| Phase 门控细则 | `rules/01-phase-gates.md` | 6 阶段入口/出口/流转 |
| 评分细则 | `rules/02-scoring.md` | 反思 + 调用链打分 |
| Subagent 调度 | `rules/03-subagent-dispatch.md` | 6 类角色、并行、心跳 |
| Memurai 缓存策略 | `rules/04-redis-strategy.md` | key 设计、TTL、预取（CLI 调用） |
| 数据对账 | `rules/05-data-reconcile.md` | 7+1 项对账 |
| 剪枝规则 | `rules/06-pruning-rules.md` | 7 条 L1 端点级剪枝 + L2 链级每层消毒 |
| 预置规则 | `rules/07-preset-rules.md` | groupId / 框架 / 注解 / 包白名单（加速扫描） |
| 子 skill 入口 | `../java-forward-vuln-discovery/SKILL.md` | Phase 5 |
| 威胁建模 | `../threat-model-analyst/SKILL.md` | Phase 2 |
| 行为规范 | `../../../行为准则/必读/` | 6 篇硬约束 |
| 漏洞类型库 | `../../../类型/` | FWD-X 判定基础 |
| 工具脚本 | `../../../脚本/` | Python 工具 |
