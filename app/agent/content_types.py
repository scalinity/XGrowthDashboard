"""V/G/P/P content type axis (§28.17, Phase 5.9).

Orthogonal to pillar/audience/CTA — pillar is *topic* (stir/build/self),
content_type is *purpose*. Distilled from Jacob Edmunds's framework and
reconciled with XGrowth's graduated-confidence discipline.

The four definitions below are load-bearing; the agent reads them in
the rendered system prompt Section 6, the agent's draft must declare
which one applies, and ``v_content_type_performance`` slices outcomes
by this axis. The orchestrator refuses any draft with
``content_type='unspecified'`` even though the CHECK constraint permits
it (the CHECK exists so the migration can backfill legacy rows).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


# The four allowed types for new agent drafts. 'unspecified' exists at
# the CHECK level for the legacy-row backfill path but is NEVER allowed
# by the orchestrator on save.
CONTENT_TYPES: tuple[str, ...] = ("value", "growth", "personality", "proof")
UNSPECIFIED: str = "unspecified"
ALL_CONTENT_TYPES_FOR_VALIDATION: tuple[str, ...] = CONTENT_TYPES + (UNSPECIFIED,)


# Load-bearing definitions — these are the canonical descriptions the
# system prompt renders. Edits here must keep the V/G/P/P shape from
# §28.17. Phrasing rephrased from the source video where it would
# otherwise import hype; load-bearing distinctions kept verbatim.
CONTENT_TYPE_DEFINITIONS: dict[str, dict[str, str]] = {
    "value": {
        "label": "Value",
        "what_it_does": (
            "Teaches the reader how to do something. Specific, actionable, "
            "holds nothing back."
        ),
        "example": (
            "Here's the exact prompt structure I use for kitchen-scanner "
            "item recognition."
        ),
    },
    "growth": {
        "label": "Growth",
        "what_it_does": (
            "Aims at a broader audience: reacts to niche news, shares a "
            "polarizing-but-genuine opinion, starts a conversation. "
            "Distinct from value because the goal is reach via "
            "conversation, not knowledge transfer."
        ),
        "example": (
            "Hot take: kitchen scanners that don't ground in nutrition "
            "data will all converge to the same bland LLM recipes."
        ),
    },
    "personality": {
        "label": "Personality",
        "what_it_does": (
            "Humanizes. Behind-the-scenes, running jokes, the actual "
            "quirks of being Daniel. Pulls back the curtain. Pairs with "
            "personality_lore (§28.21)."
        ),
        "example": (
            "Day 3 of forgetting to put the rice on before the protein "
            "finishes — Cook Mode timing logic born from real grief."
        ),
    },
    "proof": {
        "label": "Proof",
        "what_it_does": (
            "Builds credibility. Milestones, viral posts you wrote, "
            "testimonials, social proof. Distinguishes from value because "
            "anyone can copy value; only the original author can show "
            "proof."
        ),
        "example": (
            "100 followers. Still pre-launch. The build-in-public bet is "
            "working faster than I expected."
        ),
    },
}


class ContentTypeInvalidError(ValueError):
    """Raised when a save-draft path receives a content_type outside the enum.

    Distinguished from a plain ValueError so the orchestrator can render a
    targeted refusal message in the agent's tool result.
    """


def validate_for_save(content_type: str | None) -> str:
    """Validate a content_type for a save_draft_* call.

    Raises ``ContentTypeInvalidError`` if the value is missing, not in the
    enum, or equals ``'unspecified'`` — the orchestrator refuses
    ``unspecified`` from the agent per §28.17 even though the CHECK
    permits it.

    Returns the validated string on success (lower-cased + stripped).
    """
    if content_type is None or not isinstance(content_type, str):
        raise ContentTypeInvalidError(
            "content_type is required on every new agent draft per "
            f"§28.17. Pass one of {CONTENT_TYPES}."
        )
    cleaned = content_type.strip().lower()
    if cleaned == UNSPECIFIED:
        raise ContentTypeInvalidError(
            "content_type='unspecified' is rejected by the orchestrator "
            f"(§28.17). Pick a real type: {CONTENT_TYPES}."
        )
    if cleaned not in CONTENT_TYPES:
        raise ContentTypeInvalidError(
            f"content_type={content_type!r} not in §28.17 enum {CONTENT_TYPES}."
        )
    return cleaned


# ---------------------------------------------------------------------------
# get_content_type_gaps — read-only tool surface (§28.17 tool #16).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ContentTypeGap:
    """Per-type count + share + rationale fragment for the Today line."""

    content_type: str
    post_count: int


def get_content_type_gaps(
    conn: sqlite3.Connection, *, window_days: int = 7
) -> dict:
    """Counts per content type for the rolling window.

    Returns ``{counts: {type: int}, window_days: int, under_represented:
    str|None, rationale: str}``.

    The under-represented type is the V/G/P/P value with the lowest
    count over the window. Ties resolve to the canonical
    ``CONTENT_TYPES`` order (value > growth > personality > proof) so
    the suggestion is deterministic. When every type is exactly even
    across the window OR when there are zero posts in the window the
    suggestion is ``None`` and the rationale reads "even spread".
    """
    n = int(window_days)
    rows = conn.execute(
        """
        SELECT content_type, COUNT(*) AS n
        FROM posts
        WHERE created_date >= date('now', ?)
        GROUP BY content_type
        """,
        (f"-{n} days",),
    ).fetchall()
    raw_counts: dict[str, int] = {row["content_type"]: int(row["n"]) for row in rows}

    counts: dict[str, int] = {ct: int(raw_counts.get(ct, 0)) for ct in CONTENT_TYPES}
    counts[UNSPECIFIED] = int(raw_counts.get(UNSPECIFIED, 0))

    real_counts = {ct: counts[ct] for ct in CONTENT_TYPES}
    total_real = sum(real_counts.values())

    if total_real == 0:
        return {
            "counts": counts,
            "window_days": n,
            "under_represented": None,
            "rationale": (
                f"No classified posts in the last {n} days — pick what "
                "you're moved by today."
            ),
        }

    min_count = min(real_counts.values())
    max_count = max(real_counts.values())
    if min_count == max_count:
        return {
            "counts": counts,
            "window_days": n,
            "under_represented": None,
            "rationale": "even spread — pick what you're moved by today.",
        }

    # Tie-break in canonical order so suggestions don't flap turn-to-turn.
    under_represented = next(ct for ct in CONTENT_TYPES if real_counts[ct] == min_count)
    leader = next(ct for ct in CONTENT_TYPES if real_counts[ct] == max_count)
    rationale = (
        f"you've shipped {real_counts[leader]} {leader} post"
        f"{'s' if real_counts[leader] != 1 else ''} this week, "
        f"{real_counts[under_represented]} {under_represented}."
    )
    return {
        "counts": counts,
        "window_days": n,
        "under_represented": under_represented,
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# Setting helper.
# ---------------------------------------------------------------------------
def get_recommendation_window_days(conn: sqlite3.Connection) -> int:
    """Read ``content_type_recommendation_window_days`` (default 7)."""
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?",
        ("content_type_recommendation_window_days",),
    ).fetchone()
    if row is None:
        return 7
    try:
        return int(json.loads(row["value_json"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return 7


# ---------------------------------------------------------------------------
# Rendering helpers for the system prompt + UI.
# ---------------------------------------------------------------------------
def render_taxonomy_table_markdown() -> str:
    """Render the V/G/P/P definitions as a markdown table.

    Used by the system prompt Section 6 splice and by Settings hints.
    Kept here so the table lives next to CONTENT_TYPE_DEFINITIONS.
    """
    lines = [
        "| Type | What it does | Example |",
        "| --- | --- | --- |",
    ]
    for ct in CONTENT_TYPES:
        d = CONTENT_TYPE_DEFINITIONS[ct]
        lines.append(
            f"| `{ct}` | {d['what_it_does']} | _{d['example']}_ |"
        )
    return "\n".join(lines)


__all__ = [
    "ALL_CONTENT_TYPES_FOR_VALIDATION",
    "CONTENT_TYPES",
    "CONTENT_TYPE_DEFINITIONS",
    "ContentTypeGap",
    "ContentTypeInvalidError",
    "UNSPECIFIED",
    "get_content_type_gaps",
    "get_recommendation_window_days",
    "render_taxonomy_table_markdown",
    "validate_for_save",
]
