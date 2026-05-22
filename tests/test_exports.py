"""Phase 5 export tests — spec.md §16 / §14.6 / §18.

Seven scenarios from the Phase 5 prompt, plus a few defensive guards.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from app.exports import (
    CounterfactualMissingError,
    UnknownTableError,
    export_database_to_json,
    export_table_to_csv,
    export_weekly_report,
)
from app.exports.allowlists import (
    ALLOWLISTS,
    POSTS_ALLOWLIST,
    columns_for_export,
    get_excluded_columns,
    get_opt_in_columns,
)


# ---------------------------------------------------------------------------
# helpers — seed minimal rows so the SELECTs return data.
# ---------------------------------------------------------------------------
def _insert_post(
    conn: sqlite3.Connection,
    *,
    pid: int = 1,
    x_post_id: str = "x-1",
    text: str = "hello world",
    created_date: str = "2026-05-18",
) -> None:
    conn.execute(
        """
        INSERT INTO posts (
            id, x_post_id, created_at_utc, created_date, text, url,
            type, conversation_id, in_reply_to_post_id, in_reply_to_user,
            posted_via, manual_confirmation_status, contains_link, expanded_urls_json,
            utm_source, utm_medium, utm_campaign, utm_content, utm_term
        ) VALUES (?, ?, '2026-05-18T12:00:00Z', ?, ?, 'https://x.com/dannyscalant/status/1',
                  'standalone', 'conv-1', NULL, NULL,
                  'manual', 'confirmed', 0, NULL,
                  'x', 'organic', 'spring_2026', NULL, NULL)
        """,
        (pid, x_post_id, created_date, text),
    )


def _insert_weekly_review(
    conn: sqlite3.Connection,
    *,
    week_start: str = "2026-05-18",
    week_end: str = "2026-05-24",
    counterfactual: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO weekly_reviews (
            week_start_date, week_end_date,
            followers_start, followers_end, follower_delta,
            posts_shipped, replies_shipped, reply_sessions_completed,
            daily_reps_days_completed, downloads, qualified_icp_testers,
            strongest_pillar, weakest_pillar,
            what_moved, what_got_stuck, lesson, next_week_experiment,
            counterfactual_note
        ) VALUES (?, ?, 61, 73, 12, 7, 30, 5, 6, 1, 0,
                  'cooking-truths', 'tool-pitch',
                  'replies → 3 follower bumps from working-parent accounts',
                  'site visits flat',
                  'reply quality matters more than count',
                  'try a 7-post thread on the meal-plan failure mode',
                  ?)
        """,
        (week_start, week_end, counterfactual),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Test 1 — CSV export uses allowlist default columns
# ---------------------------------------------------------------------------
def test_csv_export_uses_allowlist_default_columns(db_conn, tmp_path: Path) -> None:
    _insert_post(db_conn)
    out = tmp_path / "posts.csv"

    result = export_table_to_csv("posts", out, conn=db_conn)

    with out.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)

    assert header == POSTS_ALLOWLIST["default_columns"]
    assert len(rows) == 1
    assert rows[0][header.index("x_post_id")] == "x-1"
    assert result.row_count == 1
    assert result.columns == header


# ---------------------------------------------------------------------------
# Test 2 — opt_in flag appends opt_in_columns (header still default+opt_in,
#          even when opt_in_columns is empty at this phase).
# ---------------------------------------------------------------------------
def test_csv_export_opt_in_includes_opt_in_columns(db_conn, tmp_path: Path) -> None:
    _insert_post(db_conn)
    out = tmp_path / "posts_optin.csv"

    export_table_to_csv("posts", out, include_opt_in=True, conn=db_conn)

    with out.open(encoding="utf-8") as fh:
        header = next(csv.reader(fh))

    expected = POSTS_ALLOWLIST["default_columns"] + POSTS_ALLOWLIST["opt_in_columns"]
    assert header == expected


# ---------------------------------------------------------------------------
# Test 3 — excluded columns never appear, opt-in or not.
#
# Originally a single test interleaving three concerns; split into three
# single-concern tests per /review-2 🔵 S5 so a failure points at the
# specific guarantee that broke.
# ---------------------------------------------------------------------------
def test_csv_export_no_live_allowlist_collides_with_excluded(db_conn, tmp_path: Path) -> None:
    """Sanity: no live allowlist has any default/opt-in column also in
    excluded_columns. Catches a future copy-paste mistake at import time."""
    for table in ALLOWLISTS:
        assert all(
            c not in get_excluded_columns(table)
            for c in ALLOWLISTS[table]["default_columns"] + ALLOWLISTS[table]["opt_in_columns"]
        ), table


def test_csv_export_inconsistent_allowlist_fails_fast(db_conn, tmp_path: Path, monkeypatch) -> None:
    """A misconfigured allowlist (default/opt-in column also in excluded)
    must raise ValueError BEFORE the SELECT runs."""
    from app.exports import allowlists as al

    bad = {"default_columns": ["a", "secret_col"], "opt_in_columns": [], "excluded_columns": ["secret_col"]}
    monkeypatch.setitem(al.ALLOWLISTS, "__bad__", bad)
    with pytest.raises(ValueError, match="internally inconsistent"):
        columns_for_export("__bad__")


def test_csv_export_unknown_table_raises(db_conn, tmp_path: Path) -> None:
    """A genuinely missing table name surfaces UnknownTableError."""
    with pytest.raises(UnknownTableError):
        export_table_to_csv("posts_DOES_NOT_EXIST", tmp_path / "x.csv", conn=db_conn)


# ---------------------------------------------------------------------------
# Test 4 — Markdown weekly requires counterfactual
# ---------------------------------------------------------------------------
def test_markdown_weekly_requires_counterfactual(db_conn, tmp_path: Path) -> None:
    # No row at all → raises with no week_start_date attribute.
    with pytest.raises(CounterfactualMissingError) as exc_info_a:
        export_weekly_report("2026-W21", tmp_path / "a.md", conn=db_conn)
    assert exc_info_a.value.week_start_date is None

    # Row exists but counterfactual is empty string → raises.
    _insert_weekly_review(db_conn, counterfactual="")
    db_conn.commit()
    with pytest.raises(CounterfactualMissingError) as exc_info_b:
        export_weekly_report("2026-W21", tmp_path / "b.md", conn=db_conn)
    assert exc_info_b.value.week_start_date == "2026-05-18"

    # Whitespace-only also counts as empty.
    db_conn.execute(
        "UPDATE weekly_reviews SET counterfactual_note = '   \n  \t  ' WHERE week_start_date = '2026-05-18'"
    )
    db_conn.commit()
    with pytest.raises(CounterfactualMissingError):
        export_weekly_report("2026-W21", tmp_path / "c.md", conn=db_conn)

    # Filled in → succeeds and writes file.
    db_conn.execute(
        "UPDATE weekly_reviews SET counterfactual_note = ? WHERE week_start_date = '2026-05-18'",
        ("Growth might have come from cohort drift, not my posts.",),
    )
    db_conn.commit()
    result = export_weekly_report("2026-W21", tmp_path / "d.md", conn=db_conn)
    assert result.path.exists()
    assert result.path.read_text(encoding="utf-8").strip().startswith("# X Growth Weekly Review")


# ---------------------------------------------------------------------------
# Test 5 — generated Markdown includes the App-Store-gap label and the
#          counterfactual note verbatim.
# ---------------------------------------------------------------------------
def test_markdown_weekly_includes_app_store_gap_label(db_conn, tmp_path: Path) -> None:
    _insert_weekly_review(
        db_conn,
        counterfactual="Working-parent cohort discovered Stir via Reddit threads two weeks ago.",
    )
    out = tmp_path / "weekly.md"

    export_weekly_report("2026-W21", out, conn=db_conn)

    body = out.read_text(encoding="utf-8")
    assert "App Store attribution gap" in body
    assert "self-reported source" in body
    assert "§14.5" in body
    assert "Working-parent cohort discovered Stir via Reddit threads two weeks ago." in body
    # §13 hard rules are surfaced verbatim.
    assert "stock" in body and "flow" in body
    # The counterfactual section appears before "what we know" so a future
    # reader can't skim past it.
    assert body.index("Counterfactual") < body.index("What we know")


# ---------------------------------------------------------------------------
# Test 6 — JSON export redacts secret-like columns and Authorization headers.
# ---------------------------------------------------------------------------
def test_json_export_redacts_secret_columns(db_conn, tmp_path: Path) -> None:
    # Seed a raw_api_responses row that carries an Authorization header
    # inside response_json.
    db_conn.execute(
        """
        INSERT INTO raw_api_responses (
            source, endpoint_or_command, request_params_json, response_json,
            status_code, collected_at_utc
        ) VALUES (
            'x_api', '/2/users/me',
            ?, ?, 200, '2026-05-18T12:00:00Z'
        )
        """,
        (
            json.dumps({"headers": {"Authorization": "Bearer sk-live-DEADBEEF12345"}}),
            json.dumps({
                "data": {"id": "123", "username": "dannyscalant"},
                "headers": {"Authorization": "Bearer sk-live-DEADBEEF12345"},
                "access_token": "raw-secret-shouldnt-be-here-but-redact-anyway",
            }),
        ),
    )

    out = tmp_path / "dump.json"
    result = export_database_to_json(out, conn=db_conn)
    body = out.read_text(encoding="utf-8")

    # The literal secret never appears anywhere.
    assert "sk-live-DEADBEEF12345" not in body
    assert "raw-secret-shouldnt-be-here-but-redact-anyway" not in body
    # And "Authorization" appears only as a key — the value is the sentinel.
    assert "[REDACTED]" in body
    # PII gate is in effect by default.
    assert "stir_testers" in result.redactions
    # Schema metadata is present.
    decoded = json.loads(body)
    assert decoded["schema_version"] == 1
    assert "raw_api_responses" in decoded["tables"]
    assert decoded["tables"]["raw_api_responses"][0]["response_json"]["headers"]["Authorization"] == "[REDACTED]"


def test_json_export_fail_closes_on_non_json_raw_api_response(db_conn, tmp_path: Path) -> None:
    """Regression for #12 / CA1 🔴 C1.

    raw_api_responses.response_json is TEXT NOT NULL with no JSON-validity
    constraint. A non-JSON payload (xurl transcript, partial response, error
    body stored as text) must NOT bypass redaction.
    """
    leaked_token = "Bearer sk-live-MUSTNOTAPPEAR"
    db_conn.execute(
        """
        INSERT INTO raw_api_responses (
            source, endpoint_or_command, request_params_json, response_json,
            status_code, collected_at_utc
        ) VALUES (
            'xurl', 'manual_capture',
            NULL,
            ?, 200, '2026-05-18T12:00:00Z'
        )
        """,
        (f"HTTP/1.1 200 OK\r\nAuthorization: {leaked_token}\r\n\r\n<not-json>",),
    )

    out = tmp_path / "fail_closed.json"
    result = export_database_to_json(out, conn=db_conn)
    body = out.read_text(encoding="utf-8")

    assert leaked_token not in body
    assert "[REDACTED]" in body
    cell_keys = list(result.redactions.keys())
    assert any(k.startswith("raw_api_responses.response_json") for k in cell_keys), cell_keys


def test_json_export_redacts_settings_value_when_key_matches_secret_pattern(db_conn, tmp_path: Path) -> None:
    """Regression for #16 / CA1 🟡 W2.

    The settings table's generic (key, value_json) shape escapes the
    column-name regex; the redactor must instead apply the regex to
    each row's `key` and redact `value_json` when it matches.
    """
    db_conn.execute(
        "INSERT INTO settings (key, value_json, note) VALUES (?, ?, ?)",
        ("anthropic_api_key", '"sk-ant-MUSTNOTAPPEAR"', "test row"),
    )
    db_conn.execute(
        "INSERT INTO settings (key, value_json, note) VALUES (?, ?, ?)",
        ("x_oauth_bearer_token", '"OAuth-MUSTNOTAPPEAR-EITHER"', "test row"),
    )

    out = tmp_path / "settings_secrets.json"
    result = export_database_to_json(out, conn=db_conn)
    body = out.read_text(encoding="utf-8")

    assert "sk-ant-MUSTNOTAPPEAR" not in body
    assert "OAuth-MUSTNOTAPPEAR-EITHER" not in body
    cell_keys = list(result.redactions.keys())
    assert "settings[anthropic_api_key].value_json" in cell_keys
    assert "settings[x_oauth_bearer_token].value_json" in cell_keys


# ---------------------------------------------------------------------------
# Test 7 — CSV round-trip preserves row count and key fields.
# ---------------------------------------------------------------------------
def test_csv_round_trip_preserves_data(db_conn, tmp_path: Path) -> None:
    _insert_post(db_conn, pid=1, x_post_id="x-1", text="first post")
    _insert_post(db_conn, pid=2, x_post_id="x-2", text="second post, with, commas")
    _insert_post(db_conn, pid=3, x_post_id="x-3", text='third post with "quotes"')

    out = tmp_path / "posts.csv"
    result = export_table_to_csv("posts", out, conn=db_conn)

    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert result.row_count == 3
    assert len(rows) == 3
    by_id = {r["x_post_id"]: r for r in rows}
    assert by_id["x-1"]["text"] == "first post"
    assert by_id["x-2"]["text"] == "second post, with, commas"
    assert by_id["x-3"]["text"] == 'third post with "quotes"'


def test_csv_export_handles_sqlite_keyword_identifiers(db_conn, tmp_path: Path) -> None:
    """Regression for #29 / CA1 🔵 S4.

    The defensive ``app.exports._sql.quote_identifier`` helper protects
    against column or table names that collide with SQLite keywords or
    contain hyphens/embedded quotes. The live allowlist names are all
    boring ASCII today, so this exercises the helper directly via a
    bespoke table and a synthetic allowlist entry.
    """
    db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS "weird-table" (
            "select" TEXT NOT NULL,
            "order"  INTEGER,
            "from"   TEXT
        )
        """
    )
    db_conn.execute(
        'INSERT INTO "weird-table" ("select", "order", "from") VALUES (?, ?, ?)',
        ("alpha", 1, "x"),
    )
    db_conn.execute(
        'INSERT INTO "weird-table" ("select", "order", "from") VALUES (?, ?, ?)',
        ("beta", 2, "y"),
    )
    db_conn.commit()

    from app.exports import allowlists as al

    al.ALLOWLISTS["weird-table"] = {
        "default_columns": ["select", "order", "from"],
        "opt_in_columns": [],
        "excluded_columns": [],
    }
    try:
        out = tmp_path / "weird.csv"
        result = export_table_to_csv("weird-table", out, conn=db_conn)
        assert result.row_count == 2
        with out.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["select"] == "alpha"
        assert rows[1]["order"] == "2"
    finally:
        del al.ALLOWLISTS["weird-table"]


# ---------------------------------------------------------------------------
# Defensive guard — every registered allowlist refers to columns that exist
# in the current schema. If Phase 5.5 adds new entries to ``default_columns``,
# the migration MUST land BEFORE this test will pass.
# ---------------------------------------------------------------------------
def test_every_allowlist_column_exists_in_schema(db_conn) -> None:
    for table, allowlist in ALLOWLISTS.items():
        info = db_conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        existing = {row["name"] for row in info}
        for col in allowlist["default_columns"] + allowlist["opt_in_columns"]:
            assert col in existing, (
                f"Allowlist for {table!r} references column {col!r} which "
                f"does not exist in the current schema. Either the migration "
                f"is missing or the allowlist entry was added too early."
            )


# ---------------------------------------------------------------------------
# Defensive guard — opt_in_columns and excluded_columns are mutually
# exclusive within a single table.
# ---------------------------------------------------------------------------
def test_opt_in_and_excluded_columns_are_disjoint() -> None:
    for table in ALLOWLISTS:
        assert set(get_opt_in_columns(table)).isdisjoint(set(get_excluded_columns(table))), table


# ---------------------------------------------------------------------------
# Defensive guard — data_exports audit table receives a row per export.
# ---------------------------------------------------------------------------
def test_data_exports_audit_records_each_run(db_conn, tmp_path: Path) -> None:
    _insert_post(db_conn)
    export_table_to_csv("posts", tmp_path / "p.csv", conn=db_conn)
    export_table_to_csv("posts", tmp_path / "p_opt.csv", include_opt_in=True, conn=db_conn)
    export_database_to_json(tmp_path / "dump.json", conn=db_conn)

    rows = db_conn.execute(
        "SELECT kind, table_name, include_opt_in FROM data_exports ORDER BY id ASC"
    ).fetchall()
    kinds = [(r["kind"], r["table_name"], r["include_opt_in"]) for r in rows]
    assert ("csv", "posts", 0) in kinds
    assert ("csv", "posts", 1) in kinds
    assert any(k == "json" for k, _t, _io in kinds)
