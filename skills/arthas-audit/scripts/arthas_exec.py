#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arthas_exec.py -- Robust Arthas command executor with retry, timeout, and error classification.

This is the recommended entry point for arthas-audit skill workflows. Instead of hand-rolling
HTTP calls through the tunnel server proxy, use this script which handles:
  - Session cookie authentication (with Basic Auth fallback)
  - Automatic retries with exponential backoff on transient failures
  - Configurable timeouts per command
  - Structured error classification (CONNECTION/TIMEOUT/AUTH/AGNET_OFFLINE/ARTHAS_ERROR/OK)
  - JSON output mode for downstream tool consumption

Uses the arthas-config.json produced by arthas-deploy.

Usage:
  # Single command
  python arthas_exec.py exec "version"
  python arthas_exec.py exec "jad com.example.security.JwtFilter doFilter" --timeout 60

  # Specifying config and agent
  python arthas_exec.py exec "sc com.example.*" --config output/arthas-config.json --agent-id my_app

  # JSON output for downstream parsing
  python arthas_exec.py exec "watch C.login '{params}' -n 3" --json

  # Batch execution from file
  python arthas_exec.py batch commands.json
  # commands.json: [{"cmd": "version"}, {"cmd": "thread -n 3", "timeout": 10}]

  # Probe connectivity before audit (workflow 0)
  python arthas_exec.py probe
  # Runs: version, dashboard -n 1, thread -n 3, sysprop
  # Returns 0 only if ALL probes pass

Error codes (exit codes):
  0  - OK
  1  - ARGUMENT_ERROR (bad CLI args, missing config)
  10 - CONNECTION_ERROR (tunnel server unreachable)
  11 - TIMEOUT (Arthas command timed out or agent not responding)
  12 - AUTH_FAILURE (cookies expired, Basic Auth rejected)
  13 - AGENT_OFFLINE (agent not registered in tunnel server)
  20 - ARTHAS_ERROR (command executed but returned an error)
  30 - CONFIG_ERROR (malformed arthas-config.json)
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import requests
    from requests.exceptions import (
        ConnectionError as ReqConnectionError,
        Timeout as ReqTimeout,
        RequestException,
    )
except ImportError:
    print(
        "[arthas_exec] ERROR: requests library required. Install: pip install requests",
        file=sys.stderr,
    )
    sys.exit(1)

# --- Constants ---
DEFAULT_CONFIG = "output/arthas-config.json"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds

PROBE_COMMANDS = [
    {"cmd": "version", "timeout": 5, "purpose": "agent online"},
    {"cmd": "dashboard -n 1", "timeout": 10, "purpose": "JVM health"},
    {"cmd": "thread -n 3", "timeout": 10, "purpose": "thread activity"},
    {"cmd": "sysprop", "timeout": 10, "purpose": "runtime properties"},
]

# Commands that should never be retried (they mutate state)
NO_RETRY_COMMANDS = {
    "mc", "redefine", "retransform", "reset", "stop", "shutdown",
    "vmtool --action interruptThread", "vmtool --action forceGc",
}


# --- Error classification ---
class Status:
    OK = "OK"
    ARGUMENT_ERROR = "ARGUMENT_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT = "TIMEOUT"
    AUTH_FAILURE = "AUTH_FAILURE"
    AGENT_OFFLINE = "AGENT_OFFLINE"
    ARTHAS_ERROR = "ARTHAS_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"

EXIT_CODE_MAP = {
    Status.OK: 0,
    Status.ARGUMENT_ERROR: 1,
    Status.CONNECTION_ERROR: 10,
    Status.TIMEOUT: 11,
    Status.AUTH_FAILURE: 12,
    Status.AGENT_OFFLINE: 13,
    Status.ARTHAS_ERROR: 20,
    Status.CONFIG_ERROR: 30,
}


@dataclass
class ExecResult:
    """Structured result of an Arthas command execution."""
    cmd: str
    agent_id: Optional[str]
    status: str
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    retries: int = 0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_json(self) -> dict:
        return asdict(self)


# --- Config loading ---
def load_config(config_path: str) -> dict:
    """Load and validate arthas-config.json."""
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config: {e}")

    tunnel = data.get("tunnel_server")
    agent = data.get("agent")
    if not tunnel:
        raise ValueError("Missing 'tunnel_server' in config")
    if not tunnel.get("web_endpoint"):
        raise ValueError("Missing 'tunnel_server.web_endpoint' in config")
    if not agent or not agent.get("agent_id"):
        raise ValueError("Missing 'agent.agent_id' in config")
    if data.get("status") not in ("active", None):
        raise ValueError(f"Tunnel status is '{data.get('status')}' — expected 'active'")
    if not agent.get("connected", True):
        raise ConnectionError(f"Agent '{agent.get('agent_id')}' is marked not connected")
    return data


# --- Session authentication ---
class ArthasSession:
    """Handles tunnel server authentication with cookie + Basic Auth fallback."""

    def __init__(self, config: dict):
        self.config = config
        self.tunnel = config["tunnel_server"]
        self.base_url = self.tunnel["web_endpoint"].rstrip("/")
        self.local_url = self.tunnel.get("local_web", self.base_url).rstrip("/")
        self.auth_user = self.tunnel.get("auth_user", "arthas")
        self.auth_password = self.tunnel.get("auth_password")
        self.auth_header = self.tunnel.get("auth_header")
        self.cookie_file = self.tunnel.get("session_cookie_file")
        self.session: requests.Session = requests.Session()
        self._auth_mode: Optional[str] = None  # 'cookie' or 'basic'

        self._init_auth()

    def _init_auth(self) -> None:
        """Try cookie auth first; fall back to Basic Auth; finally attempt fresh login."""
        # 1. Try loading existing cookies
        if self.cookie_file and Path(self.cookie_file).exists():
            try:
                jar = http.cookiejar.LWPCookieJar()
                jar.load(self.cookie_file, ignore_discard=True, ignore_expires=True)
                for cookie in jar:
                    self.session.cookies.set_cookie(cookie)
                # Verify cookies are still valid
                r = self.session.get(
                    f"{self.local_url}/actuator/arthas", timeout=10
                )
                if r.status_code == 200:
                    self._auth_mode = "cookie"
                    return
            except (FileNotFoundError, Exception) as e:
                print(
                    f"[arthas_exec] Cookie load failed ({e}), trying alternatives",
                    file=sys.stderr,
                )

        # 2. Try fresh cookie login
        if self.auth_password:
            try:
                if self._login_fresh():
                    self._auth_mode = "cookie"
                    return
            except Exception as e:
                print(
                    f"[arthas_exec] Fresh cookie login failed: {e}",
                    file=sys.stderr,
                )

        # 3. Fall back to Basic Auth for the rest of this session
        if self.auth_user and self.auth_password:
            self.session.auth = (self.auth_user, self.auth_password)
            self._auth_mode = "basic"
            return

        raise PermissionError(
            "No usable authentication. Provide session_cookie_file, auth_password, "
            "or re-run arthas-deploy."
        )

    def _login_fresh(self) -> bool:
        """Perform a fresh login via /login with CSRF token."""
        try:
            r = self.session.get(f"{self.local_url}/login", timeout=10)
            csrf_match = re.search(
                r'name="_csrf".*?value="([^"]+)"', r.text, re.DOTALL
            ) or re.search(r'value="([^"]+)"[^>]*name="_csrf"', r.text, re.DOTALL)
            if not csrf_match:
                return False
            csrf = csrf_match.group(1)

            r = self.session.post(
                f"{self.local_url}/login",
                data={
                    "username": self.auth_user,
                    "password": self.auth_password,
                    "_csrf": csrf,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
                allow_redirects=False,
            )
            if r.status_code not in (200, 302):
                return False

            # Verify
            r = self.session.get(f"{self.local_url}/actuator/arthas", timeout=10)
            if r.status_code != 200:
                return False

            # Persist cookies
            if self.cookie_file:
                try:
                    jar = http.cookiejar.LWPCookieJar(self.cookie_file)
                    for cookie in self.session.cookies:
                        jar.set_cookie(cookie)
                    jar.save(self.cookie_file, ignore_discard=True, ignore_expires=True)
                except Exception as e:
                    print(
                        f"[arthas_exec] Warning: failed to persist cookies: {e}",
                        file=sys.stderr,
                    )
            return True
        except RequestException:
            return False

    def describe(self) -> str:
        return f"auth_mode={self._auth_mode}"


# --- Executor ---
class ArthasExecutor:
    """Core executor using direct agent HTTP API (port 8563) with retry and timeout."""

    # Known result types that contain actual command output text
    _OUTPUT_TYPES = {
        "version", "jad", "sc", "sm", "dump", "heapdump", "thread",
        "jvm", "memory", "sysprop", "sysenv", "options", "logger",
        "session", "cls", "pwd", "cat", "echo", "base64", "grep",
        "history", "keymap", "perfcounter", "stack", "trace", "monitor",
        "watch", "tt", "ognl", "getstatic", "mbean", "vmoption",
        "vmtool", "profiler", "jfr", "mc", "redefine", "retransform",
        "reset", "stop", "shutdown", "auth", "dashboard", "banner",
    }

    def __init__(self, config: dict, session: ArthasSession, default_timeout: int = DEFAULT_TIMEOUT):
        self.config = config
        self.agent = config["agent"]
        self.tunnel = config["tunnel_server"]
        self.session = session
        self.default_timeout = default_timeout
        # Direct agent HTTP API — agent HTTP server runs on target host's port 8563.
        # For local agents, host defaults to 127.0.0.1.
        # For remote agents, host should be set in agent.host (extracted from
        # tunnel server's /actuator/arthas at deploy time and stored in config).
        agent_host = self.agent.get("host")
        if not agent_host:
            # Last resort: try to read host from tunnel server's registered agents
            agent_id = self.agent.get("agent_id", "")
            agent_host = self._resolve_agent_host_from_tunnel(agent_id) or "127.0.0.1"
        agent_port = self.agent.get("agent_http_port", 8563)
        self.agent_api_url = f"http://{agent_host}:{agent_port}/api"

    def _resolve_agent_host_from_tunnel(self, agent_id: str) -> Optional[str]:
        """Fetch agent's registered host from tunnel server's /actuator/arthas."""
        try:
            r = self.session.session.get(
                f"{self.tunnel['web_endpoint'].rstrip('/')}/actuator/arthas",
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                agents = data.get("agents", {})
                info = agents.get(agent_id, {})
                return info.get("host")
        except Exception:
            pass
        return None

    def _build_payload(self, cmd: str, timeout_ms: int) -> dict:
        return {
            "action": "exec",
            "command": cmd,
            "execTimeout": timeout_ms,
        }

    def _is_retryable(self, cmd: str) -> bool:
        """Mutating commands must never be retried."""
        cmd_lower = cmd.strip().lower()
        for no_retry in NO_RETRY_COMMANDS:
            if cmd_lower.startswith(no_retry.lower()):
                return False
        return True

    def _extract_output(self, result_list: list, cmd: str) -> str:
        """Extract human-readable output text from Arthas JSON result list."""
        if not result_list:
            return ""

        lines = []
        for res in result_list:
            res_type = res.get("type", "")
            # Skip status entries
            if res_type == "status":
                continue
            # Extract the most relevant field for each type
            if res_type == "version":
                lines.append(res.get("version", ""))
            elif res_type == "jad":
                lines.append(res.get("source", ""))
            elif "source" in res:
                lines.append(res["source"])
            elif "command" in res:
                lines.append(res["command"])
            elif res_type == "row_affect":
                lines.append(f"rowCount: {res.get('rowCount', 0)}")
            elif "message" in res:
                lines.append(res["message"])
            elif res_type in self._OUTPUT_TYPES or any(t in res_type for t in self._OUTPUT_TYPES):
                # For other types, serialize the whole dict as readable text
                try:
                    lines.append(json.dumps(res, ensure_ascii=False, indent=2))
                except Exception:
                    lines.append(str(res))
            else:
                # Unknown type — include anyway
                try:
                    lines.append(json.dumps(res, ensure_ascii=False))
                except Exception:
                    lines.append(str(res))

        # If nothing extracted, return raw JSON
        if not lines:
            lines = [json.dumps(r, ensure_ascii=False) for r in result_list if r.get("type") != "status"]

        return "\n".join(lines)

    def _classify_arthas_output(self, output: str) -> str:
        """Detect Arthas-reported errors in the textual output."""
        if not output:
            return Status.ARTHAS_ERROR
        lower = output.lower()
        patterns = [
            r"no class found",
            r"no classloader found",
            r"class\s+\S+\s+not\s+found",
            r"no method found",
            r"error:",
            r"exception",
            r"not support",
            r"agent(?:\s+|-).*offline",
        ]
        for pat in patterns:
            if re.search(pat, lower):
                return Status.ARTHAS_ERROR
        return Status.OK

    def execute(
        self,
        cmd: str,
        agent_id: Optional[str] = None,
        timeout: Optional[int] = None,
        retries: int = DEFAULT_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
    ) -> ExecResult:
        aid = agent_id or self.agent["agent_id"]
        eff_timeout = timeout or self.default_timeout
        url = self.agent_api_url
        payload = self._build_payload(cmd, eff_timeout * 1000)
        can_retry = self._is_retryable(cmd) and retries > 0

        started = time.time()
        last_error = ""
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self.session.session.post(url, json=payload, timeout=eff_timeout)
                if r.status_code == 401 or r.status_code == 403:
                    return ExecResult(
                        cmd=cmd,
                        agent_id=aid,
                        status=Status.AUTH_FAILURE,
                        error=f"HTTP {r.status_code}: {r.text[:200]}",
                        duration_ms=int((time.time() - started) * 1000),
                        retries=attempt - 1,
                    )
                if r.status_code == 404:
                    return ExecResult(
                        cmd=cmd,
                        agent_id=aid,
                        status=Status.AGENT_OFFLINE,
                        error=f"Agent '{aid}' HTTP API not found at {url} (HTTP 404)",
                        duration_ms=int((time.time() - started) * 1000),
                        retries=attempt - 1,
                    )
                if r.status_code >= 500:
                    last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                    if can_retry and attempt <= retries:
                        print(
                            f"[arthas_exec] Retry {attempt}/{retries} — {last_error}",
                            file=sys.stderr,
                        )
                        time.sleep(backoff_base * (2 ** (attempt - 1)))
                        continue
                    return ExecResult(
                        cmd=cmd, agent_id=aid,
                        status=Status.CONNECTION_ERROR, error=last_error,
                        duration_ms=int((time.time() - started) * 1000),
                        retries=attempt - 1,
                    )
                # 200 — parse JSON response from agent HTTP API
                try:
                    data = r.json()
                except Exception as e:
                    return ExecResult(
                        cmd=cmd, agent_id=aid,
                        status=Status.ARTHAS_ERROR,
                        output=r.text[:500],
                        error=f"Failed to parse JSON response: {e}",
                        duration_ms=int((time.time() - started) * 1000),
                        retries=attempt - 1,
                    )

                state = data.get("state", "UNKNOWN")
                if state == "SUCCEEDED":
                    results = data.get("body", {}).get("results", [])
                    output = self._extract_output(results, cmd)
                    arthas_status = self._classify_arthas_output(output)
                    return ExecResult(
                        cmd=cmd,
                        agent_id=aid,
                        status=arthas_status,
                        output=output,
                        error="Arthas returned an error pattern" if arthas_status != Status.OK else "",
                        duration_ms=int((time.time() - started) * 1000),
                        retries=attempt - 1,
                    )
                elif state in ("SCHEDULED", "PROCESSING"):
                    # Async command not yet complete — treat as timeout
                    return ExecResult(
                        cmd=cmd, agent_id=aid,
                        status=Status.TIMEOUT,
                        error=f"Command state '{state}' — agent still processing after {eff_timeout}s",
                        duration_ms=int((time.time() - started) * 1000),
                        retries=attempt - 1,
                    )
                else:
                    # FAILED, REFUSED, or unknown
                    error_msg = data.get("body", {}).get("message", state)
                    return ExecResult(
                        cmd=cmd, agent_id=aid,
                        status=Status.ARTHAS_ERROR,
                        error=f"Arthas {state}: {error_msg}",
                        duration_ms=int((time.time() - started) * 1000),
                        retries=attempt - 1,
                    )

            except ReqTimeout as e:
                last_error = f"Timeout after {eff_timeout}s: {e}"
                if can_retry and attempt <= retries:
                    print(
                        f"[arthas_exec] Retry {attempt}/{retries} — {last_error}",
                        file=sys.stderr,
                    )
                    time.sleep(backoff_base * (2 ** (attempt - 1)))
                    continue
                return ExecResult(
                    cmd=cmd, agent_id=aid,
                    status=Status.TIMEOUT, error=last_error,
                    duration_ms=int((time.time() - started) * 1000),
                    retries=attempt - 1,
                )
            except ReqConnectionError as e:
                last_error = f"Connection failed: {e}"
                if can_retry and attempt <= retries:
                    print(
                        f"[arthas_exec] Retry {attempt}/{retries} — {last_error}",
                        file=sys.stderr,
                    )
                    time.sleep(backoff_base * (2 ** (attempt - 1)))
                    continue
                return ExecResult(
                    cmd=cmd, agent_id=aid,
                    status=Status.CONNECTION_ERROR, error=last_error,
                    duration_ms=int((time.time() - started) * 1000),
                    retries=attempt - 1,
                )
            except RequestException as e:
                return ExecResult(
                    cmd=cmd, agent_id=aid,
                    status=Status.CONNECTION_ERROR,
                    error=f"{type(e).__name__}: {e}",
                    duration_ms=int((time.time() - started) * 1000),
                    retries=attempt - 1,
                )


# --- Subcommands ---
def cmd_exec(args) -> int:
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[arthas_exec] {Status.CONFIG_ERROR}: {e}", file=sys.stderr)
        result = ExecResult(cmd=args.cmd, agent_id=None, status=Status.CONFIG_ERROR, error=str(e))
        if args.json:
            print(json.dumps(result.to_json(), ensure_ascii=False))
        return EXIT_CODE_MAP[Status.CONFIG_ERROR]

    try:
        sess = ArthasSession(config)
    except PermissionError as e:
        result = ExecResult(cmd=args.cmd, agent_id=None, status=Status.AUTH_FAILURE, error=str(e))
        if args.json:
            print(json.dumps(result.to_json(), ensure_ascii=False))
        print(f"[arthas_exec] {result.status}: {e}", file=sys.stderr)
        return EXIT_CODE_MAP[Status.AUTH_FAILURE]

    executor = ArthasExecutor(config, sess, args.timeout)
    result = executor.execute(
        cmd=args.cmd,
        agent_id=args.agent_id,
        timeout=args.timeout,
        retries=args.retries,
    )

    if args.json:
        print(json.dumps(result.to_json(), ensure_ascii=False))
    else:
        if result.status != Status.OK:
            print(f"[arthas_exec] {result.status}: {result.error}", file=sys.stderr)
        sys.stdout.write(result.output)
        if result.output and not result.output.endswith("\n"):
            sys.stdout.write("\n")

    return EXIT_CODE_MAP[result.status]


def cmd_batch(args) -> int:
    try:
        with open(args.file, encoding="utf-8") as f:
            commands = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[arthas_exec] {Status.ARGUMENT_ERROR}: Invalid batch file: {e}", file=sys.stderr)
        return EXIT_CODE_MAP[Status.ARGUMENT_ERROR]

    if not isinstance(commands, list):
        print("[arthas_exec] Batch file must contain a JSON array", file=sys.stderr)
        return EXIT_CODE_MAP[Status.ARGUMENT_ERROR]

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[arthas_exec] {Status.CONFIG_ERROR}: {e}", file=sys.stderr)
        return EXIT_CODE_MAP[Status.CONFIG_ERROR]

    try:
        sess = ArthasSession(config)
    except PermissionError as e:
        print(f"[arthas_exec] {Status.AUTH_FAILURE}: {e}", file=sys.stderr)
        return EXIT_CODE_MAP[Status.AUTH_FAILURE]

    executor = ArthasExecutor(config, sess)
    results = []
    any_failed = False
    for entry in commands:
        cmd = entry.get("cmd")
        if not cmd:
            continue
        r = executor.execute(
            cmd=cmd,
            agent_id=entry.get("agent_id") or args.agent_id,
            timeout=entry.get("timeout") or args.timeout,
            retries=args.retries,
        )
        results.append(r.to_json())
        if r.status != Status.OK:
            any_failed = True

    print(json.dumps({
        "session_id": f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "commands_run": len(results),
        "commands_ok": sum(1 for r in results if r["status"] == Status.OK),
        "commands_failed": sum(1 for r in results if r["status"] != Status.OK),
        "results": results,
    }, indent=2, ensure_ascii=False))

    return 1 if any_failed else 0


def cmd_probe(args) -> int:
    """Workflow 0 — validate connectivity before starting any audit."""
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[arthas_exec] {Status.CONFIG_ERROR}: {e}", file=sys.stderr)
        return EXIT_CODE_MAP[Status.CONFIG_ERROR]

    try:
        sess = ArthasSession(config)
    except PermissionError as e:
        print(f"[arthas_exec] {Status.AUTH_FAILURE}: {e}", file=sys.stderr)
        return EXIT_CODE_MAP[Status.AUTH_FAILURE]

    executor = ArthasExecutor(config, sess)
    print(f"[arthas_exec] Running probe ({len(PROBE_COMMANDS)} commands) against agent "
          f"'{config['agent']['agent_id']}' via {executor.agent_api_url} ({sess.describe()})...", file=sys.stderr)

    results = []
    failed = []
    for probe in PROBE_COMMANDS:
        r = executor.execute(
            cmd=probe["cmd"],
            timeout=probe["timeout"],
            retries=1,
        )
        results.append({"cmd": probe["cmd"], "purpose": probe["purpose"],
                        "status": r.status, "duration_ms": r.duration_ms})
        if r.status != Status.OK:
            failed.append(probe["cmd"])
        marker = "OK" if r.status == Status.OK else f"FAIL({r.status})"
        print(f"  [{marker:24s}] {probe['purpose']:<25s} {probe['cmd']}", file=sys.stderr)

    if args.json:
        print(json.dumps({"probe_results": results, "ok": not failed}, ensure_ascii=False, indent=2))
    else:
        if failed:
            print(f"[arthas_exec] PROBE FAILED: {len(failed)}/{len(PROBE_COMMANDS)} commands failed → "
                  f"DO NOT proceed with audit workflows.", file=sys.stderr)
        else:
            print("[arthas_exec] PROBE OK: All preconditions met. Proceed with audit.", file=sys.stderr)

    return 1 if failed else 0


# --- CLI ---
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arthas_exec",
        description="Robust Arthas command executor (session cookie auth + retry + typed errors)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", "-c", default=DEFAULT_CONFIG,
        help=f"Path to arthas-config.json (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument("--agent-id", default=None, help="Override agent id")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                        help=f"Retry count for transient failures (default: {DEFAULT_RETRIES})")
    sub = parser.add_subparsers(dest="command", required=True)

    # exec
    p_exec = sub.add_parser("exec", help="Execute a single Arthas command")
    p_exec.add_argument("cmd", help='Arthas command, e.g. "version" or "jad com.example.X m"')
    p_exec.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Command timeout in seconds (default: {DEFAULT_TIMEOUT})")
    p_exec.add_argument("--json", action="store_true",
                        help="Output result as JSON instead of raw command output")

    # batch
    p_batch = sub.add_parser("batch", help="Execute multiple commands from a JSON array file")
    p_batch.add_argument("file", help="JSON file: [{\"cmd\": \"...\", \"timeout\": N}, ...]")
    p_batch.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)

    # probe
    p_probe = sub.add_parser("probe", help="Workflow 0: pre-audit connectivity probe")
    p_probe.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p_probe.add_argument("--json", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "exec":
        return cmd_exec(args)
    if args.command == "batch":
        return cmd_batch(args)
    if args.command == "probe":
        return cmd_probe(args)

    parser.print_help()
    return EXIT_CODE_MAP[Status.ARGUMENT_ERROR]


if __name__ == "__main__":
    sys.exit(main())
