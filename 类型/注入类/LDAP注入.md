# LDAP 注入

> **类型 ID**: `AP-INJ-LDAP`
> **轨道**: FWD-A
> **业务域**: 9 类必查之一（仅在项目用 LDAP 时加载）

## 一、定义

用户输入未过滤，被拼接到 LDAP 过滤器或 DN 中，攻击者可绕过认证、枚举用户。

## 二、典型场景

### 2.1 过滤器注入
```java
// ❌ 危险
String filter = "(uid=" + userInput + ")";
ctx.search("ou=people", filter, controls);

// ❌ 复杂过滤拼接
String filter = "(&(uid=" + userInput + ")(objectClass=person))";
```

### 2.2 DN 注入
```java
// ❌ DN 拼接
String dn = "cn=" + userInput + ",ou=users,dc=example,dc=com";
ctx.search(dn, ...);
```

## 三、检测启发式

### 3.1 AST 模式
```yaml
pattern: $CTX.search($BASE, $FILTER + $VAR, $CTRLS)
pattern: new InitialDirContext($ENV + $VAR)
```

### 3.2 SQLite 特征
```sql
SELECT m.fqn FROM method m
WHERE m.body LIKE '%InitialDirContext%'
   OR m.body LIKE '%DirContext%search%'
   OR m.body LIKE '%LdapTemplate%'
```

## 四、消毒器

| 消毒器 | 检测特征 |
|--------|---------|
| **RFC 4515 转义** | `LDAPFilter.escape(userInput)` (UnboundID) |
| **白名单字符** | `Pattern.matches("[a-zA-Z0-9]+", userInput)` |
| **DN 转义** | `LdapEncoder.filterEncode` (Spring LDAP) |

## 五、误报模式

- 完整白名单校验
- 输入字段是 `int` / `enum` 等强类型
- LDAP 上下文完全内部

## 六、业务影响

- 认证绕过
- 用户枚举
- 读取任意 LDAP 条目
