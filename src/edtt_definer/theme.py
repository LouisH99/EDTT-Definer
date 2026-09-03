from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


class ThemeMode(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @classmethod
    def from_setting(cls, value: object) -> ThemeMode:
        try:
            return cls(str(value or cls.SYSTEM.value))
        except ValueError:
            return cls.SYSTEM


def apply_theme(mode: ThemeMode) -> None:
    app = QApplication.instance()
    if app is None:
        return
    hints = app.styleHints()
    if mode is ThemeMode.SYSTEM:
        hints.unsetColorScheme()
    elif mode is ThemeMode.DARK:
        hints.setColorScheme(Qt.ColorScheme.Dark)
    else:
        hints.setColorScheme(Qt.ColorScheme.Light)
    app.setProperty("edttThemeMode", mode.value)


def is_dark_theme(widget: QWidget | None = None) -> bool:
    app = QApplication.instance()
    if app is not None:
        forced = ThemeMode.from_setting(app.property("edttThemeMode"))
        if forced is ThemeMode.DARK:
            return True
        if forced is ThemeMode.LIGHT:
            return False
        if app.styleHints().colorScheme() is Qt.ColorScheme.Dark:
            return True
    palette = widget.palette() if widget is not None else app.palette() if app is not None else QPalette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def semantic_color(role: str, widget: QWidget | None = None) -> QColor:
    dark = is_dark_theme(widget)
    palettes = {
        "error": ("#ff8a80", "#b71c1c"),
        "warning": ("#ffcc80", "#9a5b00"),
        "success": ("#81c784", "#1b5e20"),
        "muted": ("#b0bec5", "#616161"),
    }
    dark_color, light_color = palettes.get(role, palettes["muted"])
    return QColor(dark_color if dark else light_color)
