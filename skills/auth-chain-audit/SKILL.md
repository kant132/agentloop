---
name: auth-chain-audit
description: "Filter/Interceptor 认证鉴权链审计子 agent 专用 skill。通过 arthas 审计运行时 filter 链加载顺序、路径归一化差异、校验逻辑缺陷。支持认证类型：JWT、OAuth2、SSO、Session、Basic Auth、自定义认证。触发词：'审计 filter'、'检查拦截器'、'filter chain audit'、'interceptor 审计'、'authentication audit'、'鉴权链检查'、'路径绕过审计'。"
---

# Filter/Interceptor 认证鉴权链审计

## 审计流程（5 步，不可跳过）

### 1. 运行时 Filter Chain 发现（arthas 强制）

通过 arthas 获取**运行时真实**的 filter/interceptor 加载顺序：
- `sm` / `sc` 命令定位 `FilterChainProxy` / `FilterRegistrationBean`
- `watch` 获取 `getFilters()` / `getInterceptors()` 的运行时返回值
- 对比声明顺序与实际执行顺序

关注点：
- auth filter 是否在 CORS filter 之前执行？
- logging/error filter 是否吞掉了 auth 异常导致绕过？
- Spring Security 内置 filter 位置是否正确？

### 2. 路径归一化差异审计

逐层检查路径处理差异：

**Nginx 层：**
- `location` 匹配模式（前缀/正则/精确）
- `rewrite` 规则是否会吃掉路径特殊字符
- `proxy_pass` 是否带 trailing slash（路径拼接差异）

**Tomcat 层：**
- `/..;/` 处理（CVE-2018-11759 类问题）
- `;jsessionid=xxx` 分号参数是否从路径剥离后再匹配
- URL 编码处理（`%2f` → `/`，`%5c` → `\`）

**Spring 层：**
- `ant_path_matcher` vs `path_pattern_parser`（Spring 5.3+）的行为差异
- `/**` 与 `/*` 匹配范围差异
- trailing slash 默认行为（`/api/user` vs `/api/user/`）

**跨层对比（必做）：**
用 curl 发出不同路径变体请求，arthas watch filter 的 `doFilter`/`preHandle` 是否真的被触发。

### 3. 校验逻辑审计

逐个检查每个 auth/authz filter 的校验实现：

**JWT：**
- 算法：是否允许 `alg=none`？alg 与 key 是否匹配？
- 密钥：硬编码？强度？环境变量可能为空？
- 有效期：是否检查 exp/iat/nbf claim？
- 签名：真的 verify 了还是只 decode 不 verify？

**OAuth2：**
- token 校验用的是本地验证还是内省（introspection）降级？
- audience/issuer 校验是否完整？
- PKCE 是否强制？（针对 public client）
- refresh token 是否可被重放？
- state 参数是否验证？（防 CSRF）

**Session：**
- 登录成功后是否 regenerate session ID？（session fixation）
- session 超时/登出/多设备互踢是否完整？

**Basic Auth / 自定义认证：**
- 是否使用时间安全的比较函数（constant-time comparison）？
- 对比密文/签名/哈希：是否校验完整长度？
- 密码存储用的是 bcrypt/scrypt/argon2 还是裸 hash？
- 隐式放行问题：没有配置 = 默认允许？

**SSO（OIDC/SAML）：**
- OIDC 的 state/nonce 是否校验？
- SAML 签名是否验证完整（还是只验证部分 XML）？
- SAML 的 XML Signature Wrapping 攻击面？

### 4. 路径绕过构造（如前 3 步发现可疑点）

针对路径归一化差异：
- 用 arthas `watch` 目标 filter 的 `doFilter`/`preHandle`，记录实际 requestURI
- 构造多个路径变体，对比 filter 是否被触发
- 如有绕过：记录构造的 payload 和 arthas 捕获的证据

### 5. 审计结论

每个发现项：
- 风险点一句话描述
- 技术证据（arthas 输出 / 代码片段）
- 严重程度：高 / 中 / 低
- 修复方向一句话

## 输出格式

### 运行时 Filter 链
[实际加载顺序，声明顺序的差异]

### 路径归一化差异
| 层 | 发现 |
|----|------|
| Nginx | [结论] |
| Tomcat | [结论] |
| Spring | [结论] |
| 跨层风险 | [结论] |

### 校验逻辑发现
[逐个 filter 的审计结果]

### 发现汇总
| # | 风险点 | 证据 | 严重度 | 修复方向 |
|---|-------|------|--------|---------|

## 禁止行为
- 不通过 arthas 获取运行时状态，只看代码声明
- 只检查一个路径变体
- 跳过任何一种认证类型的校验逻辑审计
```
<!-- OMO_INTERNAL_INITIATOR -->