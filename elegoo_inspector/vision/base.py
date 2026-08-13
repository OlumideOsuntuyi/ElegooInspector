"""Plugin API for camera-feed processors.

A plugin is any class subclassing CVPlugin. Drop a .py file into ./plugins
and it is discovered at launch, or via Reload in the Vision panel. Plugins
run as a chain in list order; each receives the previous one's output frame,
so a detector and an annotator compose naturally.

A plugin may also request control of the car by returning a DriveIntent. The
Pilot treats that as the lowest-priority source, so any human input overrides
it instantly and the emergency stop always wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np


@dataclass
class Param:
    """Declarative control description; the UI builds widgets from these."""
    key: str
    label: str
    kind: str = "float"          # "float" | "int" | "bool" | "choice"
    value: Any = 0.0
    minimum: float = 0.0
    maximum: float = 1.0
    step: float = 0.01
    choices: List[str] = field(default_factory=list)
    help: str = ""


@dataclass
class DriveIntent:
    """Normalised movement request. throttle/steer are -1..1."""
    throttle: float = 0.0
    steer: float = 0.0
    source: str = "plugin"
    stop: bool = False


@dataclass
class PluginResult:
    frame: Optional[np.ndarray] = None
    overlay: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    intent: Optional[DriveIntent] = None


class CVPlugin:
    """Subclass this. Only `process` is mandatory."""

    name: str = "Unnamed plugin"
    description: str = ""
    category: str = "general"
    provides_control: bool = False
    # Set False if the plugin should never run on the robot's own feed
    # (gesture control normally watches a local webcam instead).
    runs_on_robot_feed: bool = True

    def __init__(self) -> None:
        self._params: Dict[str, Param] = {}
        self.enabled = True
        self.last_error: str = ""
        self.declare_params()

    # -- parameters -----------------------------------------------------
    def declare_params(self) -> None:
        """Override and call add_param() for each tunable."""

    def add_param(self, param: Param) -> None:
        self._params[param.key] = param

    def params(self) -> List[Param]:
        return list(self._params.values())

    def get(self, key: str, default: Any = None) -> Any:
        param = self._params.get(key)
        return default if param is None else param.value

    def set(self, key: str, value: Any) -> None:
        if key in self._params:
            self._params[key].value = value
            self.on_param_changed(key, value)

    def on_param_changed(self, key: str, value: Any) -> None:
        """Hook for plugins that rebuild state when tuned."""

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        """Load models, open files. Raise to signal unavailability."""

    def stop(self) -> None:
        """Release anything expensive."""

    def process(self, frame: np.ndarray, context: Dict[str, Any]) -> PluginResult:
        raise NotImplementedError

    # -- helpers --------------------------------------------------------
    def availability(self) -> str:
        """Return '' when usable, or a human explanation of what's missing."""
        return ""


PluginFactory = Callable[[], CVPlugin]
