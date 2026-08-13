# ElegooInspector

A desktop controller for the **ELEGOO Smart Robot Car V4.0 (camera edition)**
that talks to the kit exactly as it ships. No firmware flashing, no wiring
changes, no Arduino IDE.

Live camera feed, pluggable computer vision, gamepad and finger-pose control,
path recording and playback, and a raw console for poking at the protocol.

---

## Quick start

```bash
pip install -r requirements.txt
python main.py --demo      # try the whole UI with no hardware
python main.py             # connect to the real car
```

**Before running against hardware:** power on the car, then join the WiFi
access point it broadcasts (`ELEGOO-…`) from your computer. The car is its
own network — you will lose internet access while connected. Close the ELEGOO
phone app first; the firmware accepts exactly one TCP client and the app will
silently hold the slot.

Defaults: control on `192.168.4.1:100`, video on `http://192.168.4.1:81/stream`.

---

## How it talks to the car

The ESP32 camera module accepts brace-delimited JSON on TCP port 100 and
forwards it to the UNO over serial. The UNO dispatches on the `"N"` field.

```json
{"H":"12","N":1,"D1":0,"D2":180,"D3":1}
```

A heartbeat keeps the link alive; stop talking and the car stops. That runs on
its own thread whenever the connection is up.

### The part you should verify yourself

ELEGOO has shipped several firmware revisions with different motor drivers,
and the opcode tables are not identical between them. The commands here are
reconstructed from the shipped sketch and are correct for the common builds,
but I would rather tell you that than have you assume the app is broken.

If the car does not move:

1. Open the **Console** tab and send `{"H":"1","N":3,"D1":3,"D2":150}` — that
   is "forward at 150" in the simplest command family.
2. If that works but normal driving does not, switch **Settings → Command
   family** to `n3_simple`.
3. If it drives backwards, tick **Swap forward and reverse**.
4. If nothing at all responds, the link is the problem, not the opcodes —
   check the WiFi and that the phone app is closed.

Three command families are selectable: per-side `N=1` (default), wheel-pair
`N=4`, whole-car `N=3`.

---

## Features

**Camera** — MJPEG parsed off a raw socket with a latest-frame-wins policy.
`cv2.VideoCapture` buffers, which means steering from a stale picture; this
does not, including under load — if decoding falls behind, the reader skips
straight to the newest complete frame instead of working through a backlog.
Switch between the robot camera, a local webcam, a video file, or off
entirely, in the Vision tab's Feed selector.

**Vision plugins** — processors run as an ordered chain, each receiving the
previous one's output frame. Built in: crosshair overlay, edge view, motion
detection, HSV colour tracking, object detection, video writer.

Object detection tries `ultralytics` (YOLOv8), then OpenCV DNN with
MobileNet-SSD from `./models`, then Haar cascades, falling through to the
next automatically if one fails. Whatever you have installed is what it
uses; the panel tells you which. `ultralytics` fetches its weights from the
internet the first time it runs — do that once before joining the car's own
WiFi, which has none, or it will fall back to the next backend for that
session.

**Loading your own CV code** — drop a `.py` file into `./plugins`, press
**Reload**. Subclass `CVPlugin`, declare parameters, and the UI builds sliders
and checkboxes for them automatically. See `plugins/example_lane_lines.py`.

**Gamepad** — pygame, with hot-plug handling: unplug mid-drive and the car
stops rather than continuing on the last stick reading. Axes and buttons are
remappable in the Input panel, and the button hint there switches between
Xbox-style (A/B/X/Y) and PlayStation-style (Cross/Circle/Square/Triangle)
labels automatically once a controller is detected.

**Finger-pose control** — MediaPipe `HandLandmarker` hand tracking. Open palm
arms and steers, fist stops, and losing the hand stops. It runs on a
*second* camera (your laptop webcam) shown in its own preview, because the
robot's camera faces forward at the world, not at you. Add it from the Input
tab, then find it in the Vision tab's Active chain (marked "gesture cam") to
preview it and tick **Send drive commands** once you're happy. Like object
detection, the model is fetched once from the internet on first use and
cached in `./models` from then on — do that before joining the car's WiFi.

**Path recording** — records the commands issued, replays them with timing,
and draws a top-down trace. Playback goes through the same arbitration as
everything else, so the emergency stop still works during replay.

**Input arbitration** — the on-screen pad, keyboard, gamepad, hand, vision
plugins and playback all want the motors. One component owns them at a time:
highest priority with a fresh command wins outright, and nothing is blended.
A stale command triggers a stop, so releasing a key, alt-tabbing, or crashing
the UI stops the car instead of latching the last instruction.

---

## Honest limitations

**The path trace is not odometry.** This kit has no wheel encoders. The trace
is integrated from the commands *sent*, so wheel slip, carpet, a fading
battery and a hand on the chassis all cause drift. Calibrate in Settings by
driving a measured distance. Treat it as the shape of a run, not a map.

**Playback repeats commands, not positions.** A replayed run diverges from the
original for the same reason. It is useful for repeatable demos on a smooth
floor and not much more.

**This is teleoperation.** The UNO still runs ELEGOO's firmware. All the
intelligence lives on your computer, and every decision makes a WiFi round
trip. Latency is tens of milliseconds on a good link and much worse on a bad
one. Do not drive it near stairs.

**LED commands (`N=7`) are unverified** across revisions and may do nothing.

---

## Keyboard

```
W A S D / arrows   drive          Q / E   spin in place
Space              stop           Esc     emergency stop (latching)
R                  toggle path recording
[ / ]              speed down / up
```

---

## Layout

```
main.py                       entry point
elegoo_inspector/
  protocol.py                 opcodes, frame building, response parsing
  transport.py                TCP link, heartbeat, reconnect
  camera.py                   MJPEG reader, webcam, synthetic demo source
  recorder.py                 path recording, dead reckoning, playback
  config.py                   every tunable, saved to ~/.elegoo_inspector.json
  control/pilot.py            input arbitration, differential mixing, watchdog
  control/gamepad.py          joystick reader
  vision/base.py              the plugin API you subclass
  vision/loader.py            discovery and the processing pipeline
  vision/builtin.py           overlay, edges, motion, colour tracking
  vision/detection.py         object detection with three backends
  vision/hands.py             finger-pose control
  ui/                         theme, widgets, panels, main window
plugins/                      your CV code goes here
```

---

## Writing a plugin

```python
from elegoo_inspector.vision.base import CVPlugin, Param, PluginResult

class Threshold(CVPlugin):
    name = "Threshold"
    description = "Binary threshold."

    def declare_params(self):
        self.add_param(Param("level", "Level", "int", 128, 0, 255, 1))

    def process(self, frame, context):
        import cv2
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, out = cv2.threshold(grey, int(self.get("level")), 255,
                               cv2.THRESH_BINARY)
        return PluginResult(frame=cv2.cvtColor(out, cv2.COLOR_GRAY2BGR),
                            overlay=["thresholded"])
```

Return a `DriveIntent` from `process()` to steer the car. Raise from
`start()`, or return a string from `availability()`, to report a missing
dependency instead of crashing the app.
