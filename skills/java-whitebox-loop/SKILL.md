---
name: java-whitebox-loop
description: "Java 白盒审计顶层入口。用户提供 preset.json，自动产出 loop_audit/ 下 8 类产物 + 跨轮 knowledge.json。触发词:`白盒审计` / `启动 loop` / `安全审计编排`。"
---

# Java 白盒审计 — 入口契约

> 本 SKILL.md 是用户视角顶层说明。详细规则分散到 rules/ 下 5 个文件，启动时一次读完。

## 调用 & 触发

- **Daemon**: `python {agentloop_root}/scripts/audit/cross-agent-50r.py --preset projects/{group_id}/preset.json`
- **Opencode 触发词**: `白盒审计` / `启动 loop` / `安全审计编排`

完整 preset 参数、产出清单、脚本依赖、路径占位符 → **rules/entry-contract.md**

## Phase 0 — Pre-flight: 清空上轮缓存 (MANDATORY)

> 每次启动前必须执行，不可跳过。上轮残留 draft/final/chain 缓存会污染本轮结果。

```powershell
$keys = memurai-cli --raw KEYS "{group_id}:*" | Where-Object { $_ -notmatch "^{group_id}:knowledge:" }
if ($keys.Count -gt 0) {
    $keys | ForEach-Object { memurai-cli DEL $_ }
    Write-Host "[Phase 0] Cleared $($keys.Count) cache keys for {group_id}"
} else {
    Write-Host "[Phase 0] No stale cache keys found, skip"
}
# 验证 knowledge:* 未被误删
$knowledge_keys = memurai-cli --raw KEYS "{group_id}:knowledge:*"
Write-Host "[Phase 0] Preserved $($knowledge_keys.Count) knowledge keys"
```

> Daemon 模式自动执行；Opencode 直接触发时 agent 必须手动执行后再进入 Phase A。

## 必读 Rules (≤5 个文件，启动时一次读完)

1. **`rules/entry-contract.md`** — 调用方式、preset 参数、产出清单、脚本依赖、路径占位符
2. **`rules/phase-gates.md`** — 4 阶段入口/出口/失败回退
3. **`rules/pruning-and-keys.md`** — L1/L2/L3 剪枝 + Memurai key schema
4. **`rules/self-evolution.md`** — scoring/convergence/FP sampling/knowledge merge
5. **`rules/goals.md`** — 5 个根本目标 + 硬约束 (不可绕过)
