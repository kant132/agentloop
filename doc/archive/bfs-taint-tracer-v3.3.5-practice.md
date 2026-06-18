# BFS Taint Tracer v3.3.5 实践记录

> **日期**: 2026-06-05  
> **分支**: v3.3.4 → v3.3.5  
> **测试项目**: javatestcase (Spring Boot 3, Java 21, 10 个测试用例)

---

## 1. 问题发现与修复过程

### 1.1 opencode CLI subprocess 调用瓶颈

**现象**: 
- 每次 AI 分析需启动 opencode 进程 → 加载插件 → 执行 → 退出，单次调用 30-90 秒
- AI 返回 markdown 包裹的 JSON（```json ... ```），解析器频繁失败
- 进程树管理复杂（Windows taskkill /T），偶发僵尸进程

**根因**: subprocess 调用 opencode CLI 是"重型"方案——每次创建完整 Node.js 运行时 + 插件加载。对于简单的 LLM 调用，这是过度工程。

**修复**: 替换为 OpenAI SDK 直连百炼 DashScope API

```python
# 从 opencode 配置读取 API key（不硬编码）
def _get_bailian_config() -> tuple[str, str]:
    config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    config = json.load(open(config_path))
    options = config["provider"]["bailian"]["options"]
    return options["apiKey"], options["baseURL"]

# OpenAI SDK 直连
client = OpenAI(api_key=api_key, base_url=base_url)
completion = client.chat.completions.create(
    model="qwen3.7-max",
    messages=[
        {"role": "system", "content": "严格按照要求的 JSON 格式输出，不要添加 markdown 标记"},
        {"role": "user", "content": prompt}
    ],
    temperature=0.1,
)
```

**效果**:
- 速度: 7 batches ~2min vs 10 batches ~4min（**2x 提升**）
- JSON 解析: **100% 成功**（之前每批都有 `JSON parse failed`）
- 代码量: 删除 `_kill_process_tree`、`subprocess`、`shutil` 等 ~100 行

### 1.2 BFS 初始化方法名解析 bug

**现象**: 
```
Sink at PingService.java:13 → method_path = "PingService.exec"  ❌
```
sink 在 `PingService.ping()` 方法内调用 `CmdUtil.exec()`，但 BFS 初始节点的方法名错误地用了 `exec`（被调用方法）而非 `ping`（包含 sink 的方法）。

**根因**: `match.target_method` 是 sink 调用的目标方法名（如 `Runtime.exec`），不是包含 sink 调用的 enclosing method。

**修复**: 新增 `CodegraphClient.find_enclosing_method(file, line)`

```python
def find_enclosing_method(self, file_path: str, line: int) -> Optional[CodegraphNode]:
    """用 sink 的 file+line 找到包含它的方法"""
    class_name = os.path.splitext(os.path.basename(file_path))[0]
    nodes = self.query(class_name)  # 查该类所有方法
    
    for node in nodes:
        if node.kind != "method":
            continue
        if node.start_line <= line <= node.end_line:
            return node  # 找到了
    return None
```

**验证**:
```
PingService.java:13 → PingService.ping (lines 12-14) ✅
CmdUtil.java:23 → CmdUtil.exec (lines 20-39) ✅
Tc08Controller.java:28 → Tc08Controller.admin (lines 22-34) ✅
```

### 1.3 sink 点提示词混淆

**现象**: prompt 中 `sink点：com.audit.testcase.tc08.Tc08Controller.getRuntime` — 把 source 类（Tc08Controller）和 sink 方法（getRuntime）拼在一起，语义错误。

**根因**: `sink_qualified_name = f"{extract_full_class_name(sink_match.file)}.{sink_match.target_method}"` 用了 source 文件的全类名。

**修复**: 只显示方法名 + 实际调用代码

```python
# Before (错误)
sink点：com.audit.testcase.tc08.Tc08Controller.getRuntime

# After (正确)
sink点：exec（Runtime.getRuntime().exec(cmd)）
```

AI 同时看到方法名和实际 sink 调用代码，语义清晰。

### 1.4 JSON 解析失败（markdown 包裹）

**现象**: AI 返回 ````json\n[...]\n````，`json.loads()` 失败，fallback regex 也常失败。

**根因**: opencode CLI 的 agent 模式会添加 markdown 格式化。直连 API 后通过 system prompt 控制输出格式。

**修复**: 
1. system prompt: `"严格按照要求的 JSON 格式输出，不要添加任何 markdown 标记、代码块或额外说明"`
2. 保留 `parse_batch_json_response` 中的 markdown strip 作为防御层

**效果**: 7 个 batch 全部 JSON 解析成功，零失败。

---

## 2. 测试结果 (javatestcase)

### 2.1 验证矩阵

| TC | Sink 类型 | 深度 | 预期 | 实际 | 状态 |
|----|----------|------|------|------|------|
| TC01 | SQLi | 1 | VULNERABLE | SAFE | ❌ AI 误判 |
| TC02 | SQLi | 1 | SAFE | NO MATCH | ❌ sink 未定义 |
| TC03 | CMD | 3 | VULNERABLE | NEEDS_REVIEW x5 | ⚠️ key 不匹配 |
| TC04 | FileOps | 5 | VULNERABLE | NO MATCH | ❌ sink 未定义 |
| TC05 | FileOps | 5 | SAFE | NO MATCH | ❌ sink 未定义 |
| TC06 | SSRF | 11 | VULNERABLE | SAFE x3 | ❌ AI 误判 |
| TC07 | SSTI | 3 | VULNERABLE | SAFE | ❌ AI 误判 |
| TC08 | CMD | 1 | UNREACHABLE | SAFE x3 | ✅ |
| TC09 | FileOps | 1 | FIXED_PARAM | NO MATCH | ❌ sink 未定义 |
| TC10 | XXE | 1 | VULNERABLE | SAFE x6 | ❌ AI 误判 |

**命中率**: 1/10 完全匹配（TC08），4/10 无 sink 匹配

### 2.2 问题分析

**AI 误判为 SAFE 的原因**:
- TC01 (SQLi): `JdbcTemplate.query(sql, ...)` — AI 认为参数经过 PreparedStatement 消毒（实际是字符串拼接）
- TC06 (SSRF): `URL.openConnection()` — AI 认为 URL 来自内部调用（实际来自 @RequestParam）
- TC07 (SSTI): `Jinjava.render(template, ...)` — AI 认为 template 是固定值（实际来自用户输入）
- TC10 (XXE): `DocumentBuilder.parse()` — AI 认为 XML 解析器已配置安全选项（实际使用默认配置）

**根因**: 这些 sink 的 enclosing method 只包含 sink 调用本身，参数来源在 caller 中。BFS 第一层分析时缺少 caller 上下文。

**TC03 key 不匹配**: AI 返回的 key 是 `com.audit.testcase.tc03.PingService.ping`，但 BFS node 的 key 是 `Tc03Controller.GET /tc03/ping`（route 节点），导致匹配失败。

### 2.3 与 v3.3.4 对比

| 指标 | v3.3.4 (opencode CLI) | v3.3.5 (Bailian API) | 变化 |
|------|----------------------|---------------------|------|
| 总路径 | 19 | 19 | 不变 |
| VULNERABLE | 2 | 0 | ↓ |
| SAFE | 11 | 14 | ↑ |
| NEEDS_REVIEW | 6 | 5 | ↓ |
| 批次数 | 10 | 7 | ↓ 30% |
| 耗时 | ~4 min | ~2 min | ↓ 50% |
| JSON 解析失败 | 多次 | 0 | ✅ |

**结论**: 基础设施大幅改善（速度、可靠性），但 AI 判断质量下降。原因可能是：
1. system prompt 过于简洁，AI 缺少安全审计上下文
2. 直连 API 没有 opencode 的 agent 模式（自动读文件、搜索代码）
3. qwen3.7-max 对安全审计场景的理解不如 opencode 的 agent 模式

---

## 3. 架构改进总结

### 3.1 AI 调用架构

```
Before (v3.3.4):
  bfs_taint_tracer.py
    → subprocess.Popen("opencode run")
      → Node.js 运行时启动
        → 插件加载 (oh-my-openagent, browser, etc.)
          → LLM API 调用
            → 返回 markdown 包裹的响应
              → ANSI 过滤 + 元数据剥离
                → parse_ai_response()

After (v3.3.5):
  bfs_taint_tracer.py
    → OpenAI SDK (client.chat.completions.create)
      → DashScope API (qwen3.7-max)
        → 返回纯净 JSON
          → parse_batch_json_response()
```

### 3.2 方法解析优先级

```
BFS 初始化 (Layer 0):
  1. CodegraphClient.find_enclosing_method(file, line)
     → 用 sink 的 file+line 定位 enclosing method
     → 返回 CodegraphNode (name, startLine, endLine)

BFS 后续层 (Layer 1+):
  1. CodegraphClient.query_method(fqn)
     → codegraph query "{fqn}" -k method -j
     → 精确匹配 qualifiedName
  2. CodegraphClient.query(name) fallback
     → 按 class_name 过滤同名方法
```

### 3.3 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `bfs_taint_tracer.py` | 重写 | 直连百炼 API、enclosing method 修复、sink 提示词优化、清理废弃代码 |
| `codegraph.py` | 新增方法 | `find_enclosing_method(file, line)` |

---

## 4. 经验教训

### 4.1 subprocess vs SDK 直连

**教训**: 对于 LLM 调用，subprocess 是过度工程。

| 维度 | subprocess (opencode CLI) | SDK 直连 (OpenAI) |
|------|--------------------------|-------------------|
| 启动开销 | 1-3s (Node.js + 插件) | 0ms |
| 进程管理 | 复杂 (进程树、僵尸进程) | 无 |
| 输出格式 | 不可控 (markdown、ANSI) | 可控 (system prompt) |
| 调试难度 | 高 (多层抽象) | 低 (直接 HTTP) |
| 适用场景 | 需要 agent 能力 (读文件、搜索) | 纯 LLM 推理 |

**决策原则**: 
- 需要 agent 能力（读代码、搜索、多步推理）→ 用 opencode CLI
- 纯 LLM 推理（给定代码片段做判断）→ 用 SDK 直连

### 4.2 API Key 管理

**教训**: 从配置文件读取，不硬编码，不依赖环境变量。

```python
# ✅ 从 opencode 配置读取（已有、集中管理）
config_path = Path.home() / ".config" / "opencode" / "opencode.json"

# ❌ 硬编码
api_key = "sk-xxx"

# ⚠️ 环境变量（需要额外配置步骤）
api_key = os.getenv("DASHSCOPE_API_KEY")
```

### 4.3 method 名解析的语义陷阱

**教训**: `match.target_method` ≠ enclosing method。

```
SinkMatch 字段语义:
  file:          包含 sink 调用的源文件
  line:          sink 调用所在行号
  target_method: sink 调用的目标方法名 (如 Runtime.exec)
                 ↑ 不是包含 sink 的方法名！

正确做法:
  find_enclosing_method(file, line)
  → 查 codegraph 找 startLine <= line <= endLine 的方法
```

### 4.4 AI 判断质量 vs 基础设施质量

**教训**: 基础设施改善不等于结果改善。

v3.3.5 的基础设施（速度、JSON 解析、代码简洁度）全面优于 v3.3.4，但 AI 判断准确率反而下降。原因：
- opencode agent 模式会自动读取相关文件、搜索代码，给 AI 更多上下文
- 直连 API 只传递 prompt 中的代码片段，AI 缺少全局视角

**改进方向**:
1. 在 prompt 中传递更多上下文（caller 方法体、类定义）
2. 对 SAFE 判断增加二次确认（"你确定这个方法没有外部输入吗？"）
3. 考虑混合模式：简单判断用 SDK 直连，复杂分析用 opencode agent

---

## 5. 待改进项

1. **AI 判断准确率**: TC01/TC06/TC07/TC10 被误判为 SAFE，需要在 prompt 中增加安全审计专家角色设定
2. **key 匹配逻辑**: TC03 的 route 节点 key (`Tc03Controller.GET /tc03/ping`) 与 AI 返回的 key (`com.audit.testcase.tc03.PingService.ping`) 不匹配
3. **上下文传递**: 第一层分析时缺少 caller 上下文，导致 AI 无法判断参数来源
4. **sink 规则补全**: TC02/TC04/TC05/TC09 的 sink 类型未覆盖（同 v3.3.4）
5. **二次确认机制**: 对 SAFE 判断增加验证步骤，减少误判
