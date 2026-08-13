"""Finger-pose driving via MediaPipe's HandLandmarker task.

Gesture contract, chosen so the failure mode is always "stop":

  open palm (4-5 fingers) -> ARMED; hand position steers
  fist (0 fingers)        -> immediate stop
  anything else           -> disarmed, no intent emitted
  hand leaves the frame   -> stop, so walking away does not latch a command

While armed, wrist position relative to frame centre maps to steering
(horizontal) and throttle (vertical, up is forward).

Normally you run this on a local webcam pointed at you, not the robot's
forward-facing camera. Choose the source in the Input panel.

This runs on the MediaPipe Tasks API (`HandLandmarker`), not the older
`mediapipe.solutions.hands` "Solutions" API -- that module was removed from
the package entirely as of MediaPipe 1.0, so any current `pip install
mediapipe` no longer has it. Unlike the old API, the model is not bundled in
the wheel; it is a `.task` file fetched once from Google's model store and
cached in ./models from then on. Fetch it while on a normal internet
connection -- the car's own WiFi access point has none.
"""

from __future__ import annotations

import os
import urllib.request
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from .base import CVPlugin, DriveIntent, Param, PluginResult

BLUE = (203, 74, 43)
RED = (60, 60, 235)
GREEN = (70, 170, 90)

TIP_IDS = [4, 8, 12, 16, 20]
PIP_IDS = [3, 6, 10, 14, 18]

MODELS_DIR = "models"
MODEL_PATH = os.path.join(MODELS_DIR, "hand_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
            "hand_landmarker/float16/latest/hand_landmarker.task")

# mediapipe.solutions.hands.HAND_CONNECTIONS doesn't exist under the Tasks
# API; this is that same fixed 21-landmark skeleton, hardcoded so the
# preview can still draw it.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)


class HandController(CVPlugin):
    name = "Gesture control"
    description = "Drive with an open palm; make a fist to stop."
    category = "control"
    provides_control = True
    runs_on_robot_feed = False

    def __init__(self) -> None:
        super().__init__()
        self._landmarker = None
        self._armed = False
        self._history: List[tuple] = []

    def declare_params(self) -> None:
        self.add_param(Param("enabled_control", "Send drive commands", "bool", False))
        self.add_param(Param("span", "Movement span", "float", 0.30, 0.10, 0.50, 0.01,
                             help="Fraction of the frame for full deflection"))
        self.add_param(Param("deadzone", "Deadzone", "float", 0.12, 0.0, 0.4, 0.01))
        self.add_param(Param("smoothing", "Smoothing", "float", 0.45, 0.0, 0.9, 0.05))
        self.add_param(Param("max_throttle", "Max throttle", "float", 0.6, 0.1, 1.0, 0.05))
        self.add_param(Param("confidence", "Detection confidence", "float",
                             0.6, 0.3, 0.95, 0.05))
        self.add_param(Param("invert_steer", "Invert steering", "bool", False))

    def availability(self) -> str:
        try:
            import mediapipe.tasks.python.vision  # noqa: F401
            return ""
        except Exception:
            return "Requires `pip install mediapipe`"

    @staticmethod
    def _ensure_model() -> str:
        if os.path.exists(MODEL_PATH):
            return MODEL_PATH
        os.makedirs(MODELS_DIR, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        return MODEL_PATH

    def start(self) -> None:
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (HandLandmarker,
                                                    HandLandmarkerOptions,
                                                    RunningMode)
        try:
            model_path = self._ensure_model()
        except Exception as exc:
            raise RuntimeError(
                f"Could not fetch the hand-tracking model ({exc}). Download "
                f"it once on a normal internet connection from {MODEL_URL} "
                f"and save it as {MODEL_PATH} -- the car's own WiFi access "
                "point has no internet.") from exc
        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=RunningMode.IMAGE,
            num_hands=1,
            min_hand_detection_confidence=float(self.get("confidence")),
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5)
        self._landmarker = HandLandmarker.create_from_options(options)

    def stop(self) -> None:
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:
                pass
        self._landmarker = None
        self._armed = False

    def on_param_changed(self, key: str, value: Any) -> None:
        if key == "confidence":
            # The landmarker bakes the detection threshold in at
            # construction time; rebuild it lazily on the next frame.
            self.stop()

    # -- gesture decoding ---------------------------------------------------
    @staticmethod
    def _finger_count(landmarks, handedness: str) -> int:
        count = 0
        # Thumb compares on x, and the direction depends on which hand it is.
        if handedness == "Right":
            count += int(landmarks[TIP_IDS[0]].x < landmarks[PIP_IDS[0]].x)
        else:
            count += int(landmarks[TIP_IDS[0]].x > landmarks[PIP_IDS[0]].x)
        # The other four: tip above the PIP joint in image coordinates.
        for tip, pip in zip(TIP_IDS[1:], PIP_IDS[1:]):
            count += int(landmarks[tip].y < landmarks[pip].y)
        return count

    def _smooth(self, throttle: float, steer: float) -> tuple:
        alpha = float(self.get("smoothing"))
        if not self._history:
            self._history.append((throttle, steer))
            return throttle, steer
        prev_t, prev_s = self._history[-1]
        out = (prev_t * alpha + throttle * (1 - alpha),
               prev_s * alpha + steer * (1 - alpha))
        self._history.append(out)
        if len(self._history) > 8:
            self._history.pop(0)
        return out

    def _draw_skeleton(self, canvas: np.ndarray, landmarks, width: int,
                       height: int) -> None:
        points = [(int(p.x * width), int(p.y * height)) for p in landmarks]
        for a, b in HAND_CONNECTIONS:
            cv2.line(canvas, points[a], points[b], (200, 200, 200), 2)
        for x, y in points:
            cv2.circle(canvas, (x, y), 3, BLUE, -1)

    def process(self, frame: np.ndarray, context: Dict[str, Any]) -> PluginResult:
        if self._landmarker is None:
            self.start()
        import mediapipe as mp

        height, width = frame.shape[:2]
        canvas = frame.copy()
        rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)

        span = float(self.get("span"))
        deadzone = float(self.get("deadzone"))
        intent: Optional[DriveIntent] = None
        status = "no hand"
        fingers = -1

        # Draw the control box so the operator can see the mapping.
        box = (int(width * (0.5 - span)), int(height * (0.5 - span)),
               int(width * (0.5 + span)), int(height * (0.5 + span)))
        cv2.rectangle(canvas, box[:2], box[2:], (215, 215, 215), 1)
        cv2.line(canvas, (width // 2, box[1]), (width // 2, box[3]),
                 (225, 225, 225), 1)
        cv2.line(canvas, (box[0], height // 2), (box[2], height // 2),
                 (225, 225, 225), 1)

        hands = result.hand_landmarks
        if hands:
            landmarks = hands[0]
            label = "Right"
            if result.handedness and result.handedness[0]:
                label = result.handedness[0][0].category_name
            fingers = self._finger_count(landmarks, label)
            self._draw_skeleton(canvas, landmarks, width, height)

            wrist = landmarks[0]
            px, py = int(wrist.x * width), int(wrist.y * height)

            if fingers == 0:
                self._armed = False
                self._history.clear()
                status = "FIST - stop"
                intent = DriveIntent(0.0, 0.0, source="gesture", stop=True)
            elif fingers >= 4:
                self._armed = True
                steer = (wrist.x - 0.5) / span
                throttle = -(wrist.y - 0.5) / span
                steer = 0.0 if abs(steer) < deadzone else steer
                throttle = 0.0 if abs(throttle) < deadzone else throttle
                if self.get("invert_steer"):
                    steer = -steer
                throttle = float(np.clip(throttle, -1, 1)) * float(
                    self.get("max_throttle"))
                steer = float(np.clip(steer, -1, 1))
                throttle, steer = self._smooth(throttle, steer)
                status = f"ARMED  thr {throttle:+.2f}  str {steer:+.2f}"
                intent = DriveIntent(throttle, steer, source="gesture")
                cv2.circle(canvas, (px, py), 12, GREEN, -1)
                cv2.arrowedLine(canvas, (width // 2, height // 2), (px, py),
                                GREEN, 2, tipLength=0.2)
            else:
                self._armed = False
                self._history.clear()
                status = f"{fingers} fingers - disarmed"
                cv2.circle(canvas, (px, py), 12, BLUE, 2)
        else:
            if self._armed:
                intent = DriveIntent(0.0, 0.0, source="gesture", stop=True)
            self._armed = False
            self._history.clear()

        colour = GREEN if self._armed else (RED if fingers == 0 else (90, 90, 90))
        cv2.rectangle(canvas, (0, 0), (width, 30), (255, 255, 255), -1)
        cv2.putText(canvas, status, (10, 21), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, colour, 2)

        if not self.get("enabled_control"):
            intent = None      # preview only until explicitly armed in the UI

        return PluginResult(frame=canvas, intent=intent, overlay=[status],
                            data={"armed": self._armed, "fingers": fingers})
