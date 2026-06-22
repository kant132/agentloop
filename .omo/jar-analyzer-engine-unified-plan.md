# jar-analyzer-engine 统一 JAR 解析重构方案

## 目标

在 jar-analyzer-engine 基础上扩展，用纯 Java 完成所有 JAR 包解析（路由、注解、调用链、chain表），不再需要 Python 打补丁。

## 现状

### jar-analyzer-engine 已有能力
- ASM 字节码分析（类、方法、字段、注解、调用关系）
- SQLite 数据库（14 张表）
- Spring MVC 路由识别（spring_method_table，含 path 精确值）
- 继承关系 + 方法 Override（method_impl_table）
- 字符串常量提取（string_table）
- JavaWeb 组件识别（Servlet/Filter/Listener）
- FernFlower 反编译引擎
- 多阶段流水线架构（EngineBuildRunner）

### jar-analyzer-engine 缺失能力
1. JAX-RS（@Path/@GET/@POST）路由识别 → 精确路径值
2. JAX-WS（@WebService/@WebMethod）路由识别 → SOAP 端点
3. CXFServlet URL 前缀（web.xml/Spring XML 配置扫描）
4. @WebService(endpointInterface=...) SEI 接口方法追踪
5. @WebMethod(exclude=true) 排除
6. 调用链 CTE 递归查询（当前在 Python 侧用 SQLite CTE）
7. chain 表（调用链结果存储，当前在 Python 侧用 chains.db）
8. 非 groupId 方法调用（sink 识别，当前在 Python 侧从 method_call_table 查询）
9. anno_table 只存 anno_name，**不存参数值**（如 @Path("/api") 的 "/api"）

### javaparser-service 可继承的设计
- FrameworkHandler SPI 接口（框架路由处理器）
- JaxRsFramework（已实现：@Path + @GET/@POST + 方法级 @Path 子路径拼接）
- JaxWsFramework（已实现：@WebService + @WebMethod(exclude) + serviceName）
- AnnotationUtils（注解参数值提取工具类）
- RouteResult（路由结果记录类）

### Python 侧当前打补丁的方式
- auto_preset.py — 从 JAR 生成 preset.json + route.json
- jar_analyzer_cte.py — CTE 递归查询调用链
- chain_builder.py — 调用链构建 + sink 识别 + Memurai 缓存
- verify_edges.py — 链边批量验证
- scripts/exposure/collectors/cxf/ — CXF 端点识别

## 重构步骤

### Step 1: anno_value_table — 注解参数值提取

扩展 jar-analyzer-engine，在 DiscoveryRunner 阶段提取注解参数值。

新增表:
```sql
CREATE TABLE anno_value_table (
    av_id INTEGER PRIMARY KEY,
    anno_id INTEGER NOT NULL,  -- FK → anno_table.anno_id
    param_name TEXT NOT NULL,  -- 参数名 (如 "value", "path", "serviceName", "endpointInterface")
    param_value TEXT NOT NULL  -- 参数值 (如 "/api", "MyService")
);
```

需要修改:
- AnnoMapper.java / anno mapper XML — 增加 anno_value 表操作
- DiscoveryClassVisitor.java — 在 visitAnnotation 时提取参数值
- 新增 AnnoValueEntity.java + AnnoValueMapper.java

### Step 2: JaxRsService — JAX-RS 路由识别

参照 SpringService + SpringClassVisitor 模式，新增:

```
me/n1ar4/jar/analyzer/analyze/jaxrs/
├── JaxRsService.java              // 入口，遍历 classFileList
├── JaxRsConstant.java             // 常量定义 (javax.ws.rs.* / jakarta.ws.rs.*)
├── JaxRsController.java            // @Path 类模型
├── JaxRsMapping.java              // @GET/@POST + 方法级 @Path 模型
└── asm/
    ├── JaxRsClassVisitor.java     // ASM ClassVisitor，识别 @Path 类
    ├── JaxRsMethodAdapter.java    // ASM MethodVisitor，识别方法级 @GET/@POST/@Path
    └── JaxRsPathAnnoAdapter.java  // ASM AnnotationVisitor，提取 @Path("/api") 路径值
```

### Step 3: JaxWsService — JAX-WS 路由识别

```
me/n1ar4/jar/analyzer/analyze/jaxws/
├── JaxWsService.java              // 入口
├── JaxWsConstant.java             // 常量定义 (javax.jws.* / jakarta.jws.*)
├── JaxWsEndpoint.java             // @WebService 类模型
├── JaxWsOperation.java            // @WebMethod 方法模型
└── asm/
    ├── JaxWsClassVisitor.java     // ASM ClassVisitor，识别 @WebService 类
    ├── JaxWsMethodAdapter.java    // ASM MethodVisitor，识别 @WebMethod(exclude=true)
    └── JaxWsAnnoAdapter.java      // ASM AnnotationVisitor，提取 serviceName/endpointInterface/action
```

### Step 4: route_table — 统一路由表

新增表:
```sql
CREATE TABLE route_table (
    rt_id INTEGER PRIMARY KEY,
    class_name TEXT NOT NULL,      -- 控制器类名
    method_name TEXT NOT NULL,    -- 处理方法名
    method_desc TEXT NOT NULL,    -- 方法描述符
    framework TEXT NOT NULL,      -- 框架名 (spring-mvc, jax-rs, jax-ws, servlet, graphql)
    http_method TEXT NOT NULL,    -- HTTP 方法 (GET/POST/PUT/DELETE/PATCH/ANY)
    path TEXT NOT NULL,           -- 完整 URL 路径 (class basePath + method path)
    base_path TEXT,               -- 类级基础路径
    method_path TEXT,             -- 方法级子路径
    line_number INTEGER,          -- 源码行号
    jar_id INTEGER NOT NULL       -- FK → jar_table.jid
);
```

### Step 5: EngineBuildRunner 流水线增加新阶段

在 SpringService.start() 之后，增加:
- JaxRsService.start() — JAX-RS 路由识别
- JaxWsService.start() — JAX-WS 路由识别
- 统一写入 route_table

### Step 6: ChainBuilder — 调用链构建命令

新增 CLI 命令 `--build-chains`:
```bash
java -jar jar-analyzer-engine.jar --jar app.jar
java -jar jar-analyzer-engine.jar --build-chains --group-id com.macro.mall --max-depth 20
```

新增表:
```sql
CREATE TABLE chain_table (
    chain_id TEXT PRIMARY KEY,
    endpoint_fqn TEXT NOT NULL,
    entry_method_id INTEGER,
    node_path TEXT NOT NULL,
    chain_path TEXT,
    node_count INTEGER NOT NULL,
    total_sinks INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    mismatch_score REAL DEFAULT 0,
    cycle_detected INTEGER DEFAULT 0,
    is_sink INTEGER DEFAULT 0,
    last_sinks INTEGER DEFAULT 0,
    created_at TEXT
);
```

Java 实现（替代 Python jar_analyzer_cte.py + chain_builder.py）:

```java
public class ChainBuilder {
    private final Connection conn; // SQLite JDBC (已有 sqlite-jdbc 3.51 依赖)
    private final String groupId;
    private final int maxDepth;

    public void buildAll() {
        // 1. 从 route_table 取所有端点
        List<RouteEntry> routes = getRoutes();

        for (RouteEntry route : routes) {
            String entryId = resolveEntryMethodId(route);
            if (entryId == null) continue;

            // 2. CTE 递归查询 method_call_table
            List<ChainPath> paths = extractRecursivePaths(entryId);
            // SQL:
            //   WITH RECURSIVE chain(method_id, class_name, method_name, method_desc, depth, path) AS (
            //     SELECT m.method_id, m.class_name, m.method_name, m.method_desc, 0,
            //            '|' || CAST(m.method_id AS TEXT) || '|'
            //     FROM method_table m WHERE CAST(m.method_id AS TEXT) = ?
            //     UNION ALL
            //     SELECT callee.method_id, callee.class_name, callee.method_name,
            //            callee.method_desc, c.depth + 1,
            //            c.path || CAST(callee.method_id AS TEXT) || '|'
            //     FROM chain c
            //     JOIN method_call_table mc
            //       ON mc.caller_method_name = c.method_name
            //       AND mc.caller_class_name = c.class_name
            //       AND mc.caller_method_desc = c.method_desc
            //     JOIN method_table callee
            //       ON callee.method_name = mc.callee_method_name
            //       AND callee.class_name = mc.callee_class_name
            //       AND callee.method_desc = mc.callee_method_desc
            //     WHERE c.depth < ? AND instr(c.path, '|' || CAST(callee.method_id AS TEXT) || '|') = 0
            //   )
            //   SELECT method_id, class_name, method_name, method_desc, depth, path FROM chain

            // 3. 提取所有路径变体 (去重 path 字符串)
            // 4. 对每个节点查非 groupId 调用 (sink 识别) — 调 SinkIdentifier
            // 5. 写入 chain_table
            // 6. 调 ChainVerifier 验证链边
        }
    }

    // 环检测: 检查 path 字符串中是否重复出现同一 method_id
    // CTE 的 instr(c.path, ...) 已防止环，cycle_detected 标记原图中的递归调用
    private boolean detectCycle(List<String> nodeIds,
                                 Map<String, List<String>> edgesMap) {
        // 对每个节点的 edges，检查是否有 back-edge 指向上游节点
    }

    // mismatch_score 计算:
    //   对每条链的每对连续节点 (i, i+1)，检查 method_call_table 是否有边
    //   无边 → broken_edges++
    //   mismatch_score = broken_edges / total_edges
    //   > 0.5 → status='broken'
}
```

**SQLite CTE 依赖**: jar-analyzer-engine 已包含 `sqlite-jdbc 3.51.3.0`，原生支持 WITH RECURSIVE 语法。

**批量大小**: method_call_table 可能很大（万级），CTE 查询单个入口的递归路径不超过 max_depth=20 跳，单次查询耗时 <1s（已在 Python 侧验证）。

### Step 7: ChainVerifier — 链边验证

在 --build-chains 后自动执行:

```java
public class ChainVerifier {
    private final Connection conn;

    public void verifyAll() {
        // 1. 预加载所有 method_call_table 边到 HashSet (内存)
        //    key = (caller_class, caller_method, callee_class, callee_method)
        //    一次 SELECT 全量加载，避免逐边查询 (N+1 问题)

        Set<String> callEdges = loadAllCallEdges();
        Set<String> implEdges = loadAllImplEdges();

        // 2. 预加载 method_table: method_id → (class_name, method_name)
        Map<String, MethodMeta> methodMap = loadAllMethods();

        // 3. 遍历 chain_table 中每条链
        for (ChainRow chain : getAllChains()) {
            String[] nodes = chain.nodePath.split(" -> ");
            boolean broken = false;
            for (int i = 0; i < nodes.length - 1; i++) {
                MethodMeta src = methodMap.get(nodes[i]);
                MethodMeta tgt = methodMap.get(nodes[i + 1]);
                String edgeKey = src.className + "|" + src.methodName + "|" +
                                 tgt.className + "|" + tgt.methodName;
                if (!callEdges.contains(edgeKey) && !implEdges.contains(edgeKey)) {
                    broken = true;
                    break;
                }
            }
            if (broken) {
                updateChainStatus(chain.chainId, "broken", 1.0);
            }
        }
    }
}
```

**性能**: 2422 条链 × 平均 4 节点 = ~10000 次 HashSet 查找，<0.1s（已在 Python 侧验证）。

### Step 8: SinkIdentifier — 非 groupId 调用识别

```java
public class SinkIdentifier {
    private final Connection conn;
    private final String groupId;
    // groupId 的 JVM 内部格式: com/macro/mall (用 / 替换 .)
    private final String groupIdSlash;

    public int identifySinks(String className, String methodName, String methodDesc) {
        // 查 method_call_table 中该方法的 callee
        String sql = "SELECT DISTINCT callee_class_name, callee_method_name " +
                     "FROM method_call_table " +
                     "WHERE caller_class_name = ? AND caller_method_name = ? AND caller_method_desc = ?";

        int sinkCount = 0;
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setString(1, className);
            ps.setString(2, methodName);
            ps.setString(3, methodDesc);
            ResultSet rs = ps.executeQuery();
            while (rs.next()) {
                String calleeClass = rs.getString(1);
                // 非 groupId 命名空间 = sink
                if (calleeClass != null && !calleeClass.startsWith(groupIdSlash)
                    && !calleeClass.startsWith("java/lang/Object")) {
                    sinkCount++;
                }
            }
        }
        return sinkCount;
    }
}
```

**非 groupId 判定**: callee_class_name 不以 groupId 的 `/` 格式开头（如 `com/macro/mall/`）。
**排除项**: `java/lang/Object` (构造方法 super 调用), `this.`/`super.` (已在 method_call_table 中过滤)。

### Step 9: Memurai 缓存 — 方法体预取

**关键**: Python 侧 `chain_builder.py` 在构建调用链后，会通过 `java-method-call-extractor.jar` + 源文件读取，把链上所有方法体预取到 Memurai。Phase 3 AI 分析时只从 Memurai GET，不读源文件。

**Java 替代方案**: jar-analyzer-engine 已内置 FernFlower 反编译引擎。在 `--build-chains` 时，对每条链的每个节点，用 FernFlower 反编译对应的 .class 文件，提取方法体文本。

```java
public class MethodBodyPrefetcher {
    private final DecompileEngine decompileEngine; // jar-analyzer-engine 已有

    public String getMethodBody(String className, int lineNumber) {
        // 1. 用 FernFlower 反编译 class 文件 → Java 源码
        // 2. 从源码中按 lineNumber 提取方法体
        // 3. 添加 //fqn: 注释 (与 Python 侧格式一致)
        // 4. 返回方法体文本
    }
}
```

**输出格式** (与 Python 侧 Memurai 缓存格式兼容):
```json
{
  "fqn": "com.macro.mall.controller.MinioController::upload",
  "body": "//fqn: com.macro.mall.controller.MinioController::upload\npublic CommonResult upload(...) {\n  ...",
  "file": "BOOT-INF/classes/com/macro/mall/controller/MinioController.java",
  "start_line": 44,
  "depth": 0,
  "node_id": "765"
}
```

**两种输出模式**:
- **Memurai 模式** (默认): 写入 Memurai key `{groupId}:method:{method_id}`
- **SQLite 模式** (无 Memurai): 写入新增 `method_body_table`

新增表 (可选, 当 Memurai 不可用时):
```sql
CREATE TABLE method_body_table (
    mb_id INTEGER PRIMARY KEY,
    method_id INTEGER NOT NULL,    -- FK → method_table.method_id
    body TEXT NOT NULL,            -- 方法体文本 (含 //fqn: 注释)
    file_path TEXT,                -- 源文件路径
    start_line INTEGER,            -- 起始行号
    UNIQUE(method_id)
);
```

### Step 10: Python 侧适配

run_phase1_to_4.py 改为:
```bash
# Phase 0+1+2: 一条命令完成 (build + route + chain + sink + verify + body prefetch)
java -jar jar-analyzer-engine.jar --jar app.jar --build-chains --group-id com.macro.mall --max-depth 20

# Python 只做 Phase 3/4 AI 分析编排
python run_phase1_to_4.py --preset projects/{groupId}/preset.json --phase 3
```

Python 侧不再需要:
- ~~auto_preset.py~~ — jar-analyzer-engine `--jar` 直接生成 route_table
- ~~jar_analyzer_cte.py~~ — ChainBuilder 用 Java CTE
- ~~chain_builder.py~~ — ChainBuilder + SinkIdentifier 直接写 chain_table
- ~~verify_edges.py~~ — ChainVerifier 自动验证
- ~~scripts/exposure/collectors/cxf/~~ — JaxRsService + JaxWsService 内置
- ~~method_calls_extractor.py~~ — MethodBodyPrefetcher 用 FernFlower

Python 侧保留:
- `run_phase1_to_4.py` — 改为调 jar-analyzer-engine CLI + Phase 3/4 AI 编排
- `chain_db.py` — 改为读 jar-analyzer.db 的 chain_table (而非独立的 chains.db)
- `memurai_client.py` — Phase 3 AI 分析时从 Memurai GET 方法体
- Phase 3/4 AI 分析逻辑不变

## 架构变化

### Before (Python 打补丁)
```
JAR
 → jar-analyzer-engine build → jar-analyzer.db (14 表)
 → Python auto_preset.py → route.json
 → Python jar_analyzer_cte.py → CTE 递归
 → Python chain_builder.py → chains.db + Memurai (方法体)
 → Python verify_edges.py → 验证
```
两个数据库 (jar-analyzer.db + chains.db) + 5+ Python 脚本

### After (Java 统一)
```
JAR
 → jar-analyzer-engine build + build-chains → jar-analyzer.db (17 表)
   ├── route_table (路由: Spring MVC + JAX-RS + JAX-WS)
   ├── anno_value_table (注解参数值)
   ├── chain_table (调用链 + sink + 验证状态)
   └── method_body_table (方法体, Memurai 不可用时)
 → Memurai (方法体缓存, 可选)
 → Python Phase 3/4 AI 分析
```
一个数据库 (jar-analyzer.db) + 一个 Java 工具

## 迁移策略

### 现有项目数据迁移
1. **chains.db → chain_table**: 新增迁移脚本 `migrate_chains.py`，读取旧 chains.db 的 chain 数据，写入 jar-analyzer.db 的 chain_table
2. **route.json → route_table**: 读取旧 route.json，写入 route_table (framework 字段设为 'spring-mvc-legacy')
3. **Memurai 缓存**: 旧缓存格式兼容 (key 格式 `{groupId}:method:{node_id}` 不变)，Java 侧写入相同格式
4. **preset.json**: 保留，但 `jarAnalyzerDb` 字段指向包含 chain_table 的统一数据库，删除 `codegraphDb` 字段

### 分阶段切换
1. **阶段 1**: jar-analyzer-engine 新增 JAX-RS/JAX-WS 路由识别 (Steps 1-5)，Python 侧从 route_table 读取路由（不再从 spring_method_table + anno_table）
2. **阶段 2**: jar-analyzer-engine 新增 `--build-chains` (Steps 6-8)，Python 侧从 chain_table 读取调用链（不再用独立的 chains.db）
3. **阶段 3**: jar-analyzer-engine 新增方法体预取 (Step 9)，Python 侧不再调 java-method-call-extractor.jar
4. **阶段 4**: 删除 Python 侧补丁脚本 (Step 10)

## 依赖关系

```
Step 1 (anno_value_table) ─┐
                           ├──→ Step 2 (JaxRsService) ──┐
                           ├──→ Step 3 (JaxWsService) ──┤
                           │                              ├──→ Step 4 (route_table)
                           │                              │         ↓
                           │                              ├──→ Step 5 (EngineBuildRunner)
                           │                              │         ↓
                           │                              ├──→ Step 6 (ChainBuilder) ──→ Step 7 (ChainVerifier)
                           │                              │         ↓
                           │                              ├──→ Step 8 (SinkIdentifier)
                           │                              │         ↓
                           │                              └──→ Step 9 (MethodBodyPrefetcher)
                           │                                        ↓
                           └──────────────────────────────────→ Step 10 (Python 适配)
```

- Steps 1-3 可并行开发 (不同包)
- Step 4 依赖 Steps 2-3 (需要路由数据)
- Step 5 依赖 Steps 1-4 (流水线集成)
- Steps 6-8 串行 (build → verify → sink)
- Step 9 依赖 Steps 6-8 (需要链节点)
- Step 10 依赖所有前序步骤

## Git 跟踪

`tools/jar-analyzer-engine/` 当前是 UNTRACKED（254 个 Java 文件）。需要决定：
- **方案 A** (推荐): 作为 git submodule 引入，保持上游同步能力
- **方案 B**: 直接提交到 java-for-jar 分支，方便修改但失去上游同步
- **方案 C**: fork 后提交，通过 remote 上游同步

建议方案 A，在 `.gitmodules` 中添加：
```
[submodule "tools/jar-analyzer-engine"]
    path = tools/jar-analyzer-engine
    url = https://github.com/jar-analyzer/jar-analyzer-engine.git
    branch = main
```

## 验收标准

1. jar-analyzer-engine build 后，route_table 包含 Spring MVC + JAX-RS + JAX-WS 路由
2. jar-analyzer-engine build-chains 后，chain_table 包含调用链 + sink + 验证状态
3. jar-analyzer-engine build-chains 后，method_body_table 或 Memurai 包含方法体缓存
4. Python 侧 run_phase1_to_4.py 只需调 Java CLI，不再需要 Python 脚本解析 JAR
5. 只有一个数据库文件 jar-analyzer.db (17 表: 14 原有 + 3 新增)
6. 旧 chains.db 数据可通过迁移脚本导入 chain_table
