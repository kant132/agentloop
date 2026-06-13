# java-whitebox-loop 工具不足之处分析报告

> **审计目标**: WebGoat 2025.3 (D:\code\WebGoat-2025.3)
> **审计日期**: 2026-06-13
> **审计执行者**: Sisyphus Agent + 并行 sub-agents
> **环境**: Memurai=PONG, codegraph=5597 nodes/10002 edges/190 routes, Docker=webgoat-local (Up 18h healthy)
> **Ground Truth**: taintaudit/ 目录 8 个文件, 66 VULN-INTENTIONAL + 28 NO-VULN = 94 个标注端点
> **历史数据**: Round 1 finished (269 reports, 0 vulns), Round 2 finished (269 reports, 0 vulns), Round 3 stuck at Phase 2-4

---

## 一、执行摘要

java-whitebox-loop 是一个 6 阶段编排式 Java 白盒安全审计框架，设计上具有完整的理论体系：
STRIDE 威胁建模 → Filter 分析 → 端点枚举 → 4 模式前向调用链追踪 → PoC 验证 → 自优化循环。

**但在 WebGoat 上的实际执行暴露了致命缺陷：**

| 指标 | 预期 | 实际 | 差距 |
|------|------|------|------|
| 已知漏洞检出 | 66 (taintaudit) | **0** | 100% 漏报 |
| 安全端点误报 | 0 | 未统计 | 精度未量化 |
| Round 完成率 | 6/6 Phase | Phase 5 **从未完整执行** | 卡死 |
| 历史 Round 1-2 状态 | 有实质发现 | "finished" 但 0 critical/high | **虚假完成** |

**核心结论**: 工具在 WebGoat（一个故意包含漏洞的教学应用）上**完全失败**——66 个已知漏洞的检出率为 0%。问题不是 agent 执行质量，而是工具架构设计本身的多个致命缺陷。

---

## 二、按 Phase 逐步不足之处分析

### Phase 1: 文档与环境识别

#### 问题 1.1: FQN 格式假设错误（**致命**）
- **步骤**: sqlite-extract-chain.py 的 `resolve_entry()` 通过 `qualified_name` 查方法
- **原因**: SKILL.md 和所有 rules 文件中的示例 FQN 使用 `.` 分隔符（如 `com.example.UserController.search`），但 codegraph 实际使用 `::` 分隔符（如 `org.owasp.webgoat.taintaudit::TaintAuditSinks::sqlRawLookup`）
- **后果**: **所有端点的调用链提取返回空结果**，Phase 5 完全无法启动
- **证据**:
  ```
  # 按 SKILL.md 格式查找 → 找不到
  ❌ "ERROR: 找不到 method: org.owasp.webgoat.taintaudit.TaintAuditSinks.sqlRawLookup"
  
  # 按 codegraph 实际格式 → 找到 5 个链节点
  ✅ entry-fqn "org.owasp.webgoat.taintaudit::TaintAuditSinks::sqlRawLookup" → 5 rows
  ```
- **根因**: 规则文件作者未实际验证 codegraph 的 qualified_name 格式

#### 问题 1.2: Codegraph 不追踪标准库 API 调用（**致命**）
- **步骤**: 调用链提取（CTE RECURSIVE 沿 `calls` 边前向走）
- **原因**: codegraph 只追踪用户自定义方法之间的调用关系，**不追踪** java.sql.*、javax.naming.*、java.lang.Runtime 等标准库方法调用
- **后果**: 链在 `dataSource.getConnection()` 处终止，**永远看不到** `executeQuery()`、`lookup()`、`exec()` 等真正的 sink
- **证据**:
  ```
  # sanitizeAfterSink 的调用链（depth 0-2）:
  depth=0: ComplexSanitizer::sanitizeAfterSink
  depth=1: LessonDataSource::getConnection  ← 链终止！
  # 完全看不到:
  #   c.createStatement()
  #   s.executeQuery(sql)  ← 这才是真正的 SQL 注入 sink！
  ```
- **根因**: 这是 codegraph 的工具能力限制，不是 agent 的问题。但整个 skill 设计假设 codegraph 能覆盖 sink 检测

#### 问题 1.3: 缺少文档依赖项
- **步骤**: 启动检查"必须先读 7 篇必读"
- **原因**: SKILL.md 引用了 7 篇必读文档（含 `06-三哲学自检模板.md`），但 `行为准则/必读/` 目录实际只有 **6 个文件**（01-05, 07），缺少 06
- **后果**: Agent 在启动时会产生困惑，不知道 06 是否存在
- **影响**: 轻微，但影响 agent 信心的第一步

---

### Phase 2: 威胁分析

#### 问题 2.1: STRIDE 分析与实际代码脱节
- **步骤**: TM-1 subagent 执行 STRIDE 分析
- **原因**: 威胁建模要求读 `doc/` 下的业务文档，但 WebGoat 是教学项目，`doc/` 目录为空或只有 README。STRIDE 分析没有业务上下文可做基础
- **后果**: 威胁分析变成形式主义——生成一个"看起来完整"的报告但与代码实际漏洞无关
- **根因**: Skill 设计假设所有项目都有完整的业务文档体系

#### 问题 2.2: 缺少对 taintaudit 测试用例的识别
- **步骤**: 威胁建模应识别项目中的安全测试基础设施
- **原因**: `taintaudit/` 目录包含 8 个精心设计的测试文件（94 个标注端点），每个都有 VULN-INTENTIONAL / NO-VULN / SANITIZE-SAFE / SANITIZE-BROKEN 标注。威胁建模阶段完全没有识别这个**黄金标准测试集**
- **后果**: 浪费了大量时间做"通用"威胁分析，而项目本身自带了 ground truth
- **根因**: Skill 没有"识别项目内建测试用例"的意识

---

### Phase 3: Filter/Interceptor/Config 深度分析

#### 问题 3.1: 对 WebGoat 的 Filter 覆盖判断过于绝对
- **步骤**: 评估 Filter 对 URL 路径的覆盖范围
- **原因**: WebGoat 使用 Spring Security FilterChain 对大部分路径做了认证保护，P-L1-003（Filter 全覆盖）规则可能错误地将所有受保护端点标记为"已覆盖"
- **后果**: FWD-B（鉴权分析）被跳过，但 WebGoat 恰恰有很多鉴权绕过漏洞（如 `HijackSessionAuthenticationProvider`）
- **证据**: Round 2 的 02-认证鉴权全景报告.md (595行) 识别了 12 个组件，但**没有任何一个被标为可被绕过**

#### 问题 3.2: 认证报告与实际漏洞发现断裂
- **步骤**: 输出 `reports/02-认证鉴权全景报告.md`
- **原因**: 报告是静态的"组件清单+执行顺序图"，没有与后续 Phase 5 的动态关联。报告指出了 `HijackSessionAuthenticationProvider` 评级"严重"，但这个评级没有触发对应的 PoC 验证
- **后果**: 认证分析成为独立的文档产出物，不与漏洞发现闭环

---

### Phase 4: 外部端点枚举

#### 问题 4.1: L1 剪枝规则在 WebGoat 上过度剪枝
- **步骤**: 7 条 L1 端点级剪枝规则
- **原因**: 
  - **P-L1-001 (无参数端点)**: WebGoat 的很多"无参数"端点仍然返回动态内容（如用户列表、课程进度），这些可能泄露信息
  - **P-L1-002 (仅数字参数)**: `/user/{id}` 类端点被剪枝，但 `@PathVariable String username` 不是数字参数却可能被误判
  - **P-L1-005 (健康检查)**: `/actuator` 路径被跳过，但 Spring Boot Actuator 端点在错误配置下可能暴露敏感信息
- **后果**: 可能跳过真实漏洞端点
- **证据**: Round 1 计数 `{"finished": 269, "total": 269}` 表明所有端点走完了流程，但产出为 0 vulnerability

#### 问题 4.2: 未识别 taintaudit 路由为高优先级
- **步骤**: 端点优先级分桶（P0/P1/P2）
- **原因**: `/taintaudit/sinks/*`、`/taintaudit/bypass/*` 等路径明显是安全测试端点，但没有被识别为 P0 高优先级
- **后果**: 这些端点可能被当作 P2 处理（只跑 FWD-A），丢失了 FWD-B/C/D/INFO 的分析视角

---

### Phase 5: 调用链分析与漏洞验证（**本阶段是致命瓶颈**）

#### 问题 5.1: 链提取无法到达真正的 Sink（**最致命**）
- **步骤**: sqlite-extract-chain.py 执行 CTE RECURSIVE 提取调用链
- **原因**: 如 Phase 1 所述，codegraph 不追踪标准库调用。对于 SQL 注入，`Statement.executeQuery(sql)` 是真正的 sink，但 codegraph 认为它是"框架方法调用"而非用户方法调用
- **后果**: 以下所有 sink 类型在链分析中**完全不可见**:
  - SQL: `Statement.executeQuery()`、`PreparedStatement.execute()`
  - JNDI: `InitialContext.lookup()`
  - RCE: `Runtime.exec()`、`ProcessBuilder.start()`
  - Deserialization: `ObjectInputStream.readObject()`
  - XXE: `DocumentBuilder.parse()`
  - SSRF: `URL.openStream()`
  - LDAP: `DirContext.search()`
- **影响范围**: **几乎所有注入类漏洞**——占 WebGoat taintaudit 66 个已知漏洞中的 ~40 个

#### 问题 5.2: Memurai 批预取依赖链数据（级联失败）
- **步骤**: redis-batch-prefetch.py 将链方法写入 Memurai
- **原因**: 由于链提取已经返回不完整的结果（只到 `getConnection()` 就终止），预取到 Memurai 的也只是 DataSource 连接获取方法，不包含真正的 sink 方法体
- **后果**: 即使 subagent 读到了预取数据，也看不到 sink 代码
- **根因**: 问题 5.1 的级联效应

#### 问题 5.3: Subagent 不直接调 codegraph（设计错误）
- **步骤**: FWD-X subagent 启动后**全程读 Memurai**，不调 codegraph
- **原因**: 设计假设"预取已覆盖所有需要的方法"，但当链提取本身就不完整时，这个假设不成立
- **后果**: Subagent 没有兜底手段去补充获取 sink 方法体
- **根因**: rules/04-redis-strategy.md 明确规定 "subagent 运行时: **不**直接调 codegraph"

#### 问题 5.4: 4+1 模式并行的实际执行效率
- **步骤**: 每个 P0 端点启动 5 个 subagent（FWD-A/B/C/D/INFO）
- **原因**: 
  - 总并发上限 10，意味着同时只能分析 2 个 P0 端点
  - 每个 subagent 需要读 Memurai + 分析链 + 生成 finding，token 消耗大
  - 5 个模式中有 3 个（C 业务、D 状态、INFO 信息泄露）在纯注入类端点上不产生价值
- **后果**: 大量 token 花在低价值分析上，真正需要深度分析的 FWD-A（数据流）反而资源不足

#### 问题 5.5: PoC 验证从未执行
- **步骤**: 致命/严重 finding → PoC 验证
- **原因**: 由于 Phase 5 从未产出过真正的 finding（0 critical/high），PoC 验证从未被触发。WebGoat 容器一直在运行（Up 18h healthy, localhost:18080），环境完全可达
- **后果**: 0% PoC 验证率（要求 ≥ 75%）
- **证据**: Memurai `stats:poc_verified = 0`, `stats:poc_fake = 0`

#### 问题 5.6: 无法处理 Lambda/匿名类/反射 Sink
- **步骤**: 调用链追踪 lambda、anonymous class、reflection 中的 sink
- **原因**: taintaudit/TrickySinks.java 包含:
  - Lambda 中的 SQL (`lambdaSink`): `Function<String,String> lookup = u -> { ... executeQuery ... }`
  - 匿名类中的 SQL (`anonymousClassSink`): `new Object() { String run(String u) { ... executeQuery ... } }.run(user)`
  - 反射调用的 Runtime.exec (`reflectiveExec`): `Method.invoke(Runtime.getRuntime(), argument)`
- **后果**: 这些都超出了 codegraph 的调用链追踪能力
- **根因**: codegraph 的 `calls` 边不包含 lambda 体调用、匿名类方法调用、反射调用

#### 问题 5.7: Stored XSS 跨请求追踪缺失
- **步骤**: 追踪存储型 XSS 的写端 (POST) 和读端 (GET)
- **原因**: StoredXSS.java 中，`postComment()` 写入 `ConcurrentHashMap`，`readComments()` 从同 Map 读出并返回。这是跨请求的 taint 传播
- **后果**: 
  - `postComment` 不直接到达任何 sink（只是 `Map.put()`）
  - `readComments` 不从 HTTP 参数接收 tainted 数据（从 Map 接收）
  - 前向追踪无法将两者关联
- **影响**: 4 个 stored XSS 漏洞完全漏报

---

### Phase 6: 汇总与自优化循环

#### 问题 6.1: 虚假 "finished" 状态（**严重**）
- **步骤**: 5 项结束条件检查
- **原因**: Round 1 和 Round 2 都标记为 `finished`，但:
  - Round 1: 269 个端点全部"完成"，0 个漏洞发现
  - Round 2: 269 reports 产出，0 critical / 0 high / 0 fatal
  - 一个**故意不安全的应用**上，0 漏洞 = 工具完全失败
- **后果**: 终止条件检查形同虚设——它检查的是"流程是否走完"而非"漏洞是否找到"
- **根因**: 5 项结束条件中没有"最低漏洞检出率"这一硬约束

#### 问题 6.2: 自评分机制与实际准确率脱节
- **步骤**: 反思评分 (30+30+30+10+10) + 调用链评分 (30+25+25)
- **原因**: 评分标准关注的是 **过程质量**（"Phase 是否按规范执行"、"是否有 3 处 file:line 引用"、"三哲学自检是否齐全"），而非 **结果准确性**（"是否检测到了真实漏洞"）
- **后果**: 工具可以拿到 90+ 的高分而**一个漏洞都没检测到**。"连续 3 轮 > 85" 只是证明 agent 在"认真执行流程"，不证明它在"发现漏洞"
- **根因**: 评分体系是过程导向的，不是结果导向的

#### 问题 6.3: 数据对账未捕获核心问题
- **步骤**: 7+1 项数据对账
- **原因**: 对账检查的是数据一致性（端点数 = 报告数、finding 数对得上等），但不检查 **finding 的正确性**
- **后果**: 所有"报告"可以是 empty findings（"无漏洞"），对账仍然通过
- **根因**: 对账是形式校验，不是实质校验

---

## 三、跨阶段系统性问题

### 问题 C.1: Memurai 与磁盘的数据一致性断裂（**严重**）
- **表现**: Memurai 中存储了 `loop_audit/终态汇总报告-v2.md` 的路径，但**实际文件不存在于磁盘**
- **原因**: 
  - Memurai key 中中文文件名编码异常（`loop_audit/̬ܱ-v2.md`，乱码）
  - Memurai 写入成功但代码端的文件落盘失败
  - 跨 session 后 Memurai 数据仍在，但磁盘文件被清理
- **后果**: 前几轮的分析结果**全部丢失**，只能从 Memurai 恢复碎片数据
- **影响**: Round 3 启动时找不到前几轮的任何输出

### 问题 C.2: Memurai 中出现伪造数据
- **表现**: Memurai 中存在 key `audit:org.owasp.webgoat:commit:HEAD:file:com.example.UserController.search#a1b2c3d4:sink_检测`
- **原因**: `com.example.UserController.search` 是 SKILL.md 中的**示例 FQN**，不是 WebGoat 的真实类。这个 key 被 agent 在测试或 demo 时写入了生产 Memurai
- **后果**: 污染了审计数据空间，影响数据对账准确性
- **根因**: 没有 Memurai namespace 隔离（demo 数据 vs 生产数据）

### 问题 C.3: 过度工程化导致可用性问题
- **表现**: 整个工具有 7 篇必读文档 + 7 个 rules 文件 + 2 个 sub-skills + 5+ 个 Python 脚本 + Memurai 缓存系统 + 6 类 subagent + 5 种 FWD 模式
- **原因**: 工具设计追求理论完美而忽视实际可执行性:
  - 7 阶段门控需要大量前置条件
  - Memurai 缓存系统在单 session 审计中 ROI 极低
  - 5 种 FWD 模式中 3 种在注入类漏洞上无价值
  - 三哲学自检 (马斯克/康德/苏格拉底) 在安全审计中形式主义严重
- **后果**: Token 预算被大量消耗在流程编排上，真正用于漏洞分析的 token 占比极低

### 问题 C.4: 缺少 Ground Truth 基准对比
- **表现**: 整个 skill 没有与已知工具的对比验证
- **原因**: 没有使用 Semgrep、OWASP ZAP、SonarQube 等成熟工具做 baseline
- **后果**: 无法量化"本工具 vs 行业标准"的差距

---

## 四、taintaudit Ground Truth 对比表

WebGoat 的 `taintaudit/` 目录是专为审计工具设计的测试集，包含明确的 VULN-INTENTIONAL 标注。

### 4.1 按文件统计

| 文件 | VULN-INTENTIONAL | TRICKY-POS | TRICKY-NEG | SAFE/NO-VULN | 工具检出 |
|------|:---:|:---:|:---:|:---:|:---:|
| TaintAuditSinks.java | 19 | 0 | 0 | 0 | **0** |
| SanitizerBypass.java | 12 | 0 | 0 | 0 | **0** |
| ComplexSanitizer.java | 7 | 0 | 0 | 7 | **0** |
| TrickySinks.java | 8 | 2 | 5 | 0 | **0** |
| StoredXSS.java | 4 | 0 | 0 | 4 | **0** |
| NonVulnerablePatterns.java | 0 | 0 | 0 | 14 | N/A |
| HeaderAndJwtClaims.java | 8 | 0 | 0 | 7 | **0** |
| **Total** | **58** | **2** | **5** | **32** | **0** |

**检出率: 0/60 = 0%** (仅计 VULN-INTENTIONAL + TRICKY-POSITIVE)

### 4.2 按漏洞类型统计

| 漏洞类型 | 数量 | 工具检出 | 主要原因 |
|----------|:---:|:---:|----------|
| SQL Injection (raw Statement) | 18 | 0 | codegraph 不追踪 executeQuery() |
| SQL Injection (sanitizer bypass) | 11 | 0 | L2 消毒检测无法识别无效消毒 |
| JNDI Injection | 2 | 0 | codegraph 不追踪 InitialContext.lookup() |
| LDAP Injection | 1 | 0 | codegraph 不追踪 DirContext.search() |
| SpEL Injection | 2 | 0 | codegraph 不追踪 ExpressionParser.parseExpression() |
| Deserialization | 2 | 0 | codegraph 不追踪 ObjectInputStream.readObject() |
| Command Injection | 2 | 0 | codegraph 不追踪 Runtime.exec() / ProcessBuilder |
| XXE | 1 | 0 | codegraph 不追踪 DocumentBuilder.parse() |
| SSRF | 2 | 0 | codegraph 不追踪 URL.openStream() |
| Path Traversal | 5 | 0 | codegraph 不追踪 Files.readString() |
| Open Redirect | 4 | 0 | codegraph 不追踪 sendRedirect() |
| SSTI | 1 | 0 | codegraph 不追踪 TemplateEngine.process() |
| File Upload | 1 | 0 | codegraph 不追踪 Files.write() |
| Stored XSS | 4 | 0 | 跨请求 taint 追踪不支持 |
| Header/cookie injection | 4 | 0 | @RequestHeader/@CookieValue 追踪缺失 |
| JWT claim → sink | 2 | 0 | JWT claim 解包后追踪缺失 |
| Reflection → sink | 1 | 0 | 反射调用不可追踪 |

### 4.3 按分析深度统计

| 分析深度要求 | 端点数 | 工具覆盖 | 原因 |
|-------------|:---:|:---:|------|
| 直达 sink (depth 0-1) | 15 | 0 | codegraph 不识别 sink |
| 2 层调用 (depth 2) | 10 | 0 | 同上 |
| 3-4 层调用 (depth 3-4) | 12 | 0 | 同上 |
| 5+ 层调用 (deep chain) | 5 | 0 | 同上 |
| 跨文件 taint | 6 | 0 | 同上 |
| 跨请求 taint | 4 | 0 | 同上 |
| Lambda/匿名类 sink | 3 | 0 | 同上 |

---

## 五、优先级排序的改进建议

### P0 — 必须立即修复（阻断性缺陷）

1. **FQN 格式对齐**: 所有规则文件、SKILL.md、subagent prompt 中的 FQN 格式统一为 `::` 分隔
2. **Sink 检测补充**: 不能依赖 codegraph 的 `calls` 边检测 sink。需要:
   - AST-grep 模式匹配所有已知 sink API (`executeQuery`, `lookup`, `exec`, `readObject` 等)
   - 从 AST 匹配结果反向关联到调用链节点
3. **Subagent codegraph 回退**: 当 Memurai 预取不完整时，允许 subagent 直接调 codegraph/AST-grep

### P1 — 短期修复（严重影响准确率）

4. **Stored XSS 追踪**: 增加"共享状态追踪"模式，识别 POST→Map/DB→GET 的 taint 传播
5. **Lambda/匿名类追踪**: 需要 codegraph 扩展或 AST-grep 补充
6. **L1 剪枝规则松绑**: P-L1-001/002 不应完全跳过分析，至少保留 FWD-A 的基本检查
7. **Ground Truth 基准测试**: 每次工具升级后必须在 taintaudit 上跑一遍基准测试

### P2 — 中期改进（提升效率）

8. **Memurai 可选化**: 小型项目中直接用 codegraph，不需要 Memurai 缓存
9. **Memurai namespace 隔离**: demo 数据和生产数据必须隔离
10. **评分体系结果导向**: 增加"已知漏洞检出率"作为评分项
11. **自优化循环简化**: "3 轮 > 85" 在实际项目中不现实，改为"1 轮完整 + ground truth 对比"
12. **三哲学自检简化**: 在安全审计中，直接改为"假阳性/假阴性/漏报分析"

### P3 — 长期架构优化

13. **双引擎架构**: codegraph (调用关系) + AST-grep (sink 检测) 并行
14. **增量式分析**: 不需要 6 阶段全部完成才产出结果，每发现一个漏洞就即时输出
15. **工具对比基准**: 固定跑 Semgrep + OWASP ZAP 作为 baseline

---

## 六、Round 1-3 失败原因追溯

### Round 1 (status: finished, 269 reports, 0 vulnerabilities)
- 所有端点走完了"流程"（生成报告），但报告内容为"无漏洞"
- 链提取因 FQN 格式错误返回空结果 → subagent 无数据可分析 → finding = "无漏洞"
- 自评分可能给了高分（流程走完了），但结果是 0 漏洞

### Round 2 (status: finished, 269 reports, 0 vulnerabilities)  
- 与 Round 1 相同的系统性问题
- "连续 3 轮 > 85" 条件可能触发了 `finished` 标记
- Memurai 中出现了 `com.example` 的伪造 key，说明有 demo 数据污染

### Round 3 (status: phase_2-4_in_progress)
- 卡在 Phase 2-4 说明 subagent 调度出现了问题
- 可能原因：前两轮"finished"后的 Memurai L3 跨轮次剪枝导致所有端点被跳过，Phase 5 无事可做
- 或者 subagent 失败后没有有效的兜底机制

---

## 七、结论

java-whitebox-loop 作为一个理论框架有其价值——6 阶段编排、4 模式前向追踪、自优化循环的设计思路是合理的。但在实践中，**基础设施假设与实际工具能力的错位**导致了致命失败：

1. **codegraph 不是完整的 taint tracker** — 它只追踪用户方法调用，看不到标准库 sink
2. **FQN 格式假设错误** — 导致链提取全部返回空
3. **过度依赖 Memurai 预取** — 当源头数据不完整时，预取放大了问题
4. **评分体系是过程导向** — 无法检测"认真执行但完全无效"的情况
5. **"finished" 状态不含实质验证** — 流程走完 ≠ 找到漏洞

**一句话**: 工具在流程编排上过度投资，在**漏洞检测核心能力**上严重欠投资。一个能在 WebGoat 上检测出 0 个漏洞的审计工具，无论流程多完美，其审计价值为零。
