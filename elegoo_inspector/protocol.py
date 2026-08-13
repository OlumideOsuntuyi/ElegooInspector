"""ELEGOO Smart Robot Car V4.0 command protocol.

The ESP32 camera module accepts brace-delimited frames on TCP :100 and
forwards them to the UNO over Serial2. The UNO deserialises them with
ArduinoJson and dispatches on the "N" field. Responses come back the same
way, so everything here is symmetric: build a frame, write it, read braces.

The opcode table is reconstructed from ELEGOO's shipped
ApplicationFunctionSet_xxx0.cpp. High-confidence entries are marked. If a
command does nothing on your kit, probe it in the Raw Console panel rather
than assuming the app is broken -- ELEGOO has shipped several firmware
revisions and they are not identical.
"""

from __future__ import annotations

import itertools
import json
import re
from typing import Any, Dict, Optional

# --- Opcodes -----------------------------------------------------------
N_MOTOR = 1          # confident: {"N":1,"D1":side,"D2":speed,"D3":dir}
N_MOTOR_PAIR = 2     # varies across revisions
N_DIRECTION = 3      # confident: {"N":3,"D1":1..4,"D2":speed}
N_WHEELS = 4         # confident: {"N":4,"D1":left,"D2":right}
N_SERVO = 5          # confident: {"N":5,"D1":servo,"D2":angle}
N_LED = 7            # varies by revision; exposed but unverified
N_ULTRASONIC = 21    # confident: distance read
N_LINE_TRACK = 22    # confident: IR line sensors
N_OFF_GROUND = 23    # confident: returns {<H>_true} / {<H>_false}
N_CLEAR = 100        # confident: stop everything, replies {ok}
N_MODE = 101         # confident: 1=line track, 2=obstacle avoid, 3=follow
N_ROCKER = 102       # confident: {"N":102,"D1":dir,"D2":speed}

DIR_LEFT, DIR_RIGHT, DIR_FORWARD, DIR_BACK, DIR_STOP = 1, 2, 3, 4, 9

MODE_LINE_TRACK, MODE_OBSTACLE, MODE_FOLLOW = 1, 2, 3
MODE_NAMES = {
    MODE_LINE_TRACK: "Line tracking",
    MODE_OBSTACLE: "Obstacle avoidance",
    MODE_FOLLOW: "Follow",
}

HEARTBEAT = "{Heartbeat}"

_counter = itertools.count(1)
_FRAME_RE = re.compile(rb"\{[^{}]*\}")


def next_tag() -> str:
    """Sequence tag echoed back in responses so replies can be correlated."""
    return str(next(_counter) % 100000)


def frame(n: int, tag: Optional[str] = None, **fields: Any) -> bytes:
    """Build one JSON command frame ready for the wire."""
    payload: Dict[str, Any] = {"H": tag or next_tag(), "N": int(n)}
    for key, value in fields.items():
        if value is None:
            continue
        payload[key.upper()] = int(value) if isinstance(value, bool) else value
    return json.dumps(payload, separators=(",", ":")).encode("ascii")


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def speed_byte(value: float, minimum: int, maximum: int) -> int:
    """Map a 0..1 magnitude onto the usable PWM band, or 0 for idle."""
    magnitude = abs(float(value))
    if magnitude < 1e-3:
        return 0
    return int(round(minimum + (maximum - minimum) * clamp(magnitude, 0.0, 1.0)))


# --- Convenience builders ---------------------------------------------

def motor(side: int, speed: int, direction: int) -> bytes:
    return frame(N_MOTOR, d1=side, d2=int(speed), d3=direction)


def wheels(left: int, right: int) -> bytes:
    return frame(N_WHEELS, d1=int(left), d2=int(right))


def direction(code: int, speed: int) -> bytes:
    return frame(N_DIRECTION, d1=code, d2=int(speed))


def rocker(code: int, speed: int) -> bytes:
    return frame(N_ROCKER, d1=code, d2=int(speed))


def servo(index: int, angle: int) -> bytes:
    return frame(N_SERVO, d1=int(index), d2=int(clamp(angle, 0, 180)))


def stop() -> bytes:
    return frame(N_CLEAR)


def set_mode(mode: int) -> bytes:
    return frame(N_MODE, d1=int(mode))


def read_ultrasonic() -> bytes:
    return frame(N_ULTRASONIC, d1=2)


def read_line_sensors() -> bytes:
    return frame(N_LINE_TRACK, d1=0)


def read_off_ground() -> bytes:
    return frame(N_OFF_GROUND, d1=0)


def rgb(red: int, green: int, blue: int) -> bytes:
    return frame(N_LED, d1=int(red), d2=int(green), d3=int(blue))


# --- Response parsing --------------------------------------------------

def extract_frames(buffer: bytes):
    """Split a receive buffer into complete brace-delimited frames.

    Returns (frames, remainder). The firmware sends no newlines, so brace
    matching is the only framing available.
    """
    frames = []
    last_end = 0
    for match in _FRAME_RE.finditer(buffer):
        frames.append(match.group(0))
        last_end = match.end()
    return frames, buffer[last_end:]


def decode_response(raw: bytes) -> Dict[str, Any]:
    """Best-effort interpretation of one response frame.

    Replies come in two shapes: strict JSON, or the terse {<tag>_ok} /
    {<tag>_true} acknowledgement format. Handle both, never raise.
    """
    text = raw.decode("ascii", errors="replace").strip()
    inner = text[1:-1] if text.startswith("{") and text.endswith("}") else text
    result: Dict[str, Any] = {"raw": text}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            result.update(parsed)
            result["kind"] = "json"
            return result
    except ValueError:
        pass
    if "_" in inner:
        tag, _, value = inner.partition("_")
        result.update({"kind": "ack", "tag": tag, "value": value})
        if value.lstrip("-").isdigit():
            result["number"] = int(value)
        elif value in ("true", "false"):
            result["flag"] = value == "true"
    else:
        result.update({"kind": "text", "value": inner})
    return result
