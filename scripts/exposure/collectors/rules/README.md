# 路由扫描规则

每个框架一个 YAML 文件，由 `route_collector.py` 在启动时加载。

## 规则格式

```yaml
id: find-<framework>-endpoints     # 规则集唯一标识
language: java                       # 目标语言（固定 java）
framework: <framework>              # 框架名（spring/jaxrs/struts/...）
description: "..."                  # 简短描述

# 三组规则，加载时分别归类
class_rules:                         # 类级注解（控制器/资源类识别）
  - id: <rule_id>                    # 规则唯一 ID（可选）
    pattern-either:                  # 多变体语法：OR 匹配
      - pattern: "@full.qualified.Name($$$)"
      - pattern: "@ShortName($$$)"
    role: <rest_controller|controller|resource|...>
    http_method: ANY                 # 类级规则无明确方法

method_rules:                        # 方法级路由注解
  - pattern: "@GetMapping($$$)"      # ast-grep 匹配模式，$$$ 为通配
    http_method: <GET|POST|PUT|DELETE|PATCH|ANY>

param_rules:                         # 方法形参注解（请求参数绑定）
  - pattern: "@RequestParam($$$)"
    param_type: <header|query|path|body|cookie|form|bean>
```

## pattern-either 语法

`pattern-either` 让一条规则匹配多种形式（ast-grep 原生的多变体 OR 语法）。常用于：

- **全限定名 vs 短名**：`@org.springframework.web.bind.annotation.RestController` 与 `@RestController`
  都是同一个注解，需要在没有 `import` 信息时也能命中。
- **跨包别名**：JAX-RS 1.x/2.x 的 `@javax.ws.rs.Path` 与短名 `@Path`。

加载器 (`_expand_patterns`) 把 `pattern-either` 展开为 N 个 ast-grep pattern 行，
合并到外层 `any:` 块里一次扫描。`_normalize_pattern(rule)` 返回第一条 pattern，
用于需要单一字符串的场景（如 log）。

## 三组规则说明

| 组 | 用途 | 关键字段 | 示例 |
|----|------|---------|------|
| `class_rules` | 识别控制器/资源类（类级注解） | `role`, `http_method` | `@RestController`, `@Path` |
| `method_rules` | 识别 HTTP 路由端点 | `http_method` | `@GetMapping`, `@GET` |
| `param_rules` | 识别方法形参上的请求绑定 | `param_type` | `@RequestParam`, `@QueryParam` |

`param_type` 取值：`header` / `query` / `path` / `body` / `cookie` / `form` / `bean`。

## 加载机制

`route_collector._load_rules()` 在每次采集时遍历 `rules/*.yaml`，返回分类字典：

```python
{
    "class_rules": [...],   # 每条 rule 注入 _framework 字段
    "method_rules": [...],
    "param_rules": [...],
}
```

- `_build_ast_grep_rule_yaml()` 把所有组的所有 pattern（含 pattern-either 展开）
  合并到 ast-grep 的 `any:` 语法规则文件，单次 `ast-grep scan --rule` 扫描全部框架。
- `_build_http_method_map()` 对每个 pattern 提取 `@AnnotationName → http_method` 映射；
  pattern-either 的所有变体都会注册到映射表。
- `_enrich()` 给每个路由条目加 `params: list[{param_type, name}]` 字段，
  来源是同一文件/方法邻近行的 param_rules 命中。

## 字段说明

- `pattern`：ast-grep 风格的 AST 模式。`$$$` 匹配任意参数列表。
  无参数注解（如 JAX-RS 的 `@GET`）直接写 `pattern: "@GET"`。
- `pattern-either`：多变体 OR 匹配，列表中每项是 `{pattern: ...}`。
- `http_method`：该注解对应的 HTTP 方法。无明确方法的（如 `@RequestMapping`、`@Path`）填 `ANY`。
- `param_type`：仅 param_rules 用，标记请求参数的来源类型。
- `role`：仅 class_rules 用，标记控制器角色（如 `rest_controller` / `controller` / `resource`）。

## 扩展新框架

1. 复制任意一个 `.yaml` 文件
2. 修改 `framework`、`pattern`/`pattern-either`、`http_method`/`param_type`
3. 重启采集，无需改 Python 代码

## 当前规则覆盖

| 文件 | 框架 | class | method | param | 状态 |
|------|------|-------|--------|-------|------|
| `spring.yaml` | Spring MVC | 2 | 6 | 5 | 启用 |
| `jaxrs.yaml` | JAX-RS | 1 | 6 | 8 | 启用 |
| `struts.yaml` | Struts2 | 0 | 0 | 0 | 预留 |
