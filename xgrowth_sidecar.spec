# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the frozen FastAPI sidecar (spec §31.6).

Produces a single-file `xgrowth-sidecar` executable that the Tauri shell spawns
in release builds. Bundles the read-only resources the backend resolves via
`app.paths.RESOURCE_ROOT` (= sys._MEIPASS when frozen): migrations/, config/,
and spec.md. Streamlit is excluded — the sidecar never executes the lazy
`import streamlit` inside the forms' render() functions, so the excluded stub is
never triggered (keeps the binary small; Streamlit is a huge GUI dependency).

Build (see scripts/build_sidecar.sh):
    uv run pyinstaller xgrowth_sidecar.spec \
        --distpath desktop/src-tauri/bin --workpath build/pyinstaller --noconfirm
"""

from pathlib import Path

spec_dir = Path(SPECPATH)  # noqa: F821 - SPECPATH injected by PyInstaller

datas = [
    (str(spec_dir / "migrations"), "migrations"),
    (str(spec_dir / "config"), "config"),
    (str(spec_dir / "spec.md"), "."),
]

# uvicorn loads its protocol/loop implementations dynamically — PyInstaller's
# static analysis misses them without these hints.
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

# Heavy libs the sidecar never runs (Streamlit only via never-called render();
# the rest are GUI/test/notebook tooling).
excludes = [
    "streamlit",
    "tkinter",
    "matplotlib",
    "pytest",
    "_pytest",
    "IPython",
    "notebook",
]

a = Analysis(
    ["scripts/sidecar_entry.py"],
    pathex=[str(spec_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="xgrowth-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
