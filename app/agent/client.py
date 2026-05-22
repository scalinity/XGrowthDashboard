"""AgentClient — Anthropic SDK wrapper (§28, §14.8).

The client:

  1. Loads the API key from ``.env`` (``ANTHROPIC_API_KEY``).
  2. Assembles the system prompt via ``prompt_builder.build_system_prompt``.
  3. Imports tool specs from ``app.agent.tools.AGENT_TOOLS`` ONLY — the
     publish tools in ``_internal_tools`` are never imported here.
  4. Enforces the §28.6 monthly cost ceiling before each round trip.
  5. Dispatches tool_use blocks to local handlers via ``dispatch_tool_call``.
  6. Persists every assistant message + tool call to the DB.

S11: at MVP the client is **synchronous-only** — ``send_message_sync``
performs the full round trip and persists the result in one call. A
streaming surface (``st.write_stream``-compatible iterator) is on the
roadmap for V1.1+ but is not implemented yet; the architecture already
separates the SDK boundary (``_call_model``) from the persistence path
so the streaming upgrade is a future iteration on the existing surface.

The publish tools deliberately have no path into this client. The
``dispatch_tool_call`` helper raises ``KeyError`` for any unknown name —
including the publish names — so even a hypothetical leak would fail
loudly rather than execute.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.agent import audit, cost, prompt_builder, session, tools

# §28.2 rule #12 + #13: every save_draft_* call MUST run through the
# orchestrator gate (IWH self-score parse + dark-pattern lint preflight)
# BEFORE the handler is invoked. The model can emit these tool_use blocks
# at any time; without the gate the entire revision/lint discipline this
# phase exists to enforce is dead code at runtime.
SAVE_DRAFT_TOOLS: frozenset[str] = frozenset(
    {"save_draft_post", "save_draft_reply"}
)


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
    tool_input: dict[str, Any],
    message_id: int,
    assistant_text: str = "",
    current_attempt_index: int = 1,
) -> dict[str, Any]:
    """Dispatch a tool_use block to its handler and log the call.

    Raises ``KeyError`` if the tool name is unknown — including any
    publish-tool name that somehow appears in a model response. The publish
    tools are not in AGENT_TOOLS, so a valid model response can never name
    them; the explicit KeyError is defense-in-depth against a corrupt SDK
    payload or a future leak.

    §28.2 rule #12 + #13 enforcement: for tool_name in SAVE_DRAFT_TOOLS,
    run the orchestrator gate (IWH score parse + dark-pattern lint preflight)
    against the parent assistant message's text BEFORE calling the handler.
    Refuse/revise outcomes short-circuit without writing to agent_drafts.
    The caller (AgentClient.send_message_sync) threads ``assistant_text``
    and ``current_attempt_index`` here.
    """
    start = datetime.now(timezone.utc)

    if tool_name in SAVE_DRAFT_TOOLS:
        # §28.2 rule #15 — niche must be defined. This runs BEFORE the
        # IWH/lint gate; a prompt-injected request to "skip the niche
        # check" cannot bypass this because niche_gate consults only the
        # settings rows, never assistant_text.
        n_gate = session.niche_gate(conn)
        if not n_gate.passed:
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=tool_name,
                arguments=tool_input,
                status="error",
                error_message=f"niche-gate refuse: {n_gate.rationale}",
                duration_ms=int(
                    (datetime.now(timezone.utc) - start).total_seconds() * 1000
                ),
                notes="niche-gate refused",
            )
            return {
                "tool_name": tool_name,
                "status": "error",
                "error": f"refused by niche gate: {n_gate.rationale}",
            }
        # Phase 5.9 / §28.18 — pass draft_kind + target_post_text so the
        # reply-quality lint runs in-band with the IWH/dark-pattern gate.
        _draft_kind = "reply" if tool_name == "save_draft_reply" else "standalone"
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
                tool_name=tool_name,
                arguments=tool_input,
                status="error",
                error_message=f"IWH refuse: {decision.rationale}",
                duration_ms=int(
                    (datetime.now(timezone.utc) - start).total_seconds() * 1000
                ),
                notes="iwh-gate refused",
            )
            return {
                "tool_name": tool_name,
                "status": "error",
                "error": f"refused by IWH gate: {decision.rationale}",
            }
        if decision.action == "revise":
            audit.log_tool_call(
                conn,
                message_id=message_id,
                tool_name=tool_name,
                arguments=tool_input,
                status="error",
                error_message=f"IWH revise: {decision.rationale}",
                duration_ms=int(
                    (datetime.now(timezone.utc) - start).total_seconds() * 1000
                ),
                notes="iwh-gate revise",
            )
            return {
                "tool_name": tool_name,
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
        if decision.reply_quality_result is not None:
            tool_input = {
                **tool_input,
                "reply_quality_lint_passed": decision.reply_quality_result.passed,
            }

    try:
        tool = tools.get_tool(tool_name)
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
            tool_name=tool_name,
            arguments=tool_input,
            status=audit_status,
            result=result,
            duration_ms=duration_ms,
        )
        return {"tool_name": tool_name, "result": result, "status": audit_status}
    except Exception as exc:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        audit.log_tool_call(
            conn,
            message_id=message_id,
            tool_name=tool_name,
            arguments=tool_input,
            status="error",
            error_message=f"{type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
        )
        return {
            "tool_name": tool_name,
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

        try:
            assistant_text, tool_calls, in_tok, out_tok = self._call_model(
                conn, conversation_id=conversation_id
            )
        except Exception as exc:
            turn.error = f"{type(exc).__name__}: {exc}"
            return turn

        # Estimate cost from token counts using the rate snapshot.
        estimate = cost.estimate_cost(
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
            rate_snapshot=estimate.rate_snapshot,
            tool_calls=tool_calls or None,
            confidence_label=_dominant_conf,
        )

        # Dispatch each tool_use block locally.
        dispatched: list[dict] = []
        for tc in tool_calls:
            tc_name = tc.get("name", "")
            tc_input = tc.get("input", {}) or {}
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
                tool_name=tc_name,
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
            # Persist tool_result message so the next turn has it in context.
            # Switch on `status` instead of truthy result — a legitimate empty
            # result ({} / []) is falsy and used to fall through to error,
            # which was None, persisted as the literal string "null" (W6).
            if result.get("status") == "success":
                content_payload = result.get("result")
            else:
                content_payload = result.get("error") or result.get("rationale") or ""
            append_message(
                conn,
                conversation_id=conversation_id,
                role="tool_result",
                content=json.dumps(content_payload, default=str),
                tool_call_id=tc.get("id"),
            )
        turn.assistant_text = assistant_text
        turn.tool_calls = dispatched
        turn.input_tokens = in_tok
        turn.output_tokens = out_tok
        turn.cost_usd = estimate.total_usd
        return turn

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
                for tc in json.loads(r["tool_calls_json"]):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": tc.get("name"),
                            "input": tc.get("input") or {},
                        }
                    )
                history.append({"role": "assistant", "content": blocks})
            else:
                history.append({"role": role, "content": r["content"]})
        return history
