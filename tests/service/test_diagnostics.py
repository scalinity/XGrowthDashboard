"""Tests for diagnostics payload helpers."""

from __future__ import annotations

from app.db import connect
from app.service.diagnostics import build_diagnostics_payload, format_diagnostics_text

SENTINEL = "__XGROWTH_SECRET_SENTINEL__"


def test_format_diagnostics_text_redacts_sentinel(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    conn = connect(":memory:")
    try:
        payload = build_diagnostics_payload(conn, service_version="0.1.0")
        payload["recent_log_lines"] = [f"boot token={SENTINEL}"]
        text = format_diagnostics_text(payload)
        assert SENTINEL not in text
    finally:
        conn.close()
