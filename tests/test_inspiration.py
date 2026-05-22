"""Tests for the §28.29 Inspiration Library + plagiarism guard.

Load-bearing invariants (all from §28.29):

- ``save_inspiration`` rejects duplicate source text via
  ``unique(source_text_hash)``.
- ``compute_plagiarism_risk`` is pure deterministic, honors the four
  threshold settings, and combines Jaccard + n-gram by taking the
  worst label.
- ``final_risk = max(ai_reported, deterministic)`` — the AI cannot
  underreport. Specifically: AI saying 'low' while deterministic
  computes 'high' MUST resolve to 'high'.
- ``transform`` persists the FINAL ``plagiarism_risk_label`` and
  audit-logs the event.
- ``record_plagiarism_override`` requires a non-empty reason and
  audit-logs the override (the row's risk label stays unchanged).
- The two agent tools (#23 + #24) are registered and return dicts.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

import pytest

from app.agent import audit_log as _audit_log
from app.agent import inspiration as _ins
from app.agent.tools import get_tool


# ---------------------------------------------------------------------------
# Helpers: a deterministic fake model caller.
# ---------------------------------------------------------------------------
def _fake_caller(*, output_text: str, ai_label: str) -> Callable:
    import json
    payload = json.dumps(
        {"output_text": output_text, "ai_reported_risk_label": ai_label}
    )

    def caller(_sys: str, _user: str, _model: str) -> tuple[str, int, int]:
        return (payload, 800, 200)

    return caller


# ---------------------------------------------------------------------------
# Pure plagiarism math — golden inputs.
# ---------------------------------------------------------------------------
def test_jaccard_similarity_identical_text() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    assert _ins.jaccard_similarity(text, text) == 1.0


def test_jaccard_similarity_disjoint_text() -> None:
    a = "alpha beta gamma delta"
    b = "epsilon zeta eta theta"
    assert _ins.jaccard_similarity(a, b) == 0.0


def test_jaccard_similarity_partial_overlap() -> None:
    a = "alpha beta gamma delta epsilon"
    b = "alpha beta zeta eta theta"
    # tokens: {alpha,beta,gamma,delta,epsilon} ∩ {alpha,beta,zeta,eta,theta} = 2
    # union = 8 → 0.25
    assert abs(_ins.jaccard_similarity(a, b) - 0.25) < 1e-9


def test_jaccard_handles_empty_strings() -> None:
    assert _ins.jaccard_similarity("", "anything") == 0.0
    assert _ins.jaccard_similarity("anything", "") == 0.0
    assert _ins.jaccard_similarity("", "") == 0.0


def test_longest_shared_ngram_finds_exact_run() -> None:
    a = "the quick brown fox jumps over the lazy dog"
    b = "i think the quick brown fox is fast"
    # "the quick brown fox" = 4 tokens shared contiguously.
    assert _ins.longest_shared_ngram_length(a, b) == 4


def test_longest_shared_ngram_zero_when_no_overlap() -> None:
    assert _ins.longest_shared_ngram_length("a b c", "x y z") == 0


def test_longest_shared_ngram_case_insensitive() -> None:
    assert _ins.longest_shared_ngram_length("Hello World", "hello world") == 2


# ---------------------------------------------------------------------------
# compute_plagiarism_risk uses settings thresholds.
# ---------------------------------------------------------------------------
def test_compute_plagiarism_risk_high_when_jaccard_high(
    db_conn: sqlite3.Connection,
) -> None:
    text = "the quick brown fox jumps over the lazy dog"
    read = _ins.compute_plagiarism_risk(db_conn, text, text)
    assert read.deterministic_risk_label == "high"
    assert read.jaccard_similarity == 1.0


def test_compute_plagiarism_risk_low_when_no_overlap(
    db_conn: sqlite3.Connection,
) -> None:
    a = "alpha beta gamma delta epsilon zeta"
    b = "iota kappa lambda mu nu xi"
    read = _ins.compute_plagiarism_risk(db_conn, a, b)
    assert read.deterministic_risk_label == "low"
    assert read.jaccard_similarity == 0.0


def test_compute_plagiarism_risk_medium_when_ngram_medium(
    db_conn: sqlite3.Connection,
) -> None:
    # Default ngram_medium = 5 → shared 5-word run yields medium.
    a = "alpha the quick brown fox jumps over"
    b = "the quick brown fox jumps later"
    # Shared 5-token run: "the quick brown fox jumps". Jaccard low
    # (some overlap but mostly disjoint).
    read = _ins.compute_plagiarism_risk(db_conn, a, b)
    assert read.longest_shared_ngram_length >= 5
    assert read.deterministic_risk_label in ("medium", "high")


# ---------------------------------------------------------------------------
# final_risk — load-bearing AI-cannot-underreport rule.
# ---------------------------------------------------------------------------
def test_final_risk_ai_low_deterministic_high_resolves_high() -> None:
    """§28.29 load-bearing: AI cannot underreport when deterministic is high."""
    assert _ins.final_risk("low", "high") == "high"


def test_final_risk_ai_high_deterministic_low_resolves_high() -> None:
    """AI overreporting is honored — caution stays."""
    assert _ins.final_risk("high", "low") == "high"


def test_final_risk_both_low_resolves_low() -> None:
    assert _ins.final_risk("low", "low") == "low"


def test_final_risk_both_high_resolves_high() -> None:
    assert _ins.final_risk("high", "high") == "high"


def test_final_risk_mixed_medium_low_resolves_medium() -> None:
    assert _ins.final_risk("medium", "low") == "medium"
    assert _ins.final_risk("low", "medium") == "medium"


# ---------------------------------------------------------------------------
# save_inspiration.
# ---------------------------------------------------------------------------
def test_save_inspiration_returns_new_id(db_conn: sqlite3.Connection) -> None:
    sid = _ins.save_inspiration(
        db_conn,
        source_post_text="three failed dinner attempts taught me UX.",
        source_url="https://x.com/foo/status/1",
        source_author="@foo",
        tags=["hook", "self-deprecation"],
        notes="why I saved it",
    )
    assert sid > 0


def test_save_inspiration_duplicate_rejected(
    db_conn: sqlite3.Connection,
) -> None:
    _ins.save_inspiration(
        db_conn, source_post_text="exactly the same text"
    )
    with pytest.raises(_ins.DuplicateInspirationError):
        _ins.save_inspiration(
            db_conn, source_post_text="exactly the same text"
        )


def test_save_inspiration_rejects_empty_text(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(_ins.InspirationError):
        _ins.save_inspiration(db_conn, source_post_text="")
    with pytest.raises(_ins.InspirationError):
        _ins.save_inspiration(db_conn, source_post_text="   ")


def test_p511r19_save_inspiration_rejects_non_http_scheme(
    db_conn: sqlite3.Connection,
) -> None:
    """P511R-19: defense in depth. Reject javascript:, data:, vbscript:
    URLs at save time so any downstream consumer (markdown export,
    Obsidian sync, spec excerpt) that surfaces source_url as a link
    doesn't become an XSS vector."""
    for bad_url in (
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
    ):
        with pytest.raises(_ins.InspirationError, match="scheme"):
            _ins.save_inspiration(
                db_conn,
                source_post_text=f"probe for {bad_url}",
                source_url=bad_url,
            )


def test_p511r19_save_inspiration_accepts_http_https(
    db_conn: sqlite3.Connection,
) -> None:
    """http:// and https:// (case-insensitive scheme) are allowed."""
    sid1 = _ins.save_inspiration(
        db_conn,
        source_post_text="probe http",
        source_url="http://example.com/post/1",
    )
    sid2 = _ins.save_inspiration(
        db_conn,
        source_post_text="probe https",
        source_url="https://x.com/foo/status/123",
    )
    sid3 = _ins.save_inspiration(
        db_conn,
        source_post_text="probe HTTPS uppercase",
        source_url="HTTPS://x.com/bar/status/456",
    )
    assert sid1 > 0 and sid2 > 0 and sid3 > 0


def test_p511r21_archive_inspiration_no_audit_on_double_archive(
    db_conn: sqlite3.Connection,
) -> None:
    """Re-archiving an already-archived row writes neither a status
    UPDATE nor an audit row. Mirror of P511R-15's no-op rule."""
    sid = _ins.save_inspiration(db_conn, source_post_text="archive me")
    _ins.archive_inspiration(db_conn, inspiration_id=sid)
    audits_after_first = [
        r
        for r in _audit_log.query(
            db_conn, target_type="saved_inspiration_post", target_id=sid
        )
        if r.event_type == "inspiration_archived"
    ]
    assert len(audits_after_first) == 1
    # Re-archive — should be a no-op everywhere.
    _ins.archive_inspiration(db_conn, inspiration_id=sid)
    audits_after_second = [
        r
        for r in _audit_log.query(
            db_conn, target_type="saved_inspiration_post", target_id=sid
        )
        if r.event_type == "inspiration_archived"
    ]
    assert len(audits_after_second) == 1, (
        "double-archive must not write a second audit row"
    )


def test_p511r21_archive_inspiration_no_audit_on_missing_row(
    db_conn: sqlite3.Connection,
) -> None:
    """Archiving a nonexistent inspiration_id writes no audit row."""
    _ins.archive_inspiration(db_conn, inspiration_id=999_999)
    audits = _audit_log.query(
        db_conn, target_type="saved_inspiration_post", target_id=999_999
    )
    assert audits == []


def test_p511r19_save_inspiration_allows_null_or_empty_url(
    db_conn: sqlite3.Connection,
) -> None:
    """source_url is nullable per §10.2; NULL and empty/whitespace are fine."""
    sid1 = _ins.save_inspiration(
        db_conn, source_post_text="no url", source_url=None
    )
    sid2 = _ins.save_inspiration(
        db_conn, source_post_text="empty url", source_url=""
    )
    sid3 = _ins.save_inspiration(
        db_conn, source_post_text="whitespace url", source_url="   "
    )
    assert sid1 > 0 and sid2 > 0 and sid3 > 0


def test_save_inspiration_emits_audit_row(db_conn: sqlite3.Connection) -> None:
    sid = _ins.save_inspiration(
        db_conn, source_post_text="x", source_author="@bar"
    )
    rows = _audit_log.query(
        db_conn, target_type="saved_inspiration_post", target_id=sid
    )
    assert any(r.event_type == "inspiration_saved" for r in rows)


# ---------------------------------------------------------------------------
# transform — persists final_risk = max(ai, deterministic).
# ---------------------------------------------------------------------------
def test_transform_persists_final_risk_max_rule(
    db_conn: sqlite3.Connection,
) -> None:
    """The load-bearing test: AI says 'low', deterministic is 'high'
    because output is identical to source → final_risk MUST be 'high'.
    """
    source = "the quick brown fox jumps over the lazy dog"
    sid = _ins.save_inspiration(db_conn, source_post_text=source)
    # AI claims low, output is identical → deterministic high → final high.
    caller = _fake_caller(output_text=source, ai_label="low")
    result = _ins.transform(
        db_conn,
        saved_inspiration_id=sid,
        mode="structure",
        model_caller=caller,
    )
    assert result.ai_reported_risk_label == "low"
    assert result.plagiarism_risk_label == "high"  # max() wins
    # Persisted shape matches.
    persisted = _ins.list_transforms(
        db_conn, saved_inspiration_id=sid
    )[0]
    assert persisted["plagiarism_risk_label"] == "high"
    assert persisted["ai_reported_risk_label"] == "low"


def test_transform_persists_low_when_both_low(
    db_conn: sqlite3.Connection,
) -> None:
    sid = _ins.save_inspiration(
        db_conn, source_post_text="alpha beta gamma delta epsilon"
    )
    caller = _fake_caller(
        output_text="iota kappa lambda mu nu xi", ai_label="low"
    )
    result = _ins.transform(
        db_conn,
        saved_inspiration_id=sid,
        mode="counterpoint",
        model_caller=caller,
    )
    assert result.plagiarism_risk_label == "low"


def test_transform_rejects_unknown_mode(db_conn: sqlite3.Connection) -> None:
    sid = _ins.save_inspiration(db_conn, source_post_text="x")
    with pytest.raises(_ins.TransformError, match="unknown mode"):
        _ins.transform(
            db_conn,
            saved_inspiration_id=sid,
            mode="podcastify",  # type: ignore[arg-type]
            model_caller=_fake_caller(output_text="x", ai_label="low"),
        )


def test_transform_rejects_unknown_inspiration_id(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(_ins.InspirationNotFoundError):
        _ins.transform(
            db_conn,
            saved_inspiration_id=999_999,
            mode="structure",
            model_caller=_fake_caller(output_text="x", ai_label="low"),
        )


def test_transform_rejects_bad_model_response(
    db_conn: sqlite3.Connection,
) -> None:
    sid = _ins.save_inspiration(db_conn, source_post_text="x")

    def bad_caller(_s: str, _u: str, _m: str) -> tuple[str, int, int]:
        return ("not json at all", 1, 1)

    with pytest.raises(_ins.TransformError, match="non-JSON"):
        _ins.transform(
            db_conn,
            saved_inspiration_id=sid,
            mode="structure",
            model_caller=bad_caller,
        )


def test_transform_emits_audit_row(db_conn: sqlite3.Connection) -> None:
    sid = _ins.save_inspiration(db_conn, source_post_text="x source")
    result = _ins.transform(
        db_conn,
        saved_inspiration_id=sid,
        mode="structure",
        model_caller=_fake_caller(output_text="abstract pattern", ai_label="low"),
    )
    rows = _audit_log.query(
        db_conn,
        target_type="inspiration_transform",
        target_id=result.transform_id,
    )
    assert any(r.event_type == "inspiration_transformed" for r in rows)


# ---------------------------------------------------------------------------
# Override audit.
# ---------------------------------------------------------------------------
def test_record_plagiarism_override_requires_reason(
    db_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(_ins.InspirationError):
        _ins.record_plagiarism_override(
            db_conn, transform_id=1, reason=""
        )


def test_record_plagiarism_override_audit_logged(
    db_conn: sqlite3.Connection,
) -> None:
    audit_id = _ins.record_plagiarism_override(
        db_conn,
        transform_id=42,
        reason="reviewed the overlap, intentional structural homage",
    )
    assert audit_id > 0
    rows = _audit_log.query(
        db_conn, target_type="inspiration_transform", target_id=42
    )
    overrides = [r for r in rows if r.event_type == "inspiration_plagiarism_override"]
    assert len(overrides) == 1
    assert overrides[0].details is not None
    assert "intentional" in overrides[0].details["reason"]


# ---------------------------------------------------------------------------
# P511R-5: has_been_overridden — flips the §14.13 high-risk gate.
# ---------------------------------------------------------------------------
def test_p511r5_has_been_overridden_false_when_no_override(
    db_conn: sqlite3.Connection,
) -> None:
    assert _ins.has_been_overridden(db_conn, transform_id=999) is False


def test_p511r5_has_been_overridden_true_after_record(
    db_conn: sqlite3.Connection,
) -> None:
    _ins.record_plagiarism_override(
        db_conn, transform_id=123, reason="reviewed"
    )
    assert _ins.has_been_overridden(db_conn, transform_id=123) is True


def test_p511r5_has_been_overridden_scoped_per_transform(
    db_conn: sqlite3.Connection,
) -> None:
    # An override on transform 7 must not unlock transform 8.
    _ins.record_plagiarism_override(
        db_conn, transform_id=7, reason="reviewed"
    )
    assert _ins.has_been_overridden(db_conn, transform_id=7) is True
    assert _ins.has_been_overridden(db_conn, transform_id=8) is False


# ---------------------------------------------------------------------------
# Agent tools #23 + #24.
# ---------------------------------------------------------------------------
def test_tool_registry_includes_both_inspiration_tools() -> None:
    from app.agent.tools import AGENT_TOOLS
    names = {t.name for t in AGENT_TOOLS}
    assert "transform_inspiration" in names
    assert "score_inspiration_plagiarism_risk" in names


def test_tool_score_inspiration_plagiarism_returns_dict(
    db_conn: sqlite3.Connection,
) -> None:
    tool = get_tool("score_inspiration_plagiarism_risk")
    res = tool.handler(
        db_conn,
        source_text="the quick brown fox",
        output_text="the quick brown fox",
    )
    assert res["deterministic_risk_label"] == "high"
    assert res["jaccard_similarity"] == 1.0


def test_tool_transform_inspiration_surfaces_failure_as_dict(
    db_conn: sqlite3.Connection,
) -> None:
    # No ANTHROPIC_API_KEY in CI; the tool wrapper must return a
    # failure dict, never raise.
    sid = _ins.save_inspiration(db_conn, source_post_text="x source")
    tool = get_tool("transform_inspiration")
    result = tool.handler(
        db_conn,
        saved_inspiration_id=sid,
        mode="structure",
    )
    assert isinstance(result, dict)
    # Either succeeds (if API key set) or returns failure dict.
    assert "status" in result


# ---------------------------------------------------------------------------
# TRANSFORM_MODES constant matches the schema CHECK list.
# ---------------------------------------------------------------------------
def test_transform_modes_match_schema_check(db_conn: sqlite3.Connection) -> None:
    """Adding a mode requires updating BOTH TRANSFORM_MODES AND the
    migration's CHECK list (§28.29). This test fails loudly if they
    drift.
    """
    # Insert one row per mode — any mode missing from the CHECK list
    # would raise IntegrityError.
    sid = _ins.save_inspiration(db_conn, source_post_text="probe")
    for mode in _ins.TRANSFORM_MODES:
        db_conn.execute(
            """
            INSERT INTO inspiration_transforms
              (saved_inspiration_id, transform_mode, output_text,
               output_text_hash, model_used)
            VALUES (?, ?, 'probe output', ?, 'claude-opus-4-7')
            """,
            (sid, mode, f"hash-{mode}"),
        )
    count = db_conn.execute(
        "SELECT COUNT(*) FROM inspiration_transforms WHERE saved_inspiration_id = ?",
        (sid,),
    ).fetchone()[0]
    assert count == len(_ins.TRANSFORM_MODES)
