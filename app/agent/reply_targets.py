"""Reply-target scoring helpers — spec.md §29.

Pure-function surfaces (exercised without a DB):

* ``resolve_recommended_action`` — the deterministic resolver from §29.3.
  The dashboard never produces a composite "viability" score; the action
  label is derived from the four MVP dimensions by a fixed branch ladder.
  The Phase 7 4⁶=4,096-combo test (tests/test_reply_target_resolver.py)
  exhausts this function's input space composed with the velocity/timing
  modifiers below.

* ``engagement_surface_thresholds`` + ``engagement_surface_score`` — §29.4
  relative-threshold math. The thresholds scale with the *target* author's
  follower count, with the four configurable settings rows as floors.

* ``velocity_score`` + ``timing_score`` + ``apply_velocity_timing_modifiers``
  (Phase 7) — the two §29.3 trailing dimensions activated once
  ``reply_target_metrics_refresh`` is running (§17 Phase 7 job #4).
  velocity_score requires ≥2 snapshots so the differential rate exists;
  timing_score is computable from post age + author tier alone.
  The modifiers apply AFTER the base ladder per §29.3 trailing paragraph;
  the modifier function is the single composition point used by both
  ``score_reply_candidates`` and the resolver test.

The orchestrator in ``app.agent.tools`` (#6 ``score_reply_candidates``)
composes these helpers and writes the result onto a ``reply_targets`` row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

Score = int | None  # 0..3 dimensions; NULL when not yet computed

RecommendedAction = Literal["reply_now", "reply_if_time", "consider", "skip"]


ACTION_TO_SCORE: dict[RecommendedAction, int] = {
    "reply_now": 3,
    "reply_if_time": 2,
    "consider": 1,
    "skip": 0,
}

# RV2-28: spec-display ordering (high → low). Used in user-facing error
# messages so the tier presentation matches §29.3's prose. Lexical
# sort would produce ['consider', 'reply_if_time', 'reply_now', 'skip'],
# which doesn't match the spec's downgrade ladder.
RECOMMENDED_ACTIONS: tuple[RecommendedAction, ...] = (
    "reply_now",
    "reply_if_time",
    "consider",
    "skip",
)


# §29.3: the base resolver consumes only the four MVP dimensions. velocity,
# timing, and audience_quality are accepted in the signature so the call
# site is stable across phases; they are IGNORED by this function. The
# Phase 7 modifiers live in ``apply_velocity_timing_modifiers`` and are
# applied AFTER the base ladder. audience_quality is V1.2+-deferred per
# §29.1 and remains unused.
def resolve_recommended_action(
    relevance: Score,
    engagement_surface: Score,
    saturation: Score,
    reply_opportunity: Score,
    # The trailing three are accepted but never consumed here. Phase 7's
    # velocity + timing affect the action via apply_velocity_timing_modifiers
    # (composed by the caller); audience_quality remains V1.2+ deferred.
    velocity: Score = None,  # noqa: ARG001
    timing: Score = None,  # noqa: ARG001
    audience_quality: Score = None,  # noqa: ARG001
) -> RecommendedAction:
    """Deterministic mapping from the four MVP scores to an action label.

    Follows the §29.3 branch ladder verbatim:

    1. Any of the four MVP scores == 0  →  'skip' (a zero kills the row).
    2. All four >= 2                    →  'reply_now'.
    3. Relevance >= 2 AND reply_opportunity >= 2 (but not all four) → 'reply_if_time'.
    4. Otherwise                        →  'consider'.

    NULL inputs are illegal for the four MVP dimensions — the caller MUST
    have scored them before invoking this. NULL on velocity/timing is the
    pre-Phase-7 state (no metrics-refresh history yet) and is ignored.
    """
    mvp = (relevance, engagement_surface, saturation, reply_opportunity)
    if any(s is None for s in mvp):
        raise ValueError(
            "resolve_recommended_action requires all four MVP scores "
            "(relevance, engagement_surface, saturation, reply_opportunity); "
            f"got {mvp!r}"
        )
    if any(s == 0 for s in mvp):
        return "skip"
    if relevance >= 2 and engagement_surface >= 2 and saturation >= 2 and reply_opportunity >= 2:
        return "reply_now"
    if relevance >= 2 and reply_opportunity >= 2:
        return "reply_if_time"
    return "consider"


def engagement_surface_thresholds(
    target_author_follower_count: int | None,
    settings: dict[str, Any],
) -> tuple[int, int]:
    """Return ``(medium_threshold, high_threshold)`` per §29.4.

    When the target author's follower count is unknown (manual entry, no API
    enrichment yet), thresholds fall back to the floor values. The Queue UI
    is responsible for labelling the score with the "no author size" footnote
    so Daniel knows the row is using the conservative floors.
    """
    floor_med = int(settings["engagement_surface_floor_likes"])
    floor_high = int(settings["engagement_surface_high_floor_likes"])
    if target_author_follower_count is None:
        return floor_med, floor_high
    pct_med = float(settings["engagement_surface_pct_of_author"])
    pct_high = float(settings["engagement_surface_high_pct"])
    return (
        max(floor_med, int(pct_med * int(target_author_follower_count))),
        max(floor_high, int(pct_high * int(target_author_follower_count))),
    )


def engagement_footnote(
    target_author_follower_count: int | None,
    settings: dict[str, Any],
) -> str | None:
    """Footnote string when the floor (rather than the %-of-followers calc)
    is the active engagement-surface threshold — None otherwise.

    /review-2 🟡 #1 — the prior footnote rule only fired when the author
    follower count was NULL, missing the small-author case where the
    %-of-followers calculation produces a value below the floor and the
    floor wins. Daniel reads ``engagement_surface_score = 2`` and assumes
    a real per-author bar was crossed; in fact only the absolute floor was.
    """
    if target_author_follower_count is None:
        return "floor — no author size"
    floor_med = int(settings.get("engagement_surface_floor_likes", 15))
    pct_med = float(settings.get("engagement_surface_pct_of_author", 0.001))
    if int(pct_med * int(target_author_follower_count)) < floor_med:
        return (
            f"floor — author too small "
            f"({int(target_author_follower_count)} followers)"
        )
    return None


def saturation_score(reply_count: int) -> int:
    """Map current ``reply_count`` into the §29.3 saturation dimension.

    The spec frames the dimension by where Daniel's reply would land in the
    thread order:

    * 0 — "#500+; thread is dead"
    * 1 — "top 100; thread crowded"
    * 2 — "top 30; thread active"
    * 3 — "top 10; thread fresh"

    Mapping at the canonical band boundaries:
    """
    rc = max(0, int(reply_count or 0))
    if rc >= 100:
        return 0
    if rc >= 30:
        return 1
    if rc >= 10:
        return 2
    return 3


def engagement_surface_score(
    like_count: int,
    medium_threshold: int,
    high_threshold: int,
) -> int:
    """Map a like count into the 0..3 engagement-surface dimension (§29.3).

    Boundaries match the spec text:

    * ``like_count < medium``     →  0  ("below medium threshold")
    * ``medium <= lc < high``     →  1  ("between medium and high")
    * ``high  <= lc < 3 * high``  →  2  ("above high, below saturated viral")
    * ``lc   >= 3 * high``        →  3  ("above high, comment thread still navigable")

    The 2→3 boundary at ``3 * high_threshold`` is this phase's working
    interpretation of "saturated viral" — §29.3 names the band but doesn't
    give a numeric anchor. Surfaced for Daniel's review in the Phase 5.6
    closeout notes; tighten the constant here if §29.3 is amended.
    """
    if like_count < medium_threshold:
        return 0
    if like_count < high_threshold:
        return 1
    if like_count < high_threshold * 3:
        return 2
    return 3


# §29.5 v1 reply_intent enum. Kept here as the single source of truth so the
# drift check in tools.py / agent_system_prompt.md / spec.md can compare
# against one canonical Python tuple instead of duplicating the literals.
REPLY_INTENT_ENUM: tuple[str, ...] = (
    "growth",
    "icp_discovery",
    "relationship",
    "product_adjacent",
    "thought_leadership",
)

# §29.7 skip_reason dropdown values.
SKIP_REASON_ENUM: tuple[str, ...] = (
    "off_topic",
    "ragebait",
    "saturation",
    "cant_add_value",
    "target_deleted",
    "blocked_by_author",
    "other",
)

# §29.6 discovered_via — MVP set only; 'grok_semantic' is V1.2-deferred per
# §29.1 and explicitly NOT included in the CHECK constraint at this phase.
DISCOVERED_VIA_ENUM: tuple[str, ...] = (
    "manual",
    "agent_score",
    "next_rep_seed",
    "v1.1_api_search",
)

# §29.6 status lifecycle.
STATUS_ENUM: tuple[str, ...] = (
    "candidate",
    "drafted",
    "posted",
    "skipped",
    "expired",
    "target_deleted",
)


# ---------------------------------------------------------------------------
# Phase 7 — velocity + timing dimensions per §29.3 trailing block.
# ---------------------------------------------------------------------------
# The two new dimensions activate once the reply_target_metrics_refresh
# job (§17 Phase 7 job #4) has been running long enough to have ≥2
# snapshots per candidate. velocity is undefined (None) before then;
# timing is computable from post age + author tier alone, so it's
# defined on every candidate including a freshly-pasted URL.


@dataclass(frozen=True, slots=True)
class ReplyTargetSnapshot:
    """One row of ``reply_target_snapshots`` — the minimum surface
    velocity_score needs.

    The dataclass exists so velocity_score can be unit-tested without a
    DB round-trip. The metrics-refresh job constructs these from
    sqlite3.Row instances; tests construct them directly with literal
    values.

    Only the fields velocity_score reads are required; the schema has
    additional columns (impressions, bookmarks, etc.) that aren't used
    for the velocity differential.
    """

    checked_at_utc: str
    computed_likes_per_hour: float | None
    # computed_replies_per_hour is part of the schema but velocity_score
    # currently keys off likes/hour only — the rubric in §29.3 is about
    # engagement-pool growth, of which likes are the highest-volume
    # signal. Replies-per-hour is captured for future calibration; if
    # §29.3 is amended to include it, the rubric here is the single
    # point to update.
    computed_replies_per_hour: float | None = None


def velocity_score(snapshots: Iterable[ReplyTargetSnapshot]) -> int | None:
    """Map a candidate's snapshot history into the §29.3 velocity dimension.

    Returns 0..3 per the §29.3 Velocity rubric:

    * 0 — Decaying or stale. Latest rate ≤ 50% of the previous rate.
    * 1 — Flat. |latest − previous| < ``_VELOCITY_FLAT_EPSILON`` likes/hour.
    * 2 — Modest engagement gain over last hour. Within (0.5, 2.0)× previous.
    * 3 — Accelerating. Latest rate ≥ 2.0× previous rate AND previous > 0.

    Returns ``None`` when fewer than two snapshots exist for the candidate
    — the differential rate is undefined. This is the pre-Phase-7-running
    state and the state immediately after a fresh candidate is added; the
    resolver's modifier path handles ``None`` as "no upgrade applied"
    (engagement_surface unchanged).

    Snapshots are sorted by ``checked_at_utc`` ascending so the two
    most-recent rows are compared. NULL likes/hour values (the first
    snapshot for a candidate carries NULL for the computed-delta columns)
    are treated as 0.0 for comparison purposes; that yields 'flat' for
    the second-snapshot-only case, which is what spec calls for ("modest
    gain" needs a prior baseline to be modest *against*).
    """
    sorted_snaps = sorted(snapshots, key=lambda s: s.checked_at_utc)
    if len(sorted_snaps) < 2:
        return None
    prev_rate = float(sorted_snaps[-2].computed_likes_per_hour or 0.0)
    cur_rate = float(sorted_snaps[-1].computed_likes_per_hour or 0.0)

    # Decay band — latest rate dropped meaningfully. Per RV2-5, the
    # original 50%-or-less rule misses mid-band declines on low-rate
    # threads: prev=1.5, cur=0.8 is a ~47% drop that's clearly
    # decaying, but cur > 0.5×prev so the strict ≤50% gate missed it
    # and the 1.0-epsilon flat band mis-classified it as flat. The fix
    # widens the decay band to 60% for prev_rate < 2.0 (where small
    # absolute changes correspond to large relative changes) and
    # preserves the 50% boundary for higher rates where the §13 noise-
    # floor discipline still applies.
    if prev_rate > 0.0:
        decay_ratio = 0.6 if prev_rate < 2.0 else 0.5
        if cur_rate <= prev_rate * decay_ratio:
            return 0
    if prev_rate == 0.0 and cur_rate == 0.0:
        return 0

    # Flat band — small absolute delta on likes/hour. The 1.0 threshold
    # matches the §13 noise-floor discipline; at high rates we also
    # consider a fractional epsilon (0.1 × prev_rate) so a 10-like/hour
    # thread with a 1.5-like/hour delta isn't classified as a sharp
    # acceleration.
    flat_epsilon = max(_VELOCITY_FLAT_EPSILON, 0.1 * prev_rate)
    if abs(cur_rate - prev_rate) < flat_epsilon:
        return 1

    # Accelerating — latest rate is ≥ 2.0× previous AND previous > 0.
    # The previous > 0 guard prevents "0 → 1 like/hour" from registering
    # as accelerating; rate-from-zero is more often noise than a trend.
    if prev_rate > 0.0 and cur_rate >= prev_rate * 2.0:
        return 3

    # Default to modest gain.
    return 2


_VELOCITY_FLAT_EPSILON: float = 1.0
"""Below 1 like/hour absolute delta between consecutive snapshots, treat
as flat. The §13 noise-floor discipline applies — small per-hour rates
are not statistically distinguishable from zero on a single post."""


# Timing tier boundaries from §29.3:
#
#   Early (3)      | within 30 min if author follower count ≥ _LARGE_AUTHOR_FLOOR
#                  | within 6h    otherwise
#   Within  (2)    | within 4h if large; within 24h if small-niche
#   Late    (1)    | within 12h if large; within 72h if small-niche
#   Past    (0)    | beyond
#
# These are deliberate, calibratable numbers; the spec gives the bookends
# (first 30 min / first 6h) and leaves the in-between cell anchors as
# implementation choices. Tightened during Phase 7 acceptance review if
# §29.3 is amended.
_LARGE_AUTHOR_FLOOR: int = 5_000

_TIMING_BANDS_LARGE: tuple[tuple[int, int], ...] = (
    (30, 3),       # ≤ 30 minutes → 3
    (4 * 60, 2),   # ≤ 4 hours    → 2
    (12 * 60, 1),  # ≤ 12 hours   → 1
)
_TIMING_BANDS_SMALL: tuple[tuple[int, int], ...] = (
    (6 * 60, 3),    # ≤ 6 hours   → 3
    (24 * 60, 2),   # ≤ 24 hours  → 2
    (72 * 60, 1),   # ≤ 72 hours  → 1
)


def timing_score(
    post_age_minutes: int,
    target_author_follower_count: int | None,
) -> int:
    """Map post age + author tier into the §29.3 timing dimension.

    Returns 0..3 per the §29.3 Timing rubric. Author tier determines the
    relevant band table:

    * Large account (follower_count >= 5,000) → tighter optimal window
      (first 30 min for "early", within 4h for "within window").
    * Small-niche account (follower_count < 5,000 OR NULL) → wider window
      (first 6h for "early", within 24h for "within window").

    Negative ``post_age_minutes`` is clamped to 0 (a freshly-posted
    candidate registers as maximally early).
    """
    age = max(0, int(post_age_minutes or 0))
    is_large = (
        target_author_follower_count is not None
        and int(target_author_follower_count) >= _LARGE_AUTHOR_FLOOR
    )
    bands = _TIMING_BANDS_LARGE if is_large else _TIMING_BANDS_SMALL
    for cutoff_minutes, score in bands:
        if age <= cutoff_minutes:
            return score
    return 0


# Action downgrade ladder for the low-timing modifier. The 'skip' case is
# a fixed point — already at floor.
_DOWNGRADE_ONE_TIER: dict[RecommendedAction, RecommendedAction] = {
    "reply_now": "reply_if_time",
    "reply_if_time": "consider",
    "consider": "skip",
    "skip": "skip",
}


def apply_velocity_timing_modifiers(
    base_engagement_surface: int,
    base_recommended_action: RecommendedAction,
    velocity: int | None,
    timing: int | None,
) -> tuple[int, RecommendedAction]:
    """Apply the §29.3 trailing modifiers post-base-resolver.

    Rules per §29.3:

    * High velocity (velocity_score >= 2) upgrades engagement_surface_score
      by one tier, capped at 3.
    * Low timing (timing_score < 2) downgrades recommended_action by one
      tier (reply_now → reply_if_time → consider → skip → skip).

    The two modifiers compose independently — the upgraded
    engagement_surface does NOT re-trigger the base ladder. Spec wording:
    "The upgrade/downgrade applies AFTER the base four-dimension resolver
    runs; the modifiers are deterministic and pure-function-testable."

    ``velocity=None`` (pre-Phase-7-history) does NOT apply the upgrade —
    a missing dimension cannot move a score. ``timing=None`` is the same
    contract for the downgrade. In practice timing is always computable
    from age + author tier so ``None`` arrives only via the resolver test
    enumerating it as one of the four input states; the rule applies
    consistently regardless of how it got there.
    """
    if base_engagement_surface < 0 or base_engagement_surface > 3:
        raise ValueError(
            f"base_engagement_surface must be in 0..3; got {base_engagement_surface!r}"
        )
    if base_recommended_action not in _DOWNGRADE_ONE_TIER:
        raise ValueError(
            # RV2-28: spec-tiered order, not lexical.
            f"base_recommended_action must be one of "
            f"{list(RECOMMENDED_ACTIONS)}; got {base_recommended_action!r}"
        )

    adjusted_engagement = base_engagement_surface
    if velocity is not None and velocity >= 2:
        adjusted_engagement = min(base_engagement_surface + 1, 3)

    adjusted_action: RecommendedAction = base_recommended_action
    if timing is not None and timing < 2:
        adjusted_action = _DOWNGRADE_ONE_TIER[base_recommended_action]

    return adjusted_engagement, adjusted_action
