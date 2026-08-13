"""Plugin discovery and the frame-processing pipeline."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import sys
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Type

import numpy as np

from .base import CVPlugin, DriveIntent, PluginResult

BUILTIN_MODULES = [
    "elegoo_inspector.vision.builtin",
    "elegoo_inspector.vision.detection",
    "elegoo_inspector.vision.hands",
]


def discover(plugin_dirs: List[str]) -> List[Type[CVPlugin]]:
    """Return every CVPlugin subclass from builtins plus user directories."""
    found: List[Type[CVPlugin]] = []
    seen = set()

    def harvest(module) -> None:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (issubclass(obj, CVPlugin) and obj is not CVPlugin
                    and obj.__module__ == module.__name__
                    and obj.name not in seen):
                seen.add(obj.name)
                found.append(obj)

    for dotted in BUILTIN_MODULES:
        try:
            module = importlib.import_module(dotted)
            importlib.reload(module)
            harvest(module)
        except Exception:
            traceback.print_exc()

    for directory in plugin_dirs:
        path = os.path.abspath(directory)
        if not os.path.isdir(path):
            continue
        if path not in sys.path:
            sys.path.insert(0, path)
        for filename in sorted(os.listdir(path)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            module_name = f"ei_plugin_{os.path.splitext(filename)[0]}"
            spec = importlib.util.spec_from_file_location(
                module_name, os.path.join(path, filename))
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                harvest(module)
            except Exception:
                traceback.print_exc()
    return found


class Pipeline:
    """Runs enabled plugins over incoming frames on a worker thread.

    Frames are processed newest-first with a depth of one. If the chain is
    slower than the camera, intermediate frames are discarded rather than
    queued -- a backlog on a live control feed is worse than a dropped frame.
    """

    def __init__(self) -> None:
        self.plugins: List[CVPlugin] = []
        self._lock = threading.Lock()
        self._pending: Optional[np.ndarray] = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._durations: List[float] = []
        self.context: Dict[str, Any] = {}

        self.on_result: List[Callable[[np.ndarray, PluginResult], None]] = []
        self.on_intent: List[Callable[[DriveIntent], None]] = []
        self.on_error: List[Callable[[str, str], None]] = []

    # -- plugin registry -------------------------------------------------
    def add(self, plugin: CVPlugin) -> None:
        with self._lock:
            self.plugins.append(plugin)

    def remove(self, plugin: CVPlugin) -> None:
        with self._lock:
            if plugin in self.plugins:
                self.plugins.remove(plugin)
        try:
            plugin.stop()
        except Exception:
            pass

    def move(self, plugin: CVPlugin, delta: int) -> None:
        with self._lock:
            if plugin not in self.plugins:
                return
            index = self.plugins.index(plugin)
            target = max(0, min(len(self.plugins) - 1, index + delta))
            self.plugins.insert(target, self.plugins.pop(index))

    def clear(self) -> None:
        with self._lock:
            plugins, self.plugins = self.plugins, []
        for plugin in plugins:
            try:
                plugin.stop()
            except Exception:
                pass

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="vision-pipeline")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        self.clear()

    def submit(self, frame: np.ndarray) -> None:
        with self._lock:
            self._pending = frame
        self._wake.set()

    @property
    def latency_ms(self) -> float:
        if not self._durations:
            return 0.0
        return 1000.0 * sum(self._durations) / len(self._durations)

    # -- worker ----------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.1)
            self._wake.clear()
            with self._lock:
                frame, self._pending = self._pending, None
                chain = list(self.plugins)
            if frame is None:
                continue
            started = time.monotonic()
            result = self._process(frame, chain)
            self._durations.append(time.monotonic() - started)
            if len(self._durations) > 20:
                self._durations.pop(0)
            output = result.frame if result.frame is not None else frame
            for handler in list(self.on_result):
                try:
                    handler(output, result)
                except Exception:
                    pass
            if result.intent is not None:
                for handler in list(self.on_intent):
                    try:
                        handler(result.intent)
                    except Exception:
                        pass

    def _process(self, frame: np.ndarray, chain: List[CVPlugin]) -> PluginResult:
        combined = PluginResult(frame=frame)
        current = frame
        for plugin in chain:
            if not plugin.enabled:
                continue
            try:
                step = plugin.process(current, self.context)
            except Exception as exc:
                plugin.last_error = str(exc)
                plugin.enabled = False
                for handler in list(self.on_error):
                    try:
                        handler(plugin.name, f"{exc} (plugin disabled)")
                    except Exception:
                        pass
                continue
            if step is None:
                continue
            if step.frame is not None:
                current = step.frame
            combined.overlay.extend(step.overlay)
            combined.data.update({f"{plugin.name}.{k}": v
                                  for k, v in step.data.items()})
            if step.intent is not None:
                combined.intent = step.intent
        combined.frame = current
        return combined
