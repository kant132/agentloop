# {{groupId}} 审计反馈报告

> **目的**: 审计项目结束后, worker 必给 agentloop 工具本身提反馈, 沉淀到本文件。
> **追加方式**: 1 个项目 run 完 = 1 个 `## Project Run` 段, **沉底** 追加, 不修改历史 run。
> **触发**: 跑 `python 脚本/audit/collect-feedback.py` (或设 `AGENTLOOP_FINAL_FEEDBACK=1` 让 daemon 自动调用)
> **沉淀机制**: 跨 run 累积; 跨项目发现的关键不足会同步到主 agent 的 `boss-experience.md`。
> **模板版本**: v2 (2026-06-13 修订: 反馈从"每轮"改为"项目结束一次性")

---

## 元信息

| 字段 | 值 |
|------|---|
| groupId | `{{groupId}}` |
| 首次反馈 run | {{firstRunDate}} |
| 最近反馈 run | {{latestRunDate}} |
| 累计 run 数 | {{totalRuns}} |

---

## 反馈结构 (每个 Project Run 必含 4 段)

```
## Project Run {date} - {总轮次 rounds} - {总状态: PASS/FAIL/PARTIAL}

### 1. 跑通了什么 (工具优点)
- ...

### 2. 卡在哪里 (按 round 顺序列 3 个最大瓶颈)
- Round {N}: 卡在 {哪一步}, 原因: {为什么}, 后果: {导致什么}, 证据: {file:line}
- Round {N}: ...
- Round {N}: ...

### 3. 改进建议 (P0/P1/P2 排序)
- P0 (必修): ...
- P1 (关注): ...
- P2 (待观察): ...

### 4. 跨 run 趋势 (与上一个 run 比)
- 改善: ...
- 退化: ...
- 待观察: ...
```

---

## Project Run 1 - {{date}} - {{nRounds}} rounds - {{status}}

### 1. 跑通了什么
{{whatsWorks}}

### 2. 卡在哪里 (3 个最大瓶颈)
{{whatsBroken}}

### 3. 改进建议
{{improvements}}

### 4. 跨 run 趋势
{{trendVsLastRun}}

---

## 工具不足跨 run 沉淀 (主 agent 自动汇总)

<!-- 主 agent 在每个 run 反馈后, 扫描本文件所有 Project Run 段, 自动汇总"卡在哪里"段,
     按出现频次排序, 写入此节。频次高 = 工具必须修。 -->

### 频次 ≥ 3 次的不足 (P0 必修)
| 不足 | 频次 | 首次 run | 最近 run |
|------|:---:|---------|---------|
| {{issue}} | {{count}} | {{date}} | {{date}} |

### 频次 2 次的不足 (P1 关注)
| 不足 | 频次 | 首次 run | 最近 run |
|------|:---:|---------|---------|
| {{issue}} | {{count}} | {{date}} | {{date}} |

### 频次 1 次的不足 (P2 待观察)
- {{issue}} ({{date}})

---

## 改进建议采纳状态 (主 agent 跟踪)

<!-- 主 agent 收到改进建议后, 在此跟踪实施状态 -->

| 建议来源 run | 建议内容 | 优先级 | 状态 | 实施 commit |
|-------------|---------|:---:|------|------------|
| {{date}} | {{suggestion}} | P0/P1/P2 | 接受/拒绝/搁置 | {{commitHash}} |

