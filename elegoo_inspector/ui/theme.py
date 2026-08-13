"""Visual language: white surfaces, royal blue accent, soft depth.

One accent colour doing all the work, generous radii, and shadow used to
separate layers rather than to decorate. Red is reserved exclusively for
stop states so it never competes for attention. It should read like a decent
piece of desktop software, not a spaceship.
"""

from __future__ import annotations

# --- Palette ------------------------------------------------------------
BLUE = "#2B4ACB"          # royal blue, primary accent
BLUE_HOVER = "#3A59DC"
BLUE_PRESSED = "#1F3AA8"
BLUE_SOFT = "#EAEEFC"
BLUE_EDGE = "#C3CDF5"

WHITE = "#FFFFFF"
CANVAS = "#F5F6FA"
SURFACE = "#FFFFFF"
BORDER = "#E2E5EE"
BORDER_STRONG = "#CDD2E0"

INK = "#1B1F2A"
INK_MUTED = "#5A6273"
INK_FAINT = "#8B93A5"

GREEN = "#1E9E62"
AMBER = "#D08700"
RED = "#D33A38"
RED_HOVER = "#E24845"

RADIUS = 12
RADIUS_SMALL = 8

STYLESHEET = f"""
QWidget {{
    background: {CANVAS};
    color: {INK};
    font-family: "Inter", "Segoe UI", "SF Pro Text", "Ubuntu", sans-serif;
    font-size: 13px;
}}

QToolTip {{
    background: {INK}; color: {WHITE}; border: none;
    border-radius: 6px; padding: 6px 9px;
}}

QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}
QLabel#CardTitle {{
    font-size: 12px; font-weight: 600; color: {INK_MUTED};
    letter-spacing: 0.6px; background: transparent;
}}
QLabel#Hint {{ color: {INK_FAINT}; font-size: 11px; background: transparent; }}
QLabel#Value {{
    font-size: 20px; font-weight: 600; color: {INK}; background: transparent;
}}
QLabel {{ background: transparent; }}

QPushButton {{
    background: {WHITE}; color: {INK};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SMALL}px;
    padding: 8px 14px; font-weight: 500;
}}
QPushButton:hover {{ border-color: {BLUE}; color: {BLUE}; }}
QPushButton:pressed {{ background: {BLUE_SOFT}; }}
QPushButton:disabled {{
    color: {INK_FAINT}; border-color: {BORDER}; background: {CANVAS};
}}
QPushButton:checked {{
    background: {BLUE_SOFT}; border-color: {BLUE};
    color: {BLUE}; font-weight: 600;
}}

QPushButton#Primary {{
    background: {BLUE}; color: {WHITE}; border: none;
    padding: 9px 18px; font-weight: 600;
}}
QPushButton#Primary:hover   {{ background: {BLUE_HOVER}; }}
QPushButton#Primary:pressed {{ background: {BLUE_PRESSED}; }}
QPushButton#Primary:disabled {{ background: {BORDER_STRONG}; color: {WHITE}; }}

QPushButton#Danger {{
    background: {RED}; color: {WHITE}; border: none;
    font-weight: 700; letter-spacing: 0.5px;
    border-radius: {RADIUS}px; padding: 11px 20px;
}}
QPushButton#Danger:hover {{ background: {RED_HOVER}; }}

QPushButton#Ghost {{
    background: transparent; border: none;
    color: {INK_MUTED}; padding: 5px 9px;
}}
QPushButton#Ghost:hover {{ color: {BLUE}; }}

QPushButton#Pad {{
    background: {WHITE}; border: 1px solid {BORDER_STRONG};
    border-radius: 14px; font-size: 18px; font-weight: 600;
    color: {INK_MUTED};
}}
QPushButton#Pad:hover   {{ border-color: {BLUE}; color: {BLUE}; }}
QPushButton#Pad:pressed {{ background: {BLUE}; color: {WHITE}; border-color: {BLUE}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {WHITE}; border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SMALL}px; padding: 6px 10px;
    selection-background-color: {BLUE}; selection-color: {WHITE};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus {{ border-color: {BLUE}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {WHITE}; border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SMALL}px;
    selection-background-color: {BLUE_SOFT}; selection-color: {INK};
    padding: 4px;
}}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 17px; height: 17px; border: 1px solid {BORDER_STRONG};
    border-radius: 5px; background: {WHITE};
}}
QCheckBox::indicator:hover   {{ border-color: {BLUE}; }}
QCheckBox::indicator:checked {{ background: {BLUE}; border-color: {BLUE}; }}

QSlider::groove:horizontal {{
    height: 5px; background: {BORDER}; border-radius: 3px;
}}
QSlider::sub-page:horizontal {{ background: {BLUE}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {WHITE}; border: 2px solid {BLUE};
    width: 15px; height: 15px; margin: -6px 0; border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{ background: {BLUE_SOFT}; }}

QTabWidget::pane {{
    border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    background: {SURFACE}; top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {INK_MUTED};
    padding: 9px 16px; margin-right: 2px; border: none;
    border-radius: {RADIUS_SMALL}px; font-weight: 500;
}}
QTabBar::tab:hover    {{ color: {BLUE}; }}
QTabBar::tab:selected {{ background: {BLUE_SOFT}; color: {BLUE}; font-weight: 600; }}

QListWidget {{
    background: {WHITE}; border: 1px solid {BORDER};
    border-radius: {RADIUS_SMALL}px; padding: 4px; outline: none;
}}
QListWidget::item {{ padding: 7px 9px; border-radius: 6px; color: {INK}; }}
QListWidget::item:hover    {{ background: {CANVAS}; }}
QListWidget::item:selected {{ background: {BLUE_SOFT}; color: {BLUE}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG}; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {INK_FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG}; border-radius: 5px; min-width: 28px;
}}

QScrollArea {{ border: none; background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QStatusBar {{
    background: {WHITE}; border-top: 1px solid {BORDER}; color: {INK_MUTED};
}}
"""
