# Jackson 多态反序列化

> **类型 ID**: `AP-DESER-JACKSON`
> **轨道**: FWD-A
> **业务域**: 9 类必查之一（仅在项目用 Jackson 时加载）

## 一、定义

Jackson 开启了多态类型（`@JsonTypeInfo`、`enableDefaultTyping`、`@class`），攻击者可通过 `{"@class":"...gadget..."}` 触发反序列化漏洞。

## 二、典型场景

```java
// ❌ 启用多态
ObjectMapper mapper = new ObjectMapper();
mapper.enableDefaultTyping();  // 老 API
// 或
mapper.activateDefaultTyping(...);  // 新 API

// ❌ 注解多态
@JsonTypeInfo(use = JsonTypeInfo.Id.CLASS)  // 危险
public class User { ... }
```

## 三、检测启发式

### 3.1 AST 模式
```yaml
pattern: $MAPPER.enableDefaultTyping()
pattern: $MAPPER.activateDefaultTyping(...)
pattern: @JsonTypeInfo(use = JsonTypeInfo.Id.CLASS)
```

### 3.2 SQLite 特征
```sql
SELECT m.fqn FROM method m
WHERE m.body LIKE '%enableDefaultTyping%'
   OR m.body LIKE '%activateDefaultTyping%'
   OR m.body LIKE '%JsonTypeInfo%'
```

## 四、消毒器

- 关闭多态
- 用白名单 `@JsonTypeInfo` 限定子类
- 升级到 Jackson 2.10+ 并配置 `PolymorphicTypeValidator`
