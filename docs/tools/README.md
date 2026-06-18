# 工具知识中枢

本项目依赖 **3 个核心工具** + **2 个辅助工具**。核心工具任一不可用即退出，不做降级（见 AGENTS.md Tool Selection 硬规则）。

## 工具索引

| 工具 | 用途 | 指南路径 | 不可降级 |
|------|------|---------|---------|
| **codegraph** | 类/方法关系查询、调用链构建 | [codegraph-guide.md](./codegraph-guide.md) | 是 |
| **ast-grep** | 危险函数模式匹配（SQL/RCE/SSRF/...） | [ast-grep-guide.md](./ast-grep-guide.md) | 是 |
| **Memurai** | 方法体缓存（Redis 兼容，Windows 原生 CLI） | [memurai-guide.md](./memurai-guide.md) | 是 |
| javaparser-service | 注解富化（AST 元数据补全） | `docs/tools/javaparser-guide.md`（指向 `design-docs/exploration-summary-testclone.md`） | 否 |
| arthas | 运行时动态分析（OGNL/方法监控/反编译） | `skills/arthas-audit/SKILL.md` | 否 |

## 工具选型硬规则（来自 AGENTS.md）

| 任务 | 工具 | 禁用 |
|------|------|------|
| 配置文件取值（xml/yml/properties） | `grep` | codegraph |
| 类/方法关系、调用链 | codegraph SQLite（≤20 LEFT JOINs） | ast-grep |
| 危险函数模式（SQL/RCE/...） | ast-grep | grep |
| 读方法体 | Memurai 缓存 → codegraph 兜底 | 直接 `Read` 整文件 |
| 统计/报告 | Python 脚本 | 临时代码 |

> ⚠️ **不可降级**：3 个核心工具任一缺失 → 审计直接退出（exit code 2），不做降级处理。
> 子 agent **绝不直接调 codegraph** — 方法体在子 agent 启动前已预取到 Memurai。
