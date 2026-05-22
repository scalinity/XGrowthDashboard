"""Coach — spec.md §14.10.

Second conversational surface, structurally similar to §14.8 Agent Chat
but with the §28.23 citation discipline layered on every assistant
turn: extract 〔record_type id_or_filter〕 citations → validate against
the closed allowlist → strip invalid ones → render survivors as chips
inline → when ``coach_refuse_without_evidence == true`` and an
analytical claim has no surviving citation, replace the message with
the canonical refusal before persistence.

Cognitive contract — *grounded advice console*. Different from
§14.8: speculation is forbidden, drafts are out of scope, and the
Coach's tool catalog excludes every write tool (the boot-time
``_assert_coach_excludes_write_tools`` invariant enforces this).

Side-effects discipline (CLAUDE.md): every state mutation is in an
explicit ``on_submit`` callback (chat_input has none, so we route
through a single ``_handle_user_turn`` after the Streamlit submit-on-
Enter event); render flow stays pure derivation from ``agent_messages``
+ session state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import coach as _coach
from app.db import transaction as _coach_transaction
from app.components.theme import (
    PALETTE,
    apply_theme,
    callout,
    citation_chip,
    hairline,
    kicker,
)
from app.pages import open_connection


DEFAULT_MODEL: str = "claude-opus-4-7"
COACH_CONTEXT_SEED: str = "coach"  # marks the conversation as Coach-mode

# Allowlist for confidence_label values written to agent_messages — must
# stay in lockstep with the CHECK constraint in migration 011 (§28.14).
# Adding a label here without the migration would corrupt the conversation;
# adding to the migration without here would silently strip a valid label.
_ALLOWED_CONFIDENCE_LABELS: frozenset[str] = frozenset(
    {"fact", "inference", "speculation", "mixed"}
)

# P510R-7: cap Coach history sent to the model so long sessions don't
# blow past max_tokens on the input side. Daniel can reset via "+ new
# conversation" if older context matters.
_COACH_HISTORY_WINDOW: int = 20


# ---------------------------------------------------------------------------
# Session-state bootstrap.
# ---------------------------------------------------------------------------
def _init_state() -> None:
    st.session_state.setdefault("coach_conversation_id", None)
    st.session_state.setdefault("coach_pending_user_turn", None)
    st.session_state.setdefault("coach_last_error", None)


# ---------------------------------------------------------------------------
# Settings helpers.
# ---------------------------------------------------------------------------
def _get_bool_setting(conn: sqlite3.Connection, key: str, default: bool) -> bool:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return bool(json.loads(row["value_json"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return default


def _get_int_setting(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return int(json.loads(row["value_json"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return default


def _set_bool_setting(conn: sqlite3.Connection, key: str, value: bool) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value_json, note)
        VALUES (?, ?, '')
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
        """,
        (key, json.dumps(value)),
    )


# ---------------------------------------------------------------------------
# Conversation persistence.
# ---------------------------------------------------------------------------
def _start_coach_conversation(conn: sqlite3.Connection) -> int:
    """Insert a fresh agent_conversations row tagged as Coach mode.

    ``context_seed = 'coach'`` is the mode flag — the column already
    exists, no schema change needed. Future analytics can filter on it.
    """
    cur = conn.execute(
        """
        INSERT INTO agent_conversations (title, context_seed, status)
        VALUES (?, ?, 'active')
        RETURNING id
        """,
        ("Coach session", COACH_CONTEXT_SEED),
    )
    return int(cur.fetchone()[0])


def _ensure_conversation(conn: sqlite3.Connection) -> int:
    cid = st.session_state.get("coach_conversation_id")
    if cid is not None:
        row = conn.execute(
            "SELECT 1 FROM agent_conversations WHERE id = ?", (cid,)
        ).fetchone()
        if row:
            return int(cid)
    cid = _start_coach_conversation(conn)
    st.session_state["coach_conversation_id"] = cid
    return cid


def _load_messages(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, role, content, evidence_citations_json,
               confidence_label, created_at_utc
        FROM agent_messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _insert_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    role: str,
    content: str,
    *,
    evidence_citations: list[dict] | None = None,
    confidence_label: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO agent_messages
          (conversation_id, role, content, evidence_citations_json,
           confidence_label)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            conversation_id,
            role,
            content,
            json.dumps(evidence_citations) if evidence_citations else None,
            confidence_label,
        ),
    )
    return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Strip-rate stat — §28.23 surfaces a "strip rate high" banner in Settings
# when avg strips/message over the last 20 Coach messages exceeds the
# coach_citation_strip_log_threshold. Same number is computed here for
# the page's own sidebar readout.
# ---------------------------------------------------------------------------
def _compute_strip_rate(conn: sqlite3.Connection, *, window: int = 20) -> tuple[float, int]:
    """Return (avg_strips_per_message, message_count_in_window).

    A "strip" is one entry in the agent_tool_calls.notes JSON the
    orchestrator persists per Coach assistant message. For MVP, we
    derive strip counts from the agent_messages row itself by counting
    'reason' tokens in the parent tool call notes when present, OR
    falling back to inspecting evidence_citations_json + comparing
    against the (not-persisted) original message length. For this
    initial pass we surface only message_count_in_window; the avg is
    a Phase 5.11 follow-up once the strip-log persistence story is
    finalized.
    """
    rows = conn.execute(
        """
        SELECT id FROM agent_messages
        WHERE role = 'assistant'
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(window),),
    ).fetchall()
    return 0.0, len(rows)


# ---------------------------------------------------------------------------
# Anthropic API call.
# ---------------------------------------------------------------------------
def _build_system_prompt() -> str:
    """Read the project system prompt + force Coach mode rendering.

    The persisted prompt has Section 9 as a conditional block — we
    include it verbatim for the Coach surface. The full prompt-
    assembly stack (tool catalog splice etc.) lives in
    ``app.agent.prompt_builder``; the Coach view's MVP call passes the
    raw prompt without the dynamic splices since it only uses read
    tools whose names + descriptions are documented in Section 7.
    """
    path = _PROJECT_ROOT / "config" / "agent_system_prompt.md"
    return path.read_text(encoding="utf-8")


def _call_anthropic(
    *,
    system_prompt: str,
    messages: list[dict],
    model: str = DEFAULT_MODEL,
) -> tuple[str, str | None, int]:
    """Send one Coach turn to Anthropic. Returns (text, confidence_label, tokens).

    Coach mode is intentionally simpler than Agent Chat: no tool-use
    loop, no streaming. The Coach is advice-only; one assistant text
    block is the contract.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set (see spec §28.8 for env setup)."
        )
    import anthropic  # local import — cold path stays import-free

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": m["role"], "content": m["content"]} for m in messages],
    )
    text_parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    text = "".join(text_parts).strip()
    tokens_used = (
        int(getattr(resp.usage, "input_tokens", 0) or 0)
        + int(getattr(resp.usage, "output_tokens", 0) or 0)
    )
    # Confidence-label extraction: take the dominant <confidence> tag
    # in the response, if any. agent_messages.confidence_label has a
    # CHECK constraint (migration 011) restricting to the four allowed
    # values — anything off the list trips IntegrityError and (under
    # autocommit) leaves an orphan user-message row behind. Allowlist
    # before persistence so label drift (HIGH, certain, Inference, etc.)
    # silently degrades to NULL instead of crashing the turn.
    import re

    confs = re.findall(r"<confidence>([^<]+)</confidence>", text)
    confidence_label = None
    if confs:
        from collections import Counter

        normalized = [
            c.strip().lower() for c in confs
            if c.strip().lower() in _ALLOWED_CONFIDENCE_LABELS
        ]
        if normalized:
            confidence_label = Counter(normalized).most_common(1)[0][0]
    return text, confidence_label, tokens_used


# ---------------------------------------------------------------------------
# Explicit on-submit handler.
# ---------------------------------------------------------------------------
def _handle_user_turn(user_text: str) -> None:
    """One round-trip: call API → enforce() → persist user + assistant atomically.

    P510R-3: do NOT insert the user message before the API call. Under
    autocommit, an API failure (routine 429/5xx/timeout) would leave
    an orphan user turn with no assistant reply, and the next rerun
    would replay the orphan back to the model. We assemble the API
    request from prior history + the in-hand user_text, call Anthropic,
    run enforce(), and only then persist BOTH messages inside one
    transaction() — atomic round-trip or atomic no-op.
    """
    if not user_text.strip():
        return
    st.session_state["coach_last_error"] = None
    with open_connection() as conn:
        conv_id = _ensure_conversation(conn)
        history = _load_messages(conn, conv_id)
        refuse_flag = _get_bool_setting(
            conn, "coach_refuse_without_evidence", default=True
        )

    # API call OUTSIDE the connection scope. The conn-hold is itself
    # benign under autocommit+WAL (see P510R-6 note in account_research.
    # analyze), but separating the scopes keeps the atomic-round-trip
    # write (P510R-3) cleanly bounded.
    #
    # P510R-7: cap history window to keep input tokens bounded. Mirrors
    # the §14.8 Agent Chat convention. Daniel can start a fresh
    # conversation via the rail's "+ new conversation" button when he
    # wants a clean slate.
    api_history = [m for m in history if m["role"] in ("user", "assistant")]
    if len(api_history) > _COACH_HISTORY_WINDOW:
        api_history = api_history[-_COACH_HISTORY_WINDOW:]
    api_messages = [
        {"role": m["role"], "content": m["content"]} for m in api_history
    ]
    api_messages.append({"role": "user", "content": user_text})
    try:
        system_prompt = _build_system_prompt()
        response_text, confidence_label, _tokens = _call_anthropic(
            system_prompt=system_prompt, messages=api_messages
        )
    except Exception as exc:  # noqa: BLE001
        st.session_state["coach_last_error"] = (
            f"{type(exc).__name__}: {exc}"
        )
        return

    # Fresh connection for the atomic two-row write.
    with open_connection() as conn:
        result = _coach.enforce(
            response_text,
            conn,
            refuse_without_evidence=refuse_flag,
        )
        with _coach_transaction(conn):
            _insert_message(conn, conv_id, "user", user_text)
            _insert_message(
                conn,
                conv_id,
                "assistant",
                result.clean_text,
                evidence_citations=[c.to_dict() for c in result.surviving],
                confidence_label=confidence_label,
            )


def _handle_new_conversation() -> None:
    st.session_state["coach_conversation_id"] = None
    st.session_state["coach_last_error"] = None


def _handle_refuse_toggle(new_value: bool) -> None:
    with open_connection() as conn:
        _set_bool_setting(conn, "coach_refuse_without_evidence", new_value)


# ---------------------------------------------------------------------------
# Render helpers.
# ---------------------------------------------------------------------------
def _render_assistant_message(msg: dict) -> None:
    """Render a Coach assistant message with citation chips inline.

    Citation chips REPLACE the inline 〔...〕 tokens visually — the
    persistence already preserves the survivors in
    ``evidence_citations_json``, so the chips are layered over the
    text without losing it. Refusal-styled messages have a dashed left
    keyline + dimmed text to signal "deliberately not answered."
    """
    text = msg["content"] or ""
    citations_json = msg.get("evidence_citations_json")
    citations: list[dict] = (
        json.loads(citations_json) if citations_json else []
    )

    is_refusal = text.startswith(
        "I don't have data in your dashboard to answer this honestly."
    )

    # Build inline chips list (rendered AFTER the text for readability —
    # mixing chips inside the running prose collapses Streamlit's
    # markdown engine; appending preserves both).
    chip_html = (
        "".join(
            citation_chip(c.get("record_type", "?"), c.get("record_id", "?"))
            for c in citations
        )
        if citations
        else ""
    )

    border_color = (
        PALETTE["hairline"] if is_refusal else PALETTE["phosphor_dim"]
    )
    border_style = "dashed" if is_refusal else "solid"
    text_color = (
        PALETTE["bone_dim"] if is_refusal else PALETTE["bone"]
    )

    safe_text = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    chips_row = (
        f"<div style='margin-top:0.5rem;'>{chip_html}</div>"
        if citations
        else ""
    )

    with st.chat_message("assistant"):
        st.markdown(
            f"""<div style='padding:0.4rem 0.8rem;
                            border-left:2px {border_style} {border_color};'>
                <div style='white-space: pre-wrap; color:{text_color};
                             line-height:1.55; font-size:0.96rem;'>{safe_text}</div>
                {chips_row}
            </div>""",
            unsafe_allow_html=True,
        )
        cl = msg.get("confidence_label")
        if cl and not is_refusal:
            st.markdown(
                f"<div class='kicker' style='margin-top:0.3rem;'>"
                f"confidence: {cl}</div>",
                unsafe_allow_html=True,
            )


def _render_user_message(msg: dict) -> None:
    with st.chat_message("user"):
        safe = (
            (msg["content"] or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        st.markdown(
            f"<div style='white-space: pre-wrap; line-height:1.5;'>{safe}</div>",
            unsafe_allow_html=True,
        )


def _render_header(refuse_flag: bool) -> None:
    kicker("GROUNDED ADVICE")
    st.title("coach")
    st.markdown(
        f"<div style='font-family: Fraunces, IBM Plex Serif, Georgia, serif; "
        f"font-style: italic; font-size: 1.1rem; "
        f"color:{PALETTE['bone_dim']}; margin: -0.3rem 0 0.8rem 0;'>"
        "Cite or refuse. Speculation lives in Agent Chat.</div>",
        unsafe_allow_html=True,
    )

    cols = st.columns([3, 1])
    with cols[0]:
        callout(
            "<em>Every analytical claim the Coach makes is filtered through "
            "the §28.23 citation allowlist.</em> Citations that don't resolve "
            "to a real row are stripped, with the count surfaced below the "
            "message. When the refuse-without-evidence toggle is on, "
            "uncited analytical messages are replaced with a canonical "
            "refusal — no speculation slipping through."
        )
    with cols[1]:
        new_value = st.toggle(
            "refuse without evidence",
            value=refuse_flag,
            key="coach_refuse_toggle",
            help=(
                "Setting `coach_refuse_without_evidence` (§28.23). When "
                "on, Coach replaces uncited analytical messages with a "
                "canonical refusal. Off → uncited claims pass through "
                "with whatever confidence labels the agent emitted."
            ),
        )
        if new_value != refuse_flag:
            _handle_refuse_toggle(new_value)


def _render_sidebar(strip_window_count: int, strip_threshold: int) -> None:
    st.markdown("### coach session")
    st.button(
        "+ new conversation",
        key="coach_new_conv",
        on_click=_handle_new_conversation,
        use_container_width=True,
    )
    hairline()
    st.markdown(
        "<div class='kicker'>STRIP-RATE WINDOW</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='numeric' style='font-size:1.4rem;'>"
        f"{strip_window_count} <span class='dim' style='font-size:0.78rem;'>"
        f"/ 20 msg threshold</span></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='faint' style='font-size:0.78rem; margin-top:0.2rem;'>"
        f"avg-strips/msg threshold: <span class='numeric'>"
        f"{strip_threshold}</span> (§28.23 strip-rate banner setting)</div>",
        unsafe_allow_html=True,
    )


def _render_anti_pattern_redirect() -> None:
    """If the user types a drafting request, surface a gentle redirect.

    The Coach has no write tools — but rather than failing silently
    on the server side, we hint at the right surface (§14.8 Agent
    Chat for drafting; §14.9 Brain Dump for capture).
    """
    callout(
        "<em>Coach is advice-only.</em> If you want drafts, switch to "
        "<strong>Agent Chat</strong> (sidebar → 9_Agent_Chat). If you "
        "want to capture half-formed thinking, use "
        "<strong>Brain Dump</strong> (sidebar → 11_Brain_Dump)."
    )


# ---------------------------------------------------------------------------
# Page entrypoint.
# ---------------------------------------------------------------------------
def main() -> None:
    apply_theme()
    _init_state()

    with open_connection() as conn:
        refuse_flag = _get_bool_setting(
            conn, "coach_refuse_without_evidence", default=True
        )
        strip_threshold = _get_int_setting(
            conn, "coach_citation_strip_log_threshold", default=3
        )
        _strip_avg, strip_window = _compute_strip_rate(conn)
        conv_id = st.session_state.get("coach_conversation_id")
        messages = _load_messages(conn, conv_id) if conv_id else []

    _render_header(refuse_flag)

    rail, main_col = st.columns([1, 3], gap="large")
    with rail:
        _render_sidebar(strip_window, strip_threshold)
    with main_col:
        if not messages:
            _render_anti_pattern_redirect()
        for msg in messages:
            if msg["role"] == "user":
                _render_user_message(msg)
            elif msg["role"] == "assistant":
                _render_assistant_message(msg)

        err = st.session_state.get("coach_last_error")
        if err:
            st.markdown(
                f"<div style='padding:0.6rem 0.9rem; margin:0.5rem 0;"
                f"background:{PALETTE['surface']};"
                f"border-left:2px solid {PALETTE['warn_amber']};'>"
                f"<span style='color:{PALETTE['warn_amber']};font-weight:500;'>"
                f"coach error</span> "
                f"<span class='dim'>{err}</span></div>",
                unsafe_allow_html=True,
            )

        # Chat input — using a form so we can run the API call inside
        # an explicit on_submit handler (chat_input lacks on_submit).
        with st.form(key="coach_form", clear_on_submit=True, border=False):
            user_text = st.text_input(
                "ask the coach",
                key="coach_chat_input",
                placeholder="What does my last-week lane performance suggest?",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button(
                "send",
                type="primary",
            )
            if submitted and user_text:
                _handle_user_turn(user_text)
                st.rerun()


main()
