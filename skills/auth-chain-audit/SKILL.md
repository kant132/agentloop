---
name: auth-chain-audit
description: "Filter/Interceptor 认证鉴权链审计。审计运行时 filter 链加载顺序、路径归一化差异、校验逻辑缺陷。触发词：'filter chain audit'、'鉴权链审计'。"
---

# Filter/Interceptor 认证鉴权链审计

专注：认证/鉴权绕过——filter 顺序、路径归一化、校验实现缺陷。

## 思路

声明顺序 ≠ 运行时顺序。路径匹配在 Nginx/Tomcat/Spring 各层行为不同。认证逻辑"看起来正确"不等于"实际正确"。

## 步骤（5 步强制）

### 1. 运行时 Filter Chain 发现（arthas）

- `sm`/`sc` 定位 FilterChainProxy / FilterRegistrationBean
- `watch` 获取 getFilters()/getInterceptors() 的运行时返回值
- 对比声明顺序与实际执行顺序

关注：auth filter 是否在 CORS filter 之前？error filter 是否吞掉 auth 异常导致绕过？

### 2. 路径归一化差异审计

逐层检查：
- **Nginx**：location 匹配模式、rewrite 是否吃掉特殊字符、proxy_pass trailing slash
- **Tomcat**：`/..;/`（CVE-2018-11759）、`;jsessionid` 是否从路径剥离、URL 编码处理
- **Spring**：ant_path_matcher vs path_pattern_parser 行为差异、`/**` vs `/*` 范围

**必做**：用不同路径变体 curl 请求，arthas watch filter 是否被触发。

### 3. 校验逻辑审计（每种认证类型逐一检查）

**JWT**：alg=none？密钥硬编码/强度？exp/iat/nbf 是否检查？真 verify 还是只 decode？

**OAuth2**：本地验证 vs 内省降级？audience/issuer 完整？PKCE 强制？state 验证？

**Session**：登录后 regenerate session ID？session fixation？

**Basic Auth / 自定义**：constant-time 比较？bcrypt/scrypt/argon2 vs 裸 hash？

**SSO**：OIDC state/nonce？SAML 签名完整验证？XML Signature Wrapping？

### 4. 路径绕过构造（前 3 步发现可疑点时）

arthas watch 目标 filter 的 doFilter/preHandle → 构造路径变体 → 对比是否触发。

### 5. 审计结论

每个发现：风险一句话 + 证据 + 严重度 + 修复方向。

## 常见坑

- auth filter 在 CORS filter 之后 → 绕过
- error handler 吞掉 auth 异常 → 隐式放行
- 隐式放行：没配置 = 默认允许
- 路径处理差异（proxy_pass 带不带 slash）
- 只检查了一个路径变体
- 跳过某种认证类型的校验逻辑审计

## 禁止行为

- 不通过 arthas 获取运行时状态，只看代码声明
- 只检查一个路径变体
- 跳过任何认证类型的校验逻辑审计
