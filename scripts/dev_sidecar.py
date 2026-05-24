"""Dev-only fixed-port sidecar launcher (spec §31, Step 0).

Serves the same FastAPI app as ``python -m app.service``, but on a FIXED port +
bearer token (from env, with dev defaults) and WITHOUT the random-port stdout
handshake the Tauri shell parses. This lets the React frontend run in a plain
browser (``pnpm -C desktop dev``) and be screenshot-diffed against the Streamlit
pages against the SAME local DB — the per-view §31.7 fidelity gate.

    uv run python -m scripts.dev_sidecar

Env (all optional; sensible dev defaults):
    XGROWTH_DEV_PORT    loopback port to bind            (default 8765)
    XGROWTH_DEV_TOKEN   bearer token the frontend sends  (default 'dev-token')
    XGROWTH_DATA_DIR    DB dir; defaults to the repo ./data so this reads the
                        same dashboard.db the Streamlit app reads in dev.

This is NEVER used in the packaged app — production uses ``app.service.__main__``
with a random port + per-launch token. Binds 127.0.0.1 only.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

DEFAULT_PORT = 8765
DEFAULT_TOKEN = "dev-token"  # noqa: S105 - dev-only loopback token, not a secret


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    # Read the same DB the Streamlit app reads in dev (legacy ./data) unless the
    # operator has pointed XGROWTH_DATA_DIR elsewhere.
    os.environ.setdefault("XGROWTH_DATA_DIR", str(project_root / "data"))

    # Load the repo .env (dev path) so ANTHROPIC_API_KEY is available without
    # touching the Keychain (§31.5). Never hard-fail boot if it's missing.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    # Resolve the Anthropic key for the agent endpoints, but never let a missing
    # key or Keychain hiccup block the read-only view dev loop.
    try:
        from app.secret_store import resolve_anthropic_api_key

        api_key = resolve_anthropic_api_key()
        if api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = api_key
    except Exception as exc:  # noqa: BLE001 - dev convenience, agent endpoints just no-op
        print(f"[dev_sidecar] no Anthropic key resolved ({exc}); agent endpoints disabled", flush=True)

    from app.service.app import create_app

    port = int(os.environ.get("XGROWTH_DEV_PORT", DEFAULT_PORT))
    token = os.environ.get("XGROWTH_DEV_TOKEN", DEFAULT_TOKEN)

    # Vite dev origins so the browser screenshot-diff loop can fetch the loopback
    # sidecar cross-origin. DEV-ONLY — production app.service.__main__ never sets this.
    dev_cors_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    print(
        f"[dev_sidecar] serving http://127.0.0.1:{port}  "
        f"token={token}  data={os.environ['XGROWTH_DATA_DIR']}",
        flush=True,
    )
    app = create_app(token=token, dev_cors_origins=dev_cors_origins)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
