# IDOR（不安全的直接对象引用）

> **类型 ID**: `AP-BIZ-IDOR`
> **轨道**: FWD-C（业务逻辑漏洞，**无明确 sink**）
> **业务域**: 9 类必查之一（核心亮点）

## 一、定义

端点接收对象 ID（如 `userId`、`orderId`）作为路径/参数，**未校验当前用户是否有权访问该对象**，导致越权读取/修改他人数据。

## 二、典型场景

```java
// ❌ 直接用路径中的 id 查询
@GetMapping("/users/{id}")
public User getUser(@PathVariable Long id) {
    return userService.findById(id);  // 没校验当前 session 用户就是 id
}

// ❌ 批量操作无校验
@PostMapping("/orders/batch")
public List<Order> getOrders(@RequestBody List<Long> ids) {
    return orderService.findByIds(ids);  // 没校验每个 id 是否属于当前用户
}
```

## 三、检测启发式

### 3.1 关键信号
- 端点路径含 `{id}` / `{userId}` / `{orderId}` 等 ID 占位符
- Service 方法接收 ID → 查 DB → 返回对象
- **缺失**：调用方与当前 session 用户归属校验

### 3.2 AST 模式
```yaml
pattern: $SERVICE.findById($ID)
pattern: $REPO.findById($ID).orElse(null)
# 关键：缺以下模式
# 缺：if (!obj.getUserId().equals(currentUserId)) throw
```

### 3.3 SQLite 特征
```sql
SELECT m.fqn, m.class FROM method m
JOIN class cls ON cls.fqn = m.class
WHERE m.body LIKE '%findById%'
  AND m.body NOT LIKE '%getCurrentUser%'
  AND m.body NOT LIKE '%@PreAuthorize%'
  AND cls.annotation LIKE '%Controller%'
```

## 四、缺失规则模板

| 业务对象 | 应有的校验 |
|----------|----------|
| 用户数据 | 当前 session 用户 == 对象 userId，或当前用户是 admin |
| 订单 | 当前用户 == 订单 userId |
| 文档 | 当前用户有文档读权限 |
| 组织数据 | 当前用户在组织内 |

## 五、误报模式

- ID 是 enum / 白名单
- ID 是加密 token（如 UUID 自含校验）
- 已有 Filter 全局校验 session
- 已用 `@PreAuthorize("hasPermission(#id, 'Order', 'read')")`

## 六、业务影响

- **严重**：越权读取他人 PII（姓名、手机、身份证）
- **严重**：越权修改（改他人订单、收货地址）
- **致命**：越权删除（删他人数据、删他人账户）
- **特殊**：金融场景 = 资金损失

## 七、PoC 模板

```http
# 替换 ID 看是否能越权
GET /api/users/12345      # 自己的
GET /api/users/12346      # 别人的

# 批量
POST /api/orders/batch
{"ids": [1, 2, 3, ..., 9999]}

# 修改
PUT /api/users/12346
{"phone": "attacker_phone"}
```

## 八、FWD-C 必查清单

1. 端点路径含 ID 占位符？
2. Service 接收 ID 后**没有**归属校验？
3. 缺 `@PreAuthorize` 等方法级注解？
4. 缺 `@PostAuthorize("returnObject.userId == authentication.principal.id")`？
