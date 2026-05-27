"""Tests for sidecar log redaction helpers."""

from __future__ import annotations

from app.service.log_redaction import REDACTED, redact_text

SENTINEL = "__XGROWTH_SECRET_SENTINEL__"


def test_redact_text_removes_bearer_tokens() -> None:
    redacted = redact_text(f"Authorization: Bearer {SENTINEL}", extra_secrets=[SENTINEL])
    assert SENTINEL not in redacted
    assert REDACTED in redacted


def test_redact_text_removes_handshake_token_prefix() -> None:
    redacted = redact_text(f"XGROWTH_TOKEN={SENTINEL}", extra_secrets=[SENTINEL])
    assert SENTINEL not in redacted
    assert REDACTED in redacted


def test_redact_text_removes_sk_pattern() -> None:
    redacted = redact_text("key=sk-test-secret-value-12345678")
    assert "sk-test-secret-value-12345678" not in redacted
