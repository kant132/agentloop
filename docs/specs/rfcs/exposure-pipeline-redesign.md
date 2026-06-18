# RFC-0001: 暴露面搜集与调用链决策流水线重构

**状态**: Draft
**作者**: Sisyphus
**日期**: 2026-06-19
**类型**: 大规模重构（新需求 + 现有脚本拆分）

---

## 1. 摘要 (Summary)

重构暴露面搜集与调用链决策流水线为基于 SOLID 原则的小脚本集合。**确定性的工作全部由 Python 脚本完成**，AI 仅负责分类、判断与代码生成输出。新管线覆盖 8 类暴露面采集 → 数据综合 → 20% 热点决策 → 调用链按优先级分析 → PoC → 报告与经验沉淀。

## 2. 动机 (Motivation)

现状痛点：
1. `attack_surface_scanner.py`（1269 行）、`chain_builder.py`（902 行）— **上帝对象**，违反单一职责
2. AI 介入确定性逻辑（路由过滤、配置读取）— 浪费 token，引入不确定性
3. 缺少：WAF 识别、数据库结构、敏感信息、环境 filter、热点排序、加载次数追踪
4. 评价标准散乱 — 用户要求简化为 `python 方法体加载个数 / 缓存的方法体次数`

目标：
- 每个采集器 ≤200 行，单一职责，可独立测试
- AI 只做需要语义的任务（漏洞判定、PoC 构造、报告撰写）
- 全链路可追踪、可评价、可复现

## 3. 技术设计 (Technical Design)

### 3.1 架构原则

| 原则 | 落地 |
|------|------|
| **单一职责 (SRP)** | 每个 collector 只产出一类资产，一个 output JSON |
| **开闭 (OCP)** | Collector 通过注册表扩展，不改主流程 |
| **里氏替换 (LSP)** | 所有 collector 实现统一 `Collector` 协议 |
| **接口隔离 (ISP)** | 采集/综合/决策三层各自独立协议 |
| **依赖倒置 (DIP)** | 主流程依赖抽象 `Collector`，不依赖具体实现 |
| **AI 边界** | 确定性数据收集 100% 脚本化；AI 仅做分类、漏洞判定、PoC |

### 3.2 目录结构

```
scripts/exposure/                  # 新增包，单一职责小脚本
├── __init__.py
├── contracts.py                   # Collector/Synthesizer/Ranker 抽象协议
├── registry.py                    # collector 注册表 + 发现机制
├── cli.py                         # 统一 CLI 入口: python -m scripts.exposure collect|synthesize|...
│
├── collectors/                    # 8 个采集器，每个一文件
│   ├── __init__.py
│   ├── route_collector.py          # 1. 路由采集（注解、XML、编程式）
│   ├── config_collector.py         # 2. 配置文件（application.yaml/properties/xml）
│   ├── codegraph_collector.py      # 3. codegraph 查询（SQL 语句、启动配置、auth 代码）
│   ├── env_filter_collector.py     # 4. 环境 filter（SSH 到运行环境，每个进程的 filter 链）
│   ├── auth_code_collector.py      # 5. 认证鉴权代码（Filter/Interceptor/注解）
│   ├── waf_collector.py            # 6. WAF 识别（Nginx/ModSecurity/云 WAF 签名）
│   ├── db_schema_collector.py      # 7. 数据库结构（实体类、mapper.xml、JPA）
│   └── sensitive_info_collector.py # 8. 敏感信息（密钥、密码、token、PII 模式）
│
├── synthesizer.py                 # 综合阶段：数据清洗 + 归类汇总
├── hotspot_ranker.py               # 决策阶段：20% 热点代码识别
│
scripts/chain/                      # 已存在，新增模块
├── chain_file_writer.py            # 每端口一个调用链文件（method:nodeid1->method:nodeid2）
├── sink_registry.py                # 预置 sink 点库 + 动态 sink 识别（非 groupId）
├── priority_calculator.py          # 调用链优先级评分
└── auth_class_cacher.py            # 认证鉴权类缓存器
│
scripts/analysis/                   # 新增包
├── __init__.py
├── method_body_loader.py          # 前 5 层全量 + 后续按需加载
├── load_counter.py                 # 方法体加载次数追踪（sqlite/jsonl）
├── chain_report_generator.py       # 单链详尽报告（含 CVSS 4.0）
└── metric_simplifier.py            # 评价简化：loads / cache_count
```

### 3.3 Collector 协议

```python
# contracts.py
from typing import Protocol, runtime_checkable
from pathlib import Path
from dataclasses import dataclass

@dataclass(frozen=True)
class CollectorResult:
    asset_type: str          # "route" | "config" | "auth_code" | ...
    source: str              # 采集来源描述
    items: list[dict]        # 采集到的资产条目
    metadata: dict           # 统计、错误、降级标记

@runtime_checkable
class Collector(Protocol):
    name: str                # "route_collector"
    asset_type: str          # "route"
    def collect(self, ctx: "ExposureContext") -> CollectorResult: ...
    def is_available(self, ctx: "ExposureContext") -> bool: ...
```

### 3.4 输出格式

每个 collector 产出独立 JSON：`{loop_audit_dir}/exposure/{asset_type}.json`

```json
{
  "asset_type": "route",
  "collected_at": "2026-06-19T10:30:00+08:00",
  "source": "ast-grep annotation scan + javaparser-service",
  "stats": {"total": 145, "with_nodes_id": 142, "degraded_md5": 3},
  "items": [...]
}
```

### 3.5 调用链优先级公式

```
priority = base_priority + sink_count + preset_sink_matches * 10

base_priority:
  - 外部参数 == 0      →  -100
  - POST/PUT/DELETE    →  10
  - GET                →  0
```

### 3.6 评价标准（最终简化版）

```
metric = method_body_loads / cached_method_body_count
```

- `method_body_loads`: AI 通过 `method_body_loader` 实际拉取方法体次数
- `cached_method_body_count`: 该链预取到缓存的方法体总数

理想值 `< 0.3`（说明大部分链只需看前 5 层即可判定）。

## 4. 实现顺序 (Implementation Order)

按依赖关系分 5 批，每批先写测试再实现（TDD）：

### 批次 1：基础设施（无依赖）
- `contracts.py` - 协议定义
- `registry.py` - collector 注册表
- `cli.py` - 统一入口骨架

### 批次 2：8 个 Collector（依赖批次 1）
1. route_collector（重构自 attack_surface_scanner）
2. config_collector
3. codegraph_collector
4. env_filter_collector
5. auth_code_collector
6. waf_collector
7. db_schema_collector
8. sensitive_info_collector

### 批次 3：综合与决策（依赖批次 2）
- synthesizer.py
- hotspot_ranker.py

### 批次 4：调用链与 Sink（依赖批次 3）
- chain_file_writer.py
- sink_registry.py
- priority_calculator.py
- auth_class_cacher.py

### 批次 5：分析与报告（依赖批次 4）
- method_body_loader.py
- load_counter.py
- chain_report_generator.py
- metric_simplifier.py

## 5. 测试策略 (Test Strategy)

- **单元测试**: 每个 collector/synthesizer/ranker 独立测试，覆盖率 ≥ 80%
- **集成测试**: 用 `projects/_template/` 的样本代码做端到端
- **回归测试**: 对比新旧 route_collector 输出，确保等价
- **降级测试**: 模拟 codegraph/SSH/javaparser 不可用，验证优雅降级

### 验收标准 (Acceptance Criteria)

| AC ID | 条件 |
|-------|------|
| AC-1 | 8 个 collector 各自独立可运行，单一 JSON 输出 |
| AC-2 | `cli.py collect --all` 一键执行全部采集 |
| AC-3 | synthesizer 输出 `exposure_assets.json`（清洗后） |
| AC-4 | hotspot_ranker 标注前 20% 代码 |
| AC-5 | chain_file_writer 产出每端口一个调用链文件 |
| AC-6 | sink_registry 含预置库 + 动态识别 |
| AC-7 | priority_calculator 按公式评分 |
| AC-8 | method_body_loader 前 5 层全量、后续按需 |
| AC-9 | load_counter 记录每次加载 |
| AC-10 | metric_simplifier 输出 loads/cache_count |
| AC-11 | 所有测试通过，覆盖率 ≥ 80% |

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 现有 cross-agent-50r.py 依赖旧 scanner | 保留旧脚本作为 fallback，新管线并行运行验证后再切换 |
| Windows 中文路径编码 | 所有脚本 `# -*- coding: utf-8 -*-`，路径用 `pathlib.Path` |
| Memurai subprocess 单点 | 沿用现有 `memurai_client.py`，不重写 |
| SSH 环境不可用 | env_filter_collector 标 `source: "offline"`，不阻塞管线 |
| TDD 放慢进度 | 先骨架后填充，每模块最小测试集 |

## 7. 不做 (Non-Goals)

- 不重写 `memurai_client.py`（沿用现有）
- 不重写 `cross-agent-50r.py` 主循环（保留，新模块作为子调用）
- 不删除现有 `attack_surface_scanner.py`（保留作为对照）
- 不实现完整 PoC 执行（仅产出 PoC 触发建议，实际执行由现有 `poc-verify` skill）
