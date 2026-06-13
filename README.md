本文件为项目的根本
本项目的根本目标
1、代码审计输出漏洞准确已被验证，是/非漏洞
2、外部接口无遗漏
3、接口调用链分析无遗漏
4、运行高效，消耗token少
5、工具自进化，越用越智能，越用越快。

准则
所有目标都是为了发现漏洞，其他任何对目标没有帮助的内容，都需要删除。

难点：
如何准确表达需求，拆分原子需求
如何让agent准确无误执行本工具的指令：执行路径，执行过程，执行结果，执行效率，无幻觉
如何做工程优化：剪枝、Memurai缓存
如何工具自进化：每次代码审计结束主agent进行反思，输出工具改进建议；沉淀项目特有知识，特殊经验等，指导下次审计
工具迭代如何保证逻辑的一致性，如何保证工具迭代能力不会退化，不会过拟合某个项目

思维角度：
大版本改动前读取本文档，并启动三方评审skill


## 输出模板约束

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


原子需求拆解（一句话描述）

> 详见 `需求分析/RFC-0001-四位阶段重构需求分解.md`

**A. 流程重构 6阶段→4阶段**（P0）
- A001 主 SKILL 改为 Phase A/B/C/D 四阶段
- A002 Phase A = 原 P1+P4+P2技术栈：一次理解项目
- A003 Phase B = 原 P3+P2威胁 + 配置安全检查
- A004 Phase C = 原 P5：漏洞发现核心
- A005 Phase D = 原 P6：汇总 + 自检 + 知识沉淀
- A006 重写 rules/01-phase-gates 四阶段门控

**B. FWD 合并 5→1**（P0）
- B001 5 个 FWD subagent 合并为 1 个：一次遍历 + D1-D4 多维判定
- B002 删除 5 个 FWD prompt 模板，新建统一模板
- B003 重写 FWD SKILL 章节
- B004 判定规则改为 JSON（P2）

**C. 删除/替换**（P0）
- C001 删除三哲学自检模板
- C002 新建结构化自检清单（JSON Schema）
- C003 清理 SKILL 中三哲学引用
- C004 评分删除三哲学维度

**D. 持久化**
- D001 knowledge.json 跨轮次沉淀（P0）
- D002 scoring-history.jsonl 评分历史（P1）
- D003 optimization-suggestions.jsonl 优化建议（P1）
- D004 Memurai 命中率记录（P2）
- D005 收敛条件：方差<5 且均分>85 且对账全PASS（P0）

**E. 新增分析维度**
- E001 finding 增加 trust_type（P1）
- E002 finding 增加 exploitability（P1）
- E003 Phase B 配置安全检查（P1）
- E004 Phase C 硬编码凭证检查（P1）
- E005 假阳率度量：每轮抽 30 条（P0）

**F. 信息密度**
- F001 主 SKILL ≤ 50 行（P1）
- F002 rules 7 文件 → 3+1（P1）
- F003 必读保持 7 篇，密度提升（P1）
- F004 删除 AI 已知的废话（P2）
- F005 子 skill 密度审计（P2）

**G. 工具自动化**
- G001 新增 Phase A 入口脚本（P1）
- G002 工具协调矩阵（P2）

**H. 报告简化**
- H001 12 个报告模板 → 3 类（P1）
- H002 结果先行结构（P1）

共 33 条：P0 16 / P1 12 / P2 5



