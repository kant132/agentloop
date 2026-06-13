# Phase 门控细则

> 6 个 Phase 的入口条件、产出物、流转规则。

## Phase 1：文档与环境识别

**入口条件**：项目根目录存在 `pom.xml` / `build.gradle`，或为反编译 jar 项目

**核心步骤**：
1. 读取 `doc/` 下所有业务文档（业务需求、架构、部署、用户手册）
2. **SSH skill 探测业务部署环境**（前端 URL、SSH、网络拓扑、上下游）→ 生成 `.drawio` 路由图
3. 执行 `codegraph init && codegraph index`
4. 若项目含 jar 包 → 调 `jadx-python-decompile`
5. 启动 Memurai（Windows 服务，默认端口 6379）→ 用 `memurai-cli SET` 写 `:env:reachability` key

**产出**：
- `loop_audit/环境拓扑-{项目}.md` + `.drawio`
- Memurai `:env:reachability` key

**流转**：环境拓扑覆盖核心节点 + codegraph 索引就绪 → Phase 2

## Phase 2：威胁分析

**入口条件**：Phase 1 产出达标

**核心步骤**：
1. **必加** `threat-model-analyst` skill → STRIDE + 攻击树
2. 绘制数据流图（DFD）、模块调用图（UML sequenceDiagram/组件图）
3. 梳理技术栈、模块清单、协议特征
4. 枚举所有认证鉴权实现（注解 / Filter / Interceptor / SecurityConfig）
5. 登记鉴权组件到 `loop_audit/auth/`

**产出**：
- `loop_audit/modules/模块分析.md`（含模块调用关系 + 协议）
- `loop_audit/auth/认证鉴权文件清单.md`
- `reports/02-认证鉴权全景报告.md`（草稿，Phase 3 后定稿）

**流转**：所有鉴权组件候选清单就绪 → Phase 3

## Phase 3：Filter/Interceptor/Config 深度分析

**入口条件**：Phase 2 鉴权组件清单完成

**核心步骤**：
1. 对每个 Filter / Interceptor / SecurityConfig / 自定义鉴权注解 → **独立建档**到 `loop_audit/filters/{类名}.md`
2. codegraph SQL 查类/方法特征
3. 评估执行顺序、注册位置、URL 覆盖、信任传递
4. 标注鉴权绕过风险

**产出**：
- `loop_audit/filters/*.md`（每个组件一份）
- `reports/02-认证鉴权全景报告.md`（定稿）

**流转**：每个鉴权点都有评估文件 → Phase 4

## Phase 4：外部端点枚举

**入口条件**：Phase 1 已识别 Controller / Servlet / Dubbo Service 类清单

**核心步骤**：
1. 按技术栈加载对应注解清单
2. 枚举 REST / RPC / Servlet / WebSocket / GraphQL / MQ / 定时任务入口
3. 提取方法签名、参数、所属 Controller、保护状态（**初判**）
4. 与 02-全景报告交叉对账
5. **L1 端点级剪枝**（来自 `行为准则/必读/07`）：
   - 应用 7 条 L1 规则（无参数 / 数字参数 / Filter 覆盖 / 内部接口 / 健康检查 / API 文档 / 未识别）
   - 命中 → 写 `loop_audit/pruning-log.jsonl` + 跳过 Phase 5
   - 不命中 → 进入 Phase 5 分析
6. **剪枝结果分桶**：
   - `高优先级桶`（P0）：未剪枝的敏感端点
   - `中优先级桶`（P1）：未剪枝的普通端点
   - `低优先级桶`（P2）：未剪枝的辅助端点
   - `剪枝桶`：被 L1 规则跳过的端点（待人工复审）

**产出**：
- `loop_audit/external_endpoints.jsonl`
- `loop_audit/外部调用点清单.md`
- `loop_audit/pruning-log.jsonl`（被剪枝的端点）
- `项目/{groupId}/preset.json`（如缺失则用 `preset-init.py` 生成草案）

**流转**：端点清单完整 + 剪枝桶 + 优先级分桶 → Phase 5

## Phase 5：调用链分析与漏洞验证（**本轮焦点**）

**入口条件**：Phase 4 端点清单固化 + Memurai 预取就绪

**核心步骤**：
1. **调 `java-forward-vuln-discovery` skill**
2. 对每个 P0 端点启动 4 个 FWD subagent 并行（A/B/C/D）
3. SQLite 一次查链 → `memurai-cli MSET` 批预取 → subagent 全程读 Memurai
4. 致命/严重/中危 → PoC 验证（SSH / playwright）
5. 3x85 评分 → 落盘

**产出**：
- `loop_audit/findings.jsonl`（机器清单）
- `loop_audit/findings/{chainId}.json`（3x85 落盘）
- `loop_audit/poc/{chainId}.json`（PoC 报告）
- `loop_audit/needs_human/`（待人工）

**流转**：所有 P0 端点分析完成 + PoC 验证率 ≥ 75% → Phase 6

## Phase 6：汇总与自优化循环

**入口条件**：Phase 5 完成 或达到部分终止条件

**核心步骤**：
1. 7+1 项数据对账
2. 生成 `loop_audit/终态汇总报告.md`（含威胁建模、鉴权、API 资产、漏洞分级、PoC、待人工、自评）
3. 触发自优化循环
4. 判定终止条件

**产出**：`loop_audit/终态汇总报告.md`（**唯一对外交付物**）

**流转**：5 项结束条件全部满足 → loop 终止
