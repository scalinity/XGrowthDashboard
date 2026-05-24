"""Per-launch bearer-token auth for the loopback sidecar (§31.2).

The Tauri shell mints a fresh token at every launch, passes it to the sidecar,
and sends it on every request. Binding to 127.0.0.1 keeps the surface local;
the token ensures that nothing else on the loopback interface (another local
process, a stray browser tab) can drive the backend.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status


def generate_launch_token() -> str:
    """Return a fresh, URL-safe per-launch bearer token."""
    return secrets.token_urlsafe(32)


class BearerTokenAuth:
    """FastAPI dependency enforcing ``Authorization: Bearer <token>``.

    Uses a constant-time comparison so a missing/wrong token can't be timed.
    """

    def __init__(self, token: str) -> None:
        # Compare on UTF-8 bytes: secrets.compare_digest raises TypeError on
        # non-ASCII *str* input, which would surface as a 500 rather than a
        # clean 401 for a malformed Authorization header. str.encode never
        # raises, and compare_digest on bytes is constant-time for any input.
        self._expected = f"Bearer {token}".encode()

    def __call__(self, authorization: str | None = Header(default=None)) -> None:
        if authorization is None or not secrets.compare_digest(
            authorization.encode("utf-8"), self._expected
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing bearer token",
            )
