"""Agent Chat — spec.md §14.8.

Dedicated conversational interface for strategic distribution work. The
console-room counterpart to the read-only gauges in Today / Next Rep /
Content Performance.

Architecture:

* The chat surface is ``st.chat_message`` + ``st.chat_input`` plumbing.
  Tool-call expanders use ``app.components.theme.tool_call_block``.
* The sidebar carries the cost meter (§28.6), the IWH meter (§28.2 rule
  #13) when an active draft is in scope, and the past-conversations list.
* The publish modal lives inline (Streamlit ``st.dialog`` if available,
  otherwise a session-state-driven page block). Mint → atomic publish →
  callout with intent URL.

Side-effects discipline (CLAUDE.md): all DB reads happen inside the
``open_connection`` block; nothing is cached between reruns. The active
conversation id lives in ``st.session_state['agent_conversation_id']``;
the publish-modal state machine in ``st.session_state['publish_modal']``.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent import (
    _internal_tools,
    confirmation,
    cost,
    recovery,
    session,
)


# W20: cache detect_orphans across reruns so the chat-page banner +
# Settings panel don't both run a full-table SELECT on every rerun.
# Keyed by the DB path so AppTest's tmp DB doesn't collide with the
# default DB across test runs in the same process.
@st.cache_data(ttl=5, show_spinner=False)
def _cached_detect_orphans(_db_path: str) -> list[dict]:
    """Cache wrapper around recovery.detect_orphans for chat-page reads.

    Returns a list of dicts (cache-friendly) rather than OrphanPost
    dataclass instances — pages only read .post_id / .text / .publish_
    method, so the dict shape suffices and serializes cleanly under
    st.cache_data.
    """
    from app.db import connect as _connect
    _conn = _connect(_db_path)
    try:
        return [
            {
                "post_id": o.post_id,
                "text": o.text,
                "published_to_x_at": o.published_to_x_at,
                "publish_attempt_count": o.publish_attempt_count,
                "publish_method": o.publish_method,
            }
            for o in recovery.detect_orphans(_conn)
        ]
    finally:
        _conn.close()
from app.agent.client import (
    AgentClient,
    start_conversation,
)
from app.components.theme import (
    PALETTE,
    apply_theme,
    callout,
    console_log_row,
    cost_meter,
    hairline,
    iwh_meter,
    kicker,
    tool_call_block,
)
from app.pages import open_connection


# ---------------------------------------------------------------------------
# Session-state helpers.
# ---------------------------------------------------------------------------
def _bootstrap_state() -> None:
    st.session_state.setdefault("agent_conversation_id", None)
    st.session_state.setdefault("agent_context_seed", None)
    st.session_state.setdefault("publish_modal", None)
    st.session_state.setdefault("publish_result", None)


def _format_timestamp_short(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        return dt.astimezone().strftime("%a %H:%M")
    except (ValueError, TypeError):
        return iso_str[:16]


# ---------------------------------------------------------------------------
# Sidebar.
# ---------------------------------------------------------------------------
def _render_sidebar(conn) -> None:
    with st.sidebar:
        kicker("growth-agent")
        st.markdown(
            f"<div style='font-family: IBM Plex Sans, sans-serif; color: {PALETTE['bone_dim']}; "
            f"font-size: 0.85rem; margin-bottom: 0.8rem;'>"
            f"Open-ended chat for distribution work. Inline buttons in other "
            f"views jump here with context.</div>",
            unsafe_allow_html=True,
        )

        # Cost meter — always visible at the top.
        kicker("month-to-date spend")
        mtd = cost.month_to_date_spend_usd(conn)
        cap = cost.get_monthly_ceiling_usd(conn)
        cost_meter(mtd, cap)
        hairline()

        # IWH meter — only meaningful when there's an active draft in the
        # current conversation. We look up the latest draft for this conv.
        conv_id = st.session_state.get("agent_conversation_id")
        latest_draft = None
        if conv_id is not None:
            latest_draft = conn.execute(
                """
                SELECT id, iwh_attempt_index, voice_self_score, status
                FROM agent_drafts
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (conv_id,),
            ).fetchone()
        if latest_draft is not None:
            kicker("iwh self-score · latest draft")
            scores = {"intelligence": 0, "wisdom": 0, "humility": 0}
            if latest_draft["voice_self_score"]:
                try:
                    scores.update(json.loads(latest_draft["voice_self_score"]))
                except (json.JSONDecodeError, TypeError):
                    pass
            iwh_meter(
                int(scores.get("intelligence", 0)),
                int(scores.get("wisdom", 0)),
                int(scores.get("humility", 0)),
            )
            max_attempts = session.get_iwh_max_revision_attempts(conn)
            st.markdown(
                f"<div class='numeric' style='font-size: 0.85rem; color: {PALETTE['bone_dim']};'>"
                f"attempt {latest_draft['iwh_attempt_index']} / {max_attempts}"
                f"</div>",
                unsafe_allow_html=True,
            )
            hairline()

        # Past sessions.
        kicker("sessions")
        if st.button("+ new session", use_container_width=True):
            st.session_state.agent_conversation_id = None
            st.rerun()
        rows = conn.execute(
            """
            SELECT id, title, context_seed, last_message_at_utc, message_count
            FROM agent_conversations
            ORDER BY COALESCE(last_message_at_utc, started_at_utc) DESC
            LIMIT 8
            """
        ).fetchall()
        if not rows:
            st.markdown(
                "<div class='faint' style='font-size: 0.82rem;'>"
                "No conversations yet. Send a message to start one."
                "</div>",
                unsafe_allow_html=True,
            )
        for row in rows:
            title = row["title"] or row["context_seed"] or f"session #{row['id']}"
            kind = row["context_seed"] or "chat"
            active = row["id"] == st.session_state.get("agent_conversation_id")
            console_log_row(
                timestamp=_format_timestamp_short(row["last_message_at_utc"]),
                kind=kind.upper(),
                title=title,
                active=active,
            )
            if st.button(
                f"open #{row['id']}",
                key=f"open_session_{row['id']}",
                use_container_width=True,
            ):
                st.session_state.agent_conversation_id = int(row["id"])
                st.rerun()


# ---------------------------------------------------------------------------
# Chat surface.
# ---------------------------------------------------------------------------
def _render_history(conn, conversation_id: int) -> None:
    rows = conn.execute(
        """
        SELECT id, role, content, tool_calls_json
        FROM agent_messages
        WHERE conversation_id = ? AND role IN ('user', 'assistant', 'tool_result')
        ORDER BY id ASC
        """,
        (conversation_id,),
    ).fetchall()
    last_assistant_message_id: int | None = None
    for row in rows:
        role = row["role"]
        if role == "tool_result":
            # Skip — surfaced under the preceding assistant turn.
            continue
        with st.chat_message("assistant" if role == "assistant" else "user"):
            st.markdown(row["content"] or "")
            if role == "assistant" and row["tool_calls_json"]:
                last_assistant_message_id = int(row["id"])
                tool_calls = json.loads(row["tool_calls_json"])
                # Render each tool call + its persisted result.
                for tc in tool_calls:
                    result_row = conn.execute(
                        """
                        SELECT content FROM agent_messages
                        WHERE conversation_id = ? AND tool_call_id = ?
                        ORDER BY id ASC LIMIT 1
                        """,
                        (conversation_id, tc.get("id")),
                    ).fetchone()
                    result_body = (
                        result_row["content"] if result_row is not None else ""
                    )
                    try:
                        parsed = json.loads(result_body)
                        result_summary = _result_summary(tc.get("name", ""), parsed)
                    except (json.JSONDecodeError, TypeError):
                        parsed = result_body
                        result_summary = "result"
                    args_text = json.dumps(tc.get("input") or {}, indent=2)
                    result_text = (
                        json.dumps(parsed, indent=2)
                        if not isinstance(parsed, str)
                        else parsed
                    )
                    tool_call_block(
                        tc.get("name", "?"),
                        summary=result_summary,
                        body_md=(
                            f"**args**\n```json\n{args_text}\n```\n\n"
                            f"**result**\n```json\n{result_text}\n```"
                        ),
                    )
    # Inline draft action buttons for the latest assistant message that
    # produced a save_draft_* call.
    if last_assistant_message_id is not None:
        draft_row = conn.execute(
            """
            SELECT id, text, iwh_attempt_index, status
            FROM agent_drafts
            WHERE conversation_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        if draft_row is not None and draft_row["status"] == "proposed":
            _render_draft_actions(conn, draft_row, last_assistant_message_id)


def _result_summary(tool_name: str, parsed) -> str:
    """One-line summary rendered in the collapsed tool-call block."""
    if isinstance(parsed, dict):
        if "error" in parsed:
            return f"ERROR · {parsed['error']}"
        if tool_name == "query_dashboard_state":
            return f"slice={parsed.get('slice', '?')}"
        if tool_name == "get_recent_posts":
            return f"{parsed.get('count', '?')} rows"
        if tool_name == "get_lane_performance":
            return f"{len(parsed.get('lanes', []))} lanes"
        if tool_name == "save_draft_post" or tool_name == "save_draft_reply":
            return (
                f"draft #{parsed.get('draft_id')} · post #{parsed.get('post_id')} · "
                f"iwh attempt {parsed.get('iwh_attempt_index')}"
            )
    return "result"


def _render_draft_actions(conn, draft_row, message_id: int) -> None:
    """Inline draft-action button row: publish / discard."""
    st.markdown(
        f"<div class='callout' style='margin-top: 0.4rem;'>"
        f"<span class='kicker' style='margin-bottom: 0.2rem;'>draft #{draft_row['id']} · "
        f"attempt {draft_row['iwh_attempt_index']}</span>"
        f"<div style='font-family: Fraunces, serif; font-size: 1.1rem; "
        f"color: {PALETTE['bone']}; margin-top: 0.2rem;'>"
        f"{draft_row['text']}</div></div>",
        unsafe_allow_html=True,
    )
    col_a, col_b, col_c = st.columns([2, 1, 1])
    with col_a:
        if st.button("publish to X", type="primary", key=f"pub_btn_{draft_row['id']}"):
            _open_publish_modal(
                conn,
                draft_id=int(draft_row["id"]),
                draft_text=draft_row["text"],
                message_id=message_id,
            )
    with col_b:
        if st.button("save & close", key=f"save_btn_{draft_row['id']}"):
            conn.execute(
                "UPDATE agent_drafts SET status='accepted_as_is' WHERE id = ?",
                (int(draft_row["id"]),),
            )
            st.rerun()
    with col_c:
        if st.button("discard", key=f"discard_btn_{draft_row['id']}"):
            conn.execute(
                "UPDATE agent_drafts SET status='rejected' WHERE id = ?",
                (int(draft_row["id"]),),
            )
            st.rerun()


# ---------------------------------------------------------------------------
# Publish modal — kicker, exact text re-display, char count, "type 'confirm'"
# field, two-button row.
#
# §28.10 + W4 contract: the raw confirmation_token MUST live ONLY in the
# click-handler's local stack frame. We achieve that by NOT minting the
# token in `_open_publish_modal` — only in the confirm-and-publish click
# handler, where it stays in a local variable for one synchronous call
# into _internal_tools.publish_post_to_x and is then dropped. Nothing
# token-shaped is ever written to st.session_state.
#
# The post_id + draft_text live in session_state (they're not secrets;
# they're already in the DB). Token minting is cheap, so re-opening the
# modal after expiry costs nothing.
# ---------------------------------------------------------------------------
def _open_publish_modal(conn, *, draft_id: int, draft_text: str, message_id: int) -> None:
    post_id_row = conn.execute(
        "SELECT id FROM posts WHERE agent_draft_id = ? ORDER BY id DESC LIMIT 1",
        (draft_id,),
    ).fetchone()
    if post_id_row is None:
        st.error(
            "Internal: agent_drafts row has no linked posts row. "
            "save_draft_post should have created one."
        )
        return
    post_id = int(post_id_row["id"])
    # No token mint here — see module-level note. The mint happens inside
    # the confirm-and-publish click handler in _render_publish_modal.
    st.session_state.publish_modal = {
        "draft_id": draft_id,
        "post_id": post_id,
        "text": draft_text,
        "message_id": message_id,
    }
    st.rerun()


def _render_publish_modal(conn) -> None:
    modal = st.session_state.get("publish_modal")
    if modal is None:
        return

    st.markdown("<hr class='hairline' />", unsafe_allow_html=True)
    kicker("publish to X")
    st.markdown(
        f"<div style='font-family: Fraunces, serif; font-size: 1.3rem; "
        f"color: {PALETTE['bone']}; padding: 1.2rem 0; max-width: 38rem;'>"
        f"{modal['text']}</div>",
        unsafe_allow_html=True,
    )
    char_count = len(modal["text"])
    char_color = (
        PALETTE["confidence_directional_bg"]
        if char_count > 280
        else PALETTE["bone_dim"]
    )
    st.markdown(
        f"<div class='numeric' style='font-size: 0.85rem; color: {char_color};'>"
        f"CHARACTERS · {char_count} / 280</div>",
        unsafe_allow_html=True,
    )

    st.markdown("<div style='margin: 1.2rem 0;'></div>", unsafe_allow_html=True)
    # Static TTL note in place of the prior animated countdown (C4 + W4
    # combined: removing the rerun loop + dropping the pre-minted token
    # means there's no live TTL to render. The actual mint happens
    # below; the six-check chain enforces server-side expiry).
    st.markdown(
        "<div class='kicker'>publish window</div>"
        "<div class='faint' style='font-size: 0.82rem;'>"
        "On confirm: a single-use sha256-hashed token is minted with a "
        "60-second TTL and immediately consumed by the publish call. "
        "The raw token never leaves this click-handler's local stack "
        "frame (§28.10). If you take longer than 60 s between minting "
        "and confirming, the server rejects the click and you can retry."
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("<div style='margin: 1.2rem 0;'></div>", unsafe_allow_html=True)
    confirm_text = st.text_input(
        "type 'confirm' to enable publish",
        key="publish_confirm_input",
        label_visibility="visible",
    )

    # Server-side belt to W10's suspenders: refuse confirm when over the
    # X cap so the publish_post_atomic length check doesn't have to fire.
    can_publish = (
        confirm_text.strip().lower() == "confirm"
        and char_count <= 280
    )
    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button(
            "confirm and publish",
            type="primary",
            disabled=not can_publish,
            key="publish_confirm_btn",
        ):
            # ---- Raw-token critical section — keep it tight. ----
            # mint() returns a MintedToken whose .raw_token lives ONLY in
            # this local variable. The publish call below consumes it in
            # a single synchronous Python call. After the call returns
            # the local goes out of scope and the raw value is unreachable.
            minted = confirmation.mint_confirmation_token(
                conn, post_id=modal["post_id"], draft_text=modal["text"]
            )
            result = _internal_tools.publish_post_to_x(
                conn,
                post_id=modal["post_id"],
                confirmation_token=minted.raw_token,
                message_id=modal["message_id"],
            )
            del minted  # explicit: the raw value is gone from this frame.
            # ---- End critical section ----
            st.session_state.publish_result = {
                "success": result.success,
                "intent_url": result.intent_url,
                "error": result.error,
            }
            st.session_state.publish_modal = None
            st.rerun()
    with col_b:
        if st.button("cancel", key="publish_cancel_btn"):
            st.session_state.publish_modal = None
            st.rerun()


def _render_publish_result() -> None:
    pr = st.session_state.get("publish_result")
    if pr is None:
        return
    if pr["success"]:
        callout(
            f"<em>Token consumed, post staged for manual clipboard.</em><br>"
            f"Open the X compose tab and paste the URL back into "
            f"'Mark posted' once the tweet is live:<br>"
            f"<a href='{pr['intent_url']}' target='_blank' "
            f"style='color: {PALETTE['phosphor']};'>{pr['intent_url']}</a>"
        )
    else:
        st.markdown(
            f"<div class='callout' style='border-left-color: "
            f"{PALETTE['confidence_directional_bg']};'>"
            f"<em>Publish failed:</em> {pr['error']}</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Page render.
# ---------------------------------------------------------------------------
def _render() -> None:
    st.title("Agent Chat")
    apply_theme()
    _bootstrap_state()
    conn = open_connection()
    try:
        # Crash-recovery banner — surfaced at the top of every chat session
        # until orphans are reconciled. Cached at 5s TTL so rapid-fire
        # reruns don't full-table-scan posts on every interaction.
        from app.db import DEFAULT_DB_PATH as _DEFAULT_DB_PATH
        orphans = _cached_detect_orphans(str(_DEFAULT_DB_PATH))
        if orphans:
            st.markdown(
                f"<div class='callout' style='border-left-color: "
                f"{PALETTE['confidence_directional_bg']};'>"
                f"<span class='kicker'>publish state unknown</span>"
                f"{len(orphans)} post(s) began publishing but didn't complete. "
                f"Reconcile via Settings → Growth Agent → Orphan recovery."
                f"</div>",
                unsafe_allow_html=True,
            )

        _render_sidebar(conn)

        # If a context seed was passed in by another view, pre-fill the input.
        prefilled_input = st.session_state.get("agent_context_seed_text")
        if prefilled_input:
            st.session_state["chat_input_value"] = prefilled_input
            st.session_state["agent_context_seed_text"] = None

        # Conversation lookup or bootstrap on first user message.
        conv_id = st.session_state.get("agent_conversation_id")

        if conv_id is not None:
            _render_history(conn, conv_id)

        _render_publish_modal(conn)
        _render_publish_result()
        # Clear publish_result after one render so it doesn't stick.
        if st.session_state.get("publish_result") is not None:
            st.session_state.publish_result = None

        client = AgentClient()
        if not client.is_available():
            callout(
                "<em>Growth Agent disabled.</em> Set <code>ANTHROPIC_API_KEY</code> "
                "in <code>.env</code>; see spec §28.8."
            )
            return

        # Chat input. Using st.chat_input keeps Streamlit's submit-on-Enter
        # affordance. We don't pre-fill it directly (Streamlit limitation) —
        # the seed is rendered as a caption above so Daniel knows what
        # context the agent will see.
        seed = st.session_state.get("agent_context_seed")
        if seed:
            st.caption(f"context seed: {seed}")
        user_text = st.chat_input("ask the agent")
        if user_text:
            try:
                if conv_id is None:
                    conv_id = start_conversation(
                        conn,
                        title=user_text[:60],
                        context_seed=seed,
                    )
                    st.session_state.agent_conversation_id = conv_id
                turn = client.send_message_sync(
                    conn, conversation_id=conv_id, user_text=user_text
                )
                if turn.error:
                    st.error(turn.error)
                # Clear the context seed once consumed.
                st.session_state.agent_context_seed = None
            except cost.MonthlyCostCeilingExceeded as exc:
                st.error(str(exc))
            st.rerun()
    finally:
        conn.close()


_render()
