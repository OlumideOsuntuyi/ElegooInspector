"""Path recording, dead-reckoning odometry, and playback.

There are no wheel encoders on this kit, so none of this is true odometry.
The trace is integrated from the commands we *sent*, which means wheel slip,
carpet, a low battery or a hand on the chassis all make it drift. It is
still useful for seeing the shape of a run and for replaying it, which is
what people actually want from this feature. Calibrate in Settings before
believing the numbers.

A recording is a list of timestamped segments. Playback re-emits them
through the Pilot as ordinary intents, so the watchdog, the speed scale and
the emergency stop all still apply during replay.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import OdometryConfig
from .vision.base import DriveIntent


@dataclass
class Segment:
    t: float                 # seconds since recording start
    dt: float                # how long this command was held
    left: float              # -1..1
    right: float             # -1..1
    source: str = ""


@dataclass
class Marker:
    t: float
    x: float
    y: float
    label: str
    kind: str = "note"


@dataclass
class Pose:
    x: float = 0.0           # metres
    y: float = 0.0
    heading: float = 0.0     # radians, 0 = +x


@dataclass
class Recording:
    name: str = ""
    created: float = field(default_factory=time.time)
    segments: List[Segment] = field(default_factory=list)
    markers: List[Marker] = field(default_factory=list)
    notes: str = ""

    @property
    def duration(self) -> float:
        return (self.segments[-1].t + self.segments[-1].dt) if self.segments else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "created": self.created, "notes": self.notes,
                "segments": [asdict(s) for s in self.segments],
                "markers": [asdict(m) for m in self.markers]}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Recording":
        rec = cls(name=raw.get("name", ""), created=raw.get("created", time.time()),
                  notes=raw.get("notes", ""))
        rec.segments = [Segment(**s) for s in raw.get("segments", [])]
        rec.markers = [Marker(**m) for m in raw.get("markers", [])]
        return rec


class Odometry:
    """Differential-drive integrator fed by normalised wheel commands."""

    def __init__(self, config: OdometryConfig):
        self.config = config
        self.pose = Pose()
        self.trail: List[tuple] = [(0.0, 0.0)]
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.pose = Pose()
            self.trail = [(0.0, 0.0)]

    def integrate(self, left: float, right: float, dt: float) -> Pose:
        if dt <= 0 or dt > 1.0:
            return self.pose
        linear = 0.5 * (left + right) * self.config.metres_per_second_at_full
        angular = 0.5 * (right - left) * \
            self.config.radians_per_second_at_full_turn
        with self._lock:
            self.pose.heading = _wrap(self.pose.heading + angular * dt)
            self.pose.x += linear * math.cos(self.pose.heading) * dt
            self.pose.y += linear * math.sin(self.pose.heading) * dt
            last = self.trail[-1]
            if (self.pose.x - last[0]) ** 2 + (self.pose.y - last[1]) ** 2 > 4e-4:
                self.trail.append((self.pose.x, self.pose.y))
                if len(self.trail) > 8000:
                    del self.trail[:2000]
            return Pose(self.pose.x, self.pose.y, self.pose.heading)

    def replay_trail(self, recording: Recording) -> List[tuple]:
        """Compute the path a recording would trace, without driving."""
        pose = Pose()
        points = [(0.0, 0.0)]
        for segment in recording.segments:
            linear = 0.5 * (segment.left + segment.right) * \
                self.config.metres_per_second_at_full
            angular = 0.5 * (segment.right - segment.left) * \
                self.config.radians_per_second_at_full_turn
            pose.heading = _wrap(pose.heading + angular * segment.dt)
            pose.x += linear * math.cos(pose.heading) * segment.dt
            pose.y += linear * math.sin(pose.heading) * segment.dt
            points.append((pose.x, pose.y))
        return points


class PathRecorder:
    """Captures Pilot output into a Recording, and plays it back."""

    def __init__(self, odometry: Odometry, directory: str = "recordings"):
        self.odometry = odometry
        self.directory = directory
        self.current: Optional[Recording] = None
        self._t0 = 0.0
        self._playing = False
        self._play_thread: Optional[threading.Thread] = None
        self._stop_play = threading.Event()

        self.on_state: List[Callable[[str, str], None]] = []
        self.on_progress: List[Callable[[float, float], None]] = []

    def _state(self, state: str, detail: str = "") -> None:
        for handler in list(self.on_state):
            try:
                handler(state, detail)
            except Exception:
                pass

    # -- recording -------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self.current is not None

    @property
    def playing(self) -> bool:
        return self._playing

    def start(self, name: str = "") -> None:
        self.current = Recording(name=name or time.strftime("run_%Y%m%d_%H%M%S"))
        self._t0 = time.monotonic()
        self.odometry.reset()
        self._state("recording", self.current.name)

    def capture(self, left: float, right: float, dt: float,
                source: str = "") -> None:
        """Called from the Pilot for every command it emits."""
        if self.current is None:
            return
        self.current.segments.append(
            Segment(t=time.monotonic() - self._t0, dt=dt, left=round(left, 4),
                    right=round(right, 4), source=source))

    def mark(self, label: str, kind: str = "note") -> None:
        if self.current is None:
            return
        pose = self.odometry.pose
        self.current.markers.append(
            Marker(t=time.monotonic() - self._t0, x=pose.x, y=pose.y,
                   label=label, kind=kind))

    def finish(self, save: bool = True) -> Optional[str]:
        if self.current is None:
            return None
        recording, self.current = self.current, None
        path = None
        if save and recording.segments:
            path = self.save(recording)
        self._state("idle", path or "discarded")
        return path

    # -- persistence -----------------------------------------------------
    def save(self, recording: Recording) -> str:
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, f"{recording.name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(recording.to_dict(), fh, indent=1)
        return path

    def load(self, path: str) -> Recording:
        with open(path, "r", encoding="utf-8") as fh:
            return Recording.from_dict(json.load(fh))

    def list_saved(self) -> List[str]:
        if not os.path.isdir(self.directory):
            return []
        return sorted(os.path.join(self.directory, f)
                      for f in os.listdir(self.directory) if f.endswith(".json"))

    # -- playback --------------------------------------------------------
    def play(self, recording: Recording,
             submit: Callable[[DriveIntent], None],
             speed: float = 1.0, loop: bool = False) -> None:
        self.stop_playback()
        self._stop_play.clear()
        self._playing = True
        self._play_thread = threading.Thread(
            target=self._play_loop, args=(recording, submit, speed, loop),
            daemon=True, name="path-playback")
        self._play_thread.start()
        self._state("playing", recording.name)

    def stop_playback(self) -> None:
        self._stop_play.set()
        if self._play_thread:
            self._play_thread.join(timeout=2.0)
        self._play_thread = None
        self._playing = False

    def _play_loop(self, recording: Recording,
                   submit: Callable[[DriveIntent], None],
                   speed: float, loop: bool) -> None:
        rate = max(0.1, speed)
        total = recording.duration
        while not self._stop_play.is_set():
            start = time.monotonic()
            for segment in recording.segments:
                if self._stop_play.is_set():
                    break
                delay = (start + segment.t / rate) - time.monotonic()
                if delay > 0 and self._stop_play.wait(delay):
                    break
                throttle = (segment.left + segment.right) / 2.0
                steer = (segment.left - segment.right) / 2.0
                submit(DriveIntent(throttle=throttle, steer=steer,
                                   source="playback"))
                for handler in list(self.on_progress):
                    try:
                        handler(segment.t, total)
                    except Exception:
                        pass
            submit(DriveIntent(0.0, 0.0, source="playback", stop=True))
            if not loop:
                break
        self._playing = False
        self._state("idle", "playback finished")


def _wrap(angle: float) -> float:
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle < -math.pi:
        angle += 2 * math.pi
    return angle
