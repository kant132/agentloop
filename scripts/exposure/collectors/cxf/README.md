# CXF/JAX-RS/JAX-WS 端点识别框架

## 覆盖场景

| 场景 | 数据源 | 覆盖 | 说明 |
|------|--------|------|------|
| **JAX-RS @Path 类** | javaparser-service `JaxRsFramework` | ✅ | 精确提取 `@Path("/api")` 路径值 |
| **JAX-RS 方法级 @Path 子路径** | javaparser-service `JaxRsFramework` | ✅ | `@GET @Path("/{id}")` 拼接 |
| **JAX-RS @GET/@POST/@PUT/@DELETE** | javaparser-service `JaxRsFramework` | ✅ | HTTP method 映射 |
| **JAX-WS @WebService 类** | javaparser-service `JaxWsFramework` | ✅ | 新增 |
| **JAX-WS @WebMethod 方法** | javaparser-service `JaxWsFramework` | ✅ | 新增 |
| **JAX-WS @WebMethod(exclude=true)** | javaparser-service `JaxWsFramework` | ✅ | 新增：排除非操作方法 |
| **JAX-WS endpointInterface** | javaparser-service `JaxWsFramework` | ✅ | 新增：追踪 SEI 接口方法 |
| **CXFServlet URL 前缀** | `config_scanner.py` 读 web.xml | ✅ | `/services/*` 等前缀 |
| **jaxws:endpoint XML 声明** | `config_scanner.py` 读 Spring XML | ✅ | `<jaxws:endpoint implementor=...>` |
| **Endpoint.publish() 编程式** | 源码扫描 | ⚠️ | 需有源码，grep 查 `Endpoint.publish(` |
| **无源码 JAR-only** | jar-analyzer.db `anno_table` | ⚠️ | 只识别注解存在，路径不精确 |

## 数据流

```
auto_preset.py / route_collector.py
  │
  ├── 有源码 (src/main/java) ?
  │     ├── YES → javaparser-service RouteExtractor --routes
  │     │         ├── SpringMvcFramework   (Spring MVC)
  │     │         ├── JaxRsFramework       (JAX-RS RESTful)
  │     │         ├── JaxWsFramework       (JAX-WS SOAP) ← 新增
  │     │         ├── ServletFramework     (@WebServlet)
  │     │         ├── SpringGraphQLFramework (@GraphQL*)
  │     │         ├── SpringActuatorFramework (@Endpoint)
  │     │         ├── SpringWebSocketFramework (@ServerEndpoint)
  │     │         └── SpringRSocketFramework
  │     │
  │     └── config_scanner.py → web.xml / Spring XML
  │           ├── CXFServlet URL pattern
  │           └── <jaxws:endpoint> / <jaxrs:server> 声明
  │
  └── 无源码 (JAR-only) ?
        └── jar-analyzer.db anno_table
              ├── @Path 类 + @GET/@POST 方法 (路径不精确)
              └── @WebService 类 + @WebMethod 方法
```

## 文件结构

```
scripts/exposure/collectors/cxf/
├── __init__.py
├── README.md              ← 本文件
├── config_scanner.py       ← web.xml / Spring XML 配置扫描
└── cxf_route_merger.py     ← 合并 javaparser-service 路由 + config_scanner 结果

tools/javaparser-service/
├── src/main/java/com/javaparsextract/framework/
│   ├── JaxRsFramework.java       ← 已有（@Path + @GET/@POST）
│   ├── JaxWsFramework.java       ← 新增（@WebService + @WebMethod）
│   ├── SpringMvcFramework.java   ← 已有
│   └── ...
└── src/main/java/com/javaparsextract/spi/
    ├── FrameworkHandler.java    ← 已有（SPI 接口）
    ├── AnnotationUtils.java      ← 已有（注解参数值提取）
    └── RouteResult.java          ← 已有（路由结果记录）
```

## JaxWsFramework 设计

```java
// 处理 @WebService 注解的类
isController(cls):
  - 检查类是否有 @WebService 注解（javax.jws.WebService 或 jakarta.jws.WebService）

extractClassBasePath(cls):
  - 从 @WebService(serviceName="MyService") 提取 serviceName
  - 无 serviceName → 用类简单名
  - 返回 "/services/{serviceName}" (CXF 默认前缀)

handleMethodAnnotation(annotationName, annotation, method, classBasePath, classFqn):
  - @WebMethod:
    - 检查 exclude=true → 跳过
    - 提取 action 值作为 SOAP action
    - HTTP method = POST (SOAP 默认)
    - path = classBasePath (所有方法共用同一个 endpoint)
  - 非 @WebMethod:
    - 如果类有 @WebService，所有 public 方法都是端点
    - 但如果有 @WebMethod 标注的方法，只取标注的
```

## endpointInterface 追踪

```java
// @WebService(endpointInterface="com.foo.BarService")
// 实际方法在接口 com.foo.BarService 中
// 实现类的方法可能没有 @WebMethod 注解

extractClassBasePath(cls):
  1. 检查 @WebService(endpointInterface=...)
  2. 如果有 endpointInterface:
     - 解析接口 FQN
     - 从源码中找到接口定义
     - 提取接口中所有方法（接口方法默认都是 @WebMethod）
  3. 如果没有 endpointInterface:
     - 实现类自己的方法就是端点
```
