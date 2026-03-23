"""TV connection and brightness control via system Python.

Embeds a self-contained WebOS client as an inline script that runs
under /usr/bin/python3 — zero external dependencies.
"""

import asyncio
import json
import logging
import subprocess

log = logging.getLogger(__name__)

# Self-contained WebOS TV control script — stdlib only.
# Includes a minimal websocket client and the LG WebOS JSON protocol.
# Client key is persisted to ~/.config/lgtv-autobright/keys.json.
_TV_SCRIPT = r"""
import base64, hashlib, json, os, socket, ssl, struct, sys, time

# ── Minimal WebSocket client (RFC 6455, text frames only) ──

class MiniWS:
    def __init__(self, sock):
        self._sock = sock
        self._buf = b""

    @classmethod
    def connect(cls, host, port, path="/", use_ssl=False):
        raw = socket.create_connection((host, port), timeout=10)
        if use_ssl:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=host)

        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        raw.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = raw.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake failed")
            resp += chunk
        if b"101" not in resp.split(b"\r\n")[0]:
            raise ConnectionError(f"WebSocket upgrade rejected: {resp[:200]}")
        ws = cls(raw)
        ws._buf = resp.split(b"\r\n\r\n", 1)[1]
        return ws

    def send(self, text):
        data = text.encode("utf-8")
        frame = bytearray()
        frame.append(0x81)  # FIN + TEXT
        mask_key = os.urandom(4)
        length = len(data)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame += struct.pack("!H", length)
        else:
            frame.append(0x80 | 127)
            frame += struct.pack("!Q", length)
        frame += mask_key
        masked = bytearray(data)
        for i in range(len(masked)):
            masked[i] ^= mask_key[i % 4]
        frame += masked
        self._sock.sendall(frame)

    def recv(self, timeout=10):
        self._sock.settimeout(timeout)
        while True:
            while len(self._buf) < 2:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Connection closed")
                self._buf += chunk

            b0, b1 = self._buf[0], self._buf[1]
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            length = b1 & 0x7F
            offset = 2

            if length == 126:
                while len(self._buf) < offset + 2:
                    self._buf += self._sock.recv(4096)
                length = struct.unpack("!H", self._buf[offset:offset+2])[0]
                offset += 2
            elif length == 127:
                while len(self._buf) < offset + 8:
                    self._buf += self._sock.recv(4096)
                length = struct.unpack("!Q", self._buf[offset:offset+8])[0]
                offset += 8

            if masked:
                offset += 4

            while len(self._buf) < offset + length:
                self._buf += self._sock.recv(4096)

            payload = self._buf[offset:offset+length]
            if masked:
                mask_key = self._buf[offset-4:offset]
                payload = bytearray(payload)
                for i in range(len(payload)):
                    payload[i] ^= mask_key[i % 4]
                payload = bytes(payload)

            self._buf = self._buf[offset+length:]

            if opcode == 0x08:
                raise ConnectionError("Server closed connection")
            if opcode == 0x09:
                self._send_pong(payload)
                continue
            if opcode == 0x0A:
                continue
            if opcode == 0x01:
                return payload.decode("utf-8")

    def _send_pong(self, payload):
        frame = bytearray()
        frame.append(0x8A)  # FIN + PONG
        mask_key = os.urandom(4)
        frame.append(0x80 | len(payload))
        frame += mask_key
        masked = bytearray(payload)
        for i in range(len(masked)):
            masked[i] ^= mask_key[i % 4]
        frame += masked
        self._sock.sendall(frame)

    def close(self):
        try:
            frame = bytearray([0x88, 0x80]) + os.urandom(4)
            self._sock.sendall(frame)
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass


# ── Key storage ──

KEYS_FILE = os.path.expanduser("~/.config/lgtv-autobright/keys.json")

def load_keys():
    try:
        with open(KEYS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_keys(keys):
    os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)


# ── WebOS registration payload ──

REGISTER_PAYLOAD = {
    "type": "register",
    "id": "register_0",
    "payload": {
        "forcePairing": False,
        "pairingType": "PROMPT",
        "manifest": {
            "manifestVersion": 1,
            "appVersion": "1.1",
            "signed": {
                "created": "20140509",
                "appId": "com.lge.test",
                "vendorId": "com.lge",
                "localizedAppNames": {"": "LG Remote App"},
                "localizedVendorNames": {"": "LG Electronics"},
                "permissions": [
                    "TEST_SECURE", "CONTROL_INPUT_TEXT", "CONTROL_MOUSE_AND_KEYBOARD",
                    "READ_INSTALLED_APPS", "READ_LGE_SDX", "READ_NOTIFICATIONS",
                    "SEARCH", "WRITE_SETTINGS", "WRITE_NOTIFICATION_ALERT",
                    "CONTROL_POWER", "READ_CURRENT_CHANNEL", "READ_RUNNING_APPS",
                    "READ_UPDATE_INFO", "UPDATE_FROM_REMOTE_APP", "READ_LGE_TV_INPUT_EVENTS",
                    "READ_TV_CURRENT_TIME", "READ_SETTINGS",
                ],
                "serial": "2f930e2d2cfe083771f68e4fe7bb07",
            },
            "permissions": [
                "LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CLOSE", "TEST_OPEN",
                "TEST_PROTECTED", "CONTROL_AUDIO", "CONTROL_DISPLAY",
                "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_RECORDING",
                "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV",
                "CONTROL_POWER", "READ_APP_STATUS", "READ_CURRENT_CHANNEL",
                "READ_INPUT_DEVICE_LIST", "READ_NETWORK_STATE",
                "READ_RUNNING_APPS", "READ_TV_CHANNEL_LIST",
                "WRITE_NOTIFICATION_TOAST", "READ_POWER_STATE",
                "READ_COUNTRY_INFO", "READ_SETTINGS", "CONTROL_TV_SCREEN",
                "CONTROL_TV_STANBY", "CONTROL_FAVORITE_GROUP",
                "CONTROL_USER_INFO", "CHECK_BLUETOOTH_DEVICE",
                "CONTROL_BLUETOOTH", "CONTROL_TIMER_INFO",
                "STB_INTERNAL_CONNECTION", "CONTROL_RECORDING",
                "READ_RECORDING_STATE", "WRITE_RECORDING_LIST",
                "READ_RECORDING_LIST", "READ_RECORDING_SCHEDULE",
                "WRITE_RECORDING_SCHEDULE", "READ_STORAGE_DEVICE_LIST",
                "READ_TV_PROGRAM_INFO", "CONTROL_BOX_CHANNEL",
                "READ_TV_ACR_AUTH_TOKEN", "READ_TV_CONTENT_STATE",
                "READ_TV_CURRENT_TIME", "ADD_LAUNCHER_CHANNEL",
                "SET_CHANNEL_SKIP", "RELEASE_CHANNEL_SKIP",
                "CONTROL_CHANNEL_BLOCK", "DELETE_SELECT_CHANNEL",
                "CONTROL_CHANNEL_GROUP", "SCAN_TV_CHANNELS",
                "CONTROL_TV_POWER", "CONTROL_WOL",
            ],
            "signatures": [
                {
                    "signatureVersion": 1,
                    "signature": "eyJhbGdvcml0aG0iOiJSU0EtU0hBMjU2Iiwia2V5SWQiOiJ0ZXN0LXNpZ25pbmctY2VydCIsInNpZ25hdHVyZVZlcnNpb24iOjF9.hrVRgjCwXVvE2OOSpDZ58hR+59aFNwYDyjQgKk3auukd7pcegmE2CzPCa0bJ0ZsRAcKkCTJrWo5iDzNhMBWRyaMOv5zWSrthlf7G128qvIlpMT0YNY+n/FaOHE73uLrS/g7swl3/qH/BGFG2Hu4RlL48eb3lLKqTt2xKHdCs6Cd4RMfJPYnzgvI4BNrFUKsjkcu+WD4OO2A27Pq1n50cMchmcaXadJhGrOqH5YmHdOCj5NSHzJYrsW0HPlpuAx/ECMeIZYDh6RMqaFM2DXzdKX9NmmyqzJ3o/0lkk/N97gfVRLW5hA29yeAwaCViZNCP8iC9aO0q9fQojoa7NQnAtw=="
                }
            ],
        },
    },
}


# ── WebOS client ──

class WebOSClient:
    def __init__(self, ip):
        self.ip = ip
        self.ws = None
        self._msg_id = 0

    def connect(self):
        try:
            self.ws = MiniWS.connect(self.ip, 3001, "/", use_ssl=True)
        except Exception:
            self.ws = MiniWS.connect(self.ip, 3000, "/")

    def register(self):
        keys = load_keys()
        payload = json.loads(json.dumps(REGISTER_PAYLOAD))
        client_key = keys.get(self.ip)
        if client_key:
            payload["payload"]["client-key"] = client_key

        self.ws.send(json.dumps(payload))

        while True:
            resp = json.loads(self.ws.recv(timeout=60))
            if resp.get("id") == "register_0":
                if resp.get("type") == "registered":
                    new_key = resp.get("payload", {}).get("client-key")
                    if new_key:
                        keys[self.ip] = new_key
                        save_keys(keys)
                    return True
                elif resp.get("type") == "error":
                    raise RuntimeError(f"Registration failed: {resp.get('error')}")

    def _next_id(self):
        self._msg_id += 1
        return f"msg_{self._msg_id}"

    def request(self, uri, payload=None):
        msg_id = self._next_id()
        msg = {"type": "request", "id": msg_id, "uri": uri}
        if payload:
            msg["payload"] = payload
        self.ws.send(json.dumps(msg))

        while True:
            resp = json.loads(self.ws.recv(timeout=10))
            if resp.get("id") == msg_id:
                if resp.get("type") == "error":
                    raise RuntimeError(resp.get("error", "Request failed"))
                return resp.get("payload", {})

    def get_picture_settings(self, keys):
        return self.request(
            "ssap://settings/getSystemSettings",
            {"category": "picture", "keys": keys},
        )

    def set_picture_settings(self, settings):
        return self.request(
            "ssap://settings/setSystemSettings",
            {"category": "picture", "settings": settings},
        )

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None


# ── Main ──

cmd = json.loads(sys.argv[1])
ip = cmd["ip"]
action = cmd["action"]

client = WebOSClient(ip)
client.connect()
client.register()

try:
    if action == "connect":
        print(json.dumps({"ok": True}))

    elif action == "get_backlight":
        result = client.get_picture_settings(["backlight"])
        settings = result.get("settings", result)
        print(json.dumps({"ok": True, "backlight": settings.get("backlight")}))

    elif action == "set_backlight":
        value = cmd["value"]
        client.set_picture_settings({"backlight": str(value)})
        print(json.dumps({"ok": True}))
finally:
    client.close()
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
