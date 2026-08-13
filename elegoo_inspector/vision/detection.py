"""Object detection.

Three backends, tried in the order of what you actually have installed:

  ultralytics  -- pip install ultralytics; fetches yolov8n.pt on first run
  opencv-dnn   -- drop MobileNet-SSD .prototxt/.caffemodel into ./models
  haar         -- always available, faces only, so the panel is never empty
                  on a bare install

Nothing here blocks the rest of the app. With no backend available the
plugin reports why in the Vision panel and stays out of the chain.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from .base import CVPlugin, DriveIntent, Param, PluginResult

MODELS_DIR = "models"

SSD_CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse", "motorbike",
    "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor",
]

PALETTE = [(203, 74, 43), (60, 60, 235), (60, 170, 90), (200, 120, 30),
           (150, 80, 200), (40, 160, 200)]


def _colour(index: int):
    return PALETTE[index % len(PALETTE)]


class ObjectDetector(CVPlugin):
    name = "Object detection"
    description = "Bounding boxes with labels. YOLO if available, else SSD, else faces."
    category = "detector"
    provides_control = True

    def __init__(self) -> None:
        super().__init__()
        self._backend = ""
        self._model = None
        self._net = None
        self._cascade = None
        self._names: List[str] = []

    def declare_params(self) -> None:
        self.add_param(Param("backend", "Backend", "choice", "auto",
                             choices=["auto", "ultralytics", "opencv-dnn", "haar"]))
        self.add_param(Param("confidence", "Min confidence", "float",
                             0.45, 0.05, 0.95, 0.05))
        self.add_param(Param("max_boxes", "Max boxes", "int", 12, 1, 50, 1))
        self.add_param(Param("filter", "Label filter", "choice", "all",
                             choices=["all", "person", "custom"]))
        self.add_param(Param("custom_label", "Custom label", "choice", "person",
                             choices=sorted(set(SSD_CLASSES[1:]))))
        self.add_param(Param("follow", "Follow largest match", "bool", False))
        self.add_param(Param("gain", "Steering gain", "float", 1.5, 0.2, 4.0, 0.1))
        self.add_param(Param("speed", "Approach speed", "float", 0.4, 0.1, 1.0, 0.05))
        self.add_param(Param("stop_fill", "Stop when box fills", "float",
                             0.22, 0.05, 0.8, 0.01))

    # -- backend selection ------------------------------------------------
    def availability(self) -> str:
        choice = self.get("backend", "auto")
        if choice in ("auto", "ultralytics") and self._try_import_yolo():
            return ""
        if choice in ("auto", "opencv-dnn") and self._ssd_files():
            return ""
        if choice in ("auto", "haar"):
            return ""
        return "Selected backend unavailable"

    @staticmethod
    def _try_import_yolo() -> bool:
        try:
            import ultralytics  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def _ssd_files() -> Optional[tuple]:
        proto = os.path.join(MODELS_DIR, "MobileNetSSD_deploy.prototxt")
        weights = os.path.join(MODELS_DIR, "MobileNetSSD_deploy.caffemodel")
        if os.path.exists(proto) and os.path.exists(weights):
            return proto, weights
        return None

    def start(self) -> None:
        choice = self.get("backend", "auto")
        errors = []

        if choice in ("auto", "ultralytics") and self._try_import_yolo():
            try:
                from ultralytics import YOLO
                weights = os.path.join(MODELS_DIR, "yolov8n.pt")
                # YOLO() fetches yolov8n.pt from the internet if it is not
                # already in ./models. You are normally joined to the car's
                # own WiFi AP at this point, which has no internet, so this
                # fails -- fall through to the next backend rather than
                # taking the whole plugin down with it.
                self._model = YOLO(weights if os.path.exists(weights)
                                   else "yolov8n.pt")
                self._names = list(self._model.names.values())
                self._backend = "ultralytics"
                return
            except Exception as exc:
                errors.append(f"ultralytics: {exc}")
                if choice == "ultralytics":
                    raise RuntimeError(errors[-1]) from exc

        files = self._ssd_files()
        if choice in ("auto", "opencv-dnn") and files:
            try:
                self._net = cv2.dnn.readNetFromCaffe(*files)
                self._names = SSD_CLASSES
                self._backend = "opencv-dnn"
                return
            except Exception as exc:
                errors.append(f"opencv-dnn: {exc}")
                if choice == "opencv-dnn":
                    raise RuntimeError(errors[-1]) from exc

        if choice in ("auto", "haar"):
            # Recent opencv-contrib-python wheels stopped bundling the
            # haarcascades data files under cv2.data.haarcascades, which
            # broke the promise that this backend is always available on a
            # bare install. Ship our own copy of the standard frontal-face
            # cascade and prefer it; fall back to the OpenCV install's own
            # copy in case an older wheel does still carry it.
            vendored = os.path.join(MODELS_DIR, "haarcascade_frontalface_default.xml")
            for path in (vendored,
                        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"):
                if not os.path.exists(path):
                    continue
                cascade = cv2.CascadeClassifier(path)
                if not cascade.empty():
                    self._cascade = cascade
                    self._names = ["face"]
                    self._backend = "haar"
                    return
            errors.append("haar: cascade file missing from this OpenCV install")

        detail = f" ({'; '.join(errors)})" if errors else ""
        raise RuntimeError(
            "No detection backend available" + detail + ". Install "
            "`ultralytics` with an internet connection at least once to "
            "cache the weights, or place MobileNetSSD_deploy.prototxt/"
            ".caffemodel in ./models.")

    def on_param_changed(self, key: str, value: Any) -> None:
        if key == "backend":
            self._model = self._net = self._cascade = None
            self._backend = ""

    def stop(self) -> None:
        self._model = self._net = self._cascade = None
        self._backend = ""

    # -- inference --------------------------------------------------------
    def _detect(self, frame: np.ndarray):
        """Return [(label, confidence, (x1, y1, x2, y2)), ...]."""
        threshold = float(self.get("confidence"))
        height, width = frame.shape[:2]

        if self._backend == "ultralytics":
            results = self._model.predict(frame, conf=threshold, verbose=False)
            output = []
            for result in results:
                for box in result.boxes:
                    x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                    label = self._model.names[int(box.cls[0])]
                    output.append((label, float(box.conf[0]), (x1, y1, x2, y2)))
            return output

        if self._backend == "opencv-dnn":
            blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)),
                                         0.007843, (300, 300), 127.5)
            self._net.setInput(blob)
            raw = self._net.forward()
            output = []
            for i in range(raw.shape[2]):
                confidence = float(raw[0, 0, i, 2])
                if confidence < threshold:
                    continue
                index = int(raw[0, 0, i, 1])
                label = SSD_CLASSES[index] if index < len(SSD_CLASSES) else str(index)
                box = raw[0, 0, i, 3:7] * np.array([width, height, width, height])
                x1, y1, x2, y2 = box.astype(int)
                output.append((label, confidence, (x1, y1, x2, y2)))
            return output

        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(grey, 1.2, 5, minSize=(40, 40))
        return [("face", 1.0, (x, y, x + w, y + h)) for (x, y, w, h) in faces]

    def _wanted(self, label: str) -> bool:
        mode = self.get("filter", "all")
        if mode == "all":
            return True
        if mode == "person":
            return label in ("person", "face")
        return label == self.get("custom_label")

    def process(self, frame: np.ndarray, context: Dict[str, Any]) -> PluginResult:
        if not self._backend:
            self.start()
        detections = [d for d in self._detect(frame) if self._wanted(d[0])]
        detections.sort(key=lambda d: (d[2][2] - d[2][0]) * (d[2][3] - d[2][1]),
                        reverse=True)
        detections = detections[:int(self.get("max_boxes"))]

        canvas = frame.copy()
        height, width = canvas.shape[:2]
        for index, (label, confidence, (x1, y1, x2, y2)) in enumerate(detections):
            colour = _colour(self._names.index(label)
                             if label in self._names else index)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, 2)
            caption = f"{label} {confidence:.2f}"
            (tw, th), _ = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(canvas, (x1, max(0, y1 - th - 8)),
                          (x1 + tw + 8, y1), colour, -1)
            cv2.putText(canvas, caption, (x1 + 4, max(12, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        intent = None
        if self.get("follow") and detections:
            _, _, (x1, y1, x2, y2) = detections[0]
            centre = (x1 + x2) / 2.0
            offset = (centre - width / 2) / (width / 2)
            fill = ((x2 - x1) * (y2 - y1)) / float(width * height)
            steer = float(np.clip(offset * float(self.get("gain")), -1.0, 1.0))
            throttle = 0.0 if fill >= float(self.get("stop_fill")) else \
                float(self.get("speed")) * (1.0 - 0.5 * abs(steer))
            intent = DriveIntent(throttle=throttle, steer=steer,
                                 source="object-follow")
            cv2.line(canvas, (width // 2, height),
                     (int(centre), (y1 + y2) // 2), (203, 74, 43), 2)

        labels = ", ".join(sorted({d[0] for d in detections})) or "nothing"
        return PluginResult(
            frame=canvas, intent=intent,
            overlay=[f"{self._backend}: {len(detections)} ({labels})"],
            data={"count": len(detections), "backend": self._backend,
                  "labels": [d[0] for d in detections]})
