#!/usr/bin/env python3
"""
cross-agent-50r.py — Thin daemon wrapper.

Per round: cleanup Memurai → check convergence → clear loop results →
start monitor → spawn opencode → kill monitor → merge knowledge → log round.
"""
import argparse, hashlib, json, logging, os, shutil, subprocess, sys, tempfile, time
from datetime import datetime
from pathlib import Path
from typing import Optional

# --- load_counter / metric_simplifier integration -----------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
from load_counter import LoadCounter
from metric_simplifier import simplify

# --- auth_class_cacher integration (AR-10) ------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "chain"))
from auth_class_cacher import AuthClassCacher, create_cacher

# --- priority_calculator integration (AR-09) ---------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "chain"))
from priority_calculator import rank_chains, calculate_priority, _base
from sink_registry import count_sinks_in_chain, match_preset_sinks

PER_ROUND_TIMEOUT = 3600
OPENCODE_CMD = Path(r"C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRESET_PATH_ENV = "AGENTLOOP_PRESET"

# --- check_core_tools integration (req #17) ----------------------------------
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from check_core_tools import check_core_tools as _cct
except ImportError:
    def _cct(exit_on_missing: bool = True) -> dict:
        print("FATAL: check_core_tools.py not found", file=sys.stderr)
        if exit_on_missing: sys.exit(2)
        return {"all_ok": False}

# --- self_evolution integration (cached) -----------------------------------
_se_mod = None

def _load_self_evolution():
    """Load and cache self_evolution module. Returns None if unavailable."""
    global _se_mod
    if _se_mod is not None:
        return _se_mod
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import self_evolution as se
        _se_mod = se
        return se
    except ImportError:
        logging.warning("self_evolution.py not found — self-evolution disabled")
        return None

# backward-compatible alias
_se = _load_self_evolution

# --- Preset ------------------------------------------------------------------
class PVE(ValueError):
    pass

_REQUIRED = frozenset(["groupId", "projectRoot", "codegraphDb"])
_DEFAULTS = dict(maxRounds=50, appPort=8080, loopDir="loop_audit",
                 epJsonl="external_endpoints/端点.jsonl",
                 sessionCookieName="JSESSIONID")

def load_preset(path: Optional[str] = None) -> dict:
    p = path or os.environ.get(PRESET_PATH_ENV)
    if not p: raise PVE(f"Set env {PRESET_PATH_ENV} or use --preset <path>")
    pf = Path(p)
    if not pf.exists(): raise PVE(f"preset.json not found: {pf}")
    preset = json.loads(pf.read_text(encoding="utf-8"))
    missing = _REQUIRED - frozenset(preset)
    if missing: raise PVE(f"preset.json missing: {sorted(missing)}")
    preset.update({k: v for k, v in _DEFAULTS.items() if k not in preset})
    return preset

# --- Memurai cleanup --------------------------------------------------------
def _mc():
    try:
        from scripts.redis.memurai_client import Memurai
        return Memurai()
    except Exception:
        return None

def cleanup_memurai(gid: str) -> int:
    """Delete session keys, preserve knowledge:* and merge errors:log."""
    c = _mc()
    if c is None: return -1
    try:
        keys = c.scan(f"{gid}:*", count=10000)
        if not keys: return 0
        # Safety: all keys must belong to this groupId
        for k in keys:
            if not k.startswith(f"{gid}:"): return -1

        # Merge errors:log into knowledge:errors before deletion
        err_log_key = f"{gid}:errors:log"
        knowledge_err_key = f"{gid}:knowledge:errors"
        err_log = c.get_json(err_log_key)
        if err_log:
            existing = c.get_json(knowledge_err_key) or []
            if isinstance(existing, list) and isinstance(err_log, list):
                merged = existing + err_log
                c.set_json(knowledge_err_key, merged)

        # Delete everything EXCEPT knowledge:* keys
        preserve_prefix = f"{gid}:knowledge:"
        to_delete = [k for k in keys if not k.startswith(preserve_prefix)]
        if to_delete:
            n = c.delete(*to_delete)
            logging.info("Memurai cleanup: %d keys deleted (%d knowledge preserved)",
                         n, len(keys) - len(to_delete))
            return n
        return 0
    except Exception as e:
        logging.error("Memurai cleanup failed: %s", e)
        return -1

# --- Auth class query helper (AR-10) -----------------------------------------
def _query_auth_classes(db_path: Path) -> list:
    """Query codegraph SQLite for classes with auth/security annotations."""
    if not db_path or not db_path.exists():
        return []
    import sqlite3
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT n.qualified_name as fqn, n.file_path, n.start_line "
            "FROM nodes n "
            "LEFT JOIN annotations a ON a.node_id = n.id "
            "WHERE n.kind = 'class' AND ("
            "  a.name LIKE '%Auth%' OR a.name LIKE '%Secur%' OR "
            "  a.name LIKE '%Permit%' OR a.name LIKE '%Role%' OR "
            "  a.name LIKE '%PreAuthorize%' OR a.name LIKE '%PostAuthorize%' OR "
            "  a.name LIKE '%RolesAllowed%' OR a.name LIKE '%DenyAll%' OR "
            "  a.name LIKE '%PermitAll%'"
            ")"
        ).fetchall()
        return [{"fqn": r["fqn"], "file_path": r["file_path"], "start_line": r["start_line"]} for r in rows]
    except Exception as e:
        logging.warning("_query_auth_classes failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except:
            pass

# --- Loop result cleanup -----------------------------------------------------
def cleanup_loop_results(ld: Path) -> None:
    for s in ("routes", "reports"):
        d = ld / s
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

# --- Round metrics for self-evolution ----------------------------------------
def _compute_round_metrics(ld: Path, round_n: int) -> dict:
    """
    Compute scoring metrics from loop_audit/ output for round N.

    Returns dict with keys: score, coverage, poc_rate, reconcile_pass, compliance
    matching self_evolution.append_scoring_history() arguments.
    """
    # basic counts (reuse count_reports logic)
    ep = ld / "external_endpoints" / "端点.jsonl"
    ep_ln = sum(1 for l in ep.read_text(encoding="utf-8").splitlines() if l.strip()) if ep.exists() else 0

    routes_dir = ld / "routes"
    n_routes = sum(1 for _ in (routes_dir).rglob("*.md")) if routes_dir.exists() else 0
    n_poc = sum(1 for _ in (routes_dir / "poc").rglob("*.md")) if (routes_dir / "poc").exists() else 0

    hi_dir = routes_dir / "高风险端点"
    lo_dir = routes_dir / "中低险端点"
    n_hi = sum(1 for _ in hi_dir.rglob("*.md")) if hi_dir.exists() else 0
    n_lo = sum(1 for _ in lo_dir.rglob("*.md")) if lo_dir.exists() else 0
    total_reports = n_hi + n_lo

    # coverage: fraction of known endpoints that have reports
    coverage = (total_reports / ep_ln) if ep_ln > 0 else 0.0

    # poc_rate: fraction of reported endpoints that have PoC
    poc_rate = (n_poc / total_reports) if total_reports > 0 else 0.0

    # score: composite quality metric (0-100)
    score = coverage * (0.5 + 0.5 * poc_rate) * 100.0

    # reconcile_pass: run verify-endpoint-coverage.py if present
    reconcile_pass = 0
    verify_script = Path(__file__).parent / "verify-endpoint-coverage.py"
    if verify_script.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(verify_script), "--loop-audit-dir", str(ld)],
                capture_output=True, text=True, errors="replace", timeout=120)
            if r.returncode == 0:
                # parse passed count from stdout
                for line in r.stdout.splitlines():
                    if "passed" in line.lower():
                        parts = line.strip().split()
                        for i, p in enumerate(parts):
                            if p.lower() == "passed" and i > 0:
                                try:
                                    reconcile_pass = int(parts[i - 1])
                                except (ValueError, IndexError):
                                    pass
                        break
                # if no parsed output, assume script ran OK → use total_reports as proxy
                if reconcile_pass == 0 and r.stdout.strip():
                    try:
                        reconcile_pass = int(r.stdout.strip().split()[-1])
                    except (ValueError, IndexError):
                        reconcile_pass = total_reports
        except Exception:
            pass

    # compliance: 1.0 if p54_pass else 0.0  (p54_pass = total_reports == ep_ln)
    compliance = 1.0 if (ep_ln > 0 and total_reports == ep_ln) else 0.0

    return dict(
        score=round(score, 2),
        coverage=round(coverage, 4),
        poc_rate=round(poc_rate, 4),
        reconcile_pass=reconcile_pass,
        compliance=round(compliance, 4),
    )


# --- Load metrics (load_counter + metric_simplifier) -------------------------
def _compute_load_metrics(ld: Path, round_n: int) -> dict:
    """
    Produce round_metrics.json with 7 core fields.

    Fields:
      - chain_method_count: total method bodies in all chains
      - loaded_method_count: actual loads recorded by LoadCounter
      - load_ratio: loaded / total (ideal < 0.3)
      - vuln_chains: chains confirmed vulnerable
      - safe_chains: chains confirmed safe
      - unknown_chains: chains unable to determine
      - vuln_exposed_surface: endpoints with confirmed vulns

    vuln/safe/unknown are aggregated from chain_analysis_report JSON files;
    if aggregation is not yet feasible they default to 0 with TODO.
    """
    diag_dir = ld / "diag"
    diag_dir.mkdir(parents=True, exist_ok=True)

    # --- load_counter data ---
    lc = LoadCounter(ld)
    loaded_method_count = lc.count_total_loads()

    # chain_method_count: count unique node_ids across all loads
    # (each unique node_id ≈ one method body that was touched)
    chain_method_count = lc.count_unique_node_ids()
    # If no loads recorded, chain_method_count falls back to 0;
    # load_ratio = 0.0 in that case (no data → no verdict).
    load_ratio = (loaded_method_count / chain_method_count) if chain_method_count > 0 else 0.0

    # --- per_chain metrics for metric_simplifier ---
    # We need per-chain data; query loads.db directly for chain_ids
    import sqlite3
    db_path = diag_dir / "loads.db"
    per_chain_metrics = []
    if db_path.exists():
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.execute("SELECT DISTINCT chain_id FROM loads")
            chain_ids = [row[0] for row in cur.fetchall()]
        for cid in chain_ids:
            loads = lc.count_loads_by_chain(cid)
            # cached_count per chain: unknown from loads.db alone, use 0 → ratio=0.0
            per_chain_metrics.append({
                "chain_id": cid,
                "loads": loads,
                "cached": 0,
            })
    simplified = simplify(per_chain_metrics)

    # --- vuln/safe/unknown chain counts ---
    # TODO: aggregate verdict from chain_analysis_report.json files
    # when the chain report schema is stabilised. Placeholder = 0.
    vuln_chains = 0
    safe_chains = 0
    unknown_chains = 0

    # --- vuln_exposed_surface ---
    # TODO: count distinct endpoints with at least one vuln chain.
    vuln_exposed_surface = 0

    return {
        "round": round_n,
        "chain_method_count": chain_method_count,
        "loaded_method_count": loaded_method_count,
        "load_ratio": round(load_ratio, 4),
        "vuln_chains": vuln_chains,
        "safe_chains": safe_chains,
        "unknown_chains": unknown_chains,
        "vuln_exposed_surface": vuln_exposed_surface,
        "load_simplified": simplified,
    }


# --- Report counter ----------------------------------------------------------
def count_reports(ld: Path) -> dict:
    def _c(p: Path) -> int:
        return len([f for f in p.iterdir() if f.suffix == ".md"]) if p.exists() else 0
    ep = ld / "external_endpoints" / "端点.jsonl"
    n_hi = _c(ld / "routes" / "高风险端点")
    n_lo = _c(ld / "routes" / "中低险端点")
    n_poc = _c(ld / "routes" / "poc")
    ep_ln = sum(1 for l in ep.read_text(encoding="utf-8").splitlines() if l.strip()) if ep.exists() else 0
    return dict(n_hi=n_hi, n_lo=n_lo, n_poc=n_poc,
                total_reports=n_hi + n_lo, ep_lines=ep_ln,
                p54_pass=(n_hi + n_lo == ep_ln) if ep_ln else None)

# --- Convergence check ------------------------------------------------------
def check_convergence(dd: Path) -> bool:
    f = dd / "convergence.json"
    if not f.exists(): return False
    try:
        return bool(json.loads(f.read_text(encoding="utf-8")).get("satisfied"))
    except Exception:
        return False

# --- PoC monitor management -------------------------------------------------
def start_poc_monitor(preset: dict, dd: Path):
    script = Path(__file__).parent / "poc-monitor.py"
    if not script.exists():
        logging.warning("poc-monitor.py not found"); return None
    try:
        logf = open(dd / "poc-monitor.log", "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(script), "--group-id", preset["groupId"],
             "--diag-dir", str(dd)], stdout=logf, stderr=subprocess.STDOUT, text=True)
        logging.info("poc-monitor PID=%s", proc.pid); return proc
    except Exception as e:
        logging.error("poc-monitor start failed: %s", e)
        return None

def kill_poc_monitor(p) -> None:
    if p is None or p.poll() is not None: return
    try:
        p.terminate(); p.wait(timeout=10); logging.info("poc-monitor stopped")
    except Exception as e:
        logging.warning("poc-monitor kill failed: %s", e)
        try: p.kill()
        except Exception: pass

# --- Prompt discovery + rendering -------------------------------------------
def _prompt_path(group_id: Optional[str] = None) -> Path:
    # Priority: project-specific > design-docs > requirements
    candidates = [
        REPO_ROOT / "projects" / group_id / "prompt-boss.md" if group_id else None,
        REPO_ROOT / "design-docs" / "prompt-boss.md",
        REPO_ROOT / "requirements" / "prompt-boss.md",
    ]
    for p in candidates:
        if p and p.exists():
            return p
    raise FileNotFoundError("prompt-boss.md not found in projects/<groupId>/, design-docs/, or requirements/")

def render_prompt(tpl: Path, preset: dict, rn: int) -> Path:
    c = tpl.read_text(encoding="utf-8")
    for k, v in dict(__PROJECT_ROOT__=preset.get("projectRoot",""),
                     __PROJECT_NAME__=preset.get("projectName",""),
                     __GROUP_ID__=preset.get("groupId",""),
                     __DOCKER_CONTAINER__=preset.get("dockerContainer",""),
                     __APP_PORT__=str(preset.get("appPort","")),
                     __APP_CTX_PATH__=preset.get("appCtxPath",""),
                     __APP_BASE_URL__=preset.get("appBaseUrl",""),
                     __LOGIN_URL__=preset.get("loginUrl",""),
                     __REGISTER_URL__=preset.get("registerUrl",""),
                     __SESSION_COOKIE_NAME__=preset.get("sessionCookieName","JSESSIONID"),
                     __TEST_USER__=preset.get("testUser",""),
                     __TEST_PASS__=preset.get("testPass","")).items():
        c = c.replace(k, v)
    tmp = Path(tempfile.gettempdir()) / f"agentloop-prompt-r{rn:03d}.txt"
    tmp.write_text(c, encoding="utf-8"); return tmp

# --- Knowledge merge ---------------------------------------------------------
def merge_knowledge(gid: str, ld: Path) -> None:
    se = _se()
    if se is None: logging.warning("self_evolution.py missing — skip merge"); return
    fn = getattr(se, "merge_knowledge_from_memurai", None)
    if fn is None: logging.warning("no merge_knowledge_from_memurai — skip"); return
    try:
        fn(gid, ld); logging.info("Knowledge merged")
    except Exception as e:
        logging.error("Knowledge merge failed: %s", e)

# --- OpenCode worker --------------------------------------------------------
def run_opencode_session(rn: int, preset: dict, pf: Path) -> dict:
    t0 = time.time()
    msg = f"Stability R{rn}/{preset.get('maxRounds',50)}. Execute prompt on {preset['projectRoot']}."
    cmd = [str(OPENCODE_CMD),"run",msg,"--model","alibaba-cn/qwen3.7-max",
           "--agent","Sisyphus - ultraworker","--title",f"WGB-R{rn}","--file",str(pf)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=PER_ROUND_TIMEOUT)
        return dict(round=rn, ts=datetime.now().isoformat(timespec="seconds"),
                    elapsed=round(time.time()-t0,1), rc=r.returncode,
                    stdout_tail=r.stdout[-500:] if r.stdout else "",
                    stderr_tail=r.stderr[-300:] if r.stderr else "")
    except subprocess.TimeoutExpired:
        return dict(round=rn, ts=datetime.now().isoformat(timespec="seconds"),
                    elapsed=PER_ROUND_TIMEOUT, rc=-1, error="timeout")
    except Exception as e:
        return dict(round=rn, ts=datetime.now().isoformat(timespec="seconds"),
                    elapsed=round(time.time()-t0,1), rc=-1, error=str(e))

# --- Main --------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset"); ap.add_argument("--resume", type=int, default=0)
    ap.add_argument("--max-rounds", type=int); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    _cct(exit_on_missing=True)

    try: preset = load_preset(a.preset)
    except PVE as e:
        print(f"FATAL: {e}", file=sys.stderr); sys.exit(3)

    gid = preset["groupId"]; proj = Path(preset["projectRoot"])
    db_path = Path(preset["codegraphDb"])
    max_r = a.max_rounds or preset.get("maxRounds", 50)
    ld = proj / preset.get("loopDir", "loop_audit")
    dd, lgd = ld / "diag", ld / "loop-log" / "cross-50r"
    lgd.mkdir(parents=True, exist_ok=True); dd.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(lgd/"daemon.log",encoding="utf-8"),
                 logging.StreamHandler(sys.stdout)])

    # Memurai client for cross-round knowledge persistence (created once)
    se_client = _mc()

    if a.dry_run:
        print(f"groupId={gid} projectRoot={proj} loopDir={ld} maxRounds={max_r} "
              f"resume={a.resume} opencode={OPENCODE_CMD}")
        try: print("prompt:", _prompt_path(gid))
        except FileNotFoundError as e: print("prompt: NOT FOUND —", e)
        return 0

    try: tpl = _prompt_path(gid)
    except FileNotFoundError as e:
        print(f"FATAL: {e}", file=sys.stderr); sys.exit(4)

    done = [n for n in range(1, max_r+1) if (lgd/f"round{n:02d}.json").exists()]
    start = a.resume or (done[-1]+1 if done else 1)
    logging.info("Starting R%d (completed: %s)", start, done)
    t0 = time.time(); history = []

    for n in range(start, max_r+1):
        cleanup_memurai(gid)
        # Auth class caching (AR-10): after cleanup_memurai, before opencode session
        try:
            cacher = create_cacher()
            auth_items = _query_auth_classes(db_path)
            if auth_items:
                cached_count = cacher.cache_auth_classes(auth_items, gid)
                logging.info("Auth class cache: %d classes cached", cached_count)
            else:
                logging.info("Auth class cache: no auth classes found in codegraph")
        except Exception as e:
            logging.warning("Auth class caching failed: %s", e)

        # Load chains from SQLite, batch by priority (100 per batch)
        chains_db_path = ld / "chains.db"
        if chains_db_path.exists():
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "chain"))
                from chain_db import ChainDB
                db = ChainDB(chains_db_path)
                batch = db.batch_by_priority(limit=100, status="pending")
                stats = db.stats()
                logging.info("Chains DB: %d total, %d pending, avg_priority=%.1f",
                             stats["total_chains"],
                             stats["by_status"].get("pending", 0),
                             stats["avg_priority"])
                # Write batch info for AI agent to consume
                batch_path = ld / "diag" / "chain_batch.json"
                batch_path.parent.mkdir(parents=True, exist_ok=True)
                batch_path.write_text(
                    json.dumps({"batch": batch, "stats": stats},
                              ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                logging.warning("Chains DB load failed: %s", e)
        else:
            logging.info("No chains.db found, chain building not yet done")

        if check_convergence(dd):
            logging.info("Converged — breaking after R%d", n-1); break
        cleanup_loop_results(ld)
        mon = start_poc_monitor(preset, dd)
        pf = render_prompt(tpl, preset, n)
        res = run_opencode_session(n, preset, pf); pf.unlink(missing_ok=True)
        kill_poc_monitor(mon)

        # --- Self-evolution wiring ---
        se = _load_self_evolution()
        if se:
            try:
                metrics = _compute_round_metrics(ld, n)
                se.append_scoring_history(ld, n, **metrics)
                result = se.calculate_convergence(ld)
                se.write_convergence_file(ld, result)
                if n % 5 == 0:
                    sampled = se.sample_for_false_positive(ld, sample_size=30)
                    if sampled > 0:
                        logging.info("Sampled %d findings for false-positive review", sampled)
                if se_client:
                    se.merge_knowledge_from_memurai(ld, se_client, gid)
                if result.get("converged"):
                    logging.info("Converged after round %d (score=%.1f, stddev=%.2f)",
                                 n, result["last_score"], result["stddev"])
                    # write final round record then break
                    mets = count_reports(ld); res.update(mets)
                    (lgd/f"round{n:02d}.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),
                                                 encoding="utf-8")
                    # --- load metrics ---
                    try:
                        lm = _compute_load_metrics(ld, n)
                        (dd / f"round_metrics_r{n:02d}.json").write_text(
                            json.dumps(lm, ensure_ascii=False, indent=2), encoding="utf-8")
                        (dd / "round_metrics.json").write_text(
                            json.dumps(lm, ensure_ascii=False, indent=2), encoding="utf-8")
                        logging.info("R%d load_ratio=%.4f loaded=%d/%d", n,
                                     lm["load_ratio"], lm["loaded_method_count"],
                                     lm["chain_method_count"])
                    except Exception as e:
                        logging.warning("Load metrics failed for R%d: %s", n, e)
                    history.append(res)
                    break
            except Exception as e:
                logging.error("Self-evolution step failed for R%d: %s", n, e)
        # --- end self-evolution wiring ---

        mets = count_reports(ld); res.update(mets)
        (lgd/f"round{n:02d}.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),
                                             encoding="utf-8")
        # --- load metrics ---
        try:
            lm = _compute_load_metrics(ld, n)
            # per-round file (for history)
            (dd / f"round_metrics_r{n:02d}.json").write_text(
                json.dumps(lm, ensure_ascii=False, indent=2), encoding="utf-8")
            # canonical current-round file (latest snapshot)
            (dd / "round_metrics.json").write_text(
                json.dumps(lm, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info("R%d load_ratio=%.4f loaded=%d/%d", n,
                         lm["load_ratio"], lm["loaded_method_count"],
                         lm["chain_method_count"])
        except Exception as e:
            logging.warning("Load metrics failed for R%d: %s", n, e)
        history.append(res)
        st = "PASS" if mets.get("p54_pass") else "FAIL"
        logging.info("R%d %s | reports=%d/%s poc=%d elapsed=%.1fs rc=%d",
                    n, st, mets["total_reports"], str(mets.get("ep_lines","?")),
                    mets.get("n_poc",0), res["elapsed"], res["rc"])

    sm = dict(total_rounds=len(history), passed_p54=sum(1 for h in history if h.get("p54_pass")),
              elapsed_total=round(time.time()-t0,1),
              history=[dict(round=h["round"], elapsed=h.get("elapsed"),
                           p54_pass=h.get("p54_pass"), total_reports=h.get("total_reports"),
                           n_hi=h.get("n_hi"), n_lo=h.get("n_lo"), n_poc=h.get("n_poc"))
                    for h in history])
    (lgd/"summary.json").write_text(json.dumps(sm,ensure_ascii=False,indent=2),encoding="utf-8")
    logging.info("Done: %d/%d P5.4 PASS in %.1fs", sm["passed_p54"], sm["total_rounds"], sm["elapsed_total"])
    return 0

if __name__ == "__main__":
    sys.exit(main())