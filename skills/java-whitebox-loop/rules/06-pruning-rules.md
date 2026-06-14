# 剪枝规则（详细）

> 7 条主剪枝规则 + 每层消毒检查 + 跨轮次剪枝。详见 `conduct/必读/07-剪枝逻辑硬约束.md`。
> 本文件是规则定义与命中统计。

## 一、7 条 L1 端点级剪枝规则

| Rule ID | 名称 | 条件 | 动作 | 可复审 |
|---------|------|------|------|--------|
| **P-L1-001** | 无参数端点 | endpoint 无任何参数 | skip → 记 `no_params` | ✓ |
| **P-L1-002** | 仅数字参数 | 所有参数为 int/long/double/boolean/enum | skip → 记 `numeric_only` | ✓（仅 FWD-C/D） |
| **P-L1-003** | Filter 全覆盖 | 端点在 `permitAll()` 之外 + Filter 链覆盖 | skip FWD-B | ✓（其他 FWD 仍跑） |
| **P-L1-004** | 内部接口 + 内网 | `@Inner` 等内部注解 + 内网 IP | skip FWD-A | ✓（FWD-B/C/D 仍跑） |
| **P-L1-005** | 健康检查 | 路径含 `/health` / `/actuator/...` | skip | ✗（永不分析） |
| **P-L1-006** | API 文档 | 路径含 `/swagger` / `/v3/api-docs` | skip | ✗ |
| **P-L1-007** | 未识别 | 带未知自定义注解 | 标 `needs_human` | ✓（人工识别后归类） |

## 二、L2 链级剪枝（每层消毒）

详见 `conduct/必读/07-剪枝逻辑硬约束.md` § 三。

每节点判定：
1. 数据流是什么？
2. 该节点对数据做了什么？（消毒 / 转换 / 透传）
3. 消毒对当前 sink 类型是否有效？
4. 决策：continue / prune / maybe_prune

消毒器清单见同文件 § 3.2。

## 三、L3 跨轮次剪枝

| 状态 | 动作 |
|------|------|
| finding final | skip（不重做） |
| finding draft | 读取 + 继续评分 |
| 不存在 | 全新分析 |

## 四、规则文件结构

```markdown
# 剪枝规则 v{N}

## P-L1-001 无参数端点
- 状态：active / disabled / under-review
- 命中：{N} 次（{pct}%）
- 误杀：{M} 次（{pct}%）
- 抽样验证：人工抽样 30 条，发现 {K} 条误杀
- 决策：{继续/停用}
```

存到 `loop_audit/剪枝规则.md`。

## 五、误杀率自动检测

每轮 Loop 结束：
```bash
# 1. 取最近剪枝的 30 条
tail -30 loop_audit/pruning-log.jsonl

# 2. 人工复核（如有自动化工具则跑）
# 误杀率 = 真实漏洞数 / 30

# 3. 误杀 > 5% → 停用对应规则
```

## 六、复审接口

```bash
# 列出被剪枝的端点
python scripts/audit/list-pruned.py --group-id com.example.x

# 强制重扫
python scripts/audit/force-rescan.py \
  --endpoint "GET /api/foo" \
  --modes "C,D" \
  --group-id com.example.x
```

强制重扫规则：
- 必跑 FWD-C（业务）
- 必跑 FWD-D（状态）
- **不**跑 FWD-A / FWD-B / FWD-INFO

## 七、规则评分门槛

- 普通 finding 评分 ≥ 85
- **剪枝规则评分 ≥ 90**
- 误杀率 > 5% 立即停用
- 命中率 < 70% 标记 `low_value`
