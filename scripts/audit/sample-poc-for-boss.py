"""sample-poc-for-boss.py - 老板 20% OUTPUT + 5% EXECUTION 双抽样 (user 2026-06-13)

Two-pass boss sampling:
  1. 20% output files (.md) - 读内容判定 OK/FAKE/CONTRADICTION
  2. 5% execution process (opencode subagent sessions) - 读过程判定 REAL_WORK/TEMPLATE/HUNG
"""
import argparse
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

# Build all Chinese strings via chr() to avoid encoding issues
def cn(cp_list):
    return "".join(chr(c) for c in cp_list)

# 验证状态 (PoC 文件名的第一段, 用户 P5.11 规范)
VS_ISSUE      = cn([0x662F, 0x95EE, 0x9898])
VS_NOT_ISSUE  = cn([0x975E, 0x95EE, 0x9898])
VS_UNCERTAIN  = cn([0x6682, 0x65F6, 0x65E0, 0x6CD5, 0x786E, 0x8BA4])
VS_FAILED     = cn([0x5931, 0x8D25])

PFX_ISSUE      = VS_ISSUE + "_"
PFX_NOT_ISSUE  = VS_NOT_ISSUE + "_"
PFX_UNCERTAIN  = VS_UNCERTAIN + "_"
PFX_FAILED     = VS_FAILED + "_"
PFX_SUCCESS    = cn([0x6210, 0x529F]) + "_"

VERIFY_STATUS_KEYS = {
    VS_ISSUE:     PFX_ISSUE,
    VS_NOT_ISSUE: PFX_NOT_ISSUE,
    VS_UNCERTAIN: PFX_UNCERTAIN,
    VS_FAILED:    PFX_FAILED,
}

CN_VERIFY = cn([0x9A8C, 0x8BC1, 0x72B6, 0x6001])
CN_FAKE_MARKER = cn([0x7F3A, 0x5927, 0x5145, 0x8DA3, 0x8F93, 0x5165, 0x9A8C, 0x8BC1, 0x548C, 0x8F93, 0x51FA, 0x7F16, 0x7801])

OPENCODE_DB = r"C:\Users\Administrator\.local\share\opencode\opencode.db"


def extract_verify_status(filename):
    for status, prefix in VERIFY_STATUS_KEYS.items():
        if filename.startswith(prefix):
            return status
    if filename.startswith(PFX_SUCCESS):
        return VS_ISSUE
    return None


def extract_key_lines(text):
    result = {"verify_status": "", "actual_response": "", "root_cause": ""}
    in_response = False
    in_cause = False
    for line in text.splitlines():
        if CN_VERIFY in line:
            result["verify_status"] = line.strip()
        if "## 实际响应" in line:
            in_response = True
            in_cause = False
        elif "## 根因分析" in line or "## 根因" in line:
            in_cause = True
            in_response = False
        elif line.startswith("## "):
            in_response = False
            in_cause = False
        if in_response and line.strip() and not line.startswith("#"):
            result["actual_response"] = line.strip()
        if in_cause and line.strip() and not line.startswith("#"):
            result["root_cause"] = line.strip()
    return result


def sample_process_sessions(round_label, output_path, rate=0.05, seed=42):
    """5% 抽样 opencode subagent sessions - boss 读过程判定 REAL_WORK/TEMPLATE/HUNG"""
    if not Path(OPENCODE_DB).exists():
        return {"error": f"opencode.db not found: {OPENCODE_DB}"}
    conn = sqlite3.connect(OPENCODE_DB)
    cur = conn.cursor()
    # 最近 50 个 session（含 WGB-R 和 Sisyphus-Junior）
    sessions = cur.execute("""
        SELECT id, title, datetime(time_created/1000, 'unixepoch'), tokens_input, tokens_output
        FROM session
        WHERE title NOT LIKE 'PyTest%' AND title NOT LIKE 'Test%' AND title NOT LIKE 'PosFirst%'
        ORDER BY time_created DESC LIMIT 50
    """).fetchall()
    conn.close()
    if not sessions:
        return {"error": "no sessions found"}
    random.seed(seed + 1)
    n_target = max(1, int(len(sessions) * rate))
    sampled = random.sample(sessions, min(n_target, len(sessions)))
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Boss 过程抽样 - {round_label}\n\n")
        f.write("## sample rule\n")
        f.write(f"- target rate: {rate*100:.0f}%\n")
        f.write(f"- actual sample: {len(sampled)}/{len(sessions)} = {len(sampled)/len(sessions)*100:.1f}%\n\n")
        f.write("## boss must do\n")
        f.write("1. read each session's tool calls (opencode.db part table)\n")
        f.write("2. mark: REAL_WORK / TEMPLATE / HUNG\n")
        f.write("3. >= 5% TEMPLATE -> round FAIL\n\n")
        f.write("---\n\n")
        for sid, title, ts, inp, outp in sampled:
            f.write(f"## Session: `{title}`\n\n")
            f.write(f"- id: `{sid[:30]}...`\n")
            f.write(f"- ts: {ts}\n")
            f.write(f"- tokens: in={inp} out={outp}\n")
            f.write(f"- **boss judgment**: `[ ]` (REAL_WORK / TEMPLATE / HUNG)\n\n")
            f.write("---\n\n")
    return {"sessions_sampled": len(sampled), "total_sessions": len(sessions)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poc-dir", required=True)
    ap.add_argument("--sample-rate", type=float, default=0.20,
                    help="OUTPUT file sample rate (default 20 percent, user 2026-06-13)")
    ap.add_argument("--process-rate", type=float, default=0.05,
                    help="EXECUTION process sample rate (default 5 percent)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--round-label", default="Round {N}")
    args = ap.parse_args()

    poc_dir = Path(args.poc_dir)
    files = list(poc_dir.glob("*.md"))
    print(f"[+] Total PoC files: {len(files)}")

    # 20% output 抽样 (user 2026-06-13)
    random.seed(args.seed)
    n_target = max(1, int(len(files) * args.sample_rate))
    samples = random.sample(files, min(n_target, len(files)))
    actual_rate = len(samples) / max(1, len(files)) * 100
    print(f"[+] 20% output sample: {len(samples)}/{len(files)} = {actual_rate:.1f}%")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_md = out_dir / "boss-samples.md"
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(f"# Boss 输出文件抽样 - {args.round_label}\n\n")
        f.write("## sampling rule\n")
        f.write(f"- target rate: {args.sample_rate*100:.0f}%\n")
        f.write(f"- actual sample: {len(samples)}/{len(files)} = {actual_rate:.1f}%\n")
        f.write(f"- random seed: {args.seed}\n\n")
        f.write("## boss must do\n")
        f.write("1. read 3 key fields per sample\n")
        f.write("2. mark 1 line: OK / FAKE / CONTRADICTION\n")
        f.write("3. >= 5% FAKE -> round FAIL -> re-run opencode\n\n")
        f.write("---\n\n")
        for i, fpath in enumerate(samples, 1):
            text = fpath.read_text(encoding="utf-8", errors="replace")
            keys = extract_key_lines(text)
            has_fake_marker = CN_FAKE_MARKER in text
            status = extract_verify_status(fpath.stem)
            f.write(f"## Sample {i}: `{fpath.name}`\n\n")
            f.write(f"**verify status (from filename)**: {status}\n\n")
            f.write(f"**verify status line (from content)**: {keys['verify_status'] or '(empty)'}\n\n")
            f.write(f"**actual response line**: {keys['actual_response'] or '(none)'}\n\n")
            f.write(f"**root cause first line**: {keys['root_cause'] or '(none)'}\n\n")
            if has_fake_marker:
                f.write(f"**RED FLAG**: contains template string -> SUSPECTED FAKE\n\n")
            f.write(f"**boss judgment**: `[ ]` (OK / FAKE / CONTRADICTION)\n\n")
            f.write("---\n\n")
    print(f"[+] Written: {out_md}")

    # 5% process 抽样
    proc_md = out_dir / "process-samples.md"
    proc_result = sample_process_sessions(args.round_label, proc_md, args.process_rate, args.seed)
    if "error" in proc_result:
        print(f"[WARN] process sample: {proc_result['error']}")
    else:
        print(f"[+] 5% process sample: {proc_result['sessions_sampled']}/{proc_result['total_sessions']} = {proc_result['sessions_sampled']/proc_result['total_sessions']*100:.1f}%")
        print(f"[+] Written: {proc_md}")


if __name__ == "__main__":
    main()
