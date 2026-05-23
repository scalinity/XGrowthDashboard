"""Exhaustive resolver test — walks all 4^6 = 4,096 score combinations.

§29.3 defines ``recommended_action`` as a deterministic function of the
four MVP dimensions composed with the Phase 7 velocity + timing modifiers.
The full pipeline:

    # Base ladder (four MVP dimensions):
    if any score == 0:                                                  → 'skip'
    elif relevance >= 2 and engagement_surface >= 2 and saturation >= 2
         and reply_opportunity >= 2:                                    → 'reply_now'
    elif relevance >= 2 and reply_opportunity >= 2:                     → 'reply_if_time'
    else:                                                               → 'consider'

    # Phase 7 modifiers (post-base-ladder):
    if velocity >= 2:  engagement_surface = min(eng + 1, 3)
    if timing < 2:     action = downgrade_one_tier(action)

A single off-by-one in any branch is invisible until Daniel encounters
the wrong cell in production. This test computes the spec's expected
output in a second, independent way (in the test itself, NOT by importing
the resolver) and asserts equality across every combination. If the
production code and the test code disagree on any cell, this test fails.

The 4⁶ = 4,096 combo enumeration extends the Phase 5.6 4⁴ = 256 test.
Runs in <100ms over the full batch on a 2025-era laptop.
"""

from __future__ import annotations

import itertools
import time

import pytest

from app.agent.reply_targets import (
    ACTION_TO_SCORE,
    ReplyTargetSnapshot,
    apply_velocity_timing_modifiers,
    engagement_footnote,
    engagement_surface_score,
    engagement_surface_thresholds,
    resolve_recommended_action,
    saturation_score,
    timing_score,
    velocity_score,
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
# Phase 7 — velocity / timing modifiers + the 4⁶ = 4,096-combo resolver test.
# ---------------------------------------------------------------------------
# This block is the non-negotiable correctness anchor from the §25 Phase 7
# implementation checklist. If the spec's six-dimension resolver and this
# test disagree on ANY cell of the 4,096-cell Cartesian product, Daniel
# could ship the wrong action label for that cell and never notice.

# Independent oracle for the Phase 7 modifier rules. Deliberately written
# from spec text, NOT imported from app.agent.reply_targets — the whole
# point is to cross-check two independent implementations.
_DOWNGRADE_ORACLE: dict[str, str] = {
    "reply_now": "reply_if_time",
    "reply_if_time": "consider",
    "consider": "skip",
    "skip": "skip",  # fixed point — already at floor
}


def _expected_modifiers(
    base_eng: int, base_action: str, vel: int, tim: int
) -> tuple[int, str]:
    """Reproduce §29.3 modifier rules from spec prose."""
    adj_eng = base_eng
    if vel >= 2:
        adj_eng = min(base_eng + 1, 3)
    adj_action = base_action
    if tim < 2:
        adj_action = _DOWNGRADE_ORACLE[base_action]
    return adj_eng, adj_action


def test_resolver_walks_all_4096_combinations_per_spec_29_3_phase_7():
    """All 4⁶ = 4,096 (rel, eng, sat, opp, vel, tim) combos must agree
    with the independent oracle for base action AND modifier composition."""
    seen = set()
    started = time.perf_counter()
    for rel, eng, sat, opp, vel, tim in itertools.product(range(4), repeat=6):
        # Base resolver — ignores velocity/timing per the function's contract.
        got_base = resolve_recommended_action(rel, eng, sat, opp, vel, tim)
        want_base = _expected(rel, eng, sat, opp)
        assert got_base == want_base, (
            f"§29.3 base mismatch at "
            f"(rel={rel}, eng={eng}, sat={sat}, opp={opp}, vel={vel}, tim={tim}): "
            f"resolver returned {got_base!r}, spec expects {want_base!r}"
        )
        # Modifier composition.
        got_eng, got_action = apply_velocity_timing_modifiers(
            base_engagement_surface=eng,
            base_recommended_action=got_base,
            velocity=vel,
            timing=tim,
        )
        want_eng, want_action = _expected_modifiers(eng, want_base, vel, tim)
        assert (got_eng, got_action) == (want_eng, want_action), (
            f"§29.3 modifier mismatch at "
            f"(rel={rel}, eng={eng}, sat={sat}, opp={opp}, vel={vel}, tim={tim}) "
            f"with base_action={want_base!r}: "
            f"modifiers returned (eng={got_eng}, action={got_action!r}), "
            f"spec expects (eng={want_eng}, action={want_action!r})"
        )
        seen.add((rel, eng, sat, opp, vel, tim))
    elapsed = time.perf_counter() - started
    assert len(seen) == 4_096, f"expected 4,096 distinct combos, walked {len(seen)}"
    # Spec says <100ms over the full batch. Allow a generous 500ms ceiling so
    # CI on a slow runner doesn't false-fail; the local-machine target is
    # comfortably under 100ms.
    assert elapsed < 0.5, (
        f"4,096-combo resolver test took {elapsed:.3f}s — "
        f"§29.3 promises <100ms; investigate before merging"
    )


def test_modifiers_high_velocity_caps_engagement_surface_at_3():
    """velocity=3 from a base engagement_surface=3 must NOT exceed 3."""
    adj_eng, _ = apply_velocity_timing_modifiers(
        base_engagement_surface=3,
        base_recommended_action="reply_now",
        velocity=3,
        timing=3,
    )
    assert adj_eng == 3


def test_modifiers_low_timing_clamps_at_skip():
    """timing=0 from a base action='skip' stays at 'skip' (fixed point)."""
    _, adj_action = apply_velocity_timing_modifiers(
        base_engagement_surface=2,
        base_recommended_action="skip",
        velocity=2,
        timing=0,
    )
    assert adj_action == "skip"


def test_modifiers_no_op_when_velocity_and_timing_are_neutral():
    """vel<2 and tim>=2 produces no change at all."""
    adj_eng, adj_action = apply_velocity_timing_modifiers(
        base_engagement_surface=2,
        base_recommended_action="reply_if_time",
        velocity=1,
        timing=2,
    )
    assert (adj_eng, adj_action) == (2, "reply_if_time")


def test_modifiers_skip_none_inputs_for_unscored_history():
    """velocity=None (no snapshot history yet) must not move engagement."""
    adj_eng, adj_action = apply_velocity_timing_modifiers(
        base_engagement_surface=2,
        base_recommended_action="reply_now",
        velocity=None,
        timing=None,
    )
    assert (adj_eng, adj_action) == (2, "reply_now")


def test_modifiers_reject_out_of_range_engagement():
    """The function asserts its preconditions explicitly."""
    with pytest.raises(ValueError):
        apply_velocity_timing_modifiers(
            base_engagement_surface=4,
            base_recommended_action="reply_now",
            velocity=2,
            timing=3,
        )


def test_modifiers_reject_unknown_action_label():
    with pytest.raises(ValueError):
        apply_velocity_timing_modifiers(
            base_engagement_surface=2,
            base_recommended_action="reply_someday",  # type: ignore[arg-type]
            velocity=2,
            timing=3,
        )


# ---------------------------------------------------------------------------
# velocity_score — §29.3 differential-rate dimension.
# ---------------------------------------------------------------------------
def _snap(t: str, rate: float | None) -> ReplyTargetSnapshot:
    return ReplyTargetSnapshot(checked_at_utc=t, computed_likes_per_hour=rate)


def test_velocity_returns_none_for_single_snapshot():
    """One snapshot has no prior baseline to differential against."""
    assert velocity_score([_snap("2026-05-22T10:00:00", 5.0)]) is None


def test_velocity_returns_none_for_empty_history():
    assert velocity_score([]) is None


def test_velocity_decay_when_latest_is_half_or_less():
    snaps = [
        _snap("2026-05-22T09:00:00", 10.0),
        _snap("2026-05-22T10:00:00", 4.0),  # 40% of previous → 0
    ]
    assert velocity_score(snaps) == 0


def test_velocity_zero_when_both_rates_are_zero():
    """A thread that's gone cold reports decaying, not flat."""
    snaps = [
        _snap("2026-05-22T09:00:00", 0.0),
        _snap("2026-05-22T10:00:00", 0.0),
    ]
    assert velocity_score(snaps) == 0


def test_velocity_flat_below_epsilon():
    """Small absolute deltas register as flat."""
    snaps = [
        _snap("2026-05-22T09:00:00", 5.0),
        _snap("2026-05-22T10:00:00", 5.4),  # delta 0.4 < epsilon 1.0
    ]
    assert velocity_score(snaps) == 1


def test_velocity_modest_gain():
    snaps = [
        _snap("2026-05-22T09:00:00", 5.0),
        _snap("2026-05-22T10:00:00", 8.0),  # 1.6× — modest
    ]
    assert velocity_score(snaps) == 2


def test_velocity_accelerating_when_double_or_more():
    snaps = [
        _snap("2026-05-22T09:00:00", 5.0),
        _snap("2026-05-22T10:00:00", 12.0),  # 2.4× — accelerating
    ]
    assert velocity_score(snaps) == 3


def test_velocity_does_not_register_acceleration_from_zero():
    """0 → 1 like/hour is more often noise than a trend; not accelerating."""
    snaps = [
        _snap("2026-05-22T09:00:00", 0.0),
        _snap("2026-05-22T10:00:00", 1.0),
    ]
    assert velocity_score(snaps) != 3


def test_velocity_uses_chronological_order_not_input_order():
    """Snapshots passed out of order must still differential the latest two."""
    snaps = [
        _snap("2026-05-22T10:00:00", 4.0),
        _snap("2026-05-22T09:00:00", 10.0),  # earlier than [0]
    ]
    assert velocity_score(snaps) == 0


def test_velocity_treats_null_computed_rate_as_zero():
    """The first snapshot for a candidate carries NULL on computed deltas."""
    snaps = [
        _snap("2026-05-22T09:00:00", None),
        _snap("2026-05-22T10:00:00", 5.0),
    ]
    # NULL → 0.0 baseline; cur=5.0 vs prev=0 → not in the decay band
    # (prev > 0 is required), not flat (delta >= 1), not accelerating
    # (prev > 0 is required). Falls through to 'modest gain'.
    assert velocity_score(snaps) == 2


# ---------------------------------------------------------------------------
# timing_score — §29.3 post-age + author-tier bands.
# ---------------------------------------------------------------------------
def test_timing_large_author_early_window_is_30min():
    """≥5,000 followers + ≤30 min → 3."""
    assert timing_score(15, 50_000) == 3
    assert timing_score(30, 50_000) == 3


def test_timing_large_author_within_window_is_4h():
    assert timing_score(31, 50_000) == 2
    assert timing_score(240, 50_000) == 2


def test_timing_large_author_late_window_is_12h():
    assert timing_score(241, 50_000) == 1
    assert timing_score(720, 50_000) == 1


def test_timing_large_author_past_window_is_0():
    assert timing_score(721, 50_000) == 0
    assert timing_score(24 * 60, 50_000) == 0


def test_timing_small_author_early_window_is_6h():
    assert timing_score(60, 100) == 3
    assert timing_score(6 * 60, 100) == 3


def test_timing_small_author_past_window_is_0():
    assert timing_score(73 * 60, 100) == 0


def test_timing_null_author_treated_as_small_niche():
    """Missing follower count falls back to the wider small-niche window."""
    assert timing_score(60, None) == 3  # 1h ≤ 6h → 3 for small-niche
    assert timing_score(60, None) == timing_score(60, 100)


def test_timing_negative_age_clamps_to_zero_and_returns_3():
    """A wall-clock skew making post_age_minutes negative shouldn't crash."""
    assert timing_score(-10, 1_000) == 3


def test_timing_author_tier_boundary_at_5000_followers():
    """5,000 is the inclusive floor for large-author treatment."""
    assert timing_score(45, 5_000) == 2     # 45 min, large-tier → within window
    assert timing_score(45, 4_999) == 3      # 45 min, small-tier → still early


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
