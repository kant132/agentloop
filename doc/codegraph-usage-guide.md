# CodeGraph 使用文档

> **版本**: v0.9.9 (2026-06-02)  
> **项目**: [@colbymchenry/codegraph](https://github.com/colbymchenry/codegraph)  
> **测试环境**: Windows PowerShell 5.1 + Python 3.14 + Node.js v24  
> **测试项目**: ruoyi-vue-pro (5561 files, 115105 nodes, 235396 edges, 2366 routes)

---

## 目录

- [1. 概述](#1-概述)
- [2. 架构与数据模型](#2-架构与数据模型)
- [3. CLI 命令详解](#3-cli-命令详解)
  - [3.1 init - 初始化项目索引](#31-init---初始化项目索引)
  - [3.2 uninit - 删除项目索引](#32-uninit---删除项目索引)
  - [3.3 index - 全量索引](#33-index---全量索引)
  - [3.4 sync - 增量同步](#34-sync---增量同步)
  - [3.5 status - 索引状态](#35-status---索引状态)
  - [3.6 query - 符号搜索](#36-query---符号搜索)
  - [3.7 files - 文件树](#37-files---文件树)
  - [3.8 callers - 调用者查询](#38-callers---调用者查询)
  - [3.9 callees - 被调用者查询](#39-callees---被调用者查询)
  - [3.10 impact - 影响分析](#310-impact---影响分析)
  - [3.11 affected - 测试影响分析](#311-affected---测试影响分析)
  - [3.12 serve - MCP 服务器](#312-serve---mcp-服务器)
  - [3.13 unlock - 解锁索引](#313-unlock---解锁索引)
  - [3.14 install - 安装到 Agent](#314-install---安装到-agent)
  - [3.15 uninstall - 从 Agent 卸载](#315-uninstall---从-agent-卸载)
- [4. MCP 工具详解](#4-mcp-工具详解)
  - [4.1 codegraph_search - 符号搜索](#41-codegraph_search---符号搜索)
  - [4.2 codegraph_explore - 深度探索](#42-codegraph_explore---深度探索)
  - [4.3 codegraph_node - 节点详情](#43-codegraph_node---节点详情)
  - [4.4 codegraph_callers - 调用者](#44-codegraph_callers---调用者)
  - [4.5 codegraph_callees - 被调用者](#45-codegraph_callees---被调用者)
  - [4.6 codegraph_impact - 影响分析](#46-codegraph_impact---影响分析)
  - [4.7 codegraph_status - 状态检查](#47-codegraph_status---状态检查)
  - [4.8 codegraph_files - 文件树](#48-codegraph_files---文件树)
- [5. SQLite 直连访问](#5-sqlite-直连访问)
- [6. 性能对比](#6-性能对比)
- [7. Route 节点专项分析](#7-route-节点专项分析)
- [8. 最佳实践](#8-最佳实践)

---

## 1. 概述

CodeGraph 是一个**代码知识图谱引擎**，为 AI Agent 提供预索引的代码智能能力。它通过 tree-sitter 解析源代码 AST，提取符号（函数、类、方法等）和关系（调用、导入、继承等），存储到本地 SQLite 数据库，并提供 FTS5 全文搜索和图遍历能力。

### 核心价值

- **减少 Token 消耗**: 平均节省 16% 成本，减少 47% token
- **减少工具调用**: 平均减少 58% 工具调用
- **加速响应**: 平均提速 22%
- **100% 本地**: 无外部 API，无数据泄露，SQLite 单文件存储

### 支持语言 (20+)

TypeScript, JavaScript, Python, Go, Rust, Java, C#, PHP, Ruby, C, C++, Objective-C, Swift, Kotlin, Dart, Lua, Luau, Svelte, Liquid, Pascal/Delphi

### 框架感知路由

自动识别 14 个 Web 框架的路由定义：

| 框架 | 识别模式 |
|------|---------|
| Django | `path()`, `re_path()`, `url()`, `include()` |
| Flask | `@app.route()`, blueprint routes |
| FastAPI | `@app.get()`, `@router.post()` |
| Express | `app.get()`, `router.post()` |
| NestJS | `@Controller` + `@Get/@Post/...`, GraphQL `@Resolver` |
| Laravel | `Route::get()`, `Route::resource()` |
| Rails | `get '/x', to: 'users#index'` |
| **Spring** | **`@GetMapping`, `@PostMapping`, `@RequestMapping`** |
| Gin/chi | `r.GET()`, `router.HandleFunc()` |
| Axum/actix | `.route("/x", get(handler))` |
| ASP.NET | `[HttpGet("/x")]` |
| Vapor | `app.get("x", use: handler)` |
| React Router | Route component nodes |
| SvelteKit | Route component nodes |

---

## 2. 架构与数据模型

### 2.1 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Agent (Claude/Cursor)                  │
│                                                              │
│  "How does authentication work?"                            │
│       ↓                                                      │
│  calls MCP tools (codegraph_explore, codegraph_search)      │
└──────────────────────────┬──────────────────────────────────┘
                           │ JSON-RPC 2.0 (stdio)
                           ↓
┌─────────────────────────────────────────────────────────────┐
│              CodeGraph MCP Server (Node.js)                  │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ FTS5 Search  │  │ Graph Traver │  │ Context Build│      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         └─────────────────┴─────────────────┘               │
│                           │                                  │
│                    SQLite + FTS5                             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ↓
┌─────────────────────────────────────────────────────────────┐
│         .codegraph/codegraph.db (SQLite WAL)                 │
│                                                              │
│  nodes (symbols)  │  edges (relations)  │  files (tracked)  │
│  nodes_fts (FTS5) │  project_metadata   │  schema_versions  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 数据库 Schema

```sql
-- 节点表：存储所有代码符号
CREATE TABLE nodes (
  id TEXT PRIMARY KEY,           -- 例: "method:abc123..."
  kind TEXT NOT NULL,            -- function|method|class|interface|type|variable|field|enum|route|...
  name TEXT NOT NULL,            -- 符号名
  qualified_name TEXT NOT NULL,  -- 全限定名: ClassName::methodName
  file_path TEXT NOT NULL,       -- 文件路径
  language TEXT NOT NULL,        -- 编程语言
  start_line INTEGER,            -- 起始行号
  end_line INTEGER,              -- 结束行号
  start_column INTEGER,          -- 起始列号
  end_column INTEGER,            -- 结束列号
  docstring TEXT,                -- 文档字符串
  signature TEXT,                -- 函数签名
  visibility TEXT,               -- public|private|protected
  is_exported INTEGER,           -- 是否导出
  is_async INTEGER,              -- 是否异步
  is_static INTEGER,             -- 是否静态
  is_abstract INTEGER,           -- 是否抽象
  decorators TEXT,               -- 装饰器/注解 (JSON array)
  type_parameters TEXT,          -- 泛型参数 (JSON array)
  updated_at INTEGER             -- 更新时间戳
);

-- 边表：存储符号间关系
CREATE TABLE edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,          -- FK -> nodes.id
  target TEXT NOT NULL,          -- FK -> nodes.id
  kind TEXT NOT NULL,            -- calls|imports|extends|implements|references|contains|...
  metadata TEXT,                 -- JSON 元数据
  line INTEGER,                  -- 关系发生行号
  col INTEGER,                   -- 关系发生列号
  provenance TEXT                -- 'heuristic' 表示合成边
);

-- 文件表：跟踪已索引文件
CREATE TABLE files (
  path TEXT PRIMARY KEY,
  content_hash TEXT,
  language TEXT,
  size INTEGER,
  modified_at INTEGER,
  indexed_at INTEGER,
  node_count INTEGER,
  errors TEXT
);

-- FTS5 全文搜索虚拟表
CREATE VIRTUAL TABLE nodes_fts USING fts5(
  id, name, qualified_name, docstring, signature,
  content='nodes', content_rowid='rowid'
);
```

### 2.3 节点类型分布 (ruoyi-vue-pro 实测)

| kind | 数量 | 说明 |
|------|------|------|
| import | 52,969 | 导入语句 |
| field | 22,156 | 字段/属性 |
| method | 19,078 | 方法 |
| file | 5,537 | 源文件 |
| namespace | 5,454 | 包/命名空间 |
| class | 4,225 | 类 |
| **route** | **2,366** | **路由节点** |
| enum_member | 1,186 | 枚举值 |
| interface | 1,061 | 接口 |
| constant | 774 | 常量 |
| enum | 256 | 枚举类 |
| function | 41 | 顶层函数 |
| component | 1 | 组件 |
| variable | 1 | 变量 |

### 2.4 边类型分布 (ruoyi-vue-pro 实测)

| kind | 数量 | 说明 |
|------|------|------|
| contains | 106,466 | 结构层次 (class→method, file→class) |
| imports | 52,964 | 导入关系 |
| calls | 45,148 | 调用关系 |
| references | 24,570 | 引用关系 (**route→method 用此边**) |
| instantiates | 4,477 | 实例化 |
| extends | 1,160 | 继承 |
| implements | 611 | 接口实现 |

---

## 3. CLI 命令详解

### 3.1 init - 初始化项目索引

**用途**: 在项目中创建 `.codegraph/` 目录并构建初始索引。

**语法**:
```bash
codegraph init [options] [path]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `[path]` | 项目路径 | 当前目录 |
| `-i, --index` | 已废弃 (v0.9.8+ 默认执行索引) | - |
| `-v, --verbose` | 显示详细 worker 生命周期和内存信息 | false |

**示例**:
```bash
# 初始化当前项目
codegraph init

# 初始化指定项目
codegraph init /path/to/project

# 详细模式
codegraph init -v /path/to/project
```

**输出**:
```
Initializing CodeGraph in /path/to/project...
Indexing 5561 files...
  Extracted 115105 nodes, 235396 edges
  Indexed in 45.2s
Done. CodeGraph is ready.
```

**注意事项**:
- v0.9.8+ 版本默认执行索引，无需 `-i` flag
- 索引过程会创建 `.codegraph/codegraph.db` (SQLite 数据库)
- 大项目 (10k+ files) 索引可能需要 1-2 分钟
- 索引完成后自动启动文件监听器 (watcher)

---

### 3.2 uninit - 删除项目索引

**用途**: 删除项目的 `.codegraph/` 目录。

**语法**:
```bash
codegraph uninit [options] [path]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `[path]` | 项目路径 | 当前目录 |
| `-f, --force` | 跳过确认提示 | false |

**示例**:
```bash
# 删除当前项目索引
codegraph uninit

# 强制删除 (无确认)
codegraph uninit -f /path/to/project
```

---

### 3.3 index - 全量索引

**用途**: 重新构建完整索引 (用于强制重建或修复损坏的索引)。

**语法**:
```bash
codegraph index [options] [path]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `[path]` | 项目路径 | 当前目录 |
| `-f, --force` | 强制全量重建 (忽略增量) | false |
| `-q, --quiet` | 静默模式 (减少输出) | false |
| `-v, --verbose` | 详细模式 | false |

**示例**:
```bash
# 增量索引 (默认)
codegraph index

# 强制全量重建
codegraph index -f

# 静默模式 (用于脚本)
codegraph index -q
```

**与 sync 的区别**:
- `index`: 全量扫描所有文件，重建整个图谱
- `sync`: 只处理自上次索引以来变更的文件

---

### 3.4 sync - 增量同步

**用途**: 同步自上次索引以来的文件变更。

**语法**:
```bash
codegraph sync [options] [path]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `[path]` | 项目路径 | 当前目录 |
| `-q, --quiet` | 静默模式 (用于 git hooks) | false |

**示例**:
```bash
# 增量同步
codegraph sync

# 在 git hook 中使用
codegraph sync -q
```

**自动同步机制**:
- MCP 服务器启动时自动运行 watcher
- 文件变更后 2 秒去抖 (debounce) 触发同步
- 可通过 `CODEGRAPH_WATCH_DEBOUNCE_MS` 环境变量调整

---

### 3.5 status - 索引状态

**用途**: 显示索引统计信息和健康状态。

**语法**:
```bash
codegraph status [options] [path]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `[path]` | 项目路径 | 当前目录 |
| `-j, --json` | JSON 格式输出 | false |

**示例**:
```bash
# 人类可读格式
codegraph status

# JSON 格式 (用于脚本)
codegraph status -j
```

**输出示例 (JSON)**:
```json
{
  "initialized": true,
  "projectPath": "D:\\code\\ruoyi-vue-pro",
  "fileCount": 5561,
  "nodeCount": 115105,
  "edgeCount": 235396,
  "dbSizeBytes": 305659904,
  "backend": "node-sqlite",
  "journalMode": "wal",
  "nodesByKind": {
    "class": 4225,
    "method": 19078,
    "field": 22156,
    "interface": 1061,
    "enum": 256,
    "function": 41,
    "import": 52969,
    "namespace": 5454,
    "route": 2366,
    "file": 5537,
    "component": 1,
    "constant": 774,
    "enum_member": 1186,
    "variable": 1
  },
  "languages": ["java", "python", "typescript", "vue", "xml", "yaml"],
  "pendingChanges": {
    "added": 0,
    "modified": 0,
    "removed": 0
  }
}
```

**关键字段**:
- `initialized`: 是否已初始化
- `nodesByKind.route`: 路由节点数量
- `pendingChanges`: 待同步的变更 (非 0 表示索引过期)

---

### 3.6 query - 符号搜索

**用途**: 搜索代码符号 (函数、类、方法等)。

**语法**:
```bash
codegraph query [options] <search>
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `<search>` | 搜索关键词 (必填) | - |
| `-p, --path <path>` | 项目路径 | 当前目录 |
| `-l, --limit <number>` | 最大结果数 | 10 |
| `-k, --kind <kind>` | 按节点类型过滤 | 无 |
| `-j, --json` | JSON 格式输出 | false |

**支持的 kind 值**:
`function`, `method`, `class`, `interface`, `type`, `variable`, `field`, `enum`, `import`, `namespace`, `route`, `component`, `constant`, `enum_member`, `file`

**示例**:
```bash
# 搜索类
codegraph query "AuthController" -k class -j

# 搜索方法
codegraph query "login" -k method -l 5 -j

# 搜索路由
codegraph query "GET /system/auth" -k route -j

# 搜索接口
codegraph query "AuthService" -k interface -j
```

**输出示例**:
```json
[
  {
    "node": {
      "id": "method:28b2d921411014fad0a9289a78f433b4",
      "kind": "method",
      "name": "login",
      "qualifiedName": "cn.iocoder.yudao.module.system.controller.admin.auth::AuthController::login",
      "filePath": "yudao-module-system/src/main/java/cn/iocoder/yudao/module/system/controller/admin/auth/AuthController.java",
      "language": "java",
      "startLine": 66,
      "endLine": 71,
      "startColumn": 4,
      "endColumn": 5,
      "signature": "CommonResult<AuthLoginRespVO> (@RequestBody @Valid AuthLoginReqVO reqVO)",
      "visibility": "public",
      "isExported": false,
      "isAsync": false,
      "isStatic": false,
      "isAbstract": false,
      "updatedAt": 1780544571644
    },
    "score": 102.99582375597929
  }
]
```

**搜索机制**:
- 使用 FTS5 全文搜索 (BM25 排序)
- `score` 字段表示相关性 (越高越好)
- 支持模糊匹配和部分匹配

---

### 3.7 files - 文件树

**用途**: 显示项目文件结构。

**语法**:
```bash
codegraph files [options]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-p, --path <path>` | 项目路径 | 当前目录 |
| `--filter <dir>` | 按目录过滤 | 无 |
| `--pattern <glob>` | 按 glob 模式过滤 | 无 |
| `--format <format>` | 输出格式: `tree`/`flat`/`grouped` | tree |
| `--max-depth <number>` | 最大目录深度 | 无限制 |
| `--no-metadata` | 隐藏语言和符号数 | false |
| `-j, --json` | JSON 格式输出 | false |

**示例**:
```bash
# 树形格式
codegraph files

# 扁平格式
codegraph files --format flat

# 按目录过滤
codegraph files --filter "src/main/java"

# 按 glob 过滤
codegraph files --pattern "**/*Controller.java"

# JSON 格式
codegraph files -j --format flat
```

**输出示例 (JSON)**:
```json
[
  {
    "path": "yudao-module-system/src/main/java/cn/iocoder/yudao/module/system/controller/admin/auth/AuthController.java",
    "language": "java",
    "nodeCount": 67,
    "size": 7724
  }
]
```

---

### 3.8 callers - 调用者查询

**用途**: 查找调用指定符号的所有位置。

**语法**:
```bash
codegraph callers [options] <symbol>
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `<symbol>` | 目标符号名 (必填) | - |
| `-p, --path <path>` | 项目路径 | 当前目录 |
| `-l, --limit <number>` | 最大结果数 | 20 |
| `-j, --json` | JSON 格式输出 | false |

**示例**:
```bash
# 查找 login 方法的调用者
codegraph callers "login" -j

# 限制结果数
codegraph callers "createUser" -l 10 -j
```

**输出示例**:
```json
{
  "symbol": "login",
  "callers": [
    {
      "name": "login",
      "kind": "method",
      "filePath": "yudao-module-member/src/main/java/cn/iocoder/yudao/module/member/service/auth/MemberAuthService.java",
      "startLine": 22
    },
    {
      "name": "testLogin_success",
      "kind": "method",
      "filePath": "yudao-module-system/src/test/java/cn/iocoder/yudao/module/system/service/auth/AdminAuthServiceImplTest.java",
      "startLine": 150
    }
  ]
}
```

**注意事项**:
- 对于重载方法，会返回所有同名方法的调用者
- Controller 方法通常没有直接调用者 (由框架反射调用)
- 使用 `qualifiedName` 可以精确指定目标方法

---

### 3.9 callees - 被调用者查询

**用途**: 查找指定符号调用的所有其他符号。

**语法**:
```bash
codegraph callees [options] <symbol>
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `<symbol>` | 目标符号名 (必填) | - |
| `-p, --path <path>` | 项目路径 | 当前目录 |
| `-l, --limit <number>` | 最大结果数 | 20 |
| `-j, --json` | JSON 格式输出 | false |

**示例**:
```bash
# 查找 login 方法调用的其他方法
codegraph callees "login" -j
```

**输出示例**:
```json
{
  "symbol": "login",
  "callees": [
    {
      "name": "success",
      "kind": "method",
      "filePath": "yudao-framework/yudao-common/src/main/java/cn/iocoder/yudao/framework/common/pojo/CommonResult.java",
      "startLine": 72
    },
    {
      "name": "login",
      "kind": "method",
      "filePath": "yudao-module-system/src/main/java/cn/iocoder/yudao/module/system/service/auth/AdminAuthService.java",
      "startLine": 32
    }
  ]
}
```

---

### 3.10 impact - 影响分析

**用途**: 分析修改指定符号会影响的代码范围。

**语法**:
```bash
codegraph impact [options] <symbol>
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `<symbol>` | 目标符号名 (必填) | - |
| `-p, --path <path>` | 项目路径 | 当前目录 |
| `-d, --depth <number>` | 遍历深度 | 2 |
| `-j, --json` | JSON 格式输出 | false |

**示例**:
```bash
# 分析修改 AuthController 的影响
codegraph impact "AuthController" -j

# 深度为 1 (只分析直接依赖)
codegraph impact "AuthController" -d 1 -j
```

**输出示例**:
```json
{
  "symbol": "AuthController",
  "depth": 1,
  "nodeCount": 29,
  "edgeCount": 28,
  "affected": [
    {
      "name": "AuthController",
      "kind": "class",
      "filePath": "yudao-module-system/src/main/java/cn/iocoder/yudao/module/system/controller/admin/auth/AuthController.java",
      "startLine": 43
    },
    {
      "name": "authService",
      "kind": "field",
      "filePath": "yudao-module-system/src/main/java/cn/iocoder/yudao/module/system/controller/admin/auth/AuthController.java",
      "startLine": 51
    },
    {
      "name": "login",
      "kind": "method",
      "filePath": "yudao-module-system/src/main/java/cn/iocoder/yudao/module/system/controller/admin/auth/AuthController.java",
      "startLine": 66
    },
    {
      "name": "POST /system/auth/login",
      "kind": "route",
      "filePath": "yudao-module-system/src/main/java/cn/iocoder/yudao/module/system/controller/admin/auth/AuthController.java",
      "startLine": 66
    }
  ]
}
```

**关键发现**:
- impact 返回的 `affected` 列表包含 **route 节点**
- 可用于获取某个 Controller 的所有路由

---

### 3.11 affected - 测试影响分析

**用途**: 查找受源文件变更影响的测试文件。

**语法**:
```bash
codegraph affected [options] [files...]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `[files...]` | 变更的源文件列表 | - |
| `-p, --path <path>` | 项目路径 | 当前目录 |
| `--stdin` | 从 stdin 读取文件列表 | false |
| `-d, --depth <number>` | 依赖遍历深度 | 5 |
| `-f, --filter <glob>` | 测试文件 glob 过滤 | 自动检测 |
| `-j, --json` | JSON 格式输出 | false |
| `-q, --quiet` | 只输出文件路径 | false |

**示例**:
```bash
# 直接指定文件
codegraph affected src/utils.ts src/api.ts

# 从 git diff 读取
git diff --name-only | codegraph affected --stdin

# 自定义测试文件模式
codegraph affected src/auth.ts --filter "e2e/*.spec.ts"

# CI/hook 示例
#!/usr/bin/env bash
AFFECTED=$(git diff --name-only HEAD | codegraph affected --stdin --quiet)
if [ -n "$AFFECTED" ]; then
  npx vitest run $AFFECTED
fi
```

**输出示例**:
```json
{
  "changedFiles": [
    "yudao-module-system/src/main/java/cn/iocoder/yudao/module/system/controller/admin/auth/AuthController.java"
  ],
  "affectedTests": [],
  "totalDependentsTraversed": 0
}
```

---

### 3.12 serve - MCP 服务器

**用途**: 启动 MCP (Model Context Protocol) 服务器，供 AI Agent 使用。

**语法**:
```bash
codegraph serve [options]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-p, --path <path>` | 项目路径 (MCP 模式可选) | - |
| `--mcp` | 以 MCP stdio 服务器运行 | false |
| `--no-watch` | 禁用文件监听 (WSL2 慢文件系统用) | false |

**示例**:
```bash
# 启动 MCP 服务器
codegraph serve --mcp

# 指定项目路径
codegraph serve --mcp --path /path/to/project

# 禁用文件监听
codegraph serve --mcp --no-watch
```

**MCP 工具列表**:
启动后暴露 8 个 MCP 工具 (详见第 4 章):
- `codegraph_search`
- `codegraph_explore`
- `codegraph_node`
- `codegraph_callers`
- `codegraph_callees`
- `codegraph_impact`
- `codegraph_status`
- `codegraph_files`

**环境变量**:

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CODEGRAPH_NO_DAEMON` | 禁用共享守护进程 | 未设置 (启用) |
| `CODEGRAPH_DAEMON_IDLE_TIMEOUT_MS` | 守护进程空闲超时 | 300000 (5 分钟) |
| `CODEGRAPH_WATCH_DEBOUNCE_MS` | 文件监听去抖 | 2000 |
| `CODEGRAPH_MCP_TOOLS` | 选择性暴露工具 | 全部 |
| `CODEGRAPH_PPID_POLL_MS` | 父进程检查间隔 | 未设置 |
| `CODEGRAPH_ADAPTIVE_EXPLORE` | 自适应 explore 响应大小 | 1 (启用) |

---

### 3.13 unlock - 解锁索引

**用途**: 删除阻塞索引的过期锁文件。

**语法**:
```bash
codegraph unlock [path]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `[path]` | 项目路径 | 当前目录 |

**使用场景**:
- 索引进程异常退出 (OOM, kill -9)
- 多个进程同时尝试索引
- 锁文件残留导致无法重新索引

---

### 3.14 install - 安装到 Agent

**用途**: 将 CodeGraph MCP 服务器安装到 AI Agent 配置文件。

**语法**:
```bash
codegraph install [options]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-t, --target <ids>` | 目标 agent: `auto`/`all`/`none` 或逗号分隔 | prompt |
| `-l, --location <where>` | 安装位置: `global` 或 `local` | prompt |
| `-y, --yes` | 非交互模式 | false |
| `--no-permissions` | 跳过 Claude auto-allow 列表 | false |
| `--print-config <id>` | 打印配置片段 (不写文件) | - |

**支持的 Agent**:
- Claude Code
- Cursor
- Codex CLI
- opencode
- Hermes Agent
- Gemini CLI
- Antigravity IDE
- Kiro

**示例**:
```bash
# 交互式安装
codegraph install

# 非交互模式
codegraph install --yes

# 指定 agent
codegraph install --target=cursor,claude --yes

# 项目本地安装
codegraph install --target=auto --location=local

# 打印配置 (不写文件)
codegraph install --print-config codex
```

---

### 3.15 uninstall - 从 Agent 卸载

**用途**: 从 AI Agent 配置文件中移除 CodeGraph。

**语法**:
```bash
codegraph uninstall [options]
```

**参数**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-t, --target <ids>` | 目标 agent: `all` 或逗号分隔 | all |
| `-l, --location <where>` | 卸载位置: `global` 或 `local` | prompt |
| `-y, --yes` | 非交互模式 | false |

**示例**:
```bash
# 卸载所有 agent
codegraph uninstall --yes

# 卸载指定 agent
codegraph uninstall --target=cursor --yes
```

---

## 4. MCP 工具详解

MCP (Model Context Protocol) 工具通过 stdio 传输协议提供给 AI Agent 使用。需要先启动 MCP 服务器:

```bash
codegraph serve --mcp
```

### 4.1 codegraph_search - 符号搜索

**用途**: 快速符号搜索，仅返回位置信息 (不含源码)。

**输入 Schema**:
```json
{
  "query": "string (required) - 搜索关键词",
  "kind": "string (optional) - 节点类型过滤",
  "limit": "number (optional, default 10) - 最大结果数",
  "projectPath": "string (optional) - 项目路径"
}
```

**输出格式**:
```json
[
  {
    "node": {
      "id": "method:abc123",
      "kind": "method",
      "name": "login",
      "qualifiedName": "AuthController::login",
      "filePath": "src/AuthController.java",
      "startLine": 66,
      "endLine": 71
    },
    "score": 102.99
  }
]
```

**使用场景**:
- 快速定位符号位置
- 获取符号的 `id` 用于后续查询
- 不需要源码内容时

**与 codegraph_explore 的区别**:
- `search`: 只返回位置，快速
- `explore`: 返回完整源码和关系，深度分析

---

### 4.2 codegraph_explore - 深度探索

**用途**: **主工具** - 深度探索代码架构和流程，返回完整源码和关系。

**输入 Schema**:
```json
{
  "query": "string (required) - 自然语言问题或符号列表",
  "maxFiles": "number (optional, default 12) - 最大文件数",
  "projectPath": "string (optional) - 项目路径"
}
```

**输出格式** (Markdown):
```markdown
## Exploration: AuthController login logout

Found 206 symbols across 43 files.

### Blast radius — what depends on these
- `logout` (AuthController.java:73) — 1 caller; ⚠️ no covering tests found
- `login` (AuthController.java:66) — 1 caller; ⚠️ no covering tests found

### Relationships
**calls:**
- logout → obtainAuthorization
- login → login0
- ... and 178 more

### Source Code
[完整源码，按文件分组]
```

**关键特性**:
1. **自然语言查询**: 支持 "How does authentication work?" 类型的问题
2. **Blast radius**: 自动显示依赖关系和测试覆盖
3. **完整源码**: 返回相关符号的完整源码 (含行号)
4. **关系图**: 显示 calls/extends/implements 等关系

**性能**:
- 实测: 0.5-1.4 秒
- 返回大小: 16-22 KB

**使用场景**:
- 架构理解: "How does the caching system work?"
- 流程追踪: "How does POST /system/auth/login reach the database?"
- 跨文件分析: "How are errors handled and propagated?"

**v0.9.9 变更**:
- 移除了 `codegraph_trace` 和 `codegraph_context` (功能合并到 explore)
- explore 现在包含 blast radius 信息
- 自适应响应大小 (按答案而非文件数调整)

---

### 4.3 codegraph_node - 节点详情

**用途**: 获取单个符号的完整源码和调用链 (trail)。

**输入 Schema**:
```json
{
  "symbol": "string (required) - 符号名",
  "includeCode": "boolean (optional, default false) - 是否包含源码",
  "file": "string (optional) - 按文件路径消歧",
  "line": "number (optional) - 按行号消歧",
  "projectPath": "string (optional) - 项目路径"
}
```

**输出格式** (Markdown):
```markdown
## login (method)

**Location:** AuthController.java:66
**Signature:** `CommonResult<AuthLoginRespVO> (@RequestBody @Valid AuthLoginReqVO reqVO)`

```java
66    @PostMapping("/login")
67    @PermitAll
68    @Operation(summary = "使用账号密码登录")
69    public CommonResult<AuthLoginRespVO> login(@RequestBody @Valid AuthLoginReqVO reqVO) {
70        return success(authService.login(reqVO));
71    }
```

### Trail
**Calls →** success, login (AdminAuthService:32)
**Called by ←** POST /system/auth/login
```

**关键特性**:
1. **重载处理**: 模糊名称返回所有匹配定义
2. **Trail**: 显示调用链 (calls + called by)
3. **Route 链接**: Trail 中包含 route 节点 (如 `POST /system/auth/login`)

**性能**:
- 实测: **0.00 秒** (守护进程缓存)
- 返回大小: 1-10 KB

**使用场景**:
- 获取方法完整源码
- 查看调用链 (谁调用了它，它调用了谁)
- 验证 route ↔ method 映射

**消歧示例**:
```json
// 模糊查询 (返回所有 login 方法)
{ "symbol": "login", "includeCode": true }

// 精确查询 (按文件)
{ "symbol": "login", "includeCode": true, "file": "AuthController.java" }

// 精确查询 (按行号)
{ "symbol": "login", "includeCode": true, "file": "AuthController.java", "line": 66 }
```

---

### 4.4 codegraph_callers - 调用者

**用途**: 查找调用指定符号的所有位置。

**输入 Schema**:
```json
{
  "symbol": "string (required) - 目标符号名",
  "limit": "number (optional, default 20) - 最大结果数",
  "projectPath": "string (optional) - 项目路径"
}
```

**输出格式**:
```json
{
  "symbol": "login",
  "callers": [
    {
      "name": "login",
      "kind": "method",
      "filePath": "MemberAuthService.java",
      "startLine": 22
    }
  ]
}
```

**与 CLI 的区别**:
- MCP: 返回结构化 JSON
- CLI: 需要 `-j` flag 才返回 JSON

---

### 4.5 codegraph_callees - 被调用者

**用途**: 查找指定符号调用的所有其他符号。

**输入 Schema**:
```json
{
  "symbol": "string (required) - 目标符号名",
  "limit": "number (optional, default 20) - 最大结果数",
  "projectPath": "string (optional) - 项目路径"
}
```

**输出格式**:
```json
{
  "symbol": "login",
  "callees": [
    {
      "name": "success",
      "kind": "method",
      "filePath": "CommonResult.java",
      "startLine": 72
    }
  ]
}
```

---

### 4.6 codegraph_impact - 影响分析

**用途**: 分析修改指定符号会影响的代码范围。

**输入 Schema**:
```json
{
  "symbol": "string (required) - 目标符号名",
  "depth": "number (optional, default 2) - 遍历深度",
  "projectPath": "string (optional) - 项目路径"
}
```

**输出格式**:
```json
{
  "symbol": "AuthController",
  "depth": 1,
  "nodeCount": 29,
  "edgeCount": 28,
  "affected": [
    {
      "name": "login",
      "kind": "method",
      "filePath": "AuthController.java",
      "startLine": 66
    },
    {
      "name": "POST /system/auth/login",
      "kind": "route",
      "filePath": "AuthController.java",
      "startLine": 66
    }
  ]
}
```

---

### 4.7 codegraph_status - 状态检查

**用途**: 检查索引健康状态。

**输入 Schema**:
```json
{
  "projectPath": "string (optional) - 项目路径"
}
```

**输出格式**: 同 CLI `status -j`

---

### 4.8 codegraph_files - 文件树

**用途**: 显示项目文件结构。

**输入 Schema**:
```json
{
  "path": "string (optional) - 目录路径",
  "pattern": "string (optional) - glob 模式",
  "format": "string (optional) - tree|flat|grouped",
  "includeMetadata": "boolean (optional, default true)",
  "maxDepth": "number (optional)",
  "projectPath": "string (optional) - 项目路径"
}
```

**输出格式**: 同 CLI `files -j`

---

## 5. SQLite 直连访问

对于高频查询场景，可以直接访问 SQLite 数据库，性能比 CLI/MCP 快 **1000-5000 倍**。

### 5.1 Python 示例

```python
import sqlite3

DB_PATH = r"D:\code\ruoyi-vue-pro\.codegraph\codegraph.db"
db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

# 1. 符号搜索 (FTS5)
def search_nodes(query, kinds=None, limit=10):
    sql = """
        SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path,
               n.start_line, n.end_line, n.signature,
               bm25(nodes_fts) as score
        FROM nodes_fts
        JOIN nodes n ON n.rowid = nodes_fts.rowid
        WHERE nodes_fts MATCH ?
    """
    params = [query]
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        sql += f" AND n.kind IN ({placeholders})"
        params.extend(kinds)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    return db.execute(sql, params).fetchall()

# 2. 调用者查询
def get_callers(node_id, limit=20):
    return db.execute("""
        SELECT n.id, n.kind, n.name, n.file_path, n.start_line
        FROM edges e
        JOIN nodes n ON n.id = e.source
        WHERE e.target = ? AND e.kind = 'calls'
        ORDER BY n.file_path, n.start_line
        LIMIT ?
    """, (node_id, limit)).fetchall()

# 3. 被调用者查询
def get_callees(node_id, limit=20):
    return db.execute("""
        SELECT n.id, n.kind, n.name, n.file_path, n.start_line
        FROM edges e
        JOIN nodes n ON n.id = e.target
        WHERE e.source = ? AND e.kind = 'calls'
        ORDER BY n.file_path, n.start_line
        LIMIT ?
    """, (node_id, limit)).fetchall()

# 4. 影响分析 (递归 CTE)
def get_impact_radius(node_id, depth=2):
    return db.execute("""
        WITH RECURSIVE impact(id, depth) AS (
            SELECT CASE WHEN source = ? THEN target ELSE source END, 1
            FROM edges WHERE source = ? OR target = ?
            UNION ALL
            SELECT CASE WHEN e.source = i.id THEN e.target ELSE e.source END, i.depth + 1
            FROM edges e JOIN impact i ON (e.source = i.id OR e.target = i.id)
            WHERE i.depth < ?
        )
        SELECT DISTINCT n.id, n.kind, n.name, n.file_path, n.start_line
        FROM impact JOIN nodes n ON n.id = impact.id
    """, (node_id, node_id, node_id, depth)).fetchall()

# 5. 全量路由查询
def get_all_routes(limit=3000):
    return db.execute("""
        SELECT id, name, file_path, start_line, end_line
        FROM nodes WHERE kind = 'route'
        ORDER BY file_path, start_line
        LIMIT ?
    """, (limit,)).fetchall()

# 6. Route ↔ Method 链接
def get_route_method_linkage(file_path):
    return db.execute("""
        SELECT r.name as route_name, r.start_line as route_line,
               m.name as method_name, m.start_line as method_line,
               m.signature
        FROM nodes r
        JOIN nodes m ON m.file_path = r.file_path AND m.start_line = r.start_line
        WHERE r.kind = 'route' AND m.kind = 'method'
          AND r.file_path = ?
        ORDER BY r.start_line
    """, (file_path,)).fetchall()

db.close()
```

### 5.2 性能对比

| 操作 | SQLite | CLI | MCP |
|------|--------|-----|-----|
| Route 总数查询 | **0.1ms** | ~500ms | ~200ms |
| FTS5 搜索 (login, method) | **0.3ms** | ~800ms | ~300ms |
| FTS5 搜索 (GET, route, 100条) | **2.9ms** | ~1000ms | ~500ms |
| Callers 查询 | **0.1ms** | ~600ms | ~200ms |
| Callees 查询 | **0.1ms** | ~600ms | ~200ms |
| Impact depth=1 | **0.2ms** | ~800ms | ~300ms |
| 全部 Controller 类 | **9.5ms** | ~1500ms | N/A |
| explore (架构问题) | N/A | N/A | **1.36s** |
| node (类结构) | N/A | N/A | **0.00s** |

**结论**: SQLite 直连比 CLI 快 **1000-5000x**，比 MCP 快 **100-1000x**。

---

## 6. 性能对比

### 6.1 三种访问方式对比

| 维度 | CLI subprocess | MCP stdio | SQLite 直连 |
|------|---------------|-----------|-------------|
| **searchNodes / query** | `codegraph query -j` | `codegraph_search` | FTS5 SQL (0.3ms) |
| **findRelevantContext / explore** | ❌ 无 CLI | `codegraph_explore` (0.5-1.4s) | 近似模拟 (质量低) |
| **getNode / node** | ❌ 无 CLI | `codegraph_node` (0.00s) | 直接 SELECT (0.1ms, 无源码) |
| **getCallers / callers** | `codegraph callers -j` | `codegraph_callers` | 边 SQL (0.1ms) |
| **getCallees / callees** | `codegraph callees -j` | `codegraph_callees` | 边 SQL (0.1ms) |
| **getImpactRadius / impact** | `codegraph impact -j` | `codegraph_impact` | 递归 CTE (0.2ms) |
| **status** | `codegraph status -j` | `codegraph_status` | 直接 SQL |
| **files** | `codegraph files -j` | `codegraph_files` | `SELECT FROM files` |
| **返回格式** | JSON | Markdown | Row objects |
| **源码获取** | ❌ | ✅ (含行号) | 需读文件 |
| **trail (调用链标注)** | ❌ | ✅ | 需手动组装 |
| **性能** | 中 (进程开销) | 中 (协议开销) | **最高** (直连) |
| **依赖** | codegraph CLI | mcp Python SDK | sqlite3 (内置) |
| **并发安全** | ✅ | ✅ | ✅ (WAL 模式只读) |

### 6.2 推荐方案: 分层混合架构

```
┌─────────────────────────────────────────────────────────┐
│  Layer 3: MCP (深度分析, 按需调用)                        │
│  - codegraph_explore: 架构理解, 流程追踪                  │
│  - codegraph_node: 方法源码 + trail 获取                  │
│  触发: 交叉验证阶段, AI 分类阶段                           │
├─────────────────────────────────────────────────────────┤
│  Layer 2: CLI subprocess (批量操作, 简单查询)              │
│  - codegraph query -k route: 全量路由获取                 │
│  - codegraph status: 索引健康检查                         │
│  触发: Phase 3 主扫描                                    │
├─────────────────────────────────────────────────────────┤
│  Layer 1: SQLite 直连 (高频查询, 性能关键)                 │
│  - FTS5 搜索: 符号查找 (0.3ms)                           │
│  - 边查询: callers/callees (0.1ms)                       │
│  - Route↔Method 匹配: 行号对齐 (0.1ms)                   │
│  - Controller→Routes: contains + line match              │
│  触发: 全阶段高频操作                                     │
└─────────────────────────────────────────────────────────┘
```

---

## 7. Route 节点专项分析

### 7.1 Route 节点结构

**ID 格式**:
```
route:<file_path>:<line>:<METHOD>:<path>
例: route:yudao-module-system/.../AuthController.java:66:POST:/system/auth/login
```

**Name 格式**:
```
METHOD /path
例: POST /system/auth/login
```

### 7.2 Route ↔ Method 链接机制

**关键发现**:
```
route 节点 --references--> method 节点 (2364 条边)
```

- Route 和 handler method **共享相同的 `file_path` + `start_line`**
- **没有 `contains` 边**连接 class/method 到 route
- Route 不在类的结构层次中

**匹配方式**:
```python
# 按 file_path + start_line 对齐
SELECT r.name as route_name, m.name as method_name
FROM nodes r
JOIN nodes m ON m.file_path = r.file_path AND m.start_line = r.start_line
WHERE r.kind = 'route' AND m.kind = 'method'
```

**实测验证**:
- SmsChannelController: 7 methods, 7 routes, **100% 匹配**
- AuthController: 10 methods, 10 routes, **100% 匹配**

### 7.3 Route 专用查询函数

```python
def get_all_routes(db, limit=3000):
    """获取所有路由节点"""
    return db.execute("""
        SELECT id, name, file_path, start_line, end_line
        FROM nodes WHERE kind = 'route'
        ORDER BY file_path, start_line
        LIMIT ?
    """, (limit,)).fetchall()

def get_routes_by_method(db, method, limit=3000):
    """按 HTTP 方法过滤路由"""
    return db.execute("""
        SELECT id, name, file_path, start_line
        FROM nodes
        WHERE kind = 'route' AND name LIKE ?
        ORDER BY name
        LIMIT ?
    """, (f"{method} %", limit)).fetchall()

def get_controller_routes(db, controller_name):
    """获取一个 Controller 的全部路由"""
    # Step 1: 找 Controller class
    ctrl = db.execute("""
        SELECT id, name, file_path FROM nodes
        WHERE kind = 'class' AND name = ? LIMIT 1
    """, (controller_name,)).fetchone()
    if not ctrl:
        return []

    # Step 2: 获取 contains 的方法
    methods = db.execute("""
        SELECT n.name, n.start_line, n.signature
        FROM edges e JOIN nodes n ON e.target = n.id
        WHERE e.source = ? AND e.kind = 'contains' AND n.kind = 'method'
        ORDER BY n.start_line
    """, (ctrl['id'],)).fetchall()

    # Step 3: 按行号匹配 route 节点
    routes = db.execute("""
        SELECT name, start_line FROM nodes
        WHERE kind = 'route' AND file_path = ?
        ORDER BY start_line
    """, (ctrl['file_path'],)).fetchall()

    # Step 4: 合并
    route_map = {r['start_line']: r['name'] for r in routes}
    result = []
    for m in methods:
        route_name = route_map.get(m['start_line'], None)
        result.append({
            'method': m['name'],
            'route': route_name,
            'line': m['start_line'],
            'signature': m['signature'],
        })
    return result
```

---

## 8. 最佳实践

### 8.1 初始化与索引

1. **首次使用**: 运行 `codegraph init` 创建索引
2. **大项目**: 使用 `-v` 查看详细进度
3. **索引损坏**: 使用 `codegraph index -f` 强制重建
4. **CI/CD**: 在构建流程中加入 `codegraph sync -q`

### 8.2 查询优化

1. **精确查询**: 使用 `-k` 指定 kind 减少结果集
2. **限制结果**: 使用 `-l` 限制返回数量
3. **JSON 输出**: 使用 `-j` 便于脚本解析
4. **高频查询**: 使用 SQLite 直连 (快 1000x)

### 8.3 MCP 使用

1. **主工具**: 优先使用 `codegraph_explore` (v0.9.9+)
2. **深度分析**: 使用 `codegraph_node` 获取源码和 trail
3. **快速定位**: 使用 `codegraph_search` 只获取位置
4. **环境变量**: 调整 `CODEGRAPH_WATCH_DEBOUNCE_MS` 优化文件监听

### 8.4 Route 发现

1. **全量获取**: `SELECT * FROM nodes WHERE kind = 'route'`
2. **按方法过滤**: `WHERE name LIKE 'GET %'`
3. **Controller 路由**: 使用 `get_controller_routes()` 函数
4. **Route↔Method**: 按 `file_path + start_line` 对齐

### 8.5 性能调优

1. **SQLite WAL**: 确保数据库使用 WAL 模式 (默认)
2. **连接池**: 复用 SQLite 连接，避免频繁打开/关闭
3. **索引缓存**: 守护进程模式下索引常驻内存
4. **批量查询**: 合并多个查询为单次 SQL

### 8.6 全限定名查询 (Qualified Name Query)

**关键发现 (2026-06-04)**: `query` 和 `callers` 都支持 `ClassName.methodName` 格式的全限定名查询，可精确消除同名方法的歧义。

#### 问题: 裸名查询导致大量误匹配

```bash
# ❌ 裸名查询 — "execute" 匹配所有项目的 execute() 方法
codegraph query "execute" -p project -l 20 -j
# → 返回 100+ 结果: DemoJob.execute, JobHandler.execute, TenantUtils.execute ...

# ❌ 裸名 callers — 同样匹配所有同名方法
codegraph callers "execute" -p project -l 20 -j
# → 返回 10 个混杂的调用者 (来自不同类)
```

#### 解决方案: 使用 ClassName.methodName 格式

```bash
# ✅ 全限定名查询 — 精确匹配特定类的方法
codegraph query "JobHandler.execute" -p project -l 5 -j
# → 返回 1 个结果: cn.iocoder.yudao...JobHandler::execute

# ✅ 全限定名 callers — 精确追踪特定方法的调用者
codegraph callers "JobHandler.execute" -p project -l 10 -j
# → 返回 1 个结果: JobHandlerInvoker.executeInternal

# ✅ 完整 FQN 也可以
codegraph callers "cn.iocoder.yudao.framework.quartz.core.handler.JobHandler.execute" -p project -l 10 -j
# → 同上，精确匹配
```

#### 递归调用链: 每一层都必须用全限定名

```
depth=0: ImRtcCallCleanupJob.execute
  depth=1: callers("ImRtcCallCleanupJob.execute") → JobHandler.execute (接口)
    depth=2: callers("JobHandler.execute") → JobHandlerInvoker.executeInternal
      depth=3: callers("JobHandlerInvoker.executeInternal") → [chain ends]
```

**如果在任何一层使用裸名 `"execute"`，就会匹配到所有类的 `execute()` 方法，导致调用链污染。**

#### 在 Python 代码中实现

```python
# 从 file_path 提取类名
class_name = Path(file_path).stem  # "JobHandler" from ".../JobHandler.java"

# 构造全限定符号
qualified_symbol = f"{class_name}.{method_name}"  # "JobHandler.execute"

# 用全限定名查询 callers
callers = client.callers(qualified_symbol)
```

#### query 结果过滤: 按类名段匹配 (Segment Matching)

**核心原则**: 只有当方法所属的类与 sink 定义的类完全匹配时，才将其视为 sink 点。

`query` 返回的每个结果都包含 `qualifiedName` 字段 (格式: `package::ClassName::methodName`)，可用于后过滤:

```python
# 查询裸名，但按期望类名做段匹配过滤
nodes = client.query("execute")
class_simple = "Runtime"  # 从 sink 定义中的 class 字段提取

# 段匹配: 检查 "::ClassName::" 或 "::ClassName" (末尾)
# 子串匹配太宽泛: "Method" 会误匹配 "InvocableHandlerMethod"
methods = [n for n in nodes 
           if n.kind == "method" 
           and (f"::{class_simple}::" in (n.qualified_name or "")
                or (n.qualified_name or "").endswith(f"::{class_simple}"))]
# → 对于 JDK 类 (Runtime): 0 匹配 (正确 — JDK 未索引)
# → 对于项目内类: 精确匹配
```

**段匹配 vs 子串匹配对比**:

| qualifiedName | class_simple | 子串匹配 | 段匹配 | 正确结果 |
|--------------|-------------|---------|--------|---------|
| `...::JobHandler::execute` | `JobHandler` | ✅ | ✅ | ✅ 精确匹配 |
| `...::InvocableHandlerMethod::invoke` | `Method` | ✅ (误报) | ❌ | ❌ 不是 Method 类 |
| `...::IotMqttTopicUtils::normalizeReplyMethod` | `Method` | ✅ (误报) | ❌ | ❌ 不是 Method 类 |
| `...::DemoJob::execute` | `Statement` | ❌ | ❌ | ❌ 不同类 |
| `...::JdbcTemplate::execute` | `JdbcTemplate` | ✅ | ✅ | ✅ 精确匹配 |

#### SQLite 直连: 最精确的方式

```python
# 精确匹配 qualified_name
rows = db.execute("""
    SELECT id, name, qualified_name, file_path, start_line
    FROM nodes
    WHERE qualified_name = ?
    LIMIT 1
""", ("cn.iocoder.yudao...JobHandler::execute",))

# 通过 node_id 精确查询调用者
callers = db.execute("""
    SELECT n.name, n.qualified_name, n.file_path, n.start_line
    FROM edges e JOIN nodes n ON n.id = e.source
    WHERE e.target = ? AND e.kind = 'calls'
""", (node_id,))
```

#### 实测对比 (ruoyi-vue-pro)

| 查询方式 | 命令 | 结果数 | 精确度 |
|---------|------|--------|--------|
| 裸名 query | `query "execute"` | 100+ | ❌ 所有 execute() 方法 |
| 类.方法 query | `query "JobHandler.execute"` | 1 | ✅ 精确匹配 |
| 完整 FQN query | `query "cn.iocoder...JobHandler.execute"` | 1 | ✅ 精确匹配 |
| 裸名 callers | `callers "execute"` | 10 (混杂) | ❌ 跨类误匹配 |
| 类.方法 callers | `callers "JobHandler.execute"` | 1 | ✅ 精确调用者 |
| SQLite edges | `WHERE target = node_id` | 1 | ✅ 最精确 |

### 8.7 故障排查

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 索引失败 | 锁文件残留 | `codegraph unlock` |
| 查询无结果 | 索引未初始化 | `codegraph init` |
| MCP 连接失败 | 守护进程未启动 | `codegraph serve --mcp` |
| 性能慢 | 使用 CLI 而非 SQLite | 切换到 SQLite 直连 |
| Route 不匹配 | 行号偏移 | 检查 `start_line` 是否一致 |
| 同名方法误匹配 | 使用裸名查询 | 改用 `ClassName.methodName` 格式 (§8.6) |
| 调用链跨类污染 | callers 使用裸名 | 每层递归都用全限定名 (§8.6) |

---

## 附录 A: 版本历史

### v0.9.9 (2026-06-02)
- `codegraph_explore` 成为主工具
- 移除 `codegraph_trace` 和 `codegraph_context`
- explore 包含 blast radius 信息
- node 返回所有重载定义

### v0.9.8 (2026-06-01)
- `init` 默认执行索引 (无需 `-i`)
- explore 自适应响应大小
- 嵌入式库 API 重新启用

### v0.9.7 (2026-05-28)
- Java/Kotlin FQN 导入解析
- 生成文件排名最后
- Windows 黑窗口修复

### v0.9.6 (2026-05-27)
- Spring/MyBatis 端到端流程
- 字段注入 Bean 解析
- `@Value` 配置解析

### v0.9.5 (2026-05-25)
- 共享守护进程 (多 Agent 共享单索引)
- 待重新索引警告
- 默认排除 node_modules

### v0.9.4 (2026-05-24)
- **Spring 路由解析** (关键!)
- 动态分发桥接
- `CODEGRAPH_MCP_TOOLS` 环境变量

### v0.9.0 (2026-05-21)
- 自包含运行时 (无需 Node.js)
- SQLite WAL 模式

---

## 附录 B: 环境变量完整列表

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CODEGRAPH_NO_DAEMON` | 禁用共享守护进程 | 未设置 (启用) |
| `CODEGRAPH_DAEMON_IDLE_TIMEOUT_MS` | 守护进程空闲超时 | 300000 (5 分钟) |
| `CODEGRAPH_WATCH_DEBOUNCE_MS` | 文件监听去抖 | 2000 |
| `CODEGRAPH_MCP_TOOLS` | 选择性暴露 MCP 工具 | 全部 |
| `CODEGRAPH_PPID_POLL_MS` | 父进程检查间隔 | 未设置 |
| `CODEGRAPH_NO_DOWNLOAD` | 禁用 GitHub Releases 下载回退 | 未设置 |
| `CODEGRAPH_DOWNLOAD_BASE` | 自定义下载镜像 URL | GitHub Releases |
| `CODEGRAPH_VERSION` | 指定版本 | latest |
| `CODEGRAPH_ADAPTIVE_EXPLORE` | 自适应 explore 响应大小 | 1 (启用) |

---

**文档版本**: 1.2  
**最后更新**: 2026-06-04  
**作者**: Sisyphus (AI Agent)  
**v1.2 变更**: 更新 §8.6 query 结果过滤为段匹配 (Segment Matching)，修复子串匹配误报问题  
**v1.1 变更**: 新增 §8.6 全限定名查询 (Qualified Name Query) — 解决同名方法误匹配问题
