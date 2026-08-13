"""Template third-party plugin: crude lane / edge-line finder.

Copy this file, rename the class, and press Reload in the Vision panel.
Anything that subclasses CVPlugin and implements process() is picked up.

The three things worth noticing:

1. declare_params() defines the UI. You get sliders and checkboxes for free.
2. process() returns a PluginResult. Set `frame` to draw, `overlay` for text
   over the video, `data` for anything you want other plugins to read.
3. Returning an `intent` asks the car to move. It is the lowest-priority
   source, so any human input overrides it immediately.
"""

import cv2
import numpy as np

from elegoo_inspector.vision.base import CVPlugin, Param, PluginResult


class LaneLines(CVPlugin):
    name = "Lane lines (example)"
    description = "Hough lines in the lower half of the frame."
    category = "example"

    def declare_params(self):
        self.add_param(Param("canny_low", "Canny low", "int", 60, 0, 255, 1))
        self.add_param(Param("canny_high", "Canny high", "int", 170, 0, 255, 1))
        self.add_param(Param("threshold", "Hough threshold", "int", 55, 10, 300, 1))
        self.add_param(Param("min_length", "Min line length", "int", 40, 5, 400, 5))
        self.add_param(Param("horizon", "Horizon", "float", 0.55, 0.2, 0.9, 0.01))

    def process(self, frame, context):
        height, width = frame.shape[:2]
        top = int(height * float(self.get("horizon")))
        roi = frame[top:, :]

        grey = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(grey, (5, 5), 0)
        edges = cv2.Canny(blurred, int(self.get("canny_low")),
                          int(self.get("canny_high")))

        lines = cv2.HoughLinesP(edges, 1, np.pi / 180,
                                threshold=int(self.get("threshold")),
                                minLineLength=int(self.get("min_length")),
                                maxLineGap=25)

        canvas = frame.copy()
        cv2.line(canvas, (0, top), (width, top), (200, 200, 200), 1)
        count = 0
        if lines is not None:
            for x1, y1, x2, y2 in lines[:, 0]:
                if abs(x2 - x1) < 3:          # skip perfectly vertical noise
                    continue
                count += 1
                cv2.line(canvas, (x1, y1 + top), (x2, y2 + top),
                         (203, 74, 43), 2)

        return PluginResult(frame=canvas,
                            overlay=[f"lane segments: {count}"],
                            data={"segments": count})
