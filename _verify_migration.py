"""End-to-end verification for the nodes.id hashkey migration.

Tests:
  1. Run with use_nodes_id=True  -> sig_hash should mostly be nodes.id
  2. Run with use_nodes_id=False -> sig_hash should be legacy md5 (16 hex)
  3. Compare route counts -> must match
  4. md5 fallback sig_hashes must be IDENTICAL between modes (deterministic)
  5. Stability: re-run use_nodes_id=True twice -> sig_hash must match exactly
  6. nodes_id values must be IDENTICAL between the two stable runs

Python 3.14 on Windows can't exec the .CMD wrapper that npm installs for
ast-grep without shell=True. We monkey-patch run_ast_grep_annotations for
testing only.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts" / "ast"))
import attack_surface_scanner as ass  # noqa: E402


def patched_run_ast_grep_annotations(project_root: Path):
    """Test-only ast-grep wrapper with shell=True for Windows .CMD compatibility."""
    if ass._which_ast_grep() is None:
        ass.log_error("ast-grep binary not found")
        return []
    cmd = [
        ass.AST_GREP_BIN, "run",
        "--kind", ass.ANNOTATION_NODE_KIND,
        "--lang", "java",
        "--json=compact", ".",
    ]
    proc = subprocess.run(
        cmd, cwd=str(project_root), capture_output=True,
        text=True, encoding="utf-8", errors="replace", shell=True,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        return []
    return ass.parse_ast_grep_output(proc.stdout)


ass.run_ast_grep_annotations = patched_run_ast_grep_annotations


def run_scan(use_nodes_id: bool, project_root: Path, db_path: Path, group_id: str):
    return ass.scan_attack_surface(
        project_root=project_root,
        db_path=db_path,
        use_nodes_id=use_nodes_id,
        group_id=group_id,
    )


def main() -> int:
    project_root = Path(r"D:\code\WebGoat-2025.3")
    db_path = project_root / ".codegraph" / "codegraph.db"
    group_id = "org.owasp.webgoat"

    print(f"project_root = {project_root}")
    print(f"db_path      = {db_path} (exists={db_path.is_file()})")
    print()

    # --- Test 1: use_nodes_id=True ---
    print("=" * 72)
    print("RUN A: use_nodes_id=True (new behavior)")
    print("=" * 72)
    a = run_scan(True, project_root, db_path, group_id)
    print(f"  routes           : {a['summary']['routes']}")
    print(f"  nodes_id_hashes  : {a['summary']['nodes_id_hashes']}")
    print(f"  md5_hashes       : {a['summary']['md5_hashes']}")

    # --- Test 2: use_nodes_id=False ---
    print()
    print("=" * 72)
    print("RUN B: use_nodes_id=False (legacy md5)")
    print("=" * 72)
    b = run_scan(False, project_root, db_path, group_id)
    print(f"  routes           : {b['summary']['routes']}")
    print(f"  nodes_id_hashes  : {b['summary']['nodes_id_hashes']}")
    print(f"  md5_hashes       : {b['summary']['md5_hashes']}")

    # --- Test 3: route count match ---
    print()
    print("=" * 72)
    print("ASSERTION 1: route count must match between modes")
    print("=" * 72)
    assert len(a["routes"]) == len(b["routes"]), \
        f"route count mismatch: {len(a['routes'])} vs {len(b['routes'])}"
    print(f"  PASS  ({len(a['routes'])} == {len(b['routes'])})")

    # --- Test 4: nodes_id coverage ---
    nodes_id_routes = [r for r in a["routes"] if r.get("nodes_id")]
    md5_routes = [r for r in a["routes"] if not r.get("nodes_id")]
    coverage = (len(nodes_id_routes) / len(a["routes"])) * 100 if a["routes"] else 0
    print()
    print("=" * 72)
    print(f"ASSERTION 2: nodes.id coverage is meaningful (>=80% of routes)")
    print("=" * 72)
    print(f"  nodes_id coverage: {len(nodes_id_routes)}/{len(a['routes'])} = {coverage:.1f}%")
    assert coverage >= 80.0, f"coverage too low: {coverage:.1f}%"
    print(f"  PASS  (>=80%)")

    # --- Test 5: stability (nodes_id mode should be stable across runs) ---
    print()
    print("=" * 72)
    print("ASSERTION 3: nodes_id mode is STABLE across runs")
    print("=" * 72)
    a2 = run_scan(True, project_root, db_path, group_id)
    # ast-grep returns annotations in non-deterministic order, so compare as sets.
    # Build a stable key (file + line) -> sig_hash / nodes_id
    def _index(rs):
        out = {}
        for r in rs:
            key = (r.get("file", ""), int(r.get("line", 0) or 0))
            out[key] = (r.get("sig_hash"), r.get("nodes_id"))
        return out
    idx_a = _index(a["routes"])
    idx_a2 = _index(a2["routes"])
    assert idx_a == idx_a2, \
        f"sig_hash/nodes_id changed between runs: " \
        f"missing={set(idx_a) - set(idx_a2)} extra={set(idx_a2) - set(idx_a)}"
    print(f"  PASS  ({len(idx_a)} route (file,line) entries identical across runs)")

    # --- Test 6: md5 fallback must be deterministic between modes ---
    print()
    print("=" * 72)
    print("ASSERTION 4: routes that fall back to md5 in mode A produce the same")
    print("              md5 sig as mode B for the same (file, line)")
    print("=" * 72)
    def _sig_index(rs):
        out = {}
        for r in rs:
            key = (r.get("file", ""), int(r.get("line", 0) or 0))
            out[key] = r.get("sig_hash")
        return out
    sigs_a = _sig_index(a["routes"])
    sigs_b = _sig_index(b["routes"])
    # Only check routes that FELL BACK to md5 in mode A
    fallback_keys = [
        (r.get("file", ""), int(r.get("line", 0) or 0))
        for r in a["routes"] if not r.get("nodes_id")
    ]
    mismatches = []
    for key in fallback_keys:
        sig_a = sigs_a.get(key)
        sig_b = sigs_b.get(key)
        if sig_a != sig_b:
            mismatches.append((key, sig_a, sig_b))
    assert not mismatches, f"md5 fallback differs: {mismatches[:3]}"
    print(f"  PASS  ({len(fallback_keys)} fallback routes have identical md5 sigs in both modes)")

    # --- Test 7: nodes_id format is the expected codegraph scheme ---
    print()
    print("=" * 72)
    print("ASSERTION 5: nodes_id format is '<kind>:<32-hex-md5>'")
    print("=" * 72)
    import re
    nid_re = re.compile(r"^(method|class|function|field|namespace|interface|enum):[a-f0-9]{32}$")
    all_nids_a = [r.get("nodes_id") for r in a["routes"]]
    bad = [n for n in all_nids_a if n and not nid_re.match(n)]
    assert not bad, f"malformed nodes_id: {bad[:3]}"
    print(f"  PASS  ({len(set(all_nids_a))} unique nodes_id values, all well-formed)")

    # --- Test 8: sample 5 routes ---
    print()
    print("=" * 72)
    print("SAMPLE 5 ROUTES (nodes.id mode)")
    print("=" * 72)
    for r in a["routes"][:5]:
        print(json.dumps({
            "fqn":             r.get("fqn"),
            "method_name":     r.get("method_name"),
            "file":            r.get("file"),
            "line":            r.get("line"),
            "sig_hash":        r.get("sig_hash"),
            "nodes_id":        r.get("nodes_id"),
            "annotation_source": r.get("annotation_source"),
        }, indent=2, ensure_ascii=False))
        print()

    # --- Test 9: DB missing → md5 fallback ---
    print()
    print("=" * 72)
    print("ASSERTION 6: missing codegraph DB -> md5 fallback")
    print("=" * 72)
    fake_db = project_root / ".codegraph" / "does_not_exist.db"
    c = run_scan(True, project_root, fake_db, group_id)
    print(f"  routes       : {c['summary']['routes']}")
    print(f"  nodes_id hits: {c['summary']['nodes_id_hashes']}")
    print(f"  md5 fallbacks: {c['summary']['md5_hashes']}")
    assert c["summary"]["nodes_id_hashes"] == 0, \
        "should have 0 nodes_id hits when DB is missing"
    assert c["summary"]["md5_hashes"] == c["summary"]["routes"], \
        "all routes should fall back to md5 when DB missing"
    print(f"  PASS  (0 nodes_id, all md5 fallback)")

    # --- Test 10: out_path output smoke ---
    print()
    print("=" * 72)
    print("ASSERTION 7: JSON output files written via main() pipeline")
    print("=" * 72)
    out_dir = Path(r"D:\agentloop\_test_out\main_pipeline")
    if out_dir.exists():
        import shutil
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rc = ass.main([
        "--project", str(project_root),
        "--group-id", group_id,
        "--output", str(out_dir),
        "--codegraph-db", str(db_path),
    ])
    print(f"  main() exit code: {rc}")
    assert rc == 0
    all_p = out_dir / "all_annotations.json"
    route_p = out_dir / "route_annotations.json"
    assert all_p.is_file()
    assert route_p.is_file()
    print(f"  wrote {all_p.name} ({all_p.stat().st_size} bytes)")
    print(f"  wrote {route_p.name} ({route_p.stat().st_size} bytes)")
    payload = json.loads(route_p.read_text(encoding="utf-8"))
    summary = payload["summary"]
    print(f"  route_annotations.json summary: {json.dumps(summary, indent=4)}")
    hk = payload.get("hashkey_stats")
    print(f"  hashkey_stats: {json.dumps(hk, indent=4)}")
    sample = payload["route_annotations"][0]
    print(f"  sample route keys: {sorted(sample.keys())}")
    assert "sig_hash" in sample, "sig_hash missing from output"
    assert "nodes_id" in sample, "nodes_id missing from output"
    assert summary["nodes_id_hashes"] + summary["md5_hashes"] == summary["route_annotations"]
    print(f"  PASS  (output schema correct)")

    print()
    print("=" * 72)
    print("ALL CHECKS PASSED")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())