# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for adm-agent-client."""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)

_COLLECT_PKGS = [
    "typer",
    "click",
    "rich",
]

all_datas = []
all_binaries = []
all_hiddenimports = []

for pkg in _COLLECT_PKGS:
    try:
        datas, binaries, hiddenimports = collect_all(pkg)
        all_datas += datas
        all_binaries += binaries
        all_hiddenimports += hiddenimports
    except Exception:
        print(f"WARN: collect_all('{pkg}') failed — skipping")


a = Analysis(
    [str(ROOT / "src" / "cmd" / "client_cli.py")],
    pathex=[str(ROOT)] + sys.path,
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hiddenimports + [
        "src.client.config",
        "src.client.runtime",
        "src.client.bootstrap_prompt",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "test",
        "tests",
        "extension",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="adm-agent-client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="adm-agent-client",
)

