# 数据对账（8+1 项）

> 终结 Loop 前必做的数据一致性校验。

## 一、对账项

| # | 项目 | 验证内容 |
|---|------|---------|
| 1 | **端点清单一致性** | 端点清单总数 vs API 资产报告 |
| 2 | **finding 总数一致** | finding 清单 vs 各端点 finding 之和 |
| 3 | **PoC 覆盖** | high_risk 数 vs PoC 完成数（≥ 75%） |
| 4 | **评分分布** | 评分总数 vs 评分 ≥ 85 的 finding 数 |
| 5 | **落盘一致性** | 落盘 finding 数 vs Memurai final finding 数 |
| 6 | **链数一致** | 链总数 vs 端点 finding chain_id 去重数 |
| 7 | **subagent 失败** | 失败数 vs subagent-failures.jsonl |
| **8** | **端点报告覆盖率（P5.4 不变量）** | `高风险端点/.md` + `中低险端点/.md` 文件数 == 端点清单条目数 |
| **+9** | **Memurai 链 key 防造数据** | 声明的链数 vs Memurai 实际 chain key 数 |

### 1.1 第 8 项详解（P5.4 硬约束）

**不变量**（来自原始指令 § 一.6 P5.4）：
> 每个外部端点的每个方法都必须输出一篇独立的调用链分析报告

→ 端点×方法 笛卡尔积 = N 个报告，全部去 `routes/{高风险端点,中低险端点}/`

**校验脚本**：`脚本/audit/verify-endpoint-coverage.py`

```bash
python 脚本/audit/verify-endpoint-coverage.py \
  --endpoints loop_audit/external_endpoints/端点.jsonl \
  --high-risk-dir loop_audit/routes/高风险端点/ \
  --mid-low-risk-dir loop_audit/routes/中低险端点/
```

**判定**：
- 缺失报告：端点在清单中但**无对应 .md 文件** → 潜在漏报
- 多余报告：有 .md 但端点清单中**无对应条目** → 重复劳动或端点过期
- 文件名不规范：无法解析（fqn + sig_hash 缺失）→ 报告格式错误

**文件名规范**（O3 / P5.6）：
```
{危险等级}_{全限定类名}_{方法名}_{签名Hash}.md
例：严重_com.example.UserController_search_a1b2c3d4.md
```

> Windows 文件名不允许 `.`，所以类名中的 `.` 写作 `__`（如 `com__example_UserController`）。
> 校验脚本解析时还原 `_` → `.`。

## 二、执行

```bash
# 主对账（item 1-7, +9）
python 脚本/audit/data-reconcile.py \
  --findings loop_audit/findings.jsonl \
  --endpoints loop_audit/external_endpoints/端点.jsonl \
  --group-id com.example.x \
  --commit HEAD

# 端点报告覆盖率（item 8，**新增**）
python 脚本/audit/verify-endpoint-coverage.py \
  --endpoints loop_audit/external_endpoints/端点.jsonl \
  --high-risk-dir loop_audit/routes/高风险端点/ \
  --mid-low-risk-dir loop_audit/routes/中低险端点/
```

## 三、判定

- **all_ok = true** → 终止条件之一达成
- **warn_count ≤ 1** → 仍可终止
- **warn_count ≥ 2** → 不可终止，继续优化

## 四、item 8 失败常见原因

| 现象 | 根因 | 修复 |
|------|------|------|
| `missing` 非空 | 端点未分析 | 检查 subagent-failures.jsonl，补跑失败端点 |
| `extra` 非空 | 端点已废弃但报告未清 | 删多余报告 + 更新端点清单 |
| `invariant_ok = false` | 文件数 ≠ 端点数 | `len(高风险) + len(中低险) == len(端点清单)` |
