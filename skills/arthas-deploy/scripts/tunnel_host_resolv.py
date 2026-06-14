#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tunnel_host_resolv.py -- Local NIC Filter

Receives list of candidate client IPs from remote host via SSH,
returns the first one that matches this machine's actual network interface.

Usage:
  python tunnel_host_resolv.py <ip1> <ip2> ...
  python tunnel_host_resolv.py <ip1> <ip2> --prefer <ip1>
  python tunnel_host_resolv.py <ip1> ... --debug

Exit codes:
  0 - success, stdout prints single IP
  1 - empty input / no local NICs / no intersection
  2 - unsupported system / cannot get local NICs
  50 - argument error
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse
import json
import platform
import re
import subprocess
import sys

EXCLUDE_NIC_KEYWORDS = (
    "loopback", "vethernet", "docker", "hyper-v",
    "wsl", "vmware", "vmnet", "virtualbox", "tap-windows"
)

VALID_IPV4 = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def err(msg: str) -> None:
    print(f"[tunnel_host_resolv] {msg}", file=sys.stderr)


def is_excluded(alias: str) -> bool:
    low = alias.lower()
    return any(k in low for k in EXCLUDE_NIC_KEYWORDS)


def is_reserved(ip: str) -> bool:
    return ip.startswith("127.") or ip.startswith("169.254.") or ip.startswith("0.")


def get_local_ips() -> dict[str, str]:
    """返回 {ip: interface_alias} 的对外网卡字典。"""
    sys_name = platform.system()
    result: dict[str, str] = {}

    if sys_name == "Windows":
        ps_script = (
            "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue "
            "| Select-Object IPAddress, InterfaceAlias, InterfaceMetric, PrefixLength "
            "| ConvertTo-Json -Compress -Depth 3"
        )
        try:
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=20,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return result
            data = json.loads(res.stdout)
            if isinstance(data, dict):
                data = [data]
            for entry in data:
                ip = entry.get("IPAddress", "")
                alias = entry.get("InterfaceAlias", "") or ""
                if not VALID_IPV4.match(ip):
                    continue
                if is_reserved(ip):
                    continue
                if is_excluded(alias):
                    continue
                result[ip] = alias
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
            err(f"Windows Get-NetIPAddress failed: {e}")
    return {}  # 本机始终是 Windows，Linux/macOS 分支是死代码


def pick_one(ips: list[str], local: dict[str, str], prefer_ip: str | None) -> str | None:
    """从 ips 中选出存在于 local 里的第一条。优先 prefer_ip。"""
    if prefer_ip and prefer_ip in ips and prefer_ip in local:
        return prefer_ip
    for ip in ips:
        if ip in local:
            return ip
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 Agent 传入的候选 IP 列表中筛选出存在于本机网卡的那一个",
    )
    parser.add_argument("ips", nargs="+", help="Agent 传入的远端候选 IP 列表")
    parser.add_argument("--prefer", help="优先选择此 IP（通常是 $SSH_CONNECTION 的客户端 IP）")
    parser.add_argument("--debug", action="store_true", help="stderr 输出本机网卡信息")
    args = parser.parse_args()

    # 1. 校验入参 IP 格式
    validated: list[str] = []
    for ip in args.ips:
        ip = ip.strip()
        if VALID_IPV4.match(ip) and not is_reserved(ip):
            if ip not in validated:
                validated.append(ip)

    if not validated:
        err(f"入参 IP 列表格式无效或全为保留地址: {args.ips}")
        return 50

    # 2. 枚举本机网卡
    local = get_local_ips()

    if args.debug:
        err(f"candidate IPs = {validated}")
        err(f"local NICs    = {dict(sorted(local.items()))}")

    if not local:
        err(
            "本机未检测到对外可达网卡（已排除 Loopback/vEthernet/Docker/Hyper-V/VMware 等）\n"
            f"  candidates = {validated}\n"
            "  请手动指定 tunnel_host"
        )
        return 2

    # 3. 取交集：在传入顺序中第一个存在于本机的
    chosen = pick_one(validated, local, args.prefer)
    if chosen is None:
        err(
            "入参 IP 列表未命中本机任何网卡（可能经过 NAT / 跳板机中转）\n"
            f"  candidates   = {validated}\n"
            f"  local NICs   = {sorted(local.keys())}\n"
            "  请手动指定 tunnel_host"
        )
        return 1

    print(chosen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
