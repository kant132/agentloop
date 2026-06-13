# SQL 注入

> **类型 ID**: `AP-INJ-SQL`
> **轨道**: FWD-A（有明确 sink）
> **业务域**: 9 类必查之一

## 一、定义

用户输入未经过参数化处理，被拼接到 SQL 语句中执行。攻击者可注入 SQL 片段读取/修改/删除数据，甚至执行数据库命令。

## 二、触发条件

满足以下全部：
- 端点接收用户输入（`@RequestParam` / `@RequestBody` / `@PathVariable` / Header）
- 数据流到 SQL 执行函数
- 路径上**没有**参数化消毒器

## 三、检测启发式

### 3.1 危险 sink 函数
| 框架 | sink 函数 |
|------|----------|
| JDBC | `Statement.executeQuery`, `Statement.execute`, `PreparedStatement.executeQuery`（拼接 SQL 时） |
| Spring | `JdbcTemplate.query`, `JdbcTemplate.update`, `NamedParameterJdbcTemplate.query` |
| MyBatis | `${}` 字符串替换，`concat()` / `||` 拼接 |
| Hibernate/JPA | `createNativeQuery` + 拼接，`createQuery` + 拼接 |
| JOOQ | `DSL.using().fetch()` + 拼接 |

### 3.2 AST 模式
```yaml
pattern: $STM.executeQuery($SQL + $VAR)
pattern: $STM.execute($SQL + $VAR)
pattern: $TEMPLATE.query("..." + $VAR + "...")
pattern: "${"  # MyBatis XML 字符串替换
```

### 3.3 SQLite 特征查询（codegraph）
```sql
SELECT m.fqn, m.class, m.file, m.line
FROM method m
WHERE m.body LIKE '%executeQuery%'
   OR m.body LIKE '%JdbcTemplate%query%'
   OR m.body LIKE '%createNativeQuery%'
```

## 四、已知消毒器（白名单）

| 消毒器 | 检测特征 | 适用 |
|--------|---------|------|
| **PreparedStatement + setXxx** | `prepareStatement` + `setString/setInt/setLong/setObject` | JDBC |
| **MyBatis #{}** | XML/注解中用 `#{}` 而非 `${}` | MyBatis |
| **NamedParameterJdbcTemplate** | `:name` 占位 + `setParameter` | Spring |
| **Hibernate Parameter** | `setParameter` / `:name` / `?` + `setXxx` | JPA |
| **JPA TypedQuery** | `createQuery(...)` + `setParameter` | JPA |

## 五、误报模式（fwd 命中时必查）

### 5.1 已消毒
- 字符串虽含 SQL 关键字，但**所有**用户输入都通过 `setString` 等参数化
- 拼接发生在 SQL 字符串**之外**（如 log 输出）

### 5.2 死代码
- 方法被定义但无调用
- 调用链上有剪枝规则命中（框架内部、测试代码）

### 5.3 受限环境
- 数据库用户权限极低
- 输入字段被强制约束（白名单正则）

## 六、业务影响

| 等级 | 场景 |
|------|------|
| 致命 | 读 / 写 / 删 全表，dump 用户表，RCE（特定 DB） |
| 严重 | 读取敏感数据，绕过登录 |
| 中危 | 报错注入获取 schema 信息 |
| 低危 | 时间盲注（慢，影响性能） |

## 七、PoC 模板

```http
# 经典注入
GET /api/users?name=' OR '1'='1

# 注释截断
GET /api/users?name=admin'--

# UNION 注入
GET /api/users?id=1 UNION SELECT password FROM users--

# 时间盲注
GET /api/users?name=' OR IF(1=1, SLEEP(5), 0)--
```

## 八、沉淀位置

发现新变体（特殊框架、新绕过技巧）→ 追加到本文"九、新变体"小节
发现新消毒器 → 追加到本文"四、消毒器"
发现新误报模式 → 追加到本文"五、误报模式"
