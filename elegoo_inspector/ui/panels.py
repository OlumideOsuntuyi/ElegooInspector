"""Tab panels. Each is handed the MainWindow and talks to it directly; at
this size an event bus would be ceremony without benefit.
"""

from __future__ import annotations

import os
import time
from typing import Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                             QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QPlainTextEdit, QScrollArea, QSlider, QSpinBox,
                             QVBoxLayout, QWidget)

from .. import protocol
from ..recorder import Recording
from ..vision.base import CVPlugin, DriveIntent, Param
from .widgets import (Card, HoldButton, LiftButton, Metric, PathCanvas,
                      Sparkline, StatusPill)


def _scrollable(inner: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(inner)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


class ParamEditor(QWidget):
    """Builds controls from a plugin's declared Params."""

    def __init__(self, plugin: CVPlugin, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.plugin = plugin
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(9)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        if plugin.description:
            note = QLabel(plugin.description)
            note.setObjectName("Hint")
            note.setWordWrap(True)
            layout.addRow(note)

        for param in plugin.params():
            widget = self._build(param)
            if widget is None:
                continue
            if param.help:
                widget.setToolTip(param.help)
            layout.addRow(param.label, widget)

    def _build(self, param: Param) -> Optional[QWidget]:
        if param.kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(param.value))
            box.toggled.connect(lambda v, k=param.key: self.plugin.set(k, bool(v)))
            return box

        if param.kind == "choice":
            combo = QComboBox()
            combo.addItems(param.choices)
            if str(param.value) in param.choices:
                combo.setCurrentText(str(param.value))
            combo.currentTextChanged.connect(
                lambda v, k=param.key: self.plugin.set(k, v))
            return combo

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        slider = QSlider(Qt.Orientation.Horizontal)
        readout = QLabel()
        readout.setMinimumWidth(44)
        readout.setAlignment(Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter)

        if param.kind == "int":
            slider.setRange(int(param.minimum), int(param.maximum))
            slider.setValue(int(param.value))
            readout.setText(str(int(param.value)))

            def changed(value, key=param.key, label=readout):
                label.setText(str(value))
                self.plugin.set(key, int(value))

            slider.valueChanged.connect(changed)
        else:
            step = max(param.step, 1e-6)
            steps = max(1, int(round((param.maximum - param.minimum) / step)))
            slider.setRange(0, steps)
            slider.setValue(int(round((float(param.value) - param.minimum) / step)))
            readout.setText(f"{float(param.value):.2f}")

            def float_changed(value, key=param.key, label=readout, p=param):
                real = p.minimum + value * p.step
                label.setText(f"{real:.2f}")
                self.plugin.set(key, float(real))

            slider.valueChanged.connect(float_changed)

        layout.addWidget(slider, 1)
        layout.addWidget(readout)
        return row


# ---------------------------------------------------------------- Drive
class DrivePanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        speed_card = Card("Speed")
        self.speed_value = QLabel(f"{window.config.drive.default_speed_pct}%")
        self.speed_value.setObjectName("Value")
        speed_card.add_header_widget(self.speed_value)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(10, 100)
        self.speed_slider.setValue(window.config.drive.default_speed_pct)
        self.speed_slider.valueChanged.connect(self._speed_changed)
        speed_card.add(self.speed_slider)
        hint = QLabel("Scales every input source. The motors stall below "
                      "roughly a third of full PWM, so the slider maps into "
                      "the usable band rather than 0-255.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        speed_card.add(hint)
        layout.addWidget(speed_card)

        pad_card = Card("Manual control")
        grid = QGridLayout()
        grid.setSpacing(8)
        directions = {
            (0, 0): ("\u2196", 0.8, -0.7), (0, 1): ("\u2191", 1.0, 0.0),
            (0, 2): ("\u2197", 0.8, 0.7), (1, 0): ("\u2190", 0.0, -1.0),
            (1, 2): ("\u2192", 0.0, 1.0), (2, 0): ("\u2199", -0.8, -0.7),
            (2, 1): ("\u2193", -1.0, 0.0), (2, 2): ("\u2198", -0.8, 0.7),
        }
        for (row, column), (glyph, throttle, steer) in directions.items():
            button = HoldButton(glyph)
            button.setFixedSize(62, 54)
            button.held.connect(
                lambda t=throttle, s=steer: self.window.pilot.submit(
                    DriveIntent(t, s, source="ui")))
            button.released_signal.connect(
                lambda: self.window.pilot.release("ui"))
            grid.addWidget(button, row, column)

        stop_button = LiftButton("STOP", role="Primary")
        stop_button.setFixedSize(62, 54)
        stop_button.clicked.connect(self.window.pilot.halt)
        grid.addWidget(stop_button, 1, 1)

        pad_wrap = QHBoxLayout()
        pad_wrap.addStretch(1)
        pad_wrap.addLayout(grid)
        pad_wrap.addStretch(1)
        pad_card.add_layout(pad_wrap)
        keys = QLabel("Keyboard: W A S D or the arrow keys to drive, Q/E to "
                      "spin, Space to stop, Esc for emergency stop.")
        keys.setObjectName("Hint")
        keys.setWordWrap(True)
        pad_card.add(keys)
        layout.addWidget(pad_card)

        servo_card = Card("Camera servo")
        self.servo_labels: Dict[int, QLabel] = {}
        for servo_id, name, default in ((1, "Pan", 90), (2, "Tilt", 90)):
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(name)
            label.setMinimumWidth(34)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 180)
            slider.setValue(default)
            readout = QLabel(f"{default}")
            readout.setMinimumWidth(38)
            self.servo_labels[servo_id] = readout
            slider.valueChanged.connect(
                lambda value, s=servo_id: self._servo(s, value))
            row.addWidget(label)
            row.addWidget(slider, 1)
            row.addWidget(readout)
            servo_card.add_layout(row)
        centre = LiftButton("Centre servos")
        centre.clicked.connect(self._centre_servos)
        servo_card.add(centre)
        note = QLabel("Kits without a tilt servo simply ignore servo 2.")
        note.setObjectName("Hint")
        servo_card.add(note)
        layout.addWidget(servo_card)

        mode_card = Card("Firmware modes")
        note = QLabel("The UNO's own routines. While one is engaged, "
                      "ElegooInspector stops sending drive commands so the "
                      "two control loops do not fight each other.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        mode_card.add(note)
        self.mode_buttons: Dict[int, LiftButton] = {}
        for mode, label in protocol.MODE_NAMES.items():
            button = LiftButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _, m=mode: self._toggle_mode(m))
            self.mode_buttons[mode] = button
            mode_card.add(button)
        manual = LiftButton("Return to manual", role="Primary")
        manual.clicked.connect(lambda: self._toggle_mode(None))
        mode_card.add(manual)
        layout.addWidget(mode_card)

        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(inner))

    def _speed_changed(self, value: int) -> None:
        self.speed_value.setText(f"{value}%")
        self.window.pilot.set_speed_scale(value)

    def _servo(self, index: int, angle: int) -> None:
        self.servo_labels[index].setText(str(angle))
        self.window.pilot.servo(index, angle)

    def _centre_servos(self) -> None:
        for index in (1, 2):
            self.window.pilot.servo(index, 90)
            self.servo_labels[index].setText("90")

    def _toggle_mode(self, mode: Optional[int]) -> None:
        current = self.window.pilot.firmware_mode
        target = None if (mode is None or current == mode) else mode
        self.window.pilot.set_firmware_mode(target)
        for key, button in self.mode_buttons.items():
            button.setChecked(key == target)


# --------------------------------------------------------------- Vision
class VisionPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        source_card = Card("Feed")
        self.source_combo = QComboBox()
        self.source_combo.addItems(["Robot camera", "Local webcam", "Video file",
                                    "Camera off"])
        self.source_combo.currentTextChanged.connect(self._source_changed)
        source_card.add(self.source_combo)
        row = QHBoxLayout()
        snapshot = LiftButton("Snapshot")
        snapshot.clicked.connect(self.window.save_snapshot)
        open_browser = LiftButton("Open test page")
        open_browser.clicked.connect(self.window.open_test_page)
        row.addWidget(snapshot)
        row.addWidget(open_browser)
        source_card.add_layout(row)
        layout.addWidget(source_card)

        available_card = Card("Available processors")
        reload_button = LiftButton("Reload", role="Ghost")
        reload_button.clicked.connect(self.window.reload_plugins)
        available_card.add_header_widget(reload_button)
        self.available_list = QListWidget()
        self.available_list.setMaximumHeight(150)
        self.available_list.itemDoubleClicked.connect(lambda _: self._add_selected())
        available_card.add(self.available_list)
        add_button = LiftButton("Add to chain", role="Primary")
        add_button.clicked.connect(self._add_selected)
        available_card.add(add_button)
        hint = QLabel("Drop .py files into ./plugins and press Reload. "
                      "Subclass CVPlugin; plugins/example_lane_lines.py is a "
                      "working template.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        available_card.add(hint)
        layout.addWidget(available_card)

        chain_card = Card("Active chain")
        self.chain_list = QListWidget()
        self.chain_list.setMaximumHeight(150)
        self.chain_list.currentRowChanged.connect(self._show_params)
        self.chain_list.itemChanged.connect(self._toggle_enabled)
        chain_card.add(self.chain_list)
        buttons = QHBoxLayout()
        for label, handler in (("Up", lambda: self._move(-1)),
                               ("Down", lambda: self._move(1)),
                               ("Remove", self._remove)):
            button = LiftButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        chain_card.add_layout(buttons)
        order_hint = QLabel("Processors run top to bottom; each receives the "
                            "previous one's output frame.")
        order_hint.setObjectName("Hint")
        order_hint.setWordWrap(True)
        chain_card.add(order_hint)
        layout.addWidget(chain_card)

        self.params_card = Card("Parameters")
        self.params_holder = QWidget()
        self.params_layout = QVBoxLayout(self.params_holder)
        self.params_layout.setContentsMargins(0, 0, 0, 0)
        self.params_card.add(self.params_holder)
        layout.addWidget(self.params_card)

        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(inner))

    def refresh_available(self, classes) -> None:
        self.available_list.clear()
        for klass in classes:
            item = QListWidgetItem(klass.name)
            try:
                reason = klass().availability()
            except Exception as exc:
                reason = str(exc)
            if reason:
                item.setText(f"{klass.name}  -  {reason}")
                item.setForeground(Qt.GlobalColor.gray)
            item.setData(Qt.ItemDataRole.UserRole, klass)
            item.setToolTip(getattr(klass, "description", ""))
            self.available_list.addItem(item)

    def _chain_entries(self):
        """(plugin, pipeline) for every plugin, robot feed and gesture-camera
        feed alike -- the aux (gesture camera) pipeline runs a separate
        chain that otherwise has no UI of its own."""
        entries = [(p, self.window.pipeline) for p in self.window.pipeline.plugins]
        entries += [(p, self.window.aux_pipeline)
                   for p in self.window.aux_pipeline.plugins]
        return entries

    def refresh_chain(self) -> None:
        self.chain_list.blockSignals(True)
        self.chain_list.clear()
        for plugin, pipeline in self._chain_entries():
            suffix = "  (gesture cam)" if pipeline is self.window.aux_pipeline else ""
            item = QListWidgetItem(plugin.name + suffix)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if plugin.enabled
                               else Qt.CheckState.Unchecked)
            if plugin.last_error:
                item.setToolTip(plugin.last_error)
            self.chain_list.addItem(item)
        self.chain_list.blockSignals(False)

    def _add_selected(self) -> None:
        item = self.available_list.currentItem()
        if item is None:
            return
        self.window.add_plugin(item.data(Qt.ItemDataRole.UserRole))

    def _current_plugin(self) -> Optional[CVPlugin]:
        plugin, _ = self._current_entry()
        return plugin

    def _current_entry(self):
        row = self.chain_list.currentRow()
        entries = self._chain_entries()
        return entries[row] if 0 <= row < len(entries) else (None, None)

    def _move(self, delta: int) -> None:
        plugin, pipeline = self._current_entry()
        if plugin is None:
            return
        pipeline.move(plugin, delta)
        self.refresh_chain()

    def _remove(self) -> None:
        plugin, pipeline = self._current_entry()
        if plugin is None:
            return
        pipeline.remove(plugin)
        self.refresh_chain()
        self._clear_params()

    def _toggle_enabled(self, item: QListWidgetItem) -> None:
        row = self.chain_list.row(item)
        entries = self._chain_entries()
        if 0 <= row < len(entries):
            entries[row][0].enabled = item.checkState() == Qt.CheckState.Checked

    def _clear_params(self) -> None:
        while self.params_layout.count():
            child = self.params_layout.takeAt(0).widget()
            if child is not None:
                child.deleteLater()

    def _show_params(self, row: int) -> None:
        self._clear_params()
        plugin = self._current_plugin()
        if plugin is None:
            return
        self.params_layout.addWidget(ParamEditor(plugin))

    def _source_changed(self, text: str) -> None:
        if text == "Robot camera":
            self.window.set_feed("robot")
        elif text == "Local webcam":
            self.window.set_feed("local")
        elif text == "Camera off":
            self.window.set_feed("off")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Open video file")
            if path:
                self.window.set_feed("file", path)
            else:
                self.source_combo.setCurrentIndex(0)


# ----------------------------------------------------------------- Path
class PathPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._loaded: Optional[Recording] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        trace_card = Card("Trace")
        self.status_pill = StatusPill("idle", "idle")
        trace_card.add_header_widget(self.status_pill)
        self.canvas = PathCanvas()
        trace_card.add(self.canvas)
        zoom_row = QHBoxLayout()
        for label, factor in (("-", 1 / 1.25), ("+", 1.25)):
            button = LiftButton(label)
            button.setFixedWidth(40)
            button.clicked.connect(lambda _, f=factor: self.canvas.zoom(f))
            zoom_row.addWidget(button)
        clear_button = LiftButton("Clear trace")
        clear_button.clicked.connect(self._clear)
        zoom_row.addWidget(clear_button)
        zoom_row.addStretch(1)
        trace_card.add_layout(zoom_row)
        warning = QLabel("Dead reckoning from issued commands, not encoders. "
                         "Expect drift; calibrate in Settings.")
        warning.setObjectName("Hint")
        warning.setWordWrap(True)
        trace_card.add(warning)
        layout.addWidget(trace_card, 1)

        record_card = Card("Record")
        row = QHBoxLayout()
        self.record_button = LiftButton("Start recording", role="Primary")
        self.record_button.clicked.connect(self._toggle_record)
        mark_button = LiftButton("Drop marker")
        mark_button.clicked.connect(self._mark)
        row.addWidget(self.record_button, 1)
        row.addWidget(mark_button)
        record_card.add_layout(row)
        layout.addWidget(record_card)

        play_card = Card("Playback")
        self.recording_list = QListWidget()
        self.recording_list.setMaximumHeight(110)
        self.recording_list.currentRowChanged.connect(self._preview)
        play_card.add(self.recording_list)
        controls = QHBoxLayout()
        self.play_button = LiftButton("Play", role="Primary")
        self.play_button.clicked.connect(self._toggle_play)
        self.loop_box = QCheckBox("Loop")
        controls.addWidget(self.play_button)
        controls.addWidget(self.loop_box)
        controls.addWidget(QLabel("Rate"))
        self.rate_spin = QDoubleSpinBox()
        self.rate_spin.setRange(0.25, 3.0)
        self.rate_spin.setSingleStep(0.25)
        self.rate_spin.setValue(1.0)
        controls.addWidget(self.rate_spin)
        controls.addStretch(1)
        refresh = LiftButton("Refresh", role="Ghost")
        refresh.clicked.connect(self.refresh_recordings)
        controls.addWidget(refresh)
        play_card.add_layout(controls)
        caution = QLabel("Playback replays commands, not positions, so it will "
                         "diverge from the original run. Keep the emergency "
                         "stop within reach.")
        caution.setObjectName("Hint")
        caution.setWordWrap(True)
        play_card.add(caution)
        layout.addWidget(play_card)

        self.refresh_recordings()

    def refresh_recordings(self) -> None:
        self.recording_list.clear()
        for path in self.window.path_recorder.list_saved():
            item = QListWidgetItem(os.path.basename(path)[:-5])
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.recording_list.addItem(item)

    def _toggle_record(self) -> None:
        recorder = self.window.path_recorder
        if recorder.recording:
            path = recorder.finish(save=True)
            self.record_button.setText("Start recording")
            self.status_pill.set_state("idle", "idle")
            self.window.log(f"Saved recording: {path}" if path
                            else "Nothing recorded", "info")
            self.refresh_recordings()
        else:
            recorder.start()
            self.canvas.clear()
            self.record_button.setText("Stop and save")
            self.status_pill.set_state("recording", "error")

    def _mark(self) -> None:
        if self.window.path_recorder.recording:
            self.window.path_recorder.mark(time.strftime("%H:%M:%S"))
            self.window.log("Marker dropped", "info")

    def _clear(self) -> None:
        self.window.odometry.reset()
        self.canvas.clear()
        self.canvas.set_ghost([])

    def _preview(self, row: int) -> None:
        item = self.recording_list.item(row)
        if item is None:
            return
        try:
            self._loaded = self.window.path_recorder.load(
                item.data(Qt.ItemDataRole.UserRole))
        except Exception as exc:
            self.window.log(f"Could not load recording: {exc}", "error")
            return
        self.canvas.set_ghost(self.window.odometry.replay_trail(self._loaded))
        self.canvas.set_markers([(m.x, m.y, m.label)
                                 for m in self._loaded.markers])

    def _toggle_play(self) -> None:
        recorder = self.window.path_recorder
        if recorder.playing:
            recorder.stop_playback()
            self.window.pilot.halt()
            self.play_button.setText("Play")
            self.status_pill.set_state("idle", "idle")
            return
        if self._loaded is None:
            self.window.log("Select a recording first", "warn")
            return
        recorder.play(self._loaded, self.window.pilot.submit,
                      speed=self.rate_spin.value(),
                      loop=self.loop_box.isChecked())
        self.play_button.setText("Stop")
        self.status_pill.set_state("playing", "busy")


# ---------------------------------------------------------------- Input
class InputPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        pad_card = Card("Gamepad")
        self.pad_pill = StatusPill("not detected", "idle")
        pad_card.add_header_widget(self.pad_pill)
        self.pad_toggle = LiftButton("Enable gamepad", role="Primary")
        self.pad_toggle.setCheckable(True)
        self.pad_toggle.clicked.connect(self._toggle_pad)
        pad_card.add(self.pad_toggle)

        form = QFormLayout()
        form.setSpacing(8)
        self.axis_steer = QSpinBox()
        self.axis_steer.setRange(0, 15)
        self.axis_steer.setValue(0)
        self.axis_throttle = QSpinBox()
        self.axis_throttle.setRange(0, 15)
        self.axis_throttle.setValue(1)
        self.invert_box = QCheckBox("Invert throttle axis")
        self.invert_box.setChecked(True)
        self.deadzone = QDoubleSpinBox()
        self.deadzone.setRange(0.0, 0.5)
        self.deadzone.setSingleStep(0.02)
        self.deadzone.setValue(window.config.drive.stick_deadzone)
        self.axis_steer.valueChanged.connect(self._apply_mapping)
        self.axis_throttle.valueChanged.connect(self._apply_mapping)
        self.deadzone.valueChanged.connect(self._apply_mapping)
        self.invert_box.toggled.connect(self._apply_mapping)
        form.addRow("Steering axis", self.axis_steer)
        form.addRow("Throttle axis", self.axis_throttle)
        form.addRow("Deadzone", self.deadzone)
        form.addRow(self.invert_box)
        holder = QWidget()
        holder.setLayout(form)
        pad_card.add(holder)
        self.mapping_hint = QLabel()
        self.mapping_hint.setObjectName("Hint")
        self.mapping_hint.setWordWrap(True)
        self.set_controller_kind("")
        pad_card.add(self.mapping_hint)
        layout.addWidget(pad_card)

        gesture_card = Card("Gesture control")
        self.gesture_pill = StatusPill("inactive", "idle")
        gesture_card.add_header_widget(self.gesture_pill)
        explain = QLabel(
            "Open palm arms and steers, fist stops, losing the hand stops. "
            "Add it below, then tick 'Send drive commands' in its parameters "
            "on the Vision tab once you are happy with the preview.")
        explain.setObjectName("Hint")
        explain.setWordWrap(True)
        gesture_card.add(explain)
        self.gesture_source = QComboBox()
        self.gesture_source.addItems(["Local webcam (recommended)",
                                      "Robot camera"])
        self.gesture_source.currentIndexChanged.connect(self._gesture_source)
        gesture_card.add(self.gesture_source)
        add_gesture = LiftButton("Add gesture control", role="Primary")
        add_gesture.clicked.connect(self.window.add_gesture_plugin)
        gesture_card.add(add_gesture)
        layout.addWidget(gesture_card)

        keyboard_card = Card("Keyboard")
        keys = QLabel("W / Up      forward\n"
                      "S / Down    reverse\n"
                      "A / Left    left\n"
                      "D / Right   right\n"
                      "Q / E       spin in place\n"
                      "Space       stop\n"
                      "Esc         emergency stop\n"
                      "R           toggle path recording\n"
                      "[ / ]       speed down / up")
        keys.setStyleSheet("font-family:'JetBrains Mono',Menlo,Consolas,"
                           "monospace; font-size:12px; line-height:160%;")
        keyboard_card.add(keys)
        layout.addWidget(keyboard_card)

        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(inner))

    def _toggle_pad(self, checked: bool) -> None:
        if checked:
            reason = self.window.gamepad.availability()
            if reason:
                self.pad_toggle.setChecked(False)
                self.pad_pill.set_state(reason, "error")
                return
            self.window.gamepad.start()
            self.pad_toggle.setText("Disable gamepad")
        else:
            self.window.gamepad.stop()
            self.window.pilot.release("gamepad")
            self.pad_toggle.setText("Enable gamepad")
            self.pad_pill.set_state("stopped", "idle")

    def _apply_mapping(self) -> None:
        gamepad = self.window.gamepad
        gamepad.mapping["axis_steer"] = self.axis_steer.value()
        gamepad.mapping["axis_throttle"] = self.axis_throttle.value()
        gamepad.invert_throttle = self.invert_box.isChecked()
        self.window.config.drive.stick_deadzone = self.deadzone.value()

    def set_controller_kind(self, device_name: str) -> None:
        """Swap the button hint to match what is actually plugged in.

        pygame reports PlayStation pads (DualShock/DualSense) with the
        buttons in the same physical order as an Xbox pad, just with
        different faces printed on them, so the index mapping in
        gamepad.DEFAULT_MAP does not need to change -- only the label.
        """
        name = (device_name or "").strip().lower()
        # Sony pads report themselves generically as "Wireless Controller"
        # under Windows' XInput wrapper, with no other identifying text --
        # unlike "Xbox Wireless Controller", which does carry a vendor name.
        playstation = (
            any(k in name for k in ("playstation", "dualshock", "dualsense",
                                    "sony", "ps3", "ps4", "ps5"))
            or (name == "wireless controller"))
        if playstation:
            horn, estop, record, servo = "Cross", "Circle", "Square", "Triangle"
            slower, faster = "L1", "R1"
        else:
            horn, estop, record, servo = "A", "B", "X", "Y"
            slower, faster = "LB", "RB"
        self.mapping_hint.setText(
            f"Buttons: {horn} horn, {estop} emergency stop, {record} toggle "
            f"recording, {servo} centre servos, {slower}/{faster} speed. If "
            "the axes feel wrong, most controllers report the left stick as "
            "axes 0 and 1.")

    def _gesture_source(self, index: int) -> None:
        self.window.config.hand.source = "local" if index == 0 else "robot"
        self.window.log(
            f"Gesture source set to {self.window.config.hand.source}", "info")


# -------------------------------------------------------------- Console
class ConsolePanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        telemetry_card = Card("Telemetry")
        grid = QGridLayout()
        grid.setSpacing(14)
        self.distance = Metric("Ultrasonic", "--", " cm")
        self.line_sensors = Metric("Line sensors", "--")
        self.off_ground = Metric("Wheels", "--")
        self.tx_rx = Metric("TX / RX", "0 / 0")
        for column, metric in enumerate((self.distance, self.line_sensors,
                                         self.off_ground, self.tx_rx)):
            grid.addWidget(metric, 0, column)
        telemetry_card.add_layout(grid)
        self.spark = Sparkline(maximum=120)
        telemetry_card.add(self.spark)
        poll_row = QHBoxLayout()
        self.poll_box = QCheckBox("Poll sensors continuously")
        self.poll_box.toggled.connect(self.window.set_polling)
        poll_row.addWidget(self.poll_box)
        once = LiftButton("Read once")
        once.clicked.connect(self.window.poll_sensors)
        poll_row.addWidget(once)
        poll_row.addStretch(1)
        telemetry_card.add_layout(poll_row)
        layout.addWidget(telemetry_card)

        raw_card = Card("Raw command")
        hint = QLabel("Send a frame verbatim. Use this to verify opcodes "
                      "against your firmware revision before trusting them.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        raw_card.add(hint)
        row = QHBoxLayout()
        self.raw_input = QLineEdit()
        self.raw_input.setPlaceholderText('{"H":"1","N":3,"D1":3,"D2":150}')
        self.raw_input.returnPressed.connect(self._send_raw)
        send = LiftButton("Send", role="Primary")
        send.clicked.connect(self._send_raw)
        row.addWidget(self.raw_input, 1)
        row.addWidget(send)
        raw_card.add_layout(row)

        presets = QHBoxLayout()
        for label, builder in (("Stop", protocol.stop),
                               ("Distance", protocol.read_ultrasonic),
                               ("Line", protocol.read_line_sensors),
                               ("Off ground", protocol.read_off_ground)):
            button = LiftButton(label)
            button.clicked.connect(lambda _, b=builder: self.window.pilot.raw(b()))
            presets.addWidget(button)
        raw_card.add_layout(presets)
        layout.addWidget(raw_card)

        log_card = Card("Log")
        copy_button = LiftButton("Copy", role="Ghost")
        copy_button.clicked.connect(self._copy_log)
        log_card.add_header_widget(copy_button)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)
        self.log_view.setStyleSheet("font-family:'JetBrains Mono',Menlo,"
                                    "Consolas,monospace; font-size:11px;")
        log_card.add(self.log_view)
        layout.addWidget(log_card, 1)

    def _send_raw(self) -> None:
        text = self.raw_input.text().strip()
        if not text:
            return
        self.window.pilot.raw(text.encode("ascii", errors="ignore"))
        self.window.log(f"TX {text}", "info")
        self.raw_input.clear()

    def _copy_log(self) -> None:
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log_view.toPlainText())


# ------------------------------------------------------------- Settings
class SettingsPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        config = window.config
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)

        network_card = Card("Connection")
        form = QFormLayout()
        form.setSpacing(8)
        self.host = QLineEdit(config.network.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(config.network.control_port)
        self.stream = QLineEdit(config.network.stream_url)
        form.addRow("Host", self.host)
        form.addRow("Control port", self.port)
        form.addRow("Stream URL", self.stream)
        holder = QWidget()
        holder.setLayout(form)
        network_card.add(holder)
        network_hint = QLabel("Factory defaults are 192.168.4.1, port 100, "
                              "stream on :81. The car serves its own access "
                              "point; join it before connecting.")
        network_hint.setObjectName("Hint")
        network_hint.setWordWrap(True)
        network_card.add(network_hint)
        layout.addWidget(network_card)

        drive_card = Card("Drive protocol")
        form = QFormLayout()
        form.setSpacing(8)
        self.strategy = QComboBox()
        self.strategy.addItems(["n1_differential", "n4_pair", "n3_simple"])
        self.strategy.setCurrentText(config.drive.strategy)
        self.min_speed = QSpinBox()
        self.min_speed.setRange(0, 255)
        self.min_speed.setValue(config.drive.min_speed)
        self.max_speed = QSpinBox()
        self.max_speed.setRange(1, 255)
        self.max_speed.setValue(config.drive.max_speed)
        self.watchdog = QDoubleSpinBox()
        self.watchdog.setRange(0.1, 3.0)
        self.watchdog.setSingleStep(0.05)
        self.watchdog.setValue(config.drive.watchdog_timeout)
        self.flip_dir = QCheckBox("Swap forward and reverse")
        self.flip_dir.setChecked(config.drive.dir_forward != 1)
        form.addRow("Command family", self.strategy)
        form.addRow("Min PWM", self.min_speed)
        form.addRow("Max PWM", self.max_speed)
        form.addRow("Watchdog (s)", self.watchdog)
        form.addRow(self.flip_dir)
        holder = QWidget()
        holder.setLayout(form)
        drive_card.add(holder)
        drive_hint = QLabel("If the car does nothing, try another command "
                            "family. If it drives backwards, swap the "
                            "direction bytes. ELEGOO shipped several firmware "
                            "builds and they are not identical.")
        drive_hint.setObjectName("Hint")
        drive_hint.setWordWrap(True)
        drive_card.add(drive_hint)
        layout.addWidget(drive_card)

        odometry_card = Card("Odometry calibration")
        form = QFormLayout()
        form.setSpacing(8)
        self.speed_cal = QDoubleSpinBox()
        self.speed_cal.setRange(0.05, 3.0)
        self.speed_cal.setSingleStep(0.05)
        self.speed_cal.setValue(config.odometry.metres_per_second_at_full)
        self.turn_cal = QDoubleSpinBox()
        self.turn_cal.setRange(0.2, 10.0)
        self.turn_cal.setSingleStep(0.1)
        self.turn_cal.setValue(config.odometry.radians_per_second_at_full_turn)
        form.addRow("m/s at full throttle", self.speed_cal)
        form.addRow("rad/s at full spin", self.turn_cal)
        holder = QWidget()
        holder.setLayout(form)
        odometry_card.add(holder)
        cal_hint = QLabel("Drive straight for a measured distance, compare "
                          "with the trace, adjust. Then spin in place for a "
                          "known number of turns and adjust the second value.")
        cal_hint.setObjectName("Hint")
        cal_hint.setWordWrap(True)
        odometry_card.add(cal_hint)
        layout.addWidget(odometry_card)

        buttons = QHBoxLayout()
        apply_button = LiftButton("Apply", role="Primary")
        apply_button.clicked.connect(self.apply)
        save_button = LiftButton("Apply and save")
        save_button.clicked.connect(self.save)
        buttons.addWidget(apply_button)
        buttons.addWidget(save_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scrollable(inner))

    def apply(self) -> None:
        config = self.window.config
        config.network.host = self.host.text().strip()
        config.network.control_port = self.port.value()
        config.network.stream_url = self.stream.text().strip()
        config.drive.strategy = self.strategy.currentText()
        config.drive.min_speed = self.min_speed.value()
        config.drive.max_speed = self.max_speed.value()
        config.drive.watchdog_timeout = self.watchdog.value()
        config.drive.dir_forward = 2 if self.flip_dir.isChecked() else 1
        config.drive.dir_backward = 1 if self.flip_dir.isChecked() else 2
        config.odometry.metres_per_second_at_full = self.speed_cal.value()
        config.odometry.radians_per_second_at_full_turn = self.turn_cal.value()
        self.window.log("Settings applied. Reconnect to change host or port.",
                        "good")

    def save(self) -> None:
        self.apply()
        self.window.config.save()
        self.window.log("Settings saved", "good")
