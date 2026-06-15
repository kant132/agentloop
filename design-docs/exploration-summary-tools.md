# Java Audit Tool — Exploration Summary

> **Author**: Sisyphus (autonomous exploration)
> **Date**: 2026-06-15
> **Scope**: Map every tool, JAR, skill, Python script, and codegraph capability available in `D:\agentloop\` for the Java audit pipeline.
> **Goal**: Inventory "what's ready" vs "what needs to be built" so the audit loop can be assembled without re-deriving existing work.

---

## 1. Top-Level Repository Map

```
D:\agentloop\
├── README.md                  ← project root charter (60+ atomic requirements)
├── doc/                       ← tool usage guides (12 markdown files)
├── design-docs/               ← design drafts (chain-SQL engine + Chinese session logs)
├── tools/                     ← third-party JARs + arthas package
├── skills/                    ← 14 audit skills (java-whitebox-loop is the orchestrator)
├── scripts/                   ← 18 Python scripts in {ast, audit, chain, redis, tests}
├── checker/                   ← 8 person-checker skill packs (boss side, NOT used in audit)
├── projects/                  ← project presets (only _template/ exists — tool ships empty)
├── requirements/              ← original Chinese requirements (8 files)
├── proposals/                 ← methodology drafts (12 files)
├── types/                     ← vuln-type library (24 .md files + 1 JSON template)
├── loop_audit/                ← 12 output templates (audit deliverables)
└── webgoat-tools/             ← WebGoat-specific helper scripts (5 .py + 2 JSON fixtures)
```

The repository is **not a single project** — it's a meta-toolkit. `java-whitebox-loop` orchestrates everything; `java-forward-vuln-discovery` does the per-endpoint vulnerability analysis; 11 sibling skills (arthas, ssh, jadx, etc.) provide runtime/environment capabilities; the `scripts/` tree supplies the deterministic Python utilities.

---

## 2. Tools Inventory

| Name | Path | Purpose | Status |
|---|---|---|---|
| **codegraph CLI** (v0.9.9) | external `@colbymchenry/codegraph` | Code knowledge graph: parses source → SQLite, FTS5 search, callers/callees/impact/affected. Spring route auto-recognition. | ✅ Ready (CLI invoked from shell, MCP server optional) |
| **ast-grep** (v0.43.0) | external npm/cargo | Structural code search/lint (20+ langs, AST-aware). | ✅ Ready |
| **Memurai** (Redis-compatible) | `C:\Program Files\Memurai\memurai-cli.exe` | Cache + cross-subagent sharing. CLI used (not pip redis). | ✅ Ready (hard dependency — `memurai_client.py` raises `MemuraiError` if CLI missing) |
| **OpenAI SDK / DashScope** | `openai` pip + qwen3.7-max | LLM calls for FWD-X subagents. Replaced v3.3.4 `opencode CLI` subprocess (was 30-90s/call, now 0ms overhead). | ✅ Ready (per `bfs-taint-tracer-v3.3.5-practice.md`) |
| **opencode CLI** | `C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd` | Boss dispatches worker subagents per round (Sisyphus - ultraworker). | ✅ Ready (boss-only tool, NOT used in audit logic) |
| **JPype1** | pip | Bridge from Python to JavaParser JVM. | ✅ Required by `scripts/ast/javaparser_bridge.py` |
| **JDK 17+** | system | Runs JavaParser + arthas. | ✅ Required |

Documentation is in `doc/` — see §6 for breakdown.

---

## 3. Third-Party JARs (`D:\agentloop\tools\`)

| JAR | Size | Functionality | Use Case |
|---|---|---|---|
| `tools/arthas/arthas-bin.zip` | 16.2 MB | Alibaba Arthas diagnostic agent (jad/watch/stack/trace/tt/ognl/...). | Runtime audit: decompile bytecode in live JVM, watch parameter flow, dump call stacks. Triggered via `skills/arthas-deploy` + `skills/arthas-audit`. |
| `tools/arthas/arthas-tunnel-server-4.2.0-fatjar.jar` | 49.3 MB | Tunnel server (binds 0.0.0.0:8080 web / 7777 ws). Agents reverse-WebSocket connect. | Single hub pattern: Windows local server + remote Linux/k8s Pod agents via SSH. Managed by `skills/arthas-deploy`. |
| `tools/javaparser/jar-analyzer-5.22.jar` | 41.1 MB | Java static analysis (class hierarchy, method resolution, call extraction). | Pre-decompilation / class-level structural analysis. |
| `tools/javaparser/java-method-call-extractor-1.0.0.jar` | 5.8 MB | Extract every method invocation in a single `.java` file (returns JSON array of `{startLine, methodSignature, calledFQN}`). | Builds method-call graph without codegraph CLI; can feed `codegraph` SQLite via `nodes.id` lookup (per `design-docs/chain-sql-engine.md`). |

> **Note**: Only `javaparser_bridge.py` uses the JavaParser jars — it discovers them via `tools/javaparser/*.jar` glob. `java-method-call-extractor-1.0.0.jar` is documented but no current script invokes it (per `design-docs/chain-sql-engine.md` it's planned for the scripts-refactor branch).

---

## 4. Skills Inventory (`D:\agentloop\skills\`)

14 skills total. The first one is the orchestrator; the rest are leaves loaded on demand.

| Skill name | Type | 1-sentence description | Readiness |
|---|---|---|---|
| **java-whitebox-loop** | **orchestrator** (main loop) | 6-phase whitebox audit orchestrator (Phase 1 doc/env → 2 threat → 3 filter → 4 endpoint → 5 chain → 6 self-opt); uses Memurai batch prefetch + codegraph CTE. References 7 `rules/*.md` sub-docs. | ✅ Ready (parent skill) |
| **java-forward-vuln-discovery** | **audit core** (Phase 5) | Per-endpoint forward taint discovery via 4+1 subagent modes (A=data flow, B=auth, C=business, D=state, INFO=info leak). Drives `redis-batch-prefetch.py` then 5 parallel FWD-X subagents. | ✅ Ready (current focus) |
| **arthas-deploy** | runtime infra | Deploys tunnel server (Windows local) + remote arthas agents (Linux/k8s). Manages auth cookies, port conflicts, JDK version checks. | ✅ Ready |
| **arthas-audit** | runtime infra | Runtime bytecode audit via tunnel (jad/stack/watch/tt/ognl). Outputs `audit-findings.json` with `RUNTIME-CONFIRMED/DENIED/DIVERGENCE/BLOCKED` status. | ✅ Ready |
| **auth-chain-audit** | audit leaf | Filter/interceptor audit: runtime chain discovery → path normalization diff (Nginx/Tomcat/Spring) → JWT/OAuth2/Session/SSO/Base64 logic → path-bypass PoC. | ✅ Ready (requires arthas-audit) |
| **business-logic-audit** | audit leaf | Race conditions, flow bypass, state tampering, numeric boundary, batch assignment, IDOR. | ✅ Ready (requires arthas-audit) |
| **file-audit** | audit leaf | File upload/download/read/write/delete/zip with full content-type/MagicNumber/path-canonicalization checks per file type. | ✅ Ready (requires arthas-audit) |
| **injection-audit** | audit leaf | Per-type injection (SQL/CMD/XXE/deserialize/SpEL/SSTI/LDAP/NoSQL) with explicit trap list (ORDER BY, sub-queries, ORM dynamic, etc.). | ✅ Ready (requires arthas-audit) |
| **login-audit** | audit leaf | Login endpoint audit (auth compare, brute-force defense, session mgmt, MFA bypass). | ✅ Ready (requires arthas-audit) |
| **poc-verify** | audit leaf | Generic PoC verifier (4-step: env snapshot → exec with curl/arthas/SSH → CVSS 3.1 C/I/A table → conclusion). | ✅ Ready |
| **threat-model-analyst** | **Phase 2 required** | STRIDE + attack tree + DFD (drawio). Missing this = Phase 2 startup fails (no degradation). | ✅ Ready |
| **ssh-skill** | infrastructure | Dual-use: Phase 1 topology discovery (nginx conf, route table) + Phase 5 PoC backend (reuse connection). Writes `:env:reachability` to Memurai. | ✅ Ready |
| **playwright-skill** | infrastructure | Browser PoC (Phase 5 frontend): page.goto / fill / click / screenshot / network capture. | ✅ Ready |
| **jadx-python-decompile** | Phase 1/5 fallback | Decompile jar-only projects (jadx CLI) then re-index with codegraph. Distinguishes `view:source` vs `view:decompiled`. | ✅ Ready |

The skill system is **complete** — no skill is marked "stub" or "TODO". The orchestrator (`java-whitebox-loop`) and the leaf skills are all in mature state.

---

## 5. Python Scripts (`D:\agentloop\scripts\`)

Total: **18 Python files** (+ 1 `__init__.py`), grouped into 5 subdirectories.

### 5.1 `scripts/ast/` — Java source / annotation analysis (5 files)

| Script | Lines | Purpose |
|---|---|---|
| `ast-finder.py` | 119 | Find dangerous function calls via 7 hard-coded ast-grep patterns (`sql_injection`, `rce_runtime`, `deserialize_objectinput`, `ssrf_url`, `path_traversal_file`, `xss_response`, `weak_random`). CLI: `--src DIR --pattern NAME`. |
| `ast-sanitizer.py` | 112 | Detect 7 sanitizer types per method body (`PreparedStatement`, `ParameterizedQuery`, `WhitelistValidation`, `HTMLEncode`, `PathCanonicalize`, `URLWhitelist`, `AuthAnnotation`). Used at FWD-A sink check. |
| `javaparser_bridge.py` | 365 | JPype bridge to JavaParser 3.28.2. `JavaParserBridge` class: lazy JVM startup, optional symbol-solver (jar + source + JDK reflection). `extract_method_calls(file)` returns `[{line, selector, args_text, declaring_class, full_invocation, resolved}, ...]`. Requires `tools/javaparser/*.jar`. |
| `attack_surface_scanner.py` | **1216** | Two-phase scanner. Phase 1: `ast-grep run --kind annotation --json=compact` → `all_annotations.json`. Phase 2: filter for route patterns (`annotation-categories.json`) → `route_annotations.json`. Uses optional `javaparser-service.jar` for enrichment. The largest script. |
| `scanner_utils.py` | 152 | Pure helpers used by orchestrators: `node_hash_key`, `fqn_to_method_name`, `inject_sink_comment`, `get_body`, `detect_annotation_changes`, `classify_route`, `validate` (3-assertion consistency check). |
| `annotation-categories.json` | 62 lines | Annotation FQN catalog: `route` (Spring/Jakarta/Micronaut/Dubbo), `auth` (`@PreAuthorize`, `@Secured`, `@RolesAllowed`, Shiro), `validation` (Bean Validation), `ignore` (Test/Component/etc.). |

### 5.2 `scripts/audit/` — Boss-side orchestration & verification (5 files)

| Script | Lines | Purpose |
|---|---|---|
| **`cross-agent-50r.py`** | **1253** | **The boss daemon.** Runs the 50-round stability experiment. Loads `projects/{groupId}/preset.json`, dispatches opencode subagents per round, tracks `round{N}.json` + `summary.md`, hard-cleanup before each round (P5.4 invariant: `hi + lo == ep_lines`, `poc_count == high_risk_count`). Hard timeouts: `PER_ROUND_TIMEOUT=3600s`, `GLOBAL_TIMEOUT=1800s`. 5min cap only for AUDIT tools. |
| `verify-endpoint-coverage.py` | 181 | P5.4 invariant checker. Reads `external_endpoints/端点.jsonl` + counts `.md` in `routes/{高风险端点,中低险端点}/`, parses filenames to extract `sig_hash` (8-char hex), exits 1 if mismatch. |
| `verify-opencode-compliance.py` | 275 | L7 anti-lazy. Verifies 6 mandatory `diag/` artifacts exist + non-empty, CIA evidence patterns in `poc_real_attack.log` (C/I/A regex dictionary), session cookie real usage (no `<session>` placeholder), docker container status, code_reads must include `login.html` + `SecurityConfig.java`. |
| `audit-poc-quality.py` | 95 | Scans PoC `.md` files for filename-vs-content contradiction (`{状态}_{等级}_{...}` mismatch), template-text detection (`缺乏充分的输入验证和输出编码`), GET-only PoC. Built-in patterns to catch the round-1 fake PoC bug. |
| `sample-poc-for-boss.py` | 179 | **20% output + 5% process sampling** (user 2026-06-13 hard rule). Reads `opencode.db` `session`/`message`/`part` tables to extract REAL_WORK vs TEMPLATE vs HUNG signals. Outputs `boss-samples.md` + `process-samples.md`. |
| `collect-feedback.py` | 275 | Project-end feedback loop. Reads all `round{N}.json`, dispatches ONE opencode session to write `projects/{groupId}/审计反馈报告*.md` (4 sections: worked/blockers/P0-P1-P2 suggestions/cross-run trend). |

### 5.3 `scripts/chain/` — SQLite call-chain queries (4 files)

All four scripts talk directly to `codegraph.db` (SQLite WAL). Schema: `nodes(id, kind, name, qualified_name, file_path, start_line, end_line, signature, decorators, ...)`, `edges(id, source, target, kind, line, col, metadata, provenance)`.

| Script | Lines | Purpose |
|---|---|---|
| `sqlite-extract-chain.py` | 186 | Two modes: **recursive** (CTE RECURSIVE, depth ≤ 20, cycle detection via `path` column, ~100-500ms) and **left** (1-4 hops LEFT JOIN, fixed 8-12 JOIN). Used by FWD-X for call-chain extraction. |
| `sqlite-multi-hop-search.py` | 348 | **5 pre-canned templates** using up to 20 LEFT JOIN: `forward_5hop_20join`, `full_forward_4hop` (18 JOIN), `sqli_auth_context` (14 JOIN), `auth_miss_3hop` (12 JOIN), `multi_sink_search` (8 JOIN). Custom SQL with `:name` placeholders supported. |
| `sqlite-pattern-search.py` | 198 | 8 pre-defined `LIKE` patterns on `nodes.name`/`qualified_name`: `sql_injection`, `rce`, `deserialize`, `ssrf`, `path_traversal`, `ldap`, `endpoint_route`, `controller_method`. Used to find sink candidates. |
| `chain-stats.py` | 72 | Aggregate `findings.jsonl` into counters (`by_severity`, `by_vuln_type`, `by_fwd_mode`, `by_poc_status`, `high_risk_chains`). |

### 5.4 `scripts/redis/` — Memurai (Redis) integration (5 files)

All scripts shell out to `memurai-cli.exe` (NOT `pip install redis`). 5-minute subprocess timeout enforced.

| Script | Lines | Purpose |
|---|---|---|
| **`memurai_client.py`** | 482 | The Redis client. `Memurai(host, port, password, db, timeout=300, cli_path, exit_on_error)`. Exposes: `ping`, `set`, `get`, `mset`, `setex`, `expire`, `delete`, `exists`, `dbsize`, `info`, `flushdb`, `scan(pattern)`, `scan_iter(match)`, `count(pattern)`, `pipe_setex_batch(items)` (--pipe RESP, 1 subprocess for N SETs), `pipe_exec`, `pipe_set_many`, `mget`, `set_json`, `get_json`. Custom `MemuraiError` exception. |
| `redis-batch-prefetch.py` | 157 | Prefetch an entire call-chain into Memurai via `pipe_setex_batch` (1 subprocess for N methods). Writes 2 keys per chain: `audit:{g}:commit:{c}:method:{fqn}#{sigHash}` (24h) and `audit:{g}:commit:{c}:prefetch:{chainId}` (1h). Auto-computes SHA256 sig hash if missing. |
| `redis-self-check.py` | 131 | Startup consistency: sample 50 method keys, compare body/file/line against `codegraph.db` SQLite truth. Mismatches auto-deleted from cache. Exits 1 if `failed > 0`. |
| `redis-status-tracker.py` | 261 | State machine. Subcommands: `init`, `mark-file` (status/chain_count/sink counts), `mark-sink` (source-point info, INCR global stats), `mark-round`, `mark-finished-in-file` (writes `<!-- status: finished -->` to PoC .md), `summary`. Implements the boss-specified 6-key schema from `boss-experience.md` §13.1. |
| `redis-stats.py` | 77 | Reports `method_count / chain_count / prefetch_count / finding_draft_count / finding_final_count` + `used_memory_human`. |

### 5.5 `scripts/tests/` — pytest suite (3 files)

| Script | Lines | Purpose |
|---|---|---|
| `test_scanner_utils.py` | 291 | pytest cases for `scanner_utils.py` (the orchestrator helpers). Covers node_hash_key, fqn_to_method_name, inject_sink_comment, get_body, detect_annotation_changes, classify_route, validate. |
| `conftest.py` | 16 | pytest config. |
| `_loader.py` | 4 | Test loader utility. |

> **Caveat**: Tests exist only for `scanner_utils.py` — no tests for `memurai_client.py`, `redis-batch-prefetch.py`, or any `chain/` script. Per `doc/TDD.md` there are 39 cases planned across 12 scripts (TC-MC-001 through TC-RBP-004+), but most are unimplemented.

---

## 6. Documentation Inventory (`D:\agentloop\doc\`)

| File | Lines | Purpose |
|---|---|---|
| `codegraph-usage-guide.md` | 1717 | **The biggest doc.** 15 CLI commands (`init`/`index`/`sync`/`status`/`query`/`files`/`callers`/`callees`/`impact`/`affected`/`serve`/`unlock`/`install`/`uninstall`), 8 MCP tools, full schema (nodes/edges/files/nodes_fts), 14 framework route-detection patterns, SQLite direct access patterns. **The single source of truth for codegraph.** |
| `ast-grep-usage-guide.md` | 1303 | Pattern syntax (`$VAR`, `$$$`), atomic/relational/composite rules, Java-specific examples, official Agent Skill workflow. |
| `TDD.md` | 704 | Test design for 39 cases across 12 scripts (Memurai client, batch prefetch, etc.). Coverage target ≥80%. |
| `boss-experience.md` | 783 | The boss's self-experience log (2026-06-13): hard rules, opencode CLI traps (positional + --file ordering, GBK encoding), 5-min cap only for AUDIT tools, P5.4 + PoC consistency rules, 3-philosophy self-check, 5-dimension cross-validation. |
| `forward-audit-tool-recommendations.md` | 555 | Survey of static-analysis tools (Soot, Wala, FlowDroid, JavaParser, Tree-sitter, Fortify, Checkmarx, Semgrep). Recommends JavaParser (方案A) for small projects, FlowDroid (方案B) for large. |
| `atomic-requirements.md` | 139 | 60+ atomic requirements extracted from the original Chinese spec, grouped by §0/§一/§二/§三/§四 (5 sections). |
| `SDD.md` | 208 | Software Design Doc: 3 goals (G1 挖掘/G2 资产化/G3 自优化), 11 hard constraints, 4 thresholds (90/85/75%/3x85), Phase 1-6 breakdown, scoring rubric, output structure. |
| `v3.3.8-release-notes.md` | 225 | Last release notes. ast-grep project-level batching → 145x speedup (WebGoat 258s → 1.78s); `forward_audit_pipeline` parameter extraction (18 VULNERABLE in WebGoat vs 1 before). |
| `boss-reflection-2026-06-13.md` | 136 | Round 2 reflection: opencode missed `webgoat-local` container name + `/WebGoat` prefix → 100% 404 → 100% false negative. New hard constraint: must `docker ps` + read `login.html`. |
| `bfs-taint-tracer-v3.3.4-practice.md` | 171 | v3.3.4 → v3.3.4.5 changes: subprocess deadlock fix (`taskkill /T /F /PID`), SQLite-direct `CodegraphDB.get_method` (1000x faster), 2-line AI output format. 6/10 TCs matched. |
| `bfs-taint-tracer-v3.3.5-practice.md` | 280 | v3.3.5 changes: OpenAI SDK direct call (qwen3.7-max) instead of `opencode CLI` subprocess. `find_enclosing_method` fix. **BUT** TC accuracy regressed 6/10 → 1/10 (opencode agent mode has richer context than raw API). |
| `references/ast-grep-rule-reference.md` | 298 | Cloned from `ast-grep/agent-skill` — full rule system reference. |

---

## 7. Codegraph SQL Capabilities

The SQLite database (`.codegraph/codegraph.db`) supports two parallel access modes:

### 7.1 Direct query paths

| Path | Purpose | Tool |
|---|---|---|
| `codegraph init && codegraph index` | Build the SQLite | `codegraph` CLI |
| `codegraph query "<symbol>" -k kind -j` | FTS5 search via BM25 | `codegraph` CLI (~500-800ms) |
| `codegraph callers <symbol> -j` | Reverse lookup (who calls X) | `codegraph` CLI |
| `codegraph callees <symbol> -j` | Forward lookup (X calls whom) | `codegraph` CLI |
| `codegraph impact <symbol> -d N` | Blast-radius analysis (default depth=2) | `codegraph` CLI |
| `codegraph affected <files>` | Find affected tests given changed files | `codegraph` CLI |
| `codegraph serve --mcp` | Expose 8 MCP tools for AI agents | `codegraph` CLI |

### 7.2 SQLite direct (Python, ~1000x faster than CLI)

Database tables:
- `nodes(id, kind, name, qualified_name, file_path, start_line, end_line, signature, visibility, is_static, decorators, type_parameters, docstring, language, updated_at)`
- `edges(id, source, target, kind, line, col, metadata, provenance)` — kinds: `calls`, `references`, `contains`, `imports`, `extends`, `implements`, `instantiates`
- `files(path, content_hash, language, size, indexed_at)`
- `nodes_fts` — FTS5 virtual table over `name`, `qualified_name`, `docstring`, `signature`

### 7.3 Pre-built SQL templates (max 20 LEFT JOIN)

Available in `skills/java-forward-vuln-discovery/sql-cheatsheet.md` + `scripts/chain/sqlite-multi-hop-search.py`:

| Template | JOIN count | Purpose |
|---|---|---|
| `forward_5hop_20join` | 20 | endpoint → 5 callees + route + class + field + caller context |
| `full_forward_4hop` | 18 | route → 4 callees + class + field + caller + route tag |
| `sqli_auth_context` | 14 | SQLi sink + auth annotation + 3-hop context |
| `auth_miss_3hop` | 12 | 3-hop auth check via `decorators` (NB: Java decorator extraction incomplete — use class-name heuristics) |
| `multi_sink_search` | 8 | 1 SQL, 6 sink types (SQLi/RCE/Deser/SSRF/PathTraverse/LDAP) via `CASE` discriminator |

Plus a manual CTE RECURSIVE pattern in `sqlite-extract-chain.py` (forward direction, depth ≤ 20, cycle detection via path string).

### 7.4 Known limitations

- **`decorators` column is empty for Java** in v0.9.9 (WebGoat test = 0 decorators on methods). Workaround: use `class.name LIKE '%Controller'` to identify Spring controllers.
- **Route ↔ method linkage**: connected via `references` edge (NOT `contains`); aligned by `file_path + start_line`.
- **Call-graph doesn't track parameter propagation** — only function-call relations. This is the gap that FWD-X subagents fill manually (per `forward-audit-tool-recommendations.md`).

---

## 8. What's Ready vs Stub/Incomplete

### ✅ Fully Ready (proven in production)

| Component | Evidence |
|---|---|
| All 14 skills | Every `SKILL.md` has clear scope, mandatory steps, anti-patterns. No stubs. |
| Memurai client | 482-line mature client; 39 test cases planned in `TDD.md` (some implemented). |
| Codegraph SQL templates | 5 production-ready templates, validated against WebGoat (590 rows in 50ms). |
| Boss daemon `cross-agent-50r.py` | 1253 lines, runs 50 rounds, enforces 13 hard constraints. |
| P5.4 / compliance / quality verifiers | 3 separate scripts, each catches a different failure mode. |
| Boss feedback collection | `collect-feedback.py` end-of-project aggregation. |

### ⚠️ Ready but with gaps

| Component | Gap |
|---|---|
| `attack_surface_scanner.py` | Large (1216 lines), Phase 2 still references `javaparser-service.jar` which isn't in `tools/javaparser/` — may need to skip Phase 2 enrichment if absent. |
| `forward-audit` FWD-X subagent quality | Per `bfs-taint-tracer-v3.3.5-practice.md`, direct API calls regress accuracy 6/10 → 1/10 because no `codegraph_explore` context. Still open question. |
| Sink rule coverage | TC02/04/05/09/10 still NO MATCH (sinks.json doesn't cover `queryForMap`, `Files.readString`, etc.). Documented as TODO in v3.3.4. |
| AI judgment accuracy | TC01/06/07/10 misclassified as SAFE in v3.3.5. Mitigation: pass caller context, add 2nd confirmation. |

### ❌ Stub / Incomplete / TODO

| Component | Status |
|---|---|
| `scripts/ast/ast-finder.py`, `ast-sanitizer.py` | Only 7+7 hard-coded patterns; no framework-aware expansion. |
| `scripts/audit/preset-init.py` (referenced in `rules/07-preset-rules.md` §8) | **Does not exist** — operators must manually copy `projects/_template/preset.template.json`. |
| `scripts/audit/three-philosophy-check.py` (referenced in `boss-experience.md` §12.3) | **Does not exist** — boss must hand-fill. |
| `scripts/audit/batch-generate-route-reports.py` (referenced in `boss-experience.md` §10) | **Does not exist**. |
| `scripts/audit/finding-promoter.py` (referenced in `java-forward-vuln-discovery` §4.1 step 7) | **Does not exist**. |
| `scripts/audit/force-rescan.py` (referenced in `java-forward-vuln-discovery` §4.3) | **Does not exist**. |
| `scripts/redis/redis-import.py` (for batch seed import — no consumer) | **Does not exist**. |
| `requirements/codex-*-*.md` (Chinese requirement docs) | Listed but unread here; primary requirements already in `atomic-requirements.md`. |
| Most TDD test cases | `TDD.md` specifies 39 cases; `tests/test_scanner_utils.py` has 291 lines = ~10 cases. **Missing ~30 cases** for Memurai, batch-prefetch, chain scripts. |

### 🔴 External / Requires User Action

| Component | What's needed |
|---|---|
| `projects/_template/preset.template.json` | Operator must `cp` + fill in groupId, container, appPort, etc., per `cross-agent-50r.py` §load_preset. |
| `loop_audit/_template/*.md.example` | Output templates are stubs (`.example` suffix) — operators copy + rename. |
| `C:\Program Files\Memurai\memurai-cli.exe` | Windows-only dependency. `MEMURAI_CLI` env var overrides. |
| `opencode.cmd` (boss tool) | Required only for the 50-round daemon; not for the per-endpoint audit logic. |

---

## 9. Critical Architectural Notes

1. **3 hard dependencies** (per `README.md` §三.17): codegraph + ast-grep + Memurai. If any is missing, exit (no degradation).
2. **Boss vs worker split**: boss writes only prompt + dispatch + reflection; workers (`opencode` subagents) do the actual writing/auditing. This is enforced in `boss-experience.md` §1.1.
3. **Per-round clean reset**: rounds 2-39 delete all `loop_audit/routes/*.md` to prevent contamination; rounds 40-50 KEEP for knowledge reuse.
4. **Memurai batch-prefetch vs read-through**: The script prefetches BEFORE subagent starts — subagents never call codegraph at runtime, only Memurai. This is the 30x speedup mentioned in `redis-batch-prefetch.py` docstring.
5. **codegraph SQLite JOIN limit**: max 20 LEFT JOIN (per `requirements` and SQL cheatsheet). Multi-hop script enforces this.
6. **CVSS 4.0 + 7-segment filename**: Per `cross-agent-50r.py` §18-19, all reports/PoCs MUST follow `{验证状态}_{问题等级}_{CVSS}_{类型}_{fqn.method.sigHash}[-roundNNN].md`.
7. **5-dimension cross-validation** = the only true acceptance criterion (not P5.4 numbers): filename + content + actual response + root-cause + process.

---

## 10. File Counts Summary

| Directory | Files | Total bytes | Notes |
|---|---|---|---|
| `doc/*.md` | 12 | ~178 KB | Plus 1 reference doc |
| `tools/` | 4 | ~106 MB | 2 arthas + 2 javaparser |
| `skills/*/SKILL.md` | 14 | ~80 KB | Plus 7 rules/*.md in java-whitebox-loop |
| `scripts/ast/` | 6 | ~67 KB | 5 .py + 1 .json |
| `scripts/audit/` | 5 | ~98 KB | 5 .py |
| `scripts/chain/` | 4 | ~33 KB | 4 .py |
| `scripts/redis/` | 5 | ~40 KB | 5 .py |
| `scripts/tests/` | 3 | ~10 KB | pytest suite (sparse) |
| `types/` | 25 | ~45 KB | Vuln-type library (.md + JSON) |
| `loop_audit/_template/` | 12 | ~25 KB | Output template stubs (.example) |
| `webgoat-tools/` | 7 | ~245 KB | WebGoat-specific helpers + 2 JSON fixtures |
| `projects/_template/` | 1 | ~600 B | preset.template.json |
| `checker/` | 8 person skills | ~ varies | Out of scope (not audit-related) |

---

## 11. Recommendation: How to Use This Inventory

For an audit run on a target project (e.g., WebGoat):

1. **Bootstrap project** — copy `projects/_template/preset.template.json` → `projects/{groupId}/preset.json`, fill 17 fields.
2. **Init codegraph** — `codegraph init && codegraph index` against target.
3. **Start Memurai** — service should auto-run; verify with `python scripts/redis/memurai_client.py`.
4. **Load orchestrator** — `java-whitebox-loop` skill + its 7 `rules/*.md` files.
5. **Run Phase 1** via `ssh-skill` + `threat-model-analyst` (Phase 2).
6. **Phase 5 (the loop)**: For each endpoint, `sqlite-extract-chain.py` → `redis-batch-prefetch.py` → dispatch 5 FWD-X subagents → score → `redis-status-tracker.py mark-file`.
7. **Validate** every round: `verify-endpoint-coverage.py` + `verify-opencode-compliance.py` + `audit-poc-quality.py`.
8. **Sample** 20% output + 5% process with `sample-poc-for-boss.py` before accepting a round.

> **All tools except the 9 missing scripts in §8 are ready today.** The most impactful gaps to close are: `preset-init.py` (saves operator setup), `three-philosophy-check.py` (semi-automates boss self-check), and `finding-promoter.py` (closes the 3x85 promotion loop so findings actually land in `loop_audit/findings/`).