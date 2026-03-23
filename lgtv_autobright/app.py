"""PyQt6 app with system tray for LG TV auto-brightness."""

import asyncio
import logging
import math
import sys
import threading

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, QSize, pyqtSignal, QObject
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .brightness import (
    BrightnessScheduler,
    interpolate_brightness,
    load_config,
    save_config,
)
from .tv import TVController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

# -- Catppuccin Mocha --
BG = "#1a1a1a"
SURFACE0 = "#2a2a2a"
SURFACE1 = "#3a3a3a"
SURFACE2 = "#4a4a4a"
FG = "#cdd6f4"
DIM = "#6c7086"
ACCENT = "#f9a825"
GREEN = "#a6e3a1"
RED = "#f38ba8"
YELLOW = "#f9e2af"
MAUVE = "#cba6f7"

STYLESHEET = f"""
QWidget#main {{
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
QLabel#green {{
    color: {GREEN};
}}
QLabel#red {{
    color: {RED};
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
    background-color: #fbc02d;
}}
QPushButton#primary:disabled {{
    background-color: {SURFACE1};
    color: {DIM};
}}
QPushButton#danger {{
    background-color: {SURFACE0};
    color: {RED};
}}
QPushButton#danger:hover {{
    background-color: {SURFACE1};
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
#  Interactive Curve Chart Widget
# ======================================================================

class CurveChartWidget(QWidget):
    """Interactive chart for editing the backlight curve.

    - X axis: 0-24h
    - Y axis: 0-100 backlight
    - Click to add nodes, drag to move, right-click to delete
    - First and last nodes always visually connected (wraps midnight)
    """

    curve_changed = pyqtSignal()   # debounced, fires after drag/edit settles
    curve_dragging = pyqtSignal()  # fires on every drag move (for live preview)

    MARGIN_L = 28
    MARGIN_R = 10
    MARGIN_T = 16
    MARGIN_B = 28
    NODE_RADIUS = 7
    HOVER_RADIUS = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nodes: list[tuple[int, int]] = []
        self._dragging_idx: int | None = None
        self._hover_idx: int | None = None
        self._current_time_minutes: int | None = None
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(lambda: self.curve_changed.emit())

    def set_curve(self, curve: dict[str, int]):
        self._nodes = []
        for t_str, val in curve.items():
            parts = t_str.split(":")
            minutes = int(parts[0]) * 60 + int(parts[1])
            self._nodes.append((minutes, int(val)))
        self._nodes.sort()
        self.update()

    def get_curve(self) -> dict[str, int]:
        result = {}
        for minutes, val in sorted(self._nodes):
            minutes = min(minutes, 1439)
            h, m = divmod(minutes, 60)
            result[f"{h:02d}:{m:02d}"] = val
        return result

    def set_current_time(self, minutes: int):
        self._current_time_minutes = minutes
        self.update()

    # -- Coordinate conversion --

    def _chart_rect(self) -> QRectF:
        return QRectF(
            self.MARGIN_L, self.MARGIN_T,
            self.width() - self.MARGIN_L - self.MARGIN_R,
            self.height() - self.MARGIN_T - self.MARGIN_B,
        )

    def _to_pixel(self, minutes: int, value: int) -> QPointF:
        r = self._chart_rect()
        x = r.left() + (minutes / 1440) * r.width()
        y = r.bottom() - (value / 100) * r.height()
        return QPointF(x, y)

    def _from_pixel(self, pos: QPointF) -> tuple[int, int]:
        r = self._chart_rect()
        minutes = int(round((pos.x() - r.left()) / r.width() * 1440))
        value = int(round((r.bottom() - pos.y()) / r.height() * 100))
        minutes = max(0, min(1439, minutes))
        value = max(0, min(100, value))
        return minutes, value

    def _find_node_at(self, pos: QPointF) -> int | None:
        for i, (m, v) in enumerate(self._nodes):
            p = self._to_pixel(m, v)
            dx = pos.x() - p.x()
            dy = pos.y() - p.y()
            if dx * dx + dy * dy <= self.HOVER_RADIUS ** 2:
                return i
        return None

    # -- Paint --

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = self._chart_rect()

        # Background
        p.fillRect(self.rect(), QColor(SURFACE0))

        # Grid
        grid_pen = QPen(QColor(SURFACE1), 1)
        p.setPen(grid_pen)

        # Horizontal grid lines + Y labels
        p.setFont(QFont("Menlo", 8))
        for val in range(0, 101, 25):
            y = r.bottom() - (val / 100) * r.height()
            p.setPen(grid_pen)
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(DIM))
            p.drawText(QRectF(0, y - 8, self.MARGIN_L - 4, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       str(val))

        # Vertical grid lines + X labels
        for h in range(0, 25, 3):
            x = r.left() + (h / 24) * r.width()
            p.setPen(grid_pen)
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(DIM))
            p.drawText(QRectF(x - 16, r.bottom() + 4, 32, 16),
                       Qt.AlignmentFlag.AlignCenter,
                       f"{h:02d}")

        # Current time indicator
        if self._current_time_minutes is not None:
            ct_x = r.left() + (self._current_time_minutes / 1440) * r.width()
            time_pen = QPen(QColor(YELLOW), 1, Qt.PenStyle.DashLine)
            p.setPen(time_pen)
            p.drawLine(QPointF(ct_x, r.top()), QPointF(ct_x, r.bottom()))

        if not self._nodes:
            p.end()
            return

        # Sort for drawing
        sorted_nodes = sorted(self._nodes)

        # Draw the curve line (wrapping: connect last to first)
        curve_pen = QPen(QColor(ACCENT), 2)
        p.setPen(curve_pen)

        # Build full point list including wrap
        all_points = []
        for m, v in sorted_nodes:
            all_points.append(self._to_pixel(m, v))

        # Wrap: draw from last node to first node (across midnight)
        if len(all_points) >= 2:
            # Draw segments between consecutive nodes
            for i in range(len(all_points) - 1):
                p.drawLine(all_points[i], all_points[i + 1])

            # Wrap: last -> right edge, left edge -> first
            last_m, last_v = sorted_nodes[-1]
            first_m, first_v = sorted_nodes[0]

            # Interpolate value at midnight (1440/0)
            wrap_span = (1440 - last_m) + first_m
            if wrap_span > 0:
                ratio = (1440 - last_m) / wrap_span
                mid_val = round(last_v + (first_v - last_v) * ratio)
            else:
                mid_val = last_v

            right_edge = self._to_pixel(1440, mid_val)
            left_edge = self._to_pixel(0, mid_val)
            p.drawLine(all_points[-1], right_edge)
            p.drawLine(left_edge, all_points[0])
        elif len(all_points) == 1:
            # Single node: draw flat line across
            y = all_points[0].y()
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

        # Draw filled area under curve
        if len(sorted_nodes) >= 2:
            fill_path = QPainterPath()
            # Start from bottom-left through wrap
            last_m, last_v = sorted_nodes[-1]
            first_m, first_v = sorted_nodes[0]
            wrap_span = (1440 - last_m) + first_m
            if wrap_span > 0:
                ratio = (1440 - last_m) / wrap_span
                mid_val = round(last_v + (first_v - last_v) * ratio)
            else:
                mid_val = last_v

            left_edge = self._to_pixel(0, mid_val)
            fill_path.moveTo(QPointF(r.left(), r.bottom()))
            fill_path.lineTo(left_edge)
            fill_path.lineTo(all_points[0])
            for pt in all_points[1:]:
                fill_path.lineTo(pt)
            right_edge = self._to_pixel(1440, mid_val)
            fill_path.lineTo(right_edge)
            fill_path.lineTo(QPointF(r.right(), r.bottom()))
            fill_path.closeSubpath()

            fill_color = QColor(ACCENT)
            fill_color.setAlpha(30)
            p.fillPath(fill_path, fill_color)

        # Draw nodes
        for i, (m, v) in enumerate(self._nodes):
            pt = self._to_pixel(m, v)
            is_hover = (i == self._hover_idx)
            is_drag = (i == self._dragging_idx)

            radius = self.NODE_RADIUS + (2 if is_hover or is_drag else 0)

            # Node fill
            if is_drag:
                p.setBrush(QColor(GREEN))
            elif is_hover:
                p.setBrush(QColor(MAUVE))
            else:
                p.setBrush(QColor(ACCENT))

            p.setPen(QPen(QColor(BG), 2))
            p.drawEllipse(pt, radius, radius)

            # Label on hover/drag
            if is_hover or is_drag:
                h_val, m_val = divmod(m, 60)
                label = f"{h_val:02d}:{m_val:02d} = {v}"
                font = QFont("Menlo", 9, QFont.Weight.Bold)
                p.setFont(font)
                from PyQt6.QtGui import QFontMetrics
                fm = QFontMetrics(font)
                tw = fm.horizontalAdvance(label)
                pill_w = tw + 14
                pill_h = 20
                pill_x = pt.x() - pill_w / 2
                pill_y = pt.y() - radius - pill_h - 6
                # Keep in bounds
                if pill_y < r.top():
                    pill_y = pt.y() + radius + 6
                pill_x = max(r.left(), min(r.right() - pill_w, pill_x))
                pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)
                # Draw pill background
                p.setBrush(QColor(BG))
                p.setPen(QPen(QColor(SURFACE2), 1))
                p.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)
                # Draw text
                p.setPen(QColor(FG))
                p.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, label)

        p.end()

    # -- Mouse events --

    def _schedule_save(self):
        """Restart the debounce timer. curve_changed fires when dragging stops."""
        self._debounce_timer.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._find_node_at(QPointF(event.position()))
            if idx is not None:
                self._dragging_idx = idx
            else:
                r = self._chart_rect()
                pos = event.position()
                if r.contains(pos):
                    minutes, value = self._from_pixel(QPointF(pos))
                    self._nodes.append((minutes, value))
                    self._nodes.sort()
                    self._dragging_idx = next(
                        i for i, (m, v) in enumerate(self._nodes)
                        if m == minutes and v == value
                    )
                    self.curve_dragging.emit()
                    self._schedule_save()
            self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            idx = self._find_node_at(QPointF(event.position()))
            if idx is not None and len(self._nodes) > 1:
                self._nodes.pop(idx)
                self._hover_idx = None
                self.curve_dragging.emit()
                self._schedule_save()
                self.update()

    def mouseMoveEvent(self, event):
        pos = QPointF(event.position())
        if self._dragging_idx is not None:
            minutes, value = self._from_pixel(pos)
            self._nodes[self._dragging_idx] = (minutes, value)
            self.curve_dragging.emit()
            self._schedule_save()
            self.update()
        else:
            old_hover = self._hover_idx
            self._hover_idx = self._find_node_at(pos)
            if self._hover_idx != old_hover:
                self.update()
            if self._hover_idx is not None:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging_idx is not None:
            self._dragging_idx = None
            self._nodes.sort()
            self.update()


# ======================================================================
#  Toggle Switch Widget
# ======================================================================

class ToggleSwitch(QWidget):
    """A pretty iOS-style toggle switch."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked=True, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._anim_pos = 1.0 if checked else 0.0
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._animate)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool):
        if val != self._checked:
            self._checked = val
            self._timer.start()

    def _animate(self):
        target = 1.0 if self._checked else 0.0
        diff = target - self._anim_pos
        if abs(diff) < 0.05:
            self._anim_pos = target
            self._timer.stop()
        else:
            self._anim_pos += diff * 0.3
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        r = h / 2

        # Track
        off_color = QColor(SURFACE2)
        on_color = QColor(ACCENT)
        track_color = QColor(
            int(off_color.red() + (on_color.red() - off_color.red()) * self._anim_pos),
            int(off_color.green() + (on_color.green() - off_color.green()) * self._anim_pos),
            int(off_color.blue() + (on_color.blue() - off_color.blue()) * self._anim_pos),
        )
        p.setBrush(track_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)

        # Knob
        knob_r = h - 6
        knob_x = 3 + self._anim_pos * (w - knob_r - 6)
        knob_y = 3
        p.setBrush(QColor(FG))
        p.drawEllipse(QRectF(knob_x, knob_y, knob_r, knob_r))

        p.end()

    def mousePressEvent(self, event):
        self._checked = not self._checked
        self._timer.start()
        self.toggled.emit(self._checked)


# ======================================================================
#  Async signals for thread -> UI
# ======================================================================

class _AsyncSignals(QObject):
    tv_found = pyqtSignal(dict)
    scan_done = pyqtSignal()
    connect_result = pyqtSignal(str)      # error message, empty = success
    backlight_updated = pyqtSignal(int)    # target backlight value
    backlight_read = pyqtSignal(int)       # actual backlight from TV
    error = pyqtSignal(str)


# ======================================================================
#  Main Window
# ======================================================================

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("main")

        self.config = load_config()
        self.tv: TVController | None = None
        self.scheduler: BrightnessScheduler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._signals = _AsyncSignals()

        self._signals.connect_result.connect(self._on_connect_result)
        self._signals.backlight_updated.connect(self._on_backlight_updated)
        self._signals.backlight_read.connect(self._on_backlight_read)
        self._signals.error.connect(self._on_error)
        self._signals.tv_found.connect(self._on_tv_found)
        self._signals.scan_done.connect(self._on_scan_done)

        self._start_async_loop()
        self._build()

        if self.config.get("tv_ip"):
            self._do_connect_tv({"ip": self.config["tv_ip"], "name": self.config.get("tv_name"), "model": self.config.get("tv_model", ""), "serial": self.config.get("tv_serial", "")})
        else:
            self._show_connect_page()

        # Periodic backlight refresh
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_backlight)
        self._refresh_timer.start(30_000)

    def _start_async_loop(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _run_async(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ── Build UI ──

    def _build(self):
        self.setWindowTitle("LG AutoBright")
        self.setFixedSize(440, 560)
        self.setStyleSheet(STYLESHEET)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 18, 18, 18)
        self._layout.setSpacing(0)

        # -- Header --
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        self._title_label = QLabel("LG AutoBright")
        self._title_label.setFont(QFont(".AppleSystemUIFont", 18, QFont.Weight.Bold))
        header_row.addWidget(self._title_label)
        header_row.addStretch()
        self._toggle_switch = ToggleSwitch(checked=self.config.get("enabled", True))
        self._toggle_switch.toggled.connect(self._toggle_enabled)
        self._toggle_switch.hide()
        header_row.addWidget(self._toggle_switch)
        self._layout.addLayout(header_row)

        self._layout.addSpacing(4)
        self._subtitle_label = QLabel("Not connected")
        self._subtitle_label.setObjectName("dim")
        self._subtitle_label.setFont(QFont("Menlo", 11))
        self._layout.addWidget(self._subtitle_label)

        # -- Progress bar --
        self._layout.addSpacing(10)
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(3)
        self._layout.addWidget(self._progress)

        # -- Content area --
        self._layout.addSpacing(12)

        self._connected_widget = QWidget()
        self._connected_widget.setObjectName("main")
        self._build_connected_view()
        self._layout.addWidget(self._connected_widget, stretch=1)

        self._connect_widget = QWidget()
        self._connect_widget.setObjectName("main")
        self._build_connect_view()
        self._layout.addWidget(self._connect_widget, stretch=1)

        self._connected_widget.hide()
        self._connect_widget.hide()

        # Set pointer cursor on all buttons
        for btn in self.findChildren(QPushButton):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _build_connected_view(self):
        layout = QVBoxLayout(self._connected_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- TV info card --
        info = QWidget()
        info.setStyleSheet(f"background-color: {SURFACE0}; border-radius: 6px;")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(14, 10, 14, 10)
        info_layout.setSpacing(2)

        self._tv_name_label = QLabel("")
        self._tv_name_label.setFont(QFont("Menlo", 12))
        info_layout.addWidget(self._tv_name_label)

        self._tv_detail_label = QLabel("")
        self._tv_detail_label.setFont(QFont("Menlo", 10))
        self._tv_detail_label.setStyleSheet(f"color: {DIM};")
        self._tv_detail_label.setWordWrap(True)
        info_layout.addWidget(self._tv_detail_label)
        layout.addWidget(info)

        # -- Backlight display --
        layout.addSpacing(12)
        bl_row = QHBoxLayout()
        bl_row.setContentsMargins(0, 0, 0, 0)

        bl_label = QLabel("BACKLIGHT")
        bl_label.setObjectName("dim")
        bl_label.setFont(QFont(".AppleSystemUIFont", 10, QFont.Weight.Bold))
        bl_row.addWidget(bl_label)

        bl_row.addStretch()
        self._backlight_label = QLabel("--%")
        self._backlight_label.setObjectName("accent")
        self._backlight_label.setFont(QFont(".AppleSystemUIFont", 24, QFont.Weight.Bold))
        bl_row.addWidget(self._backlight_label)
        layout.addLayout(bl_row)

        layout.addSpacing(2)
        self._backlight_detail = QLabel("")
        self._backlight_detail.setObjectName("dim")
        self._backlight_detail.setFont(QFont("Menlo", 10))
        self._backlight_detail.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._backlight_detail)

        # -- Separator --
        layout.addSpacing(10)
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {SURFACE0};")
        layout.addWidget(sep)

        # -- Curve chart --
        layout.addSpacing(10)
        curve_header = QHBoxLayout()
        curve_header.setContentsMargins(0, 0, 0, 0)

        curve_label = QLabel("BACKLIGHT CURVE")
        curve_label.setObjectName("dim")
        curve_label.setFont(QFont(".AppleSystemUIFont", 10, QFont.Weight.Bold))
        curve_header.addWidget(curve_label)

        curve_header.addStretch()
        hint = QLabel("click to add  |  drag to move  |  right-click to remove")
        hint.setObjectName("dim")
        hint.setFont(QFont("Menlo", 8))
        curve_header.addWidget(hint)
        layout.addLayout(curve_header)

        layout.addSpacing(6)
        self._curve_chart = CurveChartWidget()
        self._curve_chart.curve_changed.connect(self._on_curve_changed)
        self._curve_chart.curve_dragging.connect(self._on_curve_dragging)
        layout.addWidget(self._curve_chart, stretch=1)

        # -- Bottom buttons --
        layout.addSpacing(10)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        from PyQt6.QtWidgets import QCheckBox
        self._autolaunch_cb = QCheckBox("Launch at login")
        self._autolaunch_cb.setFont(QFont("Menlo", 10))
        self._autolaunch_cb.setStyleSheet(f"color: {DIM}; spacing: 6px;")
        self._autolaunch_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autolaunch_cb.setChecked(_is_autolaunch_enabled())
        self._autolaunch_cb.toggled.connect(self._toggle_autolaunch)
        btn_row.addWidget(self._autolaunch_cb)

        btn_row.addStretch()

        disconnect_btn = QPushButton("Disconnect")
        disconnect_btn.setObjectName("danger")
        disconnect_btn.setFont(QFont("Menlo", 12))
        disconnect_btn.clicked.connect(self._disconnect)
        btn_row.addWidget(disconnect_btn)
        layout.addLayout(btn_row)

    def _build_connect_view(self):
        layout = QVBoxLayout(self._connect_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- Scan status --
        self._scan_status = QLabel("Searching your network...")
        self._scan_status.setObjectName("dim")
        self._scan_status.setFont(QFont("Menlo", 11))
        self._scan_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scan_status.setMinimumHeight(32)
        layout.addWidget(self._scan_status)

        # -- TV list --
        layout.addSpacing(4)
        self._tv_list = QListWidget()
        self._tv_list.setFont(QFont("Menlo", 12))
        self._tv_list.setIconSize(QSize(28, 28))
        self._tv_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {BG};
                border: none;
                padding: 0;
                outline: none;
            }}
            QListWidget::item {{
                background-color: {SURFACE0};
                color: {FG};
                padding: 10px 14px;
                border-radius: 8px;
                margin-bottom: 6px;
            }}
            QListWidget::item:selected {{
                background-color: {SURFACE2};
                border: 2px solid {ACCENT};
            }}
            QListWidget::item:hover {{
                background-color: {SURFACE1};
            }}
        """)
        self._tv_list.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tv_list.currentRowChanged.connect(self._on_tv_selection)
        self._tv_list.itemClicked.connect(self._on_tv_click)
        layout.addWidget(self._tv_list, stretch=1)

        # -- Rescan + Connect row --
        layout.addSpacing(10)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)

        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.setFont(QFont("Menlo", 11))
        self._rescan_btn.clicked.connect(self._start_scan)
        action_row.addWidget(self._rescan_btn)

        action_row.addStretch()

        self._scan_connect_btn = QPushButton("Connect")
        self._scan_connect_btn.setObjectName("primary")
        self._scan_connect_btn.setFont(QFont("Menlo", 11))
        self._scan_connect_btn.setEnabled(False)
        self._scan_connect_btn.clicked.connect(self._connect_selected)
        action_row.addWidget(self._scan_connect_btn)
        layout.addLayout(action_row)

        # -- Separator --
        layout.addSpacing(16)
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {SURFACE1};")
        layout.addWidget(sep)

        # -- Manual IP --
        layout.addSpacing(16)
        manual_label = QLabel("MANUAL CONNECTION")
        manual_label.setObjectName("dim")
        manual_label.setFont(QFont(".AppleSystemUIFont", 10, QFont.Weight.Bold))
        layout.addWidget(manual_label)

        layout.addSpacing(8)
        ip_row = QHBoxLayout()
        ip_row.setContentsMargins(0, 0, 0, 0)
        self._ip_entry = QLineEdit()
        self._ip_entry.setPlaceholderText("192.168.1.100")
        self._ip_entry.setFont(QFont("Menlo", 12))
        self._ip_entry.returnPressed.connect(self._manual_connect)
        ip_row.addWidget(self._ip_entry, stretch=1)

        ip_row.addSpacing(10)
        self._ip_connect_btn = QPushButton("Connect")
        self._ip_connect_btn.setFont(QFont("Menlo", 11))
        self._ip_connect_btn.clicked.connect(self._manual_connect)
        ip_row.addWidget(self._ip_connect_btn)
        layout.addLayout(ip_row)

        self._discovered_tvs: list[dict] = []

    # ── Page switching ──

    def _show_connect_page(self):
        self._connected_widget.hide()
        self._connect_widget.show()
        self._toggle_switch.hide()
        self._title_label.setText("LG AutoBright")
        self._subtitle_label.setText("Find your TV")
        self._subtitle_label.setStyleSheet(f"color: {DIM};")
        self._start_scan()

    def _show_connected_page(self):
        self._connect_widget.hide()
        self._connected_widget.show()

        name = self.config.get("tv_name", "")
        ip = self.config.get("tv_ip", "")
        model = self.config.get("tv_model", "")
        serial = self.config.get("tv_serial", "")

        self._tv_name_label.setText(name or ip)

        details = []
        if ip and name != ip:
            details.append(ip)
        if model:
            details.append(model)
        if serial:
            details.append(serial)
        self._tv_detail_label.setText("  ·  ".join(details) if details else ip)

        self._subtitle_label.setText("Connected")
        self._subtitle_label.setStyleSheet(f"color: {GREEN};")

        self._toggle_switch.setChecked(self.config.get("enabled", True))
        self._toggle_switch.show()

        curve = self.config.get("brightness_curve", {})
        self._curve_chart.set_curve(curve)
        self._update_curve_time()

        self._refresh_backlight()

    # ── Scan ──

    def _start_scan(self):
        self._tv_list.clear()
        self._discovered_tvs.clear()
        self._scan_connect_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._scan_status.setText("Searching your network...")
        self._scan_status.show()

        def scan_thread():
            from .discovery import discover_lg_tvs
            try:
                discover_lg_tvs(timeout=5.0, on_found=lambda tv: self._signals.tv_found.emit(tv))
            except Exception:
                pass
            self._signals.scan_done.emit()

        threading.Thread(target=scan_thread, daemon=True).start()

    def _make_tv_icon(self) -> QIcon:
        """Draw a simple TV icon."""
        pixmap = QPixmap(28, 28)
        pixmap.fill(QColor(0, 0, 0, 0))
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Screen
        p.setPen(QPen(QColor(ACCENT), 1.5))
        p.setBrush(QColor(SURFACE1))
        p.drawRoundedRect(QRectF(2, 3, 24, 16), 2, 2)
        # Stand
        p.setPen(QPen(QColor(DIM), 1.5))
        p.drawLine(10, 20, 18, 20)
        p.drawLine(14, 19, 14, 22)
        p.drawLine(10, 22, 18, 22)
        p.end()
        return QIcon(pixmap)

    def _on_tv_found(self, tv: dict):
        self._discovered_tvs.append(tv)
        n = len(self._discovered_tvs)
        self._scan_status.setText(f"{n} TV{'s' if n > 1 else ''} found")

        name = tv.get("name", "Unknown")
        ip = tv.get("ip", "?")
        serial = tv.get("serial", "")
        detail = ip
        if serial:
            detail = f"{ip}  ·  {serial}"
        item = QListWidgetItem(self._make_tv_icon(), f"{name}\n{detail}")
        item.setFont(QFont("Menlo", 11))
        self._tv_list.addItem(item)
        if n == 1:
            self._tv_list.setCurrentRow(0)

    def _on_scan_done(self):
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        n = len(self._discovered_tvs)
        if n == 0:
            self._scan_status.setText("No TVs found on your network")
        else:
            self._scan_status.setText(f"{n} TV{'s' if n > 1 else ''} found")

    def _on_tv_selection(self, row):
        self._scan_connect_btn.setEnabled(row >= 0)

    def _on_tv_click(self, item):
        row = self._tv_list.row(item)
        if 0 <= row < len(self._discovered_tvs):
            self._do_connect_tv(self._discovered_tvs[row])

    def _connect_selected(self):
        row = self._tv_list.currentRow()
        if 0 <= row < len(self._discovered_tvs):
            self._do_connect_tv(self._discovered_tvs[row])

    def _manual_connect(self):
        ip = self._ip_entry.text().strip()
        if ip:
            self._do_connect_tv({"ip": ip})

    # ── Connect / Disconnect ──

    def _do_connect_tv(self, tv: dict):
        ip = tv["ip"]
        self._progress.setRange(0, 0)
        self._title_label.setText(f"Connecting to {ip}...")
        self._subtitle_label.setText("")
        self._subtitle_label.setStyleSheet(f"color: {DIM};")

        self.config["tv_ip"] = ip
        self.config["tv_name"] = tv.get("name") or ip
        self.config["tv_model"] = tv.get("model", "")
        self.config["tv_serial"] = tv.get("serial", "")
        save_config(self.config)

        async def do():
            try:
                if self.scheduler:
                    await self.scheduler.stop()
                if self.tv:
                    await self.tv.disconnect()

                self.tv = TVController(ip)
                await self.tv.connect()
                self.scheduler = BrightnessScheduler(
                    self.tv, self.config,
                    on_update=lambda v: self._signals.backlight_read.emit(v),
                )
                await self.scheduler.start()
                self._signals.connect_result.emit("")
            except Exception as e:
                self._signals.connect_result.emit(str(e))

        self._run_async(do())

    def _on_connect_result(self, error: str):
        self._progress.setRange(0, 1)
        self._progress.setValue(1)

        if not error:
            self._title_label.setText("LG AutoBright")
            self._show_connected_page()
        else:
            short = error if len(error) < 80 else error[:77] + "..."
            self._title_label.setText("Connection failed")
            self._subtitle_label.setText(short)
            self._subtitle_label.setStyleSheet(f"color: {RED};")
            if not self._connect_widget.isVisible():
                self._show_connect_page()

    def _disconnect(self):
        async def do():
            try:
                if self.scheduler:
                    await self.scheduler.stop()
                    self.scheduler = None
                if self.tv:
                    await self.tv.disconnect()
                    self.tv = None
            except Exception as e:
                log.error("Disconnect failed: %s", e)

        self._run_async(do())
        self.config["tv_ip"] = None
        self.config["tv_name"] = None
        save_config(self.config)
        self._title_label.setText("LG AutoBright")
        self._backlight_label.setText("--%")
        self._backlight_detail.setText("")
        self._show_connect_page()

    # ── Backlight ──

    def _refresh_backlight(self):
        if not self.tv or not self.tv.is_connected:
            return

        self._update_curve_time()

        curve = self.config.get("brightness_curve", {})
        target = interpolate_brightness(curve)
        self._backlight_detail.setText(f"target: {target}%")

        async def do():
            try:
                actual = await self.tv.get_backlight()
                if actual is not None:
                    val = int(actual) if not isinstance(actual, int) else actual
                    self._signals.backlight_read.emit(val)
                    return
            except Exception:
                pass
            self._signals.backlight_updated.emit(target)

        self._run_async(do())

    def _update_curve_time(self):
        from datetime import datetime
        now = datetime.now()
        self._curve_chart.set_current_time(now.hour * 60 + now.minute)

    def _on_backlight_read(self, value: int):
        self._backlight_label.setText(f"{value}%")

    def _on_backlight_updated(self, value: int):
        self._backlight_label.setText(f"~{value}%")

    def _on_error(self, msg: str):
        log.error("Error: %s", msg)

    # ── Curve ──

    def _on_curve_dragging(self):
        """Live preview — update target label while dragging, no TV command."""
        curve = self._curve_chart.get_curve()
        target = interpolate_brightness(curve)
        self._backlight_detail.setText(f"target: {target}%")
        if self.config.get("enabled", True):
            self._backlight_label.setText(f"{target}%")

    def _on_curve_changed(self):
        new_curve = self._curve_chart.get_curve()
        self.config["brightness_curve"] = new_curve
        save_config(self.config)

        target = interpolate_brightness(new_curve)
        self._backlight_detail.setText(f"target: {target}%")

        if self.config.get("enabled", True) and self.scheduler:
            self._backlight_label.setText(f"{target}%")
            self._run_async(self.scheduler.update_once())

    # ── Toggle ──

    def _toggle_enabled(self, checked: bool):
        self.config["enabled"] = checked
        save_config(self.config)

        if checked and self.scheduler:
            self._run_async(self.scheduler.update_once())

    # ── Auto-launch ──

    def _toggle_autolaunch(self, checked: bool):
        if checked:
            _enable_autolaunch()
        else:
            _disable_autolaunch()

    # ── Window management ──

    def closeEvent(self, event):
        event.ignore()
        self.hide()


# ======================================================================
#  Launch at Login (macOS LaunchAgent)
# ======================================================================

import os
from pathlib import Path

_PLIST_NAME = "com.lgtv-autobright.plist"
_PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
_PLIST_PATH = _PLIST_DIR / _PLIST_NAME


def _is_autolaunch_enabled() -> bool:
    return _PLIST_PATH.exists()


def _enable_autolaunch():
    _PLIST_DIR.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lgtv-autobright</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>lgtv_autobright.app</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{Path.home()}</string>
</dict>
</plist>"""
    _PLIST_PATH.write_text(plist)


def _disable_autolaunch():
    if _PLIST_PATH.exists():
        _PLIST_PATH.unlink()


# ======================================================================
#  System Tray
# ======================================================================

class TrayIcon(QSystemTrayIcon):
    def __init__(self, window: MainWindow, app: QApplication):
        super().__init__(app)
        self._window = window
        self._app = app

        pixmap = QPixmap(22, 22)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setPen(QColor(FG))
        painter.setFont(QFont("Menlo", 11, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "LG")
        painter.end()
        self.setIcon(QIcon(pixmap))

        menu = QMenu()
        open_action = QAction("Open LG AutoBright", menu)
        open_action.triggered.connect(self._show_window)
        menu.addAction(open_action)
        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _show_window(self):
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _quit(self):
        self._app.quit()


# ======================================================================
#  Entry point
# ======================================================================

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    tray = TrayIcon(window, app)
    tray.show()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
