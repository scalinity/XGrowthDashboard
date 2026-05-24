"""Sidecar entry point (§31.2).

Run by the Tauri shell as a subprocess:

    python -m app.service

It picks a free loopback port, mints a per-launch bearer token, prints a
two-line handshake to stdout for the shell to parse, then serves the FastAPI
app on 127.0.0.1 only:

    XGROWTH_PORT=<port>
    XGROWTH_TOKEN=<token>

The shell reads those two lines, then talks to the service with
``Authorization: Bearer <token>``. Nothing is bound to a public interface.
"""

from __future__ import annotations

import os
import socket
import sys

import uvicorn

from app.paths import migrate_legacy_db_if_needed
from app.secret_store import resolve_anthropic_api_key
from app.service.app import create_app
from app.service.security import generate_launch_token

# Stable prefixes the shell greps for on the sidecar's stdout.
PORT_PREFIX = "XGROWTH_PORT="
TOKEN_PREFIX = "XGROWTH_TOKEN="  # noqa: S105 - this is a prefix label, not a secret


def _pick_free_loopback_port() -> int:
    """Bind :0 on loopback to let the OS choose a free port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    # Load a repo .env if present (dev), then resolve the Anthropic key from
    # env → Keychain and export it so AgentClient picks it up unchanged (§31.5).
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # python-dotenv always present, but never hard-fail boot
        pass
    api_key = resolve_anthropic_api_key()
    if api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = api_key

    # Migrate the legacy ./data DB into Application Support on first launch
    # (copy, not move — §31.5). Done before serving so the default conn
    # factory resolves to the migrated path.
    migrate_legacy_db_if_needed()

    token = generate_launch_token()
    port = _pick_free_loopback_port()

    # Handshake — the shell parses these two stdout lines before connecting.
    print(f"{PORT_PREFIX}{port}", flush=True)
    print(f"{TOKEN_PREFIX}{token}", flush=True)

    app = create_app(token=token)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
