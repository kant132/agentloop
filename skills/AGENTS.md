# AGENTS.md — skills/

13 个 OpenCode skill 定义。每个子目录以 SKILL.md 为唯一入口。

## 层次拓扑

```
Tier 0  java-whitebox-loop (Boss入口)
          │
Tier 1  endpoint-supervisor (端点协调器)
          │ 按需派发
          ├── injection-audit      (注入类专家)
          ├── auth-chain-audit     (鉴权链专家)
          ├── business-logic-audit (业务逻辑专家)
          ├── file-audit           (文件安全专家)
          ├── login-audit          (登录安全专家)
          └── poc-verify           (PoC验证专家)
                    │
Tier 3  工具/基础设施
          ├── arthas-deploy → ssh-skill
          ├── arthas-audit (578文件，99%是Arthas离线文档)
          ├── playwright-skill
          └── jadx-python-decompile
```

## 13 Skill 清单

| Skill | 文件数 | 一行描述 |
|-------|-------|---------|
| `java-whitebox-loop` | 11 | Boss编排入口，驱动Phase A→D全流程，含rules/ |
| `endpoint-supervisor` | 1 | 端点协调器，串行链→分析→专家→汇总 |
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

## 核心控制逻辑

`java-whitebox-loop/rules/` 下 3 个现行规则文件：
- `phase-gates.md` — 4阶段门控(A枚举→B安全上下文→C链+审计→D收敛)
- `self-evolution.md` — 收敛条件、评分公式、知识合并
- `pruning-and-keys.md` — L1/L2/L3剪枝 + Memurai key schema

7 个 DEPRECATED 旧版规则（01-07编号）仍保留作为历史参考。

## 硬依赖约束

- **Phase 0强制前置**：清Memurai缓存，保留knowledge:*
- **三核心工具缺一不可**：codegraph/ast-grep/Memurai，缺失→exit 2
- **arthas先部署后审计**：arthas-deploy产出arthas-config.json(status=active)后才能probe
- **expert超时60s**：超时标expert_timeout，不阻塞流水线
- **poc-verify不直接派发**：由poc-monitor.py守护轮询Memurai再dispatch

## 悬空引用

`endpoint-supervisor/SKILL.md`引用`skills/call-chain-audit-thinking/SKILL.md`(analyst)，但该目录**不存在**。当前analyst能力可能由agent直接执行或已废弃。

## 注意

- arthas-audit 578文件中仅`scripts/arthas_exec.py`是可执行代码，其余是离线文档
- expert skill均为轻量单文件SKILL.md，审计逻辑靠prompt约束驱动
- arthas链路：deploy(config.json) → audit(probe→审计)，禁止边修边审
