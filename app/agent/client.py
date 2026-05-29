"""AgentClient — Anthropic SDK wrapper (§28, §14.8).

The client:

  1. Loads the API key from ``.env`` (``ANTHROPIC_API_KEY``).
  2. Assembles the system prompt via ``prompt_builder.build_system_prompt``.
  3. Imports tool specs from ``app.agent.tools.AGENT_TOOLS`` ONLY — the
     publish tools in ``_internal_tools`` are never imported here.
  4. Enforces the §28.6 monthly cost ceiling before each round trip.
  5. Dispatches tool_use blocks to local handlers via ``dispatch_tool_call``.
  6. Persists every assistant message + tool call to the DB.
  7. Exposes an SSE-friendly streaming path for the native desktop app (§31).

The publish tools deliberately have no path into this client. The
``dispatch_tool_call`` helper treats unknown names and malformed
tool-call payloads as structured errors and returns them as ``error`` tool
results instead of surfacing runtime exceptions to the user-facing turn.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

from app.agent import audit, cost, prompt_builder, session, tools

_log = logging.getLogger(__name__)

# §28.2 rule #12 + #13: every save_draft_* call MUST run through the
# orchestrator gate (IWH self-score parse + dark-pattern lint preflight)
# BEFORE the handler is invoked. The model can emit these tool_use blocks
# at any time; without the gate the entire revision/lint discipline this
# phase exists to enforce is dead code at runtime.
SAVE_DRAFT_TOOLS: frozenset[str] = frozenset(
    {"save_draft_post", "save_draft_reply"}
)
MAX_TOOL_ROUNDS = 4
INVALID_TOOL_CALL_LABEL = "invalid tool call"
_TOOL_CALL_FALLBACK_ID = "toolu_invalid"


def _normalize_tool_calls(tool_calls: list[dict] | None) -> list[dict[str, Any]]:
    """Normalize tool calls so malformed entries never crash the turn loop."""

    normalized: list[dict[str, Any]] = []
    for idx, raw_tc in enumerate(tool_calls or []):
        tool_id = f"{_TOOL_CALL_FALLBACK_ID}_{idx}_{uuid4().hex[:12]}"
        raw_name = ""
        raw_input: Any = {}
        history_input: dict[str, Any] = {}

        if isinstance(raw_tc, Mapping):
            candidate_id = raw_tc.get("id")
            if isinstance(candidate_id, str) and candidate_id.strip():
                tool_id = candidate_id.strip()

            candidate_name = raw_tc.get("name", "")
            if isinstance(candidate_name, str):
                raw_name = candidate_name.strip()
            elif candidate_name is not None:
                raw_name = str(candidate_name).strip()

            candidate_input = raw_tc.get("input", {})
            if candidate_input is None:
                candidate_input = {}
            raw_input = candidate_input
            if isinstance(candidate_input, Mapping):
                history_input = dict(candidate_input)

        display_name = raw_name or INVALID_TOOL_CALL_LABEL
        normalized.append(
            {
                "id": tool_id,
                "name": display_name,
                "raw_name": raw_name,
                "input": history_input,
                "dispatch_input": raw_input,
            }
        )
    return normalized


_TOOL_ROUND_CAP_USER_MESSAGE = (
    "Growth Agent stopped after too many tool-use rounds. "
    "Try narrowing the request and run it again."
)
_TOOL_ROUND_CAP_TOOL_ERROR = (
    "Tool round limit reached for this turn — narrow the request and try again."
)


def _tool_result_content_payload(result: dict[str, Any]) -> Any:
    """Serialize a dispatch result for persistence and Anthropic replay."""
    status = result.get("status")
    if status in {"success", "partial"}:
        return result.get("result")
    return result.get("error") or result.get("rationale") or ""


def _append_tool_round_cap_results(
    conn: sqlite3.Connection,
    *,
    conversation_id: int,
    tool_calls: list[dict[str, Any]],
) -> None:
    """Synthesize tool_result rows when the per-turn tool round cap is hit.

  Without these rows, the last assistant message leaves dangling tool_use
  blocks in history and the next Anthropic call fails validation.
    """
    for tc in tool_calls:
        append_message(
            conn,
            conversation_id=conversation_id,
            role="tool_result",
            content=json.dumps(_TOOL_ROUND_CAP_TOOL_ERROR),
            tool_call_id=tc.get("id"),
        )


# Phase 10 / §29.5 reply_intent promotion. Cached defaults match the
# migration 023 INSERT OR IGNORE seed. Settings-row lookups happen on
# every dispatch — the lookup is sub-millisecond and the value is
# Daniel's calibration knob, so caching across requests would be
# stale-by-design.
_REPLY_INTENT_REQUIRED_DEFAULT: bool = True


def _read_reply_intent_required(conn: sqlite3.Connection) -> bool:
    """Pull the §29.5 Phase 10 toggle from settings.

    Falls back to True (enforce) when the row is missing or malformed —
    fail-safe direction is to enforce the new gate rather than silently
    accept NULL intents in a fresh DB that hasn't been re-seeded.
    """
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?",
        ("reply_intent_required",),
    ).fetchone()
    if row is None or row["value_json"] is None:
        return _REPLY_INTENT_REQUIRED_DEFAULT
    try:
        return bool(json.loads(row["value_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _REPLY_INTENT_REQUIRED_DEFAULT


# Phase 10 S8 — structured error codes for the reply_intent gate so
# audit-row callers can filter on `code` instead of fuzzy-matching the
# message body. Codes are stable strings; messages are presentation.
REPLY_INTENT_GATE_CODES: tuple[str, ...] = (
    "INTENT_MISSING",
    "INTENT_INVALID",
)


def _anthropic_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status
    return None


def _format_anthropic_error_for_user(exc: Exception) -> str:
    """Return a chat-safe provider error; raw SDK payloads stay in logs."""
    status_code = _anthropic_status_code(exc)
    exc_name = type(exc).__name__
    raw = str(exc).lower()

    if status_code == 529 or exc_name == "OverloadedError" or "overloaded_error" in raw:
        return (
            "Anthropic is overloaded right now (HTTP 529). The SDK already retried this "
            "request, so wait a minute and try again; your message was saved in this "
            "conversation."
        )
    if "decompressing data" in raw or "incorrect header check" in raw:
        return (
            "The Growth Agent hit a temporary provider/network decoding error while "
            "reading the Anthropic response. Try again shortly; your message was saved "
            "in this conversation."
        )
    if status_code == 429 or exc_name == "RateLimitError":
        return (
            "Anthropic rate-limited the Growth Agent request. Wait a bit and try again; "
            "your message was saved in this conversation."
        )
    if status_code is not None and status_code >= 500:
        return (
            f"Anthropic returned a temporary server error (HTTP {status_code}). "
            "Try again shortly; your message was saved in this conversation."
        )
    return (
        f"Growth Agent call failed before a response came back ({exc_name}). "
        "The underlying error was logged for debugging."
    )


def _validate_reply_intent_or_error(
    conn: sqlite3.Connection, tool_input: dict[str, Any]
) -> str | None:
    """Phase 10 / §29.5 — gate save_draft_reply on reply_intent.

    Returns ``None`` when the input is acceptable (either intent is
    present + valid, or the toggle is OFF and intent is absent/NULL).
    Returns a non-empty error string when the gate refuses; the caller
    surfaces it as a status='error' tool result with the canonical
    refuse-reason audit notes.

    Phase 10 S8 — the returned error string has the structured shape
    ``"<CODE>: <human message>"`` where CODE is one of
    REPLY_INTENT_GATE_CODES. The dispatcher's audit row carries the
    full message; downstream queryability gets the code as a stable
    prefix (e.g. ``WHERE error_message LIKE 'INTENT_MISSING:%'``).
    Message body still enumerates the valid enum so the agent gets the
    actionable info to retry.

    The single source of truth for the enum is
    ``app.agent.reply_targets.REPLY_INTENT_ENUM`` (also used by the
    tools.py schema and the spec drift check) — importing it lazily
    here keeps the client module's import graph minimal.
    """
    from app.agent.reply_targets import REPLY_INTENT_ENUM

    required = _read_reply_intent_required(conn)
    intent = tool_input.get("reply_intent")

    if intent is None or (isinstance(intent, str) and not intent.strip()):
        if not required:
            return None  # escape hatch — NULL passes when toggle is off
        return (
            "INTENT_MISSING: reply_intent is required (§29.5 Phase 10). "
            f"Pick one of {list(REPLY_INTENT_ENUM)} or skip the reply. "
            "Disable via Settings → Growth Agent → Reply discipline → "
            "reply_intent_required if this is creating calibration friction."
        )
    if intent not in REPLY_INTENT_ENUM:
        return (
            f"INTENT_INVALID: reply_intent={intent!r} not in §29.5 enum. "
            f"Valid values: {list(REPLY_INTENT_ENUM)}."
        )
    return None


# ---------------------------------------------------------------------------
# Bootstrap conversation rows.
# ---------------------------------------------------------------------------
def start_conversation(
    conn: sqlite3.Connection,
    *,
    title: str | None = None,
    context_seed: str | None = None,
    model_default: str = "claude-opus-4-7",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO agent_conversations
            (title, context_seed, status, model_default)
        VALUES (?, ?, 'active', ?)
        """,
        (title, context_seed, model_default),
    )
    return int(cur.lastrowid)


def delete_conversation(conn: sqlite3.Connection, *, conversation_id: int) -> bool:
    """Delete one agent conversation and its message/tool-call history.

    Drafts and posts are preserved as audit records: their foreign keys are
    declared ``ON DELETE SET NULL`` in §10.2/§28 migrations, while messages and
    tool calls cascade away with the deleted conversation.
    """
    row = conn.execute(
        "SELECT id FROM agent_conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM agent_conversations WHERE id = ?", (conversation_id,))
    return True


def append_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: int,
    role: str,
    content: str,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    rate_snapshot: dict | None = None,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
    confidence_label: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO agent_messages
            (conversation_id, role, content, tool_calls_json, tool_call_id,
             model, input_tokens, output_tokens, rate_snapshot_json,
             confidence_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            role,
            content,
            json.dumps(tool_calls) if tool_calls else None,
            tool_call_id,
            model,
            input_tokens,
            output_tokens,
            json.dumps(rate_snapshot) if rate_snapshot else None,
            confidence_label,
        ),
    )
    # Update conversation's denormalized counters.
    conn.execute(
        """
        UPDATE agent_conversations
        SET last_message_at_utc = datetime('now'),
            message_count = message_count + 1,
            total_input_tokens = total_input_tokens + COALESCE(?, 0),
            total_output_tokens = total_output_tokens + COALESCE(?, 0)
        WHERE id = ?
        """,
        (input_tokens, output_tokens, conversation_id),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Tool dispatch — agent-side handlers only.
# ---------------------------------------------------------------------------
def dispatch_tool_call(
    conn: sqlite3.Connection,
    *,
    tool_name: str,
    tool_input: Any,
    message_id: int,
    assistant_text: str = "",
    current_attempt_index: int = 1,
) -> dict[str, Any]:
    """Dispatch a tool_use block to its handler and log the call.

    Unknown or malformed payloads are converted into ``error`` tool results
    (instead of bubbling exceptions), so the turn can continue and recover.

    §28.2 rule #12 + #13 enforcement: for tool_name in SAVE_DRAFT_TOOLS,
    run the orchestrator gate (IWH score parse + dark-pattern lint preflight)
    against the parent assistant message's text BEFORE calling the handler.
    Refuse/revise outcomes short-circuit without writing to agent_drafts.
    The caller (AgentClient.send_message_sync) threads ``assistant_text``
    and ``current_attempt_index`` here.
    """
    start = datetime.now(timezone.utc)
    normalized_tool_name = tool_name.strip() if isinstance(tool_name, str) else ""

    if not normalized_tool_name:
        duration_ms = int(
            (datetime.now(timezone.utc) - start).total_seconds() * 1000
        )
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=INVALID_TOOL_CALL_LABEL,
            arguments=tool_input if isinstance(tool_input, Mapping) else {},
            status="error",
            error_message="tool use block missing a tool name",
            duration_ms=duration_ms,
            notes="tool-call payload malformed",
        )
        return {
            "tool_name": INVALID_TOOL_CALL_LABEL,
            "status": "error",
            "error": "tool use block missing a tool name",
        }

    if not isinstance(tool_input, Mapping):
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=normalized_tool_name,
            arguments={},
            status="error",
            error_message=(
                "tool use block had malformed input; expected a JSON object "
                f"for tool {normalized_tool_name!r}"
            ),
            duration_ms=duration_ms,
            notes="tool-call payload malformed",
        )
        return {
            "tool_name": normalized_tool_name,
            "status": "error",
            "error": (
                "tool use block had malformed input; expected a JSON object "
                f"for tool {normalized_tool_name!r}"
            ),
        }

    if normalized_tool_name in SAVE_DRAFT_TOOLS:
        # Phase 10 / §29.5 — reply_intent promotion gate. Runs BEFORE
        # niche/IWH/lint so the agent can't propose-and-then-skip the
        # other gates by burning a turn on a reply that will get
        # rejected later. Only fires for save_draft_reply (the §29.5
        # axis is reply-only). The reply_intent_required setting
        # (default ON) is the calibration escape hatch — when OFF
        # the dispatcher accepts NULL and writes through.
        if normalized_tool_name == "save_draft_reply":
            intent_gate_error = _validate_reply_intent_or_error(
                conn, tool_input
            )
            if intent_gate_error is not None:
                audit.log_tool_call(
                    conn,
                    message_id=message_id,
                    tool_name=normalized_tool_name,
                    arguments=tool_input,
                    status="error",
                    error_message=f"reply-intent gate refuse: {intent_gate_error}",
                    duration_ms=int(
                        (datetime.now(timezone.utc) - start).total_seconds() * 1000
                    ),
                    notes="reply-intent gate refused",
                )
                return {
                    "tool_name": normalized_tool_name,
                    "status": "error",
                    "error": f"refused by reply-intent gate: {intent_gate_error}",
                }
        # §28.2 rule #15 — niche must be defined. This runs BEFORE the
        # IWH/lint gate; a prompt-injected request to "skip the niche
        # check" cannot bypass this because niche_gate consults only the
        # settings rows, never assistant_text.
        n_gate = session.niche_gate(conn)
        if not n_gate.passed:
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=normalized_tool_name,
                arguments=tool_input,
                status="error",
                error_message=f"niche-gate refuse: {n_gate.rationale}",
                duration_ms=int(
                    (datetime.now(timezone.utc) - start).total_seconds() * 1000
                ),
                notes="niche-gate refused",
            )
            return {
                "tool_name": normalized_tool_name,
                "status": "error",
                "error": f"refused by niche gate: {n_gate.rationale}",
            }
        # Phase 5.9 / §28.18 — pass draft_kind + target_post_text so the
        # reply-quality lint runs in-band with the IWH/dark-pattern gate.
        _draft_kind = (
            "reply" if normalized_tool_name == "save_draft_reply" else "standalone"
        )
        _target_post_text = tool_input.get("target_post_text") if _draft_kind == "reply" else None
        decision = session.decide_save_or_revise(
            conn,
            assistant_text=assistant_text,
            draft_text=tool_input.get("text", ""),
            current_attempt_index=current_attempt_index,
            draft_kind=_draft_kind,
            target_post_text=_target_post_text,
        )
        if decision.action == "refuse":
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=normalized_tool_name,
                arguments=tool_input,
                status="error",
                error_message=f"IWH refuse: {decision.rationale}",
                duration_ms=int(
                    (datetime.now(timezone.utc) - start).total_seconds() * 1000
                ),
                notes="iwh-gate refused",
            )
            return {
                "tool_name": normalized_tool_name,
                "status": "error",
                "error": f"refused by IWH gate: {decision.rationale}",
            }
        if decision.action == "revise":
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=normalized_tool_name,
                arguments=tool_input,
                status="error",
                error_message=f"IWH revise: {decision.rationale}",
                duration_ms=int(
                    (datetime.now(timezone.utc) - start).total_seconds() * 1000
                ),
                notes="iwh-gate revise",
            )
            return {
                "tool_name": normalized_tool_name,
                "status": "revise_required",
                "rationale": decision.rationale,
                "next_attempt_index": decision.next_attempt_index,
            }
        # decision.action == "save" — fall through to handler invocation.
        # Phase 5.8 / §28.14 — inject the dominant confidence label so the
        # save_draft_* handler writes it inside the same transaction as
        # the agent_drafts INSERT. Avoids the prior post-hoc UPDATE that
        # left a transient window with NULL confidence_label visible to
        # readers (Content Performance calibration, export jobs).
        if decision.confidence_label is not None:
            tool_input = {**tool_input, "confidence_label": decision.confidence_label}
        # Phase 5.9 / §28.18 — inject the reply-quality lint result so
        # the handler can persist agent_drafts.reply_quality_lint_passed
        # alongside the row. When None (standalone draft) the handler
        # writes NULL — same semantics as the lint not running.
        #
        # Phase 10 / §28.18 — also inject failure_mode so the handler
        # can persist agent_drafts.reply_quality_lint_failure_mode. The
        # spec is explicit: populated only when passed=False; NULL on
        # pass. Skipping the injection when passed is True keeps the
        # column NULL via the handler's None-default contract.
        if decision.reply_quality_result is not None:
            rq = decision.reply_quality_result
            tool_input = {
                **tool_input,
                "reply_quality_lint_passed": rq.passed,
            }
            if not rq.passed and rq.failure_mode is not None:
                tool_input["reply_quality_lint_failure_mode"] = rq.failure_mode

    try:
        tool = tools.get_tool(normalized_tool_name)
        result = tool.handler(conn, **tool_input)
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        # W26: handlers can signal stub/partial completion via a private
        # `_audit_status` key in the result dict. Strip it from the audit
        # payload AND from the value we return to the caller, but promote
        # it to the agent_tool_calls.status column so audit reviewers can
        # filter on it.
        audit_status = "success"
        if isinstance(result, dict) and result.get("_audit_status") in {"partial", "success"}:
            audit_status = str(result["_audit_status"])
            result = {k: v for k, v in result.items() if k != "_audit_status"}
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=normalized_tool_name,
            arguments=tool_input,
            status=audit_status,
            result=result,
            duration_ms=duration_ms,
        )
        return {"tool_name": normalized_tool_name, "result": result, "status": audit_status}
    except KeyError:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=normalized_tool_name,
            arguments=tool_input,
            status="error",
            error_message=f"unknown tool: {normalized_tool_name!r}",
            duration_ms=duration_ms,
            notes="tool name not in AGENT_TOOLS",
        )
        return {
            "tool_name": normalized_tool_name,
            "error": f"unknown tool {normalized_tool_name!r}",
            "status": "error",
        }
    except Exception as exc:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=normalized_tool_name,
            arguments=tool_input,
            status="error",
            error_message=f"{type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
        )
        return {
            "tool_name": normalized_tool_name,
            "error": f"{type(exc).__name__}: {exc}",
            "status": "error",
        }


# ---------------------------------------------------------------------------
# AgentClient — orchestrates the SDK call + tool loop.
# ---------------------------------------------------------------------------
@dataclass
class AgentTurn:
    """A single user→assistant exchange — what the chat view renders."""

    user_text: str
    assistant_text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    error: str | None = None


AgentStreamFrame = tuple[str, dict[str, Any]]
ModelStreamResult = tuple[str, list[dict], int, int]
ModelStream = Generator[AgentStreamFrame, None, ModelStreamResult]


class AgentClient:
    """Wraps the Anthropic SDK with cost-ceiling enforcement + tool dispatch.

    Tests can subclass and override ``_call_model`` to avoid the live SDK.
    """

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-7",
        max_tokens: int = 4096,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def send_message_sync(
        self,
        conn: sqlite3.Connection,
        *,
        conversation_id: int,
        user_text: str,
    ) -> AgentTurn:
        """Non-streaming round trip. Persists user + assistant messages.

        Wired into the Streamlit page in a placeholder way: the page calls
        this and renders the full assistant text + tool-call blocks at
        once. Streaming UX upgrade is a future iteration; the architecture
        already separates SDK call from UI render.
        """
        turn = AgentTurn(user_text=user_text, model=self.model)
        # Cost ceiling preflight — the actual cost is computed post-call
        # from the token counts the API returns. cost.PROJECTED_CALL_COST_
        # GUESS_USD is a single tunable constant for the preflight; tune
        # there if Opus pricing shifts materially.
        try:
            cost.check_ceiling_or_raise(
                conn,
                projected_call_cost_usd=cost.PROJECTED_CALL_COST_GUESS_USD,
            )
        except cost.MonthlyCostCeilingExceeded as exc:
            turn.error = str(exc)
            return turn

        # Persist the user message.
        append_message(
            conn,
            conversation_id=conversation_id,
            role="user",
            content=user_text,
        )

        if not self.is_available():
            turn.error = (
                "Growth Agent disabled — set ANTHROPIC_API_KEY in .env. "
                "See spec §28.8 for the env setup."
            )
            return turn

        total_in_tok = 0
        total_out_tok = 0
        dispatched: list[dict] = []
        tool_rounds = 0

        while True:
            try:
                assistant_text, tool_calls, in_tok, out_tok = self._call_model(
                    conn, conversation_id=conversation_id
                )
            except Exception as exc:
                _log.exception(
                    "Anthropic call failed for conversation_id=%s model=%s status_code=%s",
                    conversation_id,
                    self.model,
                    _anthropic_status_code(exc),
                )
                turn.error = _format_anthropic_error_for_user(exc)
                return turn

            total_in_tok += in_tok
            total_out_tok += out_tok
            tool_calls = _normalize_tool_calls(tool_calls)
            call_estimate = cost.estimate_cost(
                input_tokens=in_tok, output_tokens=out_tok, model=self.model
            )
            # Phase 5.8 / §28.14 — parse confidence labels from the assistant
            # message and persist the dominant one. Drafts inherit it via the
            # tool-result wiring further down (search for "Phase 5.8 / §28.14").
            from app.agent.session import (
                dominant_confidence_label,
                extract_confidence_labels,
            )
            _conf_labels = extract_confidence_labels(assistant_text)
            _dominant_conf = dominant_confidence_label(_conf_labels)

            msg_id = append_message(
                conn,
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_text,
                model=self.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                rate_snapshot=call_estimate.rate_snapshot,
                tool_calls=tool_calls or None,
                confidence_label=_dominant_conf,
            )
            turn.assistant_text = assistant_text

            if not tool_calls:
                break
            if tool_rounds >= MAX_TOOL_ROUNDS:
                _append_tool_round_cap_results(
                    conn,
                    conversation_id=conversation_id,
                    tool_calls=tool_calls,
                )
                turn.error = _TOOL_ROUND_CAP_USER_MESSAGE
                break

            tool_rounds += 1
            for tc in tool_calls:
                tc_name = tc["name"]
                tc_input = tc.get("dispatch_input", {})
                dispatch_tool_name = tc.get("raw_name", tc_name)
                tc_id = tc.get("id")
                # For save_draft_* tools, look up the conversation's current
                # IWH attempt count so the orchestrator's refuse-on-N+1 gate
                # has the right starting index. Counter increments via
                # _revise_draft (the durable side); this is the read.
                current_attempt = 1
                if tc_name in SAVE_DRAFT_TOOLS:
                    row = conn.execute(
                        """
                        SELECT COALESCE(MAX(iwh_attempt_index), 0) + 1 AS next_idx
                        FROM agent_drafts
                        WHERE conversation_id = ? AND status != 'rejected'
                        """,
                        (conversation_id,),
                    ).fetchone()
                    if row is not None and row["next_idx"] is not None:
                        current_attempt = int(row["next_idx"])
                result = dispatch_tool_call(
                    conn,
                    tool_name=dispatch_tool_name,
                    tool_input=tc_input,
                    message_id=msg_id,
                    assistant_text=assistant_text,
                    current_attempt_index=current_attempt,
                )
                dispatched.append(result)
                # Phase 5.8 / §28.14 — the dominant confidence label is now
                # injected into the save_draft_* tool_input by dispatch_tool_call
                # (see P58R-6) so it lands inside the handler's transaction.
                # No post-hoc UPDATE needed here.
                # Persist tool_result message so this same turn can continue
                # with the local tool output and future turns keep context.
                # Switch on `status` instead of truthy result — a legitimate empty
                # result ({} / []) is falsy and used to fall through to error,
                # which was None, persisted as the literal string "null" (W6).
                content_payload = _tool_result_content_payload(result)
                append_message(
                    conn,
                    conversation_id=conversation_id,
                    role="tool_result",
                    content=json.dumps(content_payload, default=str),
                    tool_call_id=tc_id,
                )

        estimate = cost.estimate_cost(
            input_tokens=total_in_tok, output_tokens=total_out_tok, model=self.model
        )
        turn.tool_calls = dispatched
        turn.input_tokens = total_in_tok
        turn.output_tokens = total_out_tok
        turn.cost_usd = estimate.total_usd
        return turn

    def send_message_stream_sync(
        self,
        conn: sqlite3.Connection,
        *,
        conversation_id: int,
        user_text: str,
    ) -> Generator[AgentStreamFrame, None, None]:
        """Streaming turn path for the native UI while preserving persistence."""
        turn = AgentTurn(user_text=user_text, model=self.model)
        try:
            cost.check_ceiling_or_raise(
                conn,
                projected_call_cost_usd=cost.PROJECTED_CALL_COST_GUESS_USD,
            )
        except cost.MonthlyCostCeilingExceeded as exc:
            yield ("error", {"error": str(exc)})
            return

        append_message(
            conn,
            conversation_id=conversation_id,
            role="user",
            content=user_text,
        )

        if not self.is_available():
            yield (
                "error",
                {
                    "error": (
                        "Growth Agent disabled — set ANTHROPIC_API_KEY in .env. "
                        "See spec §28.8 for the env setup."
                    )
                },
            )
            return

        total_in_tok = 0
        total_out_tok = 0
        dispatched: list[dict] = []
        tool_rounds = 0

        while True:
            yield (
                "thinking_delta",
                {
                    "text": (
                        "Sending tool results back to Claude..."
                        if tool_rounds
                        else "Calling Claude..."
                    )
                },
            )
            try:
                assistant_text, tool_calls, in_tok, out_tok = yield from self._call_model_stream(
                    conn, conversation_id=conversation_id
                )
            except Exception as exc:
                _log.exception(
                    "Anthropic stream failed for conversation_id=%s model=%s status_code=%s",
                    conversation_id,
                    self.model,
                    _anthropic_status_code(exc),
                )
                yield ("error", {"error": _format_anthropic_error_for_user(exc)})
                return

            total_in_tok += in_tok
            total_out_tok += out_tok
            tool_calls = _normalize_tool_calls(tool_calls)
            call_estimate = cost.estimate_cost(
                input_tokens=in_tok, output_tokens=out_tok, model=self.model
            )
            from app.agent.session import (
                dominant_confidence_label,
                extract_confidence_labels,
            )
            _conf_labels = extract_confidence_labels(assistant_text)
            _dominant_conf = dominant_confidence_label(_conf_labels)

            msg_id = append_message(
                conn,
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_text,
                model=self.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                rate_snapshot=call_estimate.rate_snapshot,
                tool_calls=tool_calls or None,
                confidence_label=_dominant_conf,
            )
            turn.assistant_text = assistant_text
            yield (
                "assistant",
                {
                    "text": assistant_text,
                    "model": self.model,
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                },
            )

            if not tool_calls:
                break
            if tool_rounds >= MAX_TOOL_ROUNDS:
                _append_tool_round_cap_results(
                    conn,
                    conversation_id=conversation_id,
                    tool_calls=tool_calls,
                )
                yield ("error", {"error": _TOOL_ROUND_CAP_USER_MESSAGE})
                return

            tool_rounds += 1
            for tc in tool_calls:
                tc_name = tc["name"]
                tc_input = tc.get("dispatch_input", {})
                dispatch_tool_name = tc.get("raw_name", tc_name)
                tc_id = tc.get("id")
                yield (
                    "tool_call",
                    {
                        "id": tc_id,
                        "name": tc_name,
                        "input": tc.get("input", {}),
                        "status": "running",
                    },
                )
                current_attempt = 1
                if tc_name in SAVE_DRAFT_TOOLS:
                    row = conn.execute(
                        """
                        SELECT COALESCE(MAX(iwh_attempt_index), 0) + 1 AS next_idx
                        FROM agent_drafts
                        WHERE conversation_id = ? AND status != 'rejected'
                        """,
                        (conversation_id,),
                    ).fetchone()
                    if row is not None and row["next_idx"] is not None:
                        current_attempt = int(row["next_idx"])
                result = dispatch_tool_call(
                    conn,
                    tool_name=dispatch_tool_name,
                    tool_input=tc_input,
                    message_id=msg_id,
                    assistant_text=assistant_text,
                    current_attempt_index=current_attempt,
                )
                dispatched.append(result)
                content_payload = _tool_result_content_payload(result)
                yield (
                    "tool_result",
                    {
                        "id": tc_id,
                        "name": tc_name,
                        "status": result.get("status"),
                        "result": result.get("result"),
                        "error": result.get("error"),
                        "rationale": result.get("rationale"),
                    },
                )
                append_message(
                    conn,
                    conversation_id=conversation_id,
                    role="tool_result",
                    content=json.dumps(content_payload, default=str),
                    tool_call_id=tc_id,
                )

        estimate = cost.estimate_cost(
            input_tokens=total_in_tok, output_tokens=total_out_tok, model=self.model
        )
        turn.tool_calls = dispatched
        turn.input_tokens = total_in_tok
        turn.output_tokens = total_out_tok
        turn.cost_usd = estimate.total_usd
        yield (
            "done",
            {
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "cost_usd": turn.cost_usd,
                "model": turn.model,
                "error": None,
            },
        )

    # ---------------------------------------------------------------------
    # SDK boundary — overridable for tests.
    # ---------------------------------------------------------------------
    def _call_model(
        self, conn: sqlite3.Connection, *, conversation_id: int
    ) -> tuple[str, list[dict], int, int]:
        """Make the actual Anthropic API call. Returns (text, tool_calls, in_tok, out_tok).

        Tests override this to skip the network round trip.
        """
        import anthropic  # local import keeps the offline path cheap

        # P511R-20: explicit 120s timeout. The main agent loop's
        # max_tokens runs higher than the extracted-module callers
        # (which use 2-4k); the orchestrator may also have larger
        # message history. 120s gives the model breathing room
        # without letting a hung network freeze the Streamlit
        # thread for the SDK's default 10 minutes.
        client = anthropic.Anthropic(api_key=self._api_key, timeout=120.0)
        system_prompt = prompt_builder.build_system_prompt(conn)
        messages = self._load_messages_history(conn, conversation_id)
        tool_specs = [t.to_anthropic_spec() for t in tools.AGENT_TOOLS]
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            tools=tool_specs,
            messages=messages,
        )
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        return ("\n\n".join(text_parts), tool_calls, in_tok, out_tok)

    def _call_model_stream(
        self, conn: sqlite3.Connection, *, conversation_id: int
    ) -> ModelStream:
        """Stream one Anthropic round and return its accumulated message."""
        if type(self)._call_model is not AgentClient._call_model:
            assistant_text, tool_calls, in_tok, out_tok = self._call_model(
                conn, conversation_id=conversation_id
            )
            if assistant_text:
                yield ("text_delta", {"text": assistant_text})
            return (assistant_text, tool_calls, in_tok, out_tok)

        import anthropic  # local import keeps the offline path cheap

        client = anthropic.Anthropic(api_key=self._api_key, timeout=120.0)
        system_prompt = prompt_builder.build_system_prompt(conn)
        messages = self._load_messages_history(conn, conversation_id)
        tool_specs = [t.to_anthropic_spec() for t in tools.AGENT_TOOLS]
        with client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            tools=tool_specs,
            messages=messages,
        ) as stream:
            for event in stream:
                event_type = getattr(event, "type", None)
                if event_type == "text":
                    yield ("text_delta", {"text": getattr(event, "text", "")})
                elif event_type == "thinking":
                    thinking = getattr(event, "thinking", "")
                    if thinking:
                        yield ("thinking_delta", {"text": thinking})
                elif event_type == "input_json":
                    yield (
                        "tool_input_delta",
                        {
                            "partial_json": getattr(event, "partial_json", ""),
                            "snapshot": getattr(event, "snapshot", ""),
                        },
                    )
                elif event_type in {"content_block_start", "content_block_stop"}:
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", None) == "tool_use":
                        block_name = getattr(block, "name", None)
                        safe_name = (
                            block_name.strip()
                            if isinstance(block_name, str) and block_name.strip()
                            else INVALID_TOOL_CALL_LABEL
                        )
                        block_input = getattr(block, "input", {})
                        if block_input is None:
                            block_input = {}
                        if not isinstance(block_input, Mapping):
                            block_input = {}
                        yield (
                            "tool_call",
                            {
                                "id": getattr(block, "id", None),
                                "name": safe_name,
                                "input": block_input,
                                "status": (
                                    "requested"
                                    if event_type == "content_block_stop"
                                    else "forming"
                                ),
                            },
                        )
            final = stream.get_final_message()

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in final.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        usage = getattr(final, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        return ("\n\n".join(text_parts), tool_calls, in_tok, out_tok)

    def _load_messages_history(
        self, conn: sqlite3.Connection, conversation_id: int
    ) -> list[dict[str, Any]]:
        """Convert agent_messages rows into the Anthropic SDK message format."""
        rows = conn.execute(
            """
            SELECT role, content, tool_calls_json, tool_call_id
            FROM agent_messages
            WHERE conversation_id = ?
              AND role IN ('user', 'assistant', 'tool_result')
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()
        history: list[dict[str, Any]] = []
        for r in rows:
            role = r["role"]
            if role == "tool_result":
                history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r["tool_call_id"],
                                "content": r["content"],
                            }
                        ],
                    }
                )
            elif role == "assistant" and r["tool_calls_json"]:
                blocks: list[dict[str, Any]] = []
                if r["content"]:
                    blocks.append({"type": "text", "text": r["content"]})
                try:
                    raw_tool_calls = json.loads(r["tool_calls_json"])
                except (TypeError, json.JSONDecodeError):
                    raw_tool_calls = []
                for tc in _normalize_tool_calls(raw_tool_calls):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": tc["name"],
                            "input": tc["input"],
                        }
                    )
                history.append({"role": "assistant", "content": blocks})
            else:
                history.append({"role": role, "content": r["content"]})
        return history
