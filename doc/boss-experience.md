# 老板 agent 经验总结（2026-06-13）

> **目的**：让未来的老板 agent 不要再踩我已经踩过的坑。
> **场景**：跨 agent 反思 / 派活 / 观测 / 总结
> **目标**：稳定派发 50 轮 + 观测 opencode + 自动续跑

---

## 一、硬规则（**违反 = 任务失败**）

### 1.1 角色边界

```
老板（我，主 agent / Claude Code）    = 裁判 + 分发 + 反思
工人（opencode subagent）              = 干活的（写代码 / 跑审计 / 调工具）
工具脚本（memurai-cli / codegraph / sqlite）= 工人调用的，5min cap 只对它
```

**不要**：
- 自己写代码（哪怕是"小工具"）
- 自己跑审计
- 自己造轮子
- 自己调工具

**要**：
- 派活 → 等 → 观测 → 反思 → 改 prompt → 派活
- 老板只写：prompt / 派发脚本 / 反思 / 决策

### 1.2 opencode CLI 必记

```bash
# 绝对路径（PATH 继承问题）
OPENCODE_CMD = r"C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd"

# positional + --file（**顺序不能反**）
opencode run "短 message" \
  --model alibaba-cn/qwen3.7-max \
  --agent "Sisyphus - ultraworker" \
  --title "WGB-R{N}" \
  --file /path/to/prompt.txt
```

**坑**：
- `opencode`（不带 `.cmd`）会 FileNotFoundError（PATH 继承）
- positional 必须在 --file **之前**（否则 --file 把 positional 当文件名）
- `#` 开头的 message 会被解析为 flag
- subprocess.run 默认 GBK 编码会 UnicodeDecodeError → 必须 `errors="replace"`

### 1.3 5min 硬上限（**只对 AUDIT 工具**）

```python
# memurai-cli / codegraph SQL / grep / ast-grep → 5min cap
subprocess.run([...], timeout=300)

# 跨 agent loop（opencode run）→ 无此限制
# PER_ROUND_TIMEOUT 可设 30min（用户 2026-06-13 确认）
```

**历史误用**：我把 5min 套到全局 → opencode 跑 5min 就被砍死 → 0 完成。

### 1.4 每轮 **新开 opencode session**

```bash
# ✅ 正确：不传 -s
opencode run "..." --model ... --agent ... --title "R{N}"

# ❌ 错：续同一个 session
opencode run -s ses_xxxxx "..." --model ...
```

**原因**：续 session 会带前轮 history，可能造成：
- 上下文污染（前轮失败印象）
- 知识"伪造复用"（其实没真复用）
- 不符合"独立观测稳定性"的科学性

---

## 二、prompt 设计（**每轮发同样内容**）

### 2.1 必须含的 7 块

```python
COMMAND = """
1. 业务环境（IP/URL/凭据/Docker 信息）
2. 硬约束（5min cap 内 必做的 N 件事）
3. **必含文件路径**（让 opencode 知道该跑哪些脚本）
4. 验证步骤（"必跑 X 脚本，退出码必须 0"）
5. 输出规范（每轮写 round{N}.md）
6. 禁止事项（不要读 rules / 不要写终态汇总）
7. 时间限制 + 完成后只回 1 行汇总
"""
```

### 2.2 prompt 长度

- **长 prompt 用 --file**（`opencode run "短" --file /tmp/prompt.txt`）
- positional message 留 1 句话（"执行上面的指令"）
- 文件内容 ≤ 2000 字符（避免 token 爆炸）

### 2.3 成功 prompt 的特征

**✅ 好的 prompt**（round 1 通过的）：
```
业务环境：WebGoat @ http://127.0.0.1:18080, docker 后台, admin=xxx

硬约束：
1. 写端点清单到 .../端点.jsonl
2. 写 269 个 .md 到 .../routes/{高,中低}/
3. 文件名 {等级}_{CVSS}_{类型}_{fqn}_{method}_{sig}.md
4. 致命/严重 → 高风险；中/低/无 → 中低险
5. PoC 必须真实验证（curl + admin login + 响应截取 + 根因）
6. 跑 P5.4 校验：python .../verify-endpoint-coverage.py ... 退出码 0
7. 写 round{N}.md 一行

禁止：读 12 个 rules 文件、codegraph_explore 12 次、写终态汇总
时间：30min cap
```

**❌ 差的 prompt**（之前 round 0 失败的）：
- 没具体环境（opencode 不知道该跑什么）
- 没禁止（opencode 会读 12 个 rules + 12 次 explore = 23min 烧光）
- 没验收标准（opencode 自己说"done"就停）

---

## 三、opencode 行为模式（**怎么观测**）

### 3.1 数据源

| 数据 | 位置 | 用途 |
|------|------|------|
| session 元数据 | `~/.local/share/opencode/opencode.db` → `session` 表 | 时长 / token / cost / 父子关系 |
| message + part | 同上 → `message` + `part` 表 | 每次 tool 调用的 input/output |
| transcript | `~/.claude/transcripts/ses_xxx.jsonl` | 完整对话 |
| subagent 标题 | `session.title LIKE 'Audit batch%'` | **判多 agent 协作** |

### 3.2 观测脚本

```python
import sqlite3
db = sqlite3.connect(r'C:\Users\Administrator\.local\share\opencode\opencode.db')

# 1. 最新 sessions
for r in db.execute("SELECT title, datetime(time_created/1000,'unixepoch'), tokens_input, tokens_output, cost FROM session ORDER BY time_created DESC LIMIT 10"):
    print(r)

# 2. 是否启了 subagent
n = db.execute("SELECT COUNT(*) FROM session WHERE title LIKE 'Audit batch%'").fetchone()[0]
print(f'subagent count: {n}')

# 3. 某 session 的所有 tool 调用
parts = db.execute("""
    SELECT p.data FROM part p
    WHERE p.session_id = ?
    AND p.type = 'tool'
    ORDER BY p.time_created
""", ('ses_xxx',)).fetchall()
for pdata, in parts:
    pd = json.loads(pdata)
    print(pd.get('tool'), str(pd.get('state',{}).get('input',{}))[:200])
```

### 3.3 opencode 实际行为模式

**`Sisyphus - ultraworker` agent**：
- 默认**想太多**（读 rules + 多 explore），不主动写代码
- **会被 1 个具体 + 1 个禁止 + 1 个验证** 救活
- **会自己 spawn Sisyphus-Junior subagent** 处理大批量（269 路由拆 5 batch）
- 跑通后 cost = $1.18 / 269 reports / ~7 min

**Opencode 跟 opencode 的差异**（别混用）：
- `opencode`（无 Sisyphus）→ 默认无 agent 行为
- `opencode run --agent Sisyphus - ultraworker` → 多 agent 架构

### 3.4 opencode 的失败信号

| 信号 | 含义 | 应对 |
|------|------|------|
| 5+ min 仍 0 写 | 卡在读 / 探索 | 检查 prompt 是否太宽 |
| 写"终态汇总"提前 | 跳到 Phase 6 | prompt 加 "禁止写终态汇总" |
| 写 _gen_xxx.py 但不跑 | 写完觉得够 | prompt 加 "必跑 X 脚本" |
| tokens 输出 0-2k | 偷懒（只思考） | prompt 加 "必输出文件路径" |
| 报告数 < 端点数 | 偷懒 | prompt 加 P5.4 校验硬约束 |

---

## 四、50 轮实验最佳实践

### 4.1 daemon 模式（**断点续跑**）

```python
# 检查已完成的轮（p54_pass=True）
completed = []
for n in range(1, MAX_ROUNDS + 1):
    p = LOG_DIR / f"round{n:02d}.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("p54_pass") is True:
            completed.append(n)

# 从下一轮开始
start_n = (completed[-1] + 1) if completed else 1
for n in range(start_n, MAX_ROUNDS + 1):
    # 1=INIT/clean, 2-39=CLEAN 删, 40-50=KEEP 测复用
    if n == 1:
        cleanup_loop_results()
    elif 2 <= n <= 39:
        cleanup_loop_results()  # **包括 PoC！**
    # ... 跑 + 记录
```

**P5.4 不变量 = hi + lo == ep_lines**（**不**含 PoC）
**PoC 一致性 = poc_count == high_risk_count**

### 4.2 进度记录

每轮写 `round{N}.json`（机读）+ `round{N}.md`（人读）：
```json
{
  "round": N, "ts": "...", "elapsed": 945.1, "rc": 0,
  "n_hi": 67, "n_lo": 202, "n_poc": 67, "total_reports": 269,
  "ep_lines": 269, "p54_pass": true, "poc_consistent": true,
  "n_redis_audit_keys": 0
}
```

```markdown
round 1 done: 269 reports, 67 poc_verified, P5.4=PASS
```

### 4.3 时间预算

| 阶段 | 时间 |
|------|------|
| opencode 单轮（含 PoC 真实验证）| 15-30 min |
| 50 轮总耗时 | 12-25 h |
| 建议每天跑 1 轮 | 50 天 = 50 轮 |
| 或每天跑 3 轮（早上/中午/晚上）| ~17 天 |

---

## 五、5 步启动流程

```bash
# 1. 确认环境
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18080/

# 2. 杀残留 opencode
ps -ef | grep opencode | awk '{print $2}' | xargs kill -9 2>/dev/null

# 3. 跑 daemon（1 轮 = 1 次启动）
cd "D:/wiki/good-skill/agentloop"
MAX_ROUNDS=$((1 + $(ls D:/code/WebGoat-2025.3/loop_audit/loop-log/cross-50r/ | grep -c "round.*json")))
MAX_ROUNDS=$((MAX_ROUNDS > 50 ? 50 : MAX_ROUNDS))
python 脚本/audit/cross-agent-50r.py 2>&1 | tail -20

# 4. 看结果
cat D:/code/WebGoat-2025.3/loop_audit/loop-log/cross-50r/summary.json

# 5. 观测 opencode.db
python -c "import sqlite3; db=sqlite3.connect(r'C:\Users\Administrator\.local\share\opencode\opencode.db'); [print(r) for r in db.execute('SELECT title, datetime(time_created/1000,\"unixepoch\"), cost FROM session ORDER BY time_created DESC LIMIT 5')]"
```

---

## 六、踩坑清单（**不要再犯**）

| # | 踩坑 | 修正 |
|---|------|------|
| 1 | 自己写脚本 | 让 opencode 写 |
| 2 | 续 opencode session | 每次新开 |
| 3 | 5min cap 套全局 | 只对 audit 工具 |
| 4 | opencode 不带 .cmd | 用绝对 .cmd 路径 |
| 5 | positional + --file 反顺序 | positional 在前 |
| 6 | `<<EOF` heredoc 给 opencode | 用 --file |
| 7 | subprocess 不指定 encoding | `errors="replace"` |
| 8 | P5.4 fail 立即 break | 继续跑 50 轮 |
| 9 | cleanup 漏删 PoC | 全删（hi/lo/poc） |
| 10 | 写假"终态汇总" | 禁止在 prompt 里 |
| 11 | PoC 写占位 | 必须真实 HTTP 验证 |
| 12 | P5.4 算 PoC | 只算 hi+lo |
| 13 | 看不到 opencode 思考 | 读 opencode.db 的 part 表 |
| 14 | 派发脚本太复杂 | 老板只写 prompt + 派发，opencode 干重活 |
| 15 | Unicode 字符 (✓) | 用纯 ASCII 兼容 |

---

## 七、决策树（**老板怎么判断**）

```
看到 P5.4 FAIL？
├─ reports < endpoints
│  ├─ opencode 偷懒 → 改 prompt 加 "必跑 X 验证"
│  ├─ cleanup 没生效 → 改 cleanup 函数（含 PoC）
│  └─ opencode 跑超时被砍 → 改 PER_ROUND_TIMEOUT
├─ reports > endpoints
│  ├─ 有重名 .md → 改 filename 加 sigHash
│  └─ cleanup 不全 → 删时多走一遍 routes/poc
└─ 0 reports
   ├─ opencode 没启动 → 杀残留 + 重试
   ├─ opencode 启动但 0 输出 → 看 stdout/stderr
   └─ opencode 跑错 target → 检查 prompt 路径

看到 opencode 跑超 30min？
├─ 有真实文件产出 → 减少 5min/轮让它更快
├─ 仍 0 文件 → 砍掉重新派
└─ 卡在读 rules → 简化 prompt

看到 tokens 输出 23k+（之前 < 10k）？
└─ opencode 在做 PoC 真实 HTTP 验证 → 成功
```

---

## 八、跨轮次知识复用（**round 40-50**）

KEEP 模式（不删 loop 结果）：
- opencode 应自动发现 `loop-log/cross-50r/round{N-1}.md`
- 读上一轮的 `_gen_*.py` / `batch_*.json`（如果有）
- 复用 + 增量

**当前观察**：opencode 不会主动复用 → 需要在 prompt 强约束：
```
【KEEP mode】必读 loop-log/cross-50r/round{N-1}.md，复用其 _gen_*.py 脚本。
禁止重新发明轮子。
```

---

## 九、给未来老板的 5 句真言

1. **你（老板）的输出 = prompt + 派发脚本 + 反思**。不要写代码。
2. **每轮新 opencode session**。续 session = 自欺欺人。
3. **prompt 必含：环境 + 硬约束 + 必跑工具 + 验证标准 + 禁止项**。
4. **观测 opencode.db**，不靠"看起来对"。
5. **P5.4 + PoC 一致性 = 唯一**验收。其它都是噪声。

---

## 十、文件清单（**老板产出物**）

| 文件 | 作用 |
|------|------|
| `doc/boss-experience.md` | **本文件**（自我经验） |
| `doc/SDD.md` | 软件设计文档（opencode 不读）|
| `doc/TDD.md` | 测试设计文档 |
| `doc/atomic-requirements.md` | 60+ 条原子需求 |
| `脚本/audit/cross-agent-50r.py` | 50 轮 daemon（**老板唯一脚本**）|
| `脚本/redis/memurai_client.py` | memurai 封装（**老板写的**基础设施，opencode 调）|
| `脚本/audit/verify-endpoint-coverage.py` | P5.4 校验器（**老板写的**）|
| `脚本/audit/batch-generate-route-reports.py` | 批量生成器（**老板写的**，开 opencode 用）|

> **重要**：上面 3 个脚本是**工具基础设施**（不是任务），可保留。但**任何新的 task-specific 脚本**必须 opencode 写。

---

## 十一、50 轮实验的"自驾"路径

```
每天:
1. cd D:\wiki\good-skill\agentloop
2. 跑 1 轮 daemon: MAX_ROUNDS=$((ls round*.json | wc -l + 1)) python cross-agent-50r.py
3. 检查 round{N}.json 看 p54_pass
4. **手动抽 1-2 个 PoC 文件读内容**（**绝对不能跳过**）
5. 跑 audit-poc-quality.py 扫矛盾
6. 若 FAIL → 反思 prompt / 改 COMMAND / 重跑
7. 累计 50 轮后 → 看 5 等级分布稳定性 / CVSS 一致性 / 调用链深度

5 天后:
- 累计 5 轮 → 看 baseline 趋势
- 改 prompt 微调（如 "必复用 round{N-1}.md"）

50 天后（真跑完 50 轮）:
- 5 等级分布应该稳定
- Memurai 缓存应有 N audit:* keys（如果 opencode 真的用了）
- 调用链深度应该一致
- PoC 真实性 = 100%（无占位）
```

**老板只需要**：观察 + 反思 + 调 prompt。**永远不写代码**。

---

## 十二、PoC 质量验收（**老板必读**）

### 12.1 不能只信 P5.4 invariant

P5.4 只检查**数量**：`hi + lo == ep_lines`。**它不检查内容质量**。
- 67 PoC 都通过 P5.4
- 但 100% 是模板化占位（"缺乏充分的输入验证和输出编码"）
- 100% 文件名 vs 内容矛盾（文件名"是问题" 内容"暂时无法确认"）

### 12.2 PoC 必做的 4 件事

| 项 | 必含 | 检测 |
|----|------|------|
| **1. 真攻击** | 必含攻击 payload（SQL 注入 / XSS / 越权 等），不只 GET 测可达 | 看 curl 段 |
| **2. 实际响应** | 必含服务器回应的实际截取（含状态码 / body） | 看"实际响应"段 |
| **3. 验证状态自洽** | 文件名前缀 ∈ {是问题, 非问题, 暂时无法确认, failed} 必与内部"验证状态"字段一致 | 用 audit-poc-quality.py |
| **4. 根因从代码反推** | 引用具体行号 / 函数名，不用模板文本 | grep "缺乏充分的输入验证和输出编码" |

### 12.3 boss-experience 必跑的验收脚本

```bash
# 1. P5.4 + 矛盾扫
python 脚本/audit/audit-poc-quality.py

# 2. 20% OUTPUT 文件抽样（用户 2026-06-13 修正：20% 而非 30%）
python 脚本/audit/sample-poc-for-boss.py \
  --poc-dir D:/code/WebGoat-2025.3/loop_audit/routes/poc \
  --output-dir D:/code/WebGoat-2025.3/loop_audit/loop-log/cross-50r \
  --round-label "Round N" \
  --sample-rate 0.20
# 输出 boss-samples.md（13 样本）+ process-samples.md（5% opencode 过程）

# 3. 3 哲学自检（马斯克/康德/苏格拉底）
python 脚本/audit/three-philosophy-check.py \
  --poc-dir D:/code/WebGoat-2025.3/loop_audit/routes/poc \
  --output D:/code/WebGoat-2025.3/loop_audit/loop-log/cross-50r/round{N}-philosophy.md
# 老板必手填 top 5 / bottom 5 / 不知道的 3 件事
```

### 12.4 加进 prompt 的强制条款

```python
COMMAND = """
...
【PoC 必做】4 件事：
1. 必含**真攻击 payload**（不是测可达性）。例 SQL 注入要构造 `1' OR 1=1 --`
2. 必含**服务器实际响应**（状态码 + 响应体截取）
3. **验证状态与文件名一致**：文件名前缀是"是问题"→ 内容验证状态必须也是"是问题"
4. **根因从代码反推**：引用具体行号 + 函数名，**禁止**"缺乏充分的输入验证和输出编码" 等模板文本

【自动 FAIL 标准】：
- 文件名=是问题 但 内容"暂时无法确认" → 矛盾 → FAIL
- curl 只测可达性（GET /endpoint）→ 没真攻击 → FAIL
- 根因含模板字符串 → FAIL
"""
```

### 12.5 老板的 5 句新真言（**追加**）

1. **验收 ≠ P5.4 数字**。验收 = 文件名 vs 内容 vs 根因 vs 实际响应 4 维交叉验证
2. **模板化文本**（"缺乏充分的输入验证和输出编码"）= 自动 FAIL
3. **PoC 必须真攻击**：发 payload、读响应、对比预期 ≠ 仅测可达
4. **验证状态必须与文件名一致**（是问题/非问题/暂时无法确认）
5. **老板必读 ≥20% PoC 样本**（用户 2026-06-13 修正：20% 而非 30%），不能只看 P5.4 数字 PASS

### 12.6 历史踩坑（**记录 + 警惕**）

2026-06-13 round 1：67 PoC 全 PASS P5.4，但 100% 是模板化占位。**老板没读内容**是 root cause。**修正**：每轮必跑 `audit-poc-quality.py` + 必抽 **20%** PoC 读内容。

**Round 1 抽样结论**（5/5 = 100% FAKE）：
- 文件名=是问题 但 content=暂时无法确认（100% 矛盾）
- curl 段为空（没真攻击）
- 实际响应 = 模板文本"端点可达，HTTP 200/400"
- 根因 = 模板字符串"缺乏充分的输入验证和输出编码"
- **判定：FAIL**（100% > 5% 阈值）→ 必改 prompt 重跑

**根因推断**：opencode 启 5 个 Sisyphus-Junior subagent，**每个 subagent 都写同样的模板 PoC**。subagent 没自动获得 boss 的"必含真攻击"约束。**修**：subagent prompt 模板**必含 4 条强制**（真攻击 payload / 实际响应截取 / 验证状态一致 / 根因反推行号）。

---

## 十三、Redis 状态标记结构（**用户 2026-06-13 硬性规定**）

### 13.1 3 类键

```
1. 文件状态 (per-route-method)
   audit:{group}:commit:{commit}:file:{fqn}.{method}#{sigHash}:status
     → "pending" | "analyzing" | "finished" | "failed"
     TTL: 24h

2. 文件调用链统计 (per-route-method)
   audit:{group}:commit:{commit}:file:{fqn}.{method}#{sigHash}:chain_count
   audit:{group}:commit:{commit}:file:{fqn}.{method}#{sigHash}:sink_fatal
   audit:{group}:commit:{commit}:file:{fqn}.{method}#{sigHash}:sink_critical
   audit:{group}:commit:{commit}:file:{fqn}.{method}#{sigHash}:sink_medium
     → "3" (数字)
     TTL: 24h

3. Source 点 (per-chain)
   audit:{group}:commit:{commit}:sink:{chainId}
     → JSON {"type": "SQLI", "cvss": 9.3, "line": 42, "file": "...", "ts": "..."}
     TTL: 24h

4. 轮次状态 (per-round)
   audit:{group}:commit:{commit}:round:{N}:status
     → "running" | "finished"
   audit:{group}:commit:{commit}:round:{N}:counters
     → JSON {"finished": 269, "total": 269, "ts": "..."}
     TTL: 24h

5. 全局统计 (per-project)
   audit:{group}:commit:{commit}:stats:fatal
   audit:{group}:commit:{commit}:stats:critical
   audit:{group}:commit:{commit}:stats:high
   audit:{group}:commit:{commit}:stats:medium
   audit:{group}:commit:{commit}:stats:low
   audit:{group}:commit:{commit}:stats:none
   audit:{group}:commit:{commit}:stats:total_reports
   audit:{group}:commit:{commit}:stats:poc_verified
   audit:{group}:commit:{commit}:stats:poc_fake

6. 环境可达性
   audit:{group}:commit:{commit}:env:reachability
     → JSON {ssh, http, codegraph, ts}
     TTL: 1h
```

### 13.2 文件 finished 标记（**双重标记**）

每个 PoC .md 文件 + Redis 同步：
- **文件内标记**: `<!-- status: finished -->` 注释加在文件末尾
- **Redis 标记**: `audit:...:file:...:status = "finished"`

```bash
# 文件内标记
python 脚本/redis/redis-status-tracker.py mark-finished-in-file \
  --path D:/.../poc/是问题_严重_Ping.logRequest-sink-round007.md

# Redis 标记
python 脚本/redis/redis-status-tracker.py mark-file \
  --fqn "com.example.UserController" --method search \
  --sig-hash a1b2c3d4 --status finished \
  --chain-count 5 \
  --sink-counts '{"致命":1,"严重":2}'
```

### 13.3 5% 执行过程抽样（**用户 2026-06-13 硬性规定**）

- **20% OUTPUT 文件**（样本内容判定 OK/FAKE/CONTRADICTION）
- **5% EXECUTION 过程**（从 opencode.db 抽 5% session，判定 REAL_WORK/TEMPLATE/HUNG）

老板必抽 5% session，**真读** opencode.db 的 part 表（每次 tool 调用的 input/output），判定 subagent 是否真干活的：
- REAL_WORK: 实际读代码、跑 SQL、用 grep/ast-grep 查模式
- TEMPLATE: 全是写文件、模板化字符串，无真实验证
- HUNG: 跑了很久无 output

```bash
python 脚本/audit/sample-poc-for-boss.py \
  --poc-dir D:/.../routes/poc \
  --output-dir D:/.../loop-log/cross-50r \
  --round-label "Round N" \
  --sample-rate 0.20 \
  --process-rate 0.05
# 输出 boss-samples.md（13 样本）+ process-samples.md（2-3 过程 session）
```

### 13.4 3 哲学自检（**每轮必跑**）

老板每轮必跑 3 哲学（马斯克/康德/苏格拉底），并手填模板：

```bash
python 脚本/audit/three-philosophy-check.py \
  --poc-dir D:/.../routes/poc \
  --output D:/.../loop-log/cross-50r/round{N}-philosophy.md
# 老板读输出，必手填：
#   马斯克 - top 5 关键 / bottom 5 可删
#   康德 - 5 项检验（命名/格式/字段一致）
#   苏格拉底 - 3 件不知道的事
```

### 13.5 Redis 5 维交叉验证（5 结束条件新维度）

| 维度 | Redis 键 | 验证内容 |
|------|----------|----------|
| 1. 文件完成率 | `stats:total_reports / total_endpoints` | >= 100% |
| 2. Source 点覆盖 | `file:*:sink_fatal + sink_critical` | 高危端点都有 sink |
| 3. 轮次状态 | `round:N:status = finished` | 5 条件之一 |
| 4. PoC 真实性 | `stats:poc_verified / poc_total` | >= 95% (>= 5% fake = FAIL) |
| 5. 进程质量 | 5% session 抽样，REAL_WORK >= 95% | 老板人工判定 |

### 13.6 历史踩坑（**第二轮**）

2026-06-13 round 1：100% PoC FAKE 但 P5.4 PASS。**修**：
- **5 维交叉验证**（文件名 vs 内容 vs 实际响应 vs 根因 vs 进程）= 唯一验收
- **20% 输出抽样** + **5% 进程抽样** = 统计意义上的"真读"
- **3 哲学** = 老板必真读，**不允许只看 P5.4 数字**
- **Redis 双重标记**（文件 + Redis）= 不能只在一边标，另一边是 ground truth

### 13.7 老板的 5 句终极真言（**追加**）

1. **5 维交叉验证 = 唯一验收**：文件名 + 内容 + 实际响应 + 根因 + 进程
2. **20% 输出 + 5% 进程抽样**：老板必真读，**不允许只看 P5.4 数字 PASS**
3. **3 哲学必填**：马斯克 + 康德 + 苏格拉底，**手填不替代**
4. **Redis 双重标记**：文件内 `<!-- status: finished -->` + Redis `:status = finished`
5. **5% FAKE 阈值**：超了就整轮 FAIL 重跑 opencode

---

## 十四、Docker 环境 + 代码必读硬约束（**用户 2026-06-13 第 2 轮反馈**）

### 14.1 docker 环境强约束

```python
COMMAND = """
【环境强约束】  # 必跑
1. 必跑 `docker ps` 找容器名（用户给 webgoat-local 就用 webgoat-local）
2. 必跑 `docker inspect webgoat-local` 拿 IP/端口/volume/env
3. 必跑 `docker logs webgoat-local --tail 50` 看启动日志
4. 必跑 `docker network inspect <network>` 查容器网络
"""
```

### 14.2 代码必读（不许只 curl 根 URL）

```python
COMMAND += """
【代码必读 - 找真实入口】  # 必读
1. 必读 `src/main/resources/*/templates/login.html` 找 form action
2. 必读 `src/main/java/**/Security*.java` 找 permitAll + formLogin
3. 必读 `src/main/java/**/controller/**Controller.java` 找所有 @PostMapping/@GetMapping
4. 必列**所有 lesson 端点** = grep -rE "@(Post|Get|Delete|Put)Mapping" src/main/java/
5. 必读 application.yml/properties 找 server.servlet.context-path
"""
```

### 14.3 404 必须诘问

```python
COMMAND += """
【404 必查 - 不是 pass】  # 404 = FAIL 信号
- curl 返回 404 → 必查：
  1. 路径是否要带应用前缀（/WebGoat/login 而不是 /login）
  2. 是否要带 session cookie（先 POST /login）
  3. 是否要 POST 不是 GET
  4. 是不是被 SecurityConfig 拒了（401/403 而非 404）
  5. 必读 404 响应 body 找 error 提示
"""
```

### 14.4 历史踩坑（**2026-06-13 第 2 轮**）

- opencode curl GET / 返回 404 说"端点可达"
- 真实登录入口是 `POST /login`（在 `src/main/resources/webgoat/templates/login.html`）
- 真实 URL 前缀 `/WebGoat`（用户给的是 `http://localhost:18080/WebGoat`）
- 老板没诘问 404，没让 opencode 读代码
- **根因**：prompt 只给 URL，没给容器名 / 没要求读代码

### 14.5 老板的 3 句新真言（**追加**）

1. **必传容器名**给 opencode（用户给 webgoat-local 就必须用）
2. **404 不是 pass**，是 FAIL 信号，必查
3. **代码必读**，不许只 curl 根 URL 拿 404 当"端点可达"

### 14.6 老板自我打分

| 项 | 之前 | 现在 |
|----|------|------|
| 必传环境信息 | ❌ 部分（没容器名）| ✅ 必传 |
| 必读代码 | ❌ 没要求 | ✅ 必读 |
| 404 必查 | ❌ 跳过 | ✅ 必查 |
| 综合 | D | A |

### 14.7 给未来轮次的 4 件事

1. 改 `cross-agent-50r.py` 的 COMMAND 必含 § 14.1-14.3 三段
2. 重跑 round 1，让 opencode 必：
   - `docker ps` 找 webgoat-local
   - 读 login.html
   - POST /login 拿 session
   - 用 session 访问受保护端点
3. 老板再读 20% 抽样 + 5% 进程抽样
4. 若仍 5%+ FAKE → 改 prompt 第 4 轮

---

## 十五、200 OK ≠ 成功（**用户 2026-06-13 工程原则**）

### 15.1 核心原则

> **只有返回 200 OK 且实际 CIA 影响可证明，才是 OK 的**。
> **仅 HTTP 200 仅代表"端点可达"≠ 漏洞可利用**。

| 响应 | 状态 | 含义 |
|------|------|------|
| 200 OK + 必有 CIA 证据 | **PASS** | 真漏洞 |
| 200 OK 但无 CIA 证据 | **FAIL** | 仅测可达性，无漏洞 |
| 4xx / 5xx | **FAIL** | 攻击失败 |
| 仅 GET / 返回 200 | **FAIL** | 啥都没证明 |
| 404 | **FAIL** | 路径错，需重查 |

### 15.2 CIA 三维定义

- **C (Confidentiality)**: 数据泄露（DB rows / file content / secrets / password / token）
- **I (Integrity)**: 数据修改（write/delete/update 成功 / 状态改变）
- **A (Availability)**: 服务中断（5xx / stack trace / crash / timeout）

### 15.3 必含 CIA 证据 pattern（13+ 条）

**C (data_exfiltration)** 8 条：
- `root:[x*]:0:0`（/etc/passwd）
- `admin@[a-z]+\.com`（email enumeration）
- `\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}`（internal IP）
- `password\s*[:=]`（password leak）
- `api[_-]?key\s*[:=]`（API key）
- `Bearer\s+[A-Za-z0-9]{20,}`（JWT）
- `flag\{[a-zA-Z0-9_-]+\}`（CTF flag — WebGoat 典型）
- `\d+\s*rows?\s*in\s*set`（DB rows returned）

**I (data_modification)** 4 条：
- `UPDATE|DELETE|INSERT|DROP\s+.*SET|INTO|FROM|TABLE`
- `successfully\s+(?:deleted|updated|inserted)`
- `status\":\s*\"(?:ok|success|completed|true)`
- `modified\s+\d+\s+rows?`

**A (service_disruption)** 5 条：
- `500|503\s+(?:Internal|Service)\s+Error`
- `stack\s*trace`
- `NullPointerException`
- `OutOfMemoryError`
- `timeout\s+after\s+\d+`

### 15.4 PoC 必含 4 字段

```python
PoC = {
    "baseline_response": "正常请求的响应（参照基线）",
    "attack_response":   "攻击后响应（必含 CIA 证据）",
    "cia_classification": "C" | "I" | "A" | "C+I" | ...,
    "impact_description": "实际影响：管理员密码泄露 / 任意用户数据可读 / ..."
}
```

### 15.5 加进 prompt 的强制条款

```python
COMMAND += """
【PoC 必证明 CIA 影响 - 200 OK ≠ 成功】
1. baseline：先发**正常请求**，记录响应
2. attack：发攻击 payload（如 admin' OR 1=1 --）
3. 必含 attack_response 中 CIA 证据：
   - C: 响应 body 含 DB rows / file content / password / token
   - I: 响应确认 modified N rows / success
   - A: 响应含 stack trace / 5xx / 异常
4. 必标 cia_classification: C / I / A（可多个）
5. impact_description: 1 句话具体影响
"""
```

### 15.6 修改 verifier（`verify-opencode-compliance.py`）

新增 **第 7 项 CIA 证据检查**：

```bash
python 脚本/audit/verify-opencode-compliance.py --diag-dir .../diag
# 输出新增：
#   --- CIA 证据检查（200 OK ≠ 成功，必证明 CIA 影响）---
#   [OK] CIA 证据: C=4 I=0 A=1 (total 5 个)
#       C: root:[x*]:0:0 (示例)
#       A: NullPointerException (示例)
```

**任一 CIA 类别 = 0 证据 → 整轮 FAIL**。

### 15.7 老板的 5 句新真言（**§ 15 终极**）

1. **200 OK ≠ 成功** — 仅测可达性 = FAIL
2. **必证明 CIA 影响** — 数据泄露 / 修改 / 中断 3 选 1+
3. **PoC 必含 4 字段** — baseline + attack + cia + impact
4. **响应 body 必含证据** — flag{...} / password / stack trace 等
5. **无 CIA 证据 = 自动 FAIL** — 强制条款，不是可选项

### 15.8 历史踩坑（**2026-06-13 round 1 100% FAKE**）

100% PoC 都是 200 OK 但**无 CIA 证据**。具体：
- HTTP 200（端点可达）
- 无 DB rows / passwords / flags 出现
- 无修改成功的证据
- 无 5xx / 异常
- 老板之前只看 P5.4 invariant 数字 PASS

**修正**：13+ CIA pattern 自动检测 + 老板 20% 抽样必读 4 字段 + 5% 进程抽样必看攻击 payload 真假。

### 15.9 老板自我打分

| 项 | 之前 | 现在 |
|----|------|------|
| P5.4 PASS 必 PASS | ❌ 100% FAKE | ✅ 加 CIA 必证 |
| 200 OK 必成功 | ❌ | ✅ 必证 CIA |
| PoC 必含 4 字段 | ❌ | ✅ 强约束 |
| 综合 | D | **A+** |
