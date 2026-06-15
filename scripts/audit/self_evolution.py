"""
self_evolution.py

Self-evolution persistence helpers for the audit daemon.
Called by Boss at Phase D and by daemon between rounds.

File location: D:\\agentloop\\scripts\\audit\\self_evolution.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# -------------------------------------------------------------------------- types


class ConvergenceResult(dict):
    """Typed dict returned by calculate_convergence()."""

    converged: bool
    last_score: float
    stddev: float
    coverage: float
    reconcile_pass: int
    lookback_scores: list[float]
    reason: str

    def __init__(
        self,
        converged: bool,
        last_score: float,
        stddev: float,
        coverage: float,
        reconcile_pass: int,
        lookback_scores: list[float],
        reason: str,
    ):
        super().__init__(
            converged=converged,
            last_score=last_score,
            stddev=stddev,
            coverage=coverage,
            reconcile_pass=reconcile_pass,
            lookback_scores=lookback_scores,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _diag_dir(loop_audit_dir: Path) -> Path:
    d = loop_audit_dir / "diag"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_utf8_no_bom(path: Path) -> None:
    """Touch file with UTF-8-no-BOM encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. append_scoring_history
# ---------------------------------------------------------------------------


def append_scoring_history(
    loop_audit_dir: Path,
    round_n: int,
    score: float,
    coverage: float,
    poc_rate: float,
    reconcile_pass: int,
    compliance: float,
) -> None:
    """
    Append a score record to loop_audit/diag/scoring-history.jsonl

    Fields written per line:
      round, score, coverage, poc_rate, reconcile_pass, compliance, timestamp
    """
    record = {
        "round": round_n,
        "score": score,
        "coverage": coverage,
        "poc_rate": poc_rate,
        "reconcile_pass": reconcile_pass,
        "compliance": compliance,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = _diag_dir(loop_audit_dir) / "scoring-history.jsonl"
    _ensure_utf8_no_bom(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 2. calculate_convergence
# ---------------------------------------------------------------------------


def calculate_convergence(
    loop_audit_dir: Path,
    min_score: float = 85.0,
    max_stddev: float = 3.0,
    min_reconcile_pass: int = 10,
    min_coverage: float = 0.95,
    lookback: int = 3,
) -> ConvergenceResult:
    """
    Read scoring-history.jsonl, compute convergence.

    Convergence requires ALL of the following AND conditions:
      1. last_score >= min_score
      2. stddev(last lookback scores) < max_stddev
      3. last reconcile_pass >= min_reconcile_pass
      4. last coverage >= min_coverage

    Returns a ConvergenceResult dict:
      {
        "converged": bool,
        "last_score": float,
        "stddev": float,
        "coverage": float,
        "reconcile_pass": int,
        "lookback_scores": [float, ...],
        "reason": str,
      }
    """
    path = _diag_dir(loop_audit_dir) / "scoring-history.jsonl"
    if not path.exists():
        return ConvergenceResult(
            converged=False,
            last_score=0.0,
            stddev=0.0,
            coverage=0.0,
            reconcile_pass=0,
            lookback_scores=[],
            reason="scoring-history.jsonl not found; cannot compute convergence",
        )

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not records:
        return ConvergenceResult(
            converged=False,
            last_score=0.0,
            stddev=0.0,
            coverage=0.0,
            reconcile_pass=0,
            lookback_scores=[],
            reason="scoring-history.jsonl is empty; cannot compute convergence",
        )

    # sort by round ascending
    records.sort(key=lambda r: r.get("round", 0))

    # last N rounds
    lookback_records = records[-lookback:]
    lookback_scores = [r.get("score", 0.0) for r in lookback_records]

    last_record = records[-1]
    last_score = last_record.get("score", 0.0)
    coverage = last_record.get("coverage", 0.0)
    reconcile_pass = last_record.get("reconcile_pass", 0)

    # stddev only meaningful with 2+ records
    stddev = statistics.stdev(lookback_scores) if len(lookback_scores) >= 2 else 0.0

    # evaluate 4 AND conditions
    cond1 = last_score >= min_score
    cond2 = stddev < max_stddev
    cond3 = reconcile_pass >= min_reconcile_pass
    cond4 = coverage >= min_coverage

    converged = cond1 and cond2 and cond3 and cond4

    reasons: list[str] = []
    if not cond1:
        reasons.append(f"last_score={last_score:.1f} < {min_score}")
    if not cond2:
        reasons.append(f"stddev={stddev:.2f} >= {max_stddev}")
    if not cond3:
        reasons.append(f"reconcile_pass={reconcile_pass} < {min_reconcile_pass}")
    if not cond4:
        reasons.append(f"coverage={coverage:.0%} < {min_coverage:.0%}")

    reason = "; ".join(reasons) if reasons else "all convergence conditions met"
    if converged:
        reason = f"converged: score={last_score:.1f}, stddev={stddev:.2f}, reconcile={reconcile_pass}, coverage={coverage:.0%}"

    return ConvergenceResult(
        converged=converged,
        last_score=last_score,
        stddev=stddev,
        coverage=coverage,
        reconcile_pass=reconcile_pass,
        lookback_scores=lookback_scores,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# 3. write_convergence_file
# ---------------------------------------------------------------------------


def write_convergence_file(loop_audit_dir: Path, result: ConvergenceResult) -> Path:
    """
    Write loop_audit/diag/convergence.json.
    Returns the path written.
    """
    path = _diag_dir(loop_audit_dir) / "convergence.json"
    _ensure_utf8_no_bom(path)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 4. append_optimization
# ---------------------------------------------------------------------------


def append_optimization(
    loop_audit_dir: Path,
    round_n: int,
    category: str,
    description: str,
    observed_benefit: str,
    verified: bool = True,
) -> None:
    """
    Append to loop_audit/diag/optimization-suggestions.jsonl

    category examples: "fwd_skip", "chain_prune", "memurai_warm"
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "round": round_n,
        "category": category,
        "description": description,
        "observed_benefit": observed_benefit,
        "verified": verified,
    }
    path = _diag_dir(loop_audit_dir) / "optimization-suggestions.jsonl"
    _ensure_utf8_no_bom(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 5. sample_for_false_positive
# ---------------------------------------------------------------------------


def sample_for_false_positive(
    loop_audit_dir: Path,
    sample_size: int = 30,
    random_seed: Optional[int] = None,
) -> int:
    """
    Read diag/findings.jsonl, randomly sample `sample_size` entries for human
    review, and write them to diag/needs_human-review/false-positive-samples.jsonl
    (one record per finding, to be labeled by a human).

    Returns the number of samples written (0 if file missing or too few records).
    """
    import random as _random

    if random_seed is not None:
        _random.seed(random_seed)

    findings_path = _diag_dir(loop_audit_dir) / "findings.jsonl"
    if not findings_path.exists():
        return 0

    # load all finding records
    records: list[dict[str, Any]] = []
    for line in findings_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not records:
        return 0

    # sample without replacement if len > sample_size
    sampled = _random.sample(records, min(len(records), sample_size))

    out_dir = _diag_dir(loop_audit_dir) / "needs_human-review"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "false-positive-samples.jsonl"
    _ensure_utf8_no_bom(out_path)

    count = 0
    with out_path.open("a", encoding="utf-8") as fh:
        for rec in sampled:
            # write a stub record with labeled_as_fp=None so human can fill in
            stub = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "finding_id": rec.get("finding_id", rec.get("chain_id", "")),
                "fqn": rec.get("fqn", ""),
                "vuln_type": rec.get("vuln_type", ""),
                "labeled_as_fp": None,  # human fills this in
                "labeler": "human",
                "source_record": rec,
            }
            fh.write(json.dumps(stub, ensure_ascii=False) + "\n")
            count += 1

    return count


# ---------------------------------------------------------------------------
# 6. append_false_positive_sample
# ---------------------------------------------------------------------------


def append_false_positive_sample(
    loop_audit_dir: Path,
    finding_id: str,
    fqn: str,
    vuln_type: str,
    labeled_as_fp: bool,
    labeler: str = "human",
) -> None:
    """
    Append to loop_audit/diag/false-positive-samples.jsonl
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "finding_id": finding_id,
        "fqn": fqn,
        "vuln_type": vuln_type,
        "labeled_as_fp": labeled_as_fp,
        "labeler": labeler,
    }
    path = _diag_dir(loop_audit_dir) / "false-positive-samples.jsonl"
    _ensure_utf8_no_bom(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 7. write_self_check
# ---------------------------------------------------------------------------


def write_self_check(
    loop_audit_dir: Path,
    round_n: int,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Write loop_audit/diag/self-check.json

    checks: list of {"check_id": str, "description": str, "passed": bool, "reason": str}

    Returns summary: {"round": N, "total": M, "passed": K, "failed": F, "pass_rate": float}
    """
    total = len(checks)
    passed = sum(1 for c in checks if c.get("passed", False))
    failed = total - passed
    pass_rate = passed / total if total > 0 else 0.0

    doc = {
        "round": round_n,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
        },
    }

    path = _diag_dir(loop_audit_dir) / "self-check.json"
    _ensure_utf8_no_bom(path)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    return doc["summary"]


# ---------------------------------------------------------------------------
# 8. merge_knowledge_from_memurai
# ---------------------------------------------------------------------------


def merge_knowledge_from_memurai(
    loop_audit_dir: Path,
    memurai_client: Any,
    group_id: str,
    knowledge_key_pattern: str = "{groupId}:knowledge:*",
) -> int:
    """
    Scan Memurai for knowledge:* keys, merge into loop_audit/knowledge.json.
    Returns number of entries merged.

    Avoids file locks by:
      - Writing to .knowledge.tmp first, then atomic rename

    memurai_client is a memurai_client.MemuraiClient instance (optional).
    If not provided, returns 0.
    """
    if memurai_client is None:
        return 0

    pattern = knowledge_key_pattern.format(groupId=group_id)

    # scan for matching keys
    try:
        raw_keys = memurai_client.scan(pattern)
    except Exception:
        return 0

    if not raw_keys:
        return 0

    # load existing knowledge.json if present
    knowledge_path = loop_audit_dir / "knowledge.json"
    if knowledge_path.exists():
        try:
            existing = json.loads(knowledge_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {"annotations": [], "sanitizers": [], "routes": [], "findings": []}
    else:
        existing = {"annotations": [], "sanitizers": [], "routes": [], "findings": []}

    # ensure all keys exist as arrays
    for key in ("annotations", "sanitizers", "routes", "findings"):
        if key not in existing:
            existing[key] = []

    merged_count = 0
    for key in raw_keys:
        try:
            value = memurai_client.get(key)
            if value is None:
                continue
            # value may be JSON string or plain string
            try:
                data = json.loads(value)
            except Exception:
                data = {"raw": value}
            # determine category from key suffix
            suffix = key.split(":")[-1]  # e.g. "annotations"
            if suffix in existing:
                if isinstance(data, list):
                    existing[suffix].extend(data)
                    merged_count += len(data)
                else:
                    existing[suffix].append(data)
                    merged_count += 1
        except Exception:
            continue

    # atomic write: .tmp then rename
    tmp_path = loop_audit_dir / "knowledge.json.tmp"
    tmp_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.move(str(tmp_path), str(knowledge_path))

    return merged_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_calculate_convergence(args: argparse.Namespace) -> None:
    loop_dir = Path(args.loop_audit_dir).resolve()
    result = calculate_convergence(
        loop_dir,
        min_score=args.min_score,
        max_stddev=args.max_stddev,
        min_reconcile_pass=args.min_reconcile_pass,
        min_coverage=args.min_coverage,
        lookback=args.lookback,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # also write convergence.json
    path = write_convergence_file(loop_dir, result)
    print(f"Written: {path}")


def _cli_append_scoring(args: argparse.Namespace) -> None:
    loop_dir = Path(args.loop_audit_dir).resolve()
    append_scoring_history(
        loop_dir,
        round_n=args.round,
        score=args.score,
        coverage=args.coverage,
        poc_rate=args.poc_rate,
        reconcile_pass=args.reconcile_pass,
        compliance=args.compliance,
    )
    print(f"Appended scoring record for round {args.round}")


def _cli_append_optimization(args: argparse.Namespace) -> None:
    loop_dir = Path(args.loop_audit_dir).resolve()
    append_optimization(
        loop_dir,
        round_n=args.round,
        category=args.category,
        description=args.description,
        observed_benefit=args.benefit,
        verified=args.verified,
    )
    print(f"Appended optimization for round {args.round}")


def _cli_write_self_check(args: argparse.Namespace) -> None:
    loop_dir = Path(args.loop_audit_dir).resolve()
    # build checks from CLI --check flags: check_id:description:passed
    checks = []
    for entry in args.checks:
        parts = entry.split(":", 2)
        if len(parts) == 3:
            check_id, desc, passed_str = parts
            checks.append({
                "check_id": check_id,
                "description": desc,
                "passed": passed_str.lower() in ("true", "1", "yes"),
                "reason": "",
            })
    summary = write_self_check(loop_dir, args.round, checks)
    print(f"Self-check written for round {args.round}: {summary}")


def _cli_fake_history(args: argparse.Namespace) -> None:
    """Create a fake scoring history with N rounds for testing."""
    loop_dir = Path(args.loop_audit_dir).resolve()
    for i in range(1, args.rounds + 1):
        # last round at score 90
        score = 90.0 if i == args.rounds else 70.0 + i * 3
        append_scoring_history(
            loop_dir,
            round_n=i,
            score=score,
            coverage=0.80 + i * 0.02 if i < args.rounds else 0.97,
            poc_rate=0.33 + i * 0.05 if i < args.rounds else 0.85,
            reconcile_pass=9 if i < args.rounds else 12,
            compliance=0.9,
        )
    print(f"Created fake scoring history: {args.rounds} rounds in {loop_dir}/diag/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="self_evolution.py",
        description="Self-evolution persistence helpers for the audit daemon.",
    )
    parser.add_argument(
        "--loop-audit-dir",
        type=str,
        required=True,
        help="Path to loop_audit directory (e.g. D:\\agentloop\\projects\\org.owasp.webgoat\\loop_audit)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # calculate-convergence
    conv = sub.add_parser("calculate-convergence", help="Read scoring history, compute convergence")
    conv.add_argument("--min-score", type=float, default=85.0)
    conv.add_argument("--max-stddev", type=float, default=3.0)
    conv.add_argument("--min-reconcile-pass", type=int, default=10)
    conv.add_argument("--min-coverage", type=float, default=0.95)
    conv.add_argument("--lookback", type=int, default=3)

    # append-scoring
    sc = sub.add_parser("append-scoring", help="Append a scoring record")
    sc.add_argument("--round", type=int, required=True)
    sc.add_argument("--score", type=float, required=True)
    sc.add_argument("--coverage", type=float, required=True)
    sc.add_argument("--poc-rate", type=float, required=True)
    sc.add_argument("--reconcile-pass", type=int, required=True)
    sc.add_argument("--compliance", type=float, required=True)

    # append-optimization
    opt = sub.add_parser("append-optimization", help="Append an optimization suggestion")
    opt.add_argument("--round", type=int, required=True)
    opt.add_argument("--category", type=str, required=True)
    opt.add_argument("--description", type=str, required=True)
    opt.add_argument("--benefit", type=str, required=True)
    opt.add_argument("--verified", type=lambda x: x.lower() in ("true", "1", "yes"), default=True)

    # write-self-check
    chk = sub.add_parser("write-self-check", help="Write self-check.json")
    chk.add_argument("--round", type=int, required=True)
    chk.add_argument("--checks", nargs="+", default=[], help="check_id:description:passed triples")

    # fake-history (testing utility)
    fake = sub.add_parser("fake-history", help="Create a fake scoring history for testing")
    fake.add_argument("--rounds", type=int, default=5)

    args = parser.parse_args(argv)

    loop_dir = Path(args.loop_audit_dir).resolve()
    if not loop_dir.exists():
        loop_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "calculate-convergence":
        _cli_calculate_convergence(args)
    elif args.command == "append-scoring":
        _cli_append_scoring(args)
    elif args.command == "append-optimization":
        _cli_append_optimization(args)
    elif args.command == "write-self-check":
        _cli_write_self_check(args)
    elif args.command == "fake-history":
        _cli_fake_history(args)
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())