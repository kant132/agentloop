"""Run the test suite and produce a JSON report at test-output/test-results.json.

Uses unittest.TestProgram to drive tests, then computes counts from the
TextTestResult, and writes a structured JSON file with TC-level details.
"""
import datetime
import io
import json
import os
import re
import sys
import time
import unittest


# Per-TC label parsing: docstring format is "TC-XXX-NNN: <desc>"
_TC_RE = re.compile(r"^TC-[A-Z]+-\d+:\s*(.*)")


def _make_label(test_case, method_name=None):
    doc = (test_case.shortDescription() or test_case._testMethodDoc or "").strip()
    m = _TC_RE.match(doc)
    return m.group(1).strip() if m else doc


def _tc_id(test_case):
    doc = (test_case.shortDescription() or test_case._testMethodDoc or "").strip()
    m = _TC_RE.match(doc)
    return m.group(0).split(":")[0] if m else ""


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out_dir = os.path.join(root, "test-output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "test-results.json")

    # Discover all test_*.py under test_scripts/
    loader = unittest.TestLoader()
    start = os.path.dirname(os.path.abspath(__file__))
    suite = loader.discover(
        start_dir=start,
        pattern="test_*.py",
        top_level_dir=root,
    )
    n_tests = suite.countTestCases()
    print(f"Discovered {n_tests} tests from {start}", file=sys.stderr)

    t0 = time.time()
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)
    duration = time.time() - t0

    # Build TC-level detail
    tc_rows = []
    passed = failed = errored = skipped = 0
    error_set = {id(c) for c, _ in result.errors}
    fail_set = {id(c) for c, _ in result.failures}
    for case, reason in result.failures + result.errors:
        status = "ERROR" if id(case) in error_set else "FAIL"
        tc_rows.append({
            "tc": _tc_id(case),
            "module": case.__class__.__module__,
            "name": case._testMethodName,
            "status": status,
            "reason": (reason or "").splitlines()[-1] if reason else "",
        })
    for case, _ in result.errors:
        errored += 1
    for case, _ in result.failures:
        failed += 1

    skipped = len(result.skipped)

    # Walk the original suite to count successes
    total = result.testsRun

    # Build successes (everything not in failures/errors/skipped).
    # Re-discover a fresh suite (original may be exhausted after run).
    suite2 = loader.discover(start_dir=start, pattern="test_*.py", top_level_dir=root)
    # Use stable key: (module, class, method) instead of id()
    fail_keys = {
        (c.__class__.__module__, c.__class__.__name__, c._testMethodName)
        for c, _ in result.failures + result.errors + result.skipped
    }
    walked = 0
    for ts in _walk(suite2):
        walked += 1
        key = (ts.__class__.__module__, ts.__class__.__name__, ts._testMethodName)
        if key not in fail_keys:
            tc_rows.append({
                "tc": _tc_id(ts),
                "module": ts.__class__.__module__,
                "name": ts._testMethodName,
                "status": "PASS",
                "reason": "",
            })
    passed = sum(1 for r in tc_rows if r["status"] == "PASS")
    print(f"Walker enumerated {walked} test cases; passed={passed}", file=sys.stderr)
    # Skipped rows
    for case, reason in result.skipped:
        tc_rows.append({
            "tc": _tc_id(case),
            "module": case.__class__.__module__,
            "name": case._testMethodName,
            "status": "SKIP",
            "reason": reason,
        })

    # Sort by tc id
    tc_rows.sort(key=lambda r: (r["tc"], r["name"]))

    report = {
        "ran_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "total": total,
        "passed": passed,
        "failed": failed,
        "error": errored,
        "skipped": skipped,
        "coverage_percent": None,  # would need coverage.py
        "duration_sec": round(duration, 3),
        "failures": [
            {
                "tc": r["tc"],
                "module": r["module"],
                "name": r["name"],
                "status": r["status"],
                "reason": r["reason"],
            }
            for r in tc_rows
            if r["status"] in ("FAIL", "ERROR", "SKIP")
        ],
        "details": tc_rows,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nWrote {out_path}")
    print(f"Total={total} Passed={passed} Failed={failed} Error={errored} Skipped={skipped} Duration={duration:.3f}s")


def _walk(suite):
    for ts in suite:
        if ts is None:
            continue
        if isinstance(ts, unittest.TestSuite):
            yield from _walk(ts)
        else:
            yield ts


if __name__ == "__main__":
    main()
