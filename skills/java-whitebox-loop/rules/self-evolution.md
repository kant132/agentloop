# Self-Evolution Rules

Reference only by `java-whitebox-loop/SKILL.md` Phase D section.

## 8 Function Contract (from `scripts/audit/self_evolution.py`)

| Function | Writes to | Trigger |
|----------|-----------|---------|
| `append_scoring_history(loop_audit_dir, round_n, **metrics)` | `loop_audit/diag/scoring-history.jsonl` | every round |
| `calculate_convergence(loop_audit_dir)` | (in-memory) | every round |
| `write_convergence_file(loop_audit_dir, result)` | `loop_audit/diag/convergence.json` | every round |
| `sample_for_false_positive(loop_audit_dir, sample_size=30)` | `loop_audit/diag/needs_human-review/false-positive-samples.jsonl` | every 5 rounds |
| `write_self_check(loop_audit_dir, round_n, checks)` | `loop_audit/diag/self-check.json` | every round |
| `append_optimization(loop_audit_dir, round_n, category, desc, benefit, verified)` | `loop_audit/diag/optimization-suggestions.jsonl` | when optimization observed |
| `append_false_positive_sample(loop_audit_dir, finding_id, fqn, vuln_type, labeled_as_fp, labeler)` | `loop_audit/diag/false-positive-samples.jsonl` | human labels finding |
| `merge_knowledge_from_memurai(loop_audit_dir, memurai_client, group_id)` | `loop_audit/knowledge.json` | every round |

## 4-AND Convergence Conditions

All must be true for daemon to break:

```
last_score >= 85
stddev(last N=3 scores) < 3.0
reconcile_pass >= 10
coverage >= 0.95
```

Convergence verdict goes to `loop_audit/diag/convergence.json`:
```json
{
  "converged": true,
  "last_score": 87.5,
  "stddev": 1.2,
  "reconcile_pass": 12,
  "coverage": 0.97,
  "round": 15,
  "timestamp": "2026-06-15T..."
}
```

## Scoring Formula

```
score = (coverage * 30) + (poc_rate * 30) + (reconcile_pass_frac * 25) + (compliance * 15)
```

All fields normalized to 0.0-1.0 before multiplying.

## Data Reconciliation (7+1 checks)

From old `05-data-reconcile.md`. Run by daemon every Phase D:

1. `routes.json` count == `routes/*.md` file count
2. `poc/` count == `high_risk_findings` count (within ±5)
3. `scoring-history.jsonl` line count == current round number
4. `findings.jsonl` schema valid (each entry has `chain_id,finding_id,fqn,vuln_type,severity`)
5. `knowledge.json` atomic-write completed (no `.tmp` leftovers)
6. `self-check.json` has ≥3 PASS entries
7. Memurai `{groupId}:audit:finding:*:final` reachable
+1. `needs_human-review/` exists and has ≥0 entries (always PASS if exists)

Each check writes to `self-check.json`: `{"check_id":"P1","description":"...","passed":true,"reason":"..."}`.

## Knowledge Merge Mechanism

Daemon pattern:
```python
m = Memurai()
self_evolution.merge_knowledge_from_memurai(
    loop_audit_dir=ld,
    memurai_client=m,
    group_id=gid
)
```

`merge_knowledge_from_memurai` does:
1. Scan `{groupId}:knowledge:*` keys in Memurai
2. For each key, GET value, parse as JSON (or string fallback)
3. Determine category from suffix (`annotations`/`sanitizers`/`routes`/`findings`)
4. Write to `{loop_audit_dir}/knowledge.json.tmp`
5. Atomic rename `.tmp` → `knowledge.json`

Categories are fixed: only keys ending with those 4 suffixes get merged. Others are ignored (log warning).

## Atomic Write Pattern

All JSON writes go to `.tmp` first, then `os.rename`:

```python
tmp_path = loop_audit_dir / "knowledge.json.tmp"
with open(tmp_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp_path, loop_audit_dir / "knowledge.json")
```

This avoids partial writes corrupting the file if daemon crashes mid-write.