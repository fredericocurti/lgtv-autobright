"""LG TV discovery via system Python.

Embeds the full mDNS + AirPlay discovery logic as an inline script
that runs under /usr/bin/python3 (system Python), which has the Apple
entitlements needed for mDNS port 5353 binding.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Callable, Dict, List

# Self-contained discovery script — no external dependencies.
_DISCOVER_SCRIPT = """\
import json, plistlib, re, socket, struct, time, urllib.request, sys

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
SERVICES = [
    "_airplay._tcp.local.",
    "_raop._tcp.local.",
    "_googlecast._tcp.local.",
    "_display._tcp.local.",
    "_webostv._tcp.local.",
]
TIMEOUT = {timeout}

def mdns_scan():
    devices = {{}}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.settimeout(TIMEOUT)
    sock.bind(("", MDNS_PORT))
    mreq = struct.pack("4s4s", socket.inet_aton(MDNS_ADDR), socket.inet_aton("0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def build_query(name):
        header = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
        question = b""
        for part in name.rstrip(".").split("."):
            question += struct.pack("B", len(part)) + part.encode()
        question += b"\\x00"
        question += struct.pack("!HH", 12, 1)
        return header + question

    for svc in SERVICES:
        sock.sendto(build_query(svc), (MDNS_ADDR, MDNS_PORT))

    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
            ip = addr[0]
            text = data.decode("utf-8", errors="replace")
            if ip not in devices:
                devices[ip] = {{"ip": ip, "raw_names": set(), "services": set()}}
            for r in re.findall(r"[\\x20-\\x7e]{{4,}}", text):
                devices[ip]["raw_names"].add(r)
            for svc in SERVICES:
                svc_short = svc.split(".")[0]
                if svc_short in text:
                    devices[ip]["services"].add(svc_short)
        except socket.timeout:
            break
    sock.close()
    return devices

def airplay_info(ip):
    try:
        req = urllib.request.Request(f"http://{{ip}}:7000/info", headers={{"User-Agent": "LGTVFinder/1.0"}})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return plistlib.loads(resp.read())
    except Exception:
        return None

def is_lg(raw_names):
    raw = " ".join(raw_names).lower()
    return any(kw in raw for kw in ["lg", "webos", "lge", "[lg]", "lg electronics"])

def extract_serial(raw_names):
    for r in raw_names:
        m = re.search(r"serialNumber=([A-Za-z0-9_]+)", r)
        if m:
            return m.group(1).split("_")[0]
    return ""

devices = mdns_scan()
lg = {{ip: info for ip, info in devices.items() if info["services"] and is_lg(info["raw_names"])}}

results = []
for ip, info in lg.items():
    serial = extract_serial(info["raw_names"])
    name = f"LG TV ({{serial}})" if serial else "LG TV"
    model = ""

    if "_airplay" in info["services"]:
        ap = airplay_info(ip)
        if ap:
            ap_model = ap.get("model", "").lower()
            ap_maker = ap.get("manufacturer", "").lower()
            if ap_model and not any(kw in ap_model for kw in ["lg", "oled", "nano", "qned", "webos"]):
                if "lg" not in ap_maker:
                    continue
            name = ap.get("name", "") or name
            model = ap.get("model", "")
            if not serial:
                ap_serial = ap.get("serialNumber", "")
                if ap_serial:
                    serial = ap_serial.split("_")[0]

    results.append({{"ip": ip, "name": name, "model": model, "serial": serial}})

print(json.dumps(results))
"""


def discover_lg_tvs(
    timeout: float = 5.0,
    on_found: Callable[[dict], None] | None = None,
) -> List[Dict[str, object]]:
    """Discover LG TVs on the local network via system Python."""
    script = _DISCOVER_SCRIPT.format(timeout=int(timeout))

    proc = subprocess.run(
        ["/usr/bin/python3", "-c", script],
        capture_output=True, text=True,
        timeout=int(timeout) + 15,
    )

    if proc.returncode != 0:
        return []

    try:
        raw = json.loads(proc.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return []

    results = []
    for tv in raw:
        entry = {
            "ip": tv["ip"],
            "name": tv["name"],
            "model": tv.get("model", ""),
            "serial": tv.get("serial", ""),
        }
        results.append(entry)
        if on_found:
            on_found(entry)

    return results


async def async_discover_lg_tvs(
    timeout: float = 5.0,
    on_found: Callable[[dict], None] | None = None,
) -> List[Dict[str, object]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, discover_lg_tvs, timeout, on_found)
