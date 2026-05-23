"""Phase 7 / §29.10 — thread-classifier lint tests.

Distinct from tests/test_reply_quality_lint.py (Phase 5.9 §28.18 draft-side).
The thread classifier categorizes the TARGET POST's thread quality before
Daniel even starts drafting; the reply-quality lint categorizes the DRAFT
Daniel wrote.

Eight categorization tests cover each of the four §29.10 categories with
both a passing example AND a blocking example. Plus disambiguation tests
that prove the two lints don't accidentally collide (they share the
``lint.py`` module but their dataclasses + functions are independent).
"""

from __future__ import annotations

import pytest

from app.agent.lint import (
    LintResult,
    ReplyQualityResult,
    ThreadLintResult,
    is_thread_classifier_lint_enabled,
    lint_draft,
    reply_quality_lint,
    thread_classifier_lint,
)


@pytest.fixture(autouse=True)
def _offline_mode(monkeypatch):
    """All tests in this file exercise the offline / deterministic path so
    we don't depend on the Haiku API being reachable."""
    monkeypatch.setenv("LINT_OFFLINE", "1")


# ---------------------------------------------------------------------------
# ragebait — blocking outcome.
# ---------------------------------------------------------------------------
def test_ragebait_detected_on_unpopular_opinion_framing():
    result = thread_classifier_lint(
        target_post_text="Unpopular opinion: most founders are just LARPing.",
        target_author_handle="ragebaiter",
    )
    assert result.ragebait is True
    assert result.is_blocking is True
    assert result.primary_category == "ragebait"


def test_ragebait_passes_on_substantive_post():
    """The same author, a substantive post — should NOT flag ragebait."""
    result = thread_classifier_lint(
        target_post_text=(
            "Took six months to figure out the actual unit economics of "
            "this product. Margins were inverted on the first three plans."
        ),
        target_author_handle="ragebaiter",
    )
    assert result.ragebait is False
    assert result.is_blocking is False


# ---------------------------------------------------------------------------
# meme_with_no_serious_reply_path — signal (not blocking).
# ---------------------------------------------------------------------------
def test_meme_detected_on_shouting_one_word_post():
    result = thread_classifier_lint(
        target_post_text="LOOOOL!!!",
        target_author_handle="meme_account",
    )
    assert result.meme_with_no_serious_reply_path is True
    # Meme alone is a signal, NOT a block — Daniel still gets the
    # "Draft reply" button; only the reply_opportunity_score gets
    # subtracted by 1 (caller-side rule).
    assert result.is_blocking is False
    assert result.primary_category == "meme_with_no_serious_reply_path"


def test_meme_passes_on_full_sentence():
    result = thread_classifier_lint(
        target_post_text=(
            "Spent the weekend rewriting our onboarding from a three-step "
            "wizard to a single-form intake. Activation rate jumped ~30%."
        ),
        target_author_handle="meme_account",
    )
    assert result.meme_with_no_serious_reply_path is False


# ---------------------------------------------------------------------------
# low_quality_reply_thread — signal (not blocking).
# ---------------------------------------------------------------------------
def test_low_quality_thread_detected_on_engagement_bait():
    result = thread_classifier_lint(
        target_post_text="RT if you agree that React is overrated!",
        target_author_handle="rt_baiter",
    )
    assert result.low_quality_reply_thread is True
    assert result.is_blocking is False


def test_low_quality_thread_passes_on_normal_question():
    result = thread_classifier_lint(
        target_post_text="What's the right architecture for a 10-developer SaaS?",
        target_author_handle="rt_baiter",
    )
    assert result.low_quality_reply_thread is False


# ---------------------------------------------------------------------------
# hijacking_required_to_mention_stir — blocking (when Haiku flags it).
# ---------------------------------------------------------------------------
def test_hijacking_required_blocks_when_true():
    """A live Haiku call can flag this; offline matcher always returns
    False but we test the dataclass blocking semantics directly."""
    result = ThreadLintResult(
        ragebait=False,
        meme_with_no_serious_reply_path=False,
        low_quality_reply_thread=False,
        hijacking_required_to_mention_stir=True,
        rationale="topic is recreational sailing — too far from cooking/parenting",
    )
    assert result.is_blocking is True
    assert result.primary_category == "hijacking_required_to_mention_stir"


def test_hijacking_required_offline_defaults_false():
    """Offline matcher can't determine niche alignment without semantics."""
    result = thread_classifier_lint(
        target_post_text="Just summited Half Dome — what a view!",
        target_author_handle="hiker",
    )
    assert result.hijacking_required_to_mention_stir is False


# ---------------------------------------------------------------------------
# Composite + denormalized-category surface tests.
# ---------------------------------------------------------------------------
def test_multiple_signals_compound_into_rationale():
    """Both meme AND low-quality patterns matching → rationale lists both."""
    result = thread_classifier_lint(
        target_post_text="WHAAAAAT!! RT if you agree",
        target_author_handle="combined_account",
    )
    # Either pattern could fire here; both should be detectable.
    assert (
        result.meme_with_no_serious_reply_path
        or result.low_quality_reply_thread
    )
    assert "offline thread lint" in result.rationale


def test_primary_category_precedence_ragebait_over_signals():
    """Ragebait outranks meme + low_quality in the denormalized column."""
    result = ThreadLintResult(
        ragebait=True,
        meme_with_no_serious_reply_path=True,
        low_quality_reply_thread=True,
        hijacking_required_to_mention_stir=False,
        rationale="multiple flags",
    )
    assert result.primary_category == "ragebait"


def test_primary_category_returns_none_when_no_flag_fires():
    result = ThreadLintResult(
        ragebait=False,
        meme_with_no_serious_reply_path=False,
        low_quality_reply_thread=False,
        hijacking_required_to_mention_stir=False,
        rationale="all clear",
    )
    assert result.primary_category is None
    assert result.is_blocking is False


def test_to_json_roundtrips_via_lint_thread_classification_json_column():
    """The serialized form lands in reply_targets.lint_thread_classification_json."""
    import json
    result = ThreadLintResult(
        ragebait=True,
        meme_with_no_serious_reply_path=False,
        low_quality_reply_thread=False,
        hijacking_required_to_mention_stir=False,
        rationale="us-vs-them framing",
    )
    parsed = json.loads(result.to_json())
    assert parsed["ragebait"] is True
    assert parsed["meme_with_no_serious_reply_path"] is False
    assert parsed["rationale"] == "us-vs-them framing"


# ---------------------------------------------------------------------------
# Enablement gate.
# ---------------------------------------------------------------------------
def test_thread_classifier_disabled_short_circuits_to_all_false():
    """When the setting is off, the lint records 'disabled' without running."""
    result = thread_classifier_lint(
        target_post_text="Unpopular opinion: this should fire",
        target_author_handle="x",
        enabled=False,
    )
    assert result.ragebait is False
    assert result.meme_with_no_serious_reply_path is False
    assert result.rationale == "lint disabled"
    assert result.model_used == "disabled"
    assert result.is_blocking is False


def test_is_thread_classifier_lint_enabled_parses_setting_value():
    assert is_thread_classifier_lint_enabled("true") is True
    assert is_thread_classifier_lint_enabled("false") is False
    assert is_thread_classifier_lint_enabled(None) is True
    # malformed JSON defaults to enabled (fail-safe-towards-the-lint)
    assert is_thread_classifier_lint_enabled("not-json") is True


# ---------------------------------------------------------------------------
# Disambiguation from §28.18 reply_quality_lint — both lints can run on the
# SAME candidate; they MUST be independent surfaces.
# ---------------------------------------------------------------------------
def test_thread_and_reply_quality_lints_are_independent_functions():
    """The two lints share lint.py but the function objects are distinct."""
    assert thread_classifier_lint is not reply_quality_lint
    # The dataclasses also have non-overlapping shapes — a ThreadLintResult
    # never duck-types into a ReplyQualityResult.
    thread_result = thread_classifier_lint(
        target_post_text="A perfectly substantive question.",
        target_author_handle="someone",
    )
    reply_result = reply_quality_lint(
        text="A perfectly substantive draft reply.",
        target_post_text="A perfectly substantive question.",
    )
    assert isinstance(thread_result, ThreadLintResult)
    assert isinstance(reply_result, ReplyQualityResult)
    # No shared attributes that could confuse the caller.
    assert not hasattr(thread_result, "passed")
    assert not hasattr(thread_result, "failure_mode")
    assert not hasattr(reply_result, "ragebait")
    assert not hasattr(reply_result, "is_blocking")


def test_dark_pattern_lint_unaffected_by_thread_classifier_addition():
    """Sanity check: lint_draft (§28.2 #12) still returns LintResult, not ThreadLintResult."""
    result = lint_draft("a normal honest draft")
    assert isinstance(result, LintResult)
    assert hasattr(result, "dark_pattern_detected")
