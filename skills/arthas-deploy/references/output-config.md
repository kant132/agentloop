# Arthas Deploy 输出配置规范

## arthas-config.json（完整 JSON Schema）

```json
{
  "type": "arthas-tunnel-server",
  "mode": "linux | k8s",
  "tunnel_server": {
    "ws_endpoint": "string — 公网/VPN 可达的 agent 反连 WebSocket 地址",
    "web_endpoint": "string — 公网/VPN 可达的 HTTP 地址（下游 skill 用这个访问 proxy）",
    "local_web": "string — 本机 127.0.0.1 等价地址",
    "local_actuator": "string — 本机 actuator",
    "auth_user": "string — Basic Auth 用户名（默认 arthas）",
    "auth_password": "string — Basic Auth 密码（tunnel server 启动日志中提取的 UUID）",
    "auth_header": "string — 完整 Authorization 值：'Basic <base64(user:pwd)>'",
    "console_url": "string — Web Console URL",
    "actuator": "string — 远程可达 actuator URL",
    "pid": "string — tunnel server 进程 PID",
    "jar": "string — tunnel server jar 路径",
    "log": "string — tunnel server 日志路径",
    "session_cookie_file": "string — cookie 文件路径（用于 session 认证）"
  },
  "target": {
    "mode": "string — linux / k8s",
    "ssh_alias": "string — K8s 宿主机 SSH 别名",
    "host_internal_ip": "string — 宿主机内网 IP",
    "namespace": "string — (mode=k8s) K8s 命名空间",
    "pod_name": "string — (mode=k8s) Pod 名",
    "container": "string — (mode=k8s, 多容器时) 容器名",
    "java_pid": "string — 目标 Java PID",
    "java_user": "string — Java 进程运行用户",
    "java_command": "string — Java 启动命令行",
    "java_version": "string — JDK 版本",
    "remote_work_dir": "string — 宿主机/Pod 内的工作目录",
    "boot_jar_in_target": "string — arthas-boot.jar 在目标上的绝对路径"
  },
  "agent": {
    "agent_id": "string — 全局唯一，tunnel server 路由的关键字段",
    "app_name": "string — 分组标签",
    "version": "string — Arthas 版本",
    "connected": "boolean — agent 是否在线",
    "host": "string — agent HTTP API 主机名/IP（从 tunnel /actuator/arthas 提取，用于直接调用 agent HTTP API）",
    "agent_http_port": "number — agent HTTP API 端口（默认 8563）"
  },
  "all_agents": ["string — tunnel server 下所有已活跃 agentId 列表"],
  "timestamp": "string — ISO8601 部署时间",
  "status": "active | stopped | error"
}
```

## tunnel-server.json（轻量元数据）

```json
{
  "pid": "number — tunnel server 进程 PID",
  "bind_address": "0.0.0.0",
  "ws_endpoint": "string — ws://<tunnel_host>:<ws_port>/ws",
  "web_endpoint": "string — http://<tunnel_host>:<web_port>",
  "local_web": "string — http://127.0.0.1:<web_port>",
  "auth_user": "string",
  "auth_password": "string — UUID from startup log",
  "auth_header": "string — Basic <base64>",
  "session_cookie_file": "string — cookie 文件路径（用于 session 认证）"
}
```

## 下游 skill 消费指引（HTTP-only）

### Agent HTTP API（直接调用，推荐）

Arthas 4.x agent 内置 HTTP API（端口 8563），支持直接的 JSON-RPC 风格调用。

**API 地址**：`http://{agent.host}:{agent.agent_http_port}/api`  
**认证**：agent HTTP API 默认**无需认证**（本机访问），由 tunnel server 的 session cookie 控制访问权限。

```python
import json, requests

cfg = json.load(open("output/arthas-config.json"))
agent = cfg["agent"]
tunnel = cfg["tunnel_server"]

def arthas(cmd: str, agent_id: str = None) -> dict:
    """调用 Arthas 命令，返回解析后的 JSON 响应"""
    aid = agent_id or agent["agent_id"]
    # agent.host 从 tunnel server /actuator/arthas 提取，默认为 127.0.0.1
    agent_host = agent.get("host", "127.0.0.1")
    agent_port = agent.get("agent_http_port", 8563)
    url = f"http://{agent_host}:{agent_port}/api"
    
    with requests.Session() as s:
        # Agent API 本身无需认证（端口 8563）
        r = s.post(url, json={
            "action": "exec",
            "command": cmd,
            "execTimeout": 30000
        }, timeout=35)
        return r.json()
```

### Session Cookie 认证（仅 tunnel server 层面需要）

tunnel server 的 actuator 端点仍需 session cookie 认证（用于 agent 注册查询）。

如果 cookie 文件不存在或已失效，需要先登录：

```python
import requests

cfg = json.load(open("output/arthas-config.json"))
tunnel = cfg["tunnel_server"]
cookie_file = tunnel.get("session_cookie_file", "cookies.txt")

with requests.Session() as s:
    # 1. 获取 CSRF token
    r = s.get(f"{tunnel['local_web']}/login")
    import re
    csrf = re.search(r'value="([^"]+)"', r.text)
    if csrf:
        csrf = csrf.group(1)

    # 2. 登录获取 session
    s.post(f"{tunnel['local_web']}/login",
           data={"username": tunnel["auth_user"],
                 "password": tunnel["auth_password"],
                 "_csrf": csrf})

    # 3. 保存 cookie（用于查询 tunnel server actuator）
    s.cookies.save(cookie_file)

    # 4. Agent 命令执行（不需要 cookie，直接调 agent HTTP API）
    import json
    agent = cfg["agent"]
    agent_host = agent.get("host", "127.0.0.1")
    agent_port = agent.get("agent_http_port", 8563)
    url = f"http://{agent_host}:{agent_port}/api"
    r = s.post(url, json={"action": "exec", "command": "version", "execTimeout": 10000})
    print(json.dumps(r.json(), indent=2))
```

### 审计典型命令

```python
arthas("sc com.example.security.JwtFilter")
arthas("jad com.example.security.JwtFilter doFilter")
arthas("watch com.example.UserController login '{params,returnObj}' -x 3 -n 3")
arthas("stack com.example.UserController login -n 1")
arthas("classloader -t")
arthas("tt -t com.example.UserController login -n 3")
arthas("thread -n 3")
arthas("heapdump --live")
arthas("profiler start --event cpu --duration 60 --file /tmp/cpu.jfr")
```

### 多 agent 遍历

```python
for aid in cfg["all_agents"]:
    print(f"{aid}: {arthas('version', agent_id=aid).strip()}")
```

### 列出在线 agent

```python
r = requests.get(
    f"{cfg['tunnel_server']['web_endpoint']}/actuator/arthas",
    headers={"Authorization": cfg["tunnel_server"]["auth_header"], "Accept": "application/json"},
    timeout=10
)
print(r.json())
```

## 通信矩阵

| 方向 | 协议 | 端口 | 在哪开放 | 用途 |
|------|------|------|----------|------|
| 入站 | HTTP | `tunnel_web_port` (默认 8080) | **本机** | Web UI / API / proxy / actuator |
| 入站 | WebSocket | `tunnel_ws_port` (默认 7777) | **本机** | 远程 agent 反连 |
| 出站 | WebSocket | `tunnel_ws_port` | K8s 宿主机 | agent 注册回 hub |
| — | — | — | **K8s 宿主机不需要任何入站端口** | — |

## 状态管理

| status 取值 | 含义 | 触发时机 |
|--------|------|------|
| `active` | tunnel + agent 都在线 | 部署成功 |
| `stopped` | 全清理完成 | 用户主动停止 |
| `error` | agent 掉线 / tunnel 崩溃 | 网络异常 / Pod 重启 |

下游 skill 读取前必须验证：
```python
assert cfg["status"] == "active"
assert cfg["agent"]["connected"] is True
```

## 常用命令 URL 编码对照表

| 命令 | 编码后 `cmd` 参数 |
|------|------------------|
| `version` | `version` |
| `dashboard -n 1` | `dashboard%20-n%201` |
| `sc com.example.MyFilter` | `sc%20com.example.MyFilter` |
| `jad com.example.MyFilter doFilter` | `jad%20com.example.MyFilter%20doFilter` |
| `watch C.login '{params,returnObj}' -n 3` | `watch%20C.login%20%27%7Bparams%2CreturnObj%7D%27%20-n%203` |
| `thread` | `thread` |
| `jvm` | `jvm` |
| `classloader -t` | `classloader%20-t` |
| `ognl '@System@getProperty("java.version")'` | `ognl%20%40System%40getProperty%28%22java.version%22%29` |
| `heapdump --live` | `heapdump%20--live` |
| `profiler start --event cpu --duration 60` | URL 编码即可 |
