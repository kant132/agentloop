# Draft: 调用链引擎 — 后续 plan(scripts-refactor 完成后启动)

## 背景

本 draft 是为 **scripts-refactor** 完成后的下一步工作记录的思路。不是本次重构的范围。

### 触发场景
- 用户提供了调用链 SQL 思考(分层 inner join)
- 用户提供了 `tools/java-method-call-extractor-1.0.0.jar` 工具,用于提取 method 内所有调用
- 用户在 scripts-refactor 期间决定: **hashkey 策略从 md5 改为 nodes.id**
- 但 attack_surface_scanner.py 暂保持 md5(它用 ast-grep 拿不到 nodes.id); 本次 draft 计划把攻击面扫描也迁移到 nodes.id 体系

## 工具现状调研

### 1. `tools/java-method-call-extractor-1.0.0.jar`

**用途**: 给定一个 Java 源文件,提取该文件中**每个 method 的所有方法调用**(带 FQN)。

**CLI**:
```bash
java -jar java-method-call-extractor-1.0.0.jar <file.java> [sourceRoot]
```
- sourceRoot: 可选,项目的源码根目录,用于跨文件 FQN 解析

**输出格式(JSON 数组)**:
```json
[
  {"startLine": 9,  "methodSignature": "public void addItem(String item)", "calledFQN": "java.util.List.add(item)"},
  {"startLine": 9,  "methodSignature": "public void addItem(String item)", "calledFQN": "java.io.PrintStream.println(\"Added: \" + item)"},
  {"startLine": 14, "methodSignature": "public int countItems()",           "calledFQN": "java.util.List.size()"},
  {"startLine": 18, "methodSignature": "public void processAll(...)",       "calledFQN": "com.example.SampleService.addItem(s.toUpperCase())"},
  {"startLine": 18, "methodSignature": "public void processAll(...)",       "calledFQN": "java.lang.String.toUpperCase()"}
]
```

**关键字段**:
- `startLine` (1-based): method 起始行号 — **用这个关联 codegraph nodes**
- `methodSignature`: method 签名(仅用于展示,关联不靠它)
- `calledFQN`: 该方法内的每个外部调用 FQN

**与 codegraph 关联**:
```sql
-- 在 codegraph SQLite 中反查 method 的 nodes.id
SELECT id, file_path, qualified_name, start_line, end_line
FROM nodes
WHERE file_path = :file
  AND start_line - 1 = :startLine  -- codegraph start_line 是 0-based, jar 是 1-based
  AND kind = 'method'
  AND language = 'java';
-- id 即可作为本 method 的 hashkey(notes.id 策略)
```

注: 用户明确**只用 startLine + 文件名关联即可**,method 名不需参与匹配(避免重载干扰)。

---

### 2. `脚本/chain/sqlite-extract-chain.py` (已存在, CTE 模式正确)

**已实现**分层递归查询,就是用户想要的"分层 inner join":
```sql
WITH RECURSIVE chain(...) AS (
    SELECT n.id, ... FROM nodes n WHERE n.id = :entry_id AND n.kind = 'method'
    UNION ALL
    SELECT callee.id, ... FROM chain c
    JOIN edges e ON e.source = c.id AND e.kind = 'calls'
    JOIN nodes callee ON callee.id = e.target AND callee.kind = 'method'
    WHERE c.depth < :max_depth
      AND instr(c.path, '|' || callee.id || '|') = 0  -- 防环
)
SELECT id, qualified_name, depth, file_path, start_line FROM chain ORDER BY depth
```

**保留不动**,只补 sink 提取。

---

### 3. `脚本/chain/sqlite-multi-hop-search.py` (已存在, **确认有问题**)

**L3 人工批注**: "这个实现有问题,应该是分层innerjoin"

现状: 一次性 LEFT JOIN 拉平 5 跳, 20 个 LEFT JOIN。
问题:
- LEFT JOIN 拉平会把"调用链"展平成多列,但丢失了"某跳没调用 = 该分支终止"的信息
- 无法处理不定深度
- SQL 模板膨胀,每加一跳加 4 个 JOIN

**计划**: 删除模板,统一到 sqlite-extract-chain.py 的 CTE 递归模式,加 sink 提取。

---

### 4. `tools/attack-surface-scanner/attack_surface_scanner.py` (即将在重构中迁入脚本/ast/)

现状: 用 ast-grep 扫 Java 注解,产出 (注解FQN, file, line), 用 md5 当 hashkey。
目标: 改用 codegraph nodes.id 当 hashkey,需要补一步反查:
```sql
-- 拿到 (file, line) 后,反查 nodes.id 作为 hashkey
SELECT id FROM nodes
WHERE file_path = :file
  AND start_line - 1 <= :annotation_line
  AND end_line >= :annotation_line
  AND kind IN ('method', 'class')
LIMIT 1;
```

---

## 总体工作流

### 主流程(端点驱动的调用链构建)

```
输入: 一个入口端点的 nodes.id (即 hashkey)

1. sqlite-extract-chain.py → 得到调用链上每个 method 的 nodes.id 清单
   (用 CTE RECURSIVE, 已实现, 不动)

2. 对每个 method id:
   a. 查 codegraph SQLite 拿 (file_path, start_line, end_line)
   b. 调 java-method-call-extractor-1.0.0.jar 获取该文件内所有 method 的 calls
   c. 用 (file, startLine) 匹配到本 method 的 calls 清单
   d. 从 calls 中过滤 calledFQN 不以 groupId 开头的 → **全部当作 sink**
      (简化策略: 80/20,先不考虑 sink 模式精准匹配,后续可加)

3. 对每个 sink FQN, 调 scanner_utils.inject_sink_comment(method_body_line, sink_fqn) 注入注释
   (scanner_utils.py 已在本次 plan Task 1 实现)

4. 把注入 sink 注释后的 method body 写 Memurai: {groupId}:method:{nodes.id}
   (Memurai SET+EX 86400, 走 pipe_setex_batch 高效)

5. 写 chains/{nodes.id}.json: {"groupId": ..., "chains": [[id1, id2, ...], [...]]}
   (每个子链是一个路径, 存的是 nodes.id 数组, 不存 hash)

输出: chains/{nodes.id}.json + Memurai {groupId}:method:* 缓存
```

### 关键关联算法 (file + startLine → method calls)

```python
def extract_method_calls_for_node(node_id, codegraph_db, groupId, jar_path):
    # 1. 拿 nodes 信息
    row = run_sql(codegraph_db, "SELECT file_path, start_line, end_line FROM nodes WHERE id=?", (node_id,))
    file_path, start_0, end_0 = row
    start_1 = start_0 + 1  # 转 jar 的 1-based

    # 2. 调 jar(对每个 unique file 只调一次,结果缓存在 file_calls_map[file])
    if file_path not in file_calls_map:
        file_calls_map[file_path] = subprocess.run(["java","-jar",jar_path,file_path]).parsed_json

    # 3. 过滤出本 method 的 calls
    method_calls = [r for r in file_calls_map[file_path]
                    if r["startLine"] == start_1]

    # 4. 筛 sink: calledFQN 不以 groupId 开头
    sinks = [r["calledFQN"] for r in method_calls
             if not r["calledFQN"].startswith(groupId + ".")]

    return sinks
```

---

## 关键设计决策(已记录, 待 plan 化)

| 决策 | 选择 | 理由 |
|------|------|------|
| hashkey 标识 | **nodes.id** | 与 codegraph 原生 id 一致,免去 md5 计算和碰撞风险 |
| 调用链查询方式 | **CTE RECURSIVE** (sqlite-extract-chain.py) | 已实现,支持任意深度,带环检测;弃用 LEFT JOIN 模板 |
| method calls 提取 | **java-method-call-extractor-1.0.0.jar** | JavaParser 全 FQN 解析,跨文件精准,非 ast-grep 模式匹配 |
| method ↔ calls 关联 | **(file + startLine) join**, 不依赖 method 名 | 避免 Java 重载干扰; startLine 1:1 映射 method(绝大多数情况) |
| sink 判定 | **calledFQN 不以 groupId 开头 → 全是 sink** | 80/20 简化,先跑通流程,后续可加 sink 模式表精准匹配 |
| sink 注释 | **`// sink: <FQN>`** 注入 method body 内调用行之前 | 设计文档 §8 inject_sink_comment 的规格;与 scanner_utils 一致 |
| Memurai 缓存 key | `{groupId}:method:{nodes.id}` | 与 nodes.id hashkey 体系一致 |
| chains 文件命名 | `chains/{nodes.id}.json` | hashkey 即文件名,无需额外映射 |
| attack-surface-scanner | **迁移到 nodes.id 体系** | 需要在 ast-grep 产出的 (file, line) 上加 codegraph 反查 nodes.id |

---

## 范围边界(本次 draft 不实施, 记录用途)

### 本次 draft (chain-sql-engine) 范围 (未来)
- ✏️ 新建 `脚本/chain/method_calls_extractor.py`(Python 封装 jar 调用 + file/startLine 关联)
- ✏️ 新建 `脚本/chain/chain_builder.py`(整合 sqlite-extract-chain + method_calls_extractor + sink 提取 + Memurai 写入 + chains JSON 输出)
- ✏️ 调整 `脚本/ast/attack_surface_scanner.py` 输出节点 id 而非 md5 hash(需要 codegraph 反查)
- ❌ 删除 `脚本/chain/sqlite-multi-hop-search.py`(LEFT JOIN 模板,已被 CTE 完全替代)
- ❌ 删除 `脚本/ast/scanner_utils.py` 中的 `node_hash_key`(直接用 nodes.id,不需要规范化函数)

### 本 draft 明确不做
- ❌ 不引入新数据库(继续用 SQLite + Memurai)
- ❌ 不实现 sink 模式精准匹配(用 80/20 全 sink 策略)
- ❌ 不跨语言(只 Java)

---

## 与 scripts-refactor 的依赖关系

```
scripts-refactor (当前)
├── 建立 scanner_utils.py (含 node_hash_key, inject_sink_comment, classify_route, validate)
├── 把 tools/attack-surface-scanner/*.py 迁入脚本/ast/(暂不改 hashkey 策略)
└── thrid-tool typo 合并到 tools/ → 修正所有引用
    │
    ▼ (scripts-refactor 完成后)
chain-sql-engine (本 draft)
├── 实现 method_calls_extractor.py (封装 java-method-call-extractor-1.0.0.jar)
├── 实现 chain_builder.py (CTE + call extractor + sink 提取 + Memurai + chains.json)
├── 改 attack_surface_scanner 输出 nodes.id (codegraph 反查)
├── 删除 sqlite-multi-hop-search.py
└── 删除 scanner_utils.node_hash_key (冗余)
```

---

## 待回答的开放问题(启动 chain-sql-engine plan 前需澄清)

1. **startLine 是否 100% 唯一**: 在同一文件内, 不同 method 的 startLine 是否一定不同? 如果有内部匿名类方法, startLine 可能等于外层 method 吗? — 需要实测验证
2. **codegraph 的 start_line 是 0-based 还是 1-based**: sqlite-extract-chain.py 代码显示是 0-based (SELECT start_line - 1 as start), 但 jar 输出是 1-based, 关联时需要 +1 或 -1 对齐
3. **Memurai 缓存过期策略**: 设计文档 §3 说 TTL 24h + 每轮启动 DEL groupId:*; 本 draft 沿用还是缩短?
4. **codegraph SQLite 是否支持并发读**: 多个 worker 同时 query 同一 codegraph.db 会不会锁? 要不要用 WAL 模式?
5. **大 method (body > 1MB) 处理**: inject_sink_comment 是单行处理的,对超长 body 是否需要流式或分段?

---

## 验证策略(待 plan 化后细化)

- **单元**: extract_method_calls(file_path, start_line) 对 fixture Java 文件输出正确 calls + 正确 sink 过滤
- **集成**: chain_builder 对一个小型 Java 项目产出 chains/{id}.json + Memurai key 正确
- **端到端**: 对一个 WebGoat 端点完整跑流程,人工抽检一个调用链的 sink 提取是否正确

---

## 下一步

等 scripts-refactor 完工(`/start-work` 跑完) → 回到本 draft, 用 `/writing-plans` 生成正式 plan。

---

## 用户提供的原始设计对话记录(对话原文保留)

### 对话1: SQL调用链思考(用户原文)

> 这是我对其中一些细节的思考,你看看和现状的对比一下 -- 第一层
> select n.id from edges e
>          inner join  nodes n on n.id = e.target
> where e.kind = 'calls' ;
>
> -- 第二层
>
> select n.id,n1.id from edges e
>          inner join edges e1 on e.target = e1.source and e1.kind = 'calls'
>          inner join  nodes n on n.id = e1.target
>          inner join  nodes n1 on n1.id = e1.target
> where e.kind = 'calls' and n.language = 'java';
>
> -- 通过id 找到对应的类的方法 , id后面统一做为hashkey;
> select id,file_path, qualified_name, start_line -1 as start, end_line -1 as end from nodes where id = 'xxxx' and kind = 'method' and language = 'java';

**AI解读**: 这是"分层inner join"思路的具体表达,体现的核心想法:
- 用分层 JOIN 而不是 CTE 递归
- nodes.id 作为 hashkey(后面统一做为hashkey)
- 通过 id 反查方法元信息

### 对话2: 澄清 - 实现思路而非全部(用户原文)

> 需要指定起点,我给你的是实现思路,不是全部,你再反思对比一下

**AI反思**(关键转折): 用户给的是实现思路,不是完整 SQL。AI 之前误解了这一点,需要重新抓本质:
- CTE RECURSIVE (`脚本/chain/sqlite-extract-chain.py` 第 53-75 行)**已经实现了**用户的"分层inner join"思路
- 不需要重写,只需补 sink 提取逻辑

### 对话3: 澄清 - 只用 startLine 即可(用户原文)

> 稍等一下,只使用start 行号,文件对应就可以了,$ java -jar target/java-method-call-extractor-1.0.0.jar test-data/SampleService.java test-data
> [
>   {"startLine": 9, "methodSignature": "public void addItem(String item)", "calledFQN": "java.util.List.add(item)"},
>   {"startLine": 9, "methodSignature": "public void addItem(String item)", "calledFQN": "java.io.PrintStream.println(\"Added: \" + item)"},
>   {"startLine": 14, "methodSignature": "public int countItems()", "calledFQN": "java.util.List.size()"},
>   {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "java.lang.String.length()"},
>   {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "com.example.SampleService.addItem(s.toUpperCase())"},
>   {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "java.lang.String.toUpperCase()"},
>   {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "java.io.PrintStream.println(\"Processed \" + input.size() + \" items\")"},
>   {"startLine": 18, "methodSignature": "public void processAll(List<String> input)", "calledFQN": "java.util.List.size()"},
>   {"startLine": 28, "meth…

**AI解读**:
- Method 关联只用 (file, startLine), method 名不参与匹配(避免 Java 重载干扰)
- startLine 在文件中唯一(绝大多数情况)
- startLine 是 1-based(CLI 输出), 而 codegraph `nodes.start_line` 是 0-based(需 +1 对齐)
- 完整 FQN 信息在 calledFQN 字段

### 对话4: Hashkey 策略选择(用户明确决策)

> **Plan Task 1 的 sig_hash 函数应该按照哪个策略实现?**
> "A: 改用 nodes.id (推荐)"

**决策影响**:
- `scanner_utils.py` 的 `sig_hash(fqn, params)` 改名为 `node_hash_key(node_id)` — 规范化 nodes.id 为字符串 key(无 md5)
- Memurai cache key: `{groupId}:method:{nodes.id}`
- Chains 文件: `chains/{nodes.id}.json`
- `attack_surface_scanner.py` 暂保持 md5(留待后续 plan 迁移)

### 对话5: Sink判定策略(用户在 README 中的思考)

> sink点全路径查询,通过这个,tools/java-method-call-extractor-1.0.0.jar ,参考,通过start 和文件名 ,method名字,三者对应进行关联,取到所有非groupId的方法调用作为sink点

**AI解读**(简化版):
- 默认所有"非 groupId 开头的 calledFQN"都是 sink
- 80/20 原则, 先跑通流程
- 后续可加 sink 模式表精准匹配

### 对话6: Method关联三要素(用户在对话中提及)

> 通过start 和文件名 ,method名字,三者对应进行关联

**AI解读**与澄清:
- 用户提到方法名字参与关联,后来明确**只用 startLine + 文件名即可**
- Method 名仅作显示用途
- 关联公式: `(file_path, start_line_1based) → jar_output → calledFQNs`

---

## 用户与 AI 的补充澄清(已达成共识)

### AI 反思1: CTE 就是你想要的

`脚本/chain/sqlite-extract-chain.py` 中的 CTE RECURSIVE(L53-75)本质上就是用户想要的"分层inner join" + 防环路径追踪。不需要重写,只需补 sink 提取逻辑。

### AI 反思2: nodes.id 是 hashkey 的真正载体

用户决策从 md5 改为 nodes.id,意味着:
- 原 `sig_hash` 函数签名错了(无法计算,因为 nodes.id 不是能算出来的)
- 应改名为 `node_hash_key(node_id)` 或直接删除
- 当前 plan 已按 `node_hash_key` 调整(Task 1 产出)

### AI 反思3: method body 获取链路补充

设计文档没说 body 文本怎么获得。用户 SQL 第三段补充了: `(file_path, start_line -1, end_line -1)` 从 nodes 表查出位置,再从源文件 slice 出方法体文本,然后用 `inject_sink_comment` 注入 sink 注释,最后写 Memurai。

### AI 反思4: tools/ 目录路径 bug

调研发现:
- ❌ `third-tool/` 目录**不存在**
- ✅ `thrid-tool/` (typo 命名)存在 — 含 javaparser/, arthas/, install.txt
- ✅ `tools/` (正确命名)存在 — 含 javaparser-service/, attack-surface-scanner/, java-method-call-extractor-1.0.0.jar, jar-analyzer

Plan 中已把所有 `third-tool/` 替换为正确的 `tools/`。

### AI 反思5: 用户恢复状态确认

用户说"设计文档被删了"和恢复相关讨论,最终确认:
- ✅ 设计文档/暴露面扫描设计.md (已恢复 309 行)
- ✅ 设计文档/chain-sql-engine.md (本文件,记录对话)
- ✅ 脚本/ast/scanner_utils.py (已实现 123 行)
- ⚠️ 部分源文件丢失(scripts/, test_scripts/),无法通过 git 恢复(项目非 git repo)

---

## 文件完整性签名

- 创建时间: scripts-refactor plan 执行期间
- 最后更新: 2026 年对话澄清之后
- 内容溯源: 用户对话原文 + AI 反思与共识
- 用途: 后续 chain-sql-engine plan化 时作为设计依据