"""Pre-publish heuristic scorer (§28.11) — golden-input tests.

Per the spec: the scorer is a pure function of (draft text, draft metadata,
active voice profile). Re-running it on the same inputs MUST yield the
same output — drift between runs is a bug. These tests pin the
deterministic shape with known-good inputs and label boundaries.
"""

from __future__ import annotations

from app.agent import prepublish_scorer as ps
from app.agent.voice_profile import VoiceProfile


# ---------------------------------------------------------------------------
# Fixtures (plain factories — keep tests free of pytest fixture indirection).
# ---------------------------------------------------------------------------
def _profile(**overrides) -> VoiceProfile:
    payload = {
        "hook_patterns": [],
        "cadence": {"avg_chars": 180, "avg_sentences": 3.0, "one_idea_per_line_rate": 0.7},
        "vocabulary_signatures": ["shipped", "earned"],
        "tone_markers": ["dry observational"],
        "stop_phrases": ["unlock your potential", "leverage"],
        "self_description": "I open with a concrete noun.",
    }
    payload.update(overrides)
    return VoiceProfile(
        id=1,
        generated_at_utc="2026-05-22T12:00:00Z",
        is_active=True,
        source_post_window_days=90,
        source_post_count=12,
        profile_json=payload,
        model_used="claude-haiku-4-5-20251001",
        tokens_used=100,
        superseded_by_profile_id=None,
    )


# ---------------------------------------------------------------------------
# Per-dimension micro-tests
# ---------------------------------------------------------------------------
def test_clarity_runaway_sentence_returns_zero() -> None:
    long = " ".join(["word"] * 35) + "."
    assert ps.clarity_score(long) == 0


def test_clarity_short_clean_returns_three() -> None:
    text = "Short clean post. One idea per line. Lands."
    assert ps.clarity_score(text) == 3


def test_hook_generic_opener_returns_zero() -> None:
    assert ps.hook_strength_score("Just thinking about the build lane today.") == 0
    assert ps.hook_strength_score("So here's the thing.") == 0


def test_hook_with_digit_returns_three() -> None:
    assert ps.hook_strength_score("Three failed dinner attempts before 7pm.") == 3


def test_hook_digit_inside_url_does_not_score_three() -> None:
    """P58R-17: an embedded URL id like /status/1234 should NOT fake-pass
    the digit signal. With URLs stripped, this 6-word line earns 1."""
    s = ps.hook_strength_score("see https://x.com/abc/status/1234567 worth a look")
    assert s <= 1


def test_hook_digit_inside_hashtag_does_not_score_three() -> None:
    """P58R-17: digits inside a hashtag (#build2024) should not fake-pass."""
    s = ps.hook_strength_score("ship #build2024 has been wild so far")
    # Without the digit signal, this 8-word line still has a proper-noun
    # and 5+ words so earns 2 via the proper-noun branch — but it must
    # NOT earn 3 (which would require the digit signal).
    assert s < 3


def test_specificity_with_numbers_and_proper_nouns() -> None:
    text = "Stir launched in March with 12 testers. Three already cook weekly."
    assert ps.specificity_score(text) == 3


def test_specificity_vague_returns_zero() -> None:
    text = "Some people think many things are stuff that matters."
    assert ps.specificity_score(text) == 0


def test_length_over_280_returns_zero() -> None:
    text = "x" * 281
    assert ps.length_fit_score(text) == 0


def test_length_around_200_returns_three() -> None:
    # 195 chars — within 10% of target.
    text = "x" * 195
    assert ps.length_fit_score(text) == 3


def test_length_short_reply_scores_three_not_zero() -> None:
    """P58R-2: short replies (>=10 chars, <=240) should earn 3, not 0.
    Replies legitimately run shorter than standalones."""
    text = "yes — 3x in 24h."  # 16 chars
    assert ps.length_fit_score(text, draft_kind="reply") == 3
    # And the same text as a standalone would still earn the legacy 0
    # (way under the 200-char target).
    assert ps.length_fit_score(text, draft_kind="standalone") == 0


def test_length_very_short_reply_scores_one() -> None:
    text = "yes."  # 4 chars
    assert ps.length_fit_score(text, draft_kind="reply") == 1


def test_length_reply_over_ceiling_returns_zero() -> None:
    text = "x" * 281
    assert ps.length_fit_score(text, draft_kind="reply") == 0


def test_score_short_reply_does_not_collapse_to_weak() -> None:
    """End-to-end: a short reply with real substance should NOT be 'weak'
    purely on length. P58R-2 regression."""
    row = ps.score(
        draft_text="The reply substance signal here is exactly the thing.",  # 53 chars
        draft_kind="reply",
        pillar="build",
        cta="none",
        target_post_text="Specific reply substance is the dimension that matters.",
        active_voice_profile=None,
    )
    assert row.length_fit_score == 3
    assert row.composite_label in ("viable", "strong")


def test_format_trailing_ellipsis_returns_zero() -> None:
    assert ps.format_fit_score("Sentence that trails off...") == 0


def test_topic_fit_unknown_pillar_returns_one_or_default() -> None:
    text = "totally unrelated topic content here"
    # pillar 'stir' but no affinity hits.
    assert ps.topic_fit_score(text, pillar="stir") == 1
    # no pillar → default 2 (spec rule)
    assert ps.topic_fit_score(text, pillar=None) == 2


def test_topic_fit_three_when_multiple_affinity_words() -> None:
    text = "Stir launched. The kitchen scan + recipe generation feedback was strong."
    assert ps.topic_fit_score(text, pillar="stir") == 3


def test_reply_substance_thin_acknowledgment_zero() -> None:
    target = "The X algorithm rewards specific reply substance."
    reply = "Great post"
    assert ps.reply_substance_score(reply, target) == 0


def test_reply_substance_thin_acknowledgment_without_target_still_zero() -> None:
    """P58R-9: a 'great post' reply scores 0 even when target_post_text is
    None. The thin-opener check must apply to both branches."""
    assert ps.reply_substance_score("Great post", target_post_text=None) == 0


def test_reply_substance_without_target_non_thin_returns_one() -> None:
    """P58R-9: a substantive-looking reply without target text earns 1
    (no overlap evidence available), not the prior generous 2."""
    assert (
        ps.reply_substance_score(
            "Worth tracking the cohort-specific funnel here, not just the headline.",
            target_post_text=None,
        )
        == 1
    )


def test_reply_substance_overlap_three() -> None:
    target = "The X algorithm rewards specific reply substance."
    reply = "The algorithm rewarding specific replies is exactly what shifted my drafting."
    assert ps.reply_substance_score(reply, target) == 3


def test_cta_none_returns_none() -> None:
    assert ps.cta_strength_score("anything here", cta="none") is None


def test_cta_question_with_substance_returns_three() -> None:
    text = "Here's the thing.\nWhat are you actually building this week?"
    assert ps.cta_strength_score(text, cta="ask") == 3


def test_cta_one_word_generic_returns_zero() -> None:
    """P58R-16: one-word generic CTAs ('thoughts?', 'agreed?') are 0."""
    assert ps.cta_strength_score("Here's the thing.\nthoughts?", cta="ask") == 0
    assert ps.cta_strength_score("Here's the thing.\nAgreed?", cta="ask") == 0
    assert ps.cta_strength_score("Here's the thing.\nviews?", cta="ask") == 0


def test_voice_fit_returns_none_when_no_profile() -> None:
    assert ps.voice_fit_score("anything", profile=None) is None


def test_voice_fit_drops_on_stop_phrase() -> None:
    text = "I want to leverage this opportunity to unlock your potential."
    s = ps.voice_fit_score(text, profile=_profile())
    assert s == 0  # two stop_phrase hits + LLM phrase hits, clamped at 0


def test_voice_fit_rewards_signature_and_cadence() -> None:
    text = (
        "Shipped the first internal cut today.\n"
        "Three testers already cook weekly.\n"
        "What earned the launch was the kitchen scan, not the AI prose."
    )
    s = ps.voice_fit_score(text, profile=_profile())
    assert s >= 2


# ---------------------------------------------------------------------------
# Composite label derivation — pinned per §10 prepublish_scores notes.
# ---------------------------------------------------------------------------
def test_composite_strong_threshold() -> None:
    scores = {
        "clarity": 3, "hook_strength": 3, "specificity": 3, "length_fit": 2,
        "format_fit": 2, "topic_fit": 2, "reply_substance": None,
        "cta_strength": 2, "voice_fit": 2,
    }
    assert ps.compute_composite_label(scores) == "strong"


def test_composite_weak_on_any_zero() -> None:
    scores = {"a": 3, "b": 3, "c": 3, "d": 3, "e": 3, "f": 3, "g": 3, "h": 0}
    assert ps.compute_composite_label(scores) == "weak"


def test_composite_weak_when_few_high_scores() -> None:
    scores = {"a": 1, "b": 1, "c": 1, "d": 2, "e": 2, "f": 2, "g": 1, "h": 1}
    assert ps.compute_composite_label(scores) == "weak"


def test_composite_viable_otherwise() -> None:
    scores = {"a": 2, "b": 2, "c": 2, "d": 2, "e": 2, "f": 2, "g": 1, "h": 1}
    assert ps.compute_composite_label(scores) == "viable"


def test_composite_ignores_none_dimensions() -> None:
    # Only the non-None dims are scored. If we have 6 high non-None scores
    # and 2 None, that still qualifies for `strong`.
    scores = {
        "clarity": 3, "hook_strength": 3, "specificity": 2,
        "length_fit": 2, "format_fit": 2, "topic_fit": 2,
        "reply_substance": None, "cta_strength": None, "voice_fit": None,
    }
    assert ps.compute_composite_label(scores) == "strong"


# ---------------------------------------------------------------------------
# End-to-end score() orchestration — one input per composite_label value.
# ---------------------------------------------------------------------------
def test_score_yields_strong_for_polished_standalone() -> None:
    text = (
        "Three failed dinner attempts before 7pm.\n"
        "Stir scanned the fridge, suggested 3 cookable options, "
        "and the working parent texted me a photo of the meal.\n"
        "Shipped the iOS build today. Earned the launch."
    )
    row = ps.score(
        draft_text=text,
        draft_kind="standalone",
        pillar="stir",
        cta="none",
        target_post_text=None,
        active_voice_profile=_profile(),
    )
    assert row.composite_label == "strong"
    assert row.cta_strength_score is None
    assert row.reply_substance_score is None
    assert row.voice_fit_score is not None and row.voice_fit_score >= 2


def test_score_yields_weak_for_generic_filler_standalone() -> None:
    text = "Just thinking about leveraging things today..."
    row = ps.score(
        draft_text=text,
        draft_kind="standalone",
        pillar="build",
        cta="none",
        target_post_text=None,
        active_voice_profile=_profile(),
    )
    assert row.composite_label == "weak"
    assert any("generic" in w for w in row.warnings_json)


def test_score_reply_substance_path() -> None:
    text = (
        "The reply-substance scoring is exactly the cold lever I've been "
        "ignoring. Adding the overlap-detection idea to my draft loop."
    )
    target = "Reply substance and overlap with the target post determine whether the reply lands."
    row = ps.score(
        draft_text=text,
        draft_kind="reply",
        pillar="build",
        cta="none",
        target_post_text=target,
        active_voice_profile=_profile(),
    )
    assert row.reply_substance_score is not None and row.reply_substance_score >= 2
    assert row.composite_label in ("viable", "strong")


def test_score_round_trip_db(db_conn) -> None:
    # End-to-end: insert a parent draft, persist a score row via the
    # orchestrator's insert_score_row, read it back.
    draft_id = db_conn.execute(
        """
        INSERT INTO agent_drafts (draft_kind, text)
        VALUES ('standalone', 'placeholder')
        RETURNING id
        """
    ).fetchone()[0]
    row = ps.score(
        draft_text="Three dinners. Two failures. One cookbook scan that landed.",
        draft_kind="standalone",
        pillar="stir",
        cta="none",
        target_post_text=None,
        active_voice_profile=None,
    )
    score_id = ps.insert_score_row(db_conn, agent_draft_id=draft_id, row=row)

    # The cyclical FK is wired.
    wired = db_conn.execute(
        "SELECT prepublish_score_id FROM agent_drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()[0]
    assert wired == score_id

    fetched = ps.get_score_for_draft(db_conn, agent_draft_id=draft_id)
    assert fetched is not None
    assert fetched["composite_label"] in ("weak", "viable", "strong")
    assert fetched["scorer_version"] == ps.SCORER_VERSION
