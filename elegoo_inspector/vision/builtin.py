"""Built-in vision plugins. Read these as templates for your own."""

from __future__ import annotations

import os
import time
from typing import Any, Dict

import cv2
import numpy as np

from .base import CVPlugin, DriveIntent, Param, PluginResult

BLUE = (203, 74, 43)      # royal blue in BGR
DARK = (48, 48, 48)


class Crosshair(CVPlugin):
    name = "Crosshair & grid"
    description = "Centre reticle and rule-of-thirds guides for aiming the car."
    category = "overlay"

    def declare_params(self) -> None:
        self.add_param(Param("grid", "Thirds grid", "bool", True))
        self.add_param(Param("opacity", "Opacity", "float", 0.6, 0.1, 1.0, 0.05))

    def process(self, frame: np.ndarray, context: Dict[str, Any]) -> PluginResult:
        canvas = frame.copy()
        height, width = canvas.shape[:2]
        layer = canvas.copy()
        if self.get("grid"):
            for i in (1, 2):
                cv2.line(layer, (width * i // 3, 0), (width * i // 3, height),
                         (200, 200, 200), 1)
                cv2.line(layer, (0, height * i // 3), (width, height * i // 3),
                         (200, 200, 200), 1)
        cx, cy = width // 2, height // 2
        cv2.line(layer, (cx - 18, cy), (cx - 5, cy), BLUE, 2)
        cv2.line(layer, (cx + 5, cy), (cx + 18, cy), BLUE, 2)
        cv2.line(layer, (cx, cy - 18), (cx, cy - 5), BLUE, 2)
        cv2.line(layer, (cx, cy + 5), (cx, cy + 18), BLUE, 2)
        alpha = float(self.get("opacity", 0.6))
        cv2.addWeighted(layer, alpha, canvas, 1 - alpha, 0, canvas)
        return PluginResult(frame=canvas)


class EdgeView(CVPlugin):
    name = "Edge view"
    description = "Canny edges blended over the feed. Useful for framing."
    category = "filter"

    def declare_params(self) -> None:
        self.add_param(Param("low", "Low threshold", "int", 60, 0, 255, 1))
        self.add_param(Param("high", "High threshold", "int", 160, 0, 255, 1))
        self.add_param(Param("blend", "Blend", "float", 0.7, 0.0, 1.0, 0.05))

    def process(self, frame: np.ndarray, context: Dict[str, Any]) -> PluginResult:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(grey, int(self.get("low")), int(self.get("high")))
        coloured = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
        coloured[np.where((coloured == [255, 255, 255]).all(axis=2))] = BLUE
        blend = float(self.get("blend"))
        out = cv2.addWeighted(coloured, blend, frame, 1 - blend * 0.5, 0)
        return PluginResult(frame=out,
                            data={"edge_pixels": int(edges.sum() // 255)})


class MotionDetector(CVPlugin):
    name = "Motion detector"
    description = "Frame differencing with contour boxes; flags movement while parked."
    category = "detector"

    def __init__(self) -> None:
        super().__init__()
        self._background = None

    def declare_params(self) -> None:
        self.add_param(Param("sensitivity", "Sensitivity", "int", 25, 5, 90, 1))
        self.add_param(Param("min_area", "Min area (px)", "int", 700, 50, 20000, 50))
        self.add_param(Param("decay", "Background decay", "float", 0.08, 0.01, 0.6, 0.01))

    def process(self, frame: np.ndarray, context: Dict[str, Any]) -> PluginResult:
        grey = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                (21, 21), 0)
        if self._background is None:
            self._background = grey.astype("float")
            return PluginResult(frame=frame)
        cv2.accumulateWeighted(grey, self._background, float(self.get("decay")))
        delta = cv2.absdiff(grey, cv2.convertScaleAbs(self._background))
        _, mask = cv2.threshold(delta, int(self.get("sensitivity")), 255,
                                cv2.THRESH_BINARY)
        mask = cv2.dilate(mask, None, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        canvas = frame.copy()
        hits = 0
        for contour in contours:
            if cv2.contourArea(contour) < int(self.get("min_area")):
                continue
            hits += 1
            x, y, w, h = cv2.boundingRect(contour)
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (60, 60, 235), 2)
        overlay = [f"motion regions: {hits}"] if hits else []
        return PluginResult(frame=canvas, overlay=overlay, data={"regions": hits})


class ColourTracker(CVPlugin):
    """HSV blob tracker that can optionally drive the car toward the target."""

    name = "Colour tracker"
    description = "Track the largest blob of a hue range; optionally chase it."
    category = "detector"
    provides_control = True

    def declare_params(self) -> None:
        self.add_param(Param("hue", "Hue centre", "int", 10, 0, 179, 1,
                             help="0/179 red, 30 yellow, 60 green, 120 blue"))
        self.add_param(Param("hue_width", "Hue width", "int", 12, 2, 60, 1))
        self.add_param(Param("sat_min", "Min saturation", "int", 120, 0, 255, 1))
        self.add_param(Param("val_min", "Min value", "int", 80, 0, 255, 1))
        self.add_param(Param("min_area", "Min area (px)", "int", 900, 100, 60000, 100))
        self.add_param(Param("follow", "Drive toward target", "bool", False))
        self.add_param(Param("target_fill", "Stop when blob fills", "float",
                             0.16, 0.02, 0.6, 0.01))
        self.add_param(Param("gain", "Steering gain", "float", 1.6, 0.2, 4.0, 0.1))
        self.add_param(Param("speed", "Approach speed", "float", 0.45, 0.1, 1.0, 0.05))

    def process(self, frame: np.ndarray, context: Dict[str, Any]) -> PluginResult:
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hue = int(self.get("hue"))
        span = int(self.get("hue_width"))
        sat, val = int(self.get("sat_min")), int(self.get("val_min"))

        low_hue, high_hue = hue - span, hue + span
        if low_hue < 0 or high_hue > 179:   # hue wraps around red
            mask_a = cv2.inRange(hsv, (0, sat, val),
                                 (max(0, high_hue % 180), 255, 255))
            mask_b = cv2.inRange(hsv, (low_hue % 180, sat, val), (179, 255, 255))
            mask = cv2.bitwise_or(mask_a, mask_b)
        else:
            mask = cv2.inRange(hsv, (low_hue, sat, val), (high_hue, 255, 255))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        canvas = frame.copy()
        if not contours:
            return PluginResult(frame=canvas, data={"locked": False})
        best = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(best)
        if area < int(self.get("min_area")):
            return PluginResult(frame=canvas, data={"locked": False})

        x, y, w, h = cv2.boundingRect(best)
        cx, cy = x + w // 2, y + h // 2
        cv2.rectangle(canvas, (x, y), (x + w, y + h), BLUE, 2)
        cv2.circle(canvas, (cx, cy), 5, BLUE, -1)
        cv2.line(canvas, (width // 2, height), (cx, cy), BLUE, 1)

        offset = (cx - width / 2) / (width / 2)
        fill = area / float(width * height)
        cv2.putText(canvas, f"off {offset:+.2f}  fill {fill:.3f}",
                    (x, max(18, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, DARK, 1)

        intent = None
        if self.get("follow"):
            steer = float(np.clip(offset * float(self.get("gain")), -1.0, 1.0))
            if fill >= float(self.get("target_fill")):
                throttle = 0.0
            else:
                throttle = float(self.get("speed")) * (1.0 - 0.5 * abs(steer))
            intent = DriveIntent(throttle=throttle, steer=steer,
                                 source="colour-tracker")

        return PluginResult(frame=canvas, intent=intent,
                            overlay=[f"target offset {offset:+.2f}"],
                            data={"locked": True, "offset": offset,
                                  "fill": fill, "centre": (cx, cy)})


class VideoWriterPlugin(CVPlugin):
    """Writes the annotated feed to an mp4. Put it last in the chain."""

    name = "Video writer"
    description = "Save the processed feed to disk as mp4."
    category = "output"

    def __init__(self) -> None:
        super().__init__()
        self._writer = None
        self._path = ""

    def declare_params(self) -> None:
        self.add_param(Param("recording", "Recording", "bool", False))
        self.add_param(Param("fps", "Assumed FPS", "int", 15, 5, 60, 1))

    def on_param_changed(self, key: str, value: Any) -> None:
        if key == "recording" and not value:
            self._close()

    def _close(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def stop(self) -> None:
        self._close()

    def process(self, frame: np.ndarray, context: Dict[str, Any]) -> PluginResult:
        if not self.get("recording"):
            self._close()
            return PluginResult(frame=frame)
        if self._writer is None:
            os.makedirs("recordings", exist_ok=True)
            height, width = frame.shape[:2]
            self._path = time.strftime("recordings/feed_%Y%m%d_%H%M%S.mp4")
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._writer = cv2.VideoWriter(self._path, fourcc,
                                           float(self.get("fps")),
                                           (width, height))
        self._writer.write(frame)
        canvas = frame.copy()
        cv2.circle(canvas, (canvas.shape[1] - 24, 24), 8, (60, 60, 235), -1)
        return PluginResult(frame=canvas, overlay=[f"REC -> {self._path}"])
