"""The application window: wiring, layout, and keyboard control.

Worker threads never touch widgets. They call plain callbacks, those
callbacks emit Qt signals from a single Bridge object, and Qt delivers them
to the GUI thread. That is the whole concurrency story.
"""

from __future__ import annotations

import os
import time
import webbrowser
from typing import Dict, List, Optional, Type

import cv2
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QSplitter,
                             QStatusBar, QTabWidget, QVBoxLayout, QWidget)

from .. import camera as camera_mod
from .. import protocol
from ..config import AppConfig
from ..control.gamepad import GamepadReader
from ..control.pilot import Pilot
from ..recorder import Odometry, PathRecorder
from ..transport import LoopbackTransport, Transport
from ..vision.base import CVPlugin, DriveIntent
from ..vision.loader import Pipeline, discover
from . import theme
from .panels import (ConsolePanel, DrivePanel, InputPanel, PathPanel,
                     SettingsPanel, VisionPanel)
from .widgets import Card, LiftButton, StatusPill, VideoView

KEY_INTENTS = {
    Qt.Key.Key_W: (1.0, 0.0), Qt.Key.Key_Up: (1.0, 0.0),
    Qt.Key.Key_S: (-1.0, 0.0), Qt.Key.Key_Down: (-1.0, 0.0),
    Qt.Key.Key_A: (0.0, -1.0), Qt.Key.Key_Left: (0.0, -1.0),
    Qt.Key.Key_D: (0.0, 1.0), Qt.Key.Key_Right: (0.0, 1.0),
    Qt.Key.Key_Q: (0.0, -1.0), Qt.Key.Key_E: (0.0, 1.0),
}


class Bridge(QObject):
    """Single hop from worker threads into the GUI thread."""
    frame = pyqtSignal(object)
    aux_frame = pyqtSignal(object)
    overlay = pyqtSignal(list)
    link_state = pyqtSignal(str, str)
    stream_state = pyqtSignal(str, str)
    pad_state = pyqtSignal(str, str)
    response = pyqtSignal(dict)
    drive_state = pyqtSignal(object)
    odometry = pyqtSignal(object, object)
    message = pyqtSignal(str, str)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, demo: bool = False):
        super().__init__()
        self.config = config
        self.demo = demo
        self.setWindowTitle("ElegooInspector")
        self.resize(1420, 900)

        self.bridge = Bridge()

        transport_class = LoopbackTransport if demo else Transport
        self.transport = transport_class(config.network)
        self.pilot = Pilot(self.transport, config.drive)
        self.odometry = Odometry(config.odometry)
        self.path_recorder = PathRecorder(self.odometry, config.recordings_dir)
        self.gamepad = GamepadReader(config.drive)

        self.pipeline = Pipeline()
        self.aux_pipeline = Pipeline()
        self.source: Optional[camera_mod.FrameSource] = None
        self.aux_source: Optional[camera_mod.FrameSource] = None
        self.plugin_classes: List[Type[CVPlugin]] = []

        self._held_keys: set = set()
        self._pending_reads: Dict[str, str] = {}
        self._last_overlay: List[str] = []

        self._build_ui()
        self._wire()
        self.reload_plugins()

        self.pipeline.start()
        self.aux_pipeline.start()
        self.pilot.start()
        self.transport.start()
        self.set_feed("robot")

        self._key_timer = QTimer(self)
        self._key_timer.timeout.connect(self._pump_keys)
        self._key_timer.start(50)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(500)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.poll_sensors)

        if demo:
            self.log("Demo mode: synthetic camera, simulated link.", "warn")

    # ------------------------------------------------------------ layout
    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(10)
        outer.addWidget(self._build_topbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)
        splitter.addWidget(self._build_video_side())
        splitter.addWidget(self._build_panel_side())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([820, 560])
        outer.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def _build_topbar(self) -> QWidget:
        bar = Card(margins=(16, 10, 16, 10), spacing=0)
        row = QHBoxLayout()
        row.setSpacing(12)

        title = QLabel("ElegooInspector")
        title.setStyleSheet(f"font-size:17px;font-weight:700;color:{theme.INK};"
                            "letter-spacing:-0.2px;")
        subtitle = QLabel("Smart Robot Car V4.0")
        subtitle.setObjectName("Hint")
        stack = QVBoxLayout()
        stack.setSpacing(0)
        stack.addWidget(title)
        stack.addWidget(subtitle)
        row.addLayout(stack)
        row.addSpacing(18)

        self.link_pill = StatusPill("disconnected", "idle")
        self.stream_pill = StatusPill("no stream", "idle")
        self.source_pill = StatusPill("idle", "idle")
        for pill in (self.link_pill, self.stream_pill, self.source_pill):
            row.addWidget(pill)
        row.addStretch(1)

        self.connect_button = LiftButton("Disconnect", role="Primary")
        self.connect_button.clicked.connect(self._toggle_connection)
        row.addWidget(self.connect_button)

        self.estop_button = LiftButton("EMERGENCY STOP", role="Danger")
        self.estop_button.clicked.connect(self._toggle_estop)
        row.addWidget(self.estop_button)

        bar.add_layout(row)
        return bar

    def _build_video_side(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        video_card = Card("Camera", margins=(12, 12, 12, 12))
        self.video = VideoView()
        self.video.clicked.connect(self._video_clicked)
        video_card.add(self.video)
        layout.addWidget(video_card, 1)

        self.aux_card = Card("Gesture camera", margins=(12, 12, 12, 12))
        self.aux_video = VideoView()
        self.aux_video.setMinimumHeight(190)
        self.aux_video.setMaximumHeight(240)
        self.aux_video.set_placeholder("Gesture camera off")
        self.aux_card.add(self.aux_video)
        self.aux_card.hide()
        layout.addWidget(self.aux_card)
        return container

    def _build_panel_side(self) -> QWidget:
        self.tabs = QTabWidget()
        self.drive_panel = DrivePanel(self)
        self.vision_panel = VisionPanel(self)
        self.path_panel = PathPanel(self)
        self.input_panel = InputPanel(self)
        self.console_panel = ConsolePanel(self)
        self.settings_panel = SettingsPanel(self)
        for widget, label in ((self.drive_panel, "Drive"),
                              (self.vision_panel, "Vision"),
                              (self.path_panel, "Path"),
                              (self.input_panel, "Input"),
                              (self.console_panel, "Console"),
                              (self.settings_panel, "Settings")):
            self.tabs.addTab(widget, label)
        return self.tabs

    # -------------------------------------------------------------- wiring
    def _wire(self) -> None:
        bridge = self.bridge
        self.transport.on_state.append(
            lambda state, detail: bridge.link_state.emit(state, detail))
        self.transport.on_log.append(
            lambda message, level: bridge.message.emit(message, level))
        self.transport.on_response.append(
            lambda payload: bridge.response.emit(payload))

        self.pilot.on_log.append(
            lambda message, level: bridge.message.emit(message, level))
        self.pilot.on_state.append(lambda state: bridge.drive_state.emit(state))
        self.pilot.on_command.append(self._on_command)

        self.pipeline.on_result.append(
            lambda frame, result: (bridge.frame.emit(frame),
                                   bridge.overlay.emit(result.overlay)))
        self.pipeline.on_intent.append(self.pilot.submit)
        self.pipeline.on_error.append(
            lambda name, error: bridge.message.emit(f"{name}: {error}", "error"))

        self.aux_pipeline.on_result.append(
            lambda frame, result: bridge.aux_frame.emit(frame))
        self.aux_pipeline.on_intent.append(self.pilot.submit)
        self.aux_pipeline.on_error.append(
            lambda name, error: bridge.message.emit(f"{name}: {error}", "error"))

        self.gamepad.on_intent.append(self.pilot.submit)
        self.gamepad.on_status.append(
            lambda state, detail: bridge.pad_state.emit(state, detail))
        self.gamepad.on_button.append(self._gamepad_button)

        self.path_recorder.on_state.append(
            lambda state, detail: bridge.message.emit(
                f"Path: {state} {detail}".strip(), "info"))

        bridge.frame.connect(self.video.set_frame)
        bridge.aux_frame.connect(self.aux_video.set_frame)
        bridge.overlay.connect(self._set_overlay)
        bridge.link_state.connect(self._link_state)
        bridge.stream_state.connect(self._stream_state)
        bridge.pad_state.connect(self._pad_state)
        bridge.response.connect(self._response)
        bridge.drive_state.connect(self._drive_state)
        bridge.odometry.connect(self._odometry_update)
        bridge.message.connect(self.log)

    # ------------------------------------------------------------- sources
    def set_feed(self, kind: str, path: str = "") -> None:
        if self.source is not None:
            self.source.stop()
            self.source = None
        self.video.clear_frame()

        if kind == "off":
            self.video.set_placeholder("Camera feed off")
            self.bridge.stream_state.emit("stopped", "")
            return

        if kind == "robot":
            self.source = camera_mod.build_robot_source(
                self.config.network.stream_url, self.config.camera, self.demo)
            self.video.set_placeholder("Connecting to robot camera")
        elif kind == "local":
            self.source = camera_mod.CaptureSource(
                self.config.hand.local_device_index, self.config.camera,
                mirror=True)
            self.video.set_placeholder("Opening local webcam")
        else:
            self.source = camera_mod.CaptureSource(path, self.config.camera)
            self.video.set_placeholder(os.path.basename(path))

        self.source.on_frame.append(self.pipeline.submit)
        self.source.on_status.append(
            lambda state, detail: self.bridge.stream_state.emit(state, detail))
        self.source.start()

    def _ensure_aux_source(self) -> None:
        if self.aux_source is not None:
            return
        self.aux_source = camera_mod.CaptureSource(
            self.config.hand.local_device_index, self.config.camera, mirror=True)
        self.aux_source.on_frame.append(self.aux_pipeline.submit)
        self.aux_source.on_status.append(
            lambda state, detail: self.bridge.message.emit(
                f"Gesture camera: {state} {detail}".strip(),
                "error" if state == "error" else "info"))
        self.aux_source.start()
        self.aux_card.show()

    def _stop_aux_source(self) -> None:
        if self.aux_source is not None:
            self.aux_source.stop()
            self.aux_source = None
        self.aux_card.hide()
        self.pilot.release("gesture")

    # ------------------------------------------------------------- plugins
    def reload_plugins(self) -> None:
        self.plugin_classes = discover(self.config.plugin_dirs)
        self.vision_panel.refresh_available(self.plugin_classes)
        self.log(f"{len(self.plugin_classes)} vision processors available",
                 "info")

    def add_plugin(self, klass: Type[CVPlugin]) -> None:
        try:
            plugin = klass()
            reason = plugin.availability()
            if reason:
                self.log(f"{klass.name} unavailable: {reason}", "warn")
                return
            plugin.start()
        except Exception as exc:
            self.log(f"Could not start {klass.name}: {exc}", "error")
            return

        if not plugin.runs_on_robot_feed:
            self._ensure_aux_source()
            self.aux_pipeline.add(plugin)
            self.log(f"{plugin.name} added to the gesture camera", "good")
        else:
            self.pipeline.add(plugin)
            self.log(f"{plugin.name} added to the chain", "good")
        self.vision_panel.refresh_chain()

    def add_gesture_plugin(self) -> None:
        for klass in self.plugin_classes:
            if klass.name == "Gesture control":
                klass.runs_on_robot_feed = self.config.hand.source == "robot"
                self.add_plugin(klass)
                return
        self.log("Gesture control plugin not found", "error")

    # --------------------------------------------------------------- slots
    def _set_overlay(self, lines: List[str]) -> None:
        state = self.pilot.state
        header = [f"{self.source.fps:.0f} fps" if self.source else "",
                  f"pipeline {self.pipeline.latency_ms:.0f} ms"]
        if state.moving:
            header.append(f"{state.source}  L {state.left:+.2f}"
                          f"  R {state.right:+.2f}")
        self._last_overlay = [t for t in header if t] + list(lines)
        self.video.set_overlay(self._last_overlay)

    def _link_state(self, state: str, detail: str) -> None:
        level = {"connected": "good", "connecting": "busy"}.get(state, "error")
        self.link_pill.set_state(state, level)
        self.connect_button.setText(
            "Disconnect" if state == "connected" else "Connect")

    def _stream_state(self, state: str, detail: str) -> None:
        level = {"streaming": "good", "connecting": "busy",
                 "stopped": "idle"}.get(state, "error")
        self.stream_pill.set_state(state, level)
        if state == "error":
            self.video.set_placeholder(f"Stream error: {detail}")

    def _pad_state(self, state: str, detail: str) -> None:
        level = {"connected": "good", "disconnected": "warn"}.get(state, "error")
        self.input_panel.pad_pill.set_state(detail or state, level)
        if state == "connected":
            self.input_panel.set_controller_kind(detail)

    def _drive_state(self, state) -> None:
        self.source_pill.set_state(state.source if state.moving else "idle",
                                   "busy" if state.moving else "idle")

    def _on_command(self, left: float, right: float, dt: float) -> None:
        pose = self.odometry.integrate(left, right, dt)
        self.path_recorder.capture(left, right, dt, self.pilot.state.source)
        self.bridge.odometry.emit(list(self.odometry.trail),
                                  (pose.x, pose.y, pose.heading))

    def _odometry_update(self, trail, pose) -> None:
        self.path_panel.canvas.set_trail(trail, pose)

    def _response(self, payload: dict) -> None:
        tag = str(payload.get("tag", ""))
        kind = self._pending_reads.pop(tag, "")
        panel = self.console_panel
        if kind == "distance" and "number" in payload:
            value = payload["number"]
            level = "error" if value < 15 else ("warn" if value < 30 else "good")
            panel.distance.set_value(str(value), level)
            panel.spark.push(value)
        elif kind == "line":
            panel.line_sensors.set_value(str(payload.get("value", "--")))
        elif kind == "ground":
            flag = payload.get("flag")
            panel.off_ground.set_value("off ground" if flag else "on ground",
                                       "warn" if flag else "good")
        elif payload.get("raw", "") != "{ok}":
            self.log(f"RX {payload.get('raw', '')}", "rx")

    # ------------------------------------------------------------- actions
    def _toggle_connection(self) -> None:
        if self.transport.connected:
            self.pilot.halt()
            self.transport.stop()
        else:
            self.transport.config.host = self.config.network.host
            self.transport.config.control_port = self.config.network.control_port
            self.transport.start()

    def _toggle_estop(self) -> None:
        if self.pilot.estopped:
            self.pilot.clear_estop()
            self.estop_button.setText("EMERGENCY STOP")
        else:
            self.pilot.emergency_stop()
            self.estop_button.setText("CLEAR STOP")

    def set_polling(self, enabled: bool) -> None:
        if enabled:
            self._poll_timer.start(600)
        else:
            self._poll_timer.stop()

    def poll_sensors(self) -> None:
        for kind, opcode, field in (("distance", protocol.N_ULTRASONIC, 2),
                                    ("line", protocol.N_LINE_TRACK, 0),
                                    ("ground", protocol.N_OFF_GROUND, 0)):
            tag = protocol.next_tag()
            self._pending_reads[tag] = kind
            self.pilot.raw(protocol.frame(opcode, tag=tag, d1=field))
        if len(self._pending_reads) > 60:
            self._pending_reads.clear()

    def save_snapshot(self) -> None:
        frame = self.source.latest() if self.source else None
        if frame is None:
            self.log("No frame to save", "warn")
            return
        os.makedirs("recordings", exist_ok=True)
        path = time.strftime("recordings/snapshot_%Y%m%d_%H%M%S.png")
        cv2.imwrite(path, frame)
        self.log(f"Saved {path}", "good")

    def open_test_page(self) -> None:
        webbrowser.open(self.config.network.test_page_url)

    def _video_clicked(self, nx: float, ny: float) -> None:
        self.pipeline.context["click"] = (nx, ny)
        self.log(f"Click at {nx:.2f}, {ny:.2f} published to plugin context",
                 "info")

    def _gamepad_button(self, action: str) -> None:
        if action == "estop":
            self._toggle_estop()
        elif action == "record":
            self.path_panel._toggle_record()
        elif action == "servo":
            self.drive_panel._centre_servos()
        elif action == "horn":
            self.pilot.raw(protocol.rgb(0, 0, 255))
        elif action in ("faster", "slower"):
            step = 10 if action == "faster" else -10
            slider = self.drive_panel.speed_slider
            slider.setValue(slider.value() + step)

    # ------------------------------------------------------------ keyboard
    def keyPressEvent(self, event: QKeyEvent) -> None:      # noqa: N802
        if event.isAutoRepeat():
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._toggle_estop()
            return
        if key == Qt.Key.Key_Space:
            self._held_keys.clear()
            self.pilot.halt()
            return
        if key == Qt.Key.Key_R:
            self.path_panel._toggle_record()
            return
        if key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight):
            slider = self.drive_panel.speed_slider
            slider.setValue(slider.value()
                            + (5 if key == Qt.Key.Key_BracketRight else -5))
            return
        if key in KEY_INTENTS:
            self._held_keys.add(key)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:     # noqa: N802
        if event.isAutoRepeat():
            return
        self._held_keys.discard(event.key())
        if not self._held_keys:
            self.pilot.release("keyboard")
        super().keyReleaseEvent(event)

    def _pump_keys(self) -> None:
        if not self._held_keys:
            return
        throttle = steer = 0.0
        for key in self._held_keys:
            dt, ds = KEY_INTENTS[key]
            throttle += dt
            steer += ds
        self.pilot.submit(DriveIntent(max(-1.0, min(1.0, throttle)),
                                      max(-1.0, min(1.0, steer)),
                                      source="keyboard"))

    # -------------------------------------------------------------- status
    def _refresh_status(self) -> None:
        stats = self.transport.stats
        self.console_panel.tx_rx.set_value(
            f"{stats['sent']} / {stats['received']}")
        fps = self.source.fps if self.source else 0.0
        self.statusBar().showMessage(
            f"{fps:.0f} fps   |   pipeline {self.pipeline.latency_ms:.0f} ms"
            f"   |   {len(self.pipeline.plugins)} processors"
            f"   |   speed {int(self.pilot.speed_scale * 100)}%")

    def log(self, message: str, level: str = "info") -> None:
        colour = {"good": theme.GREEN, "warn": theme.AMBER, "error": theme.RED,
                  "rx": theme.BLUE}.get(level, theme.INK_MUTED)
        stamp = time.strftime("%H:%M:%S")
        self.console_panel.log_view.appendHtml(
            f'<span style="color:{theme.INK_FAINT}">{stamp}</span> '
            f'<span style="color:{colour}">{message}</span>')

    # ------------------------------------------------------------ shutdown
    def closeEvent(self, event) -> None:      # noqa: N802
        try:
            self.pilot.halt()
            self.transport.send_now(protocol.stop())
        except Exception:
            pass
        self.path_recorder.stop_playback()
        self.gamepad.stop()
        if self.source:
            self.source.stop()
        self._stop_aux_source()
        self.pipeline.stop()
        self.aux_pipeline.stop()
        self.pilot.stop()
        self.transport.stop()
        super().closeEvent(event)
