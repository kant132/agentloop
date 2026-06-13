# 部署指南：从 agentloop/ 到生产位置

> 本目录是**隔离测试沙箱**。验证通过后，将内容 cp 到生产位置。

## 一、生产位置映射

| 源（agentloop/） | 目标（生产） | 说明 |
|------------------|-------------|------|
| `行为准则/必读/` | `D:\wiki\行为准则\必读\` | 公共规范，必须在生产 |
| `行为准则/经验/` | `D:\wiki\行为准则\经验\` | 同上 |
| `行为准则/优化路径/` | `D:\wiki\行为准则\优化路径\` | 同上 |
| `脚本/chain/` | `D:\wiki\脚本\chain\` | 工具脚本 |
| `脚本/redis/` | `D:\wiki\脚本\redis\` | 同上 |
| `脚本/ast/` | `D:\wiki\脚本\ast\` | 同上 |
| `脚本/audit/` | `D:\wiki\脚本\audit\` | 同上 |
| `脚本/deprecated/` | `D:\wiki\脚本\deprecated\` | 反例库（保留） |
| `类型/注入类/` | `D:\wiki\类型\注入类\` | 漏洞类型库 |
| `类型/业务逻辑/` | `D:\wiki\类型\业务逻辑\` | 同上 |
| `类型/鉴权类/` | `D:\wiki\类型\鉴权类\` | 同上 |
| `类型/信息泄露/` | `D:\wiki\类型\信息泄露\` | 同上 |
| `类型/反序列化/` | `D:\wiki\类型\反序列化\` | 同上 |
| `类型/文件操作/` | `D:\wiki\类型\文件操作\` | 同上 |
| `类型/攻击模式模板.json` | `D:\wiki\类型\攻击模式模板.json` | 跨类型索引 |
| `项目/_template/` | `D:\wiki\项目\_template\` | 项目模板（保留） |
| `skills/java-whitebox-loop/` | `D:\wiki\skills\java-whitebox-loop\` | 主 skill |
| `skills/java-forward-vuln-discovery/` | `D:\wiki\skills\java-forward-vuln-discovery\` | 子 skill |
| `skills/threat-model-analyst/` | `D:\wiki\skills\threat-model-analyst\` | 必加子 skill |
| `skills/jadx-python-decompile/` | `D:\wiki\skills\jadx-python-decompile\` | 反编译子 skill |
| `skills/ssh-skill/` | `D:\wiki\skills\ssh-skill\` | SSH 子 skill |
| `skills/playwright-skill/` | `D:\wiki\skills\playwright-skill\` | 浏览器子 skill |
| `loop_audit/_template/` | `D:\wiki\loop_audit\_template\` | 产物模板 |

## 二、部署步骤

### 2.1 一次性部署（验证后）

```bash
# Windows PowerShell
$src = "D:\wiki\good-skill\agentloop"

# 顶层 5 个目录
foreach ($dir in @("行为准则","脚本","类型","skills")) {
    New-Item -ItemType Directory -Force -Path "D:\wiki\$dir"
    Copy-Item -Path "$src\$dir\*" -Destination "D:\wiki\$dir\" -Recurse -Force
}

# 项目目录（只复制模板，运行时按 groupId 复制）
New-Item -ItemType Directory -Force -Path "D:\wiki\项目"
Copy-Item -Path "$src\项目\_template" -Destination "D:\wiki\项目\_template\" -Recurse -Force

# loop_audit 模板
New-Item -ItemType Directory -Force -Path "D:\wiki\loop_audit"
Copy-Item -Path "$src\loop_audit\_template" -Destination "D:\wiki\loop_audit\_template\" -Recurse -Force

# 顶层 README 与 DEPLOY
Copy-Item "$src\README.md" "D:\wiki\README-agentloop.md" -Force
```

### 2.2 增量更新（日常）

```bash
# 仅更新修改过的 skill
diff -rq "D:\wiki\good-skill\agentloop\skills\java-forward-vuln-discovery" "D:\wiki\skills\java-forward-vuln-discovery"
# 手动或脚本同步
```

## 三、回滚策略

- **生产位置每次更新前**先 `cp -r D:\wiki\skills D:\wiki\skills.bak-$(date +%Y%m%d)`
- 保留最近 5 个备份
- 关键 skill（如 java-whitebox-loop）的回滚 = 还原 + 重启 orchestrator

## 四、版本管理

- 本目录用 git 管理（`good-skill/agentloop/` 应纳入版本控制）
- 生产 `D:\wiki\skills/` 等**不要**直接 git 管理（避免双向同步混乱）
- 升级路径：agentloop/ → 验证 → cp 到生产 → git tag

## 五、隔离测试

```bash
# 在 agentloop/ 内部测试
cd D:\wiki\good-skill\agentloop

# 启动 Memurai（Windows 上通常作为系统服务运行；自带 memurai-cli.exe）
#   sc query Memurai      # 查看状态
#   sc start Memurai      # 启动
#   memurai-cli ping      # 自检
# 若未安装 Memurai，可使用 WSL 中的 redis-server 或 memurai 的便携版

# codegraph 索引（假设目标项目在 ../target-project）
cd ../target-project
codegraph init && codegraph index
cd ../good-skill/agentloop

# 运行 orchestrator（伪命令，实际由 agent 触发）
# orchestrator --project ../target-project --groupId com.example.x
```

## 六、不部署的内容

- `README.md`（本目录特有）
- `DEPLOY.md`（本目录特有）
- `good-skill/agentloop/` 自身（保留在 good-skill/ 下作为开发沙箱）
