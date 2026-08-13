"""Gamepad / joystick input via pygame.

Runs on its own thread and pushes DriveIntents at a fixed rate. Handles
hot-plug, because controllers get unplugged mid-drive and the car should
stop rather than continue on the last stick reading.

Default mapping (Xbox-style layout, remappable in the Input panel):

  left stick   throttle and steering
  A            horn / LED pulse       B    emergency stop
  X            toggle path recording  Y    centre the camera servos
  LB / RB      speed scale down / up
  D-pad        nudge at half speed when the sticks are idle
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

from ..config import DriveConfig
from ..vision.base import DriveIntent

DEFAULT_MAP: Dict[str, int] = {
    "axis_steer": 0,
    "axis_throttle": 1,
    "button_horn": 0,
    "button_estop": 1,
    "button_record": 2,
    "button_servo": 3,
    "button_slower": 4,
    "button_faster": 5,
}


class GamepadReader:
    def __init__(self, config: DriveConfig, rate: float = 30.0):
        self.config = config
        self.rate = rate
        self.mapping = dict(DEFAULT_MAP)
        self.invert_throttle = True      # sticks report up as negative
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._joystick = None
        self._name = ""

        self.on_intent: List[Callable[[DriveIntent], None]] = []
        self.on_button: List[Callable[[str], None]] = []
        self.on_status: List[Callable[[str, str], None]] = []

    @property
    def device_name(self) -> str:
        return self._name

    @property
    def connected(self) -> bool:
        return self._joystick is not None

    def availability(self) -> str:
        try:
            import pygame  # noqa: F401
            return ""
        except Exception:
            return "Requires `pip install pygame`"

    def _status(self, state: str, detail: str = "") -> None:
        for handler in list(self.on_status):
            try:
                handler(state, detail)
            except Exception:
                pass

    def _fire(self, action: str) -> None:
        for handler in list(self.on_button):
            try:
                handler(action)
            except Exception:
                pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="gamepad")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None

    def _run(self) -> None:
        try:
            import pygame
        except Exception as exc:
            self._status("error", f"pygame unavailable: {exc}")
            return

        pygame.init()
        pygame.joystick.init()
        period = 1.0 / self.rate
        button_state: Dict[int, bool] = {}

        while not self._stop.is_set():
            pygame.event.pump()

            if pygame.joystick.get_count() == 0:
                if self._joystick is not None:
                    self._joystick = None
                    self._name = ""
                    self._status("disconnected", "controller removed")
                    self._emit(DriveIntent(0.0, 0.0, source="gamepad", stop=True))
                pygame.joystick.quit()
                pygame.joystick.init()
                self._stop.wait(1.0)
                continue

            if self._joystick is None:
                self._joystick = pygame.joystick.Joystick(0)
                self._joystick.init()
                self._name = self._joystick.get_name()
                self._status("connected", self._name)

            try:
                steer = self._axis(self.mapping["axis_steer"])
                throttle = self._axis(self.mapping["axis_throttle"])
            except Exception as exc:
                self._status("error", str(exc))
                self._joystick = None
                continue

            if self.invert_throttle:
                throttle = -throttle
            deadzone = self.config.stick_deadzone
            throttle = 0.0 if abs(throttle) < deadzone else throttle
            steer = 0.0 if abs(steer) < deadzone else steer

            if throttle == 0.0 and steer == 0.0 and self._joystick.get_numhats():
                hx, hy = self._joystick.get_hat(0)
                throttle, steer = hy * 0.5, hx * 0.5

            self._emit(DriveIntent(throttle, steer, source="gamepad"))

            for action, index in self.mapping.items():
                if not action.startswith("button_"):
                    continue
                try:
                    pressed = bool(self._joystick.get_button(index))
                except Exception:
                    continue
                if pressed and not button_state.get(index, False):
                    self._fire(action[len("button_"):])
                button_state[index] = pressed

            self._stop.wait(period)

        pygame.joystick.quit()
        pygame.quit()

    def _axis(self, index: int) -> float:
        if self._joystick is None or index >= self._joystick.get_numaxes():
            return 0.0
        return float(self._joystick.get_axis(index))

    def _emit(self, intent: DriveIntent) -> None:
        for handler in list(self.on_intent):
            try:
                handler(intent)
            except Exception:
                pass
