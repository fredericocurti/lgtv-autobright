"""PyQt6 UIs — Connect window and Settings window.

Runs as a subprocess to avoid main-thread conflicts with rumps.
  python -m lgtv_autobright.scan_ui              -> Connect UI (prints JSON)
  python -m lgtv_autobright.scan_ui --settings   -> Settings UI (prints JSON)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Dict, List

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# -- Catppuccin Mocha --
BG = "#1e1e2e"
SURFACE0 = "#313244"
SURFACE1 = "#45475a"
SURFACE2 = "#585b70"
FG = "#cdd6f4"
DIM = "#6c7086"
ACCENT = "#89b4fa"
GREEN = "#a6e3a1"
RED = "#f38ba8"
YELLOW = "#f9e2af"

STYLESHEET = f"""
QDialog {{
    background-color: {BG};
}}
QLabel {{
    color: {FG};
    background: transparent;
}}
QLabel#dim {{
    color: {DIM};
}}
QLabel#accent {{
    color: {ACCENT};
}}
QLabel#error {{
    color: {RED};
}}
QLabel#green {{
    color: {GREEN};
}}
QListWidget {{
    background-color: {SURFACE0};
    color: {FG};
    border: none;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 10px 14px;
    border-radius: 4px;
}}
QListWidget::item:selected {{
    background-color: {SURFACE2};
}}
QListWidget::item:hover {{
    background-color: {SURFACE1};
}}
QLineEdit {{
    background-color: {SURFACE0};
    color: {FG};
    border: none;
    border-radius: 6px;
    padding: 10px 12px;
    selection-background-color: {ACCENT};
}}
QPlainTextEdit {{
    background-color: {SURFACE0};
    color: {FG};
    border: none;
    border-radius: 6px;
    padding: 10px 12px;
    selection-background-color: {ACCENT};
}}
QPushButton {{
    background-color: {SURFACE0};
    color: {FG};
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
}}
QPushButton:hover {{
    background-color: {SURFACE1};
}}
QPushButton:pressed {{
    background-color: {SURFACE2};
}}
QPushButton:disabled {{
    color: {DIM};
}}
QPushButton#primary {{
    background-color: {ACCENT};
    color: {BG};
}}
QPushButton#primary:hover {{
    background-color: {GREEN};
}}
QPushButton#primary:disabled {{
    background-color: {SURFACE1};
    color: {DIM};
}}
QProgressBar {{
    background-color: {SURFACE0};
    border: none;
    height: 3px;
    border-radius: 1px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 1px;
}}
"""


# ======================================================================
#  Signals helper for thread -> UI communication
# ======================================================================

class _Signals(QObject):
    tv_found = pyqtSignal(dict)
    scan_done = pyqtSignal()
    connect_result = pyqtSignal(dict, str)  # tv, error (empty string = success)


# ======================================================================
#  Connect Window
# ======================================================================

class ConnectWindow(QDialog):
    def __init__(self):
        super().__init__()
        self._selected_result: dict | None = None
        self._tvs: List[Dict] = []
        self._signals = _Signals()
        self._signals.tv_found.connect(self._add_tv)
        self._signals.scan_done.connect(self._scan_done)
        self._signals.connect_result.connect(self._connect_result)

        self._build()
        self._start_scan()

    def _build(self):
        self.setWindowTitle("Connect to LG TV")
        self.setFixedSize(460, 520)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(0)

        # -- Header --
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title_label = QLabel("Scanning for LG TVs")
        self._title_label.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        header.addWidget(self._title_label)

        self._status_label = QLabel("")
        self._status_label.setObjectName("dim")
        self._status_label.setFont(QFont("SF Mono", 10))
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self._status_label)
        layout.addLayout(header)

        # -- Progress bar --
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(3)
        layout.addSpacing(10)
        layout.addWidget(self._progress)

        # -- TV list --
        layout.addSpacing(12)
        self._tv_list = QListWidget()
        self._tv_list.setFont(QFont("SF Mono", 12))
        self._tv_list.itemDoubleClicked.connect(self._on_double_click)
        self._tv_list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self._tv_list, stretch=1)

        # -- Rescan --
        layout.addSpacing(8)
        rescan_row = QHBoxLayout()
        rescan_row.setContentsMargins(0, 0, 0, 0)
        rescan_row.addStretch()
        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.setFont(QFont("SF Mono", 10))
        self._rescan_btn.clicked.connect(self._rescan)
        rescan_row.addWidget(self._rescan_btn)
        rescan_row.addStretch()
        layout.addLayout(rescan_row)

        # -- Separator --
        layout.addSpacing(12)
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {SURFACE0};")
        layout.addWidget(sep)

        # -- Manual IP --
        layout.addSpacing(12)
        manual_label = QLabel("OR ENTER IP MANUALLY")
        manual_label.setObjectName("dim")
        manual_label.setFont(QFont("SF Pro Display", 10, QFont.Weight.Bold))
        layout.addWidget(manual_label)

        layout.addSpacing(6)
        ip_row = QHBoxLayout()
        ip_row.setContentsMargins(0, 0, 0, 0)
        self._ip_entry = QLineEdit("192.168.1.")
        self._ip_entry.setFont(QFont("SF Mono", 12))
        self._ip_entry.returnPressed.connect(self._on_manual_connect)
        ip_row.addWidget(self._ip_entry, stretch=1)

        ip_row.addSpacing(10)
        self._ip_btn = QPushButton("Connect")
        self._ip_btn.setFont(QFont("SF Mono", 12))
        self._ip_btn.clicked.connect(self._on_manual_connect)
        ip_row.addWidget(self._ip_btn)
        layout.addLayout(ip_row)

        # -- Bottom buttons --
        layout.addSpacing(16)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFont(QFont("SF Mono", 12))
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        btn_row.addSpacing(10)
        self._select_btn = QPushButton("Connect")
        self._select_btn.setObjectName("primary")
        self._select_btn.setFont(QFont("SF Mono", 12))
        self._select_btn.setEnabled(False)
        self._select_btn.clicked.connect(self._on_select)
        btn_row.addWidget(self._select_btn)
        layout.addLayout(btn_row)

    # -- Scan --

    def _start_scan(self):
        self._progress.setRange(0, 0)
        self._title_label.setText("Scanning for LG TVs")
        self._title_label.setObjectName("")
        self._title_label.setStyleSheet(f"color: {FG};")

        def scan_thread():
            from lgtv_autobright.discovery import discover_lg_tvs
            try:
                discover_lg_tvs(timeout=5.0, on_found=lambda tv: self._signals.tv_found.emit(tv))
            except Exception:
                pass
            self._signals.scan_done.emit()

        threading.Thread(target=scan_thread, daemon=True).start()

    def _rescan(self):
        self._tv_list.clear()
        self._tvs.clear()
        self._select_btn.setEnabled(False)
        self._status_label.setText("")
        self._start_scan()

    def _add_tv(self, tv: dict):
        self._tvs.append(tv)
        name = tv.get("name", "Unknown")
        ip = tv.get("ip", "?")
        item = QListWidgetItem(f"{name}\n{ip}")
        item.setFont(QFont("SF Mono", 12))
        self._tv_list.addItem(item)
        self._status_label.setText(f"{len(self._tvs)} found")

        if len(self._tvs) == 1:
            self._tv_list.setCurrentRow(0)

    def _scan_done(self):
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        if not self._tvs:
            self._title_label.setText("No LG TVs found")
            self._status_label.setText("")
        else:
            self._title_label.setText(f"Found {len(self._tvs)} LG TV(s)")

    def _on_selection_changed(self, row):
        self._select_btn.setEnabled(row >= 0)

    def _on_double_click(self, item):
        row = self._tv_list.row(item)
        if 0 <= row < len(self._tvs):
            self._try_connect(self._tvs[row])

    # -- Connection --

    def _try_connect(self, tv: dict):
        ip = tv.get("ip", "")
        if not ip:
            return

        self._select_btn.setEnabled(False)
        self._ip_btn.setEnabled(False)
        self._rescan_btn.setEnabled(False)

        self._progress.setRange(0, 0)
        self._title_label.setText(f"Connecting to {ip}...")
        self._title_label.setStyleSheet(f"color: {FG};")
        self._status_label.setText("")

        def connect_thread():
            import asyncio
            from bscpylgtv import WebOsClient

            error = ""
            try:
                async def do_connect():
                    client = await WebOsClient.create(ip, ping_interval=None, states=[])
                    await client.connect()
                    await client.disconnect()
                asyncio.run(do_connect())
            except Exception as e:
                error = str(e)

            self._signals.connect_result.emit(tv, error)

        threading.Thread(target=connect_thread, daemon=True).start()

    def _connect_result(self, tv: dict, error: str):
        self._progress.setRange(0, 1)
        self._progress.setValue(1)

        if not error:
            self._title_label.setText("Connected!")
            self._title_label.setStyleSheet(f"color: {GREEN};")
            self._selected_result = tv
            QTimer.singleShot(400, self.accept)
        else:
            short_err = error if len(error) < 60 else error[:57] + "..."
            self._title_label.setText("Connection failed")
            self._title_label.setStyleSheet(f"color: {FG};")
            self._status_label.setText(short_err)
            self._status_label.setStyleSheet(f"color: {RED};")

            self._ip_btn.setEnabled(True)
            self._rescan_btn.setEnabled(True)
            if self._tv_list.currentRow() >= 0:
                self._select_btn.setEnabled(True)

    def _on_select(self):
        row = self._tv_list.currentRow()
        if 0 <= row < len(self._tvs):
            self._try_connect(self._tvs[row])

    def _on_manual_connect(self):
        ip = self._ip_entry.text().strip()
        if ip:
            self._try_connect({"ip": ip, "name": ip, "method": "manual"})

    def get_result(self) -> dict | None:
        return self._selected_result


# ======================================================================
#  Settings Window
# ======================================================================

class SettingsWindow(QDialog):
    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        self._result: dict | None = None
        self._build()

    def _build(self):
        self.setWindowTitle("LG AutoBright Settings")
        self.setFixedSize(440, 520)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(0)

        curve = self._config.get("brightness_curve", {})
        tv_name = self._config.get("tv_name", self._config.get("tv_ip", "Unknown"))

        # -- Header --
        title = QLabel("Brightness Settings")
        title.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        layout.addWidget(title)

        # -- TV info --
        layout.addSpacing(12)
        info = QWidget()
        info.setStyleSheet(f"background-color: {SURFACE0}; border-radius: 6px;")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(14, 10, 14, 10)

        name_label = QLabel(tv_name)
        name_label.setFont(QFont("SF Mono", 12))
        info_layout.addWidget(name_label)

        ip_label = QLabel(self._config.get("tv_ip", ""))
        ip_label.setFont(QFont("SF Mono", 10))
        ip_label.setStyleSheet(f"color: {DIM};")
        info_layout.addWidget(ip_label)
        layout.addWidget(info)

        # -- Current target --
        from lgtv_autobright.brightness import interpolate_brightness
        current_b = interpolate_brightness(curve)

        layout.addSpacing(12)
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)

        target_label = QLabel("CURRENT TARGET")
        target_label.setObjectName("dim")
        target_label.setFont(QFont("SF Pro Display", 10, QFont.Weight.Bold))
        target_row.addWidget(target_label)

        target_row.addStretch()
        target_value = QLabel(f"{current_b}%")
        target_value.setObjectName("accent")
        target_value.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        target_row.addWidget(target_value)
        layout.addLayout(target_row)

        # -- Separator --
        layout.addSpacing(12)
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {SURFACE0};")
        layout.addWidget(sep)

        # -- Curve editor --
        layout.addSpacing(12)
        curve_label = QLabel("BRIGHTNESS CURVE")
        curve_label.setObjectName("dim")
        curve_label.setFont(QFont("SF Pro Display", 10, QFont.Weight.Bold))
        layout.addWidget(curve_label)

        layout.addSpacing(2)
        hint = QLabel("One entry per line:  HH:MM=brightness (0-100)")
        hint.setObjectName("dim")
        hint.setFont(QFont("SF Mono", 10))
        layout.addWidget(hint)

        layout.addSpacing(6)
        self._editor = QPlainTextEdit()
        self._editor.setFont(QFont("SF Mono", 12))
        curve_text = "\n".join(f"{t}={b}" for t, b in sorted(curve.items()))
        self._editor.setPlainText(curve_text)
        layout.addWidget(self._editor, stretch=1)

        # -- Error + buttons --
        layout.addSpacing(16)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)

        self._error_label = QLabel("")
        self._error_label.setObjectName("error")
        self._error_label.setFont(QFont("SF Mono", 10))
        bottom.addWidget(self._error_label)

        bottom.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFont(QFont("SF Mono", 12))
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)

        bottom.addSpacing(10)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.setFont(QFont("SF Mono", 12))
        save_btn.clicked.connect(self._on_save)
        bottom.addWidget(save_btn)
        layout.addLayout(bottom)

    def _on_save(self):
        text = self._editor.toPlainText().strip()
        new_curve = {}
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                self._error_label.setText(f"Bad line: {line}")
                return
            t, _, b = line.partition("=")
            try:
                parts = t.strip().split(":")
                assert len(parts) == 2
                h, m = int(parts[0]), int(parts[1])
                assert 0 <= h <= 23 and 0 <= m <= 59
                bval = int(b.strip())
                assert 0 <= bval <= 100
                new_curve[t.strip()] = bval
            except (ValueError, AssertionError):
                self._error_label.setText(f"Invalid: {line}")
                return

        if not new_curve:
            self._error_label.setText("Need at least one entry")
            return

        self._result = {"brightness_curve": new_curve}
        self.accept()

    def get_result(self) -> dict | None:
        return self._result


# ======================================================================
#  Subprocess interface (called from app.py)
# ======================================================================

def run_connect_ui() -> dict | None:
    proc = subprocess.run(
        [sys.executable, "-m", "lgtv_autobright.scan_ui"],
        capture_output=True, text=True,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            return json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            return None
    return None


def run_settings_ui(config: dict) -> dict | None:
    config_json = json.dumps(config)
    proc = subprocess.run(
        [sys.executable, "-m", "lgtv_autobright.scan_ui", "--settings"],
        input=config_json, capture_output=True, text=True,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            return json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            return None
    return None


if __name__ == "__main__":
    app = QApplication(sys.argv)

    if "--settings" in sys.argv:
        config = json.loads(sys.stdin.read())
        win = SettingsWindow(config)
        if win.exec() == QDialog.DialogCode.Accepted and win.get_result():
            print(json.dumps(win.get_result()))
            sys.exit(0)
        else:
            sys.exit(1)
    else:
        win = ConnectWindow()
        if win.exec() == QDialog.DialogCode.Accepted and win.get_result():
            result = win.get_result()
            out = {
                "ip": result.get("ip"),
                "name": result.get("name"),
                "method": result.get("method"),
            }
            print(json.dumps(out))
            sys.exit(0)
        else:
            sys.exit(1)
