"""Secret resolution for the native app (spec §31.5).

``ANTHROPIC_API_KEY`` (and ``XAI_API_KEY``, X OAuth tokens) resolve env-first
(dev / repo ``.env``), then the macOS Keychain via ``keyring``. The native
sidecar stores keys in the Keychain so a ``.app`` moved to ``/Applications``
needs no ``.env`` beside the binary; dev keeps using ``.env``.

Named ``secret_store`` (not ``secrets``) to avoid shadowing the stdlib
``secrets`` module used elsewhere (e.g. ``app/service/security.py``).
"""

from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "XGrowthDashboard"
ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"


def _keyring_get(name: str) -> str | None:
    """Read a secret from the OS keyring. Returns None if keyring is
    unavailable or the entry is missing (never raises)."""
    try:
        import keyring

        return keyring.get_password(KEYCHAIN_SERVICE, name)
    except Exception as exc:  # noqa: BLE001 - keyring backend issues must not crash boot
        _log.warning("keyring lookup for %r failed: %s", name, exc)
        return None


def resolve_secret(name: str) -> str | None:
    """Return a secret: env var first (dev / .env), then the OS keyring."""
    value = os.environ.get(name)
    if value:
        return value
    return _keyring_get(name)


def store_secret(name: str, value: str) -> None:
    """Persist a secret in the OS keyring (Keychain on macOS)."""
    import keyring

    keyring.set_password(KEYCHAIN_SERVICE, name, value)


def resolve_anthropic_api_key() -> str | None:
    """Resolve the Anthropic API key (env → Keychain)."""
    return resolve_secret(ANTHROPIC_API_KEY)
