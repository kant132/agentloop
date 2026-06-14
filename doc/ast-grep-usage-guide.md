# ast-grep 使用文档

> **版本**: v0.43.0  
> **项目**: [ast-grep/ast-grep](https://github.com/ast-grep/ast-grep)  
> **官方 Agent Skill**: [ast-grep/agent-skill](https://github.com/ast-grep/agent-skill/blob/main/ast-grep/skills/ast-grep/SKILL.md)  
> **测试环境**: Windows PowerShell 5.1 + Python 3.14  
> **测试项目**: ruoyi-vue-pro (Java Spring Boot)

---

## 目录

- [0. 通用工作流 (来自官方 Skill)](#0-通用工作流-来自官方-skill)
- [1. 概述](#1-概述)
- [2. 安装与配置](#2-安装与配置)
- [3. CLI 命令详解](#3-cli-命令详解)
  - [3.1 run - 单次搜索/改写](#31-run---单次搜索改写)
  - [3.2 scan - 规则扫描](#32-scan---规则扫描)
  - [3.3 test - 规则测试](#33-test---规则测试)
  - [3.4 new - 脚手架创建](#34-new---脚手架创建)
  - [3.5 lsp - 语言服务器](#35-lsp---语言服务器)
  - [3.6 completions - Shell 补全](#36-completions---shell-补全)
- [4. 模式语法 (Pattern Syntax)](#4-模式语法-pattern-syntax)
  - [4.1 基础模式匹配](#41-基础模式匹配)
  - [4.2 元变量 (Meta Variables)](#42-元变量-meta-variables)
  - [4.3 多元变量 (Multi Meta Variables)](#43-多元变量-multi-meta-variables)
  - [4.4 元变量捕获](#44-元变量捕获)
- [5. 规则系统 (Rule System)](#5-规则系统-rule-system)
  - [5.1 规则基础](#51-规则基础)
  - [5.2 原子规则 (Atomic Rules)](#52-原子规则-atomic-rules)
  - [5.3 关系规则 (Relational Rules)](#53-关系规则-relational-rules)
  - [5.4 组合规则 (Composite Rules)](#54-组合规则-composite-rules)
- [6. Java 专用模式](#6-java-专用模式)
- [7. JSON 输出格式](#7-json-输出格式)
- [8. 调试与排错 (来自官方 Skill)](#8-调试与排错-来自官方-skill)
- [9. 实测验证](#9-实测验证)
- [10. 最佳实践](#10-最佳实践)
- [11. 官方文档索引](#11-官方文档索引)

---

## 0. 通用工作流 (来自官方 Skill)

> 来源: [ast-grep/agent-skill SKILL.md](https://github.com/ast-grep/agent-skill/blob/main/ast-grep/skills/ast-grep/SKILL.md)

遵循以下 5 步流程编写 ast-grep 规则：

### Step 1: 理解查询意图

明确用户想找什么：
- 什么代码模式或结构？
- 什么语言？
- 有哪些边界情况或变体需要考虑？
- 应包含/排除什么？

### Step 2: 创建示例代码

写一个简单代码片段，保存到临时文件中测试：

```javascript
// test_example.js
async function example() {
  const result = await fetchData();
  return result;
}
```

### Step 3: 编写 ast-grep 规则

**核心原则：**
- 关系规则 (`inside`, `has`) 始终使用 `stopBy: end`
- 简单结构用 `pattern`，复杂结构用 `kind` + `has`/`inside`
- 复杂查询用 `all`, `any`, `not` 拆成小子规则

```yaml
id: async-with-await
language: javascript
rule:
  kind: function_declaration
  has:
    pattern: await $EXPR
    stopBy: end
```

### Step 4: 测试规则

**方式 A: 内联规则 (快速迭代)**
```bash
echo "async function test() { await fetch(); }" | ast-grep scan --inline-rules "id: test
language: javascript
rule:
  kind: function_declaration
  has:
    pattern: await \$EXPR
    stopBy: end" --stdin
```

**方式 B: 规则文件 (复杂规则推荐)**
```bash
ast-grep scan --rule test_rule.yml test_example.js
```

### Step 5: 搜索代码库

```bash
# 简单模式
ast-grep run --pattern 'console.log($ARG)' --lang javascript .

# 规则文件
ast-grep scan --rule my_rule.yml /path/to/project

# 内联规则 (不创建文件)
ast-grep scan --inline-rules "id: my-rule
language: javascript
rule:
  pattern: \$PATTERN" /path/to/project
```

### Shell 转义提示

内联规则中元变量需要转义 `$`：
```bash
# 正确: 转义 $
ast-grep scan --inline-rules "rule: {pattern: 'console.log(\$ARG)'}" .

# 或使用单引号
ast-grep scan --inline-rules 'rule: {pattern: "console.log($ARG)"}' .
```

---

## 1. 概述

ast-grep 是一个基于 AST (抽象语法树) 的代码搜索、lint 和改写工具。可以理解为 "grep + eslint + codemod 的混合体"。

### 核心优势

- **结构感知**: 匹配代码模式，而非纯文本
- **语言感知**: 理解 20+ 语言的语法
- **精确匹配**: 避免字符串/注释中的误匹配
- **高性能**: Rust 编写，多核支持

### 支持语言 (20+)

C, C++, Rust, Go, Java, Python, C#, JavaScript, TypeScript, HTML, CSS, Kotlin, Swift, JSON, YAML, Ruby, PHP, Lua, Elixir, Scala, Haskell, Solidity

### 与 grep/ripgrep 的区别

| 维度 | grep/ripgrep | ast-grep |
|------|-------------|----------|
| 匹配方式 | 纯文本/正则 | AST 结构 |
| 语言感知 | ❌ | ✅ 20+ 语言 |
| 注释/字符串过滤 | ❌ 会误匹配 | ✅ 自动跳过 |
| 格式化无关 | ❌ 受空格/换行影响 | ✅ AST 级别匹配 |
| 改写能力 | ❌ | ✅ 结构化改写 |

---

## 2. 安装与配置

### 安装方式

```bash
# npm (推荐)
npm install -g @ast-grep/cli

# cargo
cargo install ast-grep

# homebrew
brew install ast-grep

# pip
pip install ast-grep
```

### 验证安装

```bash
ast-grep --version
# 输出: ast-grep 0.43.0

# Linux 上 sg 可能与 setgroups 命令冲突，使用完整名称
ast-grep --help
# 或设置别名
alias sg=ast-grep
```

### 项目配置 (sgconfig.yml)

```yaml
# sgconfig.yml - 项目根目录
ruleDirs:
  - rules
testConfigs:
  - testDir: rule-tests
utilDirs:
  - utils
```

---

## 3. CLI 命令详解

ast-grep 共有 6 个子命令:

| 命令 | 用途 | 本方案使用 |
|------|------|-----------|
| `run` | 单次模式搜索/改写 (默认命令) | ✅ 核心 |
| `scan` | 规则文件扫描 | ✅ 关系规则 |
| `test` | 规则测试 | ❌ |
| `new` | 脚手架创建 (project/rule/test/util) | ❌ |
| `lsp` | 语言服务器 | ❌ |
| `completions` | Shell 补全脚本 | ❌ |

### 3.1 run - 单次搜索/改写

**用途**: 使用 AST 模式进行一次性搜索或改写。

**语法**:
```bash
ast-grep run [OPTIONS] <--pattern|--selector|--strictness|--kind> [PATHS...]
```

**必选参数 (四选一)**:

| 参数 | 说明 | 示例 |
|------|------|------|
| `-p, --pattern <PATTERN>` | AST 模式匹配 | `@GetMapping($$$ARGS)` |
| `--selector <KIND>` | 指定 AST 节点 kind 作为匹配器 | `annotation` |
| `--strictness <LEVEL>` | 匹配严格度 | `cst\|smart\|ast\|relaxed\|signature` |
| `-k, --kind <KIND>` | 按 AST kind 匹配 (ESQuery 风格) | `method_declaration` |

**可选参数**:

| 参数 | 说明 | 默认值 | 本方案用途 |
|------|------|--------|-----------|
| `-r, --rewrite <FIX>` | 替换匹配节点的字符串 | 无 | ❌ 只读扫描 |
| `-l, --lang <LANG>` | 指定语言 | 自动推断 | ✅ `java` |
| `-c, --config <FILE>` | sgconfig.yml 路径 | `sgconfig.yml` | ❌ |
| `--debug-query[=format]` | 打印查询模式的 AST | 无 | ✅ 调试 |
| `--follow` | 跟随符号链接 | false | ❌ |
| `--no-ignore <TYPE>` | 忽略 .gitignore 等 | 尊重 | ⚠️ 可选 |
| `--stdin` | 从 stdin 读取代码 | false | ⚠️ 管道输入 |
| `--globs <GLOBS>` | 文件路径过滤 (支持 ! 排除) | 无 | ✅ `!**/test/**` |
| `-j, --threads <NUM>` | 线程数 (0=自动) | 0 | ✅ 大项目加速 |
| `-i, --interactive` | 交互式编辑 | false | ❌ |
| `-U, --update-all` | 自动应用所有改写 | false | ❌ |
| `--files-with-matches` | 只输出匹配文件路径 | false | ⚠️ 快速检查 |
| `--json[=STYLE]` | JSON 输出 | 无 | ✅ **核心** |
| `--color <WHEN>` | 颜色控制 | auto | ❌ |
| `--inspect <LEVEL>` | 扫描追踪信息 | nothing | ✅ 调试 |
| `-A, --after <NUM>` | 匹配后 N 行上下文 | 0 | ❌ |
| `-B, --before <NUM>` | 匹配前 N 行上下文 | 0 | ❌ |
| `-C, --context <NUM>` | 匹配前后 N 行上下文 | 0 | ❌ |
| `--heading <WHEN>` | 文件名标题控制 | auto | ❌ |

**`--json` 输出格式**:

| STYLE | 说明 | 用途 |
|-------|------|------|
| `pretty` | 美化 JSON 数组 | 人工阅读 |
| `stream` | 每行一个 JSON 对象 | 流式处理 |
| `compact` | 单行 JSON 数组 | **程序解析 (推荐)** |

**`--debug-query` 输出格式**:

| format | 说明 |
|--------|------|
| `pattern` | 查询解析为 Pattern 格式 |
| `ast` | tree-sitter AST (仅命名节点) |
| `cst` | tree-sitter CST (含未命名节点) |
| `sexp` | S-expression 格式 |

**示例**:

```bash
# 基本搜索
ast-grep run -p '@GetMapping($$$ARGS)' -l java --json=compact .

# 带文件过滤
ast-grep run -p '@PostMapping($$$ARGS)' -l java --json=compact --globs '!**/test/**' .

# 按 kind 搜索
ast-grep run --kind annotation -l java --json=compact .

# 搜索并改写 (交互模式)
ast-grep run -p 'var $VAR = $VAL' -r 'let $VAR = $VAL' -l javascript -i

# 搜索并改写 (自动应用)
ast-grep run -p 'var $VAR = $VAL' -r 'let $VAR = $VAL' -l javascript -U

# 从 stdin 读取
echo "var x = 1" | ast-grep run --stdin -l javascript -p 'var $V = $VAL'

# 调试模式
ast-grep run --debug-query=ast -p '@GetMapping($$$ARGS)' -l java

# 只输出匹配文件
ast-grep run -p '@GetMapping($$$ARGS)' -l java --files-with-matches .

# 扫描统计
ast-grep run -p '@GetMapping($$$ARGS)' -l java --inspect=summary .
```

**实测验证** (ruoyi-vue-pro):

| 测试 | 结果 |
|------|------|
| `--pattern "@GetMapping($$$ARGS)"` | ✅ 返回匹配 |
| `--kind annotation` | ✅ 返回所有注解节点 |
| `--kind method_declaration --globs "**/auth/**"` | ✅ 返回 auth 目录下所有方法 |
| `--files-with-matches` | ✅ 只返回文件路径列表 |
| `--inspect=summary` | ✅ stderr 输出 `scannedFileCount=456` |
| `--max-results` | ❌ **仅 scan 命令支持，run 不支持** |

---

### 3.2 scan - 规则扫描

**用途**: 使用 YAML 规则文件进行扫描，支持关系规则 (inside/has/follows/precedes)。

**语法**:
```bash
ast-grep scan [OPTIONS] [PATHS...]
```

**独有参数** (run 没有的):

| 参数 | 说明 | 本方案用途 |
|------|------|-----------|
| `-r, --rule <FILE>` | 单规则文件路径 | ✅ 类级 @RequestMapping |
| `--inline-rules <TEXT>` | 内联规则文本 (YAML, `---` 分隔多规则) | ✅ 动态生成规则 |
| `--format <FMT>` | 输出格式: `github` / `sarif` | ❌ |
| `--report-style <STYLE>` | 报告样式: `rich` / `medium` / `short` | ❌ |
| `--include-metadata` | JSON 输出中包含规则元数据 | ⚠️ |
| `--filter <REGEX>` | 按 rule id 正则过滤 | ⚠️ 大规则集 |
| `--error[=RULE_ID...]` | 设置规则严重度为 error | ❌ |
| `--warning[=RULE_ID...]` | 设置规则严重度为 warning | ❌ |
| `--info[=RULE_ID...]` | 设置规则严重度为 info | ❌ |
| `--hint[=RULE_ID...]` | 设置规则严重度为 hint | ❌ |
| `--off[=RULE_ID...]` | 关闭指定规则 | ❌ |
| `--max-results <NUM>` | **最大结果数限制** | ✅ 大项目防爆 |

**与 run 共享的参数**: `--follow`, `--no-ignore`, `--stdin`, `--globs`, `--threads`, `--interactive`, `--update-all`, `--files-with-matches`, `--json`, `--color`, `--inspect`, `-A/-B/-C`, `--heading`

**规则文件 YAML 格式**:

```yaml
id: class-level-mapping
language: java
rule:
  pattern: "@RequestMapping($$$ARGS)"
  has:
    kind: annotation_argument_list
```

**示例**:

```bash
# 使用规则文件扫描
ast-grep scan --rule rules/no-console.yml --json=compact .

# 内联规则 (适合脚本调用)
ast-grep scan --inline-rules '
id: find-deprecated
language: java
rule:
  kind: method_declaration
  has:
    kind: marker_annotation
    pattern: "@Deprecated"
' --json=compact .

# 按 rule id 过滤
ast-grep scan --filter 'no-console' --json=compact .

# 限制结果数
ast-grep scan --rule rules/my-rule.yml --max-results 100 --json=compact .
```

**`scan --json=compact` 返回结构** (比 run 多了 `ruleId`, `severity`, `message`, `labels`):

```json
[{
  "text": "@RequestMapping(\"/system/auth\")",
  "range": {"start": {"line": 44, "column": 0}, "end": {"line": 44, "column": 31}},
  "file": "D:\\code\\...\\AuthController.java",
  "lines": "@RequestMapping(\"/system/auth\")\r",
  "language": "Java",
  "metaVariables": {
    "multi": {"ARGS": [{"text": "\"/system/auth\""}]}
  },
  "ruleId": "class-level-mapping",
  "severity": "hint",
  "note": null,
  "message": "",
  "labels": [
    {"text": "@RequestMapping(\"/system/auth\")", "style": "primary"},
    {"text": "(\"/system/auth\")", "style": "secondary"}
  ]
}]
```

**实测验证**:

| 测试 | 结果 |
|------|------|
| `--rule <file>` | ✅ 成功返回 35+ 个 @RequestMapping |
| `--inline-rules` | ⚠️ PowerShell 转义困难，建议写临时文件用 `--rule` |
| `--max-results 5` | ✅ 限制返回 5 条 |
| `inside: kind: class_body` | ❌ 不匹配 (注解在 class 声明之前) |
| `has: kind: annotation_argument_list` | ✅ 正确匹配所有带参数的 @RequestMapping |

---

### 3.3 test - 规则测试

**用途**: 测试 ast-grep 规则的正确性。

**语法**:
```bash
ast-grep test [OPTIONS]
```

**参数**:

| 参数 | 说明 |
|------|------|
| `-t, --test-dir <DIR>` | 测试 YAML 文件目录 |
| `--snapshot-dir <DIR>` | 快照目录名 |
| `--skip-snapshot-tests` | 跳过快照检查 |
| `-U, --update-all` | 更新所有快照 |
| `-i, --interactive` | 交互式快照审查 |
| `-f, --filter <REGEX>` | 按正则过滤测试用例 |
| `-c, --config <FILE>` | sgconfig.yml 路径 |
| `--include-off` | 包含 severity:off 规则 |
| `--follow` | 跟随符号链接 |

**示例**:

```bash
# 运行所有测试
ast-grep test

# 更新快照
ast-grep test -U

# 过滤特定测试
ast-grep test -f 'no-console'
```

---

### 3.4 new - 脚手架创建

**用途**: 创建 ast-grep 项目结构或规则/测试模板。

**子命令**:

| 子命令 | 用途 |
|--------|------|
| `new project [NAME]` | 创建项目: sgconfig.yml + rules/ + rule-tests/ + utils/ |
| `new rule [NAME]` | 创建新规则文件 |
| `new test [NAME]` | 创建新测试用例 |
| `new util [NAME]` | 创建全局工具规则 |

**参数**:

| 参数 | 说明 |
|------|------|
| `-l, --lang <LANG>` | 语言 |
| `-y, --yes` | 非交互模式 |
| `-c, --config <FILE>` | 配置文件路径 |

**示例**:

```bash
# 创建新项目
ast-grep new project my-project

# 创建 Java 规则
ast-grep new rule no-system-out -l java -y

# 创建测试
ast-grep new test no-system-out -y
```

---

### 3.5 lsp - 语言服务器

**用途**: 启动 LSP 服务器，供编辑器使用。

**语法**:
```bash
ast-grep lsp [-c CONFIG_FILE]
```

**本方案不使用**。

---

### 3.6 completions - Shell 补全

**用途**: 生成 shell 补全脚本。

**语法**:
```bash
ast-grep completions [bash|elvish|fish|powershell|zsh]
```

**示例**:

```bash
# PowerShell 补全
ast-grep completions powershell > ast-grep-completions.ps1

# Bash 补全
ast-grep completions bash > ~/.bash_completion.d/ast-grep
```

---

## 4. 模式语法 (Pattern Syntax)

### 4.1 基础模式匹配

ast-grep 使用**模式代码**来构造 AST 树并与目标代码匹配。模式可以搜索完整的语法树，因此也能匹配嵌套表达式。

```javascript
// 模式: a + 1
// 匹配以下所有代码:
const b = a + 1
funcCall(a + 1)
deeplyNested({ target: a + 1 })
```

**关键特性**: 模式匹配与空格/换行无关，以下都能匹配:

```javascript
// 模式: obj.val && obj.val()
obj.val && obj.val()           // 精确匹配
obj.val    &&     obj.val()    // 空格不同也匹配
const result = obj.val &&
   obj.val()                   // 换行也匹配
```

**⚠️ 重要**: 模式必须是 tree-sitter 能解析的**有效代码**。

### 4.2 元变量 (Meta Variables)

元变量用于匹配动态内容，以 `$` 开头，后跟大写字母/下划线/数字。

**格式**: `$` + 大写字母 A-Z / 下划线 _ / 数字 1-9

| 合法 | 不合法 |
|------|--------|
| `$META`, `$META_VAR`, `$META_VAR1` | `$invalid`, `$Svalue`, `$123` |
| `$_`, `$_123` | `$KEBAB-CASE`, `$camelCase` |

**示例**:

```javascript
// 模式: console.log($GREETING)
// 匹配:
console.log('Hello World')
console.log(debugMessage)
console
  .log('Also matched!')

// 不匹配:
// console.log(123)  ← 注释中不匹配
'console.log(123)'   ← 字符串中不匹配
console.log()         ← 缺少参数
console.log(a, b)     ← 参数过多
```

### 4.3 多元变量 (Multi Meta Variables)

使用 `$$$` 匹配零个或多个 AST 节点:

```javascript
// 模式: console.log($$$)
console.log()                       // 匹配零个节点
console.log('hello world')          // 匹配一个节点
console.log('debug: ', key, value)  // 匹配多个节点
console.log(...args)                // 也匹配 spread

// 模式: function $FUNC($$$ARGS) { $$$ }
function foo(bar) { return bar }    // ARGS = [bar]
function noop() {}                  // ARGS = []
function add(a, b, c) { return a + b + c }  // ARGS = [a, b, c]
```

**命名多元变量**: `$$$ARGS` 可以捕获所有参数。

### 4.4 元变量捕获

**重复使用同名变量**确保匹配的代码相同:

```javascript
// 模式: $A == $A
a == a          // ✅ 匹配
1 + 1 == 1 + 1  // ✅ 匹配
a == b          // ❌ 不匹配
1 + 1 == 2      // ❌ 不匹配
```

**非捕获匹配**: 以 `_` 开头的变量不捕获 (性能优化):

```javascript
// 模式: $_FUNC($_FUNC)
// 匹配所有单参数函数调用，两个 $_FUNC 可以匹配不同内容
test(a)
testFunc(1 + 1)
testFunc(...args)
```

**捕获未命名节点**: 使用 `$$VAR` (双美元符号) 捕获未命名节点。

---

## 5. 规则系统 (Rule System)

### 5.1 规则基础

规则是 ast-grep 的核心概念，类似 CSS 选择器，可以组合使用。

**最小规则示例**:

```yaml
id: no-await-in-promise-all
language: TypeScript
rule:
  pattern: Promise.all($A)
  has:
    pattern: await $_
    stopBy: end
```

**三个必要字段**:
- `id`: 唯一标识符
- `language`: 编程语言 (决定扫描哪些文件)
- `rule`: 匹配逻辑 (规则对象)

**规则对象完整字段**:

```yaml
rule:
  # 原子规则
  pattern: 'search.pattern'
  kind: 'tree_sitter_node_kind'
  regex: 'rust|regex'
  # 关系规则
  inside: { pattern: 'sub.rule' }
  has: { kind: 'sub_rule' }
  follows: { regex: 'can|use|any' }
  precedes: { kind: 'multi_keys', pattern: 'in.sub' }
  # 组合规则
  all: [ {pattern: 'match.all'}, {kind: 'match_all'} ]
  any: [ {pattern: 'match.any'}, {kind: 'match_any'} ]
  not: { pattern: 'not.this' }
  matches: 'utility-rule'
```

**⚠️ 重要**: 规则对象是**无序的**，节点必须满足所有字段才算匹配。

### 5.2 原子规则 (Atomic Rules)

#### pattern

匹配代码模式:

```yaml
rule:
  pattern: console.log($GREETING)
```

**Pattern Object** (解决歧义):

```yaml
# 选择类字段 (避免解析为赋值表达式)
pattern:
  selector: field_definition
  context: class A { $FIELD = $INIT }
```

**strictness** (匹配策略):

| 级别 | 说明 |
|------|------|
| `cst` | 所有节点必须匹配 (最严格) |
| `smart` | 跳过目标代码中的未命名节点 (默认) |
| `ast` | 只匹配命名 AST 节点 |
| `relaxed` | 忽略注释和未命名节点 |
| `signature` | 只匹配节点 kind (最宽松) |

#### kind

按 AST 节点类型匹配:

```yaml
rule:
  kind: field_definition  # 匹配类属性定义
```

**ESQuery 风格** (v0.39.1+):

```yaml
rule:
  kind: call_expression > identifier  # 匹配 call_expression 的子 identifier
```

#### regex

按正则表达式匹配节点文本:

```yaml
rule:
  regex: "(?i)apple"  # 匹配 apple/Apple/APPLE
```

**⚠️ 注意**: 正则使用 Rust 语法，不支持任意前瞻和后向引用。

#### nthChild

按兄弟节点中的位置匹配:

```yaml
# 匹配第 3 个子节点
nthChild: 3

# An+B 风格
nthChild: 2n+1

# 对象风格
nthChild:
  position: 2n+1
  reverse: true
  ofRule:
    kind: function_declaration
```

#### range

按源码位置匹配:

```yaml
rule:
  range:
    start: { line: 0, column: 0 }
    end: { line: 1, column: 5 }
```

### 5.3 关系规则 (Relational Rules)

关系规则基于节点的**周围节点**来过滤目标节点。

#### inside

目标节点必须在匹配的子规则节点**内部**:

```yaml
# 匹配 for 循环内的 await
rule:
  pattern: await $PROMISE
  inside:
    kind: for_in_statement
    stopBy: end
```

#### has

目标节点必须**包含**匹配的子规则子节点:

```yaml
# 匹配包含 prototype key 的对象属性
rule:
  kind: pair
  has:
    field: key
    regex: 'prototype'
```

#### follows

目标节点必须在匹配的子规则节点**之后**:

```yaml
# 匹配 console.log('world') 之后的 console.log('hello')
pattern: console.log('hello');
follows:
  pattern: console.log('world');
```

#### precedes

目标节点必须在匹配的子规则节点**之前**:

```yaml
rule:
  pattern: setup()
  precedes:
    pattern: teardown()
```

**stopBy 选项**:

| 值 | 说明 |
|----|------|
| `'neighbor'` | 只匹配直接相邻 (默认) |
| `'end'` | 搜索到末尾 |
| `{ rule }` | 搜索到匹配规则时停止 |

**field 选项**: 指定子节点的字段名:

```yaml
has:
  field: key    # 只匹配 key 字段
  regex: 'prototype'
```

### 5.4 组合规则 (Composite Rules)

#### all (AND)

所有子规则都必须匹配:

```yaml
rule:
  all:
    - pattern: $VAR = $VAL
    - not:
        inside:
          kind: function_declaration
```

#### any (OR)

至少一个子规则匹配:

```yaml
rule:
  any:
    - pattern: var $V
    - pattern: let $V
    - pattern: const $V
```

#### not (NOT)

子规则必须不匹配:

```yaml
rule:
  pattern: console.log($A)
  not:
    inside:
      pattern: try { $$$ } catch ($E) { $$$ }
```

#### matches

引用其他规则:

```yaml
rule:
  matches: utility-rule-id
```

---

## 6. Java 专用模式

### 注解匹配

```yaml
# 查找所有 @Deprecated 方法
id: find-deprecated-methods
language: Java
rule:
  kind: method_declaration
  has:
    kind: marker_annotation
    pattern: "@Deprecated"
```

```yaml
# 查找没有断言的 JUnit 测试方法
id: test-without-assertions
language: Java
rule:
  kind: method_declaration
  has:
    kind: marker_annotation
    pattern: "@Test"
  not:
    has:
      any:
        - pattern: assert$$$($$$)
        - pattern: assertEquals($$$)
        - pattern: assertTrue($$$)
```

### 字段声明

**⚠️ 关键陷阱**: 不能在修饰符位置使用元变量!

```yaml
# ❌ 失败 - $MOD 无法解析为有效语法
pattern: $MOD String $FIELD;

# ✅ 正确 - 使用结构定位
rule:
  kind: field_declaration
  has:
    field: type
    regex: ^String$
```

### 异常处理

```yaml
# 检测空 catch 块
id: empty-catch-block
language: Java
rule:
  kind: catch_clause
  has:
    pattern: |
      catch ($E) {
      }
```

### 空安全

```yaml
# 查找潜在的 NullPointerException
id: missing-null-check
language: Java
rule:
  pattern: $OBJ.$METHOD($$$)
  not:
    any:
      - inside:
          pattern: if ($OBJ != null) { $$$ }
      - inside:
          pattern: if (Objects.nonNull($OBJ)) { $$$ }
```

### Java AST 节点类型参考

**声明类**:
- `class_declaration`, `interface_declaration`, `enum_declaration`
- `method_declaration`, `field_declaration`, `constructor_declaration`
- `local_variable_declaration`

**语句类**:
- `try_statement`, `try_with_resources_statement`
- `if_statement`, `for_statement`, `enhanced_for_statement`
- `while_statement`, `synchronized_statement`
- `switch_expression`, `return_statement`, `throw_statement`

**表达式类**:
- `method_invocation`, `object_creation_expression`
- `lambda_expression`, `method_reference`
- `field_access`, `array_access`, `cast_expression`
- `instanceof_expression`, `ternary_expression`

**注解类**:
- `annotation` (带值的注解)
- `marker_annotation` (无值的注解如 @Override)

### Java 常见陷阱

1. **修饰符模式不工作**: `public static $TYPE $METHOD($$$)` 会产生 ERROR 节点
2. **注解破坏简单模式**: `String $FIELD;` 不匹配 `@NotNull String field;`
3. **泛型复杂性**: 简单模式可能无法匹配复杂泛型
4. **导入处理**: 需同时匹配 `@Test` 和 `@org.junit.Test`
5. **Lambda vs 方法引用**: 是不同的 AST 结构，需分别匹配

---

## 7. JSON 输出格式

### run --json=compact 返回结构

```json
[{
  "text": "@PostMapping(\"/login\")",
  "range": {
    "byteOffset": {"start": 1234, "end": 1260},
    "start": {"line": 65, "column": 4},
    "end": {"line": 65, "column": 30}
  },
  "file": "D:\\code\\...\\AuthController.java",
  "lines": "@PostMapping(\"/login\")\r",
  "charCount": {"leading": 0, "trailing": 1},
  "language": "Java",
  "metaVariables": {
    "single": {},
    "multi": {
      "ARGS": [{"text": "\"/login\"", "range": {...}}],
      "secondary": [{"text": "(\"/login\")", "range": {...}}]
    },
    "transformed": {}
  }
}]
```

### scan --json=compact 返回结构 (额外字段)

```json
[{
  "text": "@RequestMapping(\"/system/auth\")",
  "range": {"start": {"line": 44, "column": 0}, "end": {"line": 44, "column": 31}},
  "file": "D:\\code\\...\\AuthController.java",
  "metaVariables": {
    "multi": {"ARGS": [{"text": "\"/system/auth\""}]}
  },
  "ruleId": "class-level-mapping",
  "severity": "hint",
  "note": null,
  "message": "",
  "labels": [
    {"text": "@RequestMapping(\"/system/auth\")", "style": "primary"},
    {"text": "(\"/system/auth\")", "style": "secondary"}
  ]
}]
```

---

## 8. 调试与排错 (来自官方 Skill)

> 来源: [ast-grep/agent-skill SKILL.md](https://github.com/ast-grep/agent-skill/blob/main/ast-grep/skills/ast-grep/SKILL.md)

### --debug-query 检查代码结构

当规则不匹配时，用 `--debug-query` 理解 AST 结构：

```bash
# 查看目标代码的 CST (Concrete Syntax Tree - 含所有节点和标点)
ast-grep run --pattern 'async function example() { await fetch(); }' \
  --lang javascript \
  --debug-query=cst

# 查看 AST (Abstract Syntax Tree - 仅命名节点)
ast-grep run --pattern 'class User { constructor() {} }' \
  --lang javascript \
  --debug-query=ast

# 查看 ast-grep 如何解析你的模式
ast-grep run --pattern 'class $NAME { $$$BODY }' \
  --lang javascript \
  --debug-query=pattern
```

| format | 说明 |
|--------|------|
| `cst` | Concrete Syntax Tree (含所有节点和标点) |
| `ast` | Abstract Syntax Tree (仅命名节点) |
| `pattern` | 展示 ast-grep 对模式的解析结果 |
| `sexp` | S-expression 格式 |

### 规则不匹配时排错

1. **简化规则** — 先去掉子规则，只保留最基础的 pattern/kind
2. **加 `stopBy: end`** — 关系规则缺少此选项会导致不遍历完整子树
3. **用 `--debug-query` 查 AST** — 确认 kind 名称是否正确
4. **检查规则参数** — kind 值必须与 tree-sitter 节点名严格一致

### 开发方法论

1. **从简单开始**: pattern → kind → 关系规则 (has/inside) → 组合规则 (all/any/not)
2. **用正确的规则类型**:
   - Pattern: 直接代码匹配 (如 `console.log($ARG)`)
   - Kind + Relational: 复杂结构 (如 "包含 await 的函数声明")
   - Composite: 逻辑组合 (如 "有 await 但没有 try-catch")
3. **善用 AST 检查**: `--debug-query` 是最强大的调试工具

---

## 9. 实测验证

### 测试环境

- **ast-grep**: v0.43.0
- **项目**: ruoyi-vue-pro (Java Spring Boot, 5561 files)
- **平台**: Windows PowerShell 5.1

### 测试结果

| 命令 | 参数 | 结果 | 备注 |
|------|------|------|------|
| `run` | `--pattern "@GetMapping($$$ARGS)"` | ✅ 返回匹配 | 核心功能 |
| `run` | `--kind annotation` | ✅ 返回所有注解 | 输出量大 |
| `run` | `--kind method_declaration --globs "**/auth/**"` | ✅ 返回 auth 下方法 | glob 过滤有效 |
| `run` | `--files-with-matches` | ✅ 只返回文件路径 | 快速检查 |
| `run` | `--inspect=summary` | ✅ stderr 输出统计 | 调试用 |
| `run` | `--max-results` | ❌ 不支持 | 仅 scan 有 |
| `run` | `--debug-query=pattern` | ✅ 输出模式 AST | 调试用 |
| `scan` | `--rule <file>` | ✅ 成功返回 | 关系规则 |
| `scan` | `--inline-rules` | ⚠️ PowerShell 转义困难 | 建议用 --rule |
| `scan` | `--max-results 5` | ✅ 限制返回 5 条 | 大项目防爆 |
| `scan` | `inside: kind: class_body` | ❌ 不匹配 | 注解不在 class_body 内 |
| `scan` | `has: kind: annotation_argument_list` | ✅ 正确匹配 | 推荐方式 |

### 性能观察

- `run` 命令在 5561 文件项目上扫描约 1-3 秒
- `--threads 0` 自动多线程，大项目推荐使用
- `--globs` 过滤可显著减少扫描时间
- `--json=compact` 输出最高效 (单行 JSON)

---

## 10. 最佳实践

### 1. 始终使用 --json 进行分析

```bash
# 获取结构化输出供脚本解析
ast-grep run -p 'console.log($A)' -l javascript --json=compact
ast-grep scan --json=compact
```

### 2. 选择正确的工具

| 场景 | 推荐命令 |
|------|---------|
| 快速一次性搜索 | `ast-grep run` + `--json` |
| 周期性检查 | `ast-grep scan` + 规则文件 |
| 代码重构 | `ast-grep run` + `--rewrite` + `-U` |
| CI/CD 集成 | `ast-grep scan` + JSON 输出 |

### 3. 利用关系规则

```yaml
# 查找不在 try-catch 内的 console.log
rule:
  pattern: console.log($A)
  not:
    inside:
      pattern: try { $$$ } catch ($E) { $$$ }
```

### 4. 测试规则再部署

```yaml
# rule-test.yml
id: no-console-log
testCases:
  - id: should-match
    match: console.log("test")
  - id: should-not-match
    match: logger.info("test")
```

### 5. 理解语言特定模式

```yaml
# JavaScript: snake_case 和 camelCase 是不同的
pattern: my_function()  # 不匹配 myFunction()

# Python: 缩进决定代码块
pattern: |
  if $COND:
      $BODY
```

### 6. 常见陷阱

| 陷阱 | 错误 | 正确 |
|------|------|------|
| 元变量名 | `$myVar` (小写) | `$MY_VAR` (大写) |
| 语言不匹配 | Python 语法搜 JS | 使用目标语言语法 |
| stdin 缺少 --lang | `echo "code" \| ast-grep -p 'pattern'` | 加 `--stdin -l python` |
| 过于宽泛的模式 | `$A` (匹配一切) | `if ($COND) { $BODY }` |
| 关系规则缺少 stopBy | 无限搜索 (慢且不精确) | 加 `stopBy: end` |

### 7. 有用标志速查

| 标志 | 用途 | 示例 |
|------|------|------|
| `-p, --pattern` | 搜索模式 | `-p 'console.log($A)'` |
| `-r, --rewrite` | 替换代码 | `-r 'logger.info($A)'` |
| `-l, --lang` | 指定语言 | `-l javascript` |
| `--json` | 机器可读输出 | `--json=compact` |
| `-U, --update-all` | 应用所有改写 | `-U` |
| `--stdin` | 从 stdin 读取 | `--stdin` |
| `--debug-query` | 调试模式匹配 | `--debug-query=ast` |
| `-j, --threads` | 控制并行度 | `-j 4` |
| `-i, --interactive` | 手动审查 | ⚠️ 需要人工输入 |

---

## 11. 官方文档索引

> **以下链接可通过 Playwright 进一步爬取，获取更详细的内容。**
> 
> 爬取方法: `browser_navigate(url)` → `browser_query(selector="article", mode="text")` 或 `webfetch(url, format="text")`

### Guide (指南)

| 页面 | URL | 内容 | 已爬取 |
|------|-----|------|--------|
| Quick Start | https://ast-grep.github.io/guide/quick-start.html | 安装、模式、改写入门 | ✅ |
| Pattern Syntax | https://ast-grep.github.io/guide/pattern-syntax.html | 模式语法详解 | ✅ |
| Rule Essentials | https://ast-grep.github.io/guide/rule-config.html | 规则系统概述 | ✅ |
| Atomic Rule | https://ast-grep.github.io/guide/rule-config/atomic-rule.html | pattern/kind/regex/nthChild/range | ✅ |
| Relational Rule | https://ast-grep.github.io/guide/rule-config/relational-rule.html | inside/has/follows/precedes | ✅ |
| Composite Rule | https://ast-grep.github.io/guide/rule-config/composite-rule.html | all/any/not/matches | ❌ 待爬取 |
| Utility Rule | https://ast-grep.github.io/guide/rule-config/utility-rule.html | 可复用规则定义 | ❌ 待爬取 |
| Project Setup | https://ast-grep.github.io/guide/scan-project.html | 项目配置和扫描 | ❌ 待爬取 |
| Project Configuration | https://ast-grep.github.io/guide/project/project-config.html | sgconfig.yml 详解 | ❌ 待爬取 |
| Lint Rule | https://ast-grep.github.io/guide/project/lint-rule.html | Lint 规则编写 | ❌ 待爬取 |
| Test Your Rule | https://ast-grep.github.io/guide/test-rule.html | 规则测试 | ❌ 待爬取 |
| Error Report | https://ast-grep.github.io/guide/project/severity.html | 错误报告配置 | ❌ 待爬取 |
| Rewrite Code | https://ast-grep.github.io/guide/rewrite-code.html | 代码改写 | ❌ 待爬取 |
| Transform Code | https://ast-grep.github.io/guide/rewrite/transform.html | 代码转换 | ❌ 待爬取 |
| Rewriter Rule | https://ast-grep.github.io/guide/rewrite/rewriter.html | 改写器规则 | ❌ 待爬取 |
| Tooling Overview | https://ast-grep.github.io/guide/tooling-overview.html | 工具链概述 | ❌ 待爬取 |
| Editor Integration | https://ast-grep.github.io/guide/tools/editors.html | 编辑器集成 | ❌ 待爬取 |
| JSON mode | https://ast-grep.github.io/guide/tools/json.html | JSON 输出详解 | ❌ 待爬取 |
| API Usage | https://ast-grep.github.io/guide/api-usage.html | API 使用概述 | ❌ 待爬取 |
| JavaScript API | https://ast-grep.github.io/guide/api-usage/js-api.html | JS API 参考 | ❌ 待爬取 |
| Python API | https://ast-grep.github.io/guide/api-usage/py-api.html | Python API 参考 | ❌ 待爬取 |
| Performance Tip | https://ast-grep.github.io/guide/api-usage/performance-tip.html | 性能优化 | ❌ 待爬取 |

### Reference (参考)

| 页面 | URL | 内容 | 已爬取 |
|------|-----|------|--------|
| CLI Reference | https://ast-grep.github.io/reference/cli.html | CLI 完整参考 | ❌ 待爬取 |
| ast-grep run | https://ast-grep.github.io/reference/cli/run.html | run 命令详解 | ❌ 待爬取 |
| ast-grep scan | https://ast-grep.github.io/reference/cli/scan.html | scan 命令详解 | ❌ 待爬取 |
| ast-grep test | https://ast-grep.github.io/reference/cli/test.html | test 命令详解 | ❌ 待爬取 |
| ast-grep new | https://ast-grep.github.io/reference/cli/new.html | new 命令详解 | ❌ 待爬取 |
| Project Config | https://ast-grep.github.io/reference/sgconfig.html | sgconfig.yml 参考 | ❌ 待爬取 |
| Rule Config | https://ast-grep.github.io/reference/yaml.html | YAML 规则参考 | ❌ 待爬取 |
| fix | https://ast-grep.github.io/reference/yaml/fix.html | fix 字段详解 | ❌ 待爬取 |
| transformation | https://ast-grep.github.io/reference/yaml/transformation.html | 转换规则 | ❌ 待爬取 |
| rewriter | https://ast-grep.github.io/reference/yaml/rewriter.html | 改写器规则 | ❌ 待爬取 |
| Rule Object | https://ast-grep.github.io/reference/rule.html | 规则对象完整参考 | ❌ 待爬取 |
| ESQuery Style Kind | https://ast-grep.github.io/reference/rule/esquery.html | ESQuery 选择器 | ❌ 待爬取 |
| API Reference | https://ast-grep.github.io/reference/api.html | API 完整参考 | ❌ 待爬取 |
| Language List | https://ast-grep.github.io/reference/languages.html | 支持语言列表 | ❌ 待爬取 |

### Cheat Sheet (速查表)

| 页面 | URL | 内容 | 已爬取 |
|------|-----|------|--------|
| Rule Cheat Sheet | https://ast-grep.github.io/cheatsheet/rule.html | 规则速查表 | ❌ 待爬取 |
| Config Cheat Sheet | https://ast-grep.github.io/cheatsheet/yaml.html | 配置速查表 | ❌ 待爬取 |

### Advanced Topics (高级主题)

| 页面 | URL | 内容 | 已爬取 |
|------|-----|------|--------|
| Using ast-grep with AI | https://ast-grep.github.io/advanced/prompting.html | AI 集成指南 | ❌ 待爬取 |
| FAQ | https://ast-grep.github.io/advanced/faq.html | 常见问题 | ❌ 待爬取 |
| How ast-grep Works | https://ast-grep.github.io/advanced/how-ast-grep-works.html | 内部工作原理 | ❌ 待爬取 |
| Core Concepts | https://ast-grep.github.io/advanced/core-concepts.html | 核心概念 | ❌ 待爬取 |
| Pattern Syntax (Advanced) | https://ast-grep.github.io/advanced/pattern-parse.html | 模式解析深入 | ❌ 待爬取 |
| Pattern Match Algorithm | https://ast-grep.github.io/advanced/match-algorithm.html | 匹配算法 | ❌ 待爬取 |
| How Rewrite Works | https://ast-grep.github.io/advanced/find-n-patch.html | 改写原理 | ❌ 待爬取 |
| Custom Language | https://ast-grep.github.io/advanced/custom-language.html | 自定义语言 | ❌ 待爬取 |
| Multi-Language Documents | https://ast-grep.github.io/advanced/language-injection.html | 多语言文档 | ❌ 待爬取 |
| Comparison | https://ast-grep.github.io/advanced/tool-comparison.html | 与其他工具对比 | ❌ 待爬取 |

### Examples (示例)

| 页面 | URL | 内容 | 已爬取 |
|------|-----|------|--------|
| Pattern Catalog | https://ast-grep.github.io/catalog.html | 模式目录总览 | ❌ 待爬取 |
| Java Examples | https://ast-grep.github.io/catalog/java/ | Java 模式示例 | ❌ 待爬取 |
| Python Examples | https://ast-grep.github.io/catalog/python/ | Python 模式示例 | ❌ 待爬取 |
| TypeScript Examples | https://ast-grep.github.io/catalog/typescript/ | TypeScript 模式示例 | ❌ 待爬取 |
| Go Examples | https://ast-grep.github.io/catalog/go/ | Go 模式示例 | ❌ 待爬取 |
| Rust Examples | https://ast-grep.github.io/catalog/rust/ | Rust 模式示例 | ❌ 待爬取 |

### 其他资源

| 资源 | URL |
|------|-----|
| Online Playground | https://ast-grep.github.io/playground.html |
| GitHub Repository | https://github.com/ast-grep/ast-grep |
| Mastering ast-grep (Book) | https://leanpub.com/ast-grep |
| VSCode Extension | https://marketplace.visualstudio.com/items?itemName=ast-grep.ast-grep-vscode |
| Discord | https://discord.com/invite/4YZjf6htSQ |
| StackOverflow | https://stackoverflow.com/questions/tagged/ast-grep |
| Reddit | https://www.reddit.com/r/astgrep/ |
| Docs.rs (Rust API) | https://docs.rs/ast-grep-core/latest/ast_grep_core/ |
| Codemod Studio | https://app.codemod.com/studio |

---

**文档版本**: 1.1  
**最后更新**: 2026-06-05  
**作者**: Sisyphus (AI Agent)  
**数据来源**: 官方文档 + CLI --help + 实测验证 + [官方 Agent SKILL.md](https://github.com/ast-grep/agent-skill/blob/main/ast-grep/skills/ast-grep/SKILL.md)
