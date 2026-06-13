# Arthas 命令速查卡

按审计用途分组，每条命令标注常用度。

## 一、基础侦察（每次审计必跑）

```bash
# 确认 agent 在线
version

# JVM 实时面板
dashboard -n 1

# JVM 详情（版本、启动参数、内存）
jvm

# 线程分析
thread                          # 全部线程
thread -n 3                     # 最忙 3 个
thread <id>                     # 指定线程堆栈
thread --state WAITING          # 等待中的
thread -b                       # 阻塞中的（找死锁）

# 内存
memory

# 系统属性（找敏感配置）
sysprop
sysprop java.class.path
sysprop user.dir

# 环境变量
sysenv
sysenv | grep -i password
sysenv | grep -i access_key

# 日志配置
logger
logger --name ROOT
logger --name com.example -l debug    # 动态改日志级别
```

## 二、类与方法搜索

```bash
# 搜索已加载类
sc com.example.*                # 通配符
sc -E '.*\\$Proxy.*'            # 正则（代理类）
sc javax.servlet.Filter         # 所有 Servlet Filter
sc javax.servlet.http.*

# 类详情（带 ClassLoader hash）
sc -d com.example.MyClass
sc -d -f com.example.MyClass    # 含字段
sc -d -m com.example.MyClass    # 含方法

# 搜索方法
sm com.example.MyClass
sm com.example.MyClass login
sm -d com.example.MyClass login # 详细签名

# 排除匹配
sc --exclude-class-pattern com.demo.Test
```

## 三、ClassLoader 分析

```bash
# 概览
classloader -l                  # 实例列表
classloader -t                  # 继承树
classloader                     # 类型统计

# 深入
classloader -c <hash>           # URLs
classloader -c <hash> -r META-INF/MANIFEST.MF   # 查资源
classloader -c <hash> --load com.example.X      # 测试加载
classloader --url-stat          # 已用/未用 URLs

# --url-classes（分析类来自哪个 jar）
classloader -c <hash> --url-classes
classloader -c <hash> --url-classes --jar spring-core
classloader -c <hash> --url-classes --class org.springframework

# metaspace
classloader-metaspace
```

## 四、反编译

```bash
# 整个类
jad com.example.MyClass

# 指定方法
jad com.example.MyClass myMethod

# 只输出源码（不带 ClassLoader/Location 头）
jad --source-only com.example.MyClass

# 指定 ClassLoader
jad -c <hash> com.example.MyClass
jad --classLoaderClass org.springframework.boot.loader.LaunchedURLClassLoader com.example.X

# 不带行号
jad com.example.MyClass --lineNumber false

# dump 到目录
jad com.example.MyClass -d /tmp/jad/dump
```

## 五、调用链追踪

```bash
# stack - 谁调用了当前方法
stack com.example.MyClass method
stack -E 'classA|classB' 'method1|method2'
stack com.example.MyClass method 'params[0].length() > 100'
stack com.example.MyClass method -n 3              # 限制次数
stack -c <hash> com.example.MyClass method

# trace - 方法内部调用 + 耗时
trace com.example.MyClass method
trace --skipJDKMethod false com.example.MyClass method    # 含 JDK 方法
trace -E 'classA|classB' 'method1|method2'
trace com.example.MyClass method '#cost > 500'
trace -c <hash> com.example.MyClass method

# monitor - 方法监控统计
monitor com.example.MyClass method -c 5            # 5 秒统计周期
```

## 六、方法观察（核心）

```bash
# 标准观察
watch com.example.MyClass method '{params,returnObj,throwExp}' -x 3 -n 5

# 事件点
watch com.example.MyClass method '{params}' -b              # 调用前
watch com.example.MyClass method '{returnObj}' -s           # 返回后
watch com.example.MyClass method '{throwExp}' -e            # 异常后
watch com.example.MyClass method '{params,returnObj}' -f    # 结束（默认）

# 条件过滤
watch com.example.MyClass method '{params}' 'params[0].length() > 10' -n 5
watch com.example.MyClass method '{params,returnObj}' '#cost > 200' -n 5

# 对象字段
watch com.example.MyClass method 'target.fieldName' -x 3 -n 5

# 多事件点
watch com.example.MyClass method '{params, target}' -b -s -f -n 2

# Verbose（显示条件计算结果）
watch -v com.example.MyClass method '{params}' 'condition'

# 类数限制
watch com.example.MyClass method '{params}' -m 1

# 排除指定类
watch javax.servlet.Filter * --exclude-class-pattern com.demo.Test
```

## 七、时空隧道（tt）

```bash
# 录制
tt -t com.example.MyClass method -n 5
tt -t com.example.MyClass method -m 1

# 列表
tt -l

# 检索
tt -s 'method.name=="login"'
tt -s 'throwExp != null'

# 详情
tt -i 1000
tt -i 1000 -v                     # verbose

# 重放
tt -i 1000 -p
tt -i 1000 -p --replay-times 3 --replay-interval 500

# OGNL 查录制对象
tt -w 'target.fieldName' -i 1000
tt -w '@com.example.X@method()' -i 1000

# 清理（重要！避免 OOM）
tt -d -i 1000                     # 删除单条
tt --delete-all                   # 清空
```

## 八、OGNL / 字段操作

```bash
# 静态字段
getstatic com.example.Config SECRET
getstatic com.example.Config -x 3
getstatic -c <hash> com.example.Config SECRET

# OGNL 表达式
ognl '@java.lang.System@getProperty("user.dir")'
ognl '@java.lang.System@getProperties()'
ognl '@com.example.X@staticField'
ognl '@com.example.X@staticMethod()'
ognl 'new com.example.X()'

# 多步表达式
ognl '#ctx=@com.example.SpringCtx@get(), #bean=#ctx.getBean("x"), #bean.method()'
ognl '#list={1,2,3}, #list.{? #this > 1}'
ognl '#map=#{ "a":1, "b":2}, #map.a'

# 指定 ClassLoader
ognl -c <hash> '@com.example.X@field'
ognl --classLoaderClass org.springframework.boot.loader.LaunchedURLClassLoader '@com.example.X@field'

# 展开深度
ognl '@com.example.Config@ALL' -x 3
```

## 九、vmtool（高级对象查询）

```bash
# 拿对象实例
vmtool --action getInstances --className com.example.UserService --limit 5 -x 2

# 指定 ClassLoader
vmtool --action getInstances --classLoaderClass org.springframework.boot.loader.LaunchedURLClassLoader --className com.example.X

# 执行表达式
vmtool --action getInstances --className org.springframework.context.ApplicationContext --express 'instances[0].getBeanDefinitionNames()' -x 2

# 过滤
vmtool --action getInstances --className java.lang.Thread --limit -1 --express 'instances.{? #this.daemon == false}.{name}'

# 强制 GC
vmtool --action forceGc

# 堆分析
vmtool --action heapAnalyze --classNum 20 --objectNum 10

# 引用链分析
vmtool --action referenceAnalyze --className com.example.Session --objectNum 5 --backtraceNum 5

# 中断线程
vmtool --action interruptThread -t <thread-id>

# Linux glibc 内存
vmtool --action mallocTrim
vmtool --action mallocStats
```

## 十、热加载（危险）

```bash
# 反编译 + 编辑 + 编译 + 加载
jad --source-only com.example.X > /tmp/X.java
# 编辑 /tmp/X.java
mc /tmp/X.java -d /tmp
mc /tmp/X.java --classLoaderClass <xxx>              # 指定 CL
retransform /tmp/com/example/X.class                 # 推荐
# 或
redefine /tmp/com/example/X.class                    # 旧方法

# base64 方式上传
base64 < Test.class > result.txt                     # 本地编码
# vim result.txt 粘贴到服务器
base64 -d < result.txt > Test.class                  # 服务器解码
```

## 十一、辅助命令

```bash
# 全局选项
options
options strict                    # strict mode
options unsafe true               # 不安全模式（允许修改静态字段）
options job-output-path /tmp/logs # 后台任务输出路径
options save-result true          # 保存结果到文件

# 会话
session
history
pwd
cls
help <command>

# 管道
sc java.lang.String * | grep 'index' | wc

# 重置（移除字节码增强）
reset -E '.*'                     # 全部
reset com.example.MyClass

# 关闭
quit                              # 当前 client
stop                              # 所有 client + server
```
