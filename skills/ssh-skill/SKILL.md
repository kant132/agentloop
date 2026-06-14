---
name: ssh-skill
description: SSH 远程执行子 skill。两重用途：Phase 1 拓扑发现 + Phase 5 PoC 后端验证。复用同一 SSH 连接。
---

# SSH 远程执行（ssh-skill）

> **两重用途**，不是"PoC 时连接"的单一工具。

## 一、Phase 1：拓扑发现

### 1.1 探测网络拓扑
```bash
# 登录后查看
ssh user@host 'ip route'             # 路由表
ssh user@host 'iptables -L'         # 防火墙规则
ssh user@host 'cat /etc/nginx/conf.d/*.conf'  # nginx 配置
ssh user@host 'netstat -tlnp'        # 监听端口
```

### 1.2 生成拓扑图
- 输出 `.drawio` 文件
- 节点：服务器、端口、路径、上游
- 边：网络流方向

### 1.3 写入 Memurai（走 memurai-cli.exe）
```
audit:{groupId}:commit:{commitHash}:env:reachability
  → {
      "ssh": "reachable" | "unreachable" | "degraded",
      "last_check": "...",
      "topology": "..."  # 拓扑 summary
    }
```

> 写入示例：`memurai-cli -h localhost -p 6379 SET audit:{gid}:commit:{ch}:env:reachability "<json>" EX 3600`

## 二、Phase 5：PoC 后端验证

### 2.1 复用 Phase 1 连接
- 不要重新建立 SSH
- 保持连接池，复用 keep-alive

### 2.2 PoC 执行
```bash
# 触发 payload
ssh user@host 'cd /opt/app && ./bin/curl-poc.sh'

# 查看日志
ssh user@host 'tail -f /var/log/app/app.log'

# 数据库连接（端口转发）
ssh -L 13306:localhost:3306 user@host
```

### 2.3 PoC 失败处理
- 连接失败 → 标 `degraded`
- 命令超时 → 标 `inconclusive` + 记录超时时间
- 返回非预期 → 记录实际输出，标 `confirmed_safe` 或 `confirmed_vuln`

## 三、必读

- `conduct/必读/02-环境感知.md`（PoC 三态门控）

## 四、依赖

- `~/.ssh/config` 配置目标主机
- 私钥文件权限 600
- 默认端口 22

## 五、限制

- 不要在 PoC 中执行破坏性命令
- 触发后立即回滚（如 drop table → 立即备份恢复）
- 详细 payload 必须经人工复核后才能执行
