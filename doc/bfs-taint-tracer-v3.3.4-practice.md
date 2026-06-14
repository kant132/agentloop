# BFS Taint Tracer v3.3.4 实践记录

> **日期**: 2026-06-04  
> **分支**: v.3.3.3 → v3.3.4  
> **测试项目**: javatestcase (Spring Boot 3, Java 21, 10 个测试用例)

---

## 1. 问题发现与修复过程

### 1.1 进程卡死问题 (subprocess.run + shell=True)

**现象**: 测试进程卡住数小时不退出，CPU 接近 0。

**根因**: Windows 上 `subprocess.run(shell=True)` 创建 `cmd.exe → opencode → [子进程]` 进程树。timeout 触发后只杀 `cmd.exe`，子孙进程仍持有 stdout/stderr pipe handles，`communicate()` 永远等不到 EOF → 死锁。

**修复**: 
- `subprocess.run` → `Popen` + 手动 `communicate(timeout=60)`
- 超时时用 `taskkill /T /F /PID` 杀整个进程树
- 杀完后给 5 秒 grace period 排空 pipe

**关键代码**:
```python
def _kill_process_tree(pid: int) -> None:
    subprocess.run(
        f"taskkill /T /F /PID {pid}",
        shell=True, capture_output=True, timeout=10
    )
```

### 1.2 方法体读取错误 (max_lines=80 hack)

**现象**: AI 分析的方法体不包含函数签名，从 sink 行开始读取，漏掉方法开头的注解和参数定义。

**根因**: `read_method_body(file, start_line, max_lines=80)` 从 sink 所在行开始读 80 行，不是从方法定义开始。

**第一次尝试 (codegraph query)**:
- `codegraph query "ClassName.method"` 返回 `startLine`/`endLine`
- 但 CLI 调用慢 (500-800ms/次)，且 `query "read"` 返回多个同名方法，取第一个导致解析到错误类

**最终方案 (SQLite 直连)**:
- 新建 `CodegraphDB` 类，直连 `.codegraph/codegraph.db`
- `get_method(class_name, method_name)` 通过 `qualified_name LIKE '%::ClassName::methodName'` 精确匹配
- 性能: 0.1-0.3ms vs CLI 500-800ms (快 1000-5000 倍)

**验证**:
```
Tc08Controller.admin → src/.../Tc08Controller.java:22-34 ✅ (之前: 28-0)
SsrfSink.fetch → src/.../SsrfSink.java:22-51 ✅ (之前错误解析到 Tc04Controller)
CmdUtil.exec → src/.../CmdUtil.java:20-39 ✅
```

### 1.3 AI 输出过于冗长

**现象**: AI 返回 markdown 表格、标题、分析段落，动辄 1000+ 字符，解析困难。

**修复**:
- Prompt 输出格式改为严格两行：第 1 行 `y/n/o`，第 2 行说明（≤100 字）
- 只保留 3 个**错误示例**（禁止格式），删除正确示例避免 AI 模仿冗长输出
- `parse_ai_response` 简化：扫描找 `y/n/o` 行，自动过滤 markdown 噪音

### 1.4 硬编码输出路径

**现象**: 测试脚本写死 `D:\code\WebGoat-2025.3\output\...`，无法在其他项目使用。

**修复**: 所有测试脚本改为 `sys.argv[1]` 接受 project_path，默认值保持兼容。输出目录统一为 `{project_path}/output/bfs-taint-analysis/`。

### 1.5 日志文件清理时序

**现象**: `shutil.rmtree` 删除 output 目录时报 `PermissionError: [WinError 32]`。

**根因**: 日志 FileHandler 在 `main()` 之前就打开了日志文件，清理时文件仍被占用。

**修复**: 将 FileHandler 创建移到 `main()` 内部，在 `shutil.rmtree` 之后。

---

## 2. 测试结果 (javatestcase)

### 2.1 验证矩阵

| TC | Sink 类型 | 深度 | 预期 | 实际 | 状态 |
|----|----------|------|------|------|------|
| TC01 | SQLi | 1 | VULNERABLE | VULNERABLE | ✅ |
| TC02 | SQLi | 1 | SAFE | NO MATCH | ❌ sink 未定义 |
| TC03 | CMD | 3 | VULNERABLE | VULNERABLE | ✅ |
| TC04 | FileOps | 5 | VULNERABLE | NO MATCH | ❌ sink 未定义 |
| TC05 | FileOps | 5 | SAFE | NO MATCH | ❌ sink 未定义 |
| TC06 | SSRF | 11 | VULNERABLE | VULNERABLE | ✅ |
| TC07 | SSTI | 3 | VULNERABLE | VULNERABLE | ✅ |
| TC08 | CMD | 1 | UNREACHABLE | SAFE | ✅ |
| TC09 | FileOps | 1 | FIXED_PARAM | NO MATCH | ❌ sink 未定义 |
| TC10 | XXE | 1 | VULNERABLE | VULNERABLE | ✅ |

**命中率**: 6/10 匹配，5/6 判断正确

### 2.2 未匹配原因

NO MATCH 的 4 个 TC 都是 **sink 规则覆盖不足**：
- TC02: `JdbcTemplate.queryForMap()` 不在 sinks.json（只有 `query`）
- TC04/TC05: `Files.readString()` 不在 sinks.json（文件操作 sink 缺失）
- TC09: `Files.readString(FIXED_PATH)` 同上

### 2.3 统计

- 总路径: 19 条
- VULNERABLE: 16 条
- SAFE: 3 条（TC08 的 3 个 sink 匹配）
- 处理节点: 25 个
- 总耗时: ~3 分钟（25 个 opencode 调用）

---

## 3. 架构改进总结

### 3.1 方法体解析优先级

```
Priority 1: SQLite 直连 (CodegraphDB.get_method)
  → 0.1-0.3ms，精确 qualified_name 匹配
  → 返回 startLine + endLine

Priority 2: CLI query 回退 (codegraph_client.query)
  → 500-800ms，按 class_name 过滤同名方法
  → 返回 startLine + endLine

Priority 3: Sink 文件/行号兜底
  → 仅在 layer 0 且 codegraph 无结果时使用
  → 使用 max_lines=80 回退
```

### 3.2 输出格式对比

**Before (v3.3.3)**:
```
## 分析结果
### 完整调用链
| 维度 | 分析 |
|---|---|
| **参数约束** | 无 |
...
y
参数来自...
```
→ 1000+ 字符，解析困难

**After (v3.3.4)**:
```
y
参数来自 @RequestParam 外部输入，全链路无消毒，攻击者可构造任意命令
```
→ 2 行，~100 字符，解析可靠

### 3.3 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `bfs_taint_tracer.py` | 重写 | 核心算法：SQLite 直连、进程树清理、方法体精确读取、两行输出格式 |
| `test_javatestcase.py` | 新增 | javatestcase 测试脚本 |
| `test_2_sinks.py` | 修改 | 输出路径参数化 |
| `test_10_sinks.py` | 修改 | 输出路径参数化 |
| `BFS_TAINT_TRACER_README.md` | 新增 | 算法文档 |

---

## 4. 待改进项

1. **sink 规则补全**: `Files.readString()`、`queryForMap()` 等常见 sink 未覆盖
2. **调用链深度**: TC03 (depth 3) 和 TC06 (depth 11) 的中间层未被 BFS 追踪到（layer 1 就 VULNERABLE 了，因为 sink 的 enclosing method 没有 callers → 直接标记 VULNERABLE）
3. **重复 sink 匹配**: 同一行匹配多个 sink 定义（如 `Runtime.exec` + `ShellSession.exec`），需要去重
4. **Route 节点处理**: codegraph callers 返回 `POST /tc10/xml` route 节点，BFS 将其当作普通方法继续追踪，导致重复分析
