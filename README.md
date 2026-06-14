可以注册为本项目的hook
ai 所有改动，都要以需求为维度，agentloop\需求分析\ai改动日志.md

本文件为项目的根本

本项目的根本目标
1、代码审计输出漏洞准确已被验证，是/非漏洞
2、外部接口无遗漏
3、接口调用链分析无遗漏
4、运行高效，消耗token少
5、工具自进化，越用越智能，越用越快。

准则
所有目标都是为了发现漏洞，其他任何对目标没有帮助的内容，都需要删除。
每轮agentloop 清空上一轮的缓存groupId开头的都清除了。
只有主agent可以修改执行路径，修改agentloop工具的流程，增加脚本等，所有修改操作，主agent单独委派一个工具子agent去实现，所有修改不能过拟合，然后告诉子agen怎么使用。
目前设计了几个agent：
主agent（老板）：负责评价过程和结果并进行反思；可以拒绝主管提交的结果，指出他的不足、告诉他反思修改后继续执行、老板也需要反思自己、形成经验，做一个好老板、需求明确、验收标准明确、[java-whitebox-loop](skills/java-whitebox-loop)
每一个接口启动（
子-决策agent(主管)，：负责决策，评价，协调，什么时候该调用什么agent，基于分析师的结果，决定需要哪些挖洞专家深入参与分析，基于挖洞专家（如有），决定是否需要验证专家参与。
专家在执行过程中，如发现还需其他专家参与，需要通过主管协调，并反馈主管，为什么需要；主管进行决策后发现确实需要，主管这时候需要反思并记录自己之前为了漏了，形成具有普遍性的经验。后续主管要读这些经验，自我修正。
决策路径明确，协调分工无差错，能对专家的结果进行判断，需要模拟Chris Anley、吴翰清、尹毅 等角度，对专家的输出提出质疑。结果满意后，更新相关缓存中状态，最后向老板汇报。
子-调用链分析agent（分析师）： [call-chain-audit-thinking](skills/call-chain-audit-thinking)
子-注入类安全问题挖掘agent（挖洞专家）、  [injection-audit](skills/injection-audit)
子-业务逻辑类安全问题挖掘agent（挖洞专家）、 [business-logic-audit](skills/business-logic-audit)
子-文件安全问题类挖掘agent（挖洞专家）、[file-audit](skills/file-audit)
子-认证鉴权类agent（挖洞专家）、[auth-chain-audit](skills/auth-chain-audit)、[login-audit](skills/login-audit)
子-poc验证agent（验证专家）、[poc-verify](skills/poc-verify)
）


难点：
如何准确表达需求，拆分原子需求
如何让agent准确无误执行本工具的指令：执行路径，执行过程，执行结果，执行效率，无幻觉
如何做工程优化：剪枝、Memurai缓存
如何工具自进化：每次代码审计结束主agent进行反思，输出工具改进建议；沉淀项目特有知识，特殊经验等，指导下次审计
工具迭代如何保证逻辑的一致性，如何保证工具迭代能力不会退化，不会过拟合某个项目

思维角度：
大版本改动前读取本文档，并启动三方评审skill


输出模板约束

所有产出写入目标项目的 `loop_audit/`（相对项目根目录）。

```
loop_audit/
 ├── project-context.json      ← Phase A：技术栈、依赖、端点总表
 ├── security-context.json     ← Phase B：Filter链、威胁模型、配置风险
 ├── findings/{chainId}.json   ← 机器可读 finding（评分后落盘）
 ├── routes/
 │    ├── 高风险端点/*.md      ← 按端点的审计报告
 │    ├── 中低险端点/*.md
 │    └── poc/*.md             ← PoC 验证报告
 ├── reports/
 │    ├── summary.md           ← 1 页执行摘要（给人类看）
 │    ├── api-audit/*.md       ← 按 API 方法展开
 │    └── vuln-report/*.md     ← 按 finding 展开（含 trust_type + exploitability）
 ├── diag/                     ← 过程遥测（机器用，不进报告）
 │    ├── findings.jsonl
 │    ├── scoring-history.jsonl
 │    ├── pruning-log.jsonl
 │    └── false-positive-samples.jsonl
 ├── needs_human/              ← 不可达/需人工确认的端点
 └── knowledge.json            ← 跨轮次知识沉淀
```

模板源文件在 `agentloop/loop_audit/_template/`。

文件命名：
- 端点报告：`{severity}_{fqn}_{method}_{sigHash}.md`（Windows 兼容，点号→`__`）
- PoC：`{验证状态}_{问题等级}_{fqn.端点method-sink点-roundNNN}.md`
- 验证状态枚举：是问题 / 非问题 / 暂时无法确认

不变量：端点报告总数 == `project-context.json` 中 endpoints 总数（不等则 loop 不可终止）。



# 原子需求拆解

每条原子需求是系统必须具备的不可再拆能力，去掉任何一条，系统都有真实缺陷。

流程：
1. Phase 1 只做端口扫描、Filter 等安全关键文件识别，不做文档阅读和威胁建模（人工后续执行）。预置框架 skill（Spring/SOAP 等），agent 根据项目技术栈自动加载对应路由识别模式。调用链查询结果需缓存。

2. 安全上下文合并、缓存。Filter 链、消毒器清单等在进入漏洞分析前一次性加载，后续不重复查询。

3. 漏洞发现与 PoC 验证解耦。5 个 FWD subagent 合并为 1 个（用 call-chain-audit-thinking skill 引导思考）。PoC 由后台 monitor 进程从缓存取 finished 调用链后调度 2 个专门 PoC agent 执行，状态回写缓存。

4. 结构化自检替代三哲学。每轮结束时按 if-then-else 清单自检，输出可验证 JSON 报告。

5. 假阳率量化度量。每轮抽样标注，跟踪趋势。
判定：
6. 删除。原 trust_type 需求不保留。

7. 调用链方法标注。链上每个方法标注业务意义、分支逻辑、参数传递影响，可缓存复用供下轮直接加载。

8. 配置风险通过 SSH 在实际环境检查，不从代码推断。

9. 硬编码凭证仅在影响 CIA 时检查（如密码泄露可直接利用），不做纯合规类扫描。

沉淀：
10. 项目知识跨轮次持久化。自定义注解、已知消毒器、动态路由模式、历史 finding 模式，每轮自动加载和更新。

11. 评分历史持久化，可计算方差和趋势。

12. 收敛标准：连续 N 轮方差<5 且均分>85 且对账全过。

13. 有效优化持久化到经验库，不随轮次丢失。

密度：
14. 主 SKILL ≤50 行，只写 AI 不知道的信息。

15. rules ≤3 个文件，启动时一次读完。

工具链：
16. 初始化后可预置脚本和知识，但工具核心价值在于使用中自己生成脚本和知识。项目特有知识需明确标注，避免项目间污染。

17. 三个核心工具（codegraph/ast-grep/Memurai）任一不可用则直接退出，不做降级。

18. 每个步骤明确指定使用哪个模板，不由 agent 自行选择。

端点遍历优化：
19. 端点枚举排序：跳过无入参或参数类型仅为数字的端口。优先级：存在高危 sink 的调用链（需要一个sink表（大而全），grep一下，如果能grep到就需要进行参数追踪，如果没有，就可以只进行业务逻辑漏洞审计） > POST > UPDATE > DELETE > GET。 外部接口（通过ssh到环境找到所有外部可访问的api的前缀） > 内部接口。
共 19 条（1 条删除，实际 18 条有效需求）。


新增需求：
[java-forward-vuln-discovery](skills/java-forward-vuln-discovery) ，把这个转换为python脚本。
