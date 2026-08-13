"""Central configuration.

Everything the stock ELEGOO firmware might disagree with you about lives
here. Kit revisions ship different motor drivers and slightly different
firmware builds, so treat these as starting points and verify them in the
Raw Console panel before trusting them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

CONFIG_PATH = os.path.expanduser("~/.elegoo_inspector.json")


@dataclass
class NetworkConfig:
    host: str = "192.168.4.1"
    control_port: int = 100
    # ELEGOO increments the http server port by one for the MJPEG handler,
    # so the stream lands on 81 while the test page is on 80.
    stream_url: str = "http://192.168.4.1:81/stream"
    test_page_url: str = "http://192.168.4.1/Test"
    connect_timeout: float = 4.0
    heartbeat_interval: float = 1.5
    reconnect_delay: float = 2.0


@dataclass
class DriveConfig:
    # Which command family to use for movement:
    #   "n1_differential" -> {"N":1,"D1":side,"D2":speed,"D3":dir}  (default)
    #   "n4_pair"         -> {"N":4,"D1":left,"D2":right}
    #   "n3_simple"       -> {"N":3,"D1":direction,"D2":speed}
    strategy: str = "n1_differential"

    # Motors stall below roughly a third of full PWM, so any non-zero demand
    # is mapped into [min_speed, max_speed] instead of buzzing in place.
    min_speed: int = 90
    max_speed: int = 255
    default_speed_pct: int = 60

    steer_gain: float = 0.85
    stick_deadzone: float = 0.12
    command_rate: float = 15.0
    watchdog_timeout: float = 0.45

    # N=1 direction byte mapping. Flip these if your car drives backwards.
    dir_forward: int = 1
    dir_backward: int = 2
    side_left: int = 0
    side_right: int = 1
    side_both: int = 2


@dataclass
class CameraConfig:
    reconnect_delay: float = 1.5
    read_timeout: float = 6.0
    flip_horizontal: bool = False
    flip_vertical: bool = False


@dataclass
class HandConfig:
    # The robot camera points at the world, not at you, so gesture control
    # defaults to a local webcam.
    source: str = "local"          # "local" | "robot"
    local_device_index: int = 0
    span: float = 0.30


@dataclass
class OdometryConfig:
    """Dead-reckoning calibration for the path trace.

    No wheel encoders exist on this kit, so the trace is integrated from the
    commands we sent. Drive a measured two metres and tune until the trace
    agrees with reality.
    """
    metres_per_second_at_full: float = 0.55
    radians_per_second_at_full_turn: float = 2.4


@dataclass
class AppConfig:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    drive: DriveConfig = field(default_factory=DriveConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    hand: HandConfig = field(default_factory=HandConfig)
    odometry: OdometryConfig = field(default_factory=OdometryConfig)
    plugin_dirs: list = field(default_factory=lambda: ["plugins"])
    recordings_dir: str = "recordings"

    def save(self, path: str = CONFIG_PATH) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)

    @classmethod
    def load(cls, path: str = CONFIG_PATH) -> "AppConfig":
        cfg = cls()
        if not os.path.exists(path):
            return cfg
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return cfg
        for section, klass in (("network", NetworkConfig),
                               ("drive", DriveConfig),
                               ("camera", CameraConfig),
                               ("hand", HandConfig),
                               ("odometry", OdometryConfig)):
            values = raw.get(section) or {}
            known = {k: v for k, v in values.items()
                     if k in klass.__dataclass_fields__}
            setattr(cfg, section, klass(**known))
        if isinstance(raw.get("plugin_dirs"), list):
            cfg.plugin_dirs = raw["plugin_dirs"]
        if isinstance(raw.get("recordings_dir"), str):
            cfg.recordings_dir = raw["recordings_dir"]
        return cfg
