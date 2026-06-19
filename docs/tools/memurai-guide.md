# Memurai 使用指南

> 本项目的方法体缓存层基于 **Memurai**（Windows 原生 Redis 兼容服务）。
> 全程通过 **subprocess 调用 memurai-cli.exe**，**绝不使用 `pip install redis`**。

---

## 一、为什么用 Memurai 而非 pip redis

| 维度 | pip redis | Memurai CLI（本项目） |
|------|-----------|----------------------|
| 平台 | 跨平台但 Windows 上需 WSL/虚拟机 | **Windows 原生服务**（安装即用） |
| 调用方式 | Python socket 长连接 | `subprocess.run([...])` 子进程 |
| 批量能力 | 需手写 pipeline | `--pipe` 直接吃 RESP 协议流（1 次子进程发 N 条命令） |
| 编码风险 | 无 | 必须指定 `encoding="utf-8", errors="replace"`（避免 GBK 解码崩溃） |

**结论**：Windows 环境下，subprocess + memurai-cli 的 `--pipe` / `--scan` 比 redis-py 单条调用高效 30~100 倍。

---

## 二、CLI 路径

```
C:\Program Files\Memurai\memurai-cli.exe
```

可通过环境变量 `MEMURAI_CLI` 覆盖（便于 CI / 自定义安装路径）。Python 封装见 [`scripts/redis/memurai_client.py`](../../scripts/redis/memurai_client.py)，常量 `CLI_PATH`。

---

## 三、Key Schema

```
{groupId}:method:{fqn}#{startline}
```

| 占位符 | 来源 |
|--------|------|
| `{groupId}` | `preset.json → groupId` |
| `{fqn}` | 方法全限定名（如 `com.example.UserController.search`） |
| `{startline}` | 方法起始行号（1-based，区分重载） |

**扩展键**：
- `{groupId}:method:{fqn}#{startline}:count` — 方法缓存命中计数
- `{groupId}:errors:log` — 犯错记录（JSON array：`{position, reason, count, last_seen}`），session 结束合并到 `{groupId}:knowledge:errors`
- `{groupId}:knowledge:*` — 跨轮次沉淀知识（**永久保留，Phase 0 不清**）
- `{groupId}:file:{fqn}.{method}#{startline}:status` — 文件状态（pending/analyzing/finished/failed）
- `{groupId}:round:{N}:status` — 轮次状态
- `{groupId}:stats:*` — 全局统计

---

## 四、核心 API（`scripts/redis/memurai_client.py`）

| 方法 | 说明 | 底层 |
|------|------|------|
| `set(key, value, ex=...)` | 写单条，可带 TTL | `SET key value EX s` |
| `get(key)` | 读单条 → str/None | `GET key` |
| `mset(kv: dict)` | 批量写（无 TTL） | `MSET k1 v1 k2 v2 ...` |
| `scan(pattern, count)` | 模式扫描所有 key | `--scan --pattern ... --count ...`（**自动迭代**） |
| `pipe_setex_batch(items)` | **批量 SET+EXPIRE**（核心加速点） | `--pipe` 吃 RESP 流，1 次子进程发 N 条 |
| `mget(keys)` | 批量读 | `MGET ... -D '' --raw` |
| `set_json / get_json` | JSON 便捷封装 | 上层基于 `set/get` |

**硬上限**：单次 subprocess 调用 ≤ 5 分钟（`timeout=300`，用户 2026-06-13 规定）。超限自动截断到 300s。

---

## 五、TTL 策略

| 数据类型 | TTL | 说明 |
|---------|-----|------|
| 方法体（method） | **无（session 结束 hook 清理）** | 通过 JAR + 源文件一次性预取，AI 后续只从缓存 GET；不依赖 codegraph 拿方法体 |
| 方法体计数（method:*:count） | 无（跟随主 key） | session 结束 hook 一并清理 |
| 犯错记录（errors:log） | 无（session 结束 hook 清理） | session 结束时合并高频错误到 `{groupId}:knowledge:errors`，跨轮次保留 |
| 调用链（chain） | 1h | 调用链中间结果，时效短 |
| Finding draft | **永久**（无 TTL） | 跨轮次复用，需显式清理 |
| 环境（env） | 1h | ssh/http/codegraph 可达性 |
| 知识（knowledge:*） | 永久 | **跨轮次沉淀，Phase 0 不清** |

> **方法体生命周期（铁律）**：调用链构建后、AI 分析前，由 `tools/javaparser/java-method-call-extractor-1.0.0.jar` + 源文件读取一次性预取到 memurai（见 `design-docs/data-schema.md` `method_cache key` 段「获取方式」）。AI 分析阶段**只能从缓存 GET**，禁止直接读文件或查 codegraph 获取源码。

---

## 六、Session 结束 hook（每次审计关闭时）

```powershell
# 1. 合并高频错误到 knowledge（保留跨轮次犯错经验）
& "C:\Program Files\Memurai\memurai-cli.exe" GET "{groupId}:errors:log"  # 由 hook 解析 + 合并到 {groupId}:knowledge:errors

# 2. 清 {groupId}:* 但保留 {groupId}:knowledge:*（含合并后的 errors）
& "C:\Program Files\Memurai\memurai-cli.exe" --scan --pattern "{groupId}:*" |
  Where-Object { $_ -notlike "{groupId}:knowledge:*" } |
  ForEach-Object { & "C:\Program Files\Memurai\memurai-cli.exe" DEL $_ }

# 3. 验证 CLI 可达（下次启动 sanity check）
& "C:\Program Files\Memurai\memurai-cli.exe" PING   # 必须返回 PONG
```

**Python 版**（推荐用封装）：

```python
from scripts.redis.memurai_client import Memurai
cli = Memurai()
for k in cli.scan(pattern=f"{group_id}:*"):
    if not k.startswith(f"{group_id}:knowledge:"):
        cli.delete(k)
```

> ⚠️ **不清 knowledge:*** — 这是跨轮次沉淀的项目特有知识（自定义注解、已知消毒器、动态路由模式、历史 finding 模式、合并后的高频错误），下轮自动加载。
> ⚠️ **不再是每轮清，而是 session 结束清** — 缓存不设 TTL，只在活跃审计期间有效；任务结束或 session 关闭时由 hook 触发清理。

---

## 七、相关文档

- [`conduct/必读/01-避免重复劳动.md`](../../conduct/必读/01-避免重复劳动.md) — 缓存命中是避免重复劳动的硬约束
- [`conduct/优化路径/03-缓存命中率提升.md`](../../conduct/优化路径/03-缓存命中率提升.md) — 命中率优化路径（>30% 重复 = 需优化）
- [`scripts/AGENTS.md`](../../scripts/AGENTS.md) — memurai_client.py 是缓存单点，修改需回归测试

---

## 八、常见坑

| # | 坑 | 修正 |
|---|----|------|
| 1 | 用 `pip install redis` | **禁止** — 用 `memurai-cli.exe` subprocess |
| 2 | subprocess 默认 GBK 编码崩溃 | 必须 `encoding="utf-8", errors="replace"` |
| 3 | 手写 SCAN 循环 + RESP 解析 | 用 `--scan`（内部自动迭代）或 `--pipe`（一次发 N 条） |
| 4 | Session 结束 hook 漏清 `groupId:*` | 下一轮缓存残留污染本轮结果 |
| 5 | Session 结束 hook 误清 `knowledge:*` | 项目知识丢失，下轮从零开始 |
| 6 | 单次调用 > 5min | 超时自动截断，需拆分批次 |
| 7 | AI 分析时直接读文件或查 codegraph 拿方法体 | **禁止** — 方法体已在调用链构建时一次性预取，AI 只能从缓存 GET |

---

## 九、缓存铁律

- 所有源码相关信息**只能从缓存 GET**
- codegraph 只用于构建调用拓扑（node_id、edges、fqn、start_line、end_line、file_path），**不用于获取方法体**
- 方法体预取通过 `tools/javaparser/java-method-call-extractor-1.0.0.jar` + 源文件读取（调用链构建后、AI 分析前一次性完成）
- AI 分析时**禁止直接读文件或查 codegraph**获取源码
- 缓存不设 TTL，只在活跃审计期间有效；session 结束 hook 自动清理（保留 `knowledge:*`，合并 `errors:log` 高频项到 `knowledge:errors`）
