# 正向审计工具推荐

> 本文档评估实现真正的正向污点审计所需的工具链，并给出推荐方案。

---

## 一、正向审计的核心需求

真正的正向审计需要追踪**参数从端点到 sink 的完整传播路径**，分析每层函数的消毒处理，评估安全风险。

### 核心能力矩阵

| 能力 | 说明 | 示例 |
|------|------|------|
| **参数提取** | 识别端点的用户可控参数 | `@RequestParam String name` |
| **参数追踪** | 追踪参数在函数间的传播 | `search(name)` → `buildQuery(name)` → `execute(query)` |
| **消毒识别** | 识别参数路径上的消毒操作 | `name.matches("\\d+")` |
| **消毒评估** | 评估消毒对特定 sink 是否有效 | 数字白名单对 SQL 有效，对 CMD 无效 |
| **风险判定** | 基于完整路径判定风险等级 | 无消毒直接到达 sink → CRITICAL |

### 示例：TC01 SQL 注入

```java
@GetMapping("/search")
public List<Map<String, Object>> search(@RequestParam String name) {
    return jdbcTemplate.query(
        "SELECT * FROM users WHERE name = '" + name + "'",
        new BeanPropertyRowMapper<>(User.class)
    );
}
```

**正向审计过程**：
```
Layer 0: search(name)                    ← 参数进入
Layer 1: "SELECT ... '" + name + "'"     ← 直接拼接到 SQL（无消毒）
Layer 2: jdbcTemplate.query(sql, ...)    ← 到达 SQL sink
结论: VULNERABLE (SQL Injection, CRITICAL)
```

### 示例：TC02 SQL 注入（已消毒）

```java
@GetMapping("/user")
public Map<String, Object> getUser(@RequestParam String id) {
    if (!id.matches("\\d+")) {
        throw new IllegalArgumentException("Invalid ID");
    }
    return jdbcTemplate.queryForMap("SELECT * FROM users WHERE id = " + id);
}
```

**正向审计过程**：
```
Layer 0: getUser(id)                     ← 参数进入
Layer 1: id.matches("\\d+")             ← 消毒：正则白名单（只允许数字）
Layer 2: "SELECT ... id = " + id        ← 拼接到 SQL
Layer 3: jdbcTemplate.queryForMap(sql)   ← 到达 SQL sink
消毒评估: matches("\\d+") 对 SQL sink 有效（数字无法注入）
结论: SANITIZED (有效消毒)
```

---

## 二、工具需求 vs 当前状态

| 需求 | 工具 | 当前状态 | 能力评估 |
|------|------|---------|---------|
| **AST 解析** | ast-grep | ✅ 已安装 (v0.43) | 能解析 Java AST，提取函数调用、参数 |
| **调用图** | codegraph | ✅ 已安装 (v0.9.9) | 能查询 callers/callees，构建调用链 |
| **类型信息** | LSP (jdtls) | ⚠️ 部分可用 | 需要项目编译，提供类型推断 |
| **数据流追踪** | ❌ 无专用工具 | ❌ **缺失** | 需要自己实现 |
| **消毒模式库** | ❌ 无专用库 | ❌ **缺失** | 需要自己定义 |

### 当前工具能力详解

#### ast-grep（✅ 满足）

```bash
# 提取函数调用
ast-grep --pattern '$FUNC($$$ARGS)' --lang java

# 提取参数
ast-grep --pattern 'jdbcTemplate.query($SQL, $$$)' --lang java

# 提取变量赋值
ast-grep --pattern '$VAR = $EXPR' --lang java
```

- ✅ 解析函数调用和参数
- ✅ 识别字符串拼接
- ✅ 提取条件判断
- ✅ 匹配消毒模式
- ❌ 不能跨文件追踪（需要配合 codegraph）
- ❌ 不能做类型推断（需要 LSP）

#### codegraph（✅ 满足）

```bash
# 查询函数的调用者
codegraph callers "Tc01Controller.search" -j

# 查询函数调用的子函数
codegraph callees "Tc01Controller.search" -j
```

- ✅ 构建完整的调用链
- ✅ 跨文件追踪
- ✅ 识别间接调用
- ❌ 不能追踪参数传递（只知道函数调用关系）
- ❌ 不能识别动态调用（反射、接口）

#### LSP / jdtls（⚠️ 部分满足）

- ✅ 提供类型推断
- ✅ 识别接口实现
- ✅ 解析泛型
- ⚠️ 需要项目编译（`mvn compile`）
- ⚠️ 对于大型项目启动慢

#### 数据流追踪（❌ 缺失）

需要自己实现：
```python
def trace_parameter(param_name, function_body, sink_pattern):
    """追踪参数在函数体中的传播路径"""
    path = []
    uses = find_parameter_uses(param_name, function_body)
    for use in uses:
        if is_sink_call(use, sink_pattern):
            path.append((use.line, "sink_call", False))
        elif is_sanitizer(use):
            path.append((use.line, "sanitizer", True))
        elif is_string_concat(use):
            path.append((use.line, "concat", False))
        elif is_function_call(use):
            path.append((use.line, "function_call", False))
    return path
```

#### 消毒模式库（❌ 缺失）

需要自己定义：
```python
SANITIZER_PATTERNS = {
    "SQL": [
        (r'PreparedStatement', "STRONG", "parameterized query"),
        (r'\.setString\(', "STRONG", "parameterized query"),
        (r'\.matches\("\\d\+"\)', "STRONG", "numeric whitelist"),
        (r'StringEscapeUtils\.escapeSql\(', "STRONG", "SQL escape"),
    ],
    "CMD": [
        (r'\.matches\("[a-zA-Z0-9.]+"\)', "STRONG", "hostname whitelist"),
        (r'Runtime\.exec\(new String\[\]', "STRONG", "argument array"),
    ],
    "FileOps": [
        (r'\.getCanonicalPath\(\)', "STRONG", "canonical path"),
        (r'\.matches\("[a-zA-Z0-9_.-]+"\)', "STRONG", "filename whitelist"),
    ],
}
```

---

## 三、推荐工具

### 3.1 Java 静态分析框架

#### Soot ⭐⭐⭐⭐⭐（最推荐）

| 属性 | 说明 |
|------|------|
| **类型** | 开源 Java 优化和分析框架 |
| **GitHub** | https://github.com/soot-oss/soot |
| **许可证** | LGPL 2.1 |
| **历史** | 20+ 年，社区活跃 |

**能力**：
- ✅ 完整的污点分析（taint analysis）
- ✅ 跨过程数据流追踪
- ✅ 支持 Jimple IR（中间表示）
- ✅ 支持 Android APK 分析
- ✅ 支持 Java 8-17

**优点**：成熟稳定，文档丰富，社区活跃
**缺点**：学习曲线陡峭，性能较慢（大型项目需要几分钟）

**示例**：
```java
// 定义 source 和 sink
Scene.v().addBasicClass("javax.servlet.http.HttpServletRequest", Scene.SIGNATURES);

// 运行污点分析
FlowDroidAnalysis analysis = new FlowDroidAnalysis();
analysis.run();
```

---

#### Wala ⭐⭐⭐⭐

| 属性 | 说明 |
|------|------|
| **类型** | 开源 Java 分析框架（IBM 开发） |
| **GitHub** | https://github.com/wala/WALA |
| **许可证** | EPL 2.0 |

**能力**：
- ✅ 指针分析（pointer analysis）
- ✅ 数据流分析
- ✅ 调用图构建
- ✅ 支持 Android

**优点**：精度高（context-sensitive），支持增量分析，比 Soot 更快
**缺点**：文档较少，API 复杂

**示例**：
```java
// 构建调用图
CallGraph cg = CallGraphBuilder.build(options);

// 数据流分析
DataDependenceGraph ddg = DataDependenceGraph.make(cg, method);
```

---

#### FlowDroid ⭐⭐⭐⭐⭐（专为安全审计设计）

| 属性 | 说明 |
|------|------|
| **类型** | 基于 Soot 的污点分析工具 |
| **GitHub** | https://github.com/secure-software-engineering/FlowDroid |
| **许可证** | LGPL 2.1 |

**能力**：
- ✅ 专为 Android/Java 污点分析设计
- ✅ 支持 source/sink XML 定义
- ✅ 支持自定义 taint wrapper
- ✅ 高精度（context-sensitive, flow-sensitive）

**优点**：开箱即用，支持 XML 配置 source/sink，性能优化好
**缺点**：依赖 Soot，主要针对 Android

**示例**：
```xml
<!-- sourcesAndSinks.xml -->
<sinkSources>
  <category id="SQL_INJECTION">
    <method signature="&lt;java.sql.Statement: java.sql.ResultSet executeQuery(java.lang.String)&gt;">
      <base type="ANY_TYPE" />
      <param index="0" type="java.lang.String" />
    </method>
  </category>
</sinkSources>
```

---

### 3.2 轻量级工具（快速实现）

#### JavaParser ⭐⭐⭐⭐

| 属性 | 说明 |
|------|------|
| **类型** | 开源 Java AST 解析器 |
| **GitHub** | https://github.com/javaparser/javaparser |
| **许可证** | LGPL / Apache 2.0 |

**能力**：
- ✅ 解析 Java 源码为 AST
- ✅ 访问者模式遍历 AST
- ✅ 修改和生成代码
- ✅ 支持 Java 17

**优点**：轻量级，易于集成，API 友好
**缺点**：只做语法分析，不做数据流分析，需要自己实现追踪逻辑

**示例**：
```java
// 解析 Java 文件
CompilationUnit cu = StaticJavaParser.parse(new File("Test.java"));

// 查找所有方法调用
cu.findAll(MethodCallExpr.class).forEach(call -> {
    System.out.println("Call: " + call.getName());
});
```

---

#### Tree-sitter + tree-sitter-java ⭐⭐⭐

| 属性 | 说明 |
|------|------|
| **类型** | 增量解析器生成器 |
| **GitHub** | https://github.com/tree-sitter/tree-sitter-java |
| **许可证** | MIT |

**能力**：
- ✅ 快速解析（增量更新）
- ✅ 支持 100+ 语言
- ✅ 容错解析（处理语法错误）

**优点**：极快的解析速度，支持多语言，可用于编辑器插件
**缺点**：只做语法分析，需要自己实现分析逻辑

**示例**：
```python
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

JAVA_LANGUAGE = Language(tsjava.language())
parser = Parser(JAVA_LANGUAGE)

tree = parser.parse(bytes(source_code, "utf8"))
```

---

### 3.3 商业工具（企业级）

#### Fortify SCA ⭐⭐⭐⭐⭐

| 属性 | 说明 |
|------|------|
| **类型** | 商业静态应用安全测试（SAST） |
| **官网** | https://www.microfocus.com/en-us/cyberres/application-security/static-code-analyzer |
| **价格** | $50k+/年 |

**能力**：
- ✅ 完整的污点分析
- ✅ 支持 30+ 语言
- ✅ 丰富的漏洞规则库
- ✅ 企业级报告

**优点**：精度高，误报率低，支持大规模项目，合规性报告
**缺点**：价格昂贵，需要许可证

---

#### Checkmarx CxSAST ⭐⭐⭐⭐

| 属性 | 说明 |
|------|------|
| **类型** | 商业 SAST 工具 |
| **官网** | https://checkmarx.com/product/cxsast-source-code-scanning/ |
| **价格** | 企业定价 |

**能力**：
- ✅ 增量分析
- ✅ 数据流追踪
- ✅ 支持 CI/CD 集成

**优点**：扫描速度快，支持 IDE 插件，可定制规则
**缺点**：价格昂贵

---

#### Semgrep ⭐⭐⭐⭐（开源 + 商业）

| 属性 | 说明 |
|------|------|
| **类型** | 开源规则引擎 + 商业云服务 |
| **GitHub** | https://github.com/returntocorp/semgrep |
| **许可证** | LGPL 2.1（开源部分） |

**能力**：
- ✅ 基于模式的代码分析
- ✅ 支持自定义规则（YAML）
- ✅ 跨文件分析（实验性）
- ✅ 数据流分析（实验性）

**优点**：开源版本免费，规则编写简单，社区活跃
**缺点**：数据流分析能力有限，跨过程分析较弱

**示例**：
```yaml
rules:
  - id: sql-injection
    patterns:
      - pattern: |
          $QUERY = "SELECT ... " + $USER_INPUT
          ...
          $STMT.executeQuery($QUERY)
    message: Potential SQL injection
    severity: ERROR
```

---

## 四、工具对比矩阵

| 工具 | 类型 | 污点分析 | 跨过程 | 数据流 | 性能 | 学习曲线 | 价格 |
|------|------|---------|--------|--------|------|---------|------|
| **Soot** | 开源 | ✅ 完整 | ✅ | ✅ | 慢 | 高 | 免费 |
| **Wala** | 开源 | ✅ 完整 | ✅ | ✅ | 中 | 高 | 免费 |
| **FlowDroid** | 开源 | ✅ 完整 | ✅ | ✅ | 中 | 中 | 免费 |
| **JavaParser** | 开源 | ❌ | ❌ | ❌ | 快 | 低 | 免费 |
| **Tree-sitter** | 开源 | ❌ | ❌ | ❌ | 极快 | 低 | 免费 |
| **Semgrep** | 开源+商业 | ⚠️ 实验 | ⚠️ 实验 | ⚠️ 实验 | 快 | 低 | 免费/付费 |
| **Fortify** | 商业 | ✅ 完整 | ✅ | ✅ | 中 | 中 | $50k+/年 |
| **Checkmarx** | 商业 | ✅ 完整 | ✅ | ✅ | 快 | 中 | 企业定价 |

---

## 五、推荐方案

### 方案 A：快速实现（1-2 天）

**工具组合**：
- **JavaParser**（AST 解析）
- **codegraph**（调用图）
- **自定义规则引擎**（消毒识别）

**核心算法**：
```python
def forward_audit(endpoint, project_path):
    # 1. 提取端点参数
    params = extract_params(endpoint, project_path)

    # 2. 构建调用链
    call_chain = build_call_chain(endpoint, project_path)

    # 3. 对于每个参数
    for param in params:
        # 追踪参数在调用链中的传播
        propagation = trace_param_propagation(param, call_chain, project_path)

        # 评估风险
        risk = evaluate_risk(propagation)
        report.append(risk)

    return report

def trace_param_propagation(param, call_chain, project_path):
    """追踪参数在调用链中的传播"""
    propagation = []

    for func in call_chain:
        func_body = extract_function_body(func, project_path)
        uses = find_param_uses(param.name, func_body)

        for use in uses:
            if is_sink(use):
                propagation.append({"type": "sink", "sink_type": get_sink_type(use), "sanitized": False})
            elif is_sanitizer(use):
                propagation.append({"type": "sanitizer", "pattern": get_sanitizer_pattern(use), "sanitized": True})
            elif is_function_call(use):
                sub_chain = build_call_chain(use.func, project_path)
                sub_prop = trace_param_propagation(param, sub_chain, project_path)
                propagation.extend(sub_prop)

    return propagation
```

**适用场景**：简单项目（< 100 个文件），快速原型验证，教学演示
**预期覆盖率**：80%

---

### 方案 B：专业实现（1-2 周）

**工具组合**：
- **FlowDroid**（污点分析）
- **Soot**（底层框架）
- **自定义 source/sink 规则**

**核心流程**：
```
1. 定义 source/sink XML 配置
   - source: @RequestParam, @RequestBody, @PathVariable, HttpServletRequest.getParameter()
   - sink: Statement.execute, Runtime.exec, Files.write, URL.openStream, ...

2. 配置 FlowDroid
   - 设置入口点（Spring Controller 方法）
   - 设置 taint wrapper（处理 String.concat, StringBuilder.append 等）
   - 设置分析选项（context-sensitive, flow-sensitive）

3. 运行分析
   - FlowDroid 自动追踪参数从 source 到 sink 的完整路径
   - 输出所有 taint flow（source → ... → sink）

4. 后处理
   - 过滤误报（检查消毒操作）
   - 评估风险等级
   - 生成报告
```

**适用场景**：中大型项目，高精度要求，生产环境
**预期覆盖率**：95%+

---

### 方案 C：企业级实现（采购）

**工具**：Fortify SCA 或 Checkmarx

**适用场景**：企业级项目，合规性要求，预算充足
**预期覆盖率**：98%+

---

## 六、当前项目的建议

### javatestcase 项目（10 个测试用例）

**推荐**：方案 A（JavaParser + codegraph）

**理由**：
- 项目规模小（38 个 Java 文件）
- 测试用例设计简单，参数传播路径清晰
- 1-2 天即可实现，快速验证效果

### ruoyi-vue-pro 项目（5000+ 文件）

**推荐**：方案 B（FlowDroid）

**理由**：
- 项目规模大，调用关系复杂
- 需要跨模块、跨文件的参数追踪
- 需要高精度的消毒评估

### WebGoat 项目（370 个文件）

**推荐**：方案 A 或 B 均可

**理由**：
- 项目规模中等
- 漏洞设计明确，但调用链可能较深（TC06 SSRF 有 11 层）
- 方案 A 可覆盖 80%，方案 B 可覆盖 95%+

---

## 七、实施路线图

```
Phase 1: 快速原型（1-2 天）
├── 用 ast-grep 提取端点参数
├── 用 codegraph 构建调用链
├── 用正则匹配消毒模式
└── 用规则引擎评估风险

Phase 2: 精度提升（1-2 周）
├── 集成 FlowDroid 污点分析
├── 定义 source/sink XML 配置
├── 实现自定义 taint wrapper
└── 优化误报过滤

Phase 3: 生产就绪（2-4 周）
├── 支持增量分析
├── 实现 CI/CD 集成
├── 生成合规性报告
└── 性能优化
```
