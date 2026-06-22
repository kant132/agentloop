# 4-Phase Gate Rules

Reference only by `java-whitebox-loop/SKILL.md`. Do NOT refer to older DEPRECATED rule files.

## Phase A — Endpoint Enumeration

### Entry Conditions
- `scripts/ast_scan/attack_surface_scanner.py` exists
- `codegraph.db` already indexed at `$CODEGRAPH_DB`
- `projects/_template/07-Sink表.json` accessible

### Exit Conditions
- `projects/$GROUP_ID/routes.json` produced with ≥10 routes
- ≥90% of entries have `nodes_id` (not md5 fallback)
- Top 20 routes by priority (POST/UPDATE/DELETE/GET) sorted

### Failure Rollback
- `attack_surface_scanner` fails → fall back to md5 hashkey (auto-degradation, documented in `requirements/ai改动日志.md`)
- `codegraph.db` missing → error exit (exit code 3)

## Phase B — Security Context

### Entry Conditions
- Phase A `routes.json` present
- `ssh-skill` available OR local env access

### Exit Conditions
- `projects/$GROUP_ID/security-context.json` produced:
  - `filters`: list of filter classes + paths
  - `interceptors`: list of interceptor classes
  - `spring_security_config`: parsed `SecurityFilterChain` beans
  - `annotated_sources`: 3rd-party call attributions via javaparser-service.jar

### Failure Rollback
- SSH unavailable → mark `ssh_source: "offline"` in security-context.json
- javaparser-service.jar unavailable → `annotated_sources: []` (silent degradation, logged)

## Phase C — Chain + Supervisor + Expert

### Entry Conditions
- Phase A `routes.json` present
- Phase B `security-context.json` present (may be partial)
- `scripts/chain/chain_builder.py` functional
- `endpoint-supervisor/SKILL.md` skill loadable

### Per-Chain Execution Order (strict, no parallelism across chains within one supervisor)
1. chain_builder (writes chain + Memurai cache)
2. analyst (reads chain, produces 5-dim JSON)
3. expert(s) (reads chain + 5-dim, produces findings)
4. supervisor collects → promotes to Memurai `final`

### Exit Conditions
- All chains processed OR pruned (documented in `loop_audit/diag/pruning-log.jsonl`)
- `Memurai {groupId}:audit:finding:*:final` ≥ 1 per chain (or explicit `no_finding` marker)
- High-severity findings have `poc_status=pending` (consumed by poc-monitor.py)

### Failure Rollback
- chain_builder error → log, skip chain, mark `chain_error` in chain data
- analyst error → skip to expert with default 5-dim (all `unknown`)
- expert error → mark finding as `expert_error` in Memurai

## Phase D — Reconcile + Self-Evolution

### Entry Conditions
- Phase C completed (all endpoints processed)
- `self_evolution.py` loadable

### Exit Conditions
- `loop_audit/diag/scoring-history.jsonl` has 1 new line appended
- `loop_audit/diag/convergence.json` updated (4-AND verdict)
- `loop_audit/knowledge.json` contains merged Memurai keys
- Every 5th round: `loop_audit/diag/needs_human-review/*.jsonl` has FP samples

### Reconciliation (7+1 checks)
See `self_evolution.md` for details. 7+1 PASS required for `reconcile_pass` metric.

### Failure Rollback
- `self_evolution` import fails → log warning, still exit daemon (graceful degradation)
- Memurai merge fails → `knowledge.json` stays at previous round's state (no corruption)