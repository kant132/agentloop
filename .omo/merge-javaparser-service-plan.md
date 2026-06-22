# javaparser-service 合并到 jar-analyzer-engine 执行计划

## 问题

两个 Java 工具没有合一：
- jar-analyzer-engine: Java 8, ASM 字节码分析, 输出 jar-analyzer.db
- javaparser-service: Java 21, JavaParser 源码分析, 独立 JAR

用户要求把 javaparser-service 的代码合并到 jar-analyzer-engine 中，产出单一 JAR。

## 兼容性问题

| 项目 | Java 版本 | 核心依赖 | 构建工具 |
|------|----------|---------|---------|
| jar-analyzer-engine | 8 | ASM 9.9.1 | maven-assembly-plugin |
| javaparser-service | 21 | JavaParser 3.26.3 | maven-shade-plugin |

JavaParser 3.26.3 最低需要 Java 11。jar-analyzer-engine 当前是 Java 8。

## 合并方案

### Step 1: 升级 Java 版本 + 加依赖

修改 `tools/jar-analyzer-engine/pom.xml`:
1. `maven.compiler.source` / `target` 从 `8` 改为 `11`
2. 在 `<properties>` 中加 `<javaparser.version>3.26.3</javaparser.version>`
3. 在 `<dependencies>` 中加:
```xml
<dependency>
    <groupId>com.github.javaparser</groupId>
    <artifactId>javaparser-symbol-solver-core</artifactId>
    <version>${javaparser.version}</version>
</dependency>
```

### Step 2: 复制 javaparser-service 代码

把以下文件从 `tools/javaparser-service/src/main/java/com/javaparsextract/` 复制到 `tools/jar-analyzer-engine/src/main/java/me/n1ar4/jar/analyzer/analyze/framework/`:

**SPI 层** (复制到 `me/n1ar4/jar/analyzer/analyze/framework/spi/`):
1. `FrameworkHandler.java` — 框架路由处理器接口
2. `AnnotationUtils.java` — 注解参数值提取工具
3. `RouteResult.java` — 路由结果记录类
4. `JsonUtils.java` — JSON 序列化工具

**框架实现** (复制到 `me/n1ar4/jar/analyzer/analyze/framework/`):
5. `SpringMvcFramework.java` — Spring MVC 路由
6. `JaxRsFramework.java` — JAX-RS 路由
7. `JaxWsFramework.java` — JAX-WS 路由
8. `ServletFramework.java` — Servlet 路由
9. `SpringGraphQLFramework.java` — GraphQL 路由
10. `SpringActuatorFramework.java` — Actuator 路由
11. `SpringWebSocketFramework.java` — WebSocket 路由
12. `SpringRSocketFramework.java` — RSocket 路由

**编排层** (复制到 `me/n1ar4/jar/analyzer/analyze/framework/`):
13. `RouteExtractor.java` — 路由提取编排器

### Step 3: 调整包名

每个复制的 Java 文件:
- `package com.javaparsextract;` → `package me.n1ar4.jar.analyzer.analyze.framework;`
- `package com.javaparsextract.spi;` → `package me.n1ar4.jar.analyzer.analyze.framework.spi;`
- `package com.javaparsextract.framework;` → `package me.n1ar4.jar.analyzer.analyze.framework;`
- 所有 `import com.javaparsextract.*` → `import me.n1ar4.jar.analyzer.analyze.framework.*`

### Step 4: 在 EngineBuildRunner 中集成 RouteExtractor

在 `EngineBuildRunner.java` 的 Spring 分析之后，增加 JavaParser 路由提取:

```java
// 在 SpringService.start() 之后
// 如果有源码 (jar-analyzer-temp 目录中有 .java 文件或项目有 src/main/java)
// 用 JavaParser RouteExtractor 提取精确路由 (含注解参数值)
RouteExtractor routeExtractor = new RouteExtractor();
// 遍历 classFileList，对每个 .class 对应的 .java 文件解析路由
// 结果写入 route_table (新增表)
```

### Step 5: 新增 route_table

在 jar-analyzer.db 中新增 `route_table`:
```sql
CREATE TABLE IF NOT EXISTS route_table (
    rt_id INTEGER PRIMARY KEY,
    class_name TEXT NOT NULL,
    method_name TEXT NOT NULL,
    method_desc TEXT NOT NULL,
    framework TEXT NOT NULL,
    http_method TEXT NOT NULL,
    path TEXT NOT NULL,
    base_path TEXT,
    method_path TEXT,
    line_number INTEGER,
    jar_id INTEGER NOT NULL
);
```

新增 `RouteTableMapper.java` + 对应 MyBatis mapper XML。

### Step 6: 修改 EngineMain 增加 --routes 命令

```bash
# 原有: build jar-analyzer.db
java -jar jar-analyzer-engine.jar --jar app.jar

# 新增: build + 提取路由 (如果项目有源码)
java -jar jar-analyzer-engine.jar --jar app.jar --routes

# 新增: 只提取路由 (已有 jar-analyzer.db)
java -jar jar-analyzer-engine.jar --routes --source-root /path/to/src/main/java
```

### Step 7: 构建并验证

```bash
cd tools/jar-analyzer-engine
mvn clean package -DskipTests
# 验证: java -jar target/jar-analyzer-engine-1.2.0-jar-with-dependencies.jar --help
# 应该看到 --routes 选项
```

## 执行方式

用 task(category="deep") 委派执行:
- 修改 pom.xml (加 javaparser 依赖 + 升级 Java 11)
- 复制 13 个 Java 文件并调整包名
- 修改 EngineBuildRunner + EngineMain
- 新增 RouteTableMapper + mapper XML
- 构建 JAR
- 验证 --routes 命令可用

## 风险

1. **Java 8 → 11 升级**: jar-analyzer-engine 的 ASM 和 MyBatis 代码应该兼容 Java 11，但需要验证
2. **JavaParser 依赖体积**: javaparser-symbol-solver-core 约 2MB，fat JAR 会变大
3. **module-info.class**: JavaParser 3.26 是 JPMS 模块，需要排除 module-info.class (javaparser-service 的 shade-plugin 已处理)
4. **jar-analyzer-engine 用 maven-assembly-plugin**，javaparser-service 用 maven-shade-plugin: 需要在 assembly-plugin 中添加排除 module-info.class 的配置
