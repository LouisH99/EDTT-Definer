# -*- mode: python ; coding: utf-8 -*-

import os

executable_name = os.environ.get("EDTT_EXE_NAME", "EDTT-Definer-v0.6.7")

a = Analysis(
    ["launcher.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Qt 6.11 on this Windows build links against the Windows system ICU DLL.
# PyInstaller can discover an unrelated ICU installation on PATH and bundle its
# unversioned icuuc.dll, which shadows the compatible system DLL and makes
# PySide6.QtWidgets fail with WinError 127 at startup. Keep the system ICU.
a.binaries = [
    item
    for item in a.binaries
    if item[0].lower() != "icuuc.dll" and not item[0].lower().startswith("icudt")
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=executable_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
