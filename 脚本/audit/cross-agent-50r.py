"""
cross-agent-50r.py

**老板 = 我（主 agent）执行 cross-agent 反思任务的规则**

50 轮跨 agent 稳定性实验（用户 2026-06-13 规定）。

规则：
- **每轮新开 opencode session**（不续）
- **同一命令**（Java 白盒审计 WebGoat）
- **Round 1**：基线
- **Rounds 2-39**：每轮跑前**删 loop 结果**（保留 Memurai / skill 文件）→ 测稳定性
- **Rounds 40-50**：不删 → 测知识复用
- **每轮 ≤ 2 min 硬限**（用户 5min cap 内更严）
- **全局 5min 硬限**（用户规定）

每轮观测：
1. opencode 跑通否？
2. P5.4 不变量过否？
3. 报告数 / 等级分布 / CVSS 分布
4. Memurai 缓存 audit:* key 数
5. 跑通时间

输出：
  loop_audit/loop-log/cross-50r/round{N}.json  每轮数据
  loop_audit/loop-log/cross-50r/summary.md   老板的最后反思
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ===== 硬上限（用户 2026-06-13 修正：5min 只对 AUDIT 工具调用，跨 agent loop 无全局限）=====
PER_ROUND_TIMEOUT = 3600  # **60 min per opencode round**（§ 22 修订：用户原意 1 round 1 hour）
GLOBAL_TIMEOUT = 1800    # 30 min global（5 轮小规模验证用）
OPENCODE_CMD = r"C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd"  # 必须绝对路径！

# ===== 目标项目 (从 preset.json 加载, 必传 AGENTLOOP_PRESET env 或 CLI 参数) =====
# 用户 2026-06-13: agentloop 是通用工具, 不为任何项目建实例。
# operator 必自己: cp 项目/_template/preset.template.json 项目/<groupId>/preset.json + 填值
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # 脚本/audit/cross-agent-50r.py -> 仓库根
PRESETS_DIR = REPO_ROOT / "项目"
PRESET_PATH_ENV = "AGENTLOOP_PRESET"  # 必设, 指向 项目/{groupId}/preset.json

def load_preset(path: str = None) -> dict:
    """**加载项目 preset** (机器可读项目元信息)。

    用户 2026-06-13: agentloop 是工具, 不内置任何项目实例。
    优先级: CLI 参数 > AGENTLOOP_PRESET env
    必设其一, 否则报错。
    """
    p = path or os.environ.get(PRESET_PATH_ENV)
    if not p:
        raise FileNotFoundError(
            f"未指定 preset.json (agentloop 是工具, 不内置项目实例)。请:\n"
            f"  1. 复制 项目/_template/preset.template.json 到 项目/<groupId>/preset.json\n"
            f"  2. 填 groupId / projectRoot / dockerContainer / appPort / ...\n"
            f"  3. 设 env: export AGENTLOOP_PRESET=项目/<groupId>/preset.json\n"
            f"  4. 再跑 python 脚本/audit/cross-agent-50r.py"
        )
    preset_file = Path(p)
    if not preset_file.exists():
        raise FileNotFoundError(f"preset.json 不存在: {preset_file}")
    return json.loads(preset_file.read_text(encoding="utf-8"))


# ===== 默认值 (向后兼容) — 实际从 preset.json 读 =====
_PRESET = load_preset()
GROUP_ID = _PRESET["groupId"]
REDIS_PREFIX = f"audit:{GROUP_ID}"
PROJECT_ROOT = Path(_PRESET["projectRoot"])
DB = _PRESET.get("codegraphDb", str(PROJECT_ROOT / ".codegraph" / "codegraph.db"))
LOOP_DIR = PROJECT_ROOT / _PRESET.get("loopDir", "loop_audit")
EP_JSONL = LOOP_DIR / _PRESET.get("epJsonl", "external_endpoints/端点.jsonl")
HI_DIR = LOOP_DIR / "routes" / "高风险端点"
LO_DIR = LOOP_DIR / "routes" / "中低险端点"
POC_DIR = LOOP_DIR / "routes" / "poc"
LOG_DIR = LOOP_DIR / "loop-log" / "cross-50r"
LOG_DIR.mkdir(parents=True, exist_ok=True)
MEMURAI_CLI = r"C:\Program Files\Memurai\memurai-cli.exe"

# ===== 命令（每轮都一样，运行时用 preset 替换 __VAR__ 占位符）=====
# 用户 2026-06-13: 主流程不能过拟合 WebGoat, 项目特异数据从 preset.json 读
# 占位符格式: __VAR__ (双下划线包裹, 避免与 {} 冲突, 简单 .replace() 即可)
COMMAND_TEMPLATE = """在 __PROJECT_ROOT__ 跑 Java 白盒审计。codegraph 已索引 __N_ROUTES__ routes + __N_METHODS__ methods。

## 业务环境（从 preset.json 读）

- **项目名**: __PROJECT_NAME__
- **groupId**: `__GROUP_ID__`
- **容器名**: `__DOCKER_CONTAINER__`（必用 docker ps 找，状态必 Up/healthy）
- **应用 URL**: __APP_BASE_URL____APP_CTX_PATH__    ← 注意 __APP_CTX_PATH__ 前缀
- **登录入口**: `__LOGIN_URL__` (测试账号 `__TEST_USER__` / `__TEST_PASS__`)
- **注册入口**: `__REGISTER_URL__`
- **会话 Cookie**: `__SESSION_COOKIE_NAME__`
- **真实登录 HTML**: `__LOGIN_HTML__` 找 form action
- **鉴权配置**: `__SECURITY_CONFIG__` 找 permitAll + formLogin
- **Controller 目录**: `__CONTROLLERS__` 找所有 `@PostMapping`/`@GetMapping`

**附加项目知识**: 必读 `项目/__GROUP_ID__/README.md`（worker 写项目分析）+ `项目/__GROUP_ID__/feedback.md`（累积经验/坑）。
如需更细的源码速查, 读 `项目/__GROUP_ID__/知识沉淀.md`。

## 【§ 14 强约束】docker 环境 + 代码必读

1. 必跑 `docker ps` → 找到 `__DOCKER_CONTAINER__` Up 状态 → 写 `loop_audit/diag/docker_ps.txt`
2. 必跑 `docker inspect __DOCKER_CONTAINER__` → 拿 IP/Port/Env → 写 `loop_audit/diag/docker_inspect.json`
3. 必跑 `docker logs __DOCKER_CONTAINER__ --tail 50` 看启动日志
4. 必读 `__LOGIN_HTML__` 找 form action
5. 必读 `__SECURITY_CONFIG__` 找 permitAll + formLogin
6. 必读 `__CONTROLLERS__` 找所有 `@PostMapping`/`@GetMapping`
7. 必列所有端点 = `grep -rE "@(Post|Get)Mapping" src/main/java/ | sort -u`
8. 每读一个文件 → 追加到 `loop_audit/diag/code_reads.log`

## 【§ 15 强约束】200 OK ≠ 成功，必证明 CIA 影响

> **工程原则：只有返回 200 OK 且实际 CIA 影响可证明，才是 OK**。
> 仅 HTTP 200 仅代表"端点可达"≠ 漏洞可利用。

每个 PoC 必含 4 字段：
- **baseline_response**: 正常请求的响应（参照基线）
- **attack_response**: 攻击后响应（**必含 CIA 证据**）
- **cia_classification**: C / I / A（可多选）
- **impact_description**: 1 句话具体影响

**CIA 证据 pattern 必含**（写进 `loop_audit/diag/poc_real_attack.log`）：
- C（数据泄露）：`root:[x*]:0:0` / `password\s*[:=]` / `Bearer\s+[A-Za-z0-9]{20,}` / `flag\{[a-zA-Z0-9_-]+\}` / `\d+\s*rows?\s*in\s*set`
- I（数据修改）：`UPDATE|DELETE|INSERT` / `successfully\s+(?:deleted|updated)` / `modified\s+\d+\s+rows?`
- A（服务中断）：`5xx (Internal|Service) Error` / `stack\s*trace` / `NullPointerException` / `OutOfMemoryError`

例：
```
__APP_CTX_PATH__/login -d "username=admin' OR 1=1 --&password=admin"
Response: HTTP/1.1 200
Body: "Welcome admin! flag{SQLI_SUCCESS_42}, id=1, password=admin123"
→ CIA: C (data exfiltration)
```

## 【硬约束】文件 + Redis 双标记

- 致命/严重每个端点必含 PoC：写 `loop_audit/routes/poc/`
- 文件名：`{验证状态}_{问题等级}_{fqn.端点mthod-sink点-round001}.md`
  - 验证状态 ∈ {是问题, 非问题, 暂时无法确认, failed}
  - **验证状态必须与文件名一致**（是问题=是问题/非问题=非问题）
- 文件内末尾必加 `<!-- status: finished -->` 标记
- Redis 必记 `audit:{group}:commit:{commit}:file:{fqn}.{method}#{sigHash}:status = finished`

## 【404 必查】

- curl 返回 404 = FAIL 信号，必查：
  1. 路径是否要带 `__APP_CTX_PATH__` 前缀（不是 `/`）
  2. 是否要带 session cookie（先 __LOGIN_URL__）
  3. 是否要 POST 不是 GET
- 每条 404 必写到 `loop_audit/diag/404_investigations.md`

## 【6 个必留 artifact】

写到 `loop_audit/diag/`：
1. `docker_ps.txt` — docker ps 输出（必含 `__DOCKER_CONTAINER__` Up）
2. `docker_inspect.json` — docker inspect（必含 IP/Port/Env）
3. `code_reads.log` — opencode 实际读的源文件清单（必含 `__LOGIN_HTML__` + `__SECURITY_CONFIG__`）
4. `curl_attempts.log` — 所有 curl 请求 + 状态码
5. `404_investigations.md` — 0 404 写"无 404"；有 404 每条解释
6. `poc_real_attack.log` — 真攻击 payload + CIA 证据

**任一缺失 → 整轮 FAIL**（跑 `python 脚本/audit/verify-opencode-compliance.py`）

## 【硬约束】P5.4 数量不变量

- 端点 jsonl: 269 行
- 报告: hi+lo = 269（**不**含 PoC）
- PoC: poc_count == high_risk_count
- 跑 P5.4 校验：`python D:\\wiki\\good-skill\\agentloop\\脚本\\audit\\verify-endpoint-coverage.py ... 退出码 0`

## 【§ 16 强约束】文件名 + 防残留快照污染（2026-06-13 第 3 轮反馈）

> **根因**：上轮 round 1 实际 PASS，但 `round01-boss-samples.md` 抽了**已被 cleanup 删除的 round007 残留快照**，错判 100% FAKE。

### 16.1 PoC 文件名必含 round{N} 标记（强约束）

- 致命/严重每个端点必含 PoC，文件名格式：
  `{验证状态}_{问题等级}_{fqn.端点method-sink类型-round{N}.md`
- **必含 round{N}**（如 `round001`）— opencode 必加
- 不含 round{N} 的 PoC → 旧残留 → 必在跑前 cleanup 删

### 16.3 抽样脚本必按 round{N} 过滤（防抽错源）

- 跑 `python 脚本/audit/sample-poc-for-boss.py` 必传 `--round-filter round{N}`
- 输出 boss-samples.md 必含 `sampled_from = N 个 round{N} 文件`（从磁盘实时列）
- 老板读 boss-samples.md **必先 verify** `sampled_from == 磁盘实际 .md 数`
- 不一致 = 残留污染 → 必重抽

### 16.4 opencode cleanup 范围（防 PoC 累积破坏 P5.4）

- 跑前必删 `loop_audit/routes/{高风险端点,中低险端点,poc}/` 全部 `.md`
- 跑前必删 `loop_audit/external_endpoints/端点.jsonl`
- 跑后必跑 `python 脚本/audit/verify-endpoint-coverage.py` 退出码 0

## 【禁止】

- 读 12 个 rules 文件
- codegraph_explore 12 次
- 写"终态汇总"
- **PoC 写占位**（必含真攻击 + CIA 证据）
- 200 OK 就当成功（必证 CIA）
- 解释你能做什么
- **PoC 文件名漏 round{N} 标记**
- **round{N}.md 写 stale 数字**（必写真实磁盘统计）
- **写 `_gen_routes.py` 等 comprehensive script 批量生成 100+ PoC**（必逐个真 curl）
- **attack_response 段空白 / 模板文本**（必含真实 response body + payload 痕迹）
- **round{N:03d} 编号自创**（必等于 prompt 标头 `WGB-R{N}` 的 N）
- **PoC 缺复现步骤 / Payload / 攻击结果 / CVSS 4.0 复验 4 字段**
- **attack_response = 405/400/404 错用法当 evidence**（必看 controller method 重测）
- **CVSS 写个分数了事**（必含 10 维评分表 + 复验对比 + 错则反思）
- **文件名缺"验证状态"前缀**（reports + PoC 必以 `是问题_/非问题_/暂时无法确认_/failed_` 开头）
- **文件名用 `.` 分隔 fqn**（必 `.` → `__`）
- **文件名漏 CVSS 段**（reports 必含数字 + 可选小数）
- **PoC 缺"二次利用 / 危害链"段**（拿到 secret 必进一步利用，5 步危害链 + 2 次 curl）
- **二次利用段只写"可探测内网 / 可冒充管理员"等分析话术而无实际响应 body**（§ 23 必含完整 curl + 实际响应 + 提取危害数据）
- **启 Sisyphus-Junior subagent（task tool）** — round 2 实证：subagent 跨 session 残留会被砍死，启了只跑 1 个 `ls` 就停。**必须 inline 单进程跑完**（269 路由串行可接受，30min cap 内能完成）

## 【§ 18 强约束】PoC 必含 4 字段（2026-06-13 第 5 轮反馈）

> **根因**：round 2 PasswordReset PoC 实证 — 端点 `GET /PasswordReset/reset/reset-password/{link}` 用 POST 请求拿 405 Method Not Allowed 当"密码重置缺陷"evidence，CVSS 7.5 写死无 10 维复验。**PoC 必含老板可复跑的 4 字段**。

### 18.1 复现步骤（详细流程）

- 必含**分步操作流程**（不是模糊"端点可达"话术）：
  1. 步骤 1：环境准备（docker ps 确认容器 / session 拿 cookie / 端口验证）
  2. 步骤 2：构造请求包（HTTP method / path / headers / body / URL 路径变量 / 查询参数）
  3. 步骤 3：参数修改（哪些值被改 → 改成什么 → 为什么改）
  4. 步骤 4：发送请求（curl 命令完整可粘跑）
  5. 步骤 5：观察响应（哪个字段证明 CIA 影响）
- 禁止 "正常请求返回 HTTP 200（端点可达）" / "端点设计为可被利用" 等空话
- 必含**请求方法对齐**（GET 端点不能用 POST 测，必看 controller 的 @GetMapping / @PostMapping）

### 18.2 验证代码/Payload（核心测试代码）

- 必含**完整可粘跑** 的代码段，二选一：
  - **curl 命令行**（含 cookie + method + URL + body）
    ```
    curl -s -b "__SESSION_COOKIE_NAME__=<session>" -X POST "__APP_BASE_URL____APP_CTX_PATH__/.../attack" \
      -d "username=admin' OR 1=1 --&password=admin"
    ```
  - **Python requests 代码**（含 session + payload + response 截取）
    ```python
    import requests
    s = requests.Session()
    s.post("__APP_BASE_URL____APP_CTX_PATH__/login", data={"username":"__TEST_USER__","password":"__TEST_PASS__"})
    r = s.post("__APP_BASE_URL____APP_CTX_PATH__/SqlInjection/attack2", data={"username":"admin' OR 1=1 --"})
    print(r.status_code, r.text[:500])
    ```
- 必含**为什么这个 payload 能触发漏洞**（不只贴代码）
- 禁止只写 "Payload: flag=challenge_flag_value" / "input=malicious_tainted_value" 等无意义字符串

### 18.3 攻击结果（CIA 影响可证）

- 必含 attack_response **实际响应 body** 截取（不是空 / 不是 405/400/404 错用法）
- 必含 **CIA evidence pattern**（13+ 条至少 1 条命中）：
  - C：flag{...} / password= / Bearer / DB rows
  - I：UPDATE/DELETE/INSERT / success / modified N rows
  - A：5xx / stack trace / NullPointerException
- 错用法（405 Method Not Allowed / 400 Bad Request / 404 Not Found）**不是 attack evidence** → 必重测正确 method
- 必含 impact_description **具体到业务影响**（"管理员密码被改 / 用户 token 泄露"不是"可被利用"）

### 18.4 CVSS 4.0 验证打分（10 维复验）

- 必含 **CVSS 4.0 10 维评分表**（不是写个分数了事）：

  | 维度 | 缩写 | 取值 | 解释 |
  |------|------|------|------|
  | Attack Vector | AV | N/A/L/P | 网络/邻接/本地/物理 |
  | Attack Complexity | AC | L/H | 低/高 |
  | Attack Requirements | AT | N/P | 无/有 |
  | Privileges Required | PR | N/L/H | 无/低/高 |
  | User Interaction | UI | N/P | 无/被动 |
  | Vulnerable System Confidentiality | VC | N/L/H | 无/低/高 |
  | Vulnerable System Integrity | VI | N/L/H | 无/低/高 |
  | Vulnerable System Availability | VA | N/L/H | 无/低/高 |
  | Subsequent System Confidentiality | SC | N/L/H | 无/低/高 |
  | Subsequent System Integrity | SI | N/L/H | 无/低/高 |
  | Subsequent System Availability | SA | N/L/H | 无/低/高 |

- 必含**基分计算结果**（如 `CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N` → 8.6 严重）
- 必含**与原 CVSS 对比**：原打分 X → 复验打分 Y → **错就改 + 反思 root cause**
  - 例：原 7.5 严重 → 复验 9.1 致命（错把 SC 漏算）
  - 例：原 9.1 致命 → 复验 5.3 中（错把 VC 估成 H，实际只 L）

### 18.5 错用法识别（自动 FAIL）

| 错用法响应 | 含义 | 应对 |
|------------|------|------|
| 405 Method Not Allowed | curl method 错 | 看 controller @GetMapping / @PostMapping → 改 method |
| 400 Bad Request | 参数错 / 缺必填 | 看 controller @RequestParam → 加参数 |
| 404 Not Found | URL 错 | 看 SecurityConfig permitAll + class-level @RequestMapping |
| 401 Unauthorized | 没 session | 先 POST /login 拿 cookie |
| 403 Forbidden | CSRF / 权限 | CSRF 已 disabled 不用管；权限用 admin 账号 |
| 空响应 / "端点可达" | 没真 attack | 必发真 attack payload，重测 |

## 【§ 19 强约束】统一命名 7 段格式（2026-06-13 第 6 轮反馈）

> **根因**：磁盘 reports（`routes/高风险端点/` + `routes/中低险端点/`）269 个文件**缺"验证状态"前缀**（是问题/非问题/暂时无法确认/failed），与 PoC 命名格式不一致。**统一为 7 段格式**。

### 19.1 7 段命名格式（reports + PoC 必对齐）

```
{验证状态}_{问题等级}_{CVSS}_{漏洞类型}_{fqn.replace('.', '__')}_{method}_{sigHash}.md
```

**7 段严格定义**：

| 段 | 必含 | 取值 |
|----|------|------|
| 1. 验证状态 | 必含 | `是问题` / `非问题` / `暂时无法确认` / `failed` |
| 2. 问题等级 | 必含 | `致命` / `严重` / `中` / `低` / `无` |
| 3. CVSS | 必含 | 数字 + 可选小数（如 `9.8` / `7.5` / `4.3`） |
| 4. 漏洞类型 | 必含 | 大写英文（`RCE` / `SQLi` / `XXE` / `AccessControl` / `AuthBypass` / `PasswordReset` / `SessionHijack` / `SanitizerBypass` / `DevTools` / `Client-Side` / `Deserialization` / `SSRF` / `XSS` / `Taint_Sink` / `ZIP_SLIP`） |
| 5. fqn | 必含 | Java FQN，`.` 替换为 `__`（如 `org.owasp.webgoat.taintaudit.TaintAuditSinks` → `org__owasp__webgoat__taintaudit__TaintAuditSinks`） |
| 6. method | 必含 | 端点方法名（小驼峰，如 `jndiService` / `completed` / `addComment` / `login`） |
| 7. sigHash | 必含 | 8 字符哈希（如 `cc53573c` / `47156734`） |

**分隔符**：段间用 `_`（下划线），fqn 内 `.` → `__`（双下划线），method 和 sigHash 之间也用 `_`。

### 19.2 PoC 文件名附加 round{N:03d}（防与 reports 撞名）

```
{验证状态}_{问题等级}_{CVSS}_{漏洞类型}_{fqn.replace('.', '__')}_{method}_{sigHash}-round{N:03d}.md
```

例（用户格式）：
```
是问题_致命_9.8_RCE_org__owasp__webgoat__taintaudit__TaintAuditSinks_jndiService_cc53573c-round008.md
```

### 19.3 验证状态从哪来（必与文件内 4 字段一致）

- 文件内 `## PoC 验证 → 验证状态` 字段值 = 文件名第 1 段
- 老板抽 sample 必验：filename prefix == content 验证状态
- 不一致 = 自动 FAIL（§ 18.5 错用法表同类）

### 19.4 历史 reports 缺验证状态（必补）

- 磁盘已有 269 reports 文件**缺第 1 段**（验证状态）— 需批量 rename
- **批量 rename 规则**（从报告内容反推验证状态）：
  - 读 `## PoC 验证 → 验证状态` 字段
  - 在文件名前补 `{验证状态}_`（无则跳过）
  - CVSS 必含（如缺，从 `## 危险等级 → CVSS 4.0` 读）
- 老板授权 → 派 opencode 跑 `rename_reports_add_status.py` 一次性补全

## 【§ 20 强约束】P5.4 算法修订 + 接受"宁缺毋滥"（2026-06-13 第 7 轮反馈）

> **根因**：round 3 跑出 5 PoC（opencode 拒绝假造其余 114）→ 老板判定：5 真 PoC > 119 假 PoC = § 18 强约束真生效。**P5.4 不变量需修订** = 接受 PoC 数 < 高危数（只要 PoC 都是真 evidence）。

### 20.1 P5.4 不变量新公式

```
hi + lo + 暂时无法确认 = ep_lines
其中：
- hi = 高风险端点数（致命+严重）
- lo = 中低险端点数（中+低+无）
- 暂时无法确认 = opencode 标"无真 evidence / 跳过"的端点数
- ep_lines = 端点 jsonl 行数
```

**P5.4 PASS 条件**：`hi + lo + 暂时无法确认 == ep_lines`（**不**要求 `poc_count == hi`）

### 20.2 PoC 完整性新算法

```
poc_ok = 真 evidence PoC 数（含 flag / password / IP / rows / stack trace 等 13+ pattern 至少 1 条命中）
poc_fake = 假 PoC 数（"端点可达" / 空 response / 405 错用法 / 模板字符串）
poc_skipped = 跳过端点数（opencode 主动标"实际证据缺失"）

poc_consistent = (poc_ok + poc_fake == hi)  # PoC 数 = 高危数（但允许 skipped 单独算）
poc_quality = (poc_fake / (poc_ok + poc_fake)) <= 5%  # FAKE 比例 ≤ 5%
```

**老板验收**：
- P5.4 PASS + poc_quality ≤ 5% → round PASS
- P5.4 PASS 但 poc_fake > 5% → round FAIL
- P5.4 PASS + poc_quality 0% + 大量 skipped → round PASS（宁缺毋滥）

### 20.3 § 18 强约束新范围

- § 18 必 4 字段**只对 poc_ok 端点生效**（opencode 真做了 PoC 的）
- skipped 端点**不**写假 PoC，**不**写假 4 字段
- skipped 端点在 routes/高风险端点/ 中**保留**（不删），但加 `## PoC 验证 → 实际证据缺失` 标识

### 20.4 opencode 拒绝假造 = 老板的胜利

- **宁缺毋滥 > 67% 假 PoC**（round 2 教训）
- 5 真 PoC（IP 泄露 / JWT secret / XXE）= 完美样本 > 119 假 PoC
- 老板**不**接受"为完成数量而假造 PoC" — 接受"为质量而跳过"

## 【§ 21 强约束】危害链必做（2026-06-13 第 8 轮反馈）

> **根因**：round 3 5 真 PoC 中，JWT 拿 token 没二次利用，SSRF 拿 IP 没二次探测。**§ 18 只到"证明 CIA"为止，缺"二次利用"约束**。**拿到 secret 必进一步利用证明危害**。

### 21.1 漏洞类型 → 二次利用必做

| 漏洞类型 | 一次发现 | 二次利用必做 | 证明危害 |
|----------|----------|--------------|----------|
| **JWT 弱密钥 / 伪造 token** | 拿到 JWT token | **用此 token 调 /WebGoat/admin/users 或其他受保护端点** | 拿到管理员数据 / 改用户角色 |
| **SSRF 拿 server IP** | 拿到 server IP `139.159.170.81` | **用此 IP 探测内网服务**（如 `http://169.254.169.254/latest/meta-data/`） | 拿到云元数据 / 内网服务列表 |
| **XXE 读文件** | 拿到 /etc/passwd | **用 XXE 读其他敏感文件**（`file:///home/webgoat/.ssh/id_rsa`） | 拿到 SSH 私钥 / 数据库凭据 |
| **SQLi** | 拿到 SQL error | **UNION SELECT 提取数据**（`' UNION SELECT username,password FROM users --`）| 拿到 admin 密码哈希 / 用户表 |
| **Auth Bypass / 缺 AC** | 拿到 flag | **用 admin 账号访问受保护 lesson 拿完成证明** | lessonCompleted=true for all |
| **CSRF** | 伪造请求成功 | **用此漏洞调其他修改端点**（如改密码 / 转账） | 实际数据被改 |
| **Deserialization** | 触发反序列化 | **构造 gadget chain 拿 RCE**（如 `Runtime.exec("id")`） | 服务器命令执行证据 |
| **RCE** | 触发命令执行 | **用此 RCE 拿 shell / 读 server 关键文件** | 服务器完全控制证据 |
| **ZIP_SLIP** | 路径穿越 | **用穿越路径写文件到 webroot** | 拿 webshell |
| **SSRF URL** | 拿内部 URL | **访问 metadata / admin endpoint** | 云凭据泄露 |

### 21.2 危害链必含（attack chain）

每个 PoC 必含 **5 步危害链**（不是单步）：

```
1. 一次发现：构造初始 payload → 拿到 secret（token/IP/file/data）
2. 二次利用：拿此 secret 调其他端点 / 探测内网 / 读更多文件
3. 危害证明：二次利用的响应 body 含实际危害证据
4. CIA 分类：标 C/I/A（可多选）
5. 影响描述：具体到业务（"管理员密码被改 / 任意 SQL 查询结果返回"）
```

### 21.3 § 18 4 字段扩展为 5 字段（新增第 5 字段）

原 § 18 4 字段：
- 复现步骤
- Payload
- 攻击响应
- CIA 影响

**新增第 5 字段**：
- **二次利用 / 危害链**（拿到 secret 后进一步证明危害的步骤 + 证据）

```markdown
### 二次利用 / 危害链
1. **一次发现**: {初始 payload 拿到什么 secret}
   ```bash
   {curl 完整命令}
   ```
   {响应 body 截取 + 提取的 secret}

2. **二次利用**: {用此 secret 调什么端点 / 探测什么}
   ```bash
   {第二次 curl 命令（含 secret 注入）}
   ```
   {响应 body 截取 + 危害证据}

3. **危害证明**: {实际影响，C/I/A 具体证据}
   - C: {泄露数据：password / token / flag / DB rows}
   - I: {数据修改：modified N rows / success}
   - A: {服务中断：5xx / stack trace / 异常}

4. **CIA 分类**: {C/I/A 可多选}

5. **影响描述**: {一句话具体到业务}
```

### 21.4 二次利用失败处理

- 二次利用响应 = 401/403/404 → **不算二次利用**（需调其他**受保护**端点）
- 二次利用响应 = 200 但 body 空 / "lessonCompleted:true" 模板 → **不算二次利用**（需 body 含真数据）
- 二次利用**没**真危害证据 → 标 `二次利用失败：{原因}` + 验证状态降级为 `暂时无法确认`
- 二次利用成功 → 验证状态保持 `是问题` + CIA 分类升（如 C 升 C+I）

### 21.5 老板验收新口径

- 抽 5/5 真 PoC 必读"二次利用 / 危害链"段
- 必含 2 次 curl 命令（一次发现 + 二次利用）+ 2 个响应 body 截取
- 缺二次利用段 → **FAIL**（即便一次发现是真 evidence）
- § 20 接受"宁缺毋滥"仍生效（**不**做二次利用的端点可 skipped）

## 【§ 22 强约束】流程物理矛盾修订（2026-06-13 第 9 轮反馈）

> **根因**：round 4 跑 30 min cap 触发，opencode 跑出 7 真 PoC（poc_real_attack.log 49 行）但**0 reports**（routes/ 全空）→ § 18 4 字段 + § 21 5 步 + § 17.1 禁 comprehensive script 三者**物理矛盾**：
> - § 17.1 禁 _gen_*.py → opencode 自创 _gen_reports.py + _write_pocs.py 灰色脚本（违反）
> - § 18 4 字段 + § 21 5 步 = 9 字段必含 → 30 min 跑不完 181 端点 + 回填 .md
> - cap 30 min 砍了 opencode → 0 reports

### 22.1 § 18 4 字段 → 2 必含 + 3 可选

**必含**（不写 = FAIL）：
- 复现步骤
- Payload（curl/Python 完整可粘跑）

**可选**（缺不 FAIL）：
- 攻击响应
- CIA 影响
- CVSS 4.0 复验

**§ 18.5 错用法识别仍生效**：attack_response = "端点可达 / HTTP 200/400 响应已确认" 等模板仍 FAIL。

### 22.3 删 § 17.1 禁 comprehensive script

- **新条款**：opencode **自由选择**最佳路径（单端点 / 批处理 / subagent / 工具脚本）
- **唯一约束**：最终 routes/*.md **必逐个含真 evidence**（不写假 PoC）
- 工具脚本（_gen_*.py）允许，**不**算违规

### 22.4 cap 60 min 全局（接受 1 round 1 hour）

- `PER_ROUND_TIMEOUT = 3600`
- 用户原意不是 30 min（5 min cap 只对 AUDIT 工具）

### 22.5 验收新口径

| 场景 | 判定 |
|------|------|
| P5.4 PASS + 抽样 5 个 .md 必含真 evidence + 致命级含二次利用 | **PASS** |
| P5.4 PASS + 抽样 5 个 .md 假 evidence（模板）| **FAIL** |
| P5.4 PASS + 0 reports 但 diag/ 有真 attack log | **PARTIAL**（流程工程性 OK 但产物不达标 → 老板修订下一轮） |
| P5.4 FAIL | **FAIL**（必重跑） |

## 【§ 23 强约束】二次利用必含实际攻击结果（2026-06-13 第 10 轮反馈）

> **根因**：round 3 SSRF PoC 写"服务器 IP 139.159.170.81 泄露，**可探测内网**" — 这是**分析**，**不**是**实际二次利用**。用户反馈：二次利用必**实际记录攻击结果**（不是写分析话术）。

### 23.1 二次利用 3 段必含完整 evidence

每个二次利用步骤必含 3 段（**不**只是"可探测内网"等分析话）：

```markdown
#### 二次利用步骤 N: {描述}

**完整 curl 命令**（必含 cookie + method + URL + 二次利用 payload）：
```bash
curl -s -b "__SESSION_COOKIE_NAME__=<session>" -X POST "__APP_BASE_URL____APP_CTX_PATH__/admin/users" \
  -H "Authorization: Bearer {第一次拿到的 token}" \
  -d "action=list"
```

**实际响应 body 截取**（必含 HTTP 状态 + 完整 body 200-500 字，**不**省略）：
```
HTTP/1.1 200 OK
Content-Type: application/json
{
  "users": [
    {"id":1, "username":"admin", "email":"admin@__GROUP_ID__.local", "role":"ADMIN"},
    {"id":2, "username":"__TEST_USER__", "email":"__TEST_USER__@__GROUP_ID__.local", "role":"USER"}
  ],
  "admin_email_leaked": "admin@__GROUP_ID__.local",
  "admin_token_issued": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJBRE1JTiJ9.xxxxx"
}
```

**提取的危害数据**（从响应 body 实际提取，**不**是分析推测）：
- C: admin email `admin@webgoat.local` 泄露 + admin token `eyJ...xxxxx` 拿到
- 危害值：可用此 token 调任何 admin-only 端点
```

### 23.2 二次利用必含 13+ CIA pattern 至少 1 条实际命中

| 漏洞类型 | 一次发现 | 二次利用必含的实际 pattern |
|----------|----------|----------------------------|
| JWT | 拿 token | 二次利用响应必含 `Bearer eyJ` / `{"role":"ADMIN"}` / `admin@*.com` |
| SSRF | 拿 server IP | 二次利用响应必含 `169.254.169.254` 响应 / `ami-id` / `instance-id` / 内网服务 banner |
| XXE | 读 /etc/passwd | 二次利用响应必含 `root:[x*]:0:0` / `nobody:` / `ssh-rsa` 私钥 / `mysql:` 密码行 |
| SQLi | 拿 SQL error | 二次利用响应必含 `UNION SELECT` 结果 / `\d+ rows? in set` / admin 密码哈希 |
| Auth Bypass | 拿 flag | 二次利用响应必含 `lessonCompleted:true` for protected lesson / 改角色成功 |
| CSRF | 伪造请求成功 | 二次利用响应必含 `successfully updated` / `modified \d+ rows` |
| Deserialization | 触发反序列化 | 二次利用响应必含 `Runtime.exec` 证据 / `whoami` 输出 / shell prompt |
| RCE | 触发命令执行 | 二次利用响应必含 `uid=\d+` / 实际命令输出 |
| ZIP_SLIP | 路径穿越 | 二次利用响应必含 `webshell 写入成功` / 实际文件内容 |
| SSRF URL | 拿内部 URL | 二次利用响应必含 metadata `AccessKeyId` / `SecretAccessKey` / 内网服务列表 |

### 23.3 二次利用失败的 3 种处理

| 失败场景 | 必含的标注 | 验证状态 |
|----------|-----------|----------|
| 二次利用响应 401/403/404 | `二次利用失败：{原因}` + 实际响应 body | 降级 `暂时无法确认` |
| 二次利用响应 200 但 body 空 / 模板 | `二次利用失败：响应空 / 模板` + 实际响应 body | 降级 `暂时无法确认` |
| 二次利用响应 200 但**没**13+ pattern 命中 | `二次利用响应无 13+ pattern 命中` + 实际响应 body + 反思 | 降级 `暂时无法确认` |

**禁止**：写"可探测内网 / 可冒充管理员 / 可读更多文件"等**分析话术**而无实际二次利用响应 — 自动 FAIL

### 23.4 § 21 与 § 23 关系

- § 21.2 危害链 5 步 → 必含 § 23.1 三段（完整 curl + 实际响应 body 截取 + 提取危害数据）
- § 21.5 验收口径 → 必含 § 23.2 13+ pattern 实际命中

## 【§ 24 强约束】P5.4 不许删 PoC（2026-06-13 第 11 轮反馈）

> **根因**：round 4 (实际 round 5) opencode 跑 P5.4 PASS 时 stdout_tail 自陈"**PoC files were cleaned**. Let me re-check and re-write them" — opencode 跑 P5.4 时**误删**了 routes/poc/ 已写完的 6 个真 PoC，**时间不够重写** → 0 PoC 文件。

### 24.1 P5.4 跑前必 backup routes/poc/

- 跑 `verify-endpoint-coverage.py` 前必 `cp -r routes/poc/ routes/poc.bak.{ts}/`
- 跑后必 diff：`if routes/poc/*.md 数 < backup 数 → 必恢复 backup`
- 老板验收必跑：`diff -rq routes/poc/ routes/poc.bak.{ts}/` 看是否有误删

### 24.2 opencode 必在 P5.4 PASS 前先写完 routes/poc/*.md

- **禁止**："PoC files were cleaned"等借口（cleanup 不应删 PoC）
- **必含顺序**：discover_routes → 真 attack → 写 routes/*.md → 写 routes/poc/*.md → 跑 P5.4
- P5.4 PASS 前必自检：`ls routes/poc/*.md | wc -l == expected`

### 24.3 跑完必报一致性

每轮跑完必报：
- `P5.4 = {PASS|FAIL}, routes/poc/*.md = {N} 个, diag/poc_real_attack.log = {M} 行`
- `{N} == {M}`（routes/poc 必有 N 个 PoC 文件，poc_real_attack.log 必有 M 行真 attack log）
- 不一致 → round PARTIAL（**不**算 PASS）

## 【§ 25 强约束】cleanup 不删端点 jsonl（2026-06-13 第 12 轮反馈）

> **根因**：round 5 (实际 round 6) opencode 跑出 185 reports + 5 PoC + 9.2 min（**3 倍加速**），但 `cleanup_loop_results` 删了 `external_endpoints/端点.jsonl` → 跑 `verify-endpoint-coverage.py` 时 jsonl 缺 → `ep_lines = 0` → `p54_pass = null` → 5 维 1 项 FAIL。

### 25.1 cleanup_loop_results 不删端点 jsonl

```python
def cleanup_loop_results():
    # Section 25: cleanup keeps endpoint jsonl (P5.4 needs it)
    for d in [HI_DIR, LO_DIR, POC_DIR]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
    # **不删** external_endpoints/端点.jsonl（P5.4 必查的源）
    # if EP_JSONL.exists():
    #     EP_JSONL.unlink()  # 注释掉
```

### 25.2 端点 jsonl 重建策略

| 轮次 | jsonl 处理 |
|------|-----------|
| Round 1 (INIT) | **删** + 重建（discover_routes.py 跑） |
| Round 2-39 (CLEAN) | **保留**（P5.4 必查；opencode 跑实际 attack 时复用） |
| Round 40-50 (KEEP) | **保留**（KEEP 模式直接复用） |

### 25.3 round N>1 跑前必验 jsonl 存在

```python
if EP_JSONL.exists() and (2 <= n <= 39 or 40 <= n <= 50):
    print(f"  [jsonl] 保留: {ep_lines} 行")
else:
    print(f"  [jsonl] 重建（discover_routes）")
```

### 25.4 opencode 必报 P5.4 一致性

每轮跑完 opencode 必自报：
- `routes/poc/*.md = {N} 个`（**不**为 0）
- `diag/poc_real_attack.log = {M} 行`
- `external_endpoints/端点.jsonl = {L} 行`（P5.4 必查的源）
- `{N} == {L} == {M}`（一致性，N 个 PoC 必对应 M 行真 attack log + L 行端点）
- 不一致 → 自动 PARTIAL，不算 PASS

## 时间

≤ 30 min 硬上限（用户 2026-06-13 规定）。

## Section 27: REAL secondary exploitation results, NO intent/analysis/description (2026-06-13 round 14)

> Root cause: round 9 SSRF PoC wrote "should probe 169.254.169.254 for IAM creds, but container has no cloud metadata service reachable" - this is INTENT/DESCRIPTION not REAL RESULT. User feedback: secondary exploitation MUST contain REAL observed result (actual response body, actual command output, actual step-by-step observation), not description/analysis. **No intent labels. No "can do X" / "may lead to Y" wording.**

### 27.1 secondary exploitation MUST contain real observed result

| must contain | example (good) | example (BAD - reject) |
|--------------|----------------|------------------------|
| actual curl command + **actual response body** captured | ```curl ...; Response: HTTP 200, body: {...datasource.url: jdbc:hsqldb:mem:..., ssl.key-store-password: changeit...}``` | "应探测169.254.169.254" |
| actual command execution + **actual stdout/stderr** | ```id; Output: uid=0(root) gid=0(root) groups=0(root)``` | "可执行任意命令" |
| actual file read + **actual file content** | ```cat /etc/passwd; Output: root:x:0:0:root:/root:/bin/bash\nnobody:...``` | "可读更多文件" |
| actual DB query + **actual rows** | ```UNION SELECT ...; Output: admin:x:0:0:hash:...``` | "可提取数据" |

### 27.2 mandatory 4 fields in secondary exploitation (no shortcut)

```
#### Step 1: Initial Discovery (REPEAT initial payload + actual response)
- Curl command (full)
- HTTP status code
- Response body (FULL or 200-500 char excerpt, **NOT** "200 OK")
- Extracted hazard (C/I/A pattern hit + line number / field name)

#### Step 2: Secondary Exploitation Attempt (REPEAT secondary curl + actual response)
- Curl command (with secondary payload, e.g. union select / metadata URL / id command)
- HTTP status code
- Response body (FULL or 200-500 char excerpt)
- Extracted hazard (C/I/A pattern hit + line number / field name)

#### Step 3: Tertiary Exploitation Attempt (if applicable)
- Same as Step 2 with deeper payload (e.g. cat /etc/shadow after XXE)

#### CIA Classification + Impact Description
- C / I / A
- Specific business impact (1 sentence)
```

### 27.3 FAIL criteria (no intent/analysis/description accepted)

| scenario | verdict | label |
|----------|---------|-------|
| 2nd exploit **real response body + 13+ pattern hit** | **PASS** | keep shi-wenti |
| 2nd exploit **real response body but NO 13+ pattern hit** | **FAIL** | downgrade zan-shi-wu-fa-que-ren + label "**实际证据缺失**：response body captured but no CIA pattern matched" |
| 2nd exploit **401/403/404/405 wrong method/path** | **FAIL** | downgrade zan-shi-wu-fa-que-ren + label "**实际证据缺失**：wrong usage {actual response code + body}" |
| 2nd exploit **NOT attempted** (only intent/analysis written) | **FAIL** | downgrade zan-shi-wu-fa-que-ren + label "**实际证据缺失**：no 2nd exploit attempt, only intent written" |
| 2nd exploit **intention/analysis wording** ("应探测 / 可执行 / 可读取") | **FAIL** | downgrade zan-shi-wu-fa-que-ren + label "**实际证据缺失**：intent/analysis wording detected, no real 2nd exploit attempt" |

### 27.4 Anti-hallucination verification

Boss抽样必查5个PoC的"二次利用"段:
- 必含 `Response:` / `Output:` / `Body:` 实际响应字段（**不**只"200 OK"）
- 必含 13+ pattern 实际命中字符串（如 `flag{...}` / `password=` / `Bearer` / `root:[x*]:0:0` / `uid=`）
- 必含 ≥ 2 次 curl 命令（一次发现 + 二次利用，**不**只 1 次）
- 缺任一 → round FAIL

### 27.5 replacement of Section 26

Section 26 intent label is REPLACED by Section 27. Intent labels are NO LONGER ACCEPTED.
- Section 26.4 "intent label as ACCEPTABLE" -> Section 27.3 "intent wording as FAIL"
- All future rounds use Section 27 criteria.

## Section 28: "unable to confirm" MUST include reason + reflection + code evidence (anti-lazy, 2026-06-13 round 15)

> Root cause: opencode may mark "暂时无法确认" without real attempt (lazy). Boss抽5必查. User feedback: 标"无法确认"必含失败原因 + 二次反思（是否合理 / 是否胡编 / 是否没读代码）+ 代码证据（行号 + 文件名）。

### 28.1 "unable to confirm" 4 mandatory fields (NO shortcut)

| field | content | good example | BAD example (reject) |
|-------|---------|--------------|----------------------|
| **失败原因** | specific failure mode | "2nd exploit curl 401 Unauthorized, JSESSIONID 失效" | "未尝试" / "可能不行" |
| **尝试步骤** | what was tried | "试了 (1) /actuator/env (2) 探测 169.254.169.254 (3) UNION SELECT users" | "没试" |
| **二次反思** | self-reflect: 合理? 胡编? 没读代码? | "反思: SecurityConfig.java line 42 启用 formLogin, 普通 session 拿不到 actuator 权限, 真的无法" | "懒得试" |
| **代码证据** | specific source line + file | "src/main/java/.../SecurityConfig.java:42 `http.authorizeRequests().antMatchers(\"/actuator/**\").hasRole(\"ADMIN\")`" | (no code ref) |

### 28.2 mandatory PoC template for "unable to confirm"

```markdown
## PoC Verification -> 验证状态: 暂时无法确认

### 失败原因
{what specifically failed: HTTP 401/403/404/405/timeout/etc + actual response}

### 尝试步骤
1. 试了 {step 1} -> 拿到 {result}
2. 试了 {step 2} -> 拿到 {result}
3. 试了 {step 3} -> 拿到 {result}
{最少 2 步, 不接受 "没试"}

### 二次反思
- 失败模式是否合理? {yes / no + reason}
- 是否胡编乱造? {no, based on code_reads.log line X + SecurityConfig.java line Y}
- 是否没读代码? {no, 读了 {具体文件 + 行号} 找到 {具体原因}}
- 真的无法解决? {yes / no + reason}

### 代码证据
- 文件: {FQN 路径}
- 行号: {line N - line M}
- 内容: {copy 3-5 行源码}
- 解读: {这段代码为什么导致失败}

<!-- status: finished -->
```

### 28.3 anti-lazy FAIL criteria (boss抽 5 必查)

| lazy signal | description | verdict |
|-------------|-------------|---------|
| "未尝试" / "没试" / "没读代码" in 失败原因 | marked "无法确认" but didn't try | **FAIL** (auto downgrade to "is-issue" + reopen) |
| "可能不行" / "或许" / "大概" / "也许" | vague wording without evidence | **FAIL** (must be evidence-based) |
| no code reference (no file:line) in 反思 | reflection has no source | **FAIL** |
| 同一原因复制 5 个端点 | templated "无法确认" across endpoints | **FAIL** (each endpoint must have independent analysis) |
| 尝试步骤 < 2 | only 1 step or empty | **FAIL** (minimum 2 attempted steps) |
| 失败原因只说 "未能确认" / "endpoint not reachable" | no specific HTTP code + response | **FAIL** (must have actual HTTP code + body excerpt) |

### 28.4 boss抽 5 verification flow

```
1. ls routes/poc/*暂时无法确认*.md | head -5  # 抽 5 个
2. for f in 5 files:
   - 必含 失败原因 段 (not empty, not "未尝试")
   - 必含 尝试步骤 段 (>= 2 steps)
   - 必含 二次反思 段 (mentions "失败模式 / 胡编 / 没读代码" all 3)
   - 必含 代码证据 段 (file:line + 3-5 line excerpt)
3. if any of above missing -> round FAIL
```

### 28.5 self-reflection scoring (opencode必含)

每轮跑完 opencode必自报:
- 标"无法确认"的端点数 (从 routes/poc/ 列表)
- 抽 5 必读
- 5/5 通过反思 = round PASS 反思段
- < 5/5 通过 = round FAIL 反思段

### 28.6 replace Section 20.4 "lazy" wording

Section 20.4 接受"宁缺毋滥" (opencode 主动 skip 跳过)。但 Section 28 要求:**skip 必含原因 + 反思 + 代码证据**, 不接受"未尝试就 skip"。

- Section 20.4 "opencode 拒假造 = 老板胜利" -> Section 28.6 "opencode 拒假造 = 必含代码证据反思"

## Section 29: ALL severe-level MUST contain real secondary exploitation (upgrade Section 22.2, 2026-06-13 round 16)

> Root cause: round 11 Actuator/env severe CVSS 7.5 missing 2nd exploit. Section 22.2 was "severe AND CVSS >= 7.5" (so 7.0-7.4 was optional). User feedback: ALL severe-level MUST contain real 2nd exploit (no threshold cut-off). Medium/low still optional.

### 29.1 secondary exploitation required range (UPGRADED)

| level | secondary exploitation | note |
|-------|------------------------|------|
| fatal (CVSS >= 9.0) | **MUST contain 5 steps** | unchanged |
| severe (CVSS 7.0-8.9) | **MUST contain 5 steps** | **UPGRADED** (was CVSS >= 7.5 only) |
| medium (CVSS 4.0-6.9) | optional | unchanged |
| low (CVSS 0.1-3.9) | optional | unchanged |

### 29.2 secondary exploitation mandatory 4 sections (Section 23 + 27 + 29 combined)

For fatal + severe (MUST):

```
#### Step 1: Initial Discovery (REPEAT initial payload + actual response)
- Curl command (full)
- HTTP status code
- Response body (FULL or 200-500 char excerpt, **NOT** "200 OK")
- Extracted hazard (C/I/A pattern hit + line number / field name)

#### Step 2: Secondary Exploitation Attempt (REPEAT secondary curl + actual response)
- Curl command (with secondary payload, e.g. union select / metadata URL / id command)
- HTTP status code
- Response body (FULL or 200-500 char excerpt)
- Extracted hazard (C/I/A pattern hit + line number / field name)

#### Step 3: Tertiary Exploitation Attempt (if applicable, e.g. cat /etc/shadow after XXE)
- Same as Step 2 with deeper payload

#### CIA Classification + Impact Description
- C / I / A
- Specific business impact (1 sentence)
```

### 29.3 downgrade criteria (real result vs intent)

| scenario | verdict | label |
|----------|---------|-------|
| 2nd exploit **real response body + 13+ pattern hit** | **PASS** | keep shi-wenti |
| 2nd exploit **real response body but NO 13+ pattern hit** | **FAIL** | downgrade zan-shi-wu-fa-que-ren + Section 28 reflection |
| 2nd exploit **401/403/404/405 wrong usage** | **FAIL** | downgrade zan-shi-wu-fa-que-ren + Section 28 reflection |
| 2nd exploit **NOT attempted** (only intent/analysis written) | **FAIL** | downgrade zan-shi-wu-fa-que-ren + Section 28 reflection |
| 2nd exploit **intent/analysis wording** ("应探测 / 可执行 / 可读取") | **FAIL** | downgrade zan-shi-wu-fa-que-ren + Section 28 reflection |

### 29.4 boss抽 5 verification flow (severe + fatal only)

```
1. ls routes/poc/*是问题_严重*.md + 是问题_致命*.md | head -5  # 抽 5
2. for f in 5 files:
   - 必含 Step 1 (initial discovery curl + actual response)
   - 必含 Step 2 (secondary exploit curl + actual response body + 13+ pattern hit)
   - 必含 Step 3 (if applicable)
   - 必含 CIA Classification + Impact Description
3. any file missing Step 2 二次利用 -> round FAIL
```

## Section 30: n_poc consistency check (upgrade Section 16.2, 2026-06-13 round 17)

> Root cause: round 11 opencode self-reported "109 poc_verified" but actual disk `ls routes/poc/*.md | wc -l` = 11. User feedback: opencode MUST self-check n_poc consistency, mismatch = auto FAIL.

### 30.1 opencode MUST self-check n_poc consistency before reporting PASS

```bash
# Required before reporting "round N done":
N_POC_DISK=$(ls routes/poc/*.md | wc -l)
N_POC_REPORTED=$YOUR_CLAIMED_NUMBER
if [ "$N_POC_DISK" != "$N_POC_REPORTED" ]; then
    echo "FAIL: n_poc reported=$N_POC_REPORTED but disk=$N_POC_DISK"
    exit 1
fi
```

### 30.2 mandatory self-check fields in round{N}.md 1-line summary

```
round N done: X reports, Y poc_verified, Z diag_artifacts, P5.4=PASS/FAIL

where:
- X = (ls routes/高风险端点/*.md | wc -l) + (ls routes/中低险端点/*.md | wc -l)
- Y = ls routes/poc/*.md | wc -l
- Z = ls diag/{docker_ps.txt,docker_inspect.json,code_reads.log,curl_attempts.log,404_investigations.md,poc_real_attack.log} | wc -l  # MUST = 6
- P5.4 = verify-endpoint-coverage.py exit code
```

### 30.3 anti-lie verification (boss抽 5 + cross-check)

```
1. read round{N}.md 1-line summary
2. for each of (X, Y, Z):
   - re-run `ls ... | wc -l` on disk
   - compare with summary
   - mismatch -> round FAIL (opencode lied)
3. if any of X/Y/Z mismatch -> round FAIL
```

### 30.4 FAIL criteria (lie detection)

| scenario | verdict |
|----------|---------|
| round{N}.md X claim != `ls routes/高风险端点/*.md | wc -l + ...` | **FAIL** (opencode lied about report count) |
| round{N}.md Y claim != `ls routes/poc/*.md | wc -l` | **FAIL** (opencode lied about PoC count, round 11 example) |
| round{N}.md Z claim != 6 (mandatory artifacts) | **FAIL** (opencode lied about artifacts) |
| round{N}.md P5.4=PASS claim but `verify-endpoint-coverage.py` exit != 0 | **FAIL** (opencode lied about P5.4) |

## Section 31: 全调用链枚举 + 风险分级 + PoC 隔离 (2026-06-13 第 18 轮反馈)

> **根因**: round 12 抽 `routes/高风险端点/是问题_严重_7.5_PasswordReset_..._changePassword_d575d094.md` 发现: 文档只记录了**找到漏洞的那一条调用链** (`changePassword` 直调), 没列出到达该端点的**所有调用链** + 没做**风险分级**。用户反馈: 每个 source 必须**先枚举所有调用链 → 分级 → 只对高风险调用链做 PoC**, 三步必须**串行** (等所有调用链分析完才启动 PoC 利用验证)。

### 31.1 每个 source 必须先枚举全调用链 (3 类)

对每个 source 文件 (Controller/Service/Endpoint), 必须**先**枚举到达该 source 的**所有调用链**, 分成 3 块记录在 `routes/高风险端点/*.md` **之前** (在 `## 分析` 段, 在 `## PoC 验证` 段**之前**):

```
## 分析

### # 调用链 1: {调用链描述, e.g. POST /PasswordReset/reset/change-password → changePassword()}
- 入口: {URL + method}
- 中间调用: {Controller.method -> Service.method -> ...}
- 危险点: {sink, e.g. password reset token storage / SQL query / XXE parser}
- 参数: {name, type, source}
- 风险分级: **高风险** / **低风险** / **无风险**
- 理由: {为什么这个分级, e.g. "CWE-640 weak recovery, attacker can reset others' password"}

### # 调用链 2: {另一条调用链, e.g. GET /PasswordReset/reset/change-password → 405 拒绝}
- 入口: {URL + method}
- 中间调用: ...
- 危险点: ...
- 风险分级: **低风险** / **无风险**
- 理由: {e.g. "405 Method Not Allowed, no impact"}
```

### 31.2 风险分级判定标准 (硬性, 不能跳过)

| 等级 | 判定 | PoC 必做? |
|------|------|-----------|
| **高风险** | 调用链会触达 sink (DB query / file read / password reset / RCE 等) + 用户输入可注入 + 缺防护/可绕过 | **必做 PoC** |
| **低风险** | 调用链会触达 sink, 但有部分防护 (auth required + RBAC + whitelisted input) — 攻击面**部分**存在, 不是 0 风险 | **不做 PoC, 但记录分级理由** |
| **无风险** | 调用链 405/401/404 OR 调用链到 sink 前被 sanitized/escaped OR 调用的方法无副作用 (getter / log) | **不做 PoC, 但记录分级理由** |

**关键**: **低风险 ≠ 0 风险**。低风险必须含:
1. 具体防护 (e.g. `@PreAuthorize("hasRole('ADMIN')")` 之类)
2. 攻击面的残余风险 (e.g. "admin 账号被劫持后仍可利用")
3. 不只是"看起来像 401"

### 31.3 PoC 隔离原则 (硬性)

- **只对** "高风险" 调用链做 PoC
- **不**对 "低风险" / "无风险" 调用链做 PoC
- 1 个 source 的 PoC 数量 ≤ 该 source 的 "高风险" 调用链数

**禁止**:
- 1 个 source 有 3 条调用链, 但做了 3 个 PoC (低/无风险也做) → 浪费 + 模板化
- 1 个 source 有 3 条调用链, 只做了 1 个 PoC 但没解释"哪 1 条是高风险" → 必须先有 31.1 全调用链表

### 31.4 串行执行顺序 (硬性, 不许并发)

**禁止**: "发现 1 条调用链 → 立刻做 PoC → 再找下一条调用链 → 再做 PoC" (opencode 偷懒常见模式: 找到第 1 个 sink 立刻 PoC, 其他调用链全跳过)。

**必须**:
1. **Step A**: 读 1 个 source 文件, 用 codegraph / ast-grep 列出**所有**到达 sink 的调用链
2. **Step B**: 把所有调用链**全部分级** (高/低/无), 写进 `routes/高风险端点/*.md` 的 `## 分析` 段
3. **Step C**: **等所有调用链都分析完** (高/低/无都记录), 才启动 PoC 利用验证
4. **Step D**: PoC **只**针对 Step B 标记的 "高风险" 调用链做

```
source X
  ↓
[Step A] 读 source + 列所有调用链 (e.g. 3 条: changePassword / forgotPassword / login)
  ↓
[Step B] 3 条全部分级: 高/低/无, 写进 ## 分析
  ↓
[Step C] 3 条都分析完 → 才进 Step D
  ↓
[Step D] 只对 "高风险" 调用链 (e.g. 1 条: changePassword) 做 PoC
  ↓
routes/高风险端点/{source}_*.md 写完整: ## 分析 (3 条) + ## PoC 验证 (1 个 PoC)
```

### 31.5 文档模板 (硬性结构)

`routes/高风险端点/{source}_*.md` 必须**严格**按这个结构:

```markdown
# {漏洞名} - {FQN.method}

## 端点信息
- **FQN**: `...`
- **方法**: `...`
- **HTTP**: `...`
- **漏洞类型**: ...
- **危险等级**: ...
- **CVSS 4.0**: ...
- **sig_hash**: ...

## 分析

### # 调用链 1: {描述}
- 入口: {URL + method}
- 中间调用: ...
- 危险点: ...
- 参数: ...
- 风险分级: **高风险** / **低风险** / **无风险**
- 理由: ...

### # 调用链 2: {描述}
- 入口: ...
- 风险分级: **低风险** / **无风险**
- 理由: ...

### # 调用链 N: ...
- 风险分级: **无风险**
- 理由: ...

**调用链统计**: 高风险 X 条, 低风险 Y 条, 无风险 Z 条 (X+Y+Z == 调用链总数)

## PoC 验证 -> 验证状态: 是问题 / 暂时无法确认 / 非问题

### {只针对高风险调用链 1 的 PoC}
### 复现步骤
...
### Payload
...
### 攻击响应
...
### CIA 影响
...
### CVSS 4.0 复验
...
### 二次利用 / 危害链
#### Step 1: 初始发现
#### Step 2: 二次利用尝试
```

### 31.6 验证状态判定 (基于调用链分级)

| 调用链分级 | 验证状态 |
|-----------|---------|
| 全部 "无风险" | **非问题** (源文档可标 非问题) |
| 至少 1 条 "高风险" + PoC 成功 | **是问题** (PoC 必有二次利用 + 真 evidence) |
| 至少 1 条 "高风险" + PoC 二次利用失败 (§ 27 真实结果 + § 28 4 字段) | **暂时无法确认** |
| 至少 1 条 "低风险" + 0 条 "高风险" | **非问题** (低风险不入 PoC, 但文档必须含低风险理由) |

### 31.7 必查字段 (boss 抽样 20% PoC 必查)

| 必查字段 | 不通过示例 |
|---------|-----------|
| `## 分析` 段有**所有**调用链 (≥ 2 条, 单调用链直接 FAIL) | 文档只 1 条调用链 (round 12 fail) |
| 每条调用链有 `风险分级:` 字段 (高/低/无 之一) | 缺 `风险分级:` 字段 |
| `风险分级:` 理由 ≥ 1 句 (不只写"看起来像 401") | "低风险" 理由空 |
| `## PoC 验证` 段 PoC 数 == 高风险调用链数 | 3 条调用链做 3 个 PoC (低/无也做) |
| 高风险调用链 PoC 缺二次利用 → 降级 `暂时无法确认` + § 28 4 字段 | 高风险 PoC 写"基于容器环境限制" |

### 31.8 FAIL 案例 (opencode 偷懒识别)

| 偷懒模式 | 案例 | 判定 |
|---------|------|------|
| 1 个 source 只列 1 条调用链 | round 12 `changePassword` 只列 changePassword 直调 | **FAIL** (缺其他调用链分析) |
| 1 个 source 漏掉"低风险" 理由 | "低风险: 401 拒绝" (无防护细节) | **FAIL** (低风险必须含具体防护) |
| 跳过 "无风险" 调用链不写 | 3 条调用链只写 1 条 (有 sink) + 2 条 (无 sink) 跳过 | **FAIL** (无风险也必写) |
| "低风险" 用了"高风险" PoC 链 | "低风险" 的 PoC 跟 "高风险" 是同一条 | **FAIL** (重复 + 误用 PoC) |
| PoC 数 > 高风险调用链数 | 1 高 + 2 低, 但写 3 个 PoC | **FAIL** (低/无风险不应有 PoC) |
| PoC 二次利用写"基于容器环境限制" | round 12 Actuator/env Step 2 | **FAIL** (§ 27 + § 31 双重 fail) |

## 完成后

只回报 1 行：`round N done: M reports, P poc_verified, CIA=C:x I:y A:z, P5.4=PASS/FAIL`"""


def count_reports() -> tuple:
    """数 .md 数 + P5.4 校验 + Memurai 缓存键数。PoC 不算 reports (是高危附加产物)。"""
    n_hi = len(list(HI_DIR.glob("*.md"))) if HI_DIR.exists() else 0
    n_lo = len(list(LO_DIR.glob("*.md"))) if LO_DIR.exists() else 0
    n_poc = len(list(POC_DIR.glob("*.md"))) if POC_DIR.exists() else 0
    n_reports = n_hi + n_lo  # **P5.4 不含 PoC**（PoC 是高危的 1:1 附加）
    ep_lines = 0
    if EP_JSONL.exists():
        with open(EP_JSONL, encoding="utf-8") as f:
            ep_lines = sum(1 for _ in f)

    # P5.4 校验：n_hi + n_lo == ep_lines
    p54_pass = None
    if ep_lines and n_reports:
        p54_pass = (n_reports == ep_lines)

    # **PoC 一致性**：poc == high_risk（每个高危 1 PoC）
    poc_consistent = (n_poc == n_hi) if n_hi else True

    # Memurai audit:{GROUP_ID}:* key 数 (项目隔离, 不数其他项目)
    n_redis = 0
    try:
        proc = subprocess.run(
            [MEMURAI_CLI, "-h", "localhost", "-p", "6379", "-e",
             "--scan", "--pattern", f"{REDIS_PREFIX}:*", "--count", "1000"],
            capture_output=True, text=True, timeout=10,
        )
        n_redis = sum(1 for _ in proc.stdout.splitlines() if _.strip())
    except Exception:
        pass

    return {
        "n_hi": n_hi,
        "n_lo": n_lo,
        "n_poc": n_poc,
        "total_reports": n_reports,
        "ep_lines": ep_lines,
        "p54_pass": p54_pass,
        "poc_consistent": poc_consistent,
        "n_redis_audit_keys": n_redis,
    }


def cleanup_loop_results():
    """Delete routes/*.md, KEEP endpoint jsonl + Memurai + skill files (Section 25: cleanup does not delete endpoint jsonl, P5.4 needs it)."""
    for d in [HI_DIR, LO_DIR, POC_DIR]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
    # Section 25: do NOT delete endpoint jsonl (P5.4 needs it)
    # if EP_JSONL.exists():
    #     EP_JSONL.unlink()


def format_command(preset: dict) -> str:
    """**运行时** 用 preset.json 替换 COMMAND_TEMPLATE 中的 __VAR__ 占位符。

    用户 2026-06-13: 主流程不能过拟合 WebGoat, 项目特异数据从 preset 注入。
    优点: 切项目只改 preset.json, 不改代码。
    """
    key_files = preset.get("keyFiles", {})
    codegraph = preset.get("codegraph", {})
    repl = {
        "__PROJECT_ROOT__": preset.get("projectRoot", ""),
        "__PROJECT_NAME__": preset.get("projectName", ""),
        "__GROUP_ID__": preset.get("groupId", ""),
        "__DOCKER_CONTAINER__": preset.get("dockerContainer", ""),
        "__APP_PORT__": str(preset.get("appPort", "")),
        "__APP_CTX_PATH__": preset.get("appCtxPath", ""),
        "__APP_BASE_URL__": preset.get("appBaseUrl", ""),
        "__LOGIN_URL__": preset.get("loginUrl", ""),
        "__REGISTER_URL__": preset.get("registerUrl", ""),
        "__SESSION_COOKIE_NAME__": preset.get("sessionCookieName", "JSESSIONID"),
        "__TEST_USER__": preset.get("testUser", ""),
        "__TEST_PASS__": preset.get("testPass", ""),
        "__LOGIN_HTML__": key_files.get("loginHtml", ""),
        "__SECURITY_CONFIG__": key_files.get("securityConfig", ""),
        "__CONTROLLERS__": key_files.get("controllers", ""),
        "__N_ROUTES__": str(codegraph.get("nRoutes", "?")),
        "__N_METHODS__": str(codegraph.get("nMethods", "?")),
    }
    out = COMMAND_TEMPLATE
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def clear_redis_cache() -> int:
    """**每次项目启动前清除本项目历史 Redis 缓存** (audit:{GROUP_ID}:* keys)。

    用户 2026-06-13 反馈:
    1. 项目启动前必清, 防止上轮残留污染本轮 audit 结果
    2. **必按 groupId 隔离, 避免误删其他项目的 audit:* 缓存**

    Returns: 删除的 key 数 (失败返回 -1, 0 表示无 key)。
    """
    try:
        # Step 1: SCAN 列出本项目所有 audit:{GROUP_ID}:* key (项目隔离)
        scan_proc = subprocess.run(
            [MEMURAI_CLI, "-h", "localhost", "-p", "6379", "-e",
             "--scan", "--pattern", f"{REDIS_PREFIX}:*", "--count", "1000"],
            capture_output=True, text=True, timeout=10,
        )
        if scan_proc.returncode != 0:
            return -1
        keys = [k.strip() for k in scan_proc.stdout.splitlines() if k.strip()]
        if not keys:
            return 0

        # 防御: 二次校验所有 key 必含 {REDIS_PREFIX}: 前缀, 防止 DEL 误伤其他项目
        for k in keys:
            if not k.startswith(f"{REDIS_PREFIX}:"):
                print(f"  [WARN] 跳过非本项目 key: {k}")
                return -1

        # Step 2: DEL 批量删除 (Redis 单次命令上限 1000 keys, 实际很少超)
        del_proc = subprocess.run(
            [MEMURAI_CLI, "-h", "localhost", "-p", "6379", "-e",
             "DEL", *keys],
            capture_output=True, text=True, timeout=10,
        )
        if del_proc.returncode != 0:
            return -1
        # DEL 返回删除数量 (整数)
        try:
            return int(del_proc.stdout.strip())
        except ValueError:
            return len(keys)
    except subprocess.TimeoutExpired:
        return -1
    except FileNotFoundError:
        # Memurai 未安装/未运行, 跳过 (不阻塞 round)
        return -1
    except Exception:
        return -1


def run_new_opencode_session(round_n: int) -> dict:
    """**新开** opencode session（不传 -s），跑同一命令。
    关键：positional message 必须在 --file 之前，否则 --file 把 message 当文件名解析。
    """
    t0 = time.time()
    # 短 message + 长 prompt 写文件，--file 引用
    # 用 preset.json 替换 {占位符}, 不再硬编码项目路径
    command_rendered = format_command(_PRESET)
    short_msg = f"Stability R{round_n}/50. Read the attached file and execute it on {_PRESET['projectRoot']}. Verify P5.4 before exit."
    prompt_file = Path(rf"C:\Users\ADMINI~1\AppData\Local\Temp\wgb-prompt-{round_n}.txt")
    prompt_file.write_text(command_rendered, encoding="utf-8")

    cmd = [
        OPENCODE_CMD, "run", short_msg,
        "--model", "alibaba-cn/qwen3.7-max",
        "--agent", "Sisyphus - ultraworker",
        "--title", f"WGB-R{round_n}",
        "--file", str(prompt_file),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            timeout=PER_ROUND_TIMEOUT,
        )
        elapsed = time.time() - t0
        return {
            "round": round_n,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "elapsed": round(elapsed, 1),
            "rc": proc.returncode,
            "stdout_tail": proc.stdout[-500:] if proc.stdout else "",
            "stderr_tail": proc.stderr[-300:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"round": round_n, "ts": datetime.now().isoformat(timespec="seconds"),
                "elapsed": PER_ROUND_TIMEOUT, "rc": -1, "error": "2min timeout"}
    except Exception as e:
        return {"round": round_n, "ts": datetime.now().isoformat(timespec="seconds"),
                "elapsed": time.time()-t0, "rc": -1, "error": str(e)}


def main():
    print(f"=== 跨 agent 50 轮稳定性实验启动 ===")
    print(f"规则: 每轮 NEW opencode session + 同样命令 + ≤2min/round + 5min global")
    t_start = time.time()
    history = []

    # **Daemon 模式**：跑 50 轮，**用户每跑一次累积 1 轮**
    # 1=INIT/clean baseline, 2-39=CLEAN 删结果, 40-50=KEEP 测知识复用
    MAX_ROUNDS = int(os.environ.get("MAX_ROUNDS", "50"))

    # 检查哪些轮次已完成（断点续跑）
    completed = []
    for n in range(1, MAX_ROUNDS + 1):
        p = LOG_DIR / f"round{n:02d}.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("p54_pass") is True:
                    completed.append(n)
            except Exception:
                pass

    print(f"已完成的轮: {completed}")
    if len(completed) >= MAX_ROUNDS:
        print(f"50 轮已全部完成，退出。")
        return 0

    for n in range(completed[-1] + 1 if completed else 1, MAX_ROUNDS + 1):
        if time.time() - t_start > GLOBAL_TIMEOUT:
            print(f"\n[GLOBAL {GLOBAL_TIMEOUT}s CAP] stopping at round {n-1}")
            break

        # 1=INIT/clean baseline, 2-39=CLEAN 删结果, 40-50=KEEP 测知识复用
        if n == 1:
            cleanup_loop_results()
            cleanup_label = "INIT (clean baseline)"
        elif 2 <= n <= 39:
            cleanup_loop_results()
            cleanup_label = "CLEAN (delete results)"
        else:  # 40-50
            cleanup_label = "KEEP (test knowledge reuse)"

        # **每轮项目启动前清 Redis 缓存** (用户 2026-06-13 反馈, 防上轮残留污染本轮)
        n_cleared = clear_redis_cache()
        redis_label = f"redis_cleared={n_cleared}" if n_cleared >= 0 else "redis_cleared=FAIL"
        print(f"\n--- R{n}/{MAX_ROUNDS} [{cleanup_label}] [{redis_label}] ---")
        result = run_new_opencode_session(n)
        metrics = count_reports()
        result.update(metrics)
        history.append(result)

        log_path = LOG_DIR / f"round{n:02d}.json"
        log_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        status = "OK" if metrics.get("p54_pass") else "FAIL"
        poc_status = "POC_OK" if metrics.get("poc_consistent") else "POC_MISMATCH"
        print(f"  [{status}/{poc_status}] elapsed={result['elapsed']}s  reports={metrics['total_reports']}/{metrics.get('ep_lines', '?')}  poc={metrics.get('n_poc', 0)}  redis={metrics['n_redis_audit_keys']}")

    summary = {
        "total_rounds": len(history),
        "passed_p54": sum(1 for h in history if h.get("p54_pass")),
        "history": [
            {
                "round": h["round"],
                "elapsed": h.get("elapsed"),
                "p54_pass": h.get("p54_pass"),
                "total_reports": h.get("total_reports"),
                "n_hi": h.get("n_hi"),
                "n_lo": h.get("n_lo"),
                "n_poc": h.get("n_poc"),
                "n_redis_audit_keys": h.get("n_redis_audit_keys"),
            } for h in history
        ],
        "elapsed_total": round(time.time() - t_start, 1),
    }
    (LOG_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 本次跑完 {summary['passed_p54']}/{summary['total_rounds']}  P5.4 PASS ===")
    print(f"总耗时: {summary['elapsed_total']}s (cap {GLOBAL_TIMEOUT}s)")
    print(f"summary: {LOG_DIR / 'summary.json'}")

    # **项目结束反馈** (用户 2026-06-13: 反馈沉底到 项目/{groupId}/feedback.md)
    # 触发: env AGENTLOOP_FINAL_FEEDBACK=1
    if os.environ.get("AGENTLOOP_FINAL_FEEDBACK") == "1":
        print(f"\n=== AGENTLOOP_FINAL_FEEDBACK=1, 派发项目结束反馈 ===")
        from collect_feedback import run_feedback_session, collect_rounds, get_feedback_path
        rounds = collect_rounds(LOG_DIR)
        if rounds:
            fb_path = get_feedback_path(_PRESET)
            print(f"  反馈目标: {fb_path}")
            print(f"  数据源: {len(rounds)} 个 round ({LOG_DIR}/)")
            fb_result = run_feedback_session(_PRESET, rounds, LOG_DIR)
            print(f"  反馈 session: rc={fb_result.get('rc')} elapsed={fb_result.get('elapsed')}s")
            if fb_path.exists():
                n_sections = fb_path.read_text(encoding="utf-8").count("## Project Run")
                print(f"  反馈文件 Project Run 段数: {n_sections}")
        else:
            print(f"  [SKIP] 无 round 数据可分析")


if __name__ == "__main__":
    main()
