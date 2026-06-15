# NoSQL 注入

> **类型 ID**: `AP-INJ-NOSQL`
> **轨道**: FWD-A
> **业务域**: 9 类必查之一（仅在项目用 NoSQL 时加载）

## 一、定义

用户输入未经过类型校验，被传入 NoSQL 查询构造器。MongoDB / CouchDB / Redis / Elasticsearch 等典型场景，攻击者可绕过认证、读取数据。

## 二、典型场景

### 2.1 MongoDB 注入
```java
// ❌ 危险：直接传字符串到 query
DBObject query = new BasicDBObject("name", userInput);
dbc.find(query);

// ❌ 危险：JSON 解析后直接 query
DBObject query = (DBObject) JSON.parse(userInput);
dbc.find(query);

// ❌ 危险：$where 注入
DBObject query = new BasicDBObject("$where", "this.name == '" + userInput + "'");
```

### 2.2 Redis 注入
```java
// ❌ 危险：EVAL 注入
jedis.eval("return redis.call('get', KEYS[1])", ...);  // userInput 在 KEYS[1]
```

### 2.3 Elasticsearch 注入
```java
// ❌ 危险：Lucene query 拼接
QueryBuilders.queryStringQuery("name:" + userInput);
```

## 三、检测启发式

### 3.1 AST 模式
```yaml
pattern: new BasicDBObject($KEY, $VAL + $VAR)
pattern: JSON.parse($INPUT + $VAR)
pattern: $QUERY.$where("..." + $VAR)
```

### 3.2 SQLite 特征
```sql
SELECT m.fqn FROM method m
WHERE m.body LIKE '%BasicDBObject%'
   OR m.body LIKE '%JSON.parse%'
   OR m.body LIKE '%queryString%'
   OR m.body LIKE '%EVAL%'
```

## 四、消毒器

| 消毒器 | 检测特征 |
|--------|---------|
| **类型检查** | `if (input instanceof String) {...}` |
| **白名单** | `if (!ALLOWED_KEYS.contains(key)) throw` |
| **JSON Schema 校验** | `JsonSchema.validate` |
| **MongoDB Bson Document** | 用 `Document.parse(json)` 后白名单字段 |

## 五、误报模式

- 已用白名单过滤
- 输入字段是基本类型（`int id`）→ 不可能注入
- 内部调用 → 用户不可达

## 六、业务影响

- MongoDB：读取/修改任意文档，绕过登录
- Redis：执行任意 Lua 脚本
- ES：dump 整个索引数据

## 七、PoC 模板

```json
// MongoDB
{"name": {"$ne": null}}      // 不等于 null = 匹配所有
{"$where": "1==1"}
{"$regex": ".*"}

{"username": "admin", "password": {"$gt": ""}}
```
