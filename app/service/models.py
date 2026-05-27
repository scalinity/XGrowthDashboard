"""Shared FastAPI request models for the sidecar."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

class StartConversationBody(BaseModel):
    """POST /agent/conversations request body (§14.8)."""

    title: str | None = None
    context_seed: str | None = None


class SendMessageBody(BaseModel):
    """POST /agent/conversations/{id}/messages request body."""

    text: str


class PublishBody(BaseModel):
    """POST /publish request body (§28.10). ``confirm`` must equal 'confirm'."""

    post_id: int
    text: str
    confirm: str
    message_id: int | None = None


class SettingValue(BaseModel):
    """PUT /settings/{key} request body. ``value`` is any JSON-serializable value."""

    value: Any


class SecretBody(BaseModel):
    """PUT /settings/secrets/{name} request body — a write-only secret value."""

    value: str

