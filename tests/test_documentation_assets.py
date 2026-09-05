from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage


REPOSITORY = Path(__file__).resolve().parents[1]


def test_readme_screenshots_are_valid_and_privacy_safe() -> None:
    expected = (
        "editor-overview.png",
        "gate-live-capture.png",
        "whole-race-conversion.png",
    )
    for name in expected:
        path = REPOSITORY / "docs" / "images" / name
        image = QImage(str(path))
        assert path.is_file()
        assert not image.isNull()
        assert image.width() >= 600
        assert image.height() >= 400
        assert b"LouisH99" not in path.read_bytes()
