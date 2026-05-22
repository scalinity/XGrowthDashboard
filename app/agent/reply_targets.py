"""Reply-target scoring helpers — spec.md §29.

Two pure-function surfaces live here so they can be exercised without a DB:

* ``resolve_recommended_action`` — the deterministic resolver from §29.3.
  The dashboard never produces a composite "viability" score; the action
  label is derived from the four MVP dimensions by a fixed branch ladder.
  The 256-combo test (tests/test_reply_target_resolver.py) exhausts this
  function's input space.

* ``engagement_surface_thresholds`` + ``engagement_surface_score`` — §29.4
  relative-threshold math. The thresholds scale with the *target* author's
  follower count, with the four configurable settings rows as floors.

The orchestrator in ``app.agent.tools`` (#6 ``score_reply_candidates``)
composes these helpers and writes the result onto a ``reply_targets`` row.
"""

from __future__ import annotations

from typing import Any, Literal

Score = int | None  # 0..3 at MVP; NULL for V1.1+ dimensions not yet computed

RecommendedAction = Literal["reply_now", "reply_if_time", "consider", "skip"]


ACTION_TO_SCORE: dict[RecommendedAction, int] = {
    "reply_now": 3,
    "reply_if_time": 2,
    "consider": 1,
    "skip": 0,
}


# §29.3 explicitly omits velocity / timing / audience_quality from MVP. They
# are accepted as parameters so the V1.1+ caller signature is stable, but they
# never alter the resolver's output until §29.3 is amended.
def resolve_recommended_action(
    relevance: Score,
    engagement_surface: Score,
    saturation: Score,
    reply_opportunity: Score,
    # V1.1+ — ignored at MVP per §29.3 trailing paragraph.
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
    have scored them before invoking this. NULL on a V1.1+ dimension is the
    expected state at MVP and is ignored.
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
