# 优化路径 04：subagent 失败率降低

> subagent 失败 = 重启 + 重读 chain + 重做 = 浪费 5-10 分钟/次。

## 一、失败类型

| 类型 | 占比（典型） | 信号 | 防御 |
|------|-------------|------|------|
| **超时** | 40% | 1h 无回复 | 心跳 + 自动 kill + 拆分长链 |
| **JSON 格式错** | 25% | LLM 输出不符合 schema | 强制 schema + 重试时附格式提示 |
| **逻辑死锁** | 15% | 反复问相同问题 | 限制追问次数 |
| **OOM / 上下文爆炸** | 10% | token 超限 | 压缩 prompt + 拆链 |
| **工具调用失败** | 10% | codegraph / Memurai 不可用 | 降级策略 |

## 二、各类防御

### 2.1 超时
- 1 小时心跳 → kill
- kill 前 dump 进度到 Memurai（`memurai-cli SET key value EX 3600`）：
  ```
  audit:subagent-killed:{subagent_id} = {
      killed_at: ...,
      completed_chains: [...],
      remaining: [...]
  }
  ```
- 重启时优先调度 `remaining`

### 2.2 JSON 格式错
- prompt 末尾附 JSON 示例（**正例**）
- 解析失败时 → 重试，附 `previous_output_issue: "missing field 'state'"`
- 重试 ≤ 2 次，仍失败 → 标记 `parse_error`，人工处理

### 2.3 逻辑死锁
- 限制 LLM 内部循环次数（via tool 调用计数）
- 超过 N 次同质问题 → 强制终止 + 记录 `stuck_pattern`
- 死锁 subagent 失败率 > 5% → 改进 prompt

### 2.4 OOM / 上下文爆炸
- 单 subagent 输入 < 8000 tokens
- 链深度限制 20
- 链节点超 30 → 拆分子任务
- 关键：批预取减少上下文 = subagent 启动时已加载全部方法，无需再查

### 2.5 工具调用失败
- codegraph 不可用 → 降级 LSP（仅查询，链追踪失败）
- Memurai 不可用（`memurai-cli ping` 失败） → 降级 dict 缓存 + 警告
- ast-grep 不可用 → 降级 grep + 警告

## 三、失败兜底

```python
def run_subagent_with_fallback(role, input):
    for attempt in 1..3:
        try:
            result = subagent.run(role=role, input=input, timeout=3600)
            if validate_schema(result):
                return result
            else:
                # 格式错，重试
                continue
        except TimeoutError:
            log_kill(role, input, attempt)
            dump_progress_to_memurai(role, input)  # memurai-cli SET
            return partial_result_or_kill
        except ToolUnavailable:
            # 工具失败，降级
            input_with_fallback = add_fallback_warnings(input)
            return run_with_degraded_tools(role, input_with_fallback)
    
    # 3 次都失败
    log_final_failure(role, input)
    return error_result
```

## 四、失败学习

- 失败 subagent 写入 `loop_audit/feedback/subagent-failures.jsonl`
- 同类失败 ≥ 3 次 → 沉淀到 `行为准则/经验/` 新文件
- 失败模式分类：
  - prompt 缺陷 → 改 prompt
  - 工具缺陷 → 修工具 / 改 fallback
  - 资源不足 → 拆分任务
  - 模型能力 → 换模型 / 拆分问题

## 五、监控指标

```python
{
  "subagent_total": N,
  "subagent_failures": M,
  "failure_rate": M / N,
  "by_type": {
    "timeout": x,
    "json_parse": y,
    "stuck": z,
    "oom": w,
    "tool_unavailable": v
  },
  "avg_retry_count": 1.4,
  "total_wasted_tokens": 50000
}
```

## 六、目标

- 失败率 < 5%
- 单 subagent 重试 ≤ 1.5 次
- 超时占比 < 20%
