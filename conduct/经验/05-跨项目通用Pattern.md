# 经验 05：跨项目通用 Pattern

> 以下 Pattern 来自多个项目实战，跨业务域通用。FWD-X subagent 启动时**必读**。

## 一、Spring 框架类

### 1.1 鉴权注解家族
- `@PreAuthorize("hasRole('ADMIN')")` — 方法级
- `@Secured("ROLE_ADMIN")` — 旧版
- `@RolesAllowed("ADMIN")` — JSR-250
- `@RequiresPermissions("user:read")` — Shiro
- `@PreFilter` / `@PostFilter` — 集合过滤
- **⚠️ 反模式**：注解 + 但 Filter 没覆盖 → 注解失效

### 1.2 入口注解
- `@RestController` / `@Controller` — Spring MVC
- `@RequestMapping` / `@GetMapping` / ... — HTTP 方法映射
- `@DubboService` / `@Service` (Apache Dubbo) — RPC
- `@KafkaListener` / `@RabbitListener` — MQ
- `@Scheduled` / `@EnableScheduling` — 定时
- `@WebServlet` / `@WebFilter` / `@WebListener` — Servlet

### 1.3 危险 sink 类
- `JdbcTemplate.query(...)` — SQL
- `EntityManager.createNativeQuery(...)` — JPA SQL
- `Runtime.exec` / `ProcessBuilder.start` — RCE
- `ObjectInputStream.readObject` / `XMLDecoder.readObject` — 反序列化
- `SnakeYaml.load` / `Yaml.load` — YAML 反序列化
- `JSON.parseObject` (Fastjson) — 反序列化（特定版本）
- `ObjectMapper.readValue` (Jackson, 开启 defaultTyping) — 反序列化
- `new URL(url).openConnection()` — SSRF
- `File(...)` / `Files.newInputStream` — 路径遍历
- `MultipartFile.transferTo` — 任意文件上传
- `HttpServletResponse.getWriter().write(...)` — XSS
- `InitialContext.lookup` — JNDI 注入

### 1.4 危险 redirect / forward
- `response.sendRedirect(userInput)` — 开放重定向
- `request.getRequestDispatcher(userInput).forward(...)` — 路径穿越

## 二、Spring Security 模式

### 2.1 常见 Filter 链顺序
```
SecurityContextPersistenceFilter
  → UsernamePasswordAuthenticationFilter
  → ExceptionTranslationFilter
  → AuthorizationFilter
  → CustomFilter (项目特定)
```

### 2.2 绕过模式
- Filter 顺序错：AuthorizationFilter 在自定义 Filter 之前 → 漏鉴权
- URL pattern 不覆盖：`/api/admin/**` 漏掉 `/api/admin`（无尾斜杠）
- `permitAll()` 过宽：包含敏感路径
- `antMatchers("/**")` 放行所有

## 三、MyBatis 模式

### 3.1 安全写法
```xml
<select id="findByName" resultType="User">
  SELECT * FROM users WHERE name = #{name}    <!-- ✅ 参数化 -->
</select>
```

### 3.2 危险写法
```xml
<select id="findByName" resultType="User">
  SELECT * FROM users WHERE name = '${name}'   <!-- ❌ 字符串替换 -->
</select>
```

### 3.3 拼接模式
- `${}` 是字符串替换（危险）
- `#{}` 是 PreparedStatement 参数（安全）
- `concat()` / `||` 函数拼接（危险）
- `<bind>` 标签动态拼接（视情况）

## 四、JPA / Hibernate 模式

### 4.1 安全
- `TypedQuery.setParameter(...)` 参数化

### 4.2 危险
- `createNativeQuery("... " + userInput + " ...")` 拼接
- `EntityManager.createQuery("... " + userInput + " ...")` 拼接

## 五、认证 / 会话模式

### 5.1 Session 固定
- 登录后未重生成 session ID → 固定攻击

### 5.2 JWT 模式
- `alg: none` 接受 → 签名绕过
- 弱 secret → 爆破
- 不校验 `exp` / `nbf` → 永不过期

### 5.3 密码处理
- `BCrypt.checkpw` 正确
- `MessageDigest.isEqual` 配合 SHA-256 可接受
- `password.equals(inputPassword)` 时间攻击

## 六、业务模式

### 6.1 支付
- 金额字段由前端传入 → 价格篡改
- 不校验订单与用户关联 → 替他人下单
- 不校验库存 → 超卖

### 6.2 状态机
- 状态字段由前端传入 → 跳过中间步骤
- 不校验前置状态 → 从"已退款"调"确认收货"
- 并发问题：扣款与发货非原子

### 6.3 文件操作
- 路径未规范化 → `../` 遍历
- 文件名由用户控制 → 覆盖系统文件
- 上传未校验类型 → getshell

## 七、跨项目 Prompt 模板

```markdown
# 漏洞识别检查清单（通用）

## 认证
- [ ] 端点是否在 Filter 链覆盖范围？
- [ ] 方法是否有 @PreAuthorize 等？
- [ ] 是否依赖客户端传的 role？

## 注入
- [ ] SQL 是否参数化？
- [ ] 是否拼接用户输入到命令？
- [ ] 是否反序列化用户输入？

## 业务
- [ ] 金额/状态/角色是否服务端二次校验？
- [ ] 是否校验对象归属？
- [ ] 是否有并发问题？

## 信息
- [ ] 异常是否返回堆栈？
- [ ] 日志是否含敏感信息？
- [ ] 响应是否含敏感字段？
```

## 八、沉淀位置

以上 Pattern 一旦发现新变体 → 沉淀到 `types/{分类}/{漏洞}.md` 的"已知变体"小节。
