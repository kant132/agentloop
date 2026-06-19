# 老板 agent 自反（2026-06-13 反思 2）

## 用户问题

> opencode 没找到容器名 `webgoat-local` / 状态 Up / WebGoat @ http://localhost:18080/WebGoat / WebWolf @ http://localhost:19090/WebWolf。opencode 不是通读代码吗？老板也反思，你为啥没有诘问 opencode 很多访问都报 404，为没有让他深入阅读代码，找到登录入口或者其他接口？

## 老板自查 3 个错

### 错 1：没在 prompt 强约束 docker 环境

我给 opencode 的 COMMAND 里只说：
- "WebGoat: http://127.0.0.1:18080/"
- "后台: Docker 容器运行"
- "admin 凭据"

**没说的**：
- 用 `docker ps` 找容器名 + 验证状态
- 用 `docker inspect webgoat-local` 拿 IP / 端口 / volume
- 容器名 `webgoat-local` 是用户给我的关键信息，**我没传到 prompt**

### 错 2：没让 opencode 读代码找真实入口

我让 opencode 直接 curl，但**没让**它读：
- `src/main/resources/webgoat/templates/login.html` → 找到 `<form th:action="@{/login}" method='POST'>` → **真实登录入口是 POST /login**
- `src/main/java/org/owasp/webgoat/container/users/RegistrationController.java` → `@PostMapping("/register.mvc")`, `@GetMapping("/login-oauth.mvc")`
- 各 lesson 端点（`/IDOR/login`, `/InsecureLogin/login`, `/HijackSession/login` 等）
- `src/main/java/org/owasp/webgoat/container/SecurityConfig.java` → 哪些 URL 被 `permitAll`

**没强约束 = opencode 不会主动读**。它偷懒只 curl 根 URL 拿 404，然后说"端点可达"。

### 错 3：没在 404 时诘问

opencode 跑出来 `HTTP 404` 我**没问**它：
- "404 你为啥不查？"
- "404 是不是路径错了？"
- "404 是不是要带 `/WebGoat` 前缀？"

**根因**：我看 P5.4 数字 PASS 就放行，没看实际响应。**和 round 1 FAKE 一个错**。

## 用户提供的实际信息（我之前忽略）

```
容器名: webgoat-local
状态:   Up / healthy
WebGoat: http://localhost:18080/WebGoat    ← 注意 /WebGoat 前缀
WebWolf: http://localhost:19090/WebWolf    ← 注意 /WebWolf 前缀
```

我之前只说 `http://127.0.0.1:18080/`（**没 /WebGoat 前缀**），所以 curl / 返回 404，opencode 就说"端点不可达"。

## 真实的登录入口（我刚自查找到）

| 入口 | URL | 备注 |
|------|-----|------|
| 登录页 | `GET /login` | Thymeleaf form |
| 登录提交 | `POST /login` | form-encoded username+password |
| OAuth 登录 | `GET /login-oauth.mvc` | OAuth flow |
| 注册 | `POST /register.mvc` | |
| 静态资源 | `/WebGoat/css/**`, `/WebGoat/js/**` | permitAll |
| **lesson 端点** | `POST /IDOR/login`, `POST /InsecureLogin/login` 等 | 攻击面 |

**老板的 prompt 必含**：
1. 必跑 `docker ps` + `docker inspect webgoat-local` 拿真实配置
2. 必读 `login.html` 找 form action
3. 必读 `SecurityConfig.java` 找 permitAll 路径
4. 必读每个 lesson 的 Controller 找 `@PostMapping`
5. 用 admin 凭据 POST /login 拿 session cookie
6. 用 session cookie 访问受保护端点
7. 404 必查（不是"端点可达" = pass）

## 加进 boss-experience 的新规则（§ 14）

### 14.1 docker 环境强约束

```python
COMMAND = """
【环境强约束】
1. 必跑 `docker ps` 找容器名（用户给 webgoat-local）
2. 必跑 `docker inspect webgoat-local` 拿 IP/端口/volume/env
3. 必跑 `docker logs webgoat-local --tail 50` 看启动日志
4. 必跑 `docker exec -it webgoat-local bash` 进容器查进程
5. 必跑 `docker network inspect <network>` 查容器网络
"""
```

### 14.2 代码必读（不许只 curl）

```python
COMMAND += """
【代码必读 - 找真实入口】
1. 必读 `src/main/resources/*/templates/login.html` 找 form action
2. 必读 `src/main/java/**/Security*.java` 找 permitAll + formLogin
3. 必读 `src/main/java/**/controller/**Controller.java` 找 @PostMapping/@GetMapping
4. 必列**所有 lesson 端点** = grep -rE "@(Post|Get|Delete|Put)Mapping"
5. 必读 `src/main/resources/application*.yml/properties` 找 server.servlet.context-path
"""
```

### 14.3 404 必须诘问

```python
COMMAND += """
【404 必查】
- curl 返回 404 → 必查：
  1. 路径是否要带应用前缀（/WebGoat/login 而不是 /login）
  2. 是否要带 session cookie
  3. 是否要 POST 不是 GET
  4. 是不是被 SecurityConfig 拒了（401/403 而非 404）
  5. 必读 404 响应 body 找 error 提示
"""
```

### 14.4 老板的 3 句新真言

1. **必传容器名**给 opencode（用户给了 webgoat-local 就必须用）
2. **404 不是 pass**，是 FAIL 信号，必查
3. **代码必读**，不许只 curl 根 URL 拿 404 当"端点可达"

## 给未来轮次的行动

1. 改 `cross-agent-50r.py` 的 COMMAND 必含上面 3 段
2. 重跑 round 1，让 opencode 必：
   - `docker ps` 找 webgoat-local
   - 读 login.html
   - POST /login 拿 session
   - 用 session 访问受保护端点
3. 老板再读 20% 抽样 + 5% 进程抽样

## 自我打分

| 项 | 之前 | 现在 |
|----|------|------|
| 必传环境信息 | ❌ 部分（没容器名）| ✅ 必传 |
| 必读代码 | ❌ 没要求 | ✅ 必读 |
| 404 必查 | ❌ 跳过 | ✅ 必查 |
| 综合 | **D** | **A** |
