"""TCP transport to the ESP32 camera module on port 100.

Deliberately free of any Qt import so the core can be driven from a script,
a notebook, or a test harness. Observers register plain callables.

Two things bite everyone who writes this by hand:

1. The firmware accepts exactly one client. If the ELEGOO phone app is still
   connected your socket will open and then nothing will happen.
2. There is a heartbeat. Stop talking and the car stops moving. This class
   keeps one running on its own thread whenever the link is up.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from typing import Callable, List, Optional

from . import protocol
from .config import NetworkConfig

DISCONNECTED, CONNECTING, CONNECTED = "disconnected", "connecting", "connected"


class Transport:
    def __init__(self, config: NetworkConfig):
        self.config = config
        self._sock: Optional[socket.socket] = None
        self._state = DISCONNECTED
        self._outbox: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
        self._stop_event = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()
        self._last_tx = 0.0
        self._sent = 0
        self._received = 0
        self._auto_reconnect = True

        self.on_state: List[Callable[[str, str], None]] = []
        self.on_response: List[Callable[[dict], None]] = []
        self.on_log: List[Callable[[str, str], None]] = []

    # -- observer plumbing ---------------------------------------------
    def _emit_state(self, state: str, detail: str = "") -> None:
        self._state = state
        for handler in list(self.on_state):
            try:
                handler(state, detail)
            except Exception:
                pass

    def _emit_response(self, payload: dict) -> None:
        for handler in list(self.on_response):
            try:
                handler(payload)
            except Exception:
                pass

    def log(self, message: str, level: str = "info") -> None:
        for handler in list(self.on_log):
            try:
                handler(message, level)
            except Exception:
                pass

    # -- lifecycle ------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == CONNECTED

    @property
    def stats(self) -> dict:
        return {"sent": self._sent, "received": self._received}

    def start(self) -> None:
        if self._threads:
            return
        self._stop_event.clear()
        self._auto_reconnect = True
        for target, name in ((self._link_loop, "elegoo-link"),
                             (self._heartbeat_loop, "elegoo-heartbeat")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._auto_reconnect = False
        self._stop_event.set()
        self._close_socket()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads.clear()
        self._emit_state(DISCONNECTED, "stopped")

    def _close_socket(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    # -- sending --------------------------------------------------------
    def send(self, payload: bytes) -> bool:
        """Queue a frame. Returns False if the outbox is saturated."""
        if not self.connected:
            return False
        try:
            self._outbox.put_nowait(payload)
            return True
        except queue.Full:
            self.log("Outbox full, dropping command", "warn")
            return False

    def send_now(self, payload: bytes) -> bool:
        """Bypass the queue. Used by the emergency stop only."""
        with self._lock:
            sock = self._sock
        if sock is None:
            return False
        try:
            sock.sendall(payload)
            self._sent += 1
            self._last_tx = time.monotonic()
            return True
        except OSError as exc:
            self.log(f"Immediate send failed: {exc}", "error")
            return False

    def drain_outbox(self) -> None:
        while True:
            try:
                self._outbox.get_nowait()
            except queue.Empty:
                return

    # -- threads --------------------------------------------------------
    def _link_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._auto_reconnect:
                time.sleep(0.2)
                continue
            self._emit_state(CONNECTING,
                             f"{self.config.host}:{self.config.control_port}")
            try:
                sock = socket.create_connection(
                    (self.config.host, self.config.control_port),
                    timeout=self.config.connect_timeout)
            except OSError as exc:
                self._emit_state(DISCONNECTED, str(exc))
                self.log(f"Connect failed: {exc}. Check you are joined to the "
                         f"car's WiFi and that the ELEGOO app is closed.",
                         "error")
                self._sleep(self.config.reconnect_delay)
                continue

            # Short timeout: _pump() alternates between checking the outbox
            # and this recv. A long timeout here starves the outbox check
            # whenever the link is quiet, which shows up as the car ignoring
            # drive commands for a stretch (they sit queued until recv gives
            # up). Keep this comfortably below the drive command period.
            sock.settimeout(0.05)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            with self._lock:
                self._sock = sock
            self._emit_state(CONNECTED, self.config.host)
            self.log("Link established", "good")
            self._pump(sock)
            self._close_socket()
            self._emit_state(DISCONNECTED, "link lost")
            self._sleep(self.config.reconnect_delay)

    def _pump(self, sock: socket.socket) -> None:
        buffer = b""
        while not self._stop_event.is_set() and self._auto_reconnect:
            try:
                payload = self._outbox.get(timeout=0.01)
            except queue.Empty:
                payload = None
            if payload is not None:
                try:
                    sock.sendall(payload)
                    self._sent += 1
                    self._last_tx = time.monotonic()
                except OSError as exc:
                    self.log(f"Send failed: {exc}", "error")
                    return
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    self.log("Peer closed the connection", "warn")
                    return
                buffer += chunk
                frames, buffer = protocol.extract_frames(buffer)
                if len(buffer) > 8192:
                    buffer = b""
                for raw in frames:
                    self._received += 1
                    self._emit_response(protocol.decode_response(raw))
            except socket.timeout:
                continue
            except OSError as exc:
                self.log(f"Receive failed: {exc}", "error")
                return

    def _heartbeat_loop(self) -> None:
        beat = protocol.HEARTBEAT.encode("ascii")
        while not self._stop_event.is_set():
            interval = self.config.heartbeat_interval
            if self.connected and (time.monotonic() - self._last_tx) > interval:
                self.send(beat)
            self._sleep(0.25)

    def _sleep(self, seconds: float) -> None:
        self._stop_event.wait(seconds)


class LoopbackTransport(Transport):
    """Offline stand-in so the UI runs without hardware (--demo)."""

    def __init__(self, config: NetworkConfig):
        super().__init__(config)
        self._distance = 60.0

    def start(self) -> None:
        self._stop_event.clear()
        self._emit_state(CONNECTED, "loopback")
        self.log("Demo mode: no hardware, commands are simulated", "warn")

    def stop(self) -> None:
        self._stop_event.set()
        self._emit_state(DISCONNECTED, "loopback closed")

    def send(self, payload: bytes) -> bool:
        self._sent += 1
        text = payload.decode("ascii", errors="replace")
        if '"N":21' in text:
            self._distance = max(8.0, (self._distance * 0.9) + 6.0)
            self._emit_response({"kind": "ack", "value": str(int(self._distance)),
                                 "number": int(self._distance), "raw": text})
        return True

    def send_now(self, payload: bytes) -> bool:
        return self.send(payload)
