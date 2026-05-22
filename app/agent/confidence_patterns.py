"""Confidence-label analytical-claim patterns (§28.14).

Each regex matches a class of analytical claim that, per rule #14,
MUST carry a `<confidence>` tag. The orchestrator's
`detect_untagged_claims` (in `app/agent/session.py`) scans an assistant
message for these patterns and counts the matches that are NOT inside a
`<confidence>...</confidence>` tag — each unmatched analytical claim
counts as an IWH humility failure.

Pattern design rules:

  * Each entry is `(name, compiled_re, example_match)` so docstring
    coverage is co-located with the pattern itself.
  * Patterns are case-insensitive.
  * Patterns match the CLAIM phrasing, not the entire sentence.
    Matching narrowly lets the orchestrator pin the position of the
    untagged claim.
  * Add a new pattern when reviewers see the agent slip an
    overconfident claim past — and add a unit test pinning the example.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _AnalyticalPattern:
    name: str
    pattern: re.Pattern[str]
    example: str


# Each pattern is anchored to the kind of phrasing that asserts a
# data-grounded conclusion: percentage changes, attribution verbs,
# ranking claims, causal verbs, growth narratives.
ANALYTICAL_PATTERNS: tuple[_AnalyticalPattern, ...] = (
    _AnalyticalPattern(
        name="percentage_change",
        pattern=re.compile(
            r"\b\d+(?:\.\d+)?\s*%\s+(?:increase|decrease|growth|drop|jump|spike|gain|loss|change|lift|decline)\b",
            re.IGNORECASE,
        ),
        example="a 24% increase in engagement",
    ),
    _AnalyticalPattern(
        name="lane_winner",
        pattern=re.compile(
            r"\blane\b[^.!?\n]{0,80}\b(?:is the (?:winner|best|leader|top performer))\b",
            re.IGNORECASE,
        ),
        example="the build lane is the winner",
    ),
    _AnalyticalPattern(
        name="outperformed",
        pattern=re.compile(
            r"\b(?:outperformed|outranked|crushed|dominated|beat)\b",
            re.IGNORECASE,
        ),
        example="self-lane outperformed stir last week",
    ),
    _AnalyticalPattern(
        name="caused_by",
        pattern=re.compile(
            r"\b(?:this caused|caused by|because of (?:this|the)|responsible for|drove the|led to)\b",
            re.IGNORECASE,
        ),
        example="this caused the follower jump",
    ),
    _AnalyticalPattern(
        name="data_shows",
        pattern=re.compile(
            r"\b(?:the data (?:shows|tells us|says)|metrics show|numbers (?:show|tell|prove)|clearly shows)\b",
            re.IGNORECASE,
        ),
        example="the data shows you should ship more replies",
    ),
    _AnalyticalPattern(
        name="ranking_assertion",
        pattern=re.compile(
            r"\b(?:top performer|bottom performer|highest[- ]engagement|lowest[- ]engagement|best (?:post|reply|lane) of)\b",
            re.IGNORECASE,
        ),
        example="your highest-engagement lane is build_icp",
    ),
    _AnalyticalPattern(
        name="proven_to",
        pattern=re.compile(
            r"\b(?:proven to|proves that|definitively|certainly will|guaranteed to)\b",
            re.IGNORECASE,
        ),
        example="this hook style is proven to work",
    ),
    _AnalyticalPattern(
        name="growth_attribution",
        pattern=re.compile(
            r"\b(?:gained|added|picked up|lost)\s+\d+\s+(?:followers?|impressions?|likes?|replies?)\b",
            re.IGNORECASE,
        ),
        example="gained 12 followers this week",
    ),
)


def has_analytical_claim(text: str) -> bool:
    """True if any analytical pattern matches anywhere in `text`.

    Cheap one-shot check — used by tests and as a doc-grade gate.
    For positional counting, use `find_analytical_claim_spans`.
    """
    return any(p.pattern.search(text) for p in ANALYTICAL_PATTERNS)


def find_analytical_claim_spans(text: str) -> list[tuple[int, int, str]]:
    """Return a list of (start, end, pattern_name) for each analytical
    claim found in `text`. Spans are NOT de-duplicated across patterns —
    a single phrase that matches two patterns counts twice on purpose so
    the orchestrator over-detects rather than under-detects.
    """
    spans: list[tuple[int, int, str]] = []
    for p in ANALYTICAL_PATTERNS:
        for m in p.pattern.finditer(text):
            spans.append((m.start(), m.end(), p.name))
    spans.sort(key=lambda s: s[0])
    return spans
