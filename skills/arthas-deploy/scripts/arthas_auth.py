#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arthas_auth.py -- Arthas Tunnel Server Session Cookie Authentication Tool

Usage:
  python arthas_auth.py login <tunnel_url> <username> <password> [--cookie-file FILE]
  python arthas_auth.py check <tunnel_url> [--cookie-file FILE]
  python arthas_auth.py agents <tunnel_url> [--cookie-file FILE]
  python arthas_auth.py exec <tunnel_url> <agent_id> <cmd> [--cookie-file FILE]
  python arthas_auth.py wait <agent_id> <tunnel_url> [--timeout N] [--cookie-file FILE]

Examples:
  # Login and save cookie
  python arthas_auth.py login http://127.0.0.1:8080 arthas my-password-here

  # Check connection status
  python arthas_auth.py check http://127.0.0.1:8080

  # List registered agents
  python arthas_auth.py agents http://127.0.0.1:8080 --cookie-file my-cookies.txt

  # Wait for agent registration
  python arthas_auth.py wait mathgame_local_32656 http://127.0.0.1:8080

  # Execute Arthas command
  python arthas_auth.py exec http://127.0.0.1:8080 mathgame_local_32656 "version"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import socket
import urllib.parse
from pathlib import Path

try:
    import http.cookiejar
    import requests
except ImportError:
    print("[arthas_auth] ERROR: requests library required. Install: pip install requests", file=sys.stderr)
    sys.exit(1)

DEFAULT_COOKIE_FILE = "arthas_cookies.txt"
TIMEOUT = 10


def get_csrf(session: requests.Session, login_url: str) -> str | None:
    """Get CSRF token from login page"""
    try:
        r = session.get(login_url, timeout=TIMEOUT)
        match = re.search(r'name="_csrf".*?value="([^"]+)"', r.text, re.DOTALL)
        if match:
            return match.group(1)
        match = re.search(r'value="([^"]+)"[^>]*name="_csrf"', r.text, re.DOTALL)
        if match:
            return match.group(1)
        return None
    except requests.RequestException as e:
        print(f"[arthas_auth] Failed to get CSRF: {e}", file=sys.stderr)
        return None


def load_cookies(cookie_file: str) -> requests.Session:
    """Load cookies from file into requests.Session"""
    session = requests.Session()
    try:
        lwp_cookie = http.cookiejar.LWPCookieJar()
        lwp_cookie.load(cookie_file, ignore_discard=True, ignore_expires=True)
        for cookie in lwp_cookie:
            session.cookies.set_cookie(cookie)
        return session
    except FileNotFoundError:
        raise
    except Exception as e:
        raise Exception(f"Failed to load cookie: {e}")


def login(tunnel_url: str, username: str, password: str, cookie_file: str = DEFAULT_COOKIE_FILE) -> int:
    """Login to tunnel server and save session cookie. Returns: 0 success, 1 failure"""
    base_url = tunnel_url.rstrip('/')
    login_url = f"{base_url}/login"
    actuator_url = f"{base_url}/actuator/arthas"

    session = requests.Session()

    csrf_token = get_csrf(session, login_url)
    if not csrf_token:
        print("[arthas_auth] ERROR: Failed to get CSRF token", file=sys.stderr)
        return 1

    try:
        r = session.post(
            login_url,
            data={
                "username": username,
                "password": password,
                "_csrf": csrf_token
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
            allow_redirects=False
        )

        if r.status_code == 302 and r.headers.get("Location") in (None, "/", f"{base_url}/"):
            print("[arthas_auth] Login successful")
        else:
            print(f"[arthas_auth] Login failed: HTTP {r.status_code}", file=sys.stderr)
            print(f"  Location: {r.headers.get('Location')}", file=sys.stderr)
            return 1

    except requests.RequestException as e:
        print(f"[arthas_auth] Login request failed: {e}", file=sys.stderr)
        return 1

    try:
        verify_resp = session.get(actuator_url, timeout=TIMEOUT)
        if verify_resp.status_code == 200:
            data = verify_resp.json()
            agent_count = len(data.get("agents", {}))
            print(f"[arthas_auth] Verified {agent_count} agent(s) registered")
        else:
            print(f"[arthas_auth] Warning: Verification returned {verify_resp.status_code}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"[arthas_auth] Warning: Verification request failed: {e}", file=sys.stderr)

    try:
        lwp_cookie = http.cookiejar.LWPCookieJar(cookie_file)
        for cookie in session.cookies:
            lwp_cookie.set_cookie(cookie)
        lwp_cookie.save(cookie_file, ignore_discard=True, ignore_expires=True)
        print(f"[arthas_auth] Cookie saved to: {cookie_file}")
    except Exception as e:
        print(f"[arthas_auth] Failed to save cookie: {e}", file=sys.stderr)
        return 1

    return 0


def check_connection(tunnel_url: str, cookie_file: str = DEFAULT_COOKIE_FILE) -> int:
    """Check tunnel server connection status. Returns: 0 healthy, 1 unhealthy"""
    base_url = tunnel_url.rstrip('/')

    try:
        session = load_cookies(cookie_file)
    except FileNotFoundError:
        print(f"[arthas_auth] Cookie file not found: {cookie_file}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[arthas_auth] Failed to load cookie: {e}", file=sys.stderr)
        return 1

    # Check port connectivity
    ws_match = re.search(r'ws://[^:]+:(\d+)', tunnel_url)
    http_match = re.search(r'http://[^:]+:(\d+)', tunnel_url)

    port_to_check = None
    if ws_match:
        ws_port = int(ws_match.group(1))
        port_to_check = ws_port + 313 if ws_port == 7777 else ws_port + 1
    elif http_match:
        port_to_check = int(http_match.group(1))

    if port_to_check:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('127.0.0.1', port_to_check))
            sock.close()
            if result == 0:
                print(f"[arthas_auth] Port {port_to_check} is listening")
            else:
                print(f"[arthas_auth] Port {port_to_check} is NOT reachable", file=sys.stderr)
                return 1
        except Exception as e:
            print(f"[arthas_auth] Port check failed: {e}", file=sys.stderr)

    try:
        r = session.get(f"{base_url}/actuator/arthas", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            version = data.get("version", "unknown")
            agents = data.get("agents", {})
            print(f"[arthas_auth] Tunnel server v{version} is healthy")
            print(f"[arthas_auth] Registered agents: {len(agents)}")
            for agent_id, info in agents.items():
                print(f"  - {agent_id}: {info.get('arthasVersion', '?')} @ {info.get('host')}:{info.get('port')}")
            return 0
        else:
            print(f"[arthas_auth] Actuator returned {r.status_code}", file=sys.stderr)
            return 1
    except requests.RequestException as e:
        print(f"[arthas_auth] Actuator check failed: {e}", file=sys.stderr)
        return 1


def get_agents(tunnel_url: str, cookie_file: str = DEFAULT_COOKIE_FILE) -> int:
    """Get list of registered agents. Returns: 0 success, 1 failure"""
    base_url = tunnel_url.rstrip('/')

    try:
        session = load_cookies(cookie_file)
    except FileNotFoundError:
        print(f"[arthas_auth] Cookie file not found: {cookie_file}", file=sys.stderr)
        print("[arthas_auth] Hint: Run 'arthas_auth.py login' first", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[arthas_auth] Failed to load cookie: {e}", file=sys.stderr)
        return 1

    try:
        r = session.get(f"{base_url}/actuator/arthas", timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[arthas_auth] HTTP {r.status_code}", file=sys.stderr)
            return 1

        data = r.json()
        agents = data.get("agents", {})

        if not agents:
            print("[arthas_auth] No agents registered")
            return 0

        print(json.dumps(agents, indent=2))
        return 0

    except requests.RequestException as e:
        print(f"[arthas_auth] Request failed: {e}", file=sys.stderr)
        return 1


def exec_command(tunnel_url: str, agent_id: str, cmd: str, cookie_file: str = DEFAULT_COOKIE_FILE) -> int:
    """Execute Arthas command via proxy. Returns: 0 success, 1 failure"""
    base_url = tunnel_url.rstrip('/')

    try:
        session = load_cookies(cookie_file)
    except FileNotFoundError:
        print(f"[arthas_auth] Cookie file not found: {cookie_file}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[arthas_auth] Failed to load cookie: {e}", file=sys.stderr)
        return 1

    proxy_url = f"{base_url}/proxy/{agent_id}/"
    params = {
        "method": "execArthasCommand",
        "cmd": cmd
    }

    try:
        r = session.get(proxy_url, params=params, timeout=TIMEOUT)
        print(r.text)
        return 0
    except requests.RequestException as e:
        print(f"[arthas_auth] Command failed: {e}", file=sys.stderr)
        return 1


def wait_for_agent(tunnel_url: str, agent_id: str, timeout: int = 30, cookie_file: str = DEFAULT_COOKIE_FILE) -> int:
    """Wait for agent to register. Returns: 0 success (registered), 1 timeout/failure"""
    base_url = tunnel_url.rstrip('/')

    try:
        session = load_cookies(cookie_file)
    except FileNotFoundError:
        print(f"[arthas_auth] Cookie file not found: {cookie_file}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[arthas_auth] Failed to load cookie: {e}", file=sys.stderr)
        return 1

    print(f"[arthas_auth] Waiting up to {timeout}s for agent '{agent_id}' to register...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            r = session.get(f"{base_url}/actuator/arthas", timeout=5)
            if r.status_code == 200:
                data = r.json()
                agents = data.get("agents", {})
                if agent_id in agents:
                    info = agents[agent_id]
                    print(f"[arthas_auth] Agent '{agent_id}' registered: {info.get('arthasVersion')} @ {info.get('host')}:{info.get('port')}")
                    return 0
        except requests.RequestException:
            pass

        time.sleep(1)

    print(f"[arthas_auth] Timeout: agent '{agent_id}' not registered after {timeout}s", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Arthas Tunnel Server Session Cookie Authentication Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # login
    login_parser = subparsers.add_parser("login", help="Login to tunnel server and save session cookie")
    login_parser.add_argument("tunnel_url", help="Tunnel server URL (e.g., http://127.0.0.1:8080)")
    login_parser.add_argument("username", help="Username")
    login_parser.add_argument("password", help="Password")
    login_parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE, help=f"Cookie file path (default: {DEFAULT_COOKIE_FILE})")

    # check
    check_parser = subparsers.add_parser("check", help="Check tunnel server connection status")
    check_parser.add_argument("tunnel_url", help="Tunnel server URL")
    check_parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE, help=f"Cookie file path (default: {DEFAULT_COOKIE_FILE})")

    # agents
    agents_parser = subparsers.add_parser("agents", help="List registered agents")
    agents_parser.add_argument("tunnel_url", help="Tunnel server URL")
    agents_parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE, help=f"Cookie file path (default: {DEFAULT_COOKIE_FILE})")

    # exec
    exec_parser = subparsers.add_parser("exec", help="Execute Arthas command via proxy")
    exec_parser.add_argument("tunnel_url", help="Tunnel server URL")
    exec_parser.add_argument("agent_id", help="Agent ID")
    exec_parser.add_argument("cmd", help="Arthas command")
    exec_parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE, help=f"Cookie file path (default: {DEFAULT_COOKIE_FILE})")

    # wait
    wait_parser = subparsers.add_parser("wait", help="Wait for agent to register")
    wait_parser.add_argument("agent_id", help="Agent ID to wait for")
    wait_parser.add_argument("tunnel_url", help="Tunnel server URL")
    wait_parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds (default: 30)")
    wait_parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE, help=f"Cookie file path (default: {DEFAULT_COOKIE_FILE})")

    args = parser.parse_args()

    if args.command == "login":
        return login(args.tunnel_url, args.username, args.password, args.cookie_file)
    elif args.command == "check":
        return check_connection(args.tunnel_url, args.cookie_file)
    elif args.command == "agents":
        return get_agents(args.tunnel_url, args.cookie_file)
    elif args.command == "exec":
        return exec_command(args.tunnel_url, args.agent_id, args.cmd, args.cookie_file)
    elif args.command == "wait":
        return wait_for_agent(args.tunnel_url, args.agent_id, args.timeout, args.cookie_file)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())