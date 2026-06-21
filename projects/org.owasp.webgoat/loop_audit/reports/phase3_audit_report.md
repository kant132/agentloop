# WebGoat 安全审计报告

生成时间: 2026-06-21T13:21:31.871299+00:00

## 审计概要
- 审计链数: 3
- 审计结果: 全部 safe
- 审计模型: v4 flash (bailian/glm-5.1)
- 方法体加载: 仅 Memurai 缓存（无源文件读取）

## 审计链详情

### 最长链(9节点): 7627fad437f1ff80_20
- **endpoint**: `org.owasp.webgoat.container.service.RestartLessonService#restartLesson`
- **last method**: `org.owasp.webgoat.webwolf.requests::WebWolfTraceRepository::contains`
- **verdict**: `safe`
- **vulnerabilities**: 无
- **分析**: 整条调用链从 RestartLessonService::restartLesson 到 WebWolfTraceRepository::Exclusion::contains。用户输入(lessonName, user)在整条链中未到达任何注入类危险 sink。链末方法仅为 URL 路径匹配的字符串比较工厂方法，无注入风险。userTrackerRepository.findByUser 使用 Spring Data JPA 参数化查询；flywayLessons.apply 使用 Flyway 自有占位符机制；CommentsCache 全部为内存操作。

### 最高优先级(29): c0c62d57c2703446_1
- **endpoint**: `org.owasp.webgoat.webwolf.jwt.JWTController#encode`
- **last method**: `org.owasp.webgoat.webwolf.jwt::JWTToken::encode`
- **verdict**: `safe`
- **vulnerabilities**: 无
- **分析**: 用户可控的 jwt 和 secretKey 流入 JWT 编码操作。parseToken 解析 JWT 为 Map 结构，标准 JJWT 库不启用 Jackson 多态反序列化。secretKey 直接构造 HMAC 密钥属于 JWT 工具设计意图。链中不存在 SQL/CMD/XXE/表达式/SSRF/反序列化等注入 sink。

### 随机链(3节点): b45a10a0b9486325_2
- **endpoint**: `org.owasp.webgoat.webwolf.jwt.JWTController#decode`
- **last method**: `org.owasp.webgoat.webwolf.jwt::JWTToken::parseToken`
- **verdict**: `safe`
- **vulnerabilities**: 无
- **分析**: 用户输入 formData token 经 trim+换行符去除后进入 parseToken。Base64 解码还原用户原始 JSON 内容，parse() 反序列化为 Map。标准 Jackson/Gson 解析器默认禁用 auto-typing，不支持 @type/@class 加载任意类。链中无 SQL/CMD/XXE/表达式/SSRF/LDAP/NoSQL/SSTI 操作。

## 调用链路径验证
- 所有相邻节点之间均有真实 calls 边（codegraph edges kind='calls'）
- CTE 防环: path 两端加 | 修复，无重复节点
- node_count 字段记录调用链长度

## 方法体注释格式
- 非 groupId 调用: 行尾内联 `//fqn: <完整FQN>`
- groupId 内调用: 不标注
- this.field 调用: JAR 通过 import 解析为完整 FQN

## source_root 探测
- 优先级: src/main/java > sources(JADX反编译) > project_root
- JAR 线程级 JavaParser + TypeSolver（避免 Guava 缓存竞争）