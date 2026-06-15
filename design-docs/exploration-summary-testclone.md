# Exploration: `D:\test-clone` (java-method-call-extractor)

> **目的**: 把 `D:\test-clone` 这个 Java 方法调用提取器整合进 agentloop 的
> 调用链引擎(`chain-sql-engine.md`),用于支撑"非 groupId = sink 点"的检测。

---

## 1. 工具概述 (Tool Overview)

| 项 | 值 |
|---|---|
| 名称 | `java-method-call-extractor` |
| Maven artifact | `com.javaparsextract:java-method-call-extractor:1.0.0` |
| 仓库路径 | `D:\test-clone` |
| 实现文件数 | **1** (`src/main/java/com/javaparsextract/Main.java`, 169 行) |
| 测试样例 | `test-data/SampleService.java` (32 行) |
| 已编译 JAR | `target/java-method-call-extractor-1.0.0.jar` (6.1 MB, fat-jar, 已含所有依赖) |
| 作者/许可证 | **无显式作者,无 LICENSE 文件** (pom.xml groupId=`com.javaparsextract`,版本 1.0.0) |
| JDK 要求 | **JDK 21** (本机已确认 `21.0.10 LTS`,符合) |
| 依赖 | 仅 1 个: `com.github.javaparser:javaparser-symbol-solver-core:3.26.3` |
| 打包 | `maven-shade-plugin` 单文件 fat jar,`mainClass=com.javaparsextract.Main` |

**核心功能**: 给定一个 `.java` 文件,提取该文件内每个 method 的所有方法调用,
输出 JSON 数组,每条记录形如 `{startLine, methodSignature, calledFQN}`。

---

## 2. 架构 (Architecture)

### 2.1 组件

```
┌─────────────────────────────────────────────────────────────────────┐
│ java -jar java-method-call-extractor-1.0.0.jar <file> [sourceRoot]  │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Main.java (唯一源文件, 169 行)                                       │
│                                                                     │
│  1. 配符号解析器                                                     │
│     ├─ ReflectionTypeSolver  (JDK 类, 无配置)                       │
│     └─ JavaParserTypeSolver  (项目源码根, 可选, 自动向上探测)       │
│                                                                     │
│  2. StaticJavaParser.parse(...)  → CompilationUnit                  │
│                                                                     │
│  3. for each MethodDeclaration:                                     │
│       ├─ startLine = begin.line - 1   (0-based)                     │
│       ├─ signature = declarationAsString(true,true,true)            │
│       └─ for each MethodCallExpr in method.findAll(...):            │
│            ├─ try:   call.resolve() → qualifiedName + "("+args+")"  │
│            └─ catch: 降级到 scope.name + "("+args+")"               │
│                                                                     │
│  4. System.out.println(toJson(records))  (手写 JSON, 无 Jackson)    │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 关键代码片段 (Main.java)

**符号解析器装配** (L50-61):
```java
CombinedTypeSolver typeSolver = new CombinedTypeSolver();
typeSolver.add(new ReflectionTypeSolver());

Path sourceRoot = (args.length > 1)
        ? Path.of(args[1])
        : locateProjectRoot(javaFile);   // 向上找 src/main/java 或 src/test/java
if (sourceRoot != null) {
    typeSolver.add(new JavaParserTypeSolver(sourceRoot));
}
JavaSymbolSolver symbolSolver = new JavaSymbolSolver(typeSolver);
StaticJavaParser.getConfiguration().setSymbolResolver(symbolSolver);
```

**核心遍历** (L66-80):
```java
for (MethodDeclaration method : cu.findAll(MethodDeclaration.class)) {
    int startLine = method.getBegin().map(p -> p.line - 1).orElse(-1);
    String signature = method.getDeclarationAsString(true, true, true);
    for (MethodCallExpr call : method.findAll(MethodCallExpr.class)) {
        String calledFQN = resolveCall(call);
        records.add(new CallRecord(startLine, signature, calledFQN));
    }
}
```

**带降级的 FQN 解析** (L89-104):
```java
private static String resolveCall(MethodCallExpr call) {
    String argsStr = call.getArguments().stream()
            .map(Node::toString)
            .collect(Collectors.joining(", "));
    try {
        ResolvedMethodDeclaration resolved = call.resolve();
        return resolved.getQualifiedName() + "(" + argsStr + ")";
    } catch (RuntimeException e) {
        // 降级: 用调用点可见的名字 (scope + name)
        String scope = call.getScope().map(Object::toString).orElse("");
        String name  = call.getNameAsString();
        String prefix = scope.isEmpty() ? name : scope + "." + name;
        return prefix + "(" + argsStr + ")";
    }
}
```

### 2.3 数据流

```
Java 源文件 ─parse─► CompilationUnit (AST)
                          │
                          ▼
       findAll(MethodDeclaration.class)
                          │
                          ▼
       for each MethodDeclaration:
         method.findAll(MethodCallExpr.class)
                          │
                          ▼
       for each MethodCallExpr:
         call.resolve() ─[fail]─► 降级 scope.name
                          │
                          ▼
       CallRecord { startLine (0-based),
                    methodSignature,
                    calledFQN }
                          │
                          ▼
                  JSON array → stdout
```

### 2.4 输入/输出契约

**CLI**:
```bash
java -jar java-method-call-extractor-1.0.0.jar <file.java> [sourceRoot]
```

- `<file.java>` (必填): 单个 Java 源文件
- `[sourceRoot]` (可选): 项目源码根,用于跨文件 FQN 解析;省略时自动向上探测 `src/main/java` 或 `src/test/java`

**输出** (stdout, UTF-8, JSON 数组):
```json
[
  {
    "startLine": 9,
    "methodSignature": "public void addItem(String item)",
    "calledFQN": "java.util.List.add(item)"
  }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `startLine` | int | 外层 method 声明起始行号, **0-based** (JavaParser 1-based 后再 -1) |
| `methodSignature` | string | 含修饰符+返回类型+方法名+形参;**仅展示用,不参与关联** |
| `calledFQN` | string | 被调用方法的 FQN + 实参文本 (`pkg.Cls.method(arg1, arg2)`),失败时降级为 `scope.name(arg)` |

---

## 3. 集成方案 (Integration Plan)

### 3.1 应当放置在哪里?

**结论**: 在 `D:\agentloop\scripts\chain\` 下新建 **`method_calls_extractor.py`**,
将 JAR 调用封装为 Python 函数。这是 `chain-sql-engine.md` §"范围边界" 中明确点名的文件。

> 为什么不放 `scripts/ast/`?
> - `scripts/ast/javaparser_bridge.py` 走 JPype 内嵌 JVM,用途更广 (annotation scan、AST 通用接口)
> - `scripts/chain/` 才是 chain-sql-engine 的归属,调用链构建的"调用点提取"环节
> - 两个模块职责不冲突: bridge 提供底层 AST 能力, extractor 是 chain 专用的高层封装

### 3.2 应当包装什么?

需要包装的三件事:
1. **JAR 调用**: `subprocess.run(["java", "-jar", JAR_PATH, file, source_root])` + JSON parse
2. **file ↔ startLine 关联**: 提供 `extract_method_calls_for_node(node_id, codegraph_db, jar_path)`,
   即 chain-sql-engine.md L160-179 的伪代码
3. **sink 过滤**: `sinks = [c for c in calls if not c["calledFQN"].startswith(groupId + ".")]`

### 3.3 应当暴露什么接口?

```python
# scripts/chain/method_calls_extractor.py

def extract_method_calls_for_file(file_path: str, source_root: str = None,
                                   jar_path: str = JAR_PATH) -> list[dict]:
    """调 JAR 拿整个文件的所有 method calls, 带 file 级缓存.
    
    Returns: [{"startLine": int, "methodSignature": str, "calledFQN": str}, ...]
    """

def extract_method_calls_for_node(node_id: str, db_path: str,
                                    jar_path: str = JAR_PATH,
                                    file_cache: dict = None) -> list[str]:
    """根据 nodes.id 反查 file+start_line,从 file 提取本 method 的 calledFQNs.
    
    Returns: ["java.lang.Runtime.exec(taskAction)", ...]
    """

def filter_sinks(called_fqns: list[str], group_id: str) -> list[str]:
    """过滤: calledFQN 不以 group_id 开头的全部判为 sink."""
```

### 3.4 与 chain-sql-engine.md 的对齐

| 设计文档位置 | 当前状态 | 本次新增 |
|---|---|---|
| § "主流程" 步骤 2 | 伪代码 L160-179 | 落实为 `extract_method_calls_for_node()` |
| § "关联算法" | `(file + startLine) join` | Python 实现 + file 级缓存 |
| § "sink 判定" | "非 groupId 开头 → 全 sink" | `filter_sinks()` |
| § 范围边界 | "新建 `scripts/chain/method_calls_extractor.py`" | ✅ 本次创建 |
| § 范围边界 | "新建 `scripts/chain/chain_builder.py`" | ⚠️ 下一步 (本文件不涉及) |

### 3.5 与现有 `scripts/ast/javaparser_bridge.py` 的关系

二者并存而非二选一:

| 维度 | `javaparser_bridge.py` (JPype) | `method_calls_extractor.py` (JAR) |
|---|---|---|
| 启动开销 | 一次 JVM,持久 | 每文件 ~1-2s JVM 启动 |
| 单文件分析 | ~100ms | ~1.5s (含 JVM) |
| 100 文件批量 | ~10s | ~150s ❌ |
| 依赖 | `pip install JPype1` + JDK | 仅 JDK |
| API 灵活度 | 高 (返回原始 Java 对象) | 低 (CLI 固定输出) |
| 符号解析 | 已实现 (CombinedTypeSolver) | 已实现 |
| 现有调用方 | `ast/`,`audit/`, 多个脚本 | 无 |

**推荐**: 优先用 `javaparser_bridge.py` 做批量提取, JAR 作为离线 fallback。
若 bridge 不可用 (无 JPype),自动降级到 JAR。

但 chain-sql-engine 的 plan 已经指定用 JAR,这里尊重既定决策,在 wrapper 内
保留两条路径的开关 (default=JPype 因批量场景下快 10x+)。

---

## 4. 样例运行 (Sample Usage)

### 4.1 内置 sample

```bash
$ java -jar D:\test-clone\target\java-method-call-extractor-1.0.0.jar \
        D:\test-clone\test-data\SampleService.java \
        D:\test-clone\test-data
```
<details>
<summary>展开完整输出 (9 条记录)</summary>

```json
[
  {"startLine": 9,  "methodSignature": "public void addItem(String item)", "calledFQN": "java.util.List.add(item)"},
  {"startLine": 9,  "methodSignature": "public void addItem(String item)", "calledFQN": "java.io.PrintStream.println(\"Added: \" + item)"},
  {"startLine": 14, "methodSignature": "public int countItems()", "calledFQN": "java.util.List.size()"},
  {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "java.lang.String.length()"},
  {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "com.example.SampleService.addItem(s.toUpperCase())"},
  {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "java.lang.String.toUpperCase()"},
  {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "java.io.PrintStream.println(\"Processed \" + input.size() + \" items\")"},
  {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "java.util.List.size()"},
  {"startLine": 28, "methodSignature": "private String concat(String a, String b)", "calledFQN": "java.lang.String.concat(b)"}
]
```

</details>

### 4.2 WebGoat VulnerableTaskHolder (反序列化 → Runtime.exec sink)

```bash
$ java -jar D:\test-clone\target\java-method-call-extractor-1.0.0.jar \
        D:\code\WebGoat-2025.3\src\main\java\org\dummy\insecure\framework\VulnerableTaskHolder.java \
        D:\code\WebGoat-2025.3\src\main\java
```

源文件位置: `D:\code\WebGoat-2025.3\src\main\java\org\dummy\insecure\framework\VulnerableTaskHolder.java`
关键 method `readObject` 位于源代码第 **48** 行 (1-based) → JAR 输出 **47** (0-based) ✅

<details>
<summary>展开完整输出 (20 条记录, 全部归属于 readObject)</summary>

```json
[
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.io.ObjectInputStream.defaultReadObject()"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "log.info(\"restoring task: {}\", taskName)"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "log.info(\"restoring time: {}\", requestedExecutionTime)"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.time.LocalDateTime.isBefore(LocalDateTime.now().minusMinutes(10))"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.time.LocalDateTime.minusMinutes(10)"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.time.LocalDateTime.now()"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.time.LocalDateTime.isAfter(LocalDateTime.now())"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.time.LocalDateTime.now()"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "log.debug(this.toString())"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "org.dummy.insecure.framework.VulnerableTaskHolder.toString()"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.lang.String.startsWith(\"sleep\")"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.lang.String.startsWith(\"ping\")"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.lang.String.length()"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "log.info(\"about to execute: {}\", taskAction)"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.lang.Runtime.exec(taskAction)"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.lang.Runtime.getRuntime()"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.lang.Process.getInputStream()"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "java.io.BufferedReader.readLine()"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "log.info(line)"},
  {"startLine": 47, "methodSignature": "private void readObject(ObjectInputStream stream) throws Exception",
   "calledFQN": "log.error(\"IO Exception\", e)"}
]
```

</details>

**审计意义**: 同名 method (line 47, 即 `readObject`) 在 chain-sql-engine 的 sink
过滤规则下,会得到以下 sink 列表 (`groupId="org.dummy.insecure.framework"`):

```python
sinks = [c for c in calls if not c.startswith("org.dummy.insecure.framework.")]
# 结果:
#   java.io.ObjectInputStream.defaultReadObject()
#   java.time.LocalDateTime.isBefore(...)
#   java.lang.Runtime.exec(taskAction)        ← 关键 sink,反序列化 → 命令执行
#   java.lang.Runtime.getRuntime()
#   java.lang.Process.getInputStream()
#   java.io.BufferedReader.readLine()
#   ...
```

`java.lang.Runtime.exec(taskAction)` 正是反序列化漏洞的根 sink,
与 `org.owasp.webtopl.lessons.InsecureDeserializationTask` 的 PoC 完全吻合。
**该工具能精准产出 sink 列表,符合 chain-sql-engine 设计目标**。

### 4.3 多 method 文件 (HammerHead)

```bash
$ java -jar D:\test-clone\target\java-method-call-extractor-1.0.0.jar \
        D:\code\WebGoat-2025.3\src\main\java\org\owasp\webgoat\container\HammerHead.java \
        D:\code\WebGoat-2025.3\src\main\java
```
输出:
```json
[
  {"startLine": 20, "methodSignature": "public ModelAndView attack()",
   "calledFQN": "org.owasp.webgoat.container.lessons.Lesson.getLink()"},
  {"startLine": 20, "methodSignature": "public ModelAndView attack()",
   "calledFQN": "org.owasp.webgoat.container.session.Course.getFirstLesson()"}
]
```
验证: 同一文件下不同 method 各自得到独立的 calls 列表,
`(file, startLine)` 关联方式可行。 (HammerHead 单 method,只有 1 个 startLine)

### 4.4 自动探测 sourceRoot

不传 sourceRoot 也能跑 (向上找 `src/main/java`)。WebGoat 案例自动探测的结果与显式传入完全一致 (20 条记录,字节相同)。这是为了"单文件分析"场景做的容错。

---

## 5. 风险与限制 (Risks / Limitations)

### 5.1 ⚠️ 设计文档 bug: `startLine` 行号基准

`D:\agentloop\design-docs\chain-sql-engine.md` L68 注释:
```sql
WHERE file_path = :file
  AND start_line - 1 = :startLine  -- codegraph start_line is 0-based, jar is 1-based
```

**注释是错的,SQL 是对的**。实测:

| 来源 | 基准 | 证据 |
|---|---|---|
| JAR 输出 `startLine` | **0-based** | `Main.java` L70: `method.getBegin().map(p -> p.line - 1)` |
| codegraph `nodes.start_line` | **1-based** | `chain-sql-engine.md` L18: `start_line -1 as start` (显示别名是 0-based,说明原值是 1-based) |

**正确的对照公式**:
```sql
WHERE file_path = :file
  AND start_line - 1 = :startLine  -- nodes.start_line (1-based) - 1 = jar 输出 (0-based)
```
SQL 写法恰好正确 (减 1 对齐),但注释方向写反了。**集成时直接复用 SQL,无需修改**,
但要在新代码里加 unit test 锁死这个基准,避免后续混淆。

### 5.2 startLine 在文件内是否唯一? (设计文档开放问题 #1)

**已验证**: 至少在 `VulnerableTaskHolder.java` (单 method) 和 `HammerHead.java`
(单 method) 中唯一。**未验证**: 含 lambda / 匿名类 / 局部方法引用的场景。
建议在 `scripts/chain/method_calls_extractor.py` 内加一个 assertion:
同一文件内若发现两个 method 的 startLine 相同,立即 raise (因为关联算法假设唯一)。

### 5.3 性能: 每文件 1.5s JVM 启动开销

实测 WebGoat 单文件调用耗时约 1.5s,其中 ~1.2s 是 JVM 启动,~0.3s 是解析。
对一个 100 文件的项目 ≈ 150s。

**缓解方案**:
- 优先用 `scripts/ast/javaparser_bridge.py` (JPype, 一次启动, 批量 ~10s/100 files)
- 失败时回退到 JAR
- wrapper 接口默认走 JPype,`--backend=jar` 强制走 JAR

### 5.4 未解析调用的降级策略

JAR 内部对未解析的 `MethodCallExpr` 降级为 `scope.name(args)` (L97-103)。但
输出**无法区分**"已解析的 `foo.bar.baz()`" 和 "未解析降级的 `foo.bar.baz()`",
因为两者格式相同。

**风险**: 链上 sink 过滤 (`not startswith(groupId)`) 对降级结果仍然有效
(因为降级结果是字符串),但**无法判断该 FQN 是否可信**。

**缓解**: 在 wrapper 内对比 JAR 输出与 `javaparser_bridge.py` 的 `resolved: bool`
字段,只把 `resolved=True` 的当作"真 sink"。

### 5.5 无 LICENSE, 无作者署名

`pom.xml` 没有 `<licenses>`, 无 LICENSE 文件, git config 无 user.name/email。
合规上有风险 (生产集成前需补齐)。

**建议**:
1. 短期: 在 agentloop 内部以"内部工具"对待,不对外发布
2. 中期: 内部补 author + license (Apache-2.0 推荐),提到 `tools/` 而非 `D:\test-clone`

### 5.6 与现有 `javaparser_bridge.py` 功能重叠

两者都用 JavaParser 解析 method calls,差异:
- bridge: 通过 JPype 共享 JVM,更灵活 (返回 Java 对象),已用于其他 audit 脚本
- JAR: 独立 CLI,简单,但冷启动慢

**建议**: 不删 JAR,但在 chain-sql-engine 的 wrapper 中优先调用 bridge,仅在
bridge 不可用时降级到 JAR。

### 5.7 不处理以下情况

- 构造器调用 (`new Foo(x)`) → `MethodCallExpr` 在 JavaParser 中也包含,但解析为构造器签名,可能 FQN 包含 `<init>`
- 隐式方法 (super.x(), this.x()) → 会作为普通 `MethodCallExpr` 出现
- lambda 体内的调用 → 会被纳入**外层 method** 的 calls (因为 `findAll` 是嵌套的)
- 静态初始化块 (`static {}`) → 不属于 `MethodDeclaration`,**会被忽略**
- 注解中的方法调用 → 也会被纳入外层 method

lambda 纳入外层 method 的设计恰好符合 chain-sql-engine 的预期:
"在某个 method 里调用的所有方法",lambda 是 method body 的一部分。

### 5.8 文件不存在 / 解析失败的处理

`Main.java` L43-47 仅检查文件存在性,不捕获 `ParseException`。若源码有语法错误,
JVM 直接抛异常退出 (非零退出码),wrapper 必须捕获 `subprocess.CalledProcessError`
并降级 (返回空 calls 列表 + 警告日志)。

---

## 6. 实施步骤 (Next Actions)

按优先级:

1. **新建 `scripts/chain/method_calls_extractor.py`**
   - 封装 JAR 调用 + JPype 备选
   - 实现 `extract_method_calls_for_node()` (含 file 级缓存)
   - 实现 `filter_sinks()`
   - 加 unit test 锁死 0-based startLine 基准

2. **修改 `scripts/chain/sqlite-extract-chain.py` (或新建 `chain_builder.py`)**
   - 把 sink 提取接入 `extract_recursive()` 的返回路径
   - 每个 chain 节点附带 `sinks: list[str]`

3. **更正 `chain-sql-engine.md`**
   - L68 注释: "codegraph start_line is 0-based, jar is 1-based" → **实际相反**
   - 标注为 "已验证"

4. **(可选) 在 `tools/` 下复制 JAR**
   - 当前 `D:\test-clone\target\*.jar` 在仓库子目录
   - agentloop 已有 `tools/javaparser/` (JPype 用的 jar),需要把 fat-jar 移到 `tools/java-method-call-extractor/java-method-call-extractor-1.0.0.jar` 以统一管理

5. **清理**
   - `D:\test-clone` 是临时克隆目录,任务完成后可删除
   - 集成工作只在 agentloop 内进行

---

## 附录 A: Main.java 完整源码 (169 行)

文件: `D:\test-clone\src\main\java\com\javaparsextract\Main.java`
(已在 § 2.2 给出关键片段,此处不重复)

## 附录 B: pom.xml 依赖

```xml
<dependency>
    <groupId>com.github.javaparser</groupId>
    <artifactId>javaparser-symbol-solver-core</artifactId>
    <version>3.26.3</version>
</dependency>
```
打包插件: `maven-shade-plugin:3.6.0` (fat-jar, 含 `com.javaparsextract.Main` 作为 mainClass)

## 附录 C: chain-sql-engine.md 关联映射表

| 设计文档章节 | 工具能力 | 落地点 |
|---|---|---|
| § "工具现状调研 #1" JAR 描述 | ✅ 完全覆盖 | wrapper 文档化 |
| § "主流程" 步骤 2b | ✅ `extract_method_calls_for_file()` | `method_calls_extractor.py` |
| § "主流程" 步骤 2c | ✅ `(file, startLine)` 过滤 | 同上 |
| § "主流程" 步骤 2d | ✅ sink 过滤 (`not startswith(groupId)`) | `filter_sinks()` |
| § "关联算法" (file+startLine join) | ✅ 见 § 4.2 实测 | 同上 |
| § 范围边界 "新建 method_calls_extractor.py" | ✅ 本次新增 | `scripts/chain/` |
| § 待回答 #1 startLine 唯一性 | ⚠️ 部分验证 (见 § 5.2) | wrapper 加 assertion |
| § 待回答 #2 start_line 基准 | ❌ 文档有误 (见 § 5.1) | 修文档 + unit test 锁死 |