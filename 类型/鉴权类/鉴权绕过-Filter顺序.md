# 鉴权绕过 — Filter 顺序错

> **类型 ID**: `AP-AUTH-FILTER-ORDER`
> **轨道**: FWD-B
> **业务域**: 9 类必查之一

## 一、定义

Spring Security Filter 链顺序错乱，导致鉴权 Filter 在业务 Filter 之后执行，业务逻辑先于鉴权被处理，攻击者绕过。

## 二、典型场景

```java
// ❌ 错误顺序：自定义 Filter 在 AuthorizationFilter 之后
http.addFilterBefore(myCustomFilter, AuthorizationFilter.class);
// 正确应 addFilterBefore 在 AuthorizationFilter 之前，确保先鉴权
```

```java
// ❌ ExceptionTranslationFilter 顺序错
http.addFilter(myFilter);  // 没指定位置，可能在 ExceptionTranslationFilter 之后
// 异常无法被正确处理，鉴权可能失效
```

## 三、检测启发式

### 3.1 关键信号
- 自定义 Filter 注册时**未**指定相对于 AuthorizationFilter 的位置
- 业务 Filter / 异步 Filter / CORS Filter 注册位置错
- 鉴权注解 `@PreAuthorize` 与 SecurityConfig 顺序冲突

### 3.2 SQLite 特征
```sql
-- 找 addFilter / addFilterBefore / addFilterAfter 错误用法
SELECT m.fqn, m.body FROM method m
WHERE m.body LIKE '%addFilter(%'
  AND m.body NOT LIKE '%addFilterBefore%'
  AND m.body NOT LIKE '%addFilterAfter%'
```

## 四、缺失规则

| Filter 类型 | 应在 AuthorizationFilter 之前 |
|------------|------------------------------|
| 自定义鉴权 Filter | ✅ |
| CORS Filter | ✅ |
| CSRF Filter | ✅ |
| 业务 Filter | ⚠️ 视情况 |

## 五、误报模式

- 用 Spring Security 6+ 的 Lambda DSL（顺序自动）
- Filter 在另一个完全独立的 SecurityFilterChain 中
