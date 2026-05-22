"""Approval payload hash — user-visible enforcement (§28.15).

Tests the two new orchestrator helpers (`invalidate_unconsumed_tokens_for_post`
and `update_post_text_for_publish`) and the two-modal-race contract:
once Modal B mints a token for the same post, Modal A's prior token
must fail the six-check validation chain.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app.agent import confirmation


# ---------------------------------------------------------------------------
# _parse_db_timestamp — P58R-5: support every form that has ever landed in
# the column (space-separator legacy, T-separator, Z suffix, ±HH:MM offset).
# ---------------------------------------------------------------------------
def test_parse_db_timestamp_space_separator() -> None:
    parsed = confirmation._parse_db_timestamp("2026-05-22 14:00:00")
    assert parsed == datetime(2026, 5, 22, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_db_timestamp_t_separator() -> None:
    parsed = confirmation._parse_db_timestamp("2026-05-22T14:00:00")
    assert parsed == datetime(2026, 5, 22, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_db_timestamp_z_suffix() -> None:
    parsed = confirmation._parse_db_timestamp("2026-05-22T14:00:00Z")
    assert parsed == datetime(2026, 5, 22, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_db_timestamp_offset_normalizes_to_utc() -> None:
    # 14:00 in +05:00 → 09:00 UTC.
    parsed = confirmation._parse_db_timestamp("2026-05-22T14:00:00+05:00")
    assert parsed == datetime(2026, 5, 22, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_db_timestamp_with_fractional_seconds() -> None:
    parsed = confirmation._parse_db_timestamp("2026-05-22 14:00:00.123456")
    assert parsed.year == 2026 and parsed.tzinfo == timezone.utc


def _setup_draft_post(conn: sqlite3.Connection, text: str = "Draft v1") -> int:
    """Insert a posts row in 'draft' state suitable for the publish flow."""
    row = conn.execute(
        """
        INSERT INTO posts
          (created_date, text, type, posted_via, manual_confirmation_status)
        VALUES ('2026-05-22', ?, 'standalone', 'agent_assisted', 'draft')
        RETURNING id
        """,
        (text,),
    ).fetchone()
    return int(row[0])


def _setup_message(conn: sqlite3.Connection) -> int:
    conv_id = conn.execute(
        "INSERT INTO agent_conversations (title) VALUES ('t') RETURNING id"
    ).fetchone()[0]
    row = conn.execute(
        """
        INSERT INTO agent_messages (conversation_id, role, content)
        VALUES (?, 'assistant', 'draft message')
        RETURNING id
        """,
        (conv_id,),
    ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# update_post_text_for_publish
# ---------------------------------------------------------------------------
def test_update_post_text_writes_when_changed(db_conn: sqlite3.Connection) -> None:
    post_id = _setup_draft_post(db_conn, "Original draft text.")
    message_id = _setup_message(db_conn)
    changed = confirmation.update_post_text_for_publish(
        db_conn, post_id=post_id, new_text="Edited at modal time.", message_id=message_id
    )
    assert changed is True
    new_text = db_conn.execute(
        "SELECT text FROM posts WHERE id = ?", (post_id,)
    ).fetchone()[0]
    assert new_text == "Edited at modal time."
    # Audit row landed.
    audit = db_conn.execute(
        """
        SELECT tool_name, result_json, notes
        FROM agent_tool_calls
        WHERE message_id = ? AND tool_name = 'publish_modal_edit'
        """,
        (message_id,),
    ).fetchone()
    assert audit is not None
    assert audit["notes"] == "draft edited at modal time"
    import json as _json
    result = _json.loads(audit["result_json"])
    assert "pre_edit_hash" in result and "post_edit_hash" in result
    assert result["pre_edit_hash"] != result["post_edit_hash"]


def test_update_post_text_skips_when_unchanged(db_conn: sqlite3.Connection) -> None:
    post_id = _setup_draft_post(db_conn, "Same text.")
    message_id = _setup_message(db_conn)
    changed = confirmation.update_post_text_for_publish(
        db_conn, post_id=post_id, new_text="Same text.", message_id=message_id
    )
    assert changed is False
    audit_count = db_conn.execute(
        "SELECT COUNT(*) FROM agent_tool_calls WHERE tool_name='publish_modal_edit'"
    ).fetchone()[0]
    assert audit_count == 0


# ---------------------------------------------------------------------------
# invalidate_unconsumed_tokens_for_post
# ---------------------------------------------------------------------------
def test_invalidate_kills_unconsumed_tokens(db_conn: sqlite3.Connection) -> None:
    post_id = _setup_draft_post(db_conn)
    minted_a = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="Draft v1"
    )
    minted_b = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="Draft v1"
    )
    affected = confirmation.invalidate_unconsumed_tokens_for_post(
        db_conn, post_id=post_id
    )
    # Both unconsumed tokens were expired.
    assert affected == 2

    # Validating either token now raises ExpiredTokenError (or another
    # check that surfaces the kill — either way it's not a successful
    # consume).
    with pytest.raises(confirmation.ConfirmationTokenError):
        confirmation.validate_and_consume_token(
            db_conn, post_id=post_id, raw_token=minted_a.raw_token
        )
    with pytest.raises(confirmation.ConfirmationTokenError):
        confirmation.validate_and_consume_token(
            db_conn, post_id=post_id, raw_token=minted_b.raw_token
        )


def test_invalidate_leaves_consumed_tokens_alone(db_conn: sqlite3.Connection) -> None:
    post_id = _setup_draft_post(db_conn)
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="Draft v1"
    )
    # Consume it first.
    confirmation.validate_and_consume_token(
        db_conn, post_id=post_id, raw_token=minted.raw_token
    )
    # consumed_at_utc is set; expires_at_utc should be untouched.
    consumed_before = db_conn.execute(
        "SELECT consumed_at_utc, expires_at_utc FROM publish_confirmation_tokens "
        "WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    affected = confirmation.invalidate_unconsumed_tokens_for_post(
        db_conn, post_id=post_id
    )
    assert affected == 0
    consumed_after = db_conn.execute(
        "SELECT consumed_at_utc, expires_at_utc FROM publish_confirmation_tokens "
        "WHERE id = ?",
        (minted.token_id,),
    ).fetchone()
    assert consumed_before["expires_at_utc"] == consumed_after["expires_at_utc"]


# ---------------------------------------------------------------------------
# Two-modal race — Modal B's mint must invalidate Modal A's token.
# This pins the spec contract: "the second token-mint invalidates the
# first by construction."
# ---------------------------------------------------------------------------
def test_two_modal_race_modal_a_token_fails_after_modal_b_mint(
    db_conn: sqlite3.Connection,
) -> None:
    post_id = _setup_draft_post(db_conn, "Original draft.")
    # Modal A opens and Daniel mints a token.
    modal_a_token = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="Original draft."
    )
    # Modal B opens (e.g. second window). The §28.15 click-handler
    # invalidates the prior unconsumed token BEFORE minting a new one.
    confirmation.invalidate_unconsumed_tokens_for_post(db_conn, post_id=post_id)
    modal_b_token = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="Original draft."
    )

    # Modal A's token now fails the six-check chain (expired).
    with pytest.raises(confirmation.ExpiredTokenError):
        confirmation.validate_and_consume_token(
            db_conn, post_id=post_id, raw_token=modal_a_token.raw_token
        )
    # Modal B's token still works.
    result = confirmation.validate_and_consume_token(
        db_conn, post_id=post_id, raw_token=modal_b_token.raw_token
    )
    assert result.post_id == post_id
    assert result.token_id == modal_b_token.token_id


# ---------------------------------------------------------------------------
# End-to-end edit-then-mint — the §28.15 step 5 happy path.
# ---------------------------------------------------------------------------
def test_edit_then_mint_uses_post_edit_text_hash(db_conn: sqlite3.Connection) -> None:
    post_id = _setup_draft_post(db_conn, "Original draft text v1.")
    message_id = _setup_message(db_conn)
    edited = "Daniel edited this just before publishing."
    # Step 5: UPDATE posts SET text BEFORE mint.
    confirmation.update_post_text_for_publish(
        db_conn, post_id=post_id, new_text=edited, message_id=message_id
    )
    # Then mint against the new text.
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text=edited
    )
    # The stored draft_text_hash_at_issue matches the edited text.
    stored_hash = db_conn.execute(
        "SELECT draft_text_hash_at_issue FROM publish_confirmation_tokens WHERE id = ?",
        (minted.token_id,),
    ).fetchone()[0]
    assert stored_hash == confirmation.hash_draft_text(edited)
    # And validate-and-consume completes cleanly.
    consumed = confirmation.validate_and_consume_token(
        db_conn, post_id=post_id, raw_token=minted.raw_token
    )
    assert consumed.post_id == post_id


def test_edit_changes_invalidate_prior_mint_with_drift_error(
    db_conn: sqlite3.Connection,
) -> None:
    """If a prior token exists but Daniel edits the text AFTER minting it
    (without going through update_post_text_for_publish + invalidate),
    the §28.10 six-check chain catches the drift via DraftTextChangedError.
    Belt-and-suspenders: even when the §28.15 click-handler is bypassed,
    the underlying contract still rejects.
    """
    post_id = _setup_draft_post(db_conn, "Original.")
    minted = confirmation.mint_confirmation_token(
        db_conn, post_id=post_id, draft_text="Original."
    )
    db_conn.execute(
        "UPDATE posts SET text = ? WHERE id = ?", ("Edited later.", post_id)
    )
    with pytest.raises(confirmation.DraftTextChangedError):
        confirmation.validate_and_consume_token(
            db_conn, post_id=post_id, raw_token=minted.raw_token
        )
