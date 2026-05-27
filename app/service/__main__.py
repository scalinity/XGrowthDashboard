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
from app.service.log_redaction import configure_sidecar_logging
from app.service.security import generate_launch_token

# Stable prefixes the shell greps for on the sidecar's stdout.
PORT_PREFIX = "XGROWTH_PORT="
TOKEN_PREFIX = "XGROWTH_TOKEN="  # noqa: S105 - this is a prefix label, not a secret


def _bind_free_loopback_socket() -> socket.socket:
    """Bind :0 on loopback and return the socket (kept open to avoid TOCTOU).

    RV5-C6 fix: the prior version released the socket before uvicorn bound it,
    creating a race window where another process could claim the port. Now the
    caller passes the bound socket directly to uvicorn so it's never released.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


def main() -> int:
    configure_sidecar_logging()
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
    sock = _bind_free_loopback_socket()
    port = int(sock.getsockname()[1])

    # Handshake — the shell parses these two stdout lines before connecting.
    print(f"{PORT_PREFIX}{port}", flush=True)
    print(f"{TOKEN_PREFIX}{token}", flush=True)

    # RV5-C7 fix: wrap create_app so an invariant failure (AssertionError)
    # surfaces a clear message instead of silently dying (no handshake → the
    # Tauri shell times out with a "stuck loading" state).
    try:
        app = create_app(token=token)
    except Exception as exc:  # noqa: BLE001 — must never crash silently
        print(
            f"[sidecar] FATAL: create_app failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        sock.close()
        return 1

    # RV5-C6 fix: pass the already-bound socket via `fd` so uvicorn never
    # re-binds — eliminates the TOCTOU race where another process claims the
    # port between our bind and uvicorn's.
    sock.listen(128)
    uvicorn.run(app, fd=sock.fileno(), log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
