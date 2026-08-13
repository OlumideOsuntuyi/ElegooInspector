#!/usr/bin/env python3
"""ElegooInspector entry point.

    python main.py           connect to the car at 192.168.4.1
    python main.py --demo    run the whole UI with no hardware attached
"""

from __future__ import annotations

import argparse
import sys

from PyQt6.QtWidgets import QApplication

from elegoo_inspector import APP_NAME, __version__
from elegoo_inspector.config import AppConfig
from elegoo_inspector.ui import theme
from elegoo_inspector.ui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {__version__}")
    parser.add_argument("--demo", action="store_true",
                        help="synthetic camera and simulated link; no hardware")
    parser.add_argument("--host", help="override the car's IP address")
    parser.add_argument("--stream", help="override the MJPEG stream URL")
    args = parser.parse_args()

    config = AppConfig.load()
    if args.host:
        config.network.host = args.host
        config.network.stream_url = f"http://{args.host}:81/stream"
        config.network.test_page_url = f"http://{args.host}/Test"
    if args.stream:
        config.network.stream_url = args.stream

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(theme.STYLESHEET)

    window = MainWindow(config, demo=args.demo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
