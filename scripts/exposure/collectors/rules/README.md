# 路由扫描规则

每个框架一个 YAML 文件，由 `route_collector.py` 在启动时加载。

## 规则格式

```yaml
id: find-<framework>-endpoints     # 规则集唯一标识
language: java                       # 目标语言（固定 java）
framework: <framework>              # 框架名（spring/jaxrs/struts/...）
description: "..."                  # 简短描述
rules:                              # 规则列表
  - pattern: "@<Annotation>($$$)"   # ast-grep 匹配模式，$$$ 为通配
    http_method: <GET|POST|PUT|DELETE|PATCH|ANY>
```

字段说明：
- `pattern`：ast-grep 风格的 AST 模式。`$$$` 匹配任意参数列表。
  无参数注解（如 JAX-RS 的 `@GET`）直接写 `pattern: "@GET"`。
- `http_method`：该注解对应的 HTTP 方法。无明确方法的（如 `@RequestMapping`、`@Path`）填 `ANY`。

## 加载机制

`route_collector._load_rules()` 在每次采集时遍历 `rules/*.yaml`，合并所有规则：
- 每条 rule 注入 `_framework` 字段标识来源框架
- `_build_ast_grep_rule_yaml()` 把所有 pattern 合并为 ast-grep 的 `any:` 语法规则文件
- 单次 `ast-grep scan --rule` 调用扫描全部框架，避免 N 次进程开销

## 扩展新框架

1. 复制任意一个 `.yaml` 文件
2. 修改 `framework`、`pattern`、`http_method`
3. 重启采集，无需改 Python 代码

## 当前规则覆盖

| 文件 | 框架 | 注解数 | 状态 |
|------|------|--------|------|
| `spring.yaml` | Spring MVC | 6 | 启用 |
| `jaxrs.yaml` | JAX-RS | 5 | 启用 |
| `struts.yaml` | Struts2 | 0 | 预留 |
