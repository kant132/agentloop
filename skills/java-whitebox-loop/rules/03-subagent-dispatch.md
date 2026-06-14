# Subagent 调度规则

> 6 类 subagent 角色、并行策略、心跳、失败兜底

## 一、6 类角色

| Role ID | 模式 | 输入 | 输出 |
|---------|------|------|------|
| **ENV-1** | 环境识别 | 项目根 + groupId | 拓扑 .drawio + Memurai env key |
| **TM-1** | 威胁建模 | 业务文档 + 技术栈 | STRIDE 报告 + DFD |
| **EP-1** | 端点枚举 | 项目元信息 | endpoints.jsonl |
| **FWD-A** | 数据流 | 链 + Memurai key | 注入类 finding |
| **FWD-B** | 鉴权 | 链 + 鉴权清单 | 鉴权类 finding |
| **FWD-C** | 业务 | 链 + 业务规则模板 | 业务类 finding |
| **FWD-D** | 状态 | 链 + 状态机模板 | 状态类 finding |
| **POC-X** | PoC 验证 | finding + 环境 key | poc_status |
| **REFLECT** | 反思 | 全部产出 | 评分 + 三哲学自检 |

## 二、并行策略

| 阶段 | 并行 | 上限 |
|------|------|------|
| **FWD-X**（每端点） | 4（A+B+C+D 全并行） | 4 |
| **端点并行**（全局） | P0: 3, P1: 5, P2: 10 | 10 |
| **PoC** | 4 | 4 |
| **总并发** | 受 codegraph + Memurai 约束 | 10 |

## 三、心跳 + 失败兜底

- 1 小时无回复 → kill
- kill 前 dump 进度到 Memurai
- 失败 subagent 写 `loop_audit/feedback/subagent-failures.jsonl`
- 同类失败 ≥ 3 次 → 沉淀到 `conduct/经验/`

## 四、详细策略

详见子 skill `java-forward-vuln-discovery/SKILL.md` 中的 subagent 调度部分。
