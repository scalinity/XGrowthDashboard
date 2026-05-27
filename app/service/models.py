"""Shared FastAPI request/response models for the sidecar."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class ApiError(BaseModel):
    """Standard FastAPI error envelope."""

    detail: str


class CapabilityEntry(BaseModel):
    available: bool
    label: str


class CapabilitiesResponse(BaseModel):
    """GET /capabilities — dependency availability for degraded-mode UI."""

    model_config = ConfigDict(extra="allow")

    anthropic: CapabilityEntry
    voyage: CapabilityEntry
    x_api: CapabilityEntry
    grok: CapabilityEntry
    xurl: CapabilityEntry
    keychain: CapabilityEntry


class AgentModeNicheGate(BaseModel):
    blocked: bool
    niche_problem_set: bool
    niche_person_set: bool


class AgentModeLintGate(BaseModel):
    reply_quality_lint_enabled: bool
    reply_intent_required: bool


class AgentModeSecretState(BaseModel):
    configured: bool


class AgentModeToolPermissions(BaseModel):
    read_dashboard: bool
    read_x_api: bool
    write_drafts: bool
    publish: bool
    secrets: bool


class AgentModeResponse(BaseModel):
    """GET /agent/mode — agent gates and permission matrix."""

    data_collection_mode: str
    api_read: bool
    publish_mode: str
    niche_gate: AgentModeNicheGate
    lint_gate: AgentModeLintGate
    secret_state: dict[str, AgentModeSecretState]
    tool_permissions: AgentModeToolPermissions


class HealthDetailsResponse(BaseModel):
    """GET /health/details — sidecar readiness without secrets."""

    model_config = ConfigDict(extra="allow")

    ready: bool
    sidecar_phase: str
    app_version: str
    service_version: str
    db_path: str
    latest_migration: str | None = None
    data_dir_source: str
    resource_root: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsCopyResponse(BaseModel):
    """GET /diagnostics/copy — redacted support bundle."""

    model_config = ConfigDict(extra="allow")

    text: str
    diagnostics: dict[str, Any] | None = None


class SettingsResponse(BaseModel):
    """GET /settings — all settings keys (values may be any JSON type)."""

    settings: dict[str, Any]


class SecretsResponse(BaseModel):
    """GET /settings/secrets — presence only, never values."""

    secrets: dict[str, dict[str, bool]]


class ConversationSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    title: str | None = None
    context_seed: str | None = None
    created_at: str | None = None


class ConversationsResponse(BaseModel):
    conversations: list[ConversationSummary]


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    role: str
    content: str | None = None
    tool_calls_json: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    confidence_label: str | None = None


class MessagesResponse(BaseModel):
    conversation_id: int
    messages: list[AgentMessage]


class ReplyQueueCounters(BaseModel):
    candidates: int
    drafted: int
    posted_today: int
    skipped_today: int


class ReplyQueueResponse(BaseModel):
    """GET /views/reply-queue — minimum stable contract; extra fields allowed."""

    model_config = ConfigDict(extra="allow")

    slice: str = "reply_queue"
    counters: ReplyQueueCounters


class TodayResponse(BaseModel):
    """GET /views/today — minimum stable contract; extra fields allowed."""

    model_config = ConfigDict(extra="allow")

    slice: str = "today"
    today_iso: str


class PublishResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    success: bool
    post_id: int | None = None

