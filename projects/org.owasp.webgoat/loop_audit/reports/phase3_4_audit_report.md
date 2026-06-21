# WebGoat 安全审计报告

生成时间: 2026-06-21T14:14:20.030035+00:00

## 审计概要
- Phase 1: 9 collectors, 168 routes (4.5s)
- Phase 2: 29 chains, 10 endpoints, avg_priority=4.38 (6.7s)
- Phase 3: 3条链(最长/最高优先级/随机) v4 flash 审计, 全部 safe
- Phase 4: 动态利用 - 登录WebGoat(302) + 3个端点HTTP请求
- 方法体: 仅Memurai缓存, 无源文件读取

## Phase 3 审计结果

### 最长链(4节点): b45a10a0b9486325_4
- **endpoint**: `org.owasp.webgoat.webwolf.jwt.JWTController#decode`
- **last method**: `JWTToken::write`
- **verdict**: `safe`
- **分析**: 用户输入(formData.token)经Base64解码+JSON解析成Map后流入write()，write()执行ObjectMapper序列化(对象→字符串)，非反序列化。未启用多态特性，无注入sink。
- **Phase 4 PoC**: POST http://localhost:19090/WebWolf/jwt/decode → HTTP 302

### 最高优先级(29): c0c62d57c2703446_1
- **endpoint**: `org.owasp.webgoat.webwolf.jwt.JWTController#encode`
- **last method**: `JWTToken::encode`
- **verdict**: `safe`
- **分析**: 用户可控header/payload/secretKey流入JWT构造。parse()解析JSON为Map（非反序列化）。无SQL/CMD/XXE/表达式/SSRF/反序列化sink。JWT伪造属于认证逻辑问题，不在注入审计范围。
- **Phase 4 PoC**: POST http://localhost:19090/WebWolf/jwt/encode → HTTP 302

### 随机(1节点): 9800b45ac12ef2a1_0
- **endpoint**: `org.owasp.webgoat.webwolf.requests.LandingPage#ok`
- **last method**: `LandingPage::ok`
- **verdict**: `safe`
- **分析**: 方法仅从request.getRequestURL()读取URL写入TRACE日志，返回空200 OK。无SQL/CMD/XXE/表达式/SSRF/反序列化sink。
- **Phase 4 PoC**: GET http://localhost:19090/WebWolf/landing → HTTP 200

## Phase 4 动态利用
- WebGoat登录: http://localhost:18080/WebGoat/login → 302 (获取JSESSIONID)
- longest: POST /WebWolf/jwt/decode → 302 (需要WebWolf认证)
- highest: POST /WebWolf/jwt/encode → 302 (需要WebWolf认证)
- random: GET /WebWolf/landing → 200 (成功响应)

## 调用链路径验证
- CTE纯INNER JOIN + kind='calls', 所有相邻节点有真实calls边
- 防环: path两端加|, 无重复节点
- node_count字段记录链长度

## 方法体注释格式
- 非groupId调用: 行尾内联 //fqn: <完整FQN>
- groupId内调用: 不标注
- this.field: JAR通过import解析为完整FQN

## source_root探测
- 优先级: src/main/java > sources(JADX) > project_root
- JAR线程级JavaParser+TypeSolver