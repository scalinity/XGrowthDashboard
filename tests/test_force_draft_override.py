"""Phase 7 / §29.10 / §29.7 — force-draft override audit tests.

The Queue UI's force-draft button is the only path that can write
``reply_targets.force_drafted=1``. Three load-bearing invariants:

1. The override writes a 'data'-category audit_logs row with
   event_type='lint_force_drafted' carrying the reason in details_json.
2. An empty reason is rejected — the UI's text input has a non-empty
   validation guard (mirrored here via the DB-layer write contract).
3. After a successful override, the candidate's lint_blocked column
   is UNCHANGED (the lint result stays as-is; the override is a Daniel-
   level escape hatch, not a reclassification).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.agent import audit_log
from app.db import apply_migrations, connect, transaction
from scripts.seed_settings import seed_settings


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "force_draft.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    yield conn
    conn.close()


def _seed_blocked_candidate(conn: sqlite3.Connection) -> int:
    """Insert a reply_targets row already in the lint_blocked=1 state."""
    classification = json.dumps(
        {
            "ragebait": True,
            "meme_with_no_serious_reply_path": False,
            "low_quality_reply_thread": False,
            "hijacking_required_to_mention_stir": False,
            "rationale": "us-vs-them framing",
        }
    )
    cur = conn.execute(
        """
        INSERT INTO reply_targets
          (discovered_via, target_post_url, target_author_handle,
           target_text, lint_thread_classification_json, lint_category,
           lint_blocked, last_checked_at_utc)
        VALUES ('manual', 'https://x.com/x/status/1', '@author',
                'unpopular opinion: ...',
                ?, 'ragebait', 1, date('now'))
        """,
        (classification,),
    )
    return int(cur.lastrowid)


def _apply_override(
    conn: sqlite3.Connection, rt_id: int, reason: str
) -> bool:
    """Mirror the Queue UI's transactional write. Returns True if applied,
    False if the reason was empty (validation rejects)."""
    if not (reason or "").strip():
        return False
    with transaction(conn):
        conn.execute(
            """
            UPDATE reply_targets
               SET force_drafted = 1,
                   force_drafted_reason = ?
             WHERE id = ?
            """,
            (reason.strip(), rt_id),
        )
        # Need to read lint_category for the audit row.
        lint_cat = conn.execute(
            "SELECT lint_category FROM reply_targets WHERE id = ?", (rt_id,)
        ).fetchone()[0]
        audit_log.log(
            conn,
            event_category="data",
            event_type="lint_force_drafted",
            target_type="reply_target",
            target_id=str(rt_id),
            details={
                "reply_target_id": rt_id,
                "lint_category": lint_cat,
                "reason": reason.strip(),
            },
            success=True,
        )
    return True


def test_override_with_reason_writes_audit_row(db_conn: sqlite3.Connection) -> None:
    rt_id = _seed_blocked_candidate(db_conn)
    applied = _apply_override(
        db_conn, rt_id, "Daniel knows the author and the lint flagged a meta-post"
    )
    assert applied is True
    row = db_conn.execute(
        "SELECT force_drafted, force_drafted_reason FROM reply_targets WHERE id = ?",
        (rt_id,),
    ).fetchone()
    assert row["force_drafted"] == 1
    assert "Daniel knows the author" in row["force_drafted_reason"]
    audit = db_conn.execute(
        "SELECT event_category, event_type, target_type, target_id, details_json "
        "FROM audit_logs WHERE event_type = 'lint_force_drafted' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit["event_category"] == "data"
    assert audit["target_type"] == "reply_target"
    assert audit["target_id"] == str(rt_id)
    details = json.loads(audit["details_json"])
    assert details["lint_category"] == "ragebait"
    assert details["reply_target_id"] == rt_id
    assert "Daniel knows the author" in details["reason"]


def test_empty_reason_is_rejected(db_conn: sqlite3.Connection) -> None:
    rt_id = _seed_blocked_candidate(db_conn)
    # All-whitespace reasons are equivalent to empty.
    for empty in ("", "   ", "\n\t\n"):
        applied = _apply_override(db_conn, rt_id, empty)
        assert applied is False
    row = db_conn.execute(
        "SELECT force_drafted, force_drafted_reason FROM reply_targets WHERE id = ?",
        (rt_id,),
    ).fetchone()
    assert row["force_drafted"] == 0
    assert row["force_drafted_reason"] is None
    n_audit = db_conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE event_type = 'lint_force_drafted'"
    ).fetchone()[0]
    assert n_audit == 0


def test_override_does_not_clear_lint_blocked(db_conn: sqlite3.Connection) -> None:
    """The override is an escape hatch, not a reclassification. lint_blocked
    stays at 1 so the Queue can render 'overridden' history later."""
    rt_id = _seed_blocked_candidate(db_conn)
    _apply_override(db_conn, rt_id, "knowing override")
    row = db_conn.execute(
        "SELECT lint_blocked, lint_category, force_drafted FROM reply_targets WHERE id = ?",
        (rt_id,),
    ).fetchone()
    assert row["lint_blocked"] == 1
    assert row["lint_category"] == "ragebait"
    assert row["force_drafted"] == 1
