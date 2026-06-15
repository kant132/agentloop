---
name: poc-verify
description: "漏洞 PoC 验证子 agent 专用规范。给定漏洞发现，构造并执行 PoC，输出完整执行过程、CVSS 3.1 评分和证据。支持工具：curl、arthas、SSH。触发词：'验证漏洞'、'poc 验证'、'verify vulnerability'、'漏洞是否真实存在'。"
---

## Integration with New Architecture (2026-06-15)

### Dispatch Context

PoC verifier is now called by **poc-monitor.py** daemon (not boss or supervisor). Daemon monitors `Memurai {groupId}:audit:finding:*:final` for `poc_status == pending` and dispatches verifier as subprocess.

Verifier receives:

```json
{
  "finding_id": "auto-uuid",
  "chain_id": "abc123...",
  "fqn": "org.owasp.webgoat.lessons.sqlinjection.advanced.SqlInjectionLesson6b.completed",
  "vuln_type": "SQL_INJECTION",
  "severity": "high",
  "evidence": {
    "payload": "' UNION SELECT username, password FROM users --",
    "endpoint": "/SqlInjection/attack6b",
    "http_method": "POST"
  },
  "group_id": "org.owasp.webgoat",
  "project_root": "D:\\code\\WebGoat-2025.3",
  "loop_audit_dir": "D:\\agentloop\\projects\\org.owasp.webgoat\\loop_audit"
}
```

### Finding Source

`poc-monitor.py` polls:
```python
for key in m.scan(f"{group_id}:audit:finding:*:final"):
    val = json.loads(m.get(key))
    if val.get("poc_status") == "pending" and val.get("severity") in ("critical", "high"):
        dispatch_poc_verify(val)
```

### Output Contract

Write back to `Memurai {group_id}:audit:finding:{chain_id}:poc_result`:

```json
{
  "poc_verified": true,
  "poc_status": "verified",
  "cvss_score": 9.1,
  "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N",
  "execution_log": "POST /SqlInjection/attack6b with userid='; DROP TABLE ...; -- → 200 OK, response contains 'admin, p@ssword123'",
  "evidence_path": "loop_audit/poc/abc123_poc.json",
  "timestamp": "2026-06-15T..."
}
```

### Preset Knowledge (read at start)

- `projects/_template/06-通用安全知识.md` — 10 大类 sink + 6 类业务逻辑
- `projects/_template/07-Sink表.json` — 39 sinks with exploit patterns
- `projects/_template/08-Sanitizer表.json` — 28 sanitizers (use to confirm whether an apparent sanitizer actually blocks the specific attack)

### Test Target Output

All PoC results go to `D:\agentloop\projects\{groupId}\loop_audit\poc\`. Per user spec: "所有测试相关的输出到 D:\agentloop\项目\groupId下". Note: `项目` was renamed to `projects`, but the path intent is the same.

### Self-Evolution Integration

Verified findings feed back through supervisor's `{groupId}:sup:exp:*` mechanism. Next round's experts use `knowledge.json` findings to:
- Skip already-verified vulnerabilities (avoid duplicate work)
- Learn from successful PoC payloads (e.g., known-working SQLi patterns)
- Avoid known-false-positive patterns

### Hard Constraints

- **No fabrication**: If PoC cannot actually trigger the vulnerability (404, 403, timeout), MUST set `poc_verified: false` with realistic failure log — never lie about success.
- **Atomic writes**: Write PoC result to `{finding_id}.tmp` then `os.replace` to final filename.
- **Timeout**: 300s per PoC. If exceeded, mark `poc_verified: false` with `"reason": "timeout"`.

# 漏洞 PoC 验证

## 验证工具

- **curl**：前端可直接验证的漏洞（HTTP 请求触发）
- **arthas**：需要动态调试时（watch/trace/stack 方法调用，观察运行时行为）
- **SSH + arthas**：curl 验证无果、原因不明时，通过 SSH 到后端查看日志，或通过 arthas 动态调试分析失败原因

## 验证流程（4 步强制执行，不可跳过）

### 1. 环境快照

记录：OS、运行时版本、服务版本、关键配置。
没有环境信息 → 停止，要求上游补充。

### 2. PoC 构造与执行

- 实际执行，捕获完整 stdout + stderr
- 失败时：记录失败原因 + 尝试过的变体 + 用 arthas 观察方法调用链，定位失败环节
- 若 curl 触发无法判断结果 → SSH 到后端查日志或 arthas watch 目标方法的输入输出

**禁止行为：**
- 只贴代码不执行
- 只说"已验证"但没有执行输出
- 失败时不分析原因

### 3. CVSS 3.1 影响评估

对 C / I / A 三个维度分别评分（None / Low / High），每个维度必须给出：
- 分值
- 判定理由（一句话）
- 触发证据（执行输出中的关键片段，或"无证据"）

### 4. 验证结论

一行：漏洞 [存在 / 不存在 / 无法确认]，原因一句话。

## 输出格式

### 环境
[OS / 版本 / 配置]

### 执行记录
命令：[完整命令]
输出：[完整输出]
arthas 观察：[如有]
失败分析：[如有]

### CVSS 3.1
| 维度 | 评分 | 理由 | 证据 |
|------|------|------|------|
| C    |      |      |      |
| I    |      |      |      |
| A    |      |      |      |

### 结论
[存在/不存在/无法确认] + [一句话原因]