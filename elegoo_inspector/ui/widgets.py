"""Reusable widgets: cards with shadow, animated buttons, the video surface,
status pills, telemetry readouts, and the path-trace canvas.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRectF,
                          Qt, QTimer, pyqtProperty, pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                         QPixmap)
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                             QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                             QWidget)

from . import theme


def shadow(widget: QWidget, blur: int = 26, dy: int = 6, alpha: int = 26) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(20, 28, 60, alpha))
    widget.setGraphicsEffect(effect)


class Card(QFrame):
    """White rounded panel with a soft shadow and an optional title row."""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None,
                 spacing: int = 10,
                 margins: Tuple[int, int, int, int] = (16, 14, 16, 16)):
        super().__init__(parent)
        self.setObjectName("Card")
        shadow(self)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)
        self.header: Optional[QHBoxLayout] = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(8)
            label = QLabel(title.upper())
            label.setObjectName("CardTitle")
            self.header.addWidget(label)
            self.header.addStretch(1)
            self._layout.addLayout(self.header)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self._layout.addLayout(layout)

    def add_header_widget(self, widget: QWidget) -> None:
        if self.header is not None:
            self.header.addWidget(widget)


class LiftButton(QPushButton):
    """Button that animates a small lift on hover. Subtle, not bouncy."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None,
                 role: str = ""):
        super().__init__(text, parent)
        if role:
            self.setObjectName(role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"lift", self)
        self._animation.setDuration(130)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_lift(self) -> float:
        return self._offset

    def _set_lift(self, value: float) -> None:
        delta = value - self._offset
        self._offset = value
        self.move(self.pos() + QPoint(0, int(-delta)))

    lift = pyqtProperty(float, fget=_get_lift, fset=_set_lift)

    def enterEvent(self, event):      # noqa: N802
        self._animation.stop()
        self._animation.setEndValue(2.0)
        self._animation.start()
        super().enterEvent(event)

    def leaveEvent(self, event):      # noqa: N802
        self._animation.stop()
        self._animation.setEndValue(0.0)
        self._animation.start()
        super().leaveEvent(event)


class HoldButton(QPushButton):
    """Emits `held` repeatedly while pressed, so the car only moves while the
    button is actually down."""

    held = pyqtSignal()
    released_signal = pyqtSignal()

    def __init__(self, text: str, parent: Optional[QWidget] = None,
                 interval: int = 60):
        super().__init__(text, parent)
        self.setObjectName("Pad")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self.held.emit)
        self.pressed.connect(self._begin)
        self.released.connect(self._end)

    def _begin(self) -> None:
        self.held.emit()
        self._timer.start()

    def _end(self) -> None:
        self._timer.stop()
        self.released_signal.emit()


class StatusPill(QLabel):
    """Small rounded state chip: connection, stream, controller."""

    COLOURS = {
        "good": (theme.GREEN, "#E7F5EE"),
        "warn": (theme.AMBER, "#FDF3E0"),
        "error": (theme.RED, "#FBEAEA"),
        "idle": (theme.INK_FAINT, "#EFF1F6"),
        "busy": (theme.BLUE, theme.BLUE_SOFT),
    }

    def __init__(self, text: str = "", level: str = "idle",
                 parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(24)
        self.set_state(text, level)

    def set_state(self, text: str, level: str = "idle") -> None:
        fg, bg = self.COLOURS.get(level, self.COLOURS["idle"])
        self.setText(f"  {text}  ")
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:12px;"
            f"padding:3px 10px; font-size:11px; font-weight:600;"
            f"letter-spacing:0.3px;")


class Metric(QWidget):
    """Label plus a large value, for telemetry readouts."""

    def __init__(self, title: str, value: str = "--", suffix: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self._title = QLabel(title.upper())
        self._title.setObjectName("CardTitle")
        self._value = QLabel(value)
        self._value.setObjectName("Value")
        self._suffix = suffix
        layout.addWidget(self._title)
        layout.addWidget(self._value)

    def set_value(self, value: str, level: str = "") -> None:
        self._value.setText(f"{value}{self._suffix}")
        colour = {"good": theme.GREEN, "warn": theme.AMBER,
                  "error": theme.RED}.get(level, theme.INK)
        self._value.setStyleSheet(
            f"font-size:20px;font-weight:600;color:{colour};")


class VideoView(QWidget):
    """Aspect-correct frame surface with an overlay strip.

    Clicking reports normalised coordinates, which plugins can read from the
    pipeline context for click-to-select targets.
    """

    clicked = pyqtSignal(float, float)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(480, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._pixmap: Optional[QPixmap] = None
        self._overlay: List[str] = []
        self._placeholder = "Waiting for stream"
        self._target_rect = QRectF()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        if self._pixmap is None:
            self.update()

    def set_frame(self, frame: np.ndarray) -> None:
        height, width = frame.shape[:2]
        rgb = np.ascontiguousarray(frame[:, :, ::-1])
        image = QImage(rgb.data, width, height, 3 * width,
                       QImage.Format.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def clear_frame(self) -> None:
        self._pixmap = None
        self.update()

    def set_overlay(self, lines: List[str]) -> None:
        self._overlay = list(lines)[:6]

    def mousePressEvent(self, event):     # noqa: N802
        if self._target_rect.width() > 0 and \
                self._target_rect.contains(event.position()):
            nx = (event.position().x() - self._target_rect.x()) \
                / self._target_rect.width()
            ny = (event.position().y() - self._target_rect.y()) \
                / self._target_rect.height()
            self.clicked.emit(nx, ny)
        super().mousePressEvent(event)

    def paintEvent(self, event):          # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, theme.RADIUS, theme.RADIUS)
        painter.setClipPath(path)
        painter.fillPath(path, QColor("#0E1220"))

        if self._pixmap is None:
            painter.setPen(QColor(theme.INK_FAINT))
            painter.setFont(QFont("Inter", 12))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             self._placeholder)
            self._target_rect = QRectF()
            painter.end()
            return

        scaled = self._pixmap.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        x = (self.width() - scaled.width()) / 2
        y = (self.height() - scaled.height()) / 2
        self._target_rect = QRectF(x, y, scaled.width(), scaled.height())
        painter.drawPixmap(int(x), int(y), scaled)

        if self._overlay:
            painter.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
            metrics = painter.fontMetrics()
            line_height = metrics.height() + 6
            box_height = line_height * len(self._overlay) + 8
            width = max(metrics.horizontalAdvance(t) for t in self._overlay) + 22
            box = QRectF(x + 10, y + 10, width, box_height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 225))
            painter.drawRoundedRect(box, 8, 8)
            painter.setPen(QColor(theme.INK))
            for index, text in enumerate(self._overlay):
                painter.drawText(
                    QRectF(box.x() + 11, box.y() + 4 + index * line_height,
                           box.width() - 22, line_height),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    text)
        painter.end()


class PathCanvas(QWidget):
    """Top-down dead-reckoning trace with grid, heading marker and notes."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self._trail: List[Tuple[float, float]] = []
        self._ghost: List[Tuple[float, float]] = []
        self._pose = (0.0, 0.0, 0.0)
        self._markers: List[Tuple[float, float, str]] = []
        self._scale = 90.0        # pixels per metre
        self._follow = True

    def set_trail(self, points, pose=None) -> None:
        self._trail = list(points)
        if pose is not None:
            self._pose = pose
        self.update()

    def set_ghost(self, points) -> None:
        """A loaded recording drawn faintly behind the live trace."""
        self._ghost = list(points)
        self.update()

    def set_markers(self, markers) -> None:
        self._markers = list(markers)
        self.update()

    def zoom(self, factor: float) -> None:
        self._scale = max(15.0, min(600.0, self._scale * factor))
        self.update()

    def clear(self) -> None:
        self._trail = []
        self._markers = []
        self._pose = (0.0, 0.0, 0.0)
        self.update()

    def _to_screen(self, x: float, y: float, cx: float, cy: float) -> QPoint:
        # World +x is drawn to the right, world +y is drawn up.
        return QPoint(int(cx + x * self._scale), int(cy - y * self._scale))

    def paintEvent(self, event):      # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        painter.setClipPath(path)
        painter.fillPath(path, QColor(theme.WHITE))

        origin_x, origin_y = self.width() / 2, self.height() / 2
        if self._follow and self._trail:
            origin_x -= self._pose[0] * self._scale
            origin_y += self._pose[1] * self._scale

        painter.setPen(QPen(QColor("#EDEFF5"), 1))
        step = self._scale * 0.5
        if step > 6:
            gx = origin_x % step
            while gx < self.width():
                painter.drawLine(int(gx), 0, int(gx), self.height())
                gx += step
            gy = origin_y % step
            while gy < self.height():
                painter.drawLine(0, int(gy), self.width(), int(gy))
                gy += step

        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1,
                            Qt.PenStyle.DashLine))
        painter.drawLine(int(origin_x), 0, int(origin_x), self.height())
        painter.drawLine(0, int(origin_y), self.width(), int(origin_y))

        def draw_polyline(points, pen: QPen) -> None:
            if len(points) < 2:
                return
            painter.setPen(pen)
            previous = self._to_screen(points[0][0], points[0][1],
                                       origin_x, origin_y)
            for px, py in points[1:]:
                current = self._to_screen(px, py, origin_x, origin_y)
                painter.drawLine(previous, current)
                previous = current

        draw_polyline(self._ghost, QPen(QColor(theme.BLUE_EDGE), 4,
                                        Qt.PenStyle.SolidLine,
                                        Qt.PenCapStyle.RoundCap))
        draw_polyline(self._trail, QPen(QColor(theme.BLUE), 2,
                                        Qt.PenStyle.SolidLine,
                                        Qt.PenCapStyle.RoundCap))

        for mx, my, label in self._markers:
            point = self._to_screen(mx, my, origin_x, origin_y)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.AMBER))
            painter.drawEllipse(point, 4, 4)
            painter.setPen(QColor(theme.INK_MUTED))
            painter.drawText(point + QPoint(7, 4), label)

        x, y, heading = self._pose
        centre = self._to_screen(x, y, origin_x, origin_y)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BLUE))
        painter.drawEllipse(centre, 7, 7)
        painter.setPen(QPen(QColor(theme.BLUE), 2))
        painter.drawLine(centre, centre + QPoint(int(16 * math.cos(heading)),
                                                 int(-16 * math.sin(heading))))

        painter.setPen(QColor(theme.INK_FAINT))
        painter.drawText(10, self.height() - 10,
                         f"{self._scale:.0f} px/m    x {x:+.2f} m"
                         f"    y {y:+.2f} m    hdg {math.degrees(heading):+.0f}")
        painter.end()


class Sparkline(QWidget):
    """Tiny rolling chart for one telemetry channel."""

    def __init__(self, maximum: float = 100.0, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(42)
        self._values: List[float] = []
        self._maximum = maximum

    def push(self, value: float) -> None:
        self._values.append(float(value))
        if len(self._values) > 120:
            self._values.pop(0)
        self._maximum = max(self._maximum, abs(float(value)))
        self.update()

    def clear(self) -> None:
        self._values = []
        self.update()

    def paintEvent(self, event):      # noqa: N802
        if len(self._values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        step = width / (len(self._values) - 1)
        scale = self._maximum or 1.0
        path = QPainterPath()
        for index, value in enumerate(self._values):
            px = index * step
            py = height - (abs(value) / scale) * (height - 6) - 3
            if index == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)
        painter.setPen(QPen(QColor(theme.BLUE), 2))
        painter.drawPath(path)
        painter.end()
