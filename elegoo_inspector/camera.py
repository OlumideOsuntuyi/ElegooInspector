"""MJPEG stream reader for the ESP32 camera module.

cv2.VideoCapture will open the ELEGOO stream, but it buffers internally and
you end up steering a robot from a picture of where it was a second ago. So
the primary path here parses the multipart stream by hand and keeps exactly
one frame in flight. VideoCapture stays as a fallback for local webcams,
files, and odd firmware.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

import cv2
import numpy as np

from .config import CameraConfig

SOI = b"\xff\xd8"  # JPEG start of image
EOI = b"\xff\xd9"  # JPEG end of image


class FrameSource:
    """Base: something that produces BGR numpy frames on a thread."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._latest: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._times: List[float] = []
        self.on_frame: List[Callable[[np.ndarray], None]] = []
        self.on_status: List[Callable[[str, str], None]] = []

    def _publish(self, frame: np.ndarray) -> None:
        with self._lock:
            self._latest = frame
            self._times.append(time.monotonic())
            if len(self._times) > 30:
                self._times.pop(0)
        for handler in list(self.on_frame):
            try:
                handler(frame)
            except Exception:
                pass

    def _status(self, state: str, detail: str = "") -> None:
        for handler in list(self.on_status):
            try:
                handler(state, detail)
            except Exception:
                pass

    @property
    def fps(self) -> float:
        with self._lock:
            if len(self._times) < 2:
                return 0.0
            span = self._times[-1] - self._times[0]
            count = len(self._times) - 1
        return count / span if span > 0 else 0.0

    def latest(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="frame-source")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._status("stopped", "")

    def _run(self) -> None:
        raise NotImplementedError


class MjpegStream(FrameSource):
    """Hand-rolled multipart/x-mixed-replace reader."""

    def __init__(self, url: str, config: CameraConfig):
        super().__init__()
        self.url = url
        self.config = config

    def _run(self) -> None:
        while not self._stop.is_set():
            self._status("connecting", self.url)
            try:
                response = urlopen(self.url, timeout=self.config.read_timeout)
            except Exception as exc:
                self._status("error", str(exc))
                self._stop.wait(self.config.reconnect_delay)
                continue
            self._status("streaming", self.url)
            try:
                self._consume(response)
            except Exception as exc:
                self._status("error", str(exc))
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            if not self._stop.is_set():
                self._stop.wait(self.config.reconnect_delay)

    def _consume(self, response) -> None:
        buffer = bytearray()
        while not self._stop.is_set():
            chunk = response.read(8192)
            if not chunk:
                return
            buffer.extend(chunk)
            # If a read fell behind (a slow scheduler tick, a GC pause, a
            # burst on the socket), the buffer can hold several complete
            # JPEGs at once. Decoding and publishing all of them serves up
            # a queue of stale frames one after another -- exactly the lag
            # this hand-rolled reader exists to avoid. Keep only the newest
            # complete frame and drop the rest of the backlog unread.
            newest = None
            while True:
                start = buffer.find(SOI)
                if start < 0:
                    if len(buffer) > (1 << 20):
                        del buffer[:-2]
                    break
                end = buffer.find(EOI, start + 2)
                if end < 0:
                    if start > 0:
                        del buffer[:start]
                    break
                newest = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
            if newest is None:
                continue
            frame = cv2.imdecode(np.frombuffer(newest, dtype=np.uint8),
                                 cv2.IMREAD_COLOR)
            if frame is None:
                continue
            self._publish(self._orient(frame))

    def _orient(self, frame: np.ndarray) -> np.ndarray:
        if self.config.flip_horizontal and self.config.flip_vertical:
            return cv2.flip(frame, -1)
        if self.config.flip_horizontal:
            return cv2.flip(frame, 1)
        if self.config.flip_vertical:
            return cv2.flip(frame, 0)
        return frame


class CaptureSource(FrameSource):
    """cv2.VideoCapture wrapper for local webcams, files, or RTSP."""

    def __init__(self, source, config: CameraConfig, mirror: bool = False):
        super().__init__()
        self.source = source
        self.config = config
        self.mirror = mirror

    def _run(self) -> None:
        while not self._stop.is_set():
            self._status("connecting", str(self.source))
            capture = cv2.VideoCapture(self.source)
            try:
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if not capture.isOpened():
                self._status("error", f"cannot open {self.source}")
                self._stop.wait(self.config.reconnect_delay)
                continue
            self._status("streaming", str(self.source))
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    break
                if self.mirror:
                    frame = cv2.flip(frame, 1)
                self._publish(frame)
            capture.release()
            if not self._stop.is_set():
                self._stop.wait(self.config.reconnect_delay)


class SyntheticSource(FrameSource):
    """Animated test pattern for demo mode."""

    def __init__(self, config: CameraConfig, size=(640, 480)):
        super().__init__()
        self.config = config
        self.size = size

    def _run(self) -> None:
        self._status("streaming", "synthetic")
        width, height = self.size
        tick = 0
        while not self._stop.is_set():
            frame = np.full((height, width, 3), 245, dtype=np.uint8)
            for x in range(0, width, 40):
                cv2.line(frame, (x, 0), (x, height), (228, 228, 228), 1)
            for y in range(0, height, 40):
                cv2.line(frame, (0, y), (width, y), (228, 228, 228), 1)
            cx = int(width / 2 + (width / 3) * np.sin(tick / 22.0))
            cy = int(height / 2 + (height / 5) * np.cos(tick / 17.0))
            cv2.circle(frame, (cx, cy), 46, (60, 60, 235), -1)
            cv2.circle(frame, (cx, cy), 46, (40, 40, 160), 2)
            cv2.rectangle(frame, (60, height - 120), (200, height - 40),
                          (90, 170, 90), -1)
            cv2.putText(frame, "DEMO SOURCE - NO HARDWARE", (18, 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (70, 70, 70), 2)
            self._publish(frame)
            tick += 1
            self._stop.wait(1 / 30)


def build_robot_source(stream_url: str, config: CameraConfig,
                       demo: bool = False) -> FrameSource:
    if demo:
        return SyntheticSource(config)
    parsed = urlparse(stream_url)
    if parsed.scheme in ("http", "https"):
        return MjpegStream(stream_url, config)
    return CaptureSource(stream_url, config)
