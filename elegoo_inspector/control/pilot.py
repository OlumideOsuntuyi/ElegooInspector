"""Input arbitration and command generation.

Several things want to drive the car at once: the on-screen pad, the
keyboard, a gamepad, your hand, a vision plugin, and a recording being
played back. The Pilot is the single place where that is resolved, so
exactly one component owns the motors at any instant and there is exactly
one watchdog.

Rules:

* The highest-priority source with a fresh intent wins outright. Nothing is
  blended -- blending inputs from a human and a tracker produces behaviour
  neither of them asked for.
* An intent goes stale after `watchdog_timeout`. When the winner goes stale
  and nothing else is live, a stop goes out. Releasing the stick, alt-tabbing
  or crashing the UI therefore stops the car instead of latching it.
* The emergency stop bypasses the outbound queue and latches until cleared.
* While a firmware autonomy mode (N=101) is engaged the Pilot stays quiet so
  it does not fight the UNO's own control loop.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .. import protocol
from ..config import DriveConfig
from ..transport import Transport
from ..vision.base import DriveIntent

PRIORITY: Dict[str, int] = {
    "estop": 1000,
    "ui": 80,
    "keyboard": 78,
    "gamepad": 70,
    "gesture": 60,
    "playback": 40,
    "colour-tracker": 20,
    "object-follow": 20,
    "plugin": 10,
}
DEFAULT_PRIORITY = 15


@dataclass
class DriveState:
    throttle: float = 0.0
    steer: float = 0.0
    left: float = 0.0
    right: float = 0.0
    source: str = "idle"
    moving: bool = False


class Pilot:
    def __init__(self, transport: Transport, config: DriveConfig):
        self.transport = transport
        self.config = config
        self._intents: Dict[str, tuple] = {}     # source -> (intent, stamp)
        self._lock = threading.Lock()
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._estop = False
        self._last_wire: Optional[bytes] = None
        self._state = DriveState()
        self._speed_scale = config.default_speed_pct / 100.0
        self.firmware_mode: Optional[int] = None

        self.on_state: List[Callable[[DriveState], None]] = []
        self.on_command: List[Callable[[float, float, float], None]] = []
        self.on_log: List[Callable[[str, str], None]] = []

    # -- observers -------------------------------------------------------
    def _log(self, message: str, level: str = "info") -> None:
        for handler in list(self.on_log):
            try:
                handler(message, level)
            except Exception:
                pass

    def _emit_state(self) -> None:
        for handler in list(self.on_state):
            try:
                handler(self._state)
            except Exception:
                pass

    # -- public API ------------------------------------------------------
    @property
    def state(self) -> DriveState:
        return self._state

    @property
    def estopped(self) -> bool:
        return self._estop

    @property
    def speed_scale(self) -> float:
        return self._speed_scale

    def set_speed_scale(self, percent: float) -> None:
        self._speed_scale = max(0.05, min(1.0, percent / 100.0))

    def submit(self, intent: DriveIntent) -> None:
        """Register an intent from a named source."""
        if self._estop and intent.source != "estop":
            return
        with self._lock:
            self._intents[intent.source] = (intent, time.monotonic())

    def release(self, source: str) -> None:
        with self._lock:
            self._intents.pop(source, None)

    def emergency_stop(self) -> None:
        self._estop = True
        with self._lock:
            self._intents.clear()
        self.transport.drain_outbox()
        self.transport.send_now(protocol.stop())
        self.transport.send_now(protocol.stop())
        self._last_wire = None
        self._state = DriveState(source="E-STOP")
        self._emit_state()
        self._log("EMERGENCY STOP latched", "error")

    def clear_estop(self) -> None:
        self._estop = False
        self._log("Emergency stop cleared", "good")

    def halt(self) -> None:
        """Ordinary stop, no latch."""
        with self._lock:
            self._intents.clear()
        self.transport.send(protocol.stop())
        self._last_wire = None
        self._state = DriveState()
        self._emit_state()

    # -- passthrough commands -------------------------------------------
    def servo(self, index: int, angle: int) -> None:
        self.transport.send(protocol.servo(index, angle))

    def set_firmware_mode(self, mode: Optional[int]) -> None:
        """Engage or leave one of the UNO's built-in autonomy modes."""
        if mode is None:
            self.firmware_mode = None
            self.transport.send(protocol.stop())
            self._log("Returned to manual control", "info")
            return
        self.halt()
        self.firmware_mode = mode
        self.transport.send(protocol.set_mode(mode))
        self._log(f"Firmware mode: {protocol.MODE_NAMES.get(mode, mode)}", "info")

    def raw(self, payload: bytes) -> None:
        self.transport.send(payload)

    # -- thread ----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="pilot")
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None

    def _winner(self) -> Optional[DriveIntent]:
        now = time.monotonic()
        best: Optional[DriveIntent] = None
        best_rank = -1
        stale = []
        with self._lock:
            for source, (intent, stamp) in self._intents.items():
                if now - stamp > self.config.watchdog_timeout:
                    stale.append(source)
                    continue
                rank = PRIORITY.get(source, DEFAULT_PRIORITY)
                if rank > best_rank:
                    best, best_rank = intent, rank
            for source in stale:
                self._intents.pop(source, None)
        return best

    def _run(self) -> None:
        period = 1.0 / max(1.0, self.config.command_rate)
        last_tick = time.monotonic()
        while not self._stop_flag.is_set():
            self._stop_flag.wait(period)
            now = time.monotonic()
            dt, last_tick = now - last_tick, now

            if self._estop or self.firmware_mode is not None:
                continue

            intent = self._winner()
            if intent is None or intent.stop:
                if self._last_wire is not None:
                    self.transport.send(protocol.stop())
                    self._last_wire = None
                    self._state = DriveState()
                    self._emit_state()
                continue
            self._drive(intent, dt)

    # -- mixing ----------------------------------------------------------
    def _drive(self, intent: DriveIntent, dt: float) -> None:
        cfg = self.config
        throttle = _clamp(intent.throttle, -1.0, 1.0) * self._speed_scale
        steer = _clamp(intent.steer, -1.0, 1.0) * cfg.steer_gain

        if abs(throttle) < cfg.stick_deadzone and abs(steer) < cfg.stick_deadzone:
            if self._last_wire is not None:
                self.transport.send(protocol.stop())
                self._last_wire = None
                self._state = DriveState(source=intent.source)
                self._emit_state()
            return

        # Differential mix; steer with no throttle spins in place.
        left = throttle + steer
        right = throttle - steer
        peak = max(1.0, abs(left), abs(right))
        left, right = left / peak, right / peak

        payload = self._encode(left, right)
        if payload != self._last_wire:
            self.transport.send(payload)
            self._last_wire = payload

        self._state = DriveState(throttle=throttle, steer=steer, left=left,
                                 right=right, source=intent.source, moving=True)
        self._emit_state()
        for handler in list(self.on_command):
            try:
                handler(left, right, dt)
            except Exception:
                pass

    def _encode(self, left: float, right: float) -> bytes:
        cfg = self.config
        low, high = cfg.min_speed, cfg.max_speed
        strategy = cfg.strategy

        if strategy == "n4_pair":
            # Forward-only on most builds; reverse falls back to N=3.
            if left >= 0 and right >= 0:
                return protocol.wheels(protocol.speed_byte(left, low, high),
                                       protocol.speed_byte(right, low, high))
            strategy = "n3_simple"

        if strategy == "n3_simple":
            magnitude = max(abs(left), abs(right))
            speed = protocol.speed_byte(magnitude, low, high)
            if abs(left - right) > 0.35:
                code = protocol.DIR_RIGHT if left > right else protocol.DIR_LEFT
            else:
                code = protocol.DIR_FORWARD if (left + right) >= 0 \
                    else protocol.DIR_BACK
            return protocol.direction(code, speed)

        # Default: per-side N=1 with an explicit direction byte. The two
        # frames are concatenated so both sides change on one wire write.
        left_dir = cfg.dir_forward if left >= 0 else cfg.dir_backward
        right_dir = cfg.dir_forward if right >= 0 else cfg.dir_backward
        return (protocol.motor(cfg.side_left,
                               protocol.speed_byte(left, low, high), left_dir)
                + protocol.motor(cfg.side_right,
                                 protocol.speed_byte(right, low, high),
                                 right_dir))


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value
