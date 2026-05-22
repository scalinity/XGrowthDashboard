"""Confidence labels (§28.14) — extraction, untagged-claim detection,
tie-breaking, IWH humility-penalty wiring, and the §24 weekly-export
blocker.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.agent import confidence_patterns, session


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def test_extract_finds_every_valid_tag() -> None:
    text = (
        "The build lane gained 12 followers <confidence>fact</confidence>. "
        "This will likely keep accelerating <confidence>speculation</confidence>. "
        "The hook style probably helped <confidence>inference</confidence>."
    )
    labels = session.extract_confidence_labels(text)
    assert labels == ["fact", "speculation", "inference"]


def test_extract_ignores_unknown_labels() -> None:
    text = "claim <confidence>certain</confidence>"
    assert session.extract_confidence_labels(text) == []


def test_extract_is_case_insensitive_for_tag_name() -> None:
    text = "<Confidence>fact</Confidence>"
    assert session.extract_confidence_labels(text) == ["fact"]


# ---------------------------------------------------------------------------
# Dominant-label tie-breaking — pinned per spec:
# speculation > inference > mixed > fact.
# ---------------------------------------------------------------------------
def test_dominant_simple_majority() -> None:
    assert session.dominant_confidence_label(["fact", "fact", "inference"]) == "fact"


def test_dominant_tie_break_prefers_speculation() -> None:
    assert session.dominant_confidence_label(["fact", "speculation"]) == "speculation"


def test_dominant_tie_break_chain() -> None:
    # 1 of each → speculation wins.
    assert (
        session.dominant_confidence_label(["fact", "inference", "speculation", "mixed"])
        == "speculation"
    )
    # 2-vs-2 between inference and mixed → inference wins (it's ahead in
    # _TIE_BREAK_ORDER).
    assert (
        session.dominant_confidence_label(["inference", "inference", "mixed", "mixed"])
        == "inference"
    )


def test_dominant_none_when_empty() -> None:
    assert session.dominant_confidence_label([]) is None


# ---------------------------------------------------------------------------
# Untagged-claim detection
# ---------------------------------------------------------------------------
def test_untagged_claim_counts_when_no_tag_nearby() -> None:
    text = "The build lane is the winner of the week."
    assert session.detect_untagged_claims(text) == 1


def test_tagged_claim_with_adjacent_confidence_is_excused() -> None:
    text = "Self lane outperformed last week <confidence>fact</confidence>."
    assert session.detect_untagged_claims(text) == 0


def test_untagged_when_tag_too_far_away() -> None:
    # Tag exists, but >80 chars after the claim.
    filler = "x" * 200
    text = (
        "The build lane is the winner of the week. " + filler + " <confidence>fact</confidence>"
    )
    assert session.detect_untagged_claims(text) >= 1


def test_multiple_patterns_in_one_message() -> None:
    text = (
        "Gained 12 followers and the data shows this caused the lift in the self lane."
    )
    # gained-N + data shows + caused = at least 3 untagged claims.
    assert session.detect_untagged_claims(text) >= 3


def test_no_analytical_claims_yields_zero() -> None:
    assert session.detect_untagged_claims("Drafting a post about kitchens.") == 0


def test_analytical_patterns_inventory() -> None:
    # Defensive lock: the regex inventory must include the load-bearing
    # categories the spec calls out.
    names = {p.name for p in confidence_patterns.ANALYTICAL_PATTERNS}
    for required in (
        "percentage_change",
        "lane_winner",
        "outperformed",
        "caused_by",
        "data_shows",
    ):
        assert required in names


# ---------------------------------------------------------------------------
# decide_save_or_revise — humility penalty wired in.
# ---------------------------------------------------------------------------
_VALID_DRAFT = "Three failed dinner attempts before 7pm. Stir scanned the fridge."


def _assistant_with_iwh(score: int, suffix: str = "") -> str:
    return (
        "<iwh_self_score>"
        + json.dumps({"intelligence": score, "wisdom": score, "humility": score})
        + f"</iwh_self_score>\n{suffix}"
    )


def test_decision_save_when_no_untagged_claims(db_conn: sqlite3.Connection) -> None:
    msg = _assistant_with_iwh(3, suffix="Drafting cleanly with no analytical claims.")
    decision = session.decide_save_or_revise(
        db_conn,
        assistant_text=msg,
        draft_text=_VALID_DRAFT,
        current_attempt_index=1,
    )
    assert decision.action == "save"
    assert decision.untagged_analytical_claims == 0
    assert decision.confidence_label is None


def test_decision_drops_humility_on_untagged_claims(db_conn: sqlite3.Connection) -> None:
    # Three untagged analytical claims → humility goes from 2 to 0 → fail.
    msg = _assistant_with_iwh(
        2,
        suffix=(
            "The build lane is the winner. Self lane outperformed last week. "
            "This caused the follower jump."
        ),
    )
    decision = session.decide_save_or_revise(
        db_conn,
        assistant_text=msg,
        draft_text=_VALID_DRAFT,
        current_attempt_index=1,
    )
    assert decision.action in ("revise", "refuse")
    assert decision.untagged_analytical_claims >= 3
    # Rationale namedrops the untagged-claims reason explicitly.
    assert "untagged" in decision.rationale.lower()


def test_decision_carries_dominant_label(db_conn: sqlite3.Connection) -> None:
    msg = _assistant_with_iwh(
        3,
        suffix=(
            "Lane data is in the tool result <confidence>fact</confidence>. "
            "I think the next post should target build <confidence>speculation</confidence>."
        ),
    )
    decision = session.decide_save_or_revise(
        db_conn,
        assistant_text=msg,
        draft_text=_VALID_DRAFT,
        current_attempt_index=1,
    )
    # Tie-break: 1 fact + 1 speculation → speculation wins.
    assert decision.confidence_label == "speculation"


# ---------------------------------------------------------------------------
# Weekly-export speculation blocker
# ---------------------------------------------------------------------------
def _make_weekly_review_row(conn: sqlite3.Connection, *, week_start: str) -> None:
    """Insert a row satisfying the §16 exporter's pre-checks."""
    from datetime import date as _d, timedelta as _t
    start = _d.fromisoformat(week_start)
    end = (start + _t(days=6)).isoformat()
    conn.execute(
        """
        INSERT INTO weekly_reviews
          (week_start_date, week_end_date, what_moved, what_got_stuck,
           counterfactual_note, lesson, next_week_experiment)
        VALUES (?, ?, 'mvt', 'stk',
                'real counterfactual text', 'lesson', 'next-week exp')
        """,
        (week_start, end),
    )


def _insert_conversation(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "INSERT INTO agent_conversations (title) VALUES ('t') RETURNING id"
    ).fetchone()
    return int(row[0])


def _insert_agent_message(
    conn: sqlite3.Connection, *, conv_id: int, when_date: str, confidence_label: str | None
) -> None:
    conn.execute(
        """
        INSERT INTO agent_messages
          (conversation_id, role, content, created_at_utc, confidence_label)
        VALUES (?, 'assistant', 'msg', ?, ?)
        """,
        (conv_id, f"{when_date}T12:00:00Z", confidence_label),
    )


def test_export_blocked_when_speculation_unacknowledged(
    db_conn: sqlite3.Connection, tmp_path
) -> None:
    from app.exports import markdown_weekly

    # Pick a deterministic Monday → ISO week.
    week_start = "2026-05-18"  # Mon
    week_iso = "2026-W21"
    _make_weekly_review_row(db_conn, week_start=week_start)

    conv_id = _insert_conversation(db_conn)
    _insert_agent_message(
        db_conn,
        conv_id=conv_id,
        when_date="2026-05-20",  # inside the week
        confidence_label="speculation",
    )

    with pytest.raises(markdown_weekly.SpeculationLabelBlocked) as exc:
        markdown_weekly.export_weekly_report(
            week_iso, output_path=tmp_path / "wr.md", conn=db_conn
        )
    assert exc.value.count == 1


def test_export_passes_when_ack_flag_set(
    db_conn: sqlite3.Connection, tmp_path
) -> None:
    from app.exports import markdown_weekly

    week_start = "2026-05-18"
    week_iso = "2026-W21"
    _make_weekly_review_row(db_conn, week_start=week_start)
    conv_id = _insert_conversation(db_conn)
    _insert_agent_message(
        db_conn,
        conv_id=conv_id,
        when_date="2026-05-20",
        confidence_label="speculation",
    )
    # Ack flag set → export proceeds.
    db_conn.execute(
        "INSERT INTO settings (key, value_json, note) VALUES (?, 'true', 'test ack')",
        (f"weekly_review_speculation_ack_{week_iso}",),
    )
    result = markdown_weekly.export_weekly_report(
        week_iso, output_path=tmp_path / "wr.md", conn=db_conn
    )
    assert result.path.exists()


def test_export_passes_when_no_speculation_messages(
    db_conn: sqlite3.Connection, tmp_path
) -> None:
    from app.exports import markdown_weekly

    week_iso = "2026-W21"
    _make_weekly_review_row(db_conn, week_start="2026-05-18")
    # No speculation messages → exporter runs.
    result = markdown_weekly.export_weekly_report(
        week_iso, output_path=tmp_path / "wr.md", conn=db_conn
    )
    assert result.path.exists()
