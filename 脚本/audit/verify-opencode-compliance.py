"""verify-opencode-compliance.py

**强制合规 checklist 验证**（L7 anti-lazy）

opencode 每跑完一轮必留 6 个 artifact，boss 必查全有：

1. docker_ps.txt - docker ps 输出
2. docker_inspect.json - docker inspect 输出
3. code_reads.log - opencode 实际读的源文件清单
4. curl_attempts.log - 所有 curl 请求 + 状态码
5. 404_investigations.md - 每个 404 必查
6. poc_real_attack.log - 必含真攻击 payload

任一缺失 → 整轮 FAIL
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# ===== 项目 preset 加载 (用户 2026-06-13: 主流程不能过拟合 WebGoat) =====
# 与 cross-agent-50r.py 共用 preset.json, 切项目自动适配
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PRESETS_DIR = _REPO_ROOT / "项目"

def _load_preset() -> dict:
    p = os.environ.get("AGENTLOOP_PRESET")
    if not p:
        default = _PRESETS_DIR / "org.owasp.webgoat" / "preset.json"
        if default.exists():
            p = str(default)
        else:
            return {}
    pf = Path(p)
    if not pf.exists():
        return {}
    try:
        return json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return {}

_PRESET = _load_preset()
SESSION_COOKIE_NAME = _PRESET.get("sessionCookieName", "JSESSIONID")
DOCKER_CONTAINER = _PRESET.get("dockerContainer", "webgoat-local")

# Build Chinese via chr() to avoid encoding issues
def cn(cp_list):
    return "".join(chr(c) for c in cp_list)

CN_FATAL   = cn([0x81F4, 0x547D])  # 致命
CN_FAIL    = cn([0x5931, 0x8D25])  # 失败
CN_DOCKER  = cn([0x624B, 0x5668])  # 容器
CN_PASS_   = cn([0x901A, 0x8FC7])  # 通过
CN_NOT_FOUND = cn([0x672A, 0x627E, 0x5230])  # 末末
CN_LOGIN   = cn([0x767B, 0x5F55])  # 登录

REAL_ATTACK_PATTERNS = [
    # 真攻击 payload 特征
    r"OR\s+1=1",  # SQLi
    r"UNION\s+SELECT",  # SQLi
    r"<script>",  # XSS
    r"javascript:",  # XSS
    r"\.\./",  # 路径遍历
    r"/etc/passwd",  # LFI
    r"%00",  # Null byte
    r"\\x00",  # Null byte hex
    r"admin'\s*OR",  # Auth bypass
    r"<!\s*ENTITY",  # XXE
    r"\bSELECT\b.*\bFROM\b",  # SQLi
]

# **CIA 影响证据 pattern**（2026-06-13 用户工程原则：200 OK != 成功）
# C: Confidentiality - 数据泄露 (DB rows / file content / secrets)
# I: Integrity - 数据修改 (write/delete/update 成功)
# A: Availability - 服务中断 (DoS / crash)
CIA_EVIDENCE_PATTERNS = {
    "C_data_exfiltration": [
        r"root:[x*]:0:0",
        r"admin@[a-z]+\.com",
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
        r"password\s*[:=]\s*['\"]?\w+",
        r"api[_-]?key\s*[:=]",
        r"Bearer\s+[A-Za-z0-9]{20,}",
        r"flag\{[a-zA-Z0-9_-]+\}",
        r"secret\s*[:=]\s*['\"]\w+",
        r"\d+\s*rows?\s*in\s*set",
        r'"id":\s*\d+,\s*"password"',
        r"user[-_]name:\s*\w+",
        r"SessionId\s*[:=]\s*['\"]?[\w-]+",
    ],
    "I_data_modification": [
        r"(?:UPDATE|DELETE|INSERT|DROP)\s+.*\s+(?:SET|INTO|FROM|TABLE)",
        r"successfully\s+(?:deleted|updated|inserted)",
        r"status\":\s*\"(?:ok|success|completed|true)",
        r"\bmodified\s+\d+\s+rows?\b",
    ],
    "A_service_disruption": [
        r"(?:500|503)\s+(?:Internal|Service)\s+Error",
        r"stack\s*trace",
        r"NullPointerException",
        r"OutOfMemoryError",
        r"timeout\s+after\s+\d+",
    ],
}

# **Session 必须真用**（用户 2026-06-13 第 3 轮反馈：`<session>` 占位 = FAIL）
# Cookie 名从 preset.json 读 (用户 2026-06-13 第 N 轮反馈: 不绑死 JSESSIONID)
_SCN = re.escape(SESSION_COOKIE_NAME)
SESSION_PLACEHOLDER_PATTERNS = [
    rf"{_SCN}=<session>",
    rf"{_SCN}=\$SESSION",
    rf"{_SCN}=\{{session\}}",
    rf"Cookie:\s*{_SCN}=\?",
    rf"Cookie:\s*{_SCN}=TODO",
]
SESSION_REAL_USAGE_PATTERNS = [
    r"curl\s+.*-c\s+",
    r"curl\s+.*-b\s+",
    rf"--cookie\s+['\"]?{_SCN}=",
    rf"-H\s+['\"]Cookie:?\s*{_SCN}=",
    rf"Cookie:?\s*{_SCN}=[A-F0-9]{{16,}}",
]

CACHE_COVERAGE_MIN_RATIO = 0.5


REQUIRED_ARTIFACTS = [
    ("docker_ps.txt",          "docker ps output"),
    ("docker_inspect.json",    "docker inspect output"),
    ("code_reads.log",         "source files actually read"),
    ("curl_attempts.log",      "all curl requests + status codes"),
    ("404_investigations.md",  "404 investigation notes"),
    ("poc_real_attack.log",    "real attack payload evidence"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diag-dir", required=True, help="loop_audit/diag/ 目录")
    args = ap.parse_args()

    diag = Path(args.diag_dir)
    if not diag.exists():
        print(f"[FAIL] {diag} not found")
        return 1

    print(f"=== Compliance Check: {diag} ===\n")
    failed = 0
    passed = 0

    # Artifact 1-5: 文件存在性
    for fname, desc in REQUIRED_ARTIFACTS[:5]:
        p = diag / fname
        if p.exists() and p.stat().st_size > 0:
            print(f"  [OK] {fname} ({desc}) - {p.stat().st_size} bytes")
            passed += 1
        else:
            print(f"  [{CN_FAIL}] {fname} ({desc}) - {CN_NOT_FOUND}")
            failed += 1

    # Artifact 6: poc_real_attack.log 必须含真攻击 payload
    print()
    attack_log = diag / "poc_real_attack.log"
    if attack_log.exists() and attack_log.stat().st_size > 0:
        content = attack_log.read_text(encoding="utf-8", errors="replace")
        found_patterns = []
        for pat in REAL_ATTACK_PATTERNS:
            if re.search(pat, content, re.IGNORECASE):
                found_patterns.append(pat)
        if found_patterns:
            print(f"  [OK] poc_real_attack.log 含 {len(found_patterns)} 个真攻击 payload 特征:")
            for p in found_patterns:
                print(f"    - {p}")
            passed += 1
        else:
            print(f"  [{CN_FAIL}] poc_real_attack.log 不含真攻击 payload（只有测可达性）")
            failed += 1
    else:
        print(f"  [{CN_FAIL}] poc_real_attack.log - {CN_NOT_FOUND}")
        failed += 1

    # **CIA 证据检查**（用户 2026-06-13 工程原则：200 OK != 成功，必证明 CIA 影响）
    print(f"\n--- CIA 证据检查（200 OK ≠ 成功，必证明 CIA 影响）---")
    if attack_log.exists():
        cia_content = attack_log.read_text(encoding="utf-8", errors="replace")
        cia_found = {"C": [], "I": [], "A": []}
        for cia_type, patterns in CIA_EVIDENCE_PATTERNS.items():
            cia_letter = cia_type[0]
            for pat in patterns:
                if re.search(pat, cia_content, re.IGNORECASE):
                    cia_found[cia_letter].append(pat)
        total_cia = sum(len(v) for v in cia_found.values())
        if total_cia > 0:
            print(f"  [OK] CIA 证据: C={len(cia_found['C'])} I={len(cia_found['I'])} A={len(cia_found['A'])} (total {total_cia} 个)")
            for cia, evs in cia_found.items():
                if evs:
                    print(f"    {cia}: {evs[0]} (示例)")
            passed += 1
        else:
            print(f"  [{CN_FAIL}] 无 CIA 影响证据（200 OK 只测可达性 ≠ 成功）")
            failed += 1

        # **Session 必真用**（用户 2026-06-13：`<session>` 占位 = FAIL）
        placeholders = []
        real_usage = []
        for pat in SESSION_PLACEHOLDER_PATTERNS:
            if re.search(pat, cia_content):
                placeholders.append(pat)
        for pat in SESSION_REAL_USAGE_PATTERNS:
            if re.search(pat, cia_content):
                real_usage.append(pat)
        if placeholders and not real_usage:
            print(f"  [{CN_FAIL}] Session 是占位 ({len(placeholders)} 个 placeholder) 但无真 session 使用 ({len(real_usage)} 个 real)")
            for p in placeholders[:3]:
                print(f"      placeholder: {p}")
            failed += 1
        elif placeholders and real_usage:
            print(f"  [WARN] Session 同时含占位和真使用: placeholders={len(placeholders)} real={len(real_usage)} (boss 必人读)")
            passed += 0  # 不扣分但 warn
        elif real_usage and not placeholders:
            print(f"  [OK] Session 真使用: {len(real_usage)} 个 real pattern (无占位)")
            passed += 1
        else:
            print(f"  [INFO] 无 session 提及（可能 PoC 不需要登录）")
            passed += 0  # 不强制

    # docker_ps 必须含 "<container>" Up (从 preset.json 读)
    print()
    dps = diag / "docker_ps.txt"
    if dps.exists():
        content = dps.read_text(encoding="utf-8", errors="replace")
        if DOCKER_CONTAINER in content and "Up" in content:
            print(f"  [OK] docker_ps.txt 含 '{DOCKER_CONTAINER}' Up 验证")
            passed += 1
        else:
            print(f"  [{CN_FAIL}] docker_ps.txt 缺 '{DOCKER_CONTAINER}' Up 验证")
            failed += 1

    # code_reads 必须含 login.html + SecurityConfig.java
    crl = diag / "code_reads.log"
    if crl.exists():
        content = crl.read_text(encoding="utf-8", errors="replace")
        if "login.html" in content and "SecurityConfig" in content:
            print(f"  [OK] code_reads.log 含 login.html + SecurityConfig.java 必读项")
            passed += 1
        else:
            print(f"  [{CN_FAIL}] code_reads.log 缺 login.html 或 SecurityConfig.java 读取记录")
            failed += 1

    # curl_attempts 必须含非 404 状态码
    curl_log = diag / "curl_attempts.log"
    if curl_log.exists():
        content = curl_log.read_text(encoding="utf-8", errors="replace")
        # 找 200 / 302 / 401 / 403 等非 404
        non_404 = re.findall(r"HTTP[/ ]\d\.\d\s+(\d+)", content)
        non_404 = [c for c in non_404 if c != "404"]
        if non_404:
            print(f"  [OK] curl_attempts.log 含 {len(non_404)} 个非 404 响应")
            passed += 1
        else:
            print(f"  [{CN_FAIL}] curl_attempts.log 全是 404，无可达端点")
            failed += 1

    # 总结
    print(f"\n=== Result: {passed} OK, {failed} FAIL ===")
    if failed == 0:
        print(f"[OK] 全部合规，round 可 PASS")
        return 0
    else:
        print(f"[{CN_FAIL}] {failed} 项不合规 → opencode 偷懒 → 整轮 FAIL → 重跑")
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
