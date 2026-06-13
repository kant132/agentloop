# 经验 01：PoC 失败处理回路

> 来自原始规范："对发现的致命、严重、中危漏洞链，通过环境信息进行 PoC 验证"。

## 一、PoC 三态结果

| 状态 | 含义 | finding 标记 |
|------|------|-------------|
| **是问题** | PoC 触发漏洞，证据明确 | `poc_status=confirmed_vuln` |
| **非问题** | PoC 失败，确认是 false positive | `poc_status=confirmed_safe` |
| **暂时无法确认** | 环境受限/工具失败/逻辑不清 | `poc_status=inconclusive` |

**三者都计入"PoC 通过"**（75% 终止条件）。仅 `skipped_unreachable` 不算。

## 二、失败根因分类

PoC 失败的根因决定后续动作：

| 根因 | 判定信号 | 后续动作 |
|------|---------|---------|
| **环境不可达** | SSH/HTTP 不可达 | 标记 `skipped_unreachable`，进入 `needs_human` |
| **环境受限** | 部分可达（如只有 SSH） | 降级 PoC，记录 `degraded` |
| **PoC 逻辑错** | payload 选错、HTTP 头错 | 修正 payload 重试，限 2 次 |
| **漏洞判断错** | 假阳，原本就不是漏洞 | 评级降级 + 沉淀到 `类型/{X}.md` 误报模式 |
| **触发条件不满足** | 缺鉴权、缺参数、缺前置 | 完善 PoC 链 + 改 prerequisites |
| **工具/脚本 bug** | 执行异常、返回 500 错 | 修工具，记入 `脚本/deprecated/` |

## 三、回路设计

```
PoC 失败
   │
   ├─→ 根因 = 不可达 ──→ 标 skipped，进 needs_human
   │
   ├─→ 根因 = 受限 ──→ 降级 PoC，重试
   │                       │
   │                       └→ 仍失败 ──→ 标 inconclusive
   │
   ├─→ 根因 = 逻辑错 ──→ 修正 payload，重试 ≤ 2 次
   │                       │
   │                       └→ 仍失败 ──→ 标 inconclusive + 记录失败模式
   │
   ├─→ 根因 = 假阳 ──→ 评级降级 + 沉淀误报模式
   │
   └─→ 根因 = 工具 bug ──→ 修工具，归档失败脚本
```

## 四、沉淀机制

### 4.1 PoC 模板库
- 成功的 PoC 模板 → `loop_audit/poc-templates/{type}-{chainId}.json`
- 含：完整 payload + 触发步骤 + 预期响应
- 跨项目复用

### 4.2 失败案例库
- 失败的 PoC → `loop_audit/feedback/poc-failures.jsonl`
- 含：尝试过的 payload + 失败原因 + 根因分类
- 防止下次重复犯错

## 五、75% 终止条件

```
PoC 通过率 = (confirmed_vuln + confirmed_safe + inconclusive) / total_high_risk
           ≥ 75% 才可结束 loop
```

**`inconclusive` 计入分子**——因为它代表"已尽力但环境受限"，不是"没做"。

## 六、subagent 必读

PoC subagent 启动前必读本文，输出时必带：
- `poc_status` 字段
- `poc_root_cause`（失败时）
- `poc_attempts` 数组（每次尝试的 payload + 结果）
