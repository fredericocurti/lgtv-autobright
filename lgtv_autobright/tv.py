"""TV connection and brightness control via system Python.

Homebrew/venv Python lacks Apple network entitlements needed for
websocket connections to LG TVs. We shell out to /usr/bin/python3
which uses the system bscpylgtv install at ~/Library/Python/3.9/.
"""

import asyncio
import json
import logging
import subprocess

log = logging.getLogger(__name__)

# Script template executed by system Python
_TV_SCRIPT = """\
import sys, json, asyncio
sys.path.insert(0, '/Users/fcurto/Library/Python/3.9/lib/python/site-packages')
from bscpylgtv import WebOsClient

async def main():
    cmd = json.loads(sys.argv[1])
    ip = cmd["ip"]
    action = cmd["action"]

    if action == "connect":
        client = await WebOsClient.create(ip, states=[])
        await client.connect()
        await client.disconnect()
        print(json.dumps({"ok": True}))

    elif action == "get_backlight":
        client = await WebOsClient.create(ip, states=[])
        await client.connect()
        try:
            settings = await client.get_picture_settings(["backlight"])
            print(json.dumps({"ok": True, "backlight": settings.get("backlight")}))
        finally:
            await client.disconnect()

    elif action == "set_backlight":
        value = cmd["value"]
        client = await WebOsClient.create(ip, states=[])
        await client.connect()
        try:
            await client.set_system_settings("picture", {"backlight": str(value)})
            print(json.dumps({"ok": True}))
        finally:
            await client.disconnect()

asyncio.run(main())
"""


def _run_tv_cmd(cmd: dict, timeout: float = 15.0) -> dict:
    """Run a TV command via system Python. Returns parsed JSON result."""
    proc = subprocess.run(
        ["/usr/bin/python3", "-c", _TV_SCRIPT, json.dumps(cmd)],
        capture_output=True, text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip().split("\n")[-1] if proc.stderr.strip() else "Unknown error"
        raise RuntimeError(err)
    return json.loads(proc.stdout.strip())


class TVController:
    def __init__(self, ip: str):
        self.ip = ip
        self._connected = False

    async def connect(self):
        """Connect and pair with the TV. First time will show a pairing prompt."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_tv_cmd, {"ip": self.ip, "action": "connect"})
        self._connected = True
        log.info("Connected to TV at %s", self.ip)

    async def disconnect(self):
        self._connected = False
        log.info("Disconnected from TV at %s", self.ip)

    async def set_backlight(self, value: int):
        """Set the TV backlight (0-100)."""
        value = max(0, min(100, value))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, _run_tv_cmd,
            {"ip": self.ip, "action": "set_backlight", "value": value},
        )
        log.info("Set backlight to %d", value)

    async def get_backlight(self) -> int | None:
        """Get the current backlight value."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, _run_tv_cmd,
            {"ip": self.ip, "action": "get_backlight"},
        )
        return result.get("backlight")

    @property
    def is_connected(self) -> bool:
        return self._connected
