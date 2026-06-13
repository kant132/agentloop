# 优化路径 01：Token 消耗优化

> 单轮 Loop token 目标：P0 端点 < 30k tokens，端点平均 < 10k tokens。

## 一、Token 消耗大头

| 来源 | 占比（典型） | 优化手段 |
|------|-------------|---------|
| **方法体** | 30% | Memurai 批预取（4 FWD 共享，走 memurai-cli MSET） |
| **prompt 模板** | 20% | 拆分：核心指令 + 引用文档 |
| **链节点** | 15% | Memurai 共享（一次预取 4 FWD 共享） |
| **LLM 推理输出** | 25% | 限制输出 schema + 强制 JSON |
| **类型库加载** | 10% | 一次性 + 按需子集 |

## 二、关键技巧

### 2.1 批预取 = 单次传输
**错误**：每个 FWD subagent 独立从 codegraph 读方法
**正确**：Orchestrator 一次 SQL 查询取整链 → 一次 `memurai-cli MSET k1 v1 k2 v2 ...` 写入 Memurai → 4 FWD 全程读 Memurai

节省：方法体传输 token 减少 75%（4 FWD 共享 = 1 次而非 4 次）

### 2.2 prompt 拆分
**错误**：单 prompt 5000 tokens 含全部规则
**正确**：
- 核心指令（500-800 tokens）：必含
- 类型库（按需 Read）：subagent 启动时按业务域加载相关子集
- 案例库（按需 Read）：仅在 LLM 信心不足时加载

### 2.3 输出约束
**错误**：让 LLM 自由发挥
**正确**：强制 JSON schema + 字段长度限制

```json
{
  "summary": "string, 中文, max 200 chars",
  "evidence_chain": ["array, max 10 items"],
  "poc_payload": "string, max 500 chars"
}
```

### 2.4 链深度控制
- 深度上限 20（>95% 链 < 15）
- 深度 > 15 → 警告（很可能是循环或框架内部）
- 剪枝规则命中即停

### 2.5 finding 草稿
- 草稿不落盘（在 Memurai `:finding:*:draft`）
- 3x85 评分后才落盘
- 节省 90% 落盘 IO

## 三、监控指标

```python
# 每轮 Loop 结束统计
{
  "total_tokens": N,
  "per_finding_avg": N / findings_count,
  "per_endpoint_avg": N / endpoints_count,
  "codegraph_calls": X,
  "memurai_hits": Y,
  "cache_hit_rate": Y / (Y + memurai_misses),
  "write_promotions": Z  # 3x85 落盘次数
}
```

## 四、阈值

| 指标 | 健康 | 警告 | 异常 |
|------|------|------|------|
| 单 finding token | < 5k | 5-10k | > 10k |
| 单端点 token | < 15k | 15-30k | > 30k |
| 缓存命中率 | > 70% | 50-70% | < 50% |
| 写盘率 | 30-70% | 10-30% 或 70-90% | < 10% 或 > 90% |

## 五、优化回路

```
指标异常
  │
  ├→ 缓存命中率低 → 优化 Memurai key 设计
  │
  ├→ 单 finding token 高 → 检查是否含完整调用链（应只传 evidence 不传整链）
  │
  ├→ 写盘率过低 → 检查评分门槛是否过严
  │
  └→ 写盘率过高 → 检查评分门槛是否过松
```
