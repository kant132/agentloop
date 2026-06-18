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
audit:{groupId}:commit:{commitHash}:method:{fqn}#{sigHash}
```

| 占位符 | 来源 |
|--------|------|
| `{groupId}` | `preset.json → groupId` |
| `{commitHash}` | 当前审计 commit |
| `{fqn}` | 方法全限定名（如 `com.example.UserController.search`） |
| `{sigHash}` | 方法签名哈希（区分重载） |

**扩展键**：
- `audit:{groupId}:knowledge:*` — 跨轮次沉淀知识（**永久保留，Phase 0 不清**）
- `audit:{groupId}:commit:{c}:file:{fqn}.{method}#{sig}:status` — 文件状态（pending/analyzing/finished/failed）
- `audit:{groupId}:commit:{c}:round:{N}:status` — 轮次状态
- `audit:{groupId}:commit:{c}:stats:*` — 全局统计

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
| 方法体（method） | **24h** | 同 commit 同方法 24h 内不重复查 codegraph |
| 调用链（chain） | 1h | 调用链中间结果，时效短 |
| Finding draft | **永久**（无 TTL） | 跨轮次复用，需显式清理 |
| 环境（env） | 1h | ssh/http/codegraph 可达性 |
| 知识（knowledge:*） | 永久 | **跨轮次沉淀，Phase 0 不清** |

---

## 六、Phase 0 必做（每次审计启动前）

```powershell
# 1. 清 {groupId}:* 但保留 {groupId}:knowledge:*
& "C:\Program Files\Memurai\memurai-cli.exe" --scan --pattern "{groupId}:*" |
  Where-Object { $_ -notlike "{groupId}:knowledge:*" } |
  ForEach-Object { & "C:\Program Files\Memurai\memurai-cli.exe" DEL $_ }

# 2. 验证 CLI 可达
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

> ⚠️ **不清 knowledge:*** — 这是跨轮次沉淀的项目特有知识（自定义注解、已知消毒器、动态路由模式、历史 finding 模式），每轮自动加载。

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
| 4 | Phase 0 漏清 `groupId:*` | 上一轮缓存污染本轮结果 |
| 5 | Phase 0 误清 `knowledge:*` | 项目知识丢失，下轮从零开始 |
| 6 | 单次调用 > 5min | 超时自动截断，需拆分批次 |
