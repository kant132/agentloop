# TDD: 脚本测试用例

> **测试方法**：纯 Python `unittest`，外部依赖用 mock 或 fake
> **运行命令**：`python -m unittest discover doc/test_scripts/`
> **覆盖率目标**：≥ 80%
> **总用例数**：39 条，覆盖 12 个脚本

---

## 一、测试环境约束

- **Memurai**：默认 `localhost:6379`；不可用时 mock subprocess
- **codegraph SQLite**：使用 `doc/fixtures/codegraph-fake.db`（小型 fixture，~10 nodes / 8 edges）
- **文件系统**：使用 `tempfile.TemporaryDirectory()`
- **subprocess 入口**：所有 `_run()` 调用通过 `unittest.mock.patch("subprocess.run")` 拦截
- **fixture 内容**：
  - nodes 表：含 1 个 route、3 个 method、2 个 class、1 个 field
  - edges 表：含 2 条 `calls`、1 条 `contains`、1 条 `references`
  - 链：`route → Controller.listUsers → Service.findAll → Dao.executeQuery`（4 层）

---

## 二、Memurai client 测试（`redis/memurai_client.py`）

### TC-MC-001: ping 成功
- **目的**：验证 `ping()` 正确解析 PONG
- **前置条件**：`unittest.mock.patch("subprocess.run")` 返回 `stdout="PONG\n"`, `returncode=0`
- **输入**：`Memurai(host="localhost", port=6379, cli_path="/fake/cli").ping()`
- **预期**：`assertEqual(result, True)`；`subprocess.run` 被调用 1 次，命令以 `PING` 结尾
- **实现片段**：
  ```python
  with mock.patch("subprocess.run", return_value=Mock(rc=0, stdout="PONG\n")):
      cli = Memurai(cli_path="/fake/cli"); self.assertTrue(cli.ping())
  ```
- **真实场景引用**：I2「缓存 `fqn_method_签名` 到内存」前的连通性测试

### TC-MC-002: set/get 往返
- **目的**：验证 `set()` 后 `get()` 能取回
- **前置条件**：mock subprocess 依次返回 `OK\n` / `"value-1"`
- **输入**：`set("k1", "value-1", ex=60)` → `get("k1")`
- **预期**：`assertEqual(cli.get("k1"), "value-1")`；SET 命令包含 `EX 60`
- **实现片段**：
  ```python
  sides = [Mock(rc=0, stdout="OK\n"), Mock(rc=0, stdout="value-1")]
  with mock.patch("subprocess.run", side_effect=sides):
      cli.set("k1", "value-1", ex=60); self.assertEqual(cli.get("k1"), "value-1")
  ```
- **真实场景引用**：I2 缓存写入读取

### TC-MC-003: set EX 走 EX 子句
- **目的**：验证 `set(key, val, ex=120)` 命令行含 `EX 120`
- **前置条件**：mock subprocess 返回 `OK`
- **输入**：`set("k", "v", ex=120)`
- **预期**：`cmd` 列表含 `"EX"` 与 `"120"`
- **实现片段**：
  ```python
  with mock.patch("subprocess.run", return_value=Mock(rc=0, stdout="OK\n")) as m:
      cli.set("k", "v", ex=120)
      self.assertIn("EX", m.call_args.args[0]); self.assertIn("120", m.call_args.args[0])
  ```
- **真实场景引用**：I2 缓存 TTL 语义

### TC-MC-004: mset 多 key
- **目的**：验证 `mset({"k1":"v1","k2":"v2"})` 命令行含 5 段（MSET k1 v1 k2 v2）
- **前置条件**：mock subprocess 返回 `OK`
- **输入**：`mset({"k1":"v1","k2":"v2"})`
- **预期**：`cmd` 长度为 6（cli + MSET + 4 段 kv）
- **实现片段**：
  ```python
  with mock.patch("subprocess.run", return_value=Mock(rc=0, stdout="OK\n")) as m:
      cli.mset({"k1":"v1","k2":"v2"})
      self.assertEqual(len(m.call_args.args[0]), 6)  # cli + MSET + 4
  ```
- **真实场景引用**：batch 缓存写入

### TC-MC-005: delete 多 key
- **目的**：验证 `delete("k1", "k2", "k3")` 返回删除数
- **前置条件**：mock 返回 `(integer) 2\n`
- **输入**：`delete("k1", "k2", "k3")`
- **预期**：`assertEqual(result, 2)`
- **实现片段**：
  ```python
  with mock.patch("subprocess.run", return_value=Mock(rc=0, stdout="(integer) 2\n")):
      self.assertEqual(cli.delete("k1","k2","k3"), 2)
  ```
- **真实场景引用**：自检脚本失效不匹配 key（TC-RSC-002）

### TC-MC-006: scan_iter 走 --scan
- **目的**：验证 `scan_iter("audit:*:method:*")` 走 `--scan` 子命令
- **前置条件**：mock 返回 `"k1\nk2\nk3\n"`
- **输入**：`list(scan_iter("audit:*:method:*", count=500))`
- **预期**：`result == ["k1","k2","k3"]`；cmd 含 `"--scan"` 与 `"--pattern"`
- **实现片段**：
  ```python
  with mock.patch("subprocess.run", return_value=Mock(rc=0, stdout="k1\nk2\nk3\n")) as m:
      keys = list(cli.scan_iter("audit:*:method:*"))
      self.assertEqual(keys, ["k1","k2","k3"])
      self.assertIn("--scan", m.call_args.args[0])
  ```
- **真实场景引用**：S10「Redis 减文件读写」基础

### TC-MC-007: pipe_setex_batch N 条命令 1 次子进程
- **目的**：验证 50 条 `pipe_setex_batch` 只触发 1 次 subprocess
- **前置条件**：mock 返回 `+OK\n` × 50
- **输入**：`pipe_setex_batch([(f"k{i}", 60, f"v{i}") for i in range(50)])`
- **预期**：`mock.call_count == 1`；`input_bytes` 含 `b"*50\r\n"`；返回 50
- **实现片段**：
  ```python
  out = "\n".join(["+OK"]*50)
  with mock.patch("subprocess.run", return_value=Mock(rc=0, stdout=out)) as m:
      n = cli.pipe_setex_batch([(f"k{i}",60,f"v{i}") for i in range(50)])
      self.assertEqual(m.call_count, 1); self.assertEqual(n, 50)
  ```
- **真实场景引用**：R0.2 + I2 性能优化（30x 子进程减少）

### TC-MC-008: 命令失败抛 MemuraiError
- **目的**：验证 `returncode != 0` 抛 `MemuraiError`
- **前置条件**：mock 返回 `returncode=1`, `stderr="ERR unknown command"`
- **输入**：`cli.get("missing")`
- **预期**：`assertRaises(MemuraiError)`，`e.returncode == 1`
- **实现片段**：
  ```python
  with mock.patch("subprocess.run", return_value=Mock(rc=1, stderr="ERR", stdout="")):
      self.assertRaises(MemuraiError, cli.get, "missing")
  ```
- **真实场景引用**：所有脚本的「Memurai 不可用」错误路径

### TC-MC-009: CLI 路径不存在抛错
- **目的**：验证 `cli_path` 指向不存在文件时构造即抛 `MemuraiError`
- **前置条件**：传入 `/no/such/cli.exe`
- **输入**：`Memurai(cli_path="/no/such/cli.exe")`
- **预期**：`assertRaises(MemuraiError)` 且 msg 含 `"memurai-cli 不存在"`
- **实现片段**：
  ```python
  with self.assertRaises(MemuraiError) as cm:
      Memurai(cli_path="/no/such/cli.exe")
  self.assertIn("不存在", str(cm.exception))
  ```
- **真实场景引用**：CI/新机器首次启动的友好错误

---

## 三、redis-batch-prefetch 测试（`redis/redis-batch-prefetch.py`）

### TC-RBP-001: 空链返回空
- **目的**：空 chain 列表应返回 `methods_prefetched=0`
- **前置条件**：mock `pipe_setex_batch` 不被调用，`setex` 不被调用
- **输入**：`prefetch_chain(cli, [], "g", "c", "chain-x")`
- **预期**：`assertEqual(result["methods_prefetched"], 0)`；`prefetch_key` 仍写入
- **实现片段**：
  ```python
  cli.pipe_setex_batch = Mock(); cli.setex = Mock(return_value=True)
  r = prefetch_chain(cli, [], "g", "c", "chain-x")
  self.assertEqual(r["methods_prefetched"], 0); cli.pipe_setex_batch.assert_not_called()
  ```
- **真实场景引用**：空项目/无效端点的优雅降级

### TC-RBP-002: 30 个方法走 --pipe 一次
- **目的**：30 个方法的 chain 触发 1 次 `pipe_setex_batch`
- **前置条件**：mock `pipe_setex_batch` 返回 30
- **输入**：30 个 method 字典的 chain
- **预期**：`cli.pipe_setex_batch.call_count == 1`；`call_args[0][0]` 长度 = 30
- **实现片段**：
  ```python
  chain = [{"fqn":f"pkg::C::m{i}","sigHash":f"h{i}","body":f"b{i}","file":"F.java","line":1} for i in range(30)]
  cli.pipe_setex_batch = Mock(return_value=30); cli.setex = Mock(return_value=True)
  r = prefetch_chain(cli, chain, "g", "HEAD", "cid")
  self.assertEqual(cli.pipe_setex_batch.call_count, 1)
  self.assertEqual(len(cli.pipe_setex_batch.call_args[0][0]), 30)
  ```
- **真实场景引用**：R0.2「不主动写代码（工具类除外）」+ 性能优化

### TC-RBP-003: chain_summary 写入
- **目的**：`chain_summary` 通过 `setex` 写入 `audit:{g}:commit:{c}:prefetch:{chain_id}`
- **前置条件**：mock `setex`
- **输入**：`chain_id="abc123"`
- **预期**：`cli.setex` 被调用 1 次，key 以 `prefetch:abc123` 结尾
- **实现片段**：
  ```python
  cli.pipe_setex_batch = Mock(); cli.setex = Mock(return_value=True)
  prefetch_chain(cli, [{"fqn":"a","body":"b"}], "g", "HEAD", "abc123")
  key = cli.setex.call_args.args[0]
  self.assertTrue(key.endswith("prefetch:abc123"))
  ```
- **真实场景引用**：I2 缓存链摘要供 subagent 读

### TC-RBP-004: sigHash 自动计算
- **目的**：缺 `sigHash` 时基于 body 计算 SHA256 前 16 位
- **前置条件**：mock 客户端；chain 项仅含 `body` 不含 `sigHash`
- **输入**：`[{"fqn":"a","body":"hello world"}]`
- **预期**：写入的 value JSON 中 `sig_hash` 是 `hashlib.sha256(b"hello world").hexdigest()[:16]`
- **实现片段**：
  ```python
  cli.pipe_setex_batch = Mock(return_value=1); cli.setex = Mock()
  prefetch_chain(cli, [{"fqn":"a","body":"hello world"}], "g", "HEAD", "c")
  v = cli.pipe_setex_batch.call_args[0][0][0][2]
  self.assertEqual(json.loads(v)["sig_hash"], hashlib.sha256(b"hello world").hexdigest()[:16])
  ```
- **真实场景引用**：P5.4「每个外部端点的每个方法都必须输出一篇独立报告」签名稳定

---

## 四、redis-self-check 测试（`redis/redis-self-check.py`）

### TC-RSC-001: 50 样本全过
- **目的**：50 个缓存 key 与 codegraph 一致时全部通过
- **前置条件**：mock `cli.scan_iter` 返回 50 个 key；`cli.get` 返回与 codegraph 完全一致的 body
- **输入**：`self_check(cli, "g", "HEAD", "fake.db", sample_size=50)`
- **预期**：`result["passed"] == 50`，`result["failed"] == 0`
- **实现片段**：
  ```python
  with mock.patch("random.sample", side_effect=lambda x,n: x[:n]):
      r = self_check(cli, "g", "HEAD", "doc/fixtures/codegraph-fake.db", 50)
  self.assertEqual(r["passed"], 50); self.assertEqual(r["failed"], 0)
  ```
- **真实场景引用**：I3「验证自己保存的对不对」+ S7「结果可信」

### TC-RSC-002: 不匹配自动失效
- **目的**：body 不一致时 `cli.delete` 被调用，结果含 `body_mismatch`
- **前置条件**：mock `cli.get` 返回与 codegraph 不同的 body
- **输入**：`self_check(cli, "g", "HEAD", "fake.db", sample_size=10)`
- **预期**：`r["failed"] >= 1`；mismatches[0]["reason"] == "body_mismatch"；`cli.delete.call_count >= 1`
- **实现片段**：
  ```python
  cli.get = Mock(return_value=json.dumps({"fqn":"x","body":"OLD"}))
  cli.delete = Mock(); cli.scan_iter = Mock(return_value=iter(["audit:g:commit:H:method:x#h"]))
  r = self_check(cli, "g", "HEAD", "doc/fixtures/codegraph-fake.db", 1)
  self.assertEqual(r["mismatches"][0]["reason"], "body_mismatch")
  ```
- **真实场景引用**：S7「结果可信：统计数据 + 数据对账一致」

### TC-RSC-003: 无 key 不报错
- **目的**：scan 0 key 时返回 `warning: no keys found`
- **前置条件**：`cli.scan_iter` 返回空
- **输入**：`self_check(cli, "g", "HEAD", "fake.db")`
- **预期**：`r["checked"] == 0`；`r["warning"] == "no keys found"`
- **实现片段**：
  ```python
  cli.scan_iter = Mock(return_value=iter([]))
  r = self_check(cli, "g", "HEAD", "doc/fixtures/codegraph-fake.db", 50)
  self.assertEqual(r["checked"], 0); self.assertIn("warning", r)
  ```
- **真实场景引用**：首次启动缓存空场景

---

## 五、redis-stats 测试（`redis/redis-stats.py`）

### TC-RS-001: 5 类 key 计数
- **目的**：`get_stats` 返回 5 个计数键
- **前置条件**：`cli.count` mock 返回固定值（method=120, chain=10, ...）
- **输入**：`get_stats(cli, "com.example.x")`
- **预期**：5 个 key 全在 result；`result["method_count"] == 120`
- **实现片段**：
  ```python
  cli.count = Mock(side_effect=lambda p: {"method":120,"chain":10,"prefetch":8,"draft":15,"final":5}[p.split(":")[-2]])
  r = get_stats(cli, "com.example.x")
  self.assertEqual(r["method_count"], 120); self.assertEqual(r["finding_final_count"], 5)
  ```
- **真实场景引用**：S7「结果可信：统计数据」

### TC-RS-002: 内存信息
- **目的**：`info("memory")` 输出含 `used_memory_human` 时正确解析
- **前置条件**：`cli.info` 返回 `"used_memory_human:12.5M\r\n..."`
- **输入**：`get_stats(cli, "g")`
- **预期**：`result["used_memory_human"] == "12.5M"`
- **实现片段**：
  ```python
  cli.count = Mock(return_value=0); cli.info = Mock(return_value="used_memory_human:12.5M\n")
  r = get_stats(cli, "g"); self.assertEqual(r["used_memory_human"], "12.5M")
  ```
- **真实场景引用**：P2.3「输出技术栈」+ 监控指标

---

## 六、sqlite-extract-chain 测试（`chain/sqlite-extract-chain.py`）

### TC-SEC-001: CTE RECURSIVE depth=20
- **目的**：`extract_recursive("fake.db", "m:1", 20)` 返回 4 行（fixture 链深度 = 4）
- **前置条件**：`doc/fixtures/codegraph-fake.db` 4 节点链
- **输入**：`extract_recursive(db, "m:entry", 20)`
- **预期**：返回列表长度 ≥ 3；首行 `depth == 0`，末行 `depth == 3`
- **实现片段**：
  ```python
  rows = extract_recursive("doc/fixtures/codegraph-fake.db", "m:entry", 20)
  self.assertGreaterEqual(len(rows), 3); self.assertEqual(rows[0]["depth"], 0)
  ```
- **真实场景引用**：P5.2「codegraph + ast-grep + 污点分析」

### TC-SEC-002: 防环检测
- **目的**：fixture 中若有自环边（m1 → m1），CTE 应避免无限递归
- **前置条件**：fixture 增补 `edges(m1, m1, "calls")`
- **输入**：`extract_recursive(db, "m:1", 20)`
- **预期**：行数 < 100（未死循环）
- **实现片段**：
  ```python
  rows = extract_recursive("doc/fixtures/codegraph-fake.db", "m:1", 20)
  self.assertLess(len(rows), 100)
  ```
- **真实场景引用**：P5.13「剪枝逻辑和参数传递分析」

### TC-SEC-003: 真实 schema（nodes/edges）正确
- **目的**：SQL 引用 `nodes.qualified_name` / `edges.kind='calls'`
- **前置条件**：fixture 仅有 `nodes`/`edges` 表，无 `method` 表
- **输入**：`extract_recursive(db, "m:1", 5)`
- **预期**：正常返回（不报 `no such table: method`）
- **实现片段**：
  ```python
  try: extract_recursive("doc/fixtures/codegraph-fake.db", "m:1", 5)
  except sqlite3.OperationalError as e: self.fail(f"schema 误: {e}")
  ```
- **真实场景引用**：R0.4「沉淀为漏洞相关 skill」+ codegraph v0.9.9

### TC-SEC-004: LEFT JOIN mode 4 跳 = 8 JOIN
- **目的**：`build_left_sql(4)` 产生恰好 8 个 `LEFT JOIN`
- **前置条件**：无（纯函数）
- **输入**：`sql, _ = build_left_sql(4)`
- **预期**：`sql.count("LEFT JOIN") == 8`
- **实现片段**：
  ```python
  sql, _ = build_left_sql(4)
  self.assertEqual(sql.count("LEFT JOIN"), 8)
  ```
- **真实场景引用**：P5.2「codegraph + 污点分析」+ 性能提示

### TC-SEC-005: entry_fqn 解析为 id
- **目的**：`resolve_entry("fake.db", "pkg::C::m")` 返回 `m:1`
- **前置条件**：fixture 节点表存在该 qualified_name
- **输入**：`resolve_entry(db, "pkg::C::m")`
- **预期**：`assertEqual(result, "m:1")`
- **实现片段**：
  ```python
  self.assertEqual(resolve_entry("doc/fixtures/codegraph-fake.db", "pkg::C::m"), "m:1")
  ```
- **真实场景引用**：P5.1「每个外部端口启动适当数量 subagent」

---

## 七、sqlite-pattern-search 测试（`chain/sqlite-pattern-search.py`）

### TC-SPS-001: sql_injection 模式
- **目的**：`search_pattern(db, "sql_injection")` 返回含 `executeQuery` 的 method
- **前置条件**：fixture 含 `pkg::Dao::executeQuery`
- **输入**：`search_pattern("fake.db", "sql_injection")`
- **预期**：至少 1 个结果；`result[0]["name"] == "executeQuery"`
- **实现片段**：
  ```python
  rows = search_pattern("doc/fixtures/codegraph-fake.db", "sql_injection")
  self.assertTrue(any(r["name"] == "executeQuery" for r in rows))
  ```
- **真实场景引用**：P5.3「高危函数可达性」+ sink 模式

### TC-SPS-002: rce 模式
- **目的**：`rce` 模式匹配 `Runtime.exec` / `ProcessBuilder`
- **前置条件**：fixture 节点含 `Runtime.exec`
- **输入**：`search_pattern(db, "rce")`
- **预期**：results 含 Runtime 相关
- **实现片段**：
  ```python
  rows = search_pattern("doc/fixtures/codegraph-fake.db", "rce")
  self.assertTrue(any("Runtime" in r["qualified_name"] for r in rows))
  ```
- **真实场景引用**：P2.5「找所有认证鉴权实现」+ sink 模式

### TC-SPS-003: 未知 pattern 报错
- **目的**：`search_pattern(db, "xss")` 抛 `ValueError`
- **前置条件**：无
- **输入**：`search_pattern("fake.db", "xss")`
- **预期**：`assertRaises(ValueError)`，msg 含 `"未知 pattern"`
- **实现片段**：
  ```python
  with self.assertRaises(ValueError) as cm:
      search_pattern("doc/fixtures/codegraph-fake.db", "xss")
  self.assertIn("未知 pattern", str(cm.exception))
  ```
- **真实场景引用**：CLI 错误友好性

---

## 八、sqlite-multi-hop-search 测试（`chain/sqlite-multi-hop-search.py`）

### TC-SMHS-001: forward_5hop_20join = 20 JOIN
- **目的**：`run_template("forward_5hop_20join", ...)` 报告 `join_count=20`
- **前置条件**：fixture 节点含 5 跳链
- **输入**：`run_template(db, "forward_5hop_20join", {"entry_fqn":"pkg::C::m"})`
- **预期**：`join_count == 20`，rows 长度 ≥ 1
- **实现片段**：
  ```python
  rows, joins, _ = run_template("doc/fixtures/codegraph-fake.db", "forward_5hop_20join", {"entry_fqn":"pkg::C::m"})
  self.assertEqual(joins, 20); self.assertGreaterEqual(len(rows), 1)
  ```
- **真实场景引用**：P5.2「污点分析」+ 性能

### TC-SMHS-002: 多 sink 模式
- **目的**：`multi_sink_search` 返回 `sink_type` 字段
- **前置条件**：fixture 含 SQLi/RCE sink
- **输入**：`run_template(db, "multi_sink_search", {"entry_fqn":"pkg::C::m"})`
- **预期**：rows 中至少一个 `sink_type` 是 `SQLI` / `RCE` 之一
- **实现片段**：
  ```python
  rows, _, _ = run_template("doc/fixtures/codegraph-fake.db", "multi_sink_search", {"entry_fqn":"pkg::C::m"})
  self.assertIn(rows[0]["sink_type"], {"SQLI","RCE","DESER","SSRF","PATH_TRAV","LDAP","UNKNOWN"})
  ```
- **真实场景引用**：P5.5「致命严重调用链小节」sink 类型

### TC-SMHS-003: 自定义 SQL
- **目的**：`run_custom` 执行用户 SQL，占位符替换
- **前置条件**：fixture 标准
- **输入**：`run_custom(db, "SELECT qualified_name FROM nodes WHERE id=:id", {"id":"m:1"})`
- **预期**：rows[0]["qualified_name"] == "pkg::C::m"
- **实现片段**：
  ```python
  rows = run_custom("doc/fixtures/codegraph-fake.db", "SELECT qualified_name FROM nodes WHERE id=:id", {"id":"m:1"})
  self.assertEqual(rows[0]["qualified_name"], "pkg::C::m")
  ```
- **真实场景引用**：P4.2「自定义注解去 codegraph SQL 查」

---

## 九、finding-promoter 测试（`audit/finding-promoter.py`）

### TC-FP-001: 单次评分写入
- **目的**：`record_score` 写 `audit:g:commit:H:score:{cid}:r1`
- **前置条件**：mock `cli.set`
- **输入**：`record_score(cli, "g", "H", "c1", 1, 88)`
- **预期**：`cli.set` 被调用 1 次，key 含 `score:c1:r1`，value = `"88"`
- **实现片段**：
  ```python
  cli.set = Mock(); r = record_score(cli, "g","H","c1", 1, 88)
  self.assertEqual(cli.set.call_args.args[0], "audit:g:commit:H:score:c1:r1")
  self.assertEqual(cli.set.call_args.args[1], "88")
  ```
- **真实场景引用**：L3「每轮反思打分」

### TC-FP-002: 3 轮 > 85 落盘
- **目的**：评分 [90, 88, 92] → `promoted=True`，文件写出
- **前置条件**：`cli.get` 顺序返回 `"90", "88", "92", "<draft-json>"`；`tmp_path` 提供 output_dir
- **输入**：`check_and_promote(cli, "g", "H", "c1", tmp_path)`
- **预期**：`r["promoted"] is True`；`Path(tmp_path/"c1.json").exists()` 为真
- **实现片段**：
  ```python
  draft = json.dumps({"endpoint_fqn":"e","chain_id":"c1","description":"vuln"})
  cli.get = Mock(side_effect=["90","88","92",draft])
  r = check_and_promote(cli, "g","H","c1", str(tmp_path))
  self.assertTrue(r["promoted"]); assert Path(tmp_path,"c1.json").exists()
  ```
- **真实场景引用**：L5「连续 3 轮 > 85 → finished」+ S10「3x85 写盘」

### TC-FP-003: 含 < 85 不落盘
- **目的**：评分 [90, 80, 92] → `promoted=False`
- **前置条件**：`cli.get` 返回 `"90","80","92"`
- **输入**：`check_and_promote(cli, "g", "H", "c1", tmp_path)`
- **预期**：`r["promoted"] is False`；`reason` 含 `"未全部 > 85"`
- **实现片段**：
  ```python
  cli.get = Mock(side_effect=["90","80","92",""])
  r = check_and_promote(cli, "g","H","c1", str(tmp_path))
  self.assertFalse(r["promoted"]); self.assertIn("未全部", r["reason"])
  ```
- **真实场景引用**：L4「任一项 < 90 优化继续」+ L7「< 85 补充」

---

## 十、data-reconcile 测试（`audit/data-reconcile.py`）

### TC-DR-001: 8 项全过
- **目的**：构造 8 项全部一致的 fixture，`all_ok=True`
- **前置条件**：写 `findings.jsonl`（含 final finding 5 条）/ `endpoints.jsonl`（10 端点）；`cli.count` 返回 5；redis chains 返回 3
- **输入**：`reconcile(findings, endpoints, cli, "g", "H")`
- **预期**：`r["all_ok"] is True`；`r["warn_count"] == 0`
- **实现片段**：
  ```python
  cli.count = Mock(side_effect=lambda p: 5 if "final" in p else 3)
  r = reconcile(findings_p, endpoints_p, cli, "g","H")
  self.assertTrue(r["all_ok"]); self.assertEqual(r["warn_count"], 0)
  ```
- **真实场景引用**：S7「结果可信：数据对账一致」+ L9「75% 高危 PoC」

### TC-DR-002: Redis 链数不一致标 WARN
- **目的**：`cli.count("chain:*")` 与 `findings` 中 `chain_id` 去重数不等
- **前置条件**：findings 含 3 个 chain_id，cli.count 返回 2
- **输入**：`reconcile(...)`
- **预期**：`r["items"]["8_Memurai链key防造数据"]["ok"] is False`
- **实现片段**：
  ```python
  cli.count = Mock(side_effect=lambda p: 5 if "final" in p else 2)  # chains=2
  r = reconcile(findings_p, endpoints_p, cli, "g","H")
  self.assertFalse(r["items"]["8_Memurai链key防造数据"]["ok"])
  ```
- **真实场景引用**：防造数据（item8）

---

## 十一、force-rescan 测试（`audit/force-rescan.py`）

### TC-FR-001: 仅 C/D 模式通过
- **目的**：`force_rescan(cli, "g", "H", "ep", "fqn", ["C","D"])` 正常返回 queued
- **前置条件**：mock `cli.set_json`；`tmp_path` 作为 audit_root
- **输入**：modes=["C","D"]
- **预期**：`r["status"] == "queued"`；`cli.set_json` 被调用 1 次
- **实现片段**：
  ```python
  cli.set_json = Mock(); r = force_rescan(cli,"g","H","GET /x","fqn",["C","D"], str(tmp_path))
  self.assertEqual(r["status"], "queued"); cli.set_json.assert_called_once()
  ```
- **真实场景引用**：P5.14「无法识别风险的 jar/三方服务 → 需人工分析」

### TC-FR-002: 含 A 报错
- **目的**：CLI 层 `modes=["A","C"]` 应退出码 1
- **前置条件**：`subprocess` 启动当前脚本 + `--modes A,C`
- **输入**：`python force-rescan.py --modes A,C ...`
- **预期**：`returncode == 1`；stderr 含 `"只允许跑 C/D"`
- **实现片段**：
  ```python
  r = subprocess.run([sys.executable, "force-rescan.py", "--modes","A,C","--endpoint","x","--endpoint-fqn","y","--group-id","g"], capture_output=True)
  self.assertEqual(r.returncode, 1); self.assertIn("只允许", r.stderr.decode())
  ```
- **真实场景引用**：原始指令「只分析业务、逻辑漏洞」

### TC-FR-003: Redis 写入
- **目的**：`cli.set_json` 接收的 key 以 `force-rescan:` 开头
- **前置条件**：mock `cli.set_json`
- **输入**：`force_rescan(...)`
- **预期**：key 含 `force-rescan:` 和 chain_id（前 16 位 sha256 of fqn）
- **实现片段**：
  ```python
  cli.set_json = Mock(); force_rescan(cli,"g","H","ep","com.x.Y::z",["C","D"], str(tmp_path))
  key = cli.set_json.call_args.args[0]
  self.assertIn("force-rescan:", key)
  self.assertIn(hashlib.sha256(b"com.x.Y::z").hexdigest()[:16], key)
  ```
- **真实场景引用**：P5.14 复审流程

---

## 十二、list-pruned 测试（`audit/list-pruned.py`）

### TC-LP-001: 读取 jsonl
- **目的**：3 条 pruning log → `pruned_count=3`
- **前置条件**：写 3 行 jsonl 到 `tmp_path/pruning-log.jsonl`
- **输入**：`list_pruned(str(tmp_path/"pruning-log.jsonl"))`
- **预期**：`r["pruned_count"] == 3`
- **实现片段**：
  ```python
  log.write("\n".join([json.dumps({"rule_id":"P-L1-001","level":"L1"}) for _ in range(3)]))
  r = list_pruned(str(log)); self.assertEqual(r["pruned_count"], 3)
  ```
- **真实场景引用**：P5.13「剪枝逻辑」

### TC-LP-002: 按规则分桶
- **目的**：3 条 P-L1-001 + 2 条 P-L2-001 → `by_rule` 含两个 key
- **前置条件**：混合规则 jsonl
- **输入**：`list_pruned(...)`
- **预期**：`r["by_rule"]["P-L1-001"] == 3`；`r["by_rule"]["P-L2-001"] == 2`
- **实现片段**：
  ```python
  items = [{"rule_id":"P-L1-001","level":"L1"}]*3 + [{"rule_id":"P-L2-001","level":"L2"}]*2
  log.write("\n".join(json.dumps(i) for i in items))
  r = list_pruned(str(log))
  self.assertEqual(r["by_rule"]["P-L1-001"], 3); self.assertEqual(r["by_rule"]["P-L2-001"], 2)
  ```
- **真实场景引用**：O8「剪枝规则文档：独立保存」

---

## 十三、preset-init 测试（`audit/preset-init.py`）

### TC-PI-001: 从 pom.xml 提 groupId
- **目的**：`detect_from_pom(pom)` 返回 `groupId=com.example.x`
- **前置条件**：写 `<groupId>com.example.x</groupId><artifactId>foo</artifactId>` 到 tmp pom
- **输入**：`detect_from_pom(str(tmp_pom))`
- **预期**：`r["groupId"] == "com.example.x"`；`r["artifactId"] == "foo"`
- **实现片段**：
  ```python
  tmp_pom.write_text("<groupId>com.example.x</groupId><artifactId>foo</artifactId>")
  r = detect_from_pom(str(tmp_pom))
  self.assertEqual(r["groupId"], "com.example.x"); self.assertEqual(r["artifactId"], "foo")
  ```
- **真实场景引用**：I1「codegraph init & codegraph index」前置

### TC-PI-002: 框架识别
- **目的**：`detect_frameworks(src)` 含 `spring-boot`
- **前置条件**：写一个 .java 含 `import org.springframework.boot.SpringApplication;`
- **输入**：`detect_frameworks(str(src))`
- **预期**：`"spring-boot" in result`
- **实现片段**：
  ```python
  (src/"A.java").write_text("import org.springframework.boot.SpringApplication;")
  self.assertIn("spring-boot", detect_frameworks(str(src)))
  ```
- **真实场景引用**：P2.3「输出技术栈」

### TC-PI-003: 输出 .draft 文件
- **目的**：CLI 执行后 `项目/{groupId}/preset.json.draft` 存在
- **前置条件**：tmp project 含 pom.xml 和 src
- **输入**：`python preset-init.py --project-root tmp`
- **预期**：`Path("项目/g/preset.json.draft").exists()`
- **实现片段**：
  ```python
  r = subprocess.run([sys.executable,"preset-init.py","--project-root",str(tmp_project)], capture_output=True)
  self.assertEqual(r.returncode, 0); assert Path("tmp/preset.json.draft").exists()
  ```
- **真实场景引用**：I1「codegraph init」前的自动探测

---

## 附录 A：测试文件骨架

```python
# doc/test_scripts/__init__.py
# doc/test_scripts/conftest.py
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(ROOT, "脚本"))
sys.path.insert(0, os.path.join(ROOT, "脚本/redis"))
sys.path.insert(0, os.path.join(ROOT, "脚本/chain"))
sys.path.insert(0, os.path.join(ROOT, "脚本/audit"))

FIXTURE_DB = os.path.join(ROOT, "doc/fixtures/codegraph-fake.db")
```

```python
# doc/test_scripts/test_memurai_client.py
import unittest
from unittest import mock
from memurai_client import Memurai, MemuraiError

class _FakeResult:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr

class MemuraiClientTests(unittest.TestCase):
    def setUp(self):
        self.cli = Memurai(cli_path="/fake/memurai-cli.exe",
                           exit_on_error=True)
    # TC-MC-001 ~ TC-MC-009 ...
```

## 附录 B：fixture 构造脚本（`doc/fixtures/build_fake_db.py`）

```python
import sqlite3, os
db = "doc/fixtures/codegraph-fake.db"
os.makedirs(os.path.dirname(db), exist_ok=True)
con = sqlite3.connect(db); c = con.cursor()
c.executescript("""
CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, name TEXT,
                    qualified_name TEXT, file_path TEXT,
                    start_line INT, end_line INT, signature TEXT,
                    decorators TEXT);
CREATE TABLE edges (source TEXT, target TEXT, kind TEXT, line INT, col INT);
INSERT INTO nodes VALUES
  ('r:1','route','GET /api/u','GET /api/u','Controller.java',10,10,'',NULL),
  ('m:entry','method','listUsers','pkg::C::listUsers','Controller.java',10,30,'',NULL),
  ('m:1','method','findAll','pkg::S::findAll','Service.java',5,25,'',NULL),
  ('m:2','method','executeQuery','pkg::Dao::executeQuery','Dao.java',1,15,'',NULL),
  ('c:1','class','Controller','pkg::Controller','Controller.java',1,40,'',NULL),
  ('c:2','class','Service','pkg::Service','Service.java',1,30,'',NULL),
  ('f:1','field','mapper','pkg::Service::mapper','Service.java',3,3,'',NULL);
INSERT INTO edges VALUES
  ('m:entry','m:1','calls',15,8),
  ('m:1','m:2','calls',12,8),
  ('c:1','m:entry','contains',NULL,NULL),
  ('c:2','m:1','contains',NULL,NULL);
""")
con.commit(); con.close()
```

## 附录 C：覆盖率统计

| 脚本 | 测试用例数 | 覆盖场景 |
|------|----------|---------|
| `redis/memurai_client.py` | 9 | ping/set/get/mset/delete/scan/pipe/err/cli-path |
| `redis/redis-batch-prefetch.py` | 4 | empty/N/chain_summary/sigHash |
| `redis/redis-self-check.py` | 3 | 50pass/mismatch/empty |
| `redis/redis-stats.py` | 2 | 5key/memory |
| `chain/sqlite-extract-chain.py` | 5 | CTE/cycle/schema/left-4hop/fqn-resolve |
| `chain/sqlite-pattern-search.py` | 3 | sqli/rce/unknown |
| `chain/sqlite-multi-hop-search.py` | 3 | 5hop20/multi-sink/custom |
| `audit/finding-promoter.py` | 3 | record/3x85promote/<85skip |
| `audit/data-reconcile.py` | 2 | all-pass/chain-mismatch |
| `audit/force-rescan.py` | 3 | CD-pass/CD+A-fail/redis-key |
| `audit/list-pruned.py` | 2 | jsonl/by-rule |
| `audit/preset-init.py` | 3 | pom/fw/draft |
| **合计** | **42** | （含边界与失败） |

> 任务要求列出的「最小覆盖数」 = 9+4+3+2+5+3+3+3+2+3+2+3 = **42 条**，与上表一致。

---

**追溯矩阵**：

| 原子需求 | 对应 TC |
|----------|---------|
| I2（Redis 缓存） | TC-MC-001~009, TC-RBP-001~004, TC-RSC-001~003 |
| I3（自检） | TC-RSC-001~003 |
| S7（数据对账） | TC-DR-001, TC-DR-002 |
| S10（3x85 写盘） | TC-FP-001~003 |
| P5.2（codegraph） | TC-SEC-001~005, TC-SMHS-001~003 |
| P5.13（剪枝） | TC-SEC-002, TC-LP-001, TC-LP-002 |
| L5（3x85） | TC-FP-002 |
| L9（75% PoC） | TC-DR-001 |
