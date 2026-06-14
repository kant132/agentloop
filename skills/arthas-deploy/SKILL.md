---
name: arthas-deploy
version: 1.0.0
description: "Arthas 统一远程诊断部署（Windows 本机 + Linux 远程）。本机启动 arthas-tunnel-server（bind 0.0.0.0）作为中心 hub，通过 SSH（SFTP）把 skill 自带的 deploy/arthas-bin.zip 上传到 Linux 宿主机（该宿主机装了 kubectl）。mode=linux 时直接在宿主机跑 arthas；mode=k8s 时在宿主机上再 kubectl cp 进 Pod、kubectl exec 跑 arthas。所有 agent 反向 WebSocket 连回本机 tunnel server。"
tools: [Read, Bash, Write, Glob]
depends: [ssh-skill]
---

# Arthas 统一远程部署（Tunnel Server 模式）

本机跑一个 arthas-tunnel-server 作为中心 hub；远程 Linux 宿主机或 K8s Pod 中的 arthas agent **主动反向 WebSocket** 连回 hub。下游 skill 只需访问 `http://<tunnel_host>:8080/proxy/<agentId>/?method=execArthasCommand&cmd=...` 就能操控所有已注册 agent。

所有 SSH/SFTP 操作必须通过 **ssh-skill** 标准接口（禁止裸 `ssh`/`scp`）。Agent 执行前先读取 ssh-skill 文档了解可用 API，**arthas-deploy 不写死任何 ssh-skill 内部路径**。

## 前置条件

1. 本机 JDK 11+（运行 tunnel server）
2. `deploy/` 目录完整：`arthas-bin.zip` + `arthas-tunnel-server-4.2.0-fatjar.jar`（默认配置勿改）
3. ssh-skill 已配置：目标 Linux 宿主机 SSH 别名可用
4. mode=k8s 时宿主机装好 kubectl，`~/.kube/config` 能连通集群
5. 宿主机/Pod 出站能访问 `tunnel_host:7777`（agent 反向回连关键端口）

## 输入参数

| 参数 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `ssh_alias` | mode=k8s 表示**宿主机**；mode=linux 表示目标机器 | — | SSH 别名 |
| `mode` | ✔ | — | `linux` / `k8s` |
| `namespace` | k8s 必填 | — | K8s namespace |
| `pod_name` | k8s 必填 | — | Pod 名称 |
| `container` | k8s 多容器必填 | — | 容器名 |
| `java_pid` | 否 | 自动检测 | 目标 Java PID |
| `app_name` | 否 | `arthas` | agentId 前缀 |
| `deploy_pkg` | 否 | `{skill_dir}/deploy/arthas-bin.zip` | arthas 包源路径 |
| `tunnel_server_jar` | 否 | `{skill_dir}/deploy/arthas-tunnel-server-4.2.0-fatjar.jar` | tunnel server jar |
| `tunnel_ws_port` | 否 | `7777` | agent 反向 WebSocket 端口 |
| `tunnel_web_port` | 否 | `8080` | HTTP proxy / Web Console 端口 |
| `tunnel_host` | 否 | 自动探测 | 本机对远程可达的 IP |
| `remote_work_dir` | 否 | `/tmp/.arthas` | 宿主机/Pod 内工作目录 |

## Step 0：Tunnel Server 生命周期（幂等）

已健康运行则复用，否则跑 0.2-0.4。

**0.1 健康检查**（三项全绿 → 跳到 0.5）：
- 进程: `Get-Process -Name java | ? { $_.CommandLine -match 'arthas-tunnel-server' }`
- 端口: `Get-NetTCPConnection -LocalPort <ws>,<web> -State Listen`
- 认证: 用 `scripts/arthas_auth.py check` 验证 cookie 有效

**0.2 探测 tunnel_host**：
- 0.2a 用户已显式指定 → 进 0.2d
- 0.2b ssh-skill 在宿主机取候选 IP：`echo $SSH_CONNECTION; netstat -tn | awk '$4 ~ /:22$/ && /ESTABLISHED/ {print $5}' | cut -d: -f1 | sort -u`
- 0.2c 本机网卡匹配：`python {skill_dir}/scripts/tunnel_host_resolv.py <ip1> <ip2> ... --prefer <ssh_conn_ip>`（exit 0 = stdout 即 IP，1/2 = 需用户手动指定）
- 0.2d 实测出站可达：`timeout 5 bash -c '</dev/tcp/<tunnel_host>/<tunnel_ws_port>'` → REACHABLE/BLOCKED

**0.3 启动 tunnel server**（bind 0.0.0.0）：
```powershell
$java = "C:\Program Files\Java\jdk-21.0.10\bin\java.exe"  # 显式 JDK 路径（避坑 2）
Start-Process -FilePath $java -ArgumentList `
  "-Dserver.port=<web>", "-Dserver.address=0.0.0.0",
  "-Darthas.server.port=<ws>", "-Darthas.server.bind=0.0.0.0",
  "-Darthas.enable-detail-pages=true", "-jar", "<tunnel_server_jar>" `
  -RedirectStandardOutput  "output/tunnel-stdout.log" `
  -RedirectStandardError   "output/tunnel-stderr.log" `
  -WindowStyle Hidden -PassThru | Select-Object Id
```
记录 PID → `output/tunnel-server.pid`。

**0.4 健康检查**（**与 0.3 分开调用**，不得 sleep）：等 5s 后另起一条命令：
- `Get-Process -Id <pid>`
- `Get-NetTCPConnection -LocalPort <ws>,<web> -State Listen`
- 任一失败 → 读 `output/tunnel-stderr.log` → `ERR-TUNNEL-START-FAILED`

**0.5 提取 Basic Auth 密码**：
```powershell
$pw = (Get-Content output/tunnel-stderr.log | ? { $_ -match 'generated security password' } | Select -First 1) -replace '.*password:\s*'
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("arthas:$pw"))
```

**0.6 写入 `output/tunnel-server.json`**：`{pid, bind_address, ws_endpoint, web_endpoint, local_web, auth_user, auth_password, auth_header, session_cookie_file}` — 完整 schema 见 `references/output-config.md`。

**0.7 Spring Security 认证**：tunnel 4.2.0+ 默认启用 CSRF，Basic Auth 对 `/actuator/arthas` 等端点经常 302。统一走 cookie 认证：
```bash
python {skill_dir}/scripts/arthas_auth.py login http://127.0.0.1:<web> arthas <pw> --cookie-file output/cookies.txt
python {skill_dir}/scripts/arthas_auth.py check http://127.0.0.1:<web> --cookie-file output/cookies.txt
```
Basic Auth 仅作为备选。

**0.8 防火墙**：Windows 默认放行；云主机需控制台安全组入站放行 7777/8080。

## Step 1：SSH + 目标环境校验

```
1.1 <ssh-skill> <ssh_alias> "echo ok && whoami && hostname -I | awk '{print \$1}'"
    失败 → ERR-SSH-UNREACHABLE
1.2 "java -version 2>&1 | head -1"
1.3 [k8s] "kubectl version --client --short" + "kubectl get pod <pod> -n <ns> -o jsonpath='{.status.phase}'"
    非 Running → ERR-POD-NOT-RUNNING
1.4 agent 反连可达：
    "timeout 5 bash -c '</dev/tcp/<tunnel_host>/<ws>'" → REACHABLE/BLOCKED
```

## Step 2：Java 进程检测

```
[linux] <ssh-skill> <ssh_alias> "ps -eo pid,user,comm,args | grep '[j]ava' | grep -v '[a]rthas' | head -20"
[k8s]   <ssh-skill> <ssh_alias> "kubectl exec <pod> -n <ns> [-c <container>] -- sh -c 'ps -eo pid,user,comm,args | grep \"[j]ava\" | grep -v \"[a]rthas\" | head -20'"
唯一 → 自动选 java_pid；多个 → 用户选；无 → ERR-NO-JAVA-PROCESS
```

## Step 3：上传 Arthas

```
3.1 校验 <deploy_pkg> 存在，否则 ERR-DEPLOY-NOT-FOUND
3.2 "mkdir -p <remote_work_dir>"
3.3 md5 跳过：
      "[ -f <remote_work_dir>/arthas-bin.zip ] && md5sum <remote_work_dir>/arthas-bin.zip || echo not_found"
      不一致 → MSYS_NO_PATHCONV=1 <ssh-skill 上传> <deploy_pkg> <remote_work_dir>/arthas-bin.zip
3.4 "cd <remote_work_dir> && unzip -o arthas-bin.zip && ls -la arthas-boot.jar core/ lib/"
3.5 [linux] host_boot_jar=<remote_work_dir>/arthas-boot.jar
3.6 [k8s]   kubectl cp 进 Pod：
      "kubectl exec <pod> -n <ns> [-c <c>] -- mkdir -p <remote_work_dir>"
      "kubectl cp <remote_work_dir>/arthas-boot.jar <ns>/<pod>:<remote_work_dir>/arthas-boot.jar [-c <c>]"
```

## Step 4：启动 Arthas Agent

```
4.A agentId：
    linux:  <app_name>_<host_short>_<pid>
    k8s:    <app_name>_<pod_shortID>_<pid>

4.B 检查是否已在跑：
    "ps -eo pid,args | grep '[a]rthas-boot' || echo not_running"

4.C 启动命令（nohup 背景执行）：
    [linux]
      "nohup java -jar <host_boot_jar> <pid> --target-ip 0.0.0.0 \
        --tunnel-server 'ws://<tunnel_host>:<ws>/ws' \
        --agent-id '<AGENT_ID>' --app-name '<app_name>' \
        > <work_dir>/arthas.log 2>&1 & echo \$!"
    [k8s]
      "kubectl exec <pod> -n <ns> [-c <c>] -- sh -c \
        'nohup java -jar <remote_work_dir>/arthas-boot.jar <pid> --target-ip 0.0.0.0 \
         --tunnel-server \"ws://<tunnel_host>:<ws>/ws\" \
         --agent-id \"<AGENT_ID>\" --app-name \"<app_name>\" \
         > <remote_work_dir>/arthas.log 2>&1 &' && echo started"

4.D 等 8 秒验证注册（用 arthas_auth.py）：
    python {skill_dir}/scripts/arthas_auth.py wait <AGENT_ID> http://127.0.0.1:<web> \
      --timeout 30 --cookie-file output/cookies.txt
    失败 → 读远程 arthas.log 找原因 → ERR-AGENT-REGISTER-FAILED

4.E 验证 proxy 可用：
    python {skill_dir}/scripts/arthas_auth.py exec http://127.0.0.1:<web> <AGENT_ID> version --cookie-file output/cookies.txt
```

## Step 5：输出

生成 `output/arthas-config.json`（详细 schema 查 `references/output-config.md`）。结构：

```json
{
  "type": "arthas-tunnel-server",
  "mode": "linux|k8s",
  "tunnel_server": { "ws_endpoint", "web_endpoint", "local_web", "auth_user", "auth_password", "auth_header", "session_cookie_file", "pid" },
  "target":        { "ssh_alias", "namespace", "pod_name", "java_pid", "remote_work_dir", "boot_jar_in_target" },
  "agent":         { "agent_id", "app_name", "connected", "proxy_http_template" },
  "all_agents": [...],
  "status": "active"
}
```

## 清理

```
1. 停 agent：ps ... | grep arthas-boot ... | xargs kill
2. 从 all_agents 删除该 agentId；all_agents 为空时停 tunnel
3. Stop-Process -Id <pid> -Force; Remove-Item output/tunnel-server.pid
4. [可选] <ssh-skill> <ssh_alias> "rm -rf <remote_work_dir>"
```

## 错误码

| 错误码 | 含义 | 处理 |
|--------|------|------|
| ERR-SSH-UNREACHABLE | SSH 连不上宿主机 | 检查别名/网络 |
| ERR-NO-JAVA | 宿主机/Pod 无 JDK | 安装 JDK |
| ERR-DEPLOY-NOT-FOUND | deploy/ 缺 zip | 先下载或指定 deploy_pkg |
| ERR-POD-NOT-RUNNING | Pod 非 Running | 查 Pod 事件 |
| ERR-NETWORK-UNREACHABLE | agent 无法反连 tunnel | 出站 + 安全组 + VPN |
| ERR-UPLOAD-FAILED | SFTP/kubectl cp 失败 | 查磁盘/权限 |
| ERR-ARTHAS-START-FAILED | Agent 启动失败 | 看远程 arthas.log |
| ERR-PORT-CONFLICT | 端口被占 | 换端口 |
| ERR-TUNNEL-START-FAILED | tunnel server 启动失败 | 看 output/tunnel-stderr.log |
| ERR-AGENT-REGISTER-FAILED | agent 未在 tunnel 注册 | 看远程 arthas.log |
| ERR-AUTH-FAILED | Spring Security 认证失败 | 重跑 `arthas_auth.py login` |

## 强制规则

- **禁止裸 `ssh`/`scp`**，所有远程操作走 ssh-skill 标准接口
- **K8s 必须带 `-n <namespace>`**，多容器带 `-c <container>`
- **Agent 以目标 Java 同用户身份运行**
- **agent_id 全局唯一**：`<app_name>_<host_or_pod>_<pid>`
- **tunnel_host 反推双阶段**：ssh 拿远端 $SSH_CONNECTION + netstat 客户端 IP → `python {skill_dir}/scripts/tunnel_host_resolv.py` 与本机网卡匹配
- **tunnel_host 必须 agent 出站可达**
- **tunnel server 必须 bind 0.0.0.0**
- **本机入站 7777/8080 TCP 放行**（Windows 默认开放，云主机需控制台开）
- **tunnel server 禁止直接暴露公网**
- **优先 cookie 认证**；Basic Auth 仅作备选
- **Windows 本机 MSYS 调 ssh-upload 必须 `MSYS_NO_PATHCONV=1`**，宿主机内部命令不需要此前缀

## 常见陷阱（Windows 实测）

### 陷阱 1：Start-Process + Start-Sleep 阻塞

`Start-Process ... ; Start-Sleep -Seconds N` 会同步等 N 秒。

**正确**：启动立即返回，**另起一条命令**做健康检查（Get-Process + Get-NetTCPConnection）。等 5 秒用独立 bash 调用或 Job，绝不在启动那条命令里 sleep。

### 陷阱 2：JDK 版本不一致

Windows 默认 `java.exe` 常是 Oracle Java（javapath），`JAVA_HOME` 可能指 JDK 21。target 与 Arthas 用不同 JDK → `AttachNotSupportedException: jvm.dll not loaded by target process`。

**规避**：所有 `Start-Process java` 用 `-FilePath` 显式指定同一 JDK；检测已运行进程的 JDK：
```powershell
Get-Process java | % { [PSCustomObject]@{PID=$_.Id; CMD=(Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine} }
```
不一致 → kill 后统一重启。

### 陷阱 3：Start-Process redirect 不能指向同一文件

`-RedirectStandardOutput` 和 `-RedirectStandardError` 写同一文件 → 直接抛 `InvalidOperationException`，进程根本不启动。Step 0.3 已示范用 `tunnel-stdout.log` / `tunnel-stderr.log` 两个文件。

### 陷阱 4：看到 Id 就以为启动成功

Start-Process 输出了进程 ID 不代表真成功 —— 进程可能立刻崩溃。

**每次启动后必查**：
1. `Get-Process -Id <pid>` — 进程还在
2. `Get-NetTCPConnection -LocalPort <port> -State Listen` — 端口已监听
3. 任一失败 → 读 stderr 找真实错误，**不得假设成功继续**

## 参考文档

| 文件 | 何时读 |
|------|--------|
| `references/output-config.md` | 写 output/arthas-config.json 或下游 skill 想消费 tunnel 时 |
