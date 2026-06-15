# XPath 注入

> **类型 ID**: `AP-INJ-XPATH`
> **轨道**: FWD-A
> **业务域**: 9 类必查之一

## 一、定义

用户输入未过滤，被拼接到 XPath 表达式中。攻击者可绕过 XML 认证、读取任意节点。

## 二、典型场景

```java
// ❌ XPath 拼接
String xpath = "//user[name='" + userInput + "']/password";
xpath.evaluate(xpath, document);

// ❌ 带 or 注入
String xpath = "//user[name='" + userInput + "' or '1'='1']";
```

## 三、检测启发式

```yaml
pattern: $XPATH.evaluate($EXPR + $VAR, $DOC)
pattern: $XPATH.compile($EXPR + $VAR)
```

## 四、消毒器

| 消毒器 | 检测特征 |
|--------|---------|
| **XPath 2.0+ 参数化** | 用 `?` 占位 + `setXPathVariableResolver` |
| **白名单** | `Pattern.matches("[a-zA-Z0-9_]+", userInput)` |
| **类型转换** | `Integer.parseInt(userInput)` 后再拼接 |
