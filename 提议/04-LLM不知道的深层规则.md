# LLM 不知道的深层规则：从哲学到实践

> 本文是前三篇哲学文档的实践总结。提取 LLM 天生缺失的、无法从训练数据中学到的深层规则。
> 这些规则不是「知识」，而是「认知缺陷的补丁」。

---

## 一、污点分析的深层规则（15 条）

### 规则 1：消毒器的时序正确性

**规则**：消毒器必须在数据到达 sink **之前**调用，且消毒后的数据必须被实际使用。

**反例**：
`java
String sql = "SELECT * FROM users WHERE name='" + userInput + "'";  // 拼接在前
PreparedStatement ps = conn.prepareStatement(sql);  // prepareStatement 在后，但 SQL 已经拼接好了
ps.setString(1, userInput);  // setString 被调用，但 SQL 已经包含了 userInput
`

**检查点**：
- 消毒器是否在数据到达 sink 之前调用？
- 消毒后的数据是否被实际使用（而非使用原始数据）？
- 如果消毒器在 sink 之后调用，视为无效消毒。

### 规则 2：消毒器的上下文相关性

**规则**：消毒器只对特定 sink 类型有效。HTML 转义对 SQL 无效，URL 转义对 HTML body 中的 XSS 部分无效。

**反例**：
`java
String safe = HtmlUtils.htmlEscape(userInput);  // HTML 转义
String sql = "SELECT * FROM users WHERE name='" + safe + "'";  // 对 SQL 无效
`

**检查点**：
- 消毒器类型是否匹配 sink 类型？
- 如果不匹配，视为无效消毒。

### 规则 3：参数化消毒的完整性

**规则**：参数化消毒（如 PreparedStatement）要求所有用户输入都通过参数绑定，不能有任何拼接。

**反例**：
`java
String sql = "SELECT * FROM users WHERE name='" + safe + "' AND status='active'";  // status 是硬编码，但 name 是拼接
PreparedStatement ps = conn.prepareStatement(sql);
ps.setString(1, userInput);  // 但 SQL 中已经没有占位符了
`

**检查点**：
- SQL 中是否有任何用户输入被直接拼接？
- 如果有，即使后续调用了 setString，也视为无效消毒。

### 规则 4：类型转换消毒的绝对性

**规则**：类型转换（如 Integer.parseInt）是绝对消毒。一旦数据变成 int，它就不可能包含注入 payload。

**反例**：
`java
int id = Integer.parseInt(userInput);  // 如果 userInput 不是数字，会抛异常
String sql = "SELECT * FROM users WHERE id=" + id;  // 安全，id 已经是 int
`

**检查点**：
- 数据是否被转换为 int/long/boolean/enum？
- 如果是，视为绝对消毒，可以剪枝。
- 注意：如果类型转换失败会抛异常，异常消息可能包含用户输入（信息泄露）。

### 规则 5：白名单 vs 黑名单

**规则**：白名单校验是强消毒，黑名单校验是弱消毒。

**反例**：
`java
// 黑名单（弱消毒）
if (userInput.matches(".*['\";].*")) {  // 拒绝包含 ' " ; 的输入
    throw new IllegalArgumentException();
}
// 攻击者可以用 %27（URL 编码）绕过

// 白名单（强消毒）
if (!userInput.matches("[a-zA-Z0-9]+")) {  // 只允许字母数字
    throw new IllegalArgumentException();
}
// 攻击者无法绕过
`

**检查点**：
- 校验是白名单还是黑名单？
- 黑名单视为弱消毒，高危 sink 仍然报漏洞。
- 白名单视为强消毒，可以剪枝。

### 规则 6：集合和容器的污点传播

**规则**：集合中的元素级污点传播，不是整个集合污染。

**反例**：
`java
List<String> list = new ArrayList<>();
list.add(userInput);  // 索引 0 污染
list.add("safe");     // 索引 1 安全
String result = list.get(0);  // 污染
String safe = list.get(1);    // 安全
`

**检查点**：
- list.add(untrusted) → 该索引位置污染
- list.get(i) → 如果 i 可控，返回污染值
- map.put(key, untrusted) → 该 key 对应的 value 污染
- map.get(key) → 如果 key 匹配，返回污染值

### 规则 7：异常处理的污点传播

**规则**：异常消息可能包含用户输入，是污点传播路径。

**反例**：
`java
try {
    int id = Integer.parseInt(userInput);  // 如果 userInput 不是数字
} catch (NumberFormatException e) {
    logger.error("Invalid input: " + userInput);  // 信息泄露
    throw new MyException("Invalid input: " + userInput);  // 异常消息污染
}
`

**检查点**：
- 	hrow new Exception(userInput) → 异常消息污染
- catch 块中 e.getMessage() → 可能污染
- 异常消息写入日志 → 信息泄露

### 规则 8：回调和异步的污点传播

**规则**：回调函数的参数继承上游的污点状态。

**反例**：
`java
CompletableFuture.supplyAsync(() -> userInput)
    .thenApply(x -> process(x));  // x 继承 userInput 的污点状态
`

**检查点**：
- 	henApply / 	henAccept / 	henCompose 的参数继承上游的污点状态
- @Async 方法的参数继承调用方的污点状态
- MQ 消费者的消息体继承生产者的污点状态

### 规则 9：反射和动态分派的污点传播

**规则**：反射调用中参数的污点状态丢失，需要动态分析。

**反例**：
`java
Method method = cls.getMethod("process", String.class);
method.invoke(obj, userInput);  // userInput 的污点状态在反射中丢失
`

**检查点**：
- Method.invoke(obj, args) 中 args 的污点状态丢失
- proxy.invoke(args) 中不知道 proxy 实际调用什么
- 遇到反射/代理，标注 
eeds_dynamic_analysis，降级为「可能传播」

### 规则 10：框架隐式消毒

**规则**：某些框架会自动消毒用户输入，但 LLM 不知道。

**反例**：
`java
// Spring MVC 的 @RequestParam 自动做了 URL 解码
String name = request.getParameter("name");  // 已经 URL 解码

// Thymeleaf 默认转义 HTML
<p th:text=""></p>  // 自动 HTML 转义，安全
<p th:utext=""></p>  // 不转义，XSS
`

**检查点**：
- 项目使用什么框架？
- 该框架有哪些隐式消毒？
- 存到 项目/{groupId}/implicit-sanitizers.json

### 规则 11：跨方法的污点传播记录

**规则**：每一层方法调用必须明确记录输入输出的污点状态。

**反例**：
`java
public void process(String userInput) {
    String safe = sanitize(userInput);  // 消毒
    dao.save(safe);  // 但 dao.save 内部可能又拼接了 SQL
}
`

**检查点**：
- 输入参数的污点状态
- 方法内部对参数的变换
- 输出参数的污点状态

### 规则 12：污点衰减

**规则**：污点不是二元的（trusted/untrusted），而是一个连续值。

**污点程度**：
- untrusted：直接来自外部输入，未经任何处理
- weakly_sanitized：经过了弱消毒（如黑名单过滤）
- strongly_sanitized：经过了强消毒（如白名单校验）
- 	rusted：经过了类型转换或参数化

**检查点**：
- untrusted → 任何 sink 都报漏洞
- weakly_sanitized → 高危 sink 报漏洞，低危 sink 报提示
- strongly_sanitized → 高危 sink 报提示，低危 sink 不报
- 	rusted → 不报

### 规则 13：多参数 sink 的部分消毒

**规则**：如果 sink 有多个参数，只有部分被消毒，仍然危险。

**反例**：
`java
String safe1 = sanitize(userInput1);  // 参数 1 消毒
String unsafe2 = userInput2;          // 参数 2 未消毒
dao.query(safe1, unsafe2);            // 仍然危险
`

**检查点**：
- sink 的所有参数是否都被消毒？
- 如果只有部分消毒，未消毒的参数仍然危险。

### 规则 14：条件分支的污点传播

**规则**：条件分支中，只有被执行的分支会影响污点状态。

**反例**：
`java
String result;
if (condition) {
    result = sanitize(userInput);  // 分支 1：消毒
} else {
    result = userInput;            // 分支 2：未消毒
}
dao.query(result);  // 如果 condition 为 false，result 未消毒
`

**检查点**：
- 条件分支是否都消毒了？
- 如果只有部分分支消毒，未消毒的分支仍然危险。

### 规则 15：循环中的污点传播

**规则**：循环中的污点传播可能被累积。

**反例**：
`java
String result = "";
for (String item : list) {
    result += item;  // 如果 list 中有污染项，result 会被污染
}
dao.query(result);  // 危险
`

**检查点**：
- 循环中是否有污染项被累积？
- 如果有，循环结束后结果是污染的。

---

## 二、业务逻辑分析的深层规则（10 条）

### 规则 1：归属校验缺失

**规则**：如果用户可以操作不属于自己的对象，必须检查归属校验。

**反例**：
`java
@PostMapping("/orders/{id}/cancel")
public Result cancel(@PathVariable Long id) {
    Order order = orderService.getById(id);
    // 没有校验 order.getUserId() == currentUser.getId()
    orderService.cancel(id);  // 攻击者可以取消他人的订单
    return Result.success();
}
`

**检查点**：
- 对象是否属于当前用户？
- 如果没有校验，报 IDOR 漏洞。

### 规则 2：状态校验缺失

**规则**：如果操作依赖前置状态，必须检查状态校验。

**反例**：
`java
@PostMapping("/orders/{id}/ship")
public Result ship(@PathVariable Long id) {
    Order order = orderService.getById(id);
    // 没有校验 order.getStatus() == "PAID"
    orderService.ship(id);  // 攻击者可以在未付款时发货
    return Result.success();
}
`

**检查点**：
- 操作是否依赖前置状态？
- 如果没有校验，报状态机绕过漏洞。

### 规则 3：数量校验缺失

**规则**：如果操作涉及数量，必须检查数量合理性。

**反例**：
`java
@PostMapping("/cart/add")
public Result add(@RequestBody CartItem item) {
    // 没有校验 item.getQuantity() > 0
    cartService.add(item);  // 攻击者可以传入负数数量，导致退款
    return Result.success();
}
`

**检查点**：
- 数量是否在合理范围？
- 如果没有校验，报数量校验缺失漏洞。

### 规则 4：价格校验缺失

**规则**：如果操作涉及价格，必须由后端计算，不能信任前端传入的价格。

**反例**：
`java
@PostMapping("/orders")
public Result create(@RequestBody OrderRequest req) {
    Order order = new Order();
    order.setTotalPrice(req.getTotalPrice());  // 信任前端传入的价格
    orderService.create(order);  // 攻击者可以篡改价格
    return Result.success();
}
`

**检查点**：
- 价格是否由后端计算？
- 如果信任前端价格，报价格篡改漏洞。

### 规则 5：并发控制缺失

**规则**：如果操作涉及资源竞争，必须有并发控制。

**反例**：
`java
@PostMapping("/products/{id}/buy")
public Result buy(@PathVariable Long id) {
    Product product = productService.getById(id);
    if (product.getStock() > 0) {
        product.setStock(product.getStock() - 1);  // 非原子操作
        productService.update(product);  // 并发问题：超卖
    }
    return Result.success();
}
`

**检查点**：
- 操作是否涉及资源竞争？
- 是否有锁或事务？
- 如果没有，报并发漏洞。

### 规则 6：频率限制缺失

**规则**：如果操作可以被滥用，必须有频率限制。

**反例**：
`java
@PostMapping("/coupons/claim")
public Result claim() {
    Coupon coupon = couponService.generate();  // 没有频率限制
    couponService.claim(currentUser, coupon);  // 攻击者可以无限刷券
    return Result.success();
}
`

**检查点**：
- 操作是否可以被滥用？
- 是否有频率限制？
- 如果没有，报频率限制缺失漏洞。

### 规则 7：第三方回调验证缺失

**规则**：如果接收第三方回调，必须验证回调的合法性。

**反例**：
`java
@PostMapping("/payment/callback")
public Result callback(@RequestBody PaymentCallback cb) {
    // 没有验证回调的签名
    orderService.updateStatus(cb.getOrderId(), "PAID");  // 攻击者可以伪造支付成功
    return Result.success();
}
`

**检查点**：
- 回调是否有签名验证？
- 如果没有，报回调验证缺失漏洞。

### 规则 8：业务规则矛盾

**规则**：如果代码实现了两个互相矛盾的业务规则，必须报告。

**反例**：
`java
// 规则 1：退款后不能再发货
@PostMapping("/orders/{id}/refund")
public Result refund(@PathVariable Long id) {
    orderService.updateStatus(id, "REFUNDED");
    return Result.success();
}

// 规则 2：但发货接口没有检查状态
@PostMapping("/orders/{id}/ship")
public Result ship(@PathVariable Long id) {
    orderService.ship(id);  // 即使状态是 REFUNDED 也可以发货
    return Result.success();
}
`

**检查点**：
- 业务规则之间是否有矛盾？
- 如果有，报业务规则矛盾漏洞。

### 规则 9：隐式信任客户端数据

**规则**：不能信任客户端传入的任何业务数据，必须由服务端计算或验证。

**反例**：
`java
@PostMapping("/orders")
public Result create(@RequestBody OrderRequest req) {
    Order order = new Order();
    order.setDiscount(req.getDiscount());  // 信任前端传入的折扣
    order.setTotalPrice(req.getTotalPrice());  // 信任前端传入的总价
    orderService.create(order);
    return Result.success();
}
`

**检查点**：
- 业务数据是否由服务端计算？
- 如果信任客户端数据，报隐式信任漏洞。

### 规则 10：业务流程步骤跳跃

**规则**：如果业务流程有多个步骤，必须防止步骤跳跃。

**反例**：
`java
// 步骤 1：下单
@PostMapping("/orders")
public Result create() { ... }

// 步骤 2：付款
@PostMapping("/orders/{id}/pay")
public Result pay() { ... }

// 步骤 3：发货
@PostMapping("/orders/{id}/ship")
public Result ship() {
    // 没有校验步骤 2 是否完成
    orderService.ship(id);  // 攻击者可以跳过付款直接发货
    return Result.success();
}
`

**检查点**：
- 业务流程是否有多个步骤？
- 每个步骤是否校验前置步骤？
- 如果没有，报步骤跳跃漏洞。

---

## 三、威胁分析的深层规则（8 条）

### 规则 1：核心资产识别

**规则**：必须先识别核心资产，才能确定威胁的优先级。

**检查点**：
- 攻击者想要什么？（用户数据、系统控制权、业务资源）
- 核心资产在哪里？（数据库、文件系统、内存）
- 核心资产的访问路径是什么？

### 规则 2：攻击入口识别

**规则**：必须先识别攻击入口，才能确定攻击路径。

**检查点**：
- 攻击者从哪里进入？（外部 API、用户输入、第三方集成）
- 哪些入口是对外暴露的？
- 哪些入口是内部使用的？

### 规则 3：信任边界识别

**规则**：必须先识别信任边界，才能确定防守重点。

**检查点**：
- 信任边界在哪里？（认证、鉴权、数据校验、网络隔离）
- 信任边界如何强制执行？
- 信任边界可以被绕过吗？

### 规则 4：攻击链构造

**规则**：必须从攻击者视角构造攻击链，而不是从防守者视角列清单。

**检查点**：
- 攻击者的最短路径是什么？
- 攻击者的最高回报路径是什么？
- 攻击者的最低成本路径是什么？

### 规则 5：威胁组合分析

**规则**：必须分析威胁之间的依赖和组合关系。

**检查点**：
- 哪些威胁是其他威胁的前提？（如 Info Disclosure 是 Tampering 的前提）
- 哪些威胁可以组合成更严重的攻击？
- 哪些威胁组合是致命的？

### 规则 6：薄弱环节识别

**规则**：必须识别防守者的薄弱环节。

**检查点**：
- 哪些安全措施是已知的绕过方式？
- 哪些安全措施是部分实现的？
- 哪些安全措施是缺失的？

### 规则 7：攻击可行性评估

**规则**：必须评估每条攻击链的可行性。

**检查点**：
- 技术难度（低/中/高）
- 时间成本（低/中/高）
- 回报（低/中/高）
- 可行性（高/中/低）

### 规则 8：威胁优先级排序

**规则**：必须按可行性排序威胁，而不是按类别列举。

**检查点**：
- 高可行性 + 高回报 → 致命威胁
- 高可行性 + 中回报 → 严重威胁
- 中可行性 + 高回报 → 严重威胁
- 其他 → 中/低威胁

---

## 四、一句话总结

**这些规则不是知识，而是认知缺陷的补丁。LLM 天生缺失这些认知，必须在 Skill 中明确告诉它。**