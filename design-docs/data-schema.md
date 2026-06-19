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
                               ─► method_cache (memurai 预取)    (无 TTL，session 结束 hook 清理)
                               ─► sink_registry   ─► dynamic_sink_count + preset_sink_matches
                               ─► priority_calc   ─► chain_data.json (priority 队列, dynamic×1 + preset×10)
                              ─► auth_class_cacher ─► memurai {group_id}:auth:class:*

Phase 3a (环境配置导出 + AI 配置分析)
  env_export (SSH 可达时) ─► config_collector ─► env_export/*.{conf,sql,xml}
                            ─► AI 配置分析   ─► config_analysis.json

Phase 3b (AI 认证鉴权分析，先于调用链)
  auth_code assets + config_analysis ─► AI 鉴权分析 ─► auth_analysis.json

Phase 3c (AI 调用链分析)
  chain_data.json (按 priority) + auth_analysis ─► method_body_loader ─► method 体 (memurai GET)
                                                   ─► chain_report_generator ─► chain_analysis_report.json
                                                   ─► load_counter ─► loads.db

Phase 4 (PoC + 收敛)
  chain_analysis_report.json ─► poc-monitor      ─► poc_result.json
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

`codegraphDb` 必填：所有调用链**拓扑查询**、sink 识别都依赖 codegraph，缺失即 exit 2 不降级（见 AR-01）。**注意**：方法体本身不从 codegraph 读取，codegraph 只用于构建调用拓扑（node_id、edges、fqn、start_line、end_line、file_path），方法体由 `tools/javaparser/java-method-call-extractor-1.0.0.jar` + 源文件读取获得（见 `method_cache key` 段「获取方式」）。

**字段用途与消费者**：

| 字段          | 用途                  | 消费者                                           |
| ----------- | ------------------- | --------------------------------------------- |
| projectRoot | 定位目标项目源码根           | 所有 collector + chain_builder + JAR 抽取         |
| codegraphDb | 调用拓扑查询 + sink 识别数据源 | chain_builder、sink_registry、auth_class_cacher |
| groupId     | Memurai 键隔离前缀       | 所有 memurai 读写 + Phase 0 清理 hook               |
| loopDir     | 输出目录定位              | 所有产物落盘路径                                      |
| sshTarget   | 远程环境检查目标            | env_filter collector（缺失则降级，见 AR-19）           |
| commitHash  | 缓存键版本隔离             | method_cache key（用于轮次隔离参考）                    |

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

**item 字段用途与消费者**：

| 字段 | 用途 | 消费者 |
|------|------|--------|
| asset_type | 标记资产来源类型，决定下游处理路径 | synthesizer 分流 |
| collected_at | 采集时序，用于新鲜度判定 | synthesizer 去重选最新 |
| source | 区分 ast-grep / codegraph / ssh 来源 | 审计回溯定位 |
| stats.total | 全量条数 | exposure_assets.json 的 by_type |
| stats.degraded | 降级采集数（0=完整） | 风险评估 + AR-19 env_filter 标记 |
| items[].fqn | 资产方法/类 FQN | chain_builder 选入口 + dedup |
| items[...]（类型特定） | route: http_method/path/has_external_param；config: file/key/value；其余各 collector 自有字段 | Phase 2 排序参数 + 鉴权上下文 |

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

**字段用途与消费者**：

| 字段 | 用途 | 消费者 |
|------|------|--------|
| total_assets | 去重后资产总数 | 端点覆盖率分母 |
| by_type | 8 类资产计数 | 资产均衡性检查 |
| by_risk | high/medium/low 计数 | 风险分级报告 |
| assets[].fqn | 资产唯一键 | chain_builder 入口枚举 |
| assets[]._source_type | 标记来源类型（8 类之一） | chain_builder 区分路由/配置 |
| assets[]._risk | high/medium/low | Phase 2 排序参考 |
| dedup_report.before/after/removed | 去重审计 | 质量自检 |

---

## Phase 2

### chains/{endpoint}.txt（调用链文件）

```
org.example.UserController#getUser:node1->org.example.UserService#findById:node2->org.example.UserRepo#query:node3
org.example.UserController#getUser:node4->org.example.UserRepo#audit:node5
```

格式：单行一条调用链，节点形如 `{fqn}:{node_id}`，节点之间用 `->` 连接。文件名按 AGENTS.md 的 Windows-safe 规则（点号→`__`）。一端点一文件。

`node_id` 必须与 codegraph 中 nodes 表主键对齐，供后续 method_cache 检索使用。

**格式与消费者**：

| 元素 | 用途 | 消费者 |
|------|------|--------|
| 单行一链 | 调用链拓扑存储单元 | chain_file_writer 写 / Phase 3 读 |
| `{fqn}:{node_id}` | 节点 FQN + codegraph 主键 | method_cache key（fqn+startline） |
| `->` 连接 | 表示调用方向 | 链遍历 |
| 文件名 Windows-safe | 端点路径转义（点→`__`） | 一端点一文件定位 |
| depth 信息 | （在 chain_builder 内存结构）不在 txt | Phase 3 前 5 层加载筛选 |

### chain_data.json（优先级队列，不重复存 nodes）

> **设计理由**：链拓扑已在 `chains/{endpoint}.txt`，此处只存队列元数据 + 优先级分数，避免重复。**nodes 结构不在此文件**，由 `chains/{endpoint}.txt` 提供（见上一段）。

```json
{
  "endpoint": "GET /api/users/{id}",
  "queue": [
    {
      "chain_id": "chain_001",
      "chain_file": "chains/GET_api_users_id.txt",
      "dynamic_sink_count": 2,
      "preset_sink_matches": 1,
      "priority": 22,
      "priority_breakdown": {"base": 0, "dynamic": 2, "preset": 10}
    }
  ]
}
```

| 字段 | 类型 | 用途 | 消费者 |
|------|------|------|--------|
| endpoint | string | 端点标识，用于关联链文件 | Phase 3 取队列 |
| queue | array | 按优先级降序排列的链条目 | Phase 3 按序消费 |
| queue[].chain_id | string | 链唯一标识 | Phase 3/4 报告引用 |
| queue[].chain_file | string | 指向 chains/{endpoint}.txt 路径 | Phase 3 读链拓扑 |
| queue[].dynamic_sink_count | int | 动态识别的非 groupId 调用数（基础权重×1） | 优先级计算 + 统计 |
| queue[].preset_sink_matches | int | 匹配预置危险 sink 库的数量（高权重×10） | 优先级计算 + 统计 |
| queue[].priority | int | 最终优先级分数 | Phase 3 排序依据 |
| queue[].priority_breakdown | object | 分数明细（base/dynamic/preset） | 调试 + 可解释性 |

**排序公式**（区分动态 sink 与预置 sink 权重）：

```
priority = base + dynamic_sink_count × 1 + preset_sink_matches × 10
```

- `base`：无外部参数 `-100` / POST/PUT/DELETE/PATCH `10` / GET `0`
- `dynamic_sink_count × 1`：动态识别的非 groupId 命名空间调用（外部依赖），基础权重 ×1
- `preset_sink_matches × 10`：命中预置危险 sink 库（Runtime.exec、Statement.executeQuery 等 80+ 条），高权重 ×10（动态的 10 倍）

> **sink 类型说明（务必区分）**：
> - **动态 sink**（dynamic_sink_count）：运行时通过 JAR 抽取的所有非 groupId 命名空间方法调用，覆盖所有外部依赖。
> - **预置 sink**（preset_sink_matches）：用预置危险函数库精确匹配的（SQL/RCE/LDAP/SpEL/NoSQL/XXE/反序列化/SSRF/路径穿越等 15 类约 80 条）。
> - **预置匹配用于增加权重**（×10），预置匹配必定是 dynamic_sink_count 的子集，不单独重复计数。
> - **工作流顺序**：①先动态找到所有非 groupId 调用 → dynamic_sink_count；②然后用预置库匹配 → preset_sink_matches；③预置匹配增加权重（×10）→ 修改优先级。

优先级计算必须发生在调用链构建（AR-05）与 sink 识别（AR-08）之后（依赖 dynamic_sink_count 与 preset_sink_matches，二者均来自链上节点）。

### method_cache key（方法体缓存）

**格式**：
```
{groupId}:method:{fqn}#{startline}
```

| 段 | 说明 | 示例 |
|----|------|------|
| `{groupId}` | 项目 groupId（Phase 0 清理前缀） | `org.owasp.webgoat` |
| `method` | 固定段标记 | — |
| `{fqn}` | 方法完整限定名 | `com.example.UserController#getUser` |
| `{startline}` | 方法起始行号（1-based） | `42` |

**设计理由**：
- `{groupId}:` 开头，Phase 0 用 `KEYS {groupId}:*` 一次清理（保留 knowledge:*）
- 去掉 `commit:{hash}`：同项目审计通常同 commit，不需要隔离维度
- `#startline` 替代 `#sigHash`：行号+fqn 直观稳定，不需要查 codegraph 算 hash

**value**：方法体文本（UTF-8）
**TTL**：无（session 结束 hook 清理，保留 `{groupId}:knowledge:*`）

**获取方式**（一次性预取）：
1. codegraph 构建调用链拓扑（只拿 node_id, fqn, start_line, end_line, file_path）
2. 对链上每个节点的源文件，调 `tools/javaparser/java-method-call-extractor-1.0.0.jar` 抽取方法调用与方法体起止行
3. 基于方法体 start_line + end_line 从源文件读取方法体文本
4. 写入 memurai `{groupId}:method:{fqn}#{startline}`
5. AI 后续只从缓存 GET，INCR count，**禁止直接读文件或查 codegraph 获取源码**

**铁律**：所有源码相关信息只能从缓存拿。codegraph 只用于构建调用拓扑关系，不用于获取方法体。

缓存时机：**调用链构建完成后、AI 分析前**，把链上所有方法体批量预取到 Memurai（见 AR-07）。AI 分析阶段直接 `GET`，不再每次读文件或查 codegraph。

### method_cache count key（访问计数）

**格式**：
```
{groupId}:method:{fqn}#{startline}:count
```

**value**：int（通过 memurai INCR 原子递增）
**TTL**：无（跟随主 key，session 结束 hook 清理）

**使用流程**：
1. AI 需要方法体 → `GET {groupId}:method:{fqn}#{startline}`
2. 拿到方法体后 → `INCR {groupId}:method:{fqn}#{startline}:count`
3. 或 memurai_client 封装 `get_and_count(key)` 一步完成

**统计用途**：
- 高 count = 被频繁访问的方法（可能复杂/可疑，值得深入分析）
- AR-13 加载次数追踪可从 count key 直接统计
- load_ratio = sum(count) / chain_method_count

**字段用途与消费者**：

| 元素 | 用途 | 消费者 |
|------|------|--------|
| `:count` 后缀 | 区分计数 key 与方法体 key | memurai INCR 原子递增 |
| value: int | 该方法被加载次数 | load_counter 写入 loads.db |
| INCR 原子操作 | 并发安全计数 | method_body_loader 调 get_and_count |

### errors:log key（犯错记录）

**格式**：
```
{groupId}:errors:log
```

**value**：JSON array
```json
[
  {
    "position": "fqn#startline — 犯错位置",
    "reason": "string — 犯错原因",
    "count": "int — 该位置犯错次数（重复犯错 INCR）",
    "last_seen": "ISO8601 — 最后一次犯错时间"
  }
]
```

**TTL**：无（session 结束 hook 清理，但合并到 knowledge 后可跨轮次保留）

**用途**：
- 统计执行过程中哪些方法/位置犯错最多
- 分析常见犯错原因（如假阳、漏报、误判）
- session 结束时合并高频错误到 `{groupId}:knowledge:errors` 供下次审计参考

**字段用途与消费者**：

| 字段 | 用途 | 消费者 |
|------|------|--------|
| position | 犯错位置（fqn#startline） | knowledge 合并去重键 |
| reason | 犯错原因 | 误报模式沉淀 |
| count | 重复犯错次数（INCR） | 高频错误优先合并 |
| last_seen | 最后犯错时间 | 新鲜度判定 |

### auth_class_cache key（Memurai）

```
key:   {groupId}:auth:class:{fqn}
value: 鉴权类 FQN + 类型清单（Filter / Interceptor / 注解）
TTL:   跨轮次保留（无 TTL，或与 knowledge 同寿）
```

**字段用途与消费者**：

| 元素 | 用途 | 消费者 |
|------|------|--------|
| `auth:class:` 段 | 标记鉴权类缓存命名空间 | auth_class_cacher 写 / Phase 3 读 |
| value: 鉴权类 FQN | 命中的 Filter/Interceptor/注解 | 链上是否有鉴权上下文判定 |
| value: 类型清单 | 区分鉴权机制类型 | sink 是否被鉴权覆盖判定 |
| 跨轮 TTL | 项目级鉴权稳定，可跨轮复用 | Phase 0 清理 hook 保留 |

---

## Phase 3

> 分析顺序：先搞清楚"门"（配置+鉴权），再分析"内部"（调用链）。3a 配置 → 3b 鉴权 → 3c 调用链。鉴权有漏洞会影响所有调用链的风险评估，故鉴权分析先于调用链。

### config_analysis.json（AI 配置分析报告）

> Phase 3a 产出。环境配置导出 + AI 分析。所有环境配置（filter/nginx/svc/建表语句）**先从环境导出到本地** `loop_audit/env_export/`，再在本地分析，与代码对比。

```json
{
  "db_schema": [
    {
      "table_name": "string (必填) — 表名",
      "columns": [
        {"name": "string (必填)", "type": "string (必填)", "is_sensitive": "bool (可选，缺省 false)"}
      ]
    }
  ],
  "nginx_routes": [
    {
      "location": "string (必填) — nginx location 匹配规则",
      "proxy_pass": "string (必填) — 后端转发目标"
    }
  ],
  "jvm_args": "object (必填) — JVM 启动参数（键值对）",
  "svc_configs": [
    {"...": "服务配置（Spring Cloud Gateway / Dubbo 等），各框架自有字段"}
  ],
  "config_mismatches": [
    {
      "type": "string (必填) — 枚举 missing|extra|changed",
      "detail": "string (必填) — 具体差异描述"
    }
  ],
  "exported_at": "string ISO8601 (必填)",
  "source": "string (必填) — 枚举 ssh|local|offline"
}
```

**字段用途与消费者**：

| 字段 | 类型 | 用途 | 消费者 |
|------|------|------|--------|
| db_schema | array | 数据库表结构（从环境 DDL 或代码 JPA/Mapper 推断） | 调用链分析（SQL 注入需要表名） |
| db_schema[].table_name | string | 表名 | SQL 注入 PoC 构造 |
| db_schema[].columns | array | 列定义 [{name, type, is_sensitive}] | 判断敏感字段 |
| nginx_routes | array | nginx 路由配置（从环境导出） | 确认外部可达端点 |
| nginx_routes[].location | string | nginx location 匹配规则 | 路径遍历/绕过分析 |
| nginx_routes[].proxy_pass | string | 后端转发目标 | SSRF 分析 |
| jvm_args | object | JVM 启动参数 | 配置风险（如 -Dspring.config.location） |
| svc_configs | array | 服务配置（Spring Cloud Gateway / Dubbo 等） | 动态路由分析 |
| config_mismatches | array | 代码配置 vs 环境实际配置的差异 | 配置风险标注 |
| config_mismatches[].type | string | 差异类型（missing/extra/changed） | 风险评级 |
| config_mismatches[].detail | string | 具体差异描述 | 人工复核 |
| exported_at | string ISO8601 | 导出时间 | 时效性判断 |
| source | string | 导出来源（ssh/local/offline） | 降级判断 |

### auth_analysis.json（AI 认证鉴权分析报告）

> Phase 3b 产出。**先于调用链分析**，鉴权是第一道防线。结合环境实际进程加载顺序分析 filter 链。

```json
{
  "filter_chain": [
    {
      "fqn": "string (必填) — Filter 类全限定名",
      "order": "int (必填) — 加载顺序（来自环境或 @Order）",
      "path_pattern": "string (必填) — URL 匹配模式",
      "is_custom": "bool (必填) — 是否用户自定义"
    }
  ],
  "interceptors": [
    {"...": "拦截器链，字段同 filter_chain"}
  ],
  "auth_mechanisms": [
    {
      "type": "string (必填) — 枚举 JWT|OAuth|Session|Basic|APIKey",
      "config_fqn": "string (必填) — 配置类 FQN"
    }
  ],
  "bypass_risks": [
    {
      "type": "string (必填) — 枚举 path_normalization|url_encoding|filter_order|annotation_invalidation",
      "affected_endpoints": ["string (必填) — 受影响端点列表"],
      "severity": "string (必填) — 枚举 high|medium|low",
      "root_cause": "string (必填) — 根因"
    }
  ],
  "access_control": "object (必填) — 访问控制摘要（RBAC/ABAC/无）",
  "analyzed_at": "string ISO8601 (必填)"
}
```

**字段用途与消费者**：

| 字段 | 类型 | 用途 | 消费者 |
|------|------|------|--------|
| filter_chain | array | Filter 链（按实际加载顺序） | 绕过分析 |
| filter_chain[].fqn | string | Filter 类全限定名 | 关联代码 |
| filter_chain[].order | int | 加载顺序（来自环境或 @Order） | 顺序正确性判断 |
| filter_chain[].path_pattern | string | URL 匹配模式 | 路径覆盖判断 |
| filter_chain[].is_custom | bool | 是否用户自定义 | 框架 Filter vs 自定义 |
| interceptors | array | 拦截器链 | 同上 |
| auth_mechanisms | array | 认证机制清单（JWT/OAuth/Session/Basic/API Key） | 认证完整性判断 |
| auth_mechanisms[].type | string | 机制类型 | 关联分析 |
| auth_mechanisms[].config_fqn | string | 配置类 FQN | 关联代码 |
| bypass_risks | array | 识别的鉴权绕过风险 | 调用链分析消费 |
| bypass_risks[].type | string | 绕过类型（path_normalization/url_encoding/filter_order/annotation_invalidation） | PoC 构造 |
| bypass_risks[].affected_endpoints | array | 受影响端点列表 | 调用链风险标注 |
| bypass_risks[].severity | string | high/medium/low | 优先级调整 |
| bypass_risks[].root_cause | string | 根因 | 报告 |
| access_control | object | 访问控制摘要（RBAC/ABAC/无） | IDOR 分析 |
| analyzed_at | string ISO8601 | 分析时间 | 时效性 |

### chain_analysis_report.json（单链报告，原 analysis_report.json）

> Phase 3c 产出。原 `analysis_report.json` 重命名，schema 字段不变。

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

**字段用途与消费者**：

| 字段 | 用途 | 消费者 |
|------|------|--------|
| chain_id | 关联调用链 | poc-monitor 取链 + 报告引用 |
| entry_fqn | 入口方法 | 报告定位 + 入口分析 |
| verdict | vuln\|safe\|unknown 三态结论 | **统计指标**（round_metrics 的 vuln/safe/unknown_chains） |
| loaded_methods | 本次实际加载方法数 | **load_ratio 计算**（loaded_method_count 累加） |
| total_methods | 链上方法体总数 | load_ratio 分母 + chain_method_count |
| load_ratio | 加载效率 | 性能评估 |
| findings[].type | 漏洞类型枚举 | **PoC**（poc-monitor 按 type 派发） |
| findings[].root_cause | 根因描述 | PoC payload 构造依据 |
| findings[].entry_point | HTTP 入口 | PoC 目标定位 |
| findings[].payload | PoC 草稿 | poc-monitor 发送 payload |
| findings[].impact | 业务影响 | 严重性评估 |
| findings[].cvss_4.base_score | 0.0~10.0 | 优先级再排序 |
| findings[].cvss_4.vector | CVSS 4.0 向量 | 复现性分析 |

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

**字段用途与消费者**：

| 字段 | 用途 | 消费者 |
|------|------|--------|
| chain_id | 关联调用链 | round_metrics 统计 |
| poc_status | confirmed\|denied\|inconclusive 三态 | **统计指标**（vuln_chains / safe_chains / unknown_chains） |
| payload_sent | 实际发送 payload | 审计证据 + 复现 |
| response_evidence | 响应证据片段或状态码 | 验证结论佐证 |
| executed_at | 执行时间 | 时序追踪 |

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

**字段用途与消费者**（6 核心指标的消费者明确标注）：

| 字段 | 用途 | 消费者 |
|------|------|--------|
| round | 轮次序号 | self_evolution 轮次对齐 + 趋势追踪 |
| chain_method_count | 链上方法体总数 | **load_ratio 分母** |
| loaded_method_count | 实际加载数（来自 load_counter） | **load_ratio 分子** |
| load_ratio | loaded/chain_method_count | **效率收敛指标**（理想 < 0.3） |
| vuln_chains | verdict=vuln 链数 | **收敛判定 4-AND 之一 + 暴露面** |
| safe_chains | verdict=safe 链数 | **收敛判定**（质量稳定信号） |
| unknown_chains | verdict=unknown 链数 | **收敛判定**（越少越好，理想 0） |
| vuln_exposed_surface | 确认漏洞端点数 | **收敛判定 4-AND 之一**（覆盖率反推） |
| total_chains | 链总数（分母参考） | 不计入 6 核心指标 |
| total_endpoints | 端点总数（分母参考） | 端点覆盖率计算 |

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

**字段用途与消费者**：

| 字段 | 用途 | 消费者 |
|------|------|--------|
| custom_annotations | 项目自定义路由/鉴权注解 FQN | Phase 1 路由发现 + Phase 2 鉴权识别 |
| known_sanitizers | 已确认消毒器（PreparedStatement、HtmlUtils.escape 等） | **L2 剪枝**（消毒器匹配即剪枝） |
| dynamic_route_patterns | 反射/动态路由模式 | Phase 1 动态路由发现 |
| false_positive_patterns[].pattern | 误报特征 | finding 过滤 + L3 跨轮剪枝 |
| false_positive_patterns[].reason | 误报依据 | 审计可解释性 |
| last_updated | 最后更新时间 | 知识新鲜度判定 |

---

## 验收

```powershell
# schema 定义数 ≥ 7
Select-String -Path design-docs/data-schema.md -Pattern "^### "

# 含数据流总览 ASCII 图
Select-String -Path design-docs/data-schema.md -Pattern "数据流总览"
```
