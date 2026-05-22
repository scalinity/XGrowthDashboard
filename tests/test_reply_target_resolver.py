"""Exhaustive resolver test — walks all 4^4 = 256 score combinations.

§29.3 defines ``recommended_action`` as a deterministic function of the
four MVP dimensions. The spec's branch ladder is:

    if any score == 0:                                                  → 'skip'
    elif relevance >= 2 and engagement_surface >= 2 and saturation >= 2
         and reply_opportunity >= 2:                                    → 'reply_now'
    elif relevance >= 2 and reply_opportunity >= 2:                     → 'reply_if_time'
    else:                                                               → 'consider'

A single off-by-one in that ladder is invisible until Daniel encounters
the wrong cell in production. This test computes the spec's expected
label in a second, independent way (in the test itself, NOT by importing
the resolver) and asserts equality across every combination. If the
production code and the test code disagree on any cell, this test fails.
"""

from __future__ import annotations

import itertools

import pytest

from app.agent.reply_targets import (
    ACTION_TO_SCORE,
    engagement_footnote,
    engagement_surface_score,
    engagement_surface_thresholds,
    resolve_recommended_action,
    saturation_score,
)


# ---------------------------------------------------------------------------
# Independent oracle — the §29.3 branch ladder written out by hand.
# Deliberately not factored to share code with resolve_recommended_action;
# the whole point is to cross-check two independent implementations.
# ---------------------------------------------------------------------------
def _expected(rel: int, eng: int, sat: int, opp: int) -> str:
    if rel == 0 or eng == 0 or sat == 0 or opp == 0:
        return "skip"
    if rel >= 2 and eng >= 2 and sat >= 2 and opp >= 2:
        return "reply_now"
    if rel >= 2 and opp >= 2:
        return "reply_if_time"
    return "consider"


def test_resolver_walks_all_256_combinations_per_spec_29_3():
    """All 4^4 inputs must agree with the independent oracle."""
    seen = set()
    for rel, eng, sat, opp in itertools.product(range(4), repeat=4):
        got = resolve_recommended_action(rel, eng, sat, opp)
        want = _expected(rel, eng, sat, opp)
        assert got == want, (
            f"§29.3 mismatch at (rel={rel}, eng={eng}, sat={sat}, opp={opp}): "
            f"resolver returned {got!r}, spec expects {want!r}"
        )
        seen.add((rel, eng, sat, opp))
    assert len(seen) == 256, f"expected 256 distinct combos, walked {len(seen)}"


def test_resolver_returns_only_known_labels():
    """Every output must be a member of the §29.3 enum, never anything else."""
    valid = set(ACTION_TO_SCORE.keys())
    for rel, eng, sat, opp in itertools.product(range(4), repeat=4):
        assert resolve_recommended_action(rel, eng, sat, opp) in valid


def test_action_to_score_ordering_matches_spec():
    """§29.3 trailing paragraph: reply_now (3) > reply_if_time (2) > consider (1) > skip (0)."""
    assert ACTION_TO_SCORE["reply_now"] == 3
    assert ACTION_TO_SCORE["reply_if_time"] == 2
    assert ACTION_TO_SCORE["consider"] == 1
    assert ACTION_TO_SCORE["skip"] == 0


def test_resolver_rejects_null_mvp_dimensions():
    """All four MVP dimensions must be scored. NULL is illegal at the resolver."""
    with pytest.raises(ValueError):
        resolve_recommended_action(None, 2, 2, 2)
    with pytest.raises(ValueError):
        resolve_recommended_action(2, None, 2, 2)
    with pytest.raises(ValueError):
        resolve_recommended_action(2, 2, None, 2)
    with pytest.raises(ValueError):
        resolve_recommended_action(2, 2, 2, None)


def test_resolver_ignores_v1_1_dimensions_at_mvp():
    """velocity / timing / audience_quality must NOT alter the label at MVP."""
    base = resolve_recommended_action(3, 3, 3, 3)  # 'reply_now'
    assert base == "reply_now"
    assert (
        resolve_recommended_action(3, 3, 3, 3, velocity=0, timing=0, audience_quality=0)
        == base
    )
    assert (
        resolve_recommended_action(3, 3, 3, 3, velocity=3, timing=3, audience_quality=3)
        == base
    )


# ---------------------------------------------------------------------------
# Engagement-surface thresholds — §29.4
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "engagement_surface_floor_likes": 15,
    "engagement_surface_pct_of_author": 0.001,
    "engagement_surface_high_floor_likes": 50,
    "engagement_surface_high_pct": 0.005,
}


def test_thresholds_null_author_uses_floors():
    """Manual entry without an author follower count must fall back to floors."""
    med, hi = engagement_surface_thresholds(None, DEFAULT_SETTINGS)
    assert med == 15
    assert hi == 50


def test_thresholds_small_author_uses_floors():
    """200 followers × 0.001 = 0.2 → still well below the floor; floors apply."""
    med, hi = engagement_surface_thresholds(200, DEFAULT_SETTINGS)
    assert med == 15
    assert hi == 50


def test_thresholds_large_author_scales_up():
    """50000 followers → medium=50 (0.1%), high=250 (0.5%)."""
    med, hi = engagement_surface_thresholds(50_000, DEFAULT_SETTINGS)
    assert med == 50
    assert hi == 250


def test_thresholds_use_max_of_floor_and_pct():
    """At follower counts straddling the floor, max() picks correctly."""
    # 20000 × 0.001 = 20 < floor 15? no, 20 > 15 → medium=20.
    med, _hi = engagement_surface_thresholds(20_000, DEFAULT_SETTINGS)
    assert med == 20
    # 9000 × 0.005 = 45 < floor 50 → high stays at 50.
    _med, hi = engagement_surface_thresholds(9_000, DEFAULT_SETTINGS)
    assert hi == 50


# ---------------------------------------------------------------------------
# engagement_surface_score boundaries
# ---------------------------------------------------------------------------
def test_score_below_medium_is_0():
    assert engagement_surface_score(14, 15, 50) == 0


def test_score_at_medium_is_1():
    assert engagement_surface_score(15, 15, 50) == 1


def test_score_just_below_high_is_1():
    assert engagement_surface_score(49, 15, 50) == 1


def test_score_at_high_is_2():
    assert engagement_surface_score(50, 15, 50) == 2


def test_score_in_band_above_high_is_2():
    assert engagement_surface_score(120, 15, 50) == 2


def test_score_at_3x_high_is_3():
    """The working interpretation of the 2→3 boundary is 3 * high_threshold."""
    assert engagement_surface_score(150, 15, 50) == 3


def test_score_well_above_3x_high_is_3():
    assert engagement_surface_score(10_000, 15, 50) == 3


# ---------------------------------------------------------------------------
# saturation_score boundaries — §29.3 thread-position bands.
# ---------------------------------------------------------------------------
def test_saturation_zero_replies_is_fresh():
    assert saturation_score(0) == 3


def test_saturation_nine_replies_is_3():
    assert saturation_score(9) == 3


def test_saturation_ten_replies_is_2():
    assert saturation_score(10) == 2


def test_saturation_twenty_nine_replies_is_2():
    assert saturation_score(29) == 2


def test_saturation_thirty_replies_is_1():
    assert saturation_score(30) == 1


def test_saturation_one_hundred_replies_is_0():
    assert saturation_score(100) == 0


def test_saturation_huge_thread_is_0():
    assert saturation_score(1_500) == 0


# ---------------------------------------------------------------------------
# engagement_footnote — §29.4 floor-binding labeling (/review-2 🟡 #1).
# ---------------------------------------------------------------------------
def test_footnote_null_author_uses_no_author_size_label():
    assert engagement_footnote(None, DEFAULT_SETTINGS) == "floor — no author size"


def test_footnote_fires_when_floor_binds_on_small_author():
    """200 × 0.001 = 0.2 → max(15, 0) = 15 (floor wins). Must surface footnote."""
    note = engagement_footnote(200, DEFAULT_SETTINGS)
    assert note is not None
    assert "author too small" in note
    assert "200" in note


def test_footnote_none_when_pct_calc_exceeds_floor():
    """50000 × 0.001 = 50 > floor 15 → no footnote, pct calc dominates."""
    assert engagement_footnote(50_000, DEFAULT_SETTINGS) is None


def test_footnote_boundary_at_floor_breakeven():
    """15000 × 0.001 = 15 == floor 15 → not strictly less, no footnote."""
    assert engagement_footnote(15_000, DEFAULT_SETTINGS) is None
    # Just below the breakeven follower count: 14999 × 0.001 = 14 < 15 → footnote.
    assert engagement_footnote(14_999, DEFAULT_SETTINGS) is not None
