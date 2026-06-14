# AgentLoop

> **Java white-box security audit agent loop** — drives a coding agent (opencode) through 50 rounds of audit on a target Java project (e.g. WebGoat) with strict, accumulating quality controls.

## What it does

AgentLoop is a **boss + worker** architecture for cross-agent stability experiments:

- **Boss** (this orchestrator) writes prompts, runs the daemon, observes results
- **Worker** (opencode) executes the actual Java audit
- **N rounds** verify the audit pipeline is stable + producing real evidence

Each round opencode:

1. Probes Docker / environment (§ 14)
2. Reads real source code (login.html, SecurityConfig, Controllers) (§ 14)
3. Discovers routes via ast-grep
4. Runs **real curl attacks** against each high-risk endpoint
5. Writes PoC files with 4 mandatory fields + secondary exploitation (§ 18, § 21, § 27, § 29)
6. Marks **"unable to confirm"** with code evidence + self-reflection (§ 28)
7. Self-checks n_poc consistency before reporting PASS (§ 30)
8. Verifies P5.4 invariant (hi + lo + unable-to-confirm == ep_lines) (§ 20)

## 17 mandatory constraints (Section 14-30)

| # | Constraint | Purpose |
|---|------------|---------|
| § 14 | docker env + code reading | Real env, real source |
| § 15 | 200 OK != success, prove CIA | Avoid "endpoint reachable" false positives |
| § 16 | Filename + no snapshot pollution | Avoid stale residuals polluting samples |
| § 17 | (deprecated, replaced by § 22.3) | — |
| § 18 | PoC 4 fields (2 required + 3 optional per § 22) | Real evidence |
| § 19 | 7-segment filename format | Consistent naming |
| § 20 | P5.4 accept "宁缺毋滥" (better to skip than fake) | Quality over quantity |
| § 21 | Hazard chain 5 steps | Real 2nd exploit |
| § 22 | Physical contradiction fix (4 fields relaxed, 60min cap, free path) | Make audit actually finish |
| § 23 | 2nd exploit must contain real result 3 sections | Anti-hallucination |
| § 24 | P5.4 must not delete PoC | Backup before P5.4 |
| § 25 | cleanup must not delete endpoint jsonl | P5.4 needs source |
| § 26 | (deprecated, replaced by § 27) | — |
| § 27 | REAL 2nd exploit results, NO intent/analysis wording | Anti-hallucination hard |
| § 28 | "Unable to confirm" must have reason + reflection + code evidence | Anti-lazy |
| § 29 | ALL severe-level MUST contain real 2nd exploit | No threshold cut-off |
| § 30 | opencode MUST self-check n_poc consistency | Anti-lie |

## 5-dimensional cross-verification

| Dimension | Source | Threshold |
|-----------|--------|-----------|
| 1. File completion | routes/{高风险端点,中低险端点,poc}/*.md count | == 100% |
| 2. Source coverage | diag/ 6 mandatory artifacts | >= 95% |
| 3. Round status | p54_pass + ep_lines + reports | all pass |
| 4. PoC truthfulness | 20% sample hand-read + 13+ CIA pattern | <= 5% FAKE |
| 5. Process quality | 5% session sample + REAL_WORK ratio | >= 95% |

## Quick start

```bash
# 1. Verify env
docker ps --filter "name=webgoat-local"  # MUST be Up/healthy
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18080/WebGoat/

# 2. Run a round (e.g. round 5 onwards, MAX_ROUNDS=6 means run round 6)
cd "D:/wiki/good-skill/agentloop"
MAX_ROUNDS=6 python 脚本/audit/cross-agent-50r.py 2>&1 | tail -20

# 3. Check round result
cat D:/code/WebGoat-2025.3/loop_audit/loop-log/cross-50r/round06.json

# 4. Hand-verify 5 PoC for 2nd exploit
ls D:/code/WebGoat-2025.3/loop_audit/routes/poc/*round006.md | head -5 | \
  xargs -I {} sh -c 'echo "=== {} ==="; cat {}'
```

## Layout

```
agentloop/
├── 脚本/audit/
│   ├── cross-agent-50r.py      # main daemon (60min cap/round, runs opencode)
│   ├── audit-poc-quality.py    # scan PoC for filename vs content contradiction
│   ├── sample-poc-for-boss.py  # 20% sample + 5% process sample
│   ├── three-philosophy-check.py
│   ├── verify-endpoint-coverage.py
│   ├── verify-opencode-compliance.py
│   └── ... (see 脚本/audit/ for full list)
├── doc/
│   ├── boss-experience.md      # accumulated boss lessons (12 sections)
│   ├── boss-reflection-2026-06-13.md
│   ├── SDD.md / TDD.md / atomic-requirements.md
│   ├── ast-grep-usage-guide.md
│   ├── codegraph-usage-guide.md
│   ├── forward-audit-tool-recommendations.md
│   └── v3.3.8-release-notes.md
├── test_scripts/               # validation scripts
├── test_webgoat.py             # smoke test
├── README.md / DEPLOY.md
└── (loose 行为准则/ 类型/ 项目/ skills/ as documented in DEPLOY.md)
```

## Stability evidence (9 rounds)

| Round | reports | PoC | time | 5D verdict | Key learning |
|-------|---------|-----|------|------------|--------------|
| 1 | 260 | 171 | 5min | PASS (after fixing sampling-source bug) | § 16 anti-snapshot-pollution |
| 2 | 269 | 119 | 8min | FAIL (67% fake PoC) | § 18 4 fields, ban subagent |
| 3 | 260 | 5 | 10min | PASS | § 20 accept "宁缺毋滥" |
| 4 | 0 | 0 | 30min cap | FAIL | § 22 fix physical contradiction |
| 5 | 185 | 6 (log only) | 28min | PARTIAL | § 24 backup PoC before P5.4 |
| 6 | 185 | 5 | 9.2min | PARTIAL | § 25 cleanup keeps jsonl |
| 7 | 60 | 4 | 14.7min | PARTIAL | § 26 (later replaced by § 27) |
| 8 | 60 | 4 | 10.4min | PASS | — |
| 9 | 182 | 6 | 11.4min | PASS | — |
| 11 | 182 | 11 | 21min | PASS | § 27 + § 28 partial生效 |

**Stable steady-state**: 10-15 min/round, 60-182 reports, 4-6 real PoC, 5/5 dimensional cross-check PASS.

## Boss role (4 hard rules from 50-round experiment)

1. **Don't write code** — only write prompts, dispatch scripts, reflection
2. **Each round = new opencode session** (no `-s` resume) — independent observation
3. **Prompt must contain**: env + hard constraints + required tools + verification + prohibitions
4. **Observe opencode.db** — don't trust "looks right", trust 5D cross-check

## License

Internal — see DEPLOY.md.
