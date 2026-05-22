"""Voice profile module (§28.12) — Haiku synthesis + atomic activation.

These tests stub the Haiku call (we never hit the network in tests) and
exercise the generation logic, schema validation, atomic activation, and
the diff helper used by the Settings UI.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pytest

from app.agent import voice_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _seed_posts(conn: sqlite3.Connection, count: int, *, days_back_each: int = 1) -> None:
    """Insert `count` posts, dated `days_back_each` days apart from today
    back. Every post is `x_post_id IS NOT NULL` so it's in scope.
    """
    today = date.today()
    for i in range(count):
        when = (today - timedelta(days=i * days_back_each)).isoformat()
        conn.execute(
            """
            INSERT INTO posts
              (created_date, created_at_utc, text, type, posted_via,
               manual_confirmation_status, x_post_id)
            VALUES (?, ?, ?, 'standalone', 'manual', 'confirmed', ?)
            """,
            (when, f"{when}T12:00:00Z", f"sample post #{i}", f"x_{i}"),
        )


def _ok_profile_json(**overrides) -> dict:
    base = {
        "hook_patterns": ["concrete noun", "small number"],
        "cadence": {"avg_chars": 180, "avg_sentences": 3.0, "one_idea_per_line_rate": 0.7},
        "vocabulary_signatures": ["actually", "earned"],
        "tone_markers": ["dry observational"],
        "stop_phrases": ["unlock your potential", "leverage"],
        "self_description": (
            "I open with a concrete noun and avoid abstract verbs like "
            "'navigate' or 'leverage'. I prefer one idea per line."
        ),
    }
    base.update(overrides)
    return base


def _make_caller(returned_json: dict, *, in_tok: int = 100, out_tok: int = 200):
    """Return a ModelCaller stub that yields a fixed JSON payload."""
    def caller(system_prompt: str, user_message: str, model: str):
        return (json.dumps(returned_json), in_tok, out_tok)
    return caller


# ---------------------------------------------------------------------------
# Source-post selection
# ---------------------------------------------------------------------------
def test_get_active_returns_none_when_table_empty(db_conn: sqlite3.Connection) -> None:
    assert voice_profile.get_active(db_conn) is None


def test_select_source_posts_respects_window(db_conn: sqlite3.Connection) -> None:
    # Inject 5 posts spaced 30 days apart. With a 60-day window only the
    # first 3 (today, -30, -60) are eligible.
    _seed_posts(db_conn, count=5, days_back_each=30)
    rows = voice_profile._select_source_posts(db_conn, window_days=60)
    assert len(rows) == 3


def test_select_source_posts_skips_unposted_or_empty(db_conn: sqlite3.Connection) -> None:
    # Insert two NOT-in-scope rows then assert _select returns 0.
    today = date.today().isoformat()
    db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status, x_post_id)
        VALUES (?, 'unpublished draft', 'standalone', 'manual', 'draft', NULL)
        """,
        (today,),
    )
    db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status, x_post_id)
        VALUES (?, '', 'standalone', 'manual', 'confirmed', 'x_empty')
        """,
        (today,),
    )
    assert voice_profile._select_source_posts(db_conn, window_days=90) == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_validate_rejects_missing_keys() -> None:
    bad = _ok_profile_json()
    del bad["cadence"]
    with pytest.raises(voice_profile.VoiceProfileGenerationError):
        voice_profile.validate_profile_json(bad)


def test_validate_rejects_list_field_with_wrong_type() -> None:
    bad = _ok_profile_json(vocabulary_signatures="actually, earned")
    with pytest.raises(voice_profile.VoiceProfileGenerationError):
        voice_profile.validate_profile_json(bad)


def test_validate_rejects_empty_self_description() -> None:
    bad = _ok_profile_json(self_description="   ")
    with pytest.raises(voice_profile.VoiceProfileGenerationError):
        voice_profile.validate_profile_json(bad)


def test_validate_rejects_top_level_non_dict() -> None:
    with pytest.raises(voice_profile.VoiceProfileGenerationError):
        voice_profile.validate_profile_json(["not", "a", "dict"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Generate — happy path and atomic activation
# ---------------------------------------------------------------------------
def test_generate_rejects_when_too_few_posts(db_conn: sqlite3.Connection) -> None:
    _seed_posts(db_conn, count=3)
    caller = _make_caller(_ok_profile_json())
    with pytest.raises(voice_profile.VoiceProfileGenerationError) as exc:
        voice_profile.generate(db_conn, window_days=90, model_caller=caller)
    assert "Not enough posts" in str(exc.value)


def test_generate_inserts_and_activates(db_conn: sqlite3.Connection) -> None:
    _seed_posts(db_conn, count=12)
    caller = _make_caller(_ok_profile_json(), in_tok=50, out_tok=150)
    prof = voice_profile.generate(db_conn, window_days=90, model_caller=caller)

    assert prof.is_active is True
    assert prof.source_post_count == 12
    assert prof.tokens_used == 200
    assert prof.self_description().startswith("I open with a concrete noun")

    # The active singleton invariant holds:
    active = voice_profile.get_active(db_conn)
    assert active is not None
    assert active.id == prof.id


def test_generate_atomic_activation_supersedes_prior(db_conn: sqlite3.Connection) -> None:
    _seed_posts(db_conn, count=12)
    caller_a = _make_caller(_ok_profile_json(self_description="First profile self-desc."))
    first = voice_profile.generate(db_conn, window_days=90, model_caller=caller_a)

    caller_b = _make_caller(_ok_profile_json(self_description="Second profile self-desc."))
    second = voice_profile.generate(db_conn, window_days=90, model_caller=caller_b)

    assert second.id != first.id
    # Only one row is active.
    active_count = db_conn.execute(
        "SELECT COUNT(*) FROM voice_profiles WHERE is_active = 1"
    ).fetchone()[0]
    assert active_count == 1

    # The newly-active row is `second`.
    active = voice_profile.get_active(db_conn)
    assert active is not None and active.id == second.id

    # The prior row got marked superseded_by_profile_id pointing at the new one.
    prior_row = db_conn.execute(
        "SELECT is_active, superseded_by_profile_id FROM voice_profiles WHERE id = ?",
        (first.id,),
    ).fetchone()
    assert prior_row["is_active"] == 0
    assert prior_row["superseded_by_profile_id"] == second.id


def test_generate_leaves_prior_active_on_validation_failure(
    db_conn: sqlite3.Connection,
) -> None:
    _seed_posts(db_conn, count=12)
    caller_good = _make_caller(_ok_profile_json())
    first = voice_profile.generate(db_conn, window_days=90, model_caller=caller_good)

    # Second attempt returns a JSON missing required keys — should NOT
    # touch the prior active row.
    bad = _ok_profile_json()
    del bad["stop_phrases"]
    caller_bad = _make_caller(bad)
    with pytest.raises(voice_profile.VoiceProfileGenerationError):
        voice_profile.generate(db_conn, window_days=90, model_caller=caller_bad)

    active = voice_profile.get_active(db_conn)
    assert active is not None and active.id == first.id


def test_generate_strips_code_fence(db_conn: sqlite3.Connection) -> None:
    _seed_posts(db_conn, count=12)

    def caller_with_fence(system_prompt: str, user_message: str, model: str):
        text = "```json\n" + json.dumps(_ok_profile_json()) + "\n```"
        return (text, 50, 50)

    prof = voice_profile.generate(db_conn, window_days=90, model_caller=caller_with_fence)
    assert prof.is_active is True


# ---------------------------------------------------------------------------
# Diff helper
# ---------------------------------------------------------------------------
def test_diff_detects_list_changes() -> None:
    old = _ok_profile_json()
    new = _ok_profile_json(vocabulary_signatures=["actually", "shipped"])
    d = voice_profile.diff(old, new)
    assert "vocabulary_signatures" in d["changed"]
    assert d["changed"]["vocabulary_signatures"]["added"] == ["shipped"]
    assert d["changed"]["vocabulary_signatures"]["removed"] == ["earned"]


def test_diff_detects_scalar_change_to_self_description() -> None:
    old = _ok_profile_json()
    new = _ok_profile_json(self_description="Different self-desc text.")
    d = voice_profile.diff(old, new)
    assert "self_description" in d["changed"]
    assert d["changed"]["self_description"]["before"].startswith("I open with")
    assert d["changed"]["self_description"]["after"] == "Different self-desc text."


# ---------------------------------------------------------------------------
# Prompt builder splice
# ---------------------------------------------------------------------------
def test_prompt_builder_splices_active_profile(db_conn: sqlite3.Connection) -> None:
    from app.agent import prompt_builder

    _seed_posts(db_conn, count=12)
    voice_profile.generate(
        db_conn,
        window_days=90,
        model_caller=_make_caller(
            _ok_profile_json(self_description="Daniel test self description.")
        ),
    )
    prompt = prompt_builder.build_system_prompt(db_conn)
    assert "Daniel test self description." in prompt
    assert "Voice profile (generated)" in prompt
    # Placeholders fully replaced.
    assert "VOICE_PROFILE_SELF_DESCRIPTION_PLACEHOLDER" not in prompt
    assert "VOICE_PROFILE_STRUCTURAL_PLACEHOLDER" not in prompt


def test_prompt_builder_handles_no_active_profile(db_conn: sqlite3.Connection) -> None:
    from app.agent import prompt_builder

    prompt = prompt_builder.build_system_prompt(db_conn)
    # Placeholders are replaced (even with empty strings) — they MUST NOT
    # leak into the rendered prompt.
    assert "VOICE_PROFILE_SELF_DESCRIPTION_PLACEHOLDER" not in prompt
    assert "VOICE_PROFILE_STRUCTURAL_PLACEHOLDER" not in prompt


def test_voice_profile_invariant_check(db_conn: sqlite3.Connection) -> None:
    from app.agent import prompt_builder

    count, replaced = prompt_builder.verify_voice_profile_invariants(
        db_conn, prompt_builder.build_system_prompt(db_conn)
    )
    assert count == 0
    assert replaced is True
