# Memurai 缓存策略

> 详细 key 设计、TTL、批预取、一致性自检
>
> **运行时通过 `C:\Program Files\Memurai\memurai-cli.exe` 访问**（不再依赖 pip install redis）。
> 封装脚本：`脚本/redis/memurai_client.py`（提供 set/get/mset/setex/expire/delete/scan_iter/ping/info）

## 一、Key Schema（前缀：项目隔离）

```
audit:{groupId}:commit:{commitHash}
  ├─ :method:{fqn}#{sigHash}        → method body + meta
  ├─ :chain:{chainId}              → forward chain summary
  ├─ :prefetch:{chainId}           → 预取 chain summary
  ├─ :judgment:{fqn}#{D-id}        → D1-D5 判定结果
  ├─ :astmatch:{fqn}#{patternId}   → AST 模式匹配结果
  ├─ :endpoint:{fqn}#meta          → 端点元信息
  ├─ :finding:{chainId}:draft      → finding 草稿
  ├─ :finding:{chainId}:final      → finding 终稿（3x85 后）
  ├─ :score:{chainId}:r{roundN}    → 评分历史
  ├─ :env:reachability             → 环境可达性
  ├─ :force-rescan:{chainId}       → 人工强制重扫请求
  └─ :subagent-killed:{id}         → kill 时的进度 dump

audit:global
  ├─ :sink-patterns                → 全局 AST 危险模式
  ├─ :auth-check-templates         → 全局鉴权检查模板
  ├─ :business-rule-templates      → 全局业务规则模板
  └─ :prompt-versions              → prompt 版本 + sha256
```

## 二、TTL

| 数据 | TTL | 理由 |
|------|-----|------|
| method body | 24h | 稳定 |
| chain / prefetch | 1h | 迭代期短 |
| judgment | 1h | 跟着 chain |
| finding draft | 30 轮 | 防永久滞留 |
| finding final | 永久 | 落盘后只读 |
| env reachability | 1h | 环境会变 |
| score | 永久 | 累计用 |
| astmatch | 24h | 跟着 commit |
| force-rescan | 7d | 复审周期 |

## 三、批预取模式

**subagent 启动前**：
```bash
python 脚本/redis/redis-batch-prefetch.py \
  --chain chain.json \
  --group-id com.example.x \
  --commit HEAD
```

底层走 `memurai-cli MSET k1 v1 k2 v2 ...` + 逐条 `EXPIRE`。

**subagent 启动时**：
- 读 `audit:{gid}:commit:{ch}:prefetch:{chainId}` → 整链 summary
- 后续读 `:method:{fqn}#{sigHash}` → 单方法（**必命中**）

**subagent 运行时**：
- **不**直接调 codegraph
- 所有方法体从 Memurai 取

## 四、一致性自检（启动时）

```bash
python 脚本/redis/redis-self-check.py \
  --group-id com.example.x \
  --commit HEAD \
  --codegraph-db codegraph.db
```

底层走 `memurai-cli SCAN 0 MATCH pat COUNT 1000` 反复迭代。

- 随机抽 50 个 method key
- 与 codegraph 真实值比对
- 不匹配 → 自动失效（`memurai-cli DEL`） + 报告
- 不匹配 > 0 → 启动失败警告

## 五、跨轮次复用

- finding final 永久保留
- 启动新轮前先查 `audit:{gid}:commit:{ch}:finding:{chainId}:final` → 跳过
- 第二轮 50%+ 链无需重跑

## 六、跨项目复用

- 全局类型库（不带 groupId）→ 永久共享
- 项目特有（带 groupId）→ 按 groupId 加载

## 七、性能与限制

封装层（`脚本/redis/memurai_client.py`）充分利用 memurai-cli 的高阶特性：

| 场景 | 实现 | 子进程次数 |
|------|------|-----------|
| N 条 `SET k v EX ttl` | `--pipe` 走 RESP 流 | **1** |
| 1 条 `MSET k1 v1 k2 v2 ...` | `memurai-cli MSET ...` | 1 |
| 模式匹配所有 key | `memurai-cli --scan --pattern pat --count 1000` | 1（自动 SCAN 迭代）|
| 单条 `GET / SET / DEL` | `memurai-cli GET k` | 1 |
| MGET k1 k2 ... | `memurai-cli MGET k1 k2 -D '' --raw` | 1 |

**关键**：
- `pipe_setex_batch(items)`：链方法预取 30 个方法 = 30 次子进程 → **1 次**
- `scan(pattern)`：替代手写 SCAN 循环 + RESP 文本解析
- `-e` 标志：所有调用默认带 -e，错误以退出码 1 呈现
- Windows 上中文/换行 key/value 经 RESP bulk string 二进制安全传输（不再受命令行长度限制）
