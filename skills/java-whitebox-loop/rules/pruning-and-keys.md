# Pruning & Memurai Key Schema

Combines L1/L2/L3 pruning and the Memurai key catalog. Reference only by `java-whitebox-loop/SKILL.md`.

## L1 Endpoint Pruning (Phase A)

Skip endpoint if:
- **No parameters**: `routes.json` entry has `parameters.length == 0`
- **Only numeric parameters**: all parameter types are `int/long/float/double` (no strings, no POJOs)
- **Health check**: endpoint contains `/health`, `/actuator/health`, `/status`, `/ping`
- **Documentation**: endpoint contains `/api-docs`, `/swagger`, `/openapi.json`

Skipped endpoints go to `loop_audit/diag/pruning-log.jsonl`:
```json
{"route": "/actuator/health", "level": "L1", "reason": "health_check", "timestamp": "..."}
```

## L2 Chain Pruning (Phase C, per chain)

Skip chain body exploration if:
- **Sanitizers cover all parameters** — every parameter has a matching sanitizer in `08-Sanitizer表.json`
- **Chain fully audited in previous round** — `Memurai {groupId}:audit:chain:{sig_hash}:audit_complete` is `true`
- **Chain depth > 20** — risk of recursion; prune with warning

Skipped chains go to `loop_audit/diag/pruning-log.jsonl`:
```json
{"chain_id": "abc123...", "level": "L2", "reason": "sanitizers_cover_all", "timestamp": "..."}
```

## L3 Cross-Round Persistence

After each round's Phase D:
- Successful chains get `Memurai {groupId}:audit:chain:{sig_hash}:audit_complete = true` (TTL 24h)
- Next round's supervisor skips chains marked complete
- Round N's knowledge.json merges Round N-1's knowledge + new findings

## Memurai Key Schema

```
{groupId}:audit:chain:{sig_hash}                  # chain data (TTL 24h)
{groupId}:audit:chain:{sig_hash}:audit_complete   # flag (TTL 24h)
{groupId}:audit:finding:{chainId}:draft           # expert writes here
{groupId}:audit:finding:{chainId}:final           # supervisor promotes here
{groupId}:sup:exp:{chainId}                       # supervisor experience notes
{groupId}:knowledge:annotations                    # merged to knowledge.json
{groupId}:knowledge:sanitizers                     # merged to knowledge.json
{groupId}:knowledge:routes                          # merged to knowledge.json
{groupId}:knowledge:findings                       # merged to knowledge.json
```

All keys use `:` as separator. The `{groupId}` prefix comes from preset.json and MUST match exactly (case-sensitive).

## Key TTL Policy

- `audit:chain:*` → 24h TTL (expire if no new round in a day)
- `audit:finding:*` → no TTL (permanent)
- `sup:exp:*` → no TTL (supervisor experience persists forever)
- `knowledge:*` → no TTL (merged to knowledge.json, then deleted)

## Key Deletion on Round Start

`cross-agent-50r.py` does this at round start:
```python
for pattern in ["{groupId}:audit:finding:*:draft", "{groupId}:sup:exp:*"]:
    # delete these to avoid stale state from prior rounds
    m.delete_pattern(pattern)
```

Note: `audit:finding:*:final` and `knowledge:*` are NOT deleted — they persist across rounds for the self-evolution feedback loop.

## Codegraph Priority

All chain/edge queries MUST go through `codegraph.db` SQLite (CTE RECURSIVE). ast-grep is reserved for Phase A annotation discovery only. Never use ast-grep to walk call graphs — that's orders of magnitude slower.

```python
# CORRECT: codegraph CTE for chain walk
conn = sqlite3.connect(codegraph_db)
chain = chain_builder.build_chain(entry_fqn, db_path=codegraph_db)

# WRONG: ast-grep for call edges
# ast_grep("class X { method y() { z.foo() } }")  # don't do this
```