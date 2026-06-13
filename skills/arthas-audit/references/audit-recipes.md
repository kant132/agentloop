# 安全审计命令配方（按漏洞类型）

按 OWASP Top 10 + 常见漏洞分类，每类给出完整命令序列。

## A01:2021 - 失效的访问控制

### 1.1 身份绕过 / JWT 绕过

```bash
# 反编译 JWT Filter
jad com.example.security.JwtFilter doFilter
jad com.example.security.JwtUtil

# 看 JWT secret / 配置
getstatic com.example.security.JwtUtil SECRET_KEY -x 2
getstatic com.example.security.JwtConfig *
ognl '@com.example.security.JwtUtil@SECRET_KEY'

# 观察 JWT 解析全过程
watch com.example.security.JwtFilter doFilter \
  '{params[0].getHeader("Authorization"), returnObj, throwExp}' -x 4 -n 5

# 找 Spring Security 实际配置
vmtool --action getInstances \
  --classLoaderClass org.springframework.boot.loader.LaunchedURLClassLoader \
  --className org.springframework.security.web.SecurityFilterChain \
  --limit -1 \
  --express 'instances.{#this.getFilters().{#this.getClass().getName()}}' -x 4

# 找所有 Filter 加载顺序
vmtool --action getInstances --className javax.servlet.Filter \
  --limit -1 --express 'instances.{#this.getClass().getName()}'
```

### 1.2 水平/垂直越权

```bash
# 观察用户上下文切换
watch com.example.service.UserService getCurrentUser 'target' -x 3 -n 5

# 看 Session / ThreadLocal 中的用户信息
vmtool --action getInstances --className java.lang.ThreadLocal \
  --limit -1 --express 'instances.{? #this.get() != null}.{#this.get()}' -x 4

# 看拦截器检查了什么
watch com.example.interceptor.AuthInterceptor preHandle \
  '{params[0].getRequestURI(), params[0].getSession().getAttribute("userId"), returnObj}' -x 3 -n 5
```

## A02:2021 - 密码学失败

```bash
# 找硬编码密钥
sc -E '.*Config.*|.*Secret.*|.*Key.*'
jad com.example.util.AESUtil
getstatic com.example.util.AESUtil KEY -x 2
getstatic com.example.util.HashUtil SALT -x 2

# 观察加密方法调用（看实际加密算法和密钥）
watch com.example.util.AESUtil encrypt \
  '{params, returnObj}' -x 3 -n 3

watch com.example.util.HashUtil hash \
  '{params, returnObj}' -x 3 -n 10   # 看用的什么 hash 算法、是否有 salt

# 找使用了弱加密的调用
stack java.security.MessageDigest getInstance -n 5
# 可能看到 MD5、SHA-1 这些弱算法

stack javax.crypto.Cipher getInstance -n 5
# 看使用什么加密模式（ECB 弱，CBC 强）

stack java.util.Random nextInt -n 10
stack java.security.SecureRandom nextInt -n 10
# 对比：Random 不安全，SecureRandom 安全
```

## A03:2021 - 注入（SQL / NoSQL / Command / LDAP）

### 3.1 SQL 注入

```bash
# 观察 SQL 语句（最关键）
watch java.sql.Statement execute* '{params}' -x 3 -n 5
watch java.sql.PreparedStatement execute* '{params}' -x 3 -n 5  # 参数化查询

# 从驱动层抓 SQL（更底层）
watch com.mysql.cj.jdbc.ClientPreparedStatement execute '{target.toString()}' -x 2 -n 3
watch org.postgresql.jdbc.PgStatement execute '{target.toString()}' -x 2 -n 3

# 追踪触发路径
stack java.sql.Statement execute* -n 3

# OGNL 验证：直接访问数据层
vmtool --action getInstances \
  --className org.springframework.jdbc.core.JdbcTemplate \
  --limit 1 \
  --express 'instances[0].queryForList("SELECT count(*) FROM user")'
```

### 3.2 命令注入

```bash
# 核心追踪点
stack java.lang.Runtime exec -n 5
stack java.lang.ProcessBuilder start -n 5

# 观察命令内容
watch java.lang.Runtime exec '{params}' -x 3 -n 3
watch java.lang.ProcessBuilder '<init>' '{params}' -x 3 -n 3
watch java.lang.ProcessBuilder start '{target.command()}' -x 3 -n 3

# 验证 PoC（高危，仅限授权环境）
ognl '@java.lang.Runtime@getRuntime().exec("whoami")'
# 或
ognl '#cmd={"id"}, new java.lang.ProcessBuilder(#cmd).start()'
```

### 3.3 JNDI 注入

```bash
stack javax.naming.InitialContext lookup -n 5
watch javax.naming.InitialContext lookup '{params}' -x 3 -n 5

# 找 RMI / LDAP 客户端
stack java.rmi.registry.LocateRegistry getRegistry -n 5
stack com.sun.jndi.ldap.LdapCtx doLookup -n 5
```

### 3.4 LDAP 注入

```bash
watch com.sun.jndi.ldap.LdapCtx doSearch '{params}' -x 3 -n 5
```

## A06:2021 - 易受攻击和陈旧组件

```bash
# 扫描运行时加载的依赖版本
classloader -c <hash> --url-classes --jar spring
classloader -c <hash> --url-classes --jar log4j
classloader -c <hash> --url-classes --jar fastjson
classloader -c <hash> --url-classes --jar shiro

# 看完整依赖树
vmtool --action getInstances \
  --classLoaderClass org.springframework.boot.loader.LaunchedURLClassLoader \
  --className org.springframework.context.ApplicationContext \
  --express 'instances[0].getBean("environment").getProperty("spring.boot.version")'

# 找已知有漏洞的类
sc org.apache.commons.collections.functors.InvokerTransformer   # commons-collections
sc org.apache.struts2.dispatcher.multipart.MultiPartRequest     # struts2
sc org.apache.log4j.net.JMSAppender                             # log4shell
sc com.alibaba.fastjson.parser.ParserConfig                     # fastjson
```

## A07:2021 - 身份认证和鉴定失败

```bash
# 看用户认证流程
watch com.example.auth.AuthService authenticate \
  '{params, returnObj, throwExp}' -x 4 -n 5

# 看 Session 管理
vmtool --action getInstances --className javax.servlet.http.HttpSession \
  --limit -1 --express 'instances.{#this.getId()}'

# 看密码存储方式
jad com.example.service.UserService createUser
jad com.example.util.PasswordUtil

# 找明文密码
sysprop | grep -i password
sysenv | grep -i password
vmtool --action getInstances --className javax.sql.DataSource \
  --limit 5 --express 'instances.{#this.getUrl() + " | " + #this.getUsername()}'
```

## A08:2021 - 软件和数据完整性故障

### 反序列化

```bash
# 核心追踪
stack java.io.ObjectInputStream readObject -n 10
stack java.io.ObjectInputStream readUnshared -n 10

# 观察序列化数据
watch java.io.ObjectInputStream readObject '{target, throwExp}' -x 2 -n 5

# 找危险的反序列化 gadget
sc com.sun.rowset.JdbcRowSetImpl                    # JNDI gadget
sc org.apache.commons.collections.functors.*        # CC gadget
sc org.springframework.beans.factory.config.PropertyPathFactoryBean  # Spring
sc com.sun.org.apache.xalan.internal.xsltc.trax.TemplatesImpl

# 录制反序列化调用（用于取证）
tt -t java.io.ObjectInputStream readObject -n 5
# 看完整调用栈和异常
```

## A09:2021 - 安全日志和监控失败

```bash
# 查看日志配置
logger
logger --name com.example

# 看日志输出级别（是否记录重要事件）
watch org.slf4j.Logger warn '{params}' -x 3 -n 5
watch org.slf4j.Logger error '{params}' -x 3 -n 5

# 找审计日志实现
sc com.example.audit.*
jad com.example.audit.AuditLogger

# 观察审计日志记录内容
watch com.example.audit.AuditLogger log '{params}' -x 4 -n 10
```

## A10:2021 - 服务端请求伪造 (SSRF)

```bash
# HTTP 客户端追踪
stack java.net.URL openConnection -n 5
stack java.net.HttpURLConnection connect -n 5
stack org.apache.http.impl.client.CloseableHttpClient execute -n 5
stack org.springframework.web.client.RestTemplate exchange -n 5
stack feign.Client execute -n 5

# 观察 URL 内容
watch java.net.URL '<init>' '{params}' -x 2 -n 5
watch java.net.HttpURLConnection connect '{target.getURL()}' -x 2 -n 5

# DNS 解析追踪
stack java.net.InetAddress getByName -n 5
```

## 文件操作类漏洞

### 路径遍历 / 任意文件读取

```bash
stack java.io.File '<init>' -n 5
stack java.nio.file.Files newInputStream -n 5
stack java.io.FileInputStream '<init>' -n 5
stack java.nio.file.Paths get -n 5

# 观察文件路径（看是否有 ../../）
watch java.io.File '<init>' '{params}' -x 2 -n 5
watch java.nio.file.Files readAllBytes '{params}' -x 2 -n 3
```

### 文件上传漏洞

```bash
watch org.springframework.web.multipart.MultipartFile getOriginalFilename '{params, target}' -x 3 -n 5
watch org.apache.commons.fileupload.servlet.ServletFileUpload parseRequest '{params, returnObj}' -x 3 -n 3

# 看上传处理逻辑
jad com.example.controller.UploadController
```

## XXE (XML 外部实体注入)

```bash
# 找 XML 解析器
sc javax.xml.parsers.DocumentBuilder
sc org.xml.sax.XMLReader
sc javax.xml.stream.XMLInputFactory
sc org.dom4j.io.SAXReader

# 观察解析输入
watch javax.xml.parsers.DocumentBuilder parse '{params}' -x 3 -n 5
watch org.dom4j.io.SAXReader read '{params}' -x 3 -n 5
watch javax.xml.stream.XMLInputFactory createXMLStreamReader '{params}' -x 2 -n 3

# 看是否禁用了外部实体
jad com.example.util.XmlParser
jad javax.xml.parsers.DocumentBuilderFactory
```

## 敏感信息泄露

```bash
# 看异常处理
watch com.example.exception.GlobalExceptionHandler handleException '{params, returnObj}' -x 4 -n 5
# 看是否会返回完整堆栈给前端

# 看响应头
watch javax.servlet.http.HttpServletResponse addHeader '{params}' -x 2 -n 10
# 找 Server、X-Powered-By 等

# 看 HTTP status 异常
watch javax.servlet.http.HttpServletResponse sendError '{params}' -x 3 -n 5
```

## 动态类加载 / Webshell

```bash
# ClassLoader 异常
classloader -l
# 找 URLClassLoader 加载了可疑路径（如 /tmp/, /var/tmp/, 用户目录）

classloader -c <hash>

# 找动态加载的类
sc -E '.*\$Proxy.*'
sc -E '.*CGLIB\$.*'
sc -E '.*\$\$.*'                 # 动态生成类

# Spring 容器中的动态 Bean
vmtool --action getInstances \
  --classLoaderClass org.springframework.boot.loader.LaunchedURLClassLoader \
  --className org.springframework.context.ApplicationContext \
  --express 'instances[0].getBeanDefinitionNames()' -x 2

# 找可疑的 URLClassLoader 实例
vmtool --action getInstances --className java.net.URLClassLoader \
  --limit -1 --express 'instances.{#this.getURLs()}' -x 2

# 找 ScriptEngine（可能执行恶意脚本）
sc javax.script.ScriptEngine
sc javax.script.ScriptEngineManager
vmtool --action getInstances --className javax.script.ScriptEngineManager \
  --limit 1 --express 'instances[0].getEngineByName("nashorn") != null'

# 找反射调用链
stack java.lang.reflect.Method invoke -n 10
# 重点看：谁在用什么参数调用什么方法
```

## 反挖矿 / 反后门

```bash
# 线程堆栈全量分析
thread -all > /tmp/threads.txt
# 找非业务线程（名称可疑：MinerThread, Backdoor, Xxxx$1）

# CPU 高占用线程（挖矿典型特征）
thread -n 5
# 看最忙 5 个线程在跑什么

# 网络连接
vmtool --action getInstances --className java.net.Socket \
  --limit -1 --express 'instances.{#this.getInetAddress().getHostAddress() + ":" + #this.getPort()}'

vmtool --action getInstances --className java.net.ServerSocket \
  --limit -1 --express 'instances.{#this.getLocalPort()}'

# 找可疑的 Runtime.exec
tt -t java.lang.Runtime exec -n 30
# 录制 + 事后分析
```

---

## 通用审计开场三板斧

每次审计先跑这三条，建立基线：

```bash
# 1. 侦察
arthas("version")
arthas("dashboard -n 1")
arthas("jvm")
arthas("sysprop")
arthas("sysenv")

# 2. 类图快照
arthas("sc com.example.*")
arthas("classloader -l")

# 3. 线程快照
arthas("thread -all")
```

## 通用审计收尾动作

```bash
# 移除所有字节码增强
arthas("reset -E '.*'")

# 清理 tt 录制
arthas("tt --delete-all")

# 关闭 agent（可选，arthas-deploy 也可处理）
arthas("stop")
```
