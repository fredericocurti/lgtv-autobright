"""LG TV discovery — runs lgtv_autofind via system Python for mDNS access."""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Callable, Dict, List

# System Python has Apple entitlements needed for mDNS port 5353 binding.
# Homebrew/venv Python does not, so we shell out.
_DISCOVER_SCRIPT = """\
import sys, json
sys.path.insert(0, {autofind_path!r})
from lgtv_autofind import discover
tvs = discover(mdns_timeout={timeout}, airplay_timeout=3)
result = []
for tv in tvs:
    result.append({{"ip": tv.ip, "name": tv.name, "model": tv.model, "serial": tv.serial}})
print(json.dumps(result))
"""


def _find_autofind_path() -> str:
    import lgtv_autofind
    import os
    return os.path.dirname(os.path.dirname(lgtv_autofind.__file__))


def discover_lg_tvs(
    timeout: float = 5.0,
    on_found: Callable[[dict], None] | None = None,
) -> List[Dict[str, object]]:
    """Discover LG TVs using lgtv_autofind via system Python."""
    autofind_path = _find_autofind_path()
    script = _DISCOVER_SCRIPT.format(autofind_path=autofind_path, timeout=int(timeout))

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
            "method": "mdns+airplay" if tv.get("model") else "mdns",
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
