# AGENTS.md — skills/

OpenCode skill 定义。每个子目录以 SKILL.md 为唯一入口。

## 层次拓扑（两层架构）

```
主 agent (Boss) — 调度中心，加载 java-whitebox-loop skill
  │
  ├── 脚本工具: Phase 0-2（确定性工作，不需要 AI）
  │     ├── Phase 0: check_core_tools.py + Memurai cleanup
  │     ├── Phase 1: exposure/cli.py collect（9 collectors）
  │     └── Phase 2: chain_builder.py → jar-analyzer.db chains 表 + Memurai 方法体缓存
  │
  ├── 专家 agent: Phase 3（AI 分析，通过 task() 委派）
  │     ├── injection-audit      → SQL/CMD/XXE/SpEL/LDAP/反序列化
  │     ├── auth-chain-audit     → Filter/Interceptor/JWT/OAuth/Session
  │     ├── business-logic-audit → 竞态/流程绕过/IDOR/mass assignment
  │     ├── file-audit           → 上传/下载/路径遍历/ZipSlip
  │     └── login-audit          → 暴力破解/凭证/MFA/Session Fixation
  │
  └── 验证 agent: Phase 4（PoC，通过 task() 委派）
        └── poc-verify           → CVSS 评分 + curl/arthas/SSH 验证

工具/基础设施（按需调用）：
  ├── arthas-deploy → ssh-skill
  ├── arthas-audit (578文件，99%是Arthas离线文档)
  ├── playwright-skill
  └── jadx-python-decompile
```

**关键变化**：
- 去掉 endpoint-supervisor（主管层），Boss 直接对接专家
- 不启动独立 opencode 子进程，主 agent 直接消费 jar-analyzer.db chains 表 + Memurai 数据
- 专家 agent 通过 task() 委派，主 agent 整理好数据后发放

## Skill 清单

| Skill | 文件数 | 一行描述 |
|-------|-------|---------|
| `java-whitebox-loop` | 11 | Boss编排入口，驱动Phase 0→4全流程，含rules/ |
| `injection-audit` | 1 | SQL/CMD/XXE/SpEL/SSTI/LDAP/NoSQL/反序列化 |
| `auth-chain-audit` | 1 | Filter顺序/路径归一化/JWT/OAuth2/Session |
| `business-logic-audit` | 1 | 竞态/流程绕过/数值边界/mass assignment/IDOR |
| `file-audit` | 1 | 上传/下载/解压(ZipSlip)/路径遍历/沙箱 |
| `login-audit` | 1 | 暴力破解/凭证/MFA/Session Fixation/用户枚举 |
| `poc-verify` | 1 | CVSS 3.1评分 + curl/arthas/SSH执行验证 |
| `arthas-audit` | 578 | 运行时动态分析（**569个是Arthas官方文档离线拷贝**） |
| `arthas-deploy` | 7 | Tunnel Server部署 + K8s/Pod注入 |
| `ssh-skill` | 1 | Phase 1拓扑发现 + Phase 5 PoC后端 |
| `playwright-skill` | 1 | 浏览器自动化PoC验证 |
| `jadx-python-decompile` | 1 | jar反编译（仅jar-only项目） |

**已移除**：
- `endpoint-supervisor` — 主管层已去掉，Boss 直接对接专家

## 核心控制逻辑

`java-whitebox-loop/rules/` 下 3 个现行规则文件：
- `phase-gates.md` — 4阶段门控(Phase 0→Phase 4)
- `self-evolution.md` — 收敛条件、评分公式、知识合并
- `pruning-and-keys.md` — L1/L2/L3剪枝 + Memurai key schema

## 硬依赖约束

- **Phase 0强制前置**：清Memurai缓存，保留knowledge:*
- **三核心工具缺一不可**：codegraph/ast-grep/Memurai，缺失→exit 2
- **arthas先部署后审计**：arthas-deploy产出arthas-config.json(status=active)后才能probe
- **专家 agent 通过 task() 委派**：主 agent 整理好数据后发放，不启动 opencode 子进程
- **PoC 由主 agent 直接调度**：不再通过 poc-monitor.py 守护轮询

## 注意

- arthas-audit 578文件中仅`scripts/arthas_exec.py`是可执行代码，其余是离线文档
- expert skill均为轻量单文件SKILL.md，审计逻辑靠prompt约束驱动
- arthas链路：deploy(config.json) → audit(probe→审计)，禁止边修边审
