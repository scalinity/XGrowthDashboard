"""Shared sidecar helpers."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import HTTPException

from app.forms import FormError


def _form_error(exc: FormError) -> HTTPException:
    """Map a forms-layer FormError to a 400 with structured field errors."""
    return HTTPException(
        status_code=400,
        detail={"message": str(exc), "field_errors": exc.field_errors},
    )


def _automation_queue_row(row: sqlite3.Row) -> dict[str, Any]:
    """Normalize manual cleanup queue rows for Streamlit-era and native clients."""
    payload = dict(row)
    preview = payload.get("preview") or payload.get("text_preview") or ""
    created = (
        payload.get("created_at_utc")
        or payload.get("created_at")
        or payload.get("created_date")
    )
    payload["text_preview"] = preview
    payload["created_at"] = created
    return payload


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Event frame (text/event-stream)."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

