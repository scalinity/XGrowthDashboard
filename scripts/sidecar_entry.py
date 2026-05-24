"""PyInstaller entry point for the frozen FastAPI sidecar (spec §31.6).

Frozen into a single ``xgrowth-sidecar`` executable that the Tauri shell spawns
in release builds. Delegates to the same ``app.service.__main__.main`` the dev
sidecar uses, so dev (`uv run python -m app.service`) and prod run identical code.
"""

from app.service.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
