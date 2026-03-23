"""Brightness curve interpolation and scheduler."""

import asyncio
import json
import logging
import os
from datetime import datetime, time
from pathlib import Path

from .tv import TVController

log = logging.getLogger(__name__)

CONFIG_DIR = Path(os.path.expanduser("~/.config/lgtv-autobright"))
CONFIG_FILE = CONFIG_DIR / "config.json"

# Default curve: time_str -> brightness (0-100)
DEFAULT_CURVE = {
    "00:00": 65,
    "12:01": 90,
    "18:00": 70,
}

DEFAULT_CONFIG = {
    "tv_ip": None,
    "tv_name": None,
    "brightness_curve": DEFAULT_CURVE,
    "update_interval_seconds": 300,
    "enabled": True,
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return dict(DEFAULT_CONFIG)


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _parse_time(t: str) -> time:
    parts = t.split(":")
    return time(int(parts[0]), int(parts[1]))


def interpolate_brightness(curve: dict[str, int], now: datetime | None = None) -> int:
    """Interpolate brightness from the curve for the current time."""
    if now is None:
        now = datetime.now()
    current_minutes = now.hour * 60 + now.minute

    # Sort curve points by time
    points = []
    for t_str, brightness in curve.items():
        t = _parse_time(t_str)
        minutes = t.hour * 60 + t.minute
        points.append((minutes, brightness))
    points.sort(key=lambda x: x[0])

    if not points:
        return 50

    # Find surrounding points (wrap around midnight)
    before = points[-1]  # wrap: last point is "before" if we're past all
    after = points[0]    # wrap: first point is "after" if we're past all

    for i, (m, b) in enumerate(points):
        if m <= current_minutes:
            before = (m, b)
            after = points[(i + 1) % len(points)]
        else:
            after = (m, b)
            break

    bm, bb = before
    am, ab = after

    # Handle midnight wrap
    if am <= bm:
        am += 1440  # add 24h in minutes
    if current_minutes < bm:
        current_minutes += 1440

    span = am - bm
    if span == 0:
        return bb

    ratio = (current_minutes - bm) / span
    ratio = max(0.0, min(1.0, ratio))
    return round(bb + (ab - bb) * ratio)


class BrightnessScheduler:
    def __init__(self, tv: TVController, config: dict, on_update=None):
        self.tv = tv
        self.config = config
        self._task: asyncio.Task | None = None
        self._running = False
        self.last_brightness: int | None = None
        self.last_error: str | None = None
        self.on_update = on_update  # callback(brightness: int) after successful set

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("Brightness scheduler started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("Brightness scheduler stopped")

    async def update_once(self):
        """Compute and apply brightness for right now."""
        curve = self.config.get("brightness_curve", DEFAULT_CURVE)
        brightness = interpolate_brightness(curve)
        self.last_brightness = brightness
        try:
            await self.tv.set_backlight(brightness)
            self.last_error = None
            log.info("Applied brightness: %d", brightness)
            if self.on_update:
                self.on_update(brightness)
        except Exception as e:
            self.last_error = str(e)
            log.error("Failed to set brightness: %s", e)

    async def _loop(self):
        while self._running:
            if self.config.get("enabled", True):
                await self.update_once()
            interval = 300
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
