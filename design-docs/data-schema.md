# 数据格式规范（Schema）

> 所有脚本与产物的输入输出格式定义。原子需求（`原子需求-v2.md`）与工作流（`工作流.md`）引用本文档，不内联 schema。
> 下游脚本按 schema 校验，字段缺失或类型不符即报错终止。

## 数据流总览

```
Phase 0 (准备)
  preset.json ─────────────────────────────────────────────────┐
                                                                │
Phase 1 (暴露面采集)                                            │
  preset.json ─► collectors × 8 ─► exposure/{type}.json        │
                  └► synthesizer  ─► exposure_assets.json      │
                                                                │
Phase 2 (调用链 + 缓存 + 排序)                                  │
  exposure_assets.json                                          │
    + codegraph.db (必选) ─► chain_builder  ─► chains/{ep}.txt  │
                              ─► chain_file_writer ─► chains/*  ◄┘
                              ─► method_cache (memurai 预取)    (TTL 24h)
                              ─► sink_registry   ─► sink_count + preset_matches
                              ─► priority_calc   ─► chain_data.json (priority 队列)
                              ─► auth_class_cacher ─► memurai {group_id}:auth:class:*

Phase 3 (AI 分析)
  chain_data.json (按 priority) ─► method_body_loader ─► method 体 (memurai GET)
                                  ─► chain_report_generator ─► analysis_report.json
                                  ─► load_counter ─► loads.db

Phase 4 (PoC + 收敛)
  analysis_report.json ─► poc-monitor      ─► poc_result.json
                        ─► self_evolution  ─► convergence.json + knowledge.json
                        ─► metric_simplifier ─► round_metrics.json (6 指标)
```

字段约定：`必填` 表示该字段不可缺；`可选` 表示可省略，缺省时取约定默认值；`枚举` 表示只能取列出的值之一。

---

## Phase 0

### preset.json（输入）

```json
{
  "projectRoot": "string (必填) — 目标项目根目录绝对路径",
  "codegraphDb": "string (必填) — codegraph SQLite 数据库路径",
  "groupId": "string (必填) — 项目命名空间前缀，用于 Memurai 键隔离",
  "loopDir": "string (可选) — loop_audit 输出目录，缺省 {projectRoot}/loop_audit",
  "sshTarget": "string (可选) — SSH 远程目标 user@host，缺失时 env_filter 降级",
  "commitHash": "string (必填) — 当前审计 commit，用于缓存键版本隔离"
}
```

`codegraphDb` 必填：所有调用链查询、sink 识别、方法体定位都依赖 codegraph，缺失即 exit 2 不降级（见 AR-01）。

---

## Phase 1

### exposure/{asset_type}.json（8 个采集器输出）

```json
{
  "asset_type": "string (必填) — 枚举 route|config|codegraph|auth_code|sensitive_info|waf|db_schema|env_filter",
  "collected_at": "string ISO8601 (必填)",
  "source": "string (必填) — 采集来源（ast-grep / codegraph / ssh / file-scan 等）",
  "stats": {
    "total": "int (必填) — items 数量",
    "degraded": "int (必填) — 降级采集条数，0 表示完整采集"
  },
  "items": [
    {
      "fqn": "string (必填) — 完整方法/类 FQN",
      "...": "类型特定字段（route 含 http_method/path/has_external_param；config 含 file/key/value；其余见各 collector）"
    }
  ]
}
```

`asset_type` 取值固定 8 种，collector 文件名与之对齐：`route.json` / `config.json` / `codegraph.json` / `auth_code.json` / `sensitive_info.json` / `waf.json` / `db_schema.json` / `env_filter.json`。

### exposure_assets.json（综合后）

```json
{
  "total_assets": "int (必填) — 去重后剩余资产总数",
  "by_type": {
    "route": "int (必填)",
    "config": "int (必填)",
    "codegraph": "int (必填)",
    "auth_code": "int (必填)",
    "sensitive_info": "int (必填)",
    "waf": "int (必填)",
    "db_schema": "int (必填)",
    "env_filter": "int (必填)"
  },
  "by_risk": {
    "high": "int (必填)",
    "medium": "int (必填)",
    "low": "int (必填)"
  },
  "assets": [
    {
      "fqn": "string (必填)",
      "_source_type": "string (必填) — 8 类之一，标明资产来源",
      "_risk": "string (必填) — 枚举 high|medium|low"
    }
  ],
  "dedup_report": {
    "before": "int (必填) — 去重前条数",
    "after": "int (必填) — 去重后条数",
    "removed": "int (必填) — 删除条数"
  }
}
```

`_source_type` 用于 Phase 2 chain_builder 区分哪些资产是路由入口、哪些是配置上下文。

---

## Phase 2

### chains/{endpoint}.txt（调用链文件）

```
org.example.UserController#getUser:node1->org.example.UserService#findById:node2->org.example.UserRepo#query:node3
org.example.UserController#getUser:node4->org.example.UserRepo#audit:node5
```

格式：单行一条调用链，节点形如 `{fqn}:{node_id}`，节点之间用 `->` 连接。文件名按 AGENTS.md 的 Windows-safe 规则（点号→`__`）。一端点一文件。

`node_id` 必须与 codegraph 中 nodes 表主键对齐，供后续 method_cache 检索使用。

### chain_data.json（链 + sink + 优先级）

```json
{
  "endpoint": "string (必填) — 形如 GET /api/users/{id}",
  "entry_fqn": "string (必填) — 入口方法 FQN",
  "chains": [
    {
      "chain_id": "string (必填) — 唯一链 ID",
      "nodes": [
        {
          "fqn": "string (必填)",
          "node_id": "string (必填)",
          "depth": "int (必填) — 距入口的调用深度，入口 depth=0"
        }
      ],
      "total_methods": "int (必填) — 链上方法体总数",
      "sink_count": "int (必填) — 链上 sink 总数（预置 + 动态）",
      "preset_sink_matches": "int (必填) — 命中预置 sink 库的次数",
      "priority": "int (必填) — 优先级分数，越大越先分析",
      "priority_breakdown": {
        "base": "int (必填) — HTTP 方法与参数 base 分",
        "sink_count": "int (必填) — sink_count 加分",
        "preset_match": "int (必填) — preset_sink_matches × 10"
      }
    }
  ],
  "total_chains": "int (必填) — 该端点链总数"
}
```

排序公式：`priority = base + sink_count + preset_sink_matches * 10`。`base` 取值：无外部参数 `-100` / POST/PUT/DELETE `10` / GET `0`。优先级计算必须发生在调用链构建之后（依赖 sink_count，而 sink_count 来自链上节点）。

### method_cache key（Memurai）

```
key:   audit:{groupId}:commit:{commitHash}:method:{fqn}#{sigHash}
value: 方法体文本（含签名、注解、方法体源码）
TTL:   24h
```

缓存时机：**调用链构建完成后、AI 分析前**，把链上所有方法体批量预取到 Memurai（见 AR-07）。AI 分析阶段直接 `GET`，不再每次查 codegraph。

辅助键（链摘要）：

```
key:   audit:{groupId}:commit:{commitHash}:prefetch:{chainId}
value: chain.json（链节点摘要）
TTL:   24h
```

`sigHash` 缺失时退化为 `body` 的 sha256 前 16 位，见 `scripts/redis/redis-batch-prefetch.py`。

### auth_class_cache key（Memurai）

```
key:   {groupId}:auth:class:{fqn}
value: 鉴权类 FQN + 类型清单（Filter / Interceptor / 注解）
TTL:   跨轮次保留（无 TTL，或与 knowledge 同寿）
```

---

## Phase 3

### analysis_report.json（单链报告）

```json
{
  "chain_id": "string (必填)",
  "entry_fqn": "string (必填)",
  "verdict": "string (必填) — 枚举 vuln|safe|unknown",
  "loaded_methods": "int (必填) — 本次实际加载的方法体数",
  "total_methods": "int (必填) — 链上方法体总数",
  "load_ratio": "float (必填) — loaded_methods / total_methods",
  "findings": [
    {
      "type": "string (必填) — 枚举如 SQL注入|CMD注入|SSRF|路径穿越|XXE|反序列化|SpEL|...",
      "root_cause": "string (必填) — 漏洞根因描述",
      "entry_point": "string (必填) — HTTP 入口（endpoint）",
      "payload": "string (必填) — PoC payload 草稿",
      "impact": "string (必填) — 业务影响",
      "cvss_4": {
        "base_score": "float (必填) — 0.0~10.0",
        "vector": "string (必填) — CVSS 4.0 向量串"
      }
    }
  ]
}
```

`verdict` 三值枚举对齐 Phase 4 的 `vuln_chains` / `safe_chains` / `unknown_chains` 计数。`findings[]` 仅在 `verdict=vuln` 时非空。**严禁**包含 `remediation` / `fix_suggestion` / `secure_alternative` 字段（硬约束，含则自评分 0）。

---

## Phase 4

### poc_result.json

```json
{
  "chain_id": "string (必填)",
  "poc_status": "string (必填) — 枚举 confirmed|denied|inconclusive",
  "payload_sent": "string (必填) — 实际发送 payload",
  "response_evidence": "string (必填) — 响应证据片段或状态码",
  "executed_at": "string ISO8601 (必填)"
}
```

仅 `confirmed` 计入 `vuln_chains` 与 `vuln_exposed_surface`。

### round_metrics.json（最终 6 指标）

```json
{
  "round": "int (必填) — 轮次序号",
  "chain_method_count": "int (必填) — 所有链上方法体总数",
  "loaded_method_count": "int (必填) — 实际加载数（来自 load_counter）",
  "load_ratio": "float (必填) — loaded_method_count / chain_method_count，理想 < 0.3",
  "vuln_chains": "int (必填) — verdict=vuln 的链数",
  "safe_chains": "int (必填) — verdict=safe 的链数",
  "unknown_chains": "int (必填) — verdict=unknown 的链数",
  "vuln_exposed_surface": "int (必填) — 确认有漏洞的端点数",
  "total_chains": "int (可选) — 链总数，作分母参考，不计入 6 项核心指标",
  "total_endpoints": "int (可选) — 端点总数，作分母参考"
}
```

6 项核心指标：`chain_method_count` / `loaded_method_count` / `vuln_chains` / `safe_chains` / `unknown_chains` / `vuln_exposed_surface`。其他字段作参考分母，不进入收敛判定。

### knowledge.json（跨轮次知识）

```json
{
  "custom_annotations": ["string (必填) — 项目自定义路由/鉴权注解 FQN"],
  "known_sanitizers": ["string (必填) — 已确认消毒器（PreparedStatement、HtmlUtils.escape 等）"],
  "dynamic_route_patterns": ["string (必填) — 反射/动态路由模式"],
  "false_positive_patterns": [
    {
      "pattern": "string (必填) — 误报特征",
      "reason": "string (必填) — 为何判定为误报"
    }
  ],
  "last_updated": "string ISO8601 (必填)"
}
```

跨轮持久化在 `{groupId}:knowledge:*` Memurai 键与 `{loop_audit_dir}/knowledge.json`，下一轮经 `merge_knowledge_from_memurai()` 自动合并。

---

## 验收

```powershell
# schema 定义数 ≥ 7
Select-String -Path design-docs/data-schema.md -Pattern "^### "

# 含数据流总览 ASCII 图
Select-String -Path design-docs/data-schema.md -Pattern "数据流总览"
```
