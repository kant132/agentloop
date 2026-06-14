#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy_transcript.py -- Idempotent JSONL transcript logger for arthas-deploy.

Records every phase/step/command of an arthas-deploy run as line-delimited JSON,
enabling post-mortem reconstruction of the execution trace.

**Idempotency contract (per user requirement: 每次部署都写一个，要先检查是否已经存在了):**
  - If transcript file exists → APPEND entries to it (do not overwrite / truncate)
  - If transcript file does not exist → CREATE with a `session_start` header line
  - If `new-session` is called explicitly → always append a new session header (multiple
    sessions may coexist in one JSONL file; each is delimited by its session_start line)

Line schema:
  Session header:  {"_type":"session_start","session_id":"...","started_at":"...","host":"...","user":"..."}
  Log entry:       {"_type":"log","ts":"...","phase":"...","step":"...","command":"...","status":"ok|fail","result":"...","error":"...","duration_ms":N}
  Session close:   {"_type":"session_end","session_id":"...","ended_at":"...","phases_completed":[...],"total_duration_ms":N}

Usage:
  # Begin a new deployment session (idempotent: creates file if missing, always appends)
  python deploy_transcript.py new-session [--transcript output/deploy-transcript.jsonl]

  # Log a step during deployment
  python deploy_transcript.py log \\
      --phase 0 --step "0.3-start-tunnel" \\
      --command "Start-Process java -ArgumentList ..." \\
      --status ok --duration-ms 42

  # Log a failure
  python deploy_transcript.py log \\
      --phase 4 --step "4.D-verify-agent" \\
      --command "curl.exe actuator" \\
      --status fail --error "HTTP 401 - cookie expired" --duration-ms 3100

  # Summarize the current session
  python deploy_transcript.py status

  # Close a session (records total elapsed + phases completed)
  python deploy_transcript.py close --phases 0,1,2,3,4

  # Query log entries (by phase, status, or command substring)
  python deploy_transcript.py query --phase 4
  python deploy_transcript.py query --status fail
  python deploy_transcript.py query --command "curl"

Output file (default: output/deploy-transcript.jsonl):
  - Append-only JSONL
  - Multiple sessions may coexist; each delimited by `_type: session_start`
  - Consumers may filter with `jq` or any JSONL-aware tool

Exit codes:
  0 - success
  1 - argument error
  2 - I/O error (permission, disk full)
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List


DEFAULT_TRANSCRIPT = "output/deploy-transcript.jsonl"


# --- Helpers ---
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_epoch_ms() -> int:
    return int(time.time() * 1000)


def make_session_id() -> str:
    """Generate a unique session id: timestamp_host-alias_pid."""
    host_short = socket.gethostname().split(".")[0].lower()
    pid = os.getpid()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{host_short}-{pid}"


def ensure_parent_dir(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[transcript] Failed to create parent dir: {e}", file=sys.stderr)
        sys.exit(2)


def append_line(path: Path, obj: dict) -> None:
    """Append a single JSON line (idempotent: create if missing, append if exists)."""
    ensure_parent_dir(path)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[transcript] Write failed: {e}", file=sys.stderr)
        sys.exit(2)


def read_entries(path: Path) -> List[dict]:
    """Read all JSONL entries from the transcript file."""
    if not path.exists():
        return []
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(
                        f"[transcript] Warning: malformed JSON at line {lineno}: {e}",
                        file=sys.stderr,
                    )
    except OSError as e:
        print(f"[transcript] Read failed: {e}", file=sys.stderr)
        return []
    return entries


def find_current_session(entries: List[dict]) -> Optional[dict]:
    """Return the most recent session_start entry (the 'current' session)."""
    for entry in reversed(entries):
        if isinstance(entry, dict) and entry.get("_type") == "session_start":
            return entry
    return None


# --- Subcommands ---
def cmd_new_session(args) -> int:
    path = Path(args.transcript)
    session_id = args.session_id or make_session_id()

    header = {
        "_type": "session_start",
        "session_id": session_id,
        "started_at": now_iso(),
        "started_at_ms": now_epoch_ms(),
        "host": socket.gethostname(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "platform": platform.system(),
        "python": platform.python_version(),
        "argv": " ".join(sys.argv),
    }

    if path.exists():
        # Idempotent: file exists → append a new session header (multi-session file supported)
        print(f"[transcript] File exists, appending new session '{session_id}' "
              f"to {path}", file=sys.stderr)
    else:
        print(f"[transcript] Creating new transcript at {path} "
              f"(session '{session_id}')", file=sys.stderr)

    append_line(path, header)

    if args.json:
        print(json.dumps({"status": "created", "session_id": session_id,
                          "path": str(path)}, ensure_ascii=False))
    else:
        print(f"[transcript] Session started: {session_id}")
    return 0


def cmd_log(args) -> int:
    path = Path(args.transcript)
    entries = read_entries(path)
    cur = find_current_session(entries)

    if not cur:
        # Auto-begin a session if none exists
        print("[transcript] No active session — auto-beginning one (call 'new-session' "
              "explicitly for a known id)", file=sys.stderr)
        cmd_new_session(argparse.Namespace(
            transcript=args.transcript, session_id=None, json=False,
        ))

    entry = {
        "_type": "log",
        "ts": now_iso(),
        "ts_ms": now_epoch_ms(),
        "session_id": cur.get("session_id") if cur else "auto",
        "phase": args.phase,
        "step": args.step,
        "command": args.cmd_payload,
        "status": args.status,
        "result": args.result or "",
        "error": args.error or "",
        "duration_ms": args.duration_ms,
    }
    append_line(path, entry)

    if args.json:
        print(json.dumps(entry, ensure_ascii=False))
    else:
        marker = "OK" if args.status == "ok" else "FAIL"
        dur = f"{args.duration_ms}ms" if args.duration_ms is not None else "-"
        print(f"[transcript] [{marker}] phase={args.phase} step={args.step} "
              f"dur={dur} cmd={args.cmd_payload[:60] if args.cmd_payload else ''}")
    return 0


def cmd_close(args) -> int:
    path = Path(args.transcript)
    entries = read_entries(path)
    cur = find_current_session(entries)
    if not cur:
        print("[transcript] No active session to close", file=sys.stderr)
        return 1

    start_ms = cur.get("started_at_ms", 0)
    elapsed_ms = now_epoch_ms() - start_ms if start_ms else 0
    phases = [p.strip() for p in args.phases.split(",") if p.strip()] if args.phases else []

    close_record = {
        "_type": "session_end",
        "session_id": cur.get("session_id"),
        "ended_at": now_iso(),
        "ended_at_ms": now_epoch_ms(),
        "phases_completed": phases,
        "total_duration_ms": elapsed_ms,
        "log_entries": sum(1 for e in entries if isinstance(e, dict) and e.get("_type") == "log"),
    }
    append_line(path, close_record)

    print(f"[transcript] Session closed: {cur.get('session_id')} "
          f"(elapsed={elapsed_ms}ms, phases={phases})", file=sys.stderr)
    return 0


def cmd_status(args) -> int:
    path = Path(args.transcript)
    if not path.exists():
        print(f"[transcript] No transcript file at {path}", file=sys.stderr)
        return 1

    entries = read_entries(path)
    sessions = [e for e in entries if isinstance(e, dict) and e.get("_type") == "session_start"]
    closed = [e for e in entries if isinstance(e, dict) and e.get("_type") == "session_end"]
    closed_ids = {e.get("session_id") for e in closed}
    logs = [e for e in entries if isinstance(e, dict) and e.get("_type") == "log"]

    cur = find_current_session(entries)
    has_active = cur is not None and cur.get("session_id") not in closed_ids

    if has_active:
        active_id = cur.get("session_id")
        active_logs = [e for e in logs if e.get("session_id") == active_id]
        ok = sum(1 for e in active_logs if e.get("status") == "ok")
        fail = len(active_logs) - ok
        phases = sorted({str(e.get("phase")) for e in active_logs if e.get("phase") is not None})
    else:
        active_id = None
        active_logs = []
        ok = 0
        fail = 0
        phases = []

    if args.json:
        if has_active:
            output = {
                "transcript_path": str(path),
                "active_session": active_id,
                "started_at": cur.get("started_at"),
                "total_sessions": len(sessions),
                "closed_sessions": len(closed),
                "log_entries_in_active_session": len(active_logs),
                "ok_count": ok,
                "fail_count": fail,
                "phases_seen": phases,
            }
        else:
            output = {
                "transcript_path": str(path),
                "active_session": None,
                "total_sessions": len(sessions),
                "closed_sessions": len(closed),
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    # Human-readable
    print(f"[transcript] File: {path}")
    print(f"[transcript] Total sessions recorded: {len(sessions)} (closed: {len(closed)})")
    print(f"[transcript] Total log entries: {len(logs)}")

    if has_active:
        print(f"[transcript] Active session: {active_id} (started {cur.get('started_at')})")
        print(f"[transcript]  - Logs: {len(active_logs)} ({ok} ok, {fail} fail)")
        print(f"[transcript]  - Phases seen: {', '.join(phases) if phases else 'none'}")
    else:
        print("[transcript] No active session (all closed)")
    return 0


def cmd_query(args) -> int:
    path = Path(args.transcript)
    entries = read_entries(path)
    logs = [e for e in entries if isinstance(e, dict) and e.get("_type") == "log"]

    filtered = logs
    if args.session_id:
        filtered = [e for e in filtered if e.get("session_id") == args.session_id]
    if args.phase is not None:
        # Allow both exact int and string match
        filtered = [e for e in filtered
                    if str(e.get("phase")) == str(args.phase)]
    if args.step:
        filtered = [e for e in filtered if args.step in (e.get("step") or "")]
    if args.cmd_filter:
        filtered = [e for e in filtered if args.cmd_filter in (e.get("command") or "")]
    if args.status:
        filtered = [e for e in filtered if e.get("status") == args.status]
    if args.last:
        filtered = filtered[-args.last:]

    if args.json:
        print(json.dumps({"query": {
            "phase": args.phase, "step": args.step, "command": args.cmd_filter,
            "status": args.status, "session_id": args.session_id,
        }, "match_count": len(filtered), "results": filtered}, ensure_ascii=False, indent=2))
    else:
        if not filtered:
            print("[transcript] No matching entries")
            return 0
        print(f"[transcript] {len(filtered)} matching entr{'y' if len(filtered) == 1 else 'ies'}:")
        for e in filtered:
            marker = "OK  " if e.get("status") == "ok" else "FAIL"
            dur = f"{e.get('duration_ms', 0)}ms" if e.get("duration_ms") is not None else "-"
            print(f"  [{marker}] phase={e.get('phase'):<4} step={e.get('step'):<30} "
                  f"dur={dur:>8} cmd={(e.get('command') or '')[:50]}")
            if e.get("error"):
                print(f"        error: {e.get('error')}")
    return 0


# --- CLI ---
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="deploy_transcript",
        description="Idempotent JSONL transcript logger for arthas-deploy",
        epilog=__doc__,
    )
    ap.add_argument(
        "--transcript", "-t", default=DEFAULT_TRANSCRIPT,
        help=f"Transcript file path (default: {DEFAULT_TRANSCRIPT})",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # new-session
    p_new = sub.add_parser("new-session",
                           help="Start a new deployment session (idempotent: appends if file exists)")
    p_new.add_argument("--session-id", help="Custom session id (default: auto-generated)")
    p_new.add_argument("--json", action="store_true")

    # log
    p_log = sub.add_parser("log", help="Append a log entry for a deployment step")
    p_log.add_argument("--phase", required=True, help="Phase number or name (e.g. '0', 'start-tunnel')")
    p_log.add_argument("--step", required=True, help="Step identifier (e.g. '0.3-start', '4.D-verify')")
    p_log.add_argument("--cmd", required=True, dest="cmd_payload",
                       help="Command or action description that was executed")
    p_log.add_argument("--status", required=True, choices=["ok", "fail"], help="ok or fail")
    p_log.add_argument("--result", help="Optional result payload (short text — do not paste huge logs)")
    p_log.add_argument("--error", help="Error message if status=fail")
    p_log.add_argument("--duration-ms", type=int, help="Step duration in milliseconds")
    p_log.add_argument("--json", action="store_true")

    # close
    p_close = sub.add_parser("close", help="Close the current session with summary")
    p_close.add_argument("--phases", help="Comma-separated list of completed phases (e.g. '0,1,4,5')")

    # status
    p_status = sub.add_parser("status", help="Summarize the current session")
    p_status.add_argument("--json", action="store_true")

    # query
    p_q = sub.add_parser("query", help="Query log entries by filter")
    p_q.add_argument("--session-id", help="Filter by session id")
    p_q.add_argument("--phase", help="Filter by phase (int or string)")
    p_q.add_argument("--step", help="Substring filter on step")
    p_q.add_argument("--cmd", help="Substring filter on command", dest="cmd_filter")
    p_q.add_argument("--status", choices=["ok", "fail"])
    p_q.add_argument("--last", type=int, help="Return only the last N matches")
    p_q.add_argument("--json", action="store_true")

    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    handlers = {
        "new-session": cmd_new_session,
        "log": cmd_log,
        "close": cmd_close,
        "status": cmd_status,
        "query": cmd_query,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
