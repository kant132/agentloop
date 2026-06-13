"""
batch-generate-route-reports.py

为 WebGoat 全部 269 个 routes 批量生成 per-endpoint 报告（P5.4 不变量）。

输出：
- D:/code/WebGoat-2025.3/loop_audit/external_endpoints/端点.jsonl  (P4.1 端点清单)
- D:/code/WebGoat-2025.3/loop_audit/routes/高风险端点/*.md          (致命/严重/中危)
- D:/code/WebGoat-2025.3/loop_audit/routes/中低险端点/*.md          (低危/无风险)
- D:/code/WebGoat-2025.3/loop_audit/routes/poc/*.md                 (高危 PoC 报告)

文件名规范（O3 / P5.6）：
  {危险等级}_{全限定类名}_{方法名}_{签名Hash}.md
  例：严重_com__owasp__webgoat__container__HammerHead_attack_a1b2c3d4.md

危险等级判定：
- 端点所属类含 lesson sink 关键词 → 严重
- 路径含 admin/pay/secret/key/reset → 中危
- 其余 → 低危/无风险
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path


# ============================================================== 配置

# 优先 CLI 参数 > 环境变量 > 默认 WebGoat
DEFAULT_DB = r"D:\code\WebGoat-2025.3\.codegraph\codegraph.db"
DEFAULT_OUT = Path(r"D:\code\WebGoat-2025.3\loop_audit")

# 关键词 → (CVSS 4.0 score, 漏洞类型)
# CVSS 4.0 评分范围 0.0-10.0
# 0.0=无 0.1-3.9=低 4.0-6.9=中 7.0-8.9=严重 9.0-10.0=致命
RISK_KEYWORDS = {
    # ── 致命 (9.0-10.0)
    "SqlInjection":       (9.3, "SQLI"),
    "SqlOnlyInput":       (9.3, "SQLI"),
    "VulnerableComponents":(9.8, "VULNERABLE_COMP"),  # 反序列化等
    "MissingFunctionAC":  (9.8, "AUTH_MISSING"),
    "InsecureLogin":      (9.1, "INSECURE_DESIGN"),
    "AuthBypass":         (9.8, "AUTH_BYPASS"),
    "BypassRestrictions": (9.1, "AUTH_BYPASS"),
    "PathTraversal":      (9.1, "PATH_TRAV"),
    "ProfileUpload":      (9.1, "PATH_TRAV"),
    "HijackSession":      (9.1, "AUTH_BYPASS"),
    # ── 严重 (7.0-8.9)
    "IDOR":               (8.1, "IDOR"),
    "SpoofCookie":        (8.1, "AUTH_BYPASS"),
    "JWT":                (7.5, "JWT_WEAK"),
    "XXE":                (8.6, "XXE"),
    "XSS":                (8.0, "XSS"),          # stored 默认
    "CrossSiteScripting": (8.0, "XSS"),
    "SSRF":               (8.6, "SSRF"),
    "CSRF":               (7.5, "CSRF"),
    "HtmlTampering":      (7.5, "HTML_TAMPER"),
    # ── 中 (4.0-6.9)
    "Crypto":             (5.9, "CRYPTO_WEAK"),
    "Hash":               (5.9, "CRYPTO_WEAK"),
    "Encoding":           (5.3, "WEAK_ENC"),
    "SecurePasswords":    (5.3, "WEAK_PWD"),
    "PasswordReset":      (6.5, "PWD_RESET"),
    "ClientSideFiltering":(6.1, "CLIENT_FILTER"),
    "LogSpoofing":        (5.3, "LOG_SPOOF"),
    "ChromeDevTools":     (4.3, "DEBUG_LEAK"),
    "HttpProxies":        (5.3, "INSECURE_TRANSPORT"),
}

PATH_HIGH_RISK = ["admin", "pay", "secret", "key", "reset", "delete", "upload", "config"]

# 通用 Java 框架关键词（适配 yudao/ruoyi 等通用项目）
GENERIC_RISK_KEYWORDS = {
    # 致命
    "DataSource":        (9.1, "INJECTION_SINK"),
    "JdbcTemplate":      (9.3, "SQLI"),
    "executeSql":        (9.3, "SQLI"),
    "createQuery":       (9.3, "SQLI"),
    "Runtime.getRuntime":(9.8, "RCE"),
    "ProcessBuilder":    (9.8, "RCE"),
    "readObject":        (9.8, "DESER"),
    "XMLDecoder":        (9.1, "XXE"),
    "openConnection":    (8.6, "SSRF"),
    "new URL":           (8.6, "SSRF"),
    # 严重
    "AdminController":   (8.1, "ADMIN_API"),
    "UserController":    (7.5, "USER_API"),
    "PayController":     (8.6, "PAY_API"),
    "OrderController":   (7.5, "ORDER_API"),
    "AuthController":    (8.1, "AUTH_API"),
    "delete":            (7.5, "DELETE_OP"),
    "remove":            (7.5, "DELETE_OP"),
    "upload":            (7.5, "UPLOAD_OP"),
    "export":            (6.5, "EXPORT_OP"),
    "import":            (6.5, "IMPORT_OP"),
    # 中
    "Controller":        (5.0, "REST_API"),
    "Service":           (4.0, "SVC_API"),
    "download":          (5.0, "DOWNLOAD_OP"),
    "getById":           (4.0, "READ_API"),
    "list":              (3.5, "LIST_API"),
    "page":              (3.5, "LIST_API"),
    "query":             (3.5, "QUERY_API"),
    "update":            (4.5, "UPDATE_OP"),
    "save":              (4.0, "WRITE_OP"),
    "create":            (4.5, "CREATE_OP"),
}


def cvss_to_severity(score: float) -> str:
    """CVSS 4.0 score → 5 等级中文"""
    if score >= 9.0:
        return "致命"
    if score >= 7.0:
        return "严重"
    if score >= 4.0:
        return "中"
    if score > 0.0:
        return "低"
    return "无"


# ============================================================== 工具

def sig_hash(route_id: str) -> str:
    return hashlib.sha256(route_id.encode()).hexdigest()[:8]


def class_fqcn(file_path: str) -> str:
    """从 file_path 提取 FQCN：src/main/java/com/foo/Bar.java → com.foo.Bar"""
    m = re.search(r"src/main/java/(.+?)\.java", file_path)
    if m:
        return m.group(1).replace("/", ".")
    return file_path.replace("/", ".").replace(".java", "")


def filename_safe(s: str) -> str:
    """FQCN 中 . → __  (Windows 文件名不允许 .)"""
    return s.replace(".", "__")


def method_name(route: dict) -> str:
    """从 route 生成伪方法名（route.kind 不直接是 method）"""
    name = route["name"] or "root"
    # 例 "GET /api/users" → "get_api_users"
    safe = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return safe[:50] or "root"


def classify(route: dict) -> tuple:
    """返回 (cvss_score, 危险等级, 漏洞类型, 描述)"""
    fqcn = class_fqcn(route["file_path"])
    name = route["name"] or ""
    path_part = name.split(" ", 1)[1] if " " in name else ""

    # 1. WebGoat 教学项目专用关键词
    for kw, (score, vt) in RISK_KEYWORDS.items():
        if kw in fqcn or kw in name:
            return score, cvss_to_severity(score), vt, f"命中 WebGoat 关键词 {kw}：{name}"

    # 2. 通用 Java 框架关键词（覆盖 yudao/ruoyi/Spring 等）
    for kw, (score, vt) in GENERIC_RISK_KEYWORDS.items():
        if kw in fqcn or kw in name:
            return score, cvss_to_severity(score), vt, f"命中通用关键词 {kw}：{name}"

    # 3. 路径
    for kw in PATH_HIGH_RISK:
        if kw in path_part.lower():
            score = 6.5
            return score, cvss_to_severity(score), "PATH_RISK", f"高风险路径含 {kw}：{name}"

    return 0.0, "无", "NONE", f"普通端点：{name}"


def extract_call_chain(db_path: str, file_path: str, start_line: int, depth: int = 8) -> list:
    """查同 file:line 的 method 节点的 calls 链（CTE RECURSIVE）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 找起点 method
    row = cur.execute(
        "SELECT id, qualified_name FROM nodes WHERE kind='method' AND file_path=? AND start_line=?",
        (file_path, start_line),
    ).fetchone()
    if not row:
        conn.close()
        return []

    method_id = row["id"]
    qn = row["qualified_name"]

    # 递归
    rows = cur.execute("""
        WITH RECURSIVE chain(id, qn, depth, path) AS (
            SELECT n.id, n.qualified_name, 0, '|' || n.id
            FROM nodes n WHERE n.id = ?
            UNION ALL
            SELECT callee.id, callee.qualified_name, c.depth + 1, c.path || '|' || callee.id
            FROM chain c
            JOIN edges e ON e.source = c.id AND e.kind = 'calls'
            JOIN nodes callee ON callee.id = e.target AND callee.kind = 'method'
            WHERE c.depth < ? AND instr(c.path, '|' || callee.id || '|') = 0
        )
        SELECT id, qn, depth FROM chain ORDER BY depth
    """, (method_id, depth)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def make_report(route: dict, chain: list, cvss: float, severity: str, vuln_type: str, desc: str) -> str:
    fqcn = class_fqcn(route["file_path"])
    method = method_name(route)
    sh = sig_hash(route["id"])
    http_method, _, path = (route["name"] or "GET /").partition(" ")

    chain_lines = []
    for c in chain[:15]:
        chain_lines.append(f"  - d={c['depth']}  `{c['qn']}`")
    chain_block = "\n".join(chain_lines) if chain_lines else "  - (无调用链 — 可能为叶子方法)"

    return f"""# 端点调用链分析报告 — {http_method} {path}

## 元信息

| 项 | 值 |
|---|---|
| **外部端点** | `{http_method} {path}` |
| **全限定类名** | `{fqcn}` |
| **方法名** | `{method}` |
| **签名 Hash** | `{sh}` |
| **危险等级（5级）** | **{severity}** |
| **CVSS 4.0 评分** | **{cvss}** |
| **漏洞类型** | `{vuln_type}` |
| **源文件** | `{route['file_path']}:{route['start_line']}` |
| **业务说明** | {desc} |

## 1. 执行摘要

{desc}

**综合定级**：**{severity}**（{vuln_type}，CVSS {cvss}）

**链节点数**：{len(chain)}
**链最大深度**：{max((c['depth'] for c in chain), default=0)}

## 2. 审计 Checklist

| 检查项 | 状态 |
|--------|------|
| 认证 | 待人工 |
| 鉴权 | 待人工 |
| 注入 | 见漏洞类型 |
| 业务逻辑 | 见漏洞类型 |
| PoC 验证 | 待 PoC 阶段 |

## 3. 调用链

```
{chain_block}
```

## 4. 评分

- 污点追踪（30）：18（程序化生成，待人工细化）
- 业务/安全漏洞（25）：{ {"致命":25, "严重":22, "中":18, "低":12, "无":8}.get(severity, 10) }
- PoC 清晰度（25）：待 PoC

**本轮总分**：占位（待人工完善后 ≥ 85 三轮 finished）
"""


def make_poc_report(route: dict, severity: str, vuln_type: str, cvss: float) -> str:
    fqcn = class_fqcn(route["file_path"])
    method = method_name(route)
    sh = sig_hash(route["id"])
    http_method, _, path = (route["name"] or "GET /").partition(" ")

    return f"""# PoC 验证报告 — {http_method} {path}

## 元信息

| 项 | 值 |
|---|---|
| **验证状态** | **待确认**（自动生成占位） |
| **问题等级** | {severity} |
| **CVSS 4.0** | {cvss:.1f} |
| **漏洞类型** | {vuln_type} |
| **关联 finding** | `{severity}_{cvss:.1f}_{vuln_type}_{filename_safe(fqcn)}_{method}_{sh}.md` |
| **外部端点** | `{http_method} {path}` |
| **sink** | (待人工定位) |

## 1. 验证步骤（待人工执行）

```
# 1. 启动 WebGoat
./mvnw spring-boot:run

# 2. 触发端点
curl -X {http_method} "http://localhost:8080{path}"

# 3. 观察响应 / 日志
tail -f logs/webgoat.log
```

## 2. 验证状态

**待人工确认** — 此 PoC 报告由批量生成器自动产生，需人工根据实际漏洞类型填充：
- 是问题 / 非问题 / 暂时无法确认

## 3. 评分（占位）

- 污点追踪（30）：待 PoC
- 业务漏洞（25）：待 PoC
- PoC 清晰度（25）：待 PoC
"""


# ============================================================== 主流程

def main():
    # 参数解析：DB / OUT 路径
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("DB_PATH", DEFAULT_DB))
    ap.add_argument("--out", default=os.environ.get("OUT_ROOT", str(DEFAULT_OUT)))
    args = ap.parse_args()

    DB = args.db
    OUT = Path(args.out)
    EP_DIR = OUT / "external_endpoints"
    HI_DIR = OUT / "routes" / "高风险端点"
    LO_DIR = OUT / "routes" / "中低险端点"
    POC_DIR = OUT / "routes" / "poc"

    for d in [EP_DIR, HI_DIR, LO_DIR, POC_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    print(f"[+] DB: {DB}")
    print(f"[+] OUT: {OUT}")

    # 1. 枚举 routes
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    routes = cur.execute("""
        SELECT id, name, qualified_name, file_path, start_line
        FROM nodes WHERE kind='route'
        ORDER BY file_path, start_line
    """).fetchall()
    conn.close()
    routes = [dict(r) for r in routes]
    print(f"[+] Found {len(routes)} routes")

    # 2. 写端点 jsonl（P4.1）
    ep_jsonl = EP_DIR / "端点.jsonl"
    with open(ep_jsonl, "w", encoding="utf-8") as f:
        for r in routes:
            f.write(json.dumps({
                "id": r["id"],
                "endpoint": r["name"],
                "fqn": class_fqcn(r["file_path"]),
                "file_path": r["file_path"],
                "start_line": r["start_line"],
                "sig_hash": sig_hash(r["id"]),
            }, ensure_ascii=False) + "\n")
    print(f"[+] Wrote {ep_jsonl}")

    # 3. 批量生成报告
    counters = Counter()
    t0 = time.time()
    SCRIPT_TIMEOUT = 300
    for i, route in enumerate(routes):
        if time.time() - t0 > SCRIPT_TIMEOUT:
            print(f"[WARN] 超 {SCRIPT_TIMEOUT}s 强制退出（已生成 {i}/{len(routes)}）")
            break
        cvss, severity, vuln_type, desc = classify(route)
        counters[severity] += 1

        chain = extract_call_chain(DB, route["file_path"], route["start_line"], depth=8)

        fqcn_safe = filename_safe(class_fqcn(route["file_path"]))
        method = method_name(route)
        sh = sig_hash(route["id"])
        cvss_str = f"{cvss:.1f}"
        filename = f"{severity}_{cvss_str}_{vuln_type}_{fqcn_safe}_{method}_{sh}.md"

        if severity in ("致命", "严重"):
            target = HI_DIR
        else:
            target = LO_DIR

        (target / filename).write_text(make_report(route, chain, cvss, severity, vuln_type, desc), encoding="utf-8")

        if severity in ("致命", "严重"):
            poc_name = f"待确认_{severity}_{cvss_str}_{vuln_type}_{fqcn_safe}_{method}_sink_round001.md"
            (POC_DIR / poc_name).write_text(make_poc_report(route, severity, vuln_type, cvss), encoding="utf-8")

    elapsed = time.time() - t0
    print(f"\n[+] Done in {elapsed:.1f}s")
    print(f"[+] 等级分布: {dict(counters)}")
    print(f"[+] 高风险 (致命+严重): {len(list(HI_DIR.glob('*.md')))} / 中低险 (中+低+无): {len(list(LO_DIR.glob('*.md')))}")
    print(f"[+] PoC 占位: {len(list(POC_DIR.glob('*.md')))}")


if __name__ == "__main__":
    main()
