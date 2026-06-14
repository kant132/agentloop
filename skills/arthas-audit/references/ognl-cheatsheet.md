# OGNL 表达式速查表

OGNL (Object-Graph Navigation Language) 是 Arthas 表达式的核心语法。

## 一、核心变量（Advice 对象）

在 `watch` / `tt` / `trace` / `stack` 命令的 `express` 中可用的变量：

| 变量名 | 类型 | 含义 |
|--------|------|------|
| `params` | `Object[]` | 方法入参数组 |
| `returnObj` | `Object` | 方法返回值（仅 `-s`/`-f` 事件点可用） |
| `throwExp` | `Throwable` | 抛出的异常（仅 `-e`/`-f` 事件点可用） |
| `target` | `Object` | 当前 this 对象 |
| `clazz` | `Class` | 当前类 |
| `method` | `Method` | 当前方法 |
| `loader` | `ClassLoader` | 当前 ClassLoader |
| `#cost` | `double` | 本次调用耗时（ms） |

## 二、基础语法

```ognl
# 字面量
123                           # Integer
123L                          # Long
123.45                        # Double
true / false
"hello"                       # String
null
'a'                           # Character

# 算术
a + b
a - b
a * b
a / b
a % b

# 比较
a == b
a != b
a > b
a >= b
a < b
a <= b

# 逻辑
a && b
a || b
!a
```

## 三、对象导航

```ognl
# 访问字段
user.name                     # 公共字段
user.age

# 调用方法
user.getName()
user.getAge()
list.size()
map.get("key")
map.put("key", "value")

# 调用链
user.getAddress().getCity()
user.orders[0].total

# 数组/列表访问
arr[0]
list[0]
list.get(0)

# 静态访问（最重要！）
@class@field
@class@method()
@java.lang.System@out
@java.lang.System@getProperty("user.dir")
@com.example.Config@SECRET_KEY

# 创建对象
new java.util.ArrayList()
new com.example.User("name", 10)
new int[]{1, 2, 3}
{"a", "b", "c"}               # List 字面量
```

## 四、变量绑定

```ognl
# #varName 定义局部变量
#result=@com.example.X@method(), #result.name

# 多步表达式用逗号分隔
#a=1, #b=2, #a + #b

# 链式定义
#ctx=@com.example.SpringCtx@get(),
#user=#ctx.getBean("userService").getCurrent(),
#user.getName()
```

## 五、条件与选择

```ognl
# 三元运算
a > 0 ? 'positive' : 'negative'

# 集合过滤（重要！）
list.{? #this > 10}
list.{? #this.name.startsWith("admin")}
users.{? #this.role == "admin"}

# 集合映射
list.{#this * 2}
users.{#this.getName()}
users.{#this.getName() + ":" + #this.getAge()}

# 取第一个匹配
list.{^ #this > 10}           # first match
list.{$ #this > 10}           # last match

# isEmpty / size 检查
list.isEmpty()
list.size() > 0
```

## 六、Map 操作

```ognl
# 创建
#{ 'key1': 'value1', 'key2': 'value2' }
#{ 'name': 'alice', 'age': 30 }

# 访问
map.key
map['key']
map.get('key')

# 遍历（通过 keys）
map.keySet().{#this + "=" + map.get(#this)}
```

## 七、字符串操作

```ognl
'hello' + ' ' + 'world'       # 连接
str.length()
str.toUpperCase()
str.toLowerCase()
str.contains('sub')
str.startsWith('prefix')
str.endsWith('suffix')
str.indexOf('sub')
str.substring(0, 5)
str.replace('old', 'new')
str.split(',')
str.toString()
```

## 八、类型判断与转换

```ognl
# instanceof
obj instanceof java.lang.String
param instanceof java.util.List

# 类型转换（cast）
((java.lang.String) obj)
((java.util.List) list)

# 强制转换方法调用
((com.example.User) target).isAdmin()
```

## 九、常用审计表达式模板

### 观察方法全貌

```ognl
# 标准三件套
{params, returnObj, throwExp}

# 含 this 对象
{params, target, returnObj, throwExp}

# 含类/方法信息
{clazz.name, method.name, params, returnObj}

# Map 格式输出（HTTP API 友好）
#{ "params": params, "return": returnObj, "exception": throwExp }
```

### 字段/状态快照

```ognl
# 看 this 的所有字段
target

# 看特定字段
target.currentUser
target.securityContext
target.sessionId

# 看多个字段组合
{target.user, target.session, target.token}
```

### 条件过滤

```ognl
# 按参数值过滤
params[0] == 'admin'
params[0].length() > 100
params[0].contains('select')

# 按返回值过滤
returnObj != null
returnObj.size() > 10
returnObj.getClass().getName().contains('Exception')

# 按异常过滤
throwExp != null
throwExp.getClass().getName() == 'java.sql.SQLException'
throwExp.getMessage().contains('duplicate')

# 按耗时过滤（在 -f 事件点）
#cost > 100
#cost < 1

# 多条件组合
params[0].contains('admin') && throwExp != null
returnObj == null && params[0] != null
```

### Spring 上下文访问

```ognl
# 拿 ApplicationContext
@org.springframework.context.ApplicationContext@

# Spring Boot 启动类（假设是 main class）
@com.example.Application@context.getBean("userService")

# 通过 vmtool（推荐）
# 见 vmtool 章节，比纯 OGNL 更可靠
```

### 危险操作（PoC 验证用）

```ognl
# RCE
@java.lang.Runtime@getRuntime().exec("whoami")

# 文件读取
#f=new java.io.File("/etc/passwd"),
#is=new java.io.FileInputStream(#f),
#buf=new byte[1024],
#len=#is.read(#buf),
new java.lang.String(#buf, 0, #len)

# 文件写入（谨慎！）
#f=new java.io.FileWriter("/tmp/test.txt"),
#f.write("hello"),
#f.close()

# 网络请求（谨慎！）
#url=new java.net.URL("http://example.com/"),
#conn=#url.openConnection(),
#conn.connect(),
#conn.getResponseCode()

# JNDI 探测
@javax.naming.InitialContext@lookup("dns://log.example.com/test")

# 反射调用
#cls=@java.lang.Class@forName("com.example.X"),
#method=#cls.getMethod("method", null),
#method.invoke(null, null)
```

## 十、转义与 JSON 注意

### Shell 转义

```bash
# 单引号包整个表达式，内部用双引号
watch com.example.X method '{params[0], "literal"}' -n 1

# 包含双引号时（在 shell 双引号环境中）
watch com.example.X method "{params[0], \"literal\"}" -n 1

# HTTP API JSON 中的双引号要转义
# command 字段:
watch com.example.X method '#{ "key": params[0] }' -n 1
```

### JSON 转义（HTTP API）

调用 tunnel-server HTTP API 时，command 字段中的 OGNL 表达式：
- 整个 JSON 是双引号环境
- 表达式内部的双引号必须转义为 `\"`

```json
{
  "action": "exec",
  "command": "watch com.example.X method '#{ \"key\": params[0] }' -n 1"
}
```

### Python 调用示例

```python
# 让 Python 处理转义
cmd = 'watch com.example.X method \'#{ "key": params[0] }\' -n 1'
encoded = urllib.parse.quote(cmd, safe="")
```

## 十一、常见陷阱

| 陷阱 | 原因 | 解决 |
|------|------|------|
| 拿不到 returnObj | 在 `-b` 事件点 | 用 `-s` 或 `-f` |
| 拿不到 throwExp | 在 `-b` 或 `-s` 事件点 | 用 `-e` 或 `-f` |
| 对象展开只有 hashcode | 默认展开深度 1 | 加 `-x 3` 或更高 |
| params 为空数组 | 方法是无参方法 | 改用 target / returnObj |
| 静态访问报错 | 需要完整类名 / ClassLoader 不对 | `sc -d` 看 ClassLoader，加 `-c <hash>` |
| OGNL 语法错误 | 表达式写错 | 用简单表达式逐步验证 |
| 大对象导致 OOM | 展开太深或对象太大 | 用 `-x 1`，字段精确访问 |

## 十二、测试 OGNL 的方法

```bash
# 1. 单步验证
ognl '1+1'                    # 期望返回 2
ognl '"hello".length()'       # 期望返回 5
ognl '@java.lang.System@currentTimeMillis()'   # 期望返回时间戳

# 2. 渐进测试
ognl '#x=1, #x'               # 变量绑定
ognl '#list={1,2,3}, #list.size()'   # 列表
ognl '#map=#{ "a":1}, #map.a'        # Map

# 3. 真实场景
ognl '@java.lang.System@getProperties()'   # 全部系统属性
```
