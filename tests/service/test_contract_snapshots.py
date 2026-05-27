"""JSON-schema snapshots for prioritized response models (Phase G)."""

from __future__ import annotations

import json
from pathlib import Path

from app.service.models import (
    AgentModeResponse,
    CapabilitiesResponse,
    ConversationsResponse,
    DiagnosticsCopyResponse,
    HealthDetailsResponse,
    MessagesResponse,
    PublishResponse,
    ReplyQueueResponse,
    SecretsResponse,
    SettingsResponse,
    TodayResponse,
)

SNAPSHOT = Path(__file__).parent / "__snapshots__" / "contracts.json"

MODELS = {
    "AgentModeResponse": AgentModeResponse,
    "CapabilitiesResponse": CapabilitiesResponse,
    "ConversationsResponse": ConversationsResponse,
    "DiagnosticsCopyResponse": DiagnosticsCopyResponse,
    "HealthDetailsResponse": HealthDetailsResponse,
    "MessagesResponse": MessagesResponse,
    "PublishResponse": PublishResponse,
    "ReplyQueueResponse": ReplyQueueResponse,
    "SecretsResponse": SecretsResponse,
    "SettingsResponse": SettingsResponse,
    "TodayResponse": TodayResponse,
}


def _strip_volatile(schema: dict) -> dict:
    """Remove titles/descriptions that churn without semantic change."""
    cleaned = json.loads(json.dumps(schema))
    cleaned.pop("title", None)
    if "properties" in cleaned:
        for prop in cleaned["properties"].values():
            if isinstance(prop, dict):
                prop.pop("title", None)
                prop.pop("description", None)
    return cleaned


def test_contract_schema_snapshot() -> None:
    current = {
        name: _strip_volatile(model.model_json_schema())
        for name, model in MODELS.items()
    }
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    if not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert current == expected
