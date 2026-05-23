"""RV2-27: pin the offline-lint pattern → label mapping.

The five offline pattern tuples in app/agent/lint.py are the
deterministic fallback when Haiku is unreachable. They were private
pre-RV2-27 and unaddressable from focused tests — a false-positive
report from production required guessing which pattern fired. These
tests pin each pattern → label mapping so a future regex tweak fails
loudly if it changes coverage.

The tests are parametrized over each public pattern tuple; if a
pattern is removed, renamed, or its semantics change, the targeted
test fails with the offending entry's index.
"""

from __future__ import annotations

import re

import pytest

from app.agent import lint


@pytest.mark.parametrize("tuple_name,patterns,arity", [
    ("ENGAGEMENT_BAIT_PATTERNS", lint.ENGAGEMENT_BAIT_PATTERNS, 2),
    # Phase 10 W5 — REPLY_QUALITY_PATTERNS grew a third slot carrying
    # the canonical enum value, eliminating the prior substring-fallback
    # _label_to_failure_mode helper.
    ("REPLY_QUALITY_PATTERNS", lint.REPLY_QUALITY_PATTERNS, 3),
    ("RAGEBAIT_PATTERNS", lint.RAGEBAIT_PATTERNS, 2),
    ("MEME_PATTERNS", lint.MEME_PATTERNS, 2),
    ("LOW_QUALITY_THREAD_PATTERNS", lint.LOW_QUALITY_THREAD_PATTERNS, 2),
])
def test_pattern_tuples_compile_and_have_labels(tuple_name, patterns, arity):
    """RV2-27 + Phase 10 W5: every pattern compiles AND has the right arity."""
    assert patterns, f"{tuple_name} must be non-empty"
    for i, entry in enumerate(patterns):
        assert isinstance(entry, tuple) and len(entry) == arity, (
            f"{tuple_name}[{i}] must be a {arity}-tuple"
        )
        pattern, label = entry[0], entry[1]
        assert isinstance(pattern, str) and pattern, (
            f"{tuple_name}[{i}] pattern must be non-empty string"
        )
        assert isinstance(label, str) and label, (
            f"{tuple_name}[{i}] label must be non-empty string"
        )
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            pytest.fail(
                f"{tuple_name}[{i}] pattern {pattern!r} does not compile: {exc}"
            )
        # Phase 10 W5 — when the arity is 3, the third slot must be a
        # canonical enum value in REPLY_QUALITY_FAILURE_MODES.
        if arity == 3:
            assert entry[2] in lint.REPLY_QUALITY_FAILURE_MODES, (
                f"{tuple_name}[{i}] enum={entry[2]!r} not in "
                f"REPLY_QUALITY_FAILURE_MODES"
            )


def test_engagement_bait_unpopular_opinion_not_in_dark_pattern():
    """Pin the dark-pattern vs thread-classifier disambiguation.
    Pre-RV2-27 the contract was implicit; this test makes it explicit:
    'unpopular opinion' framing is RAGEBAIT (thread-side), NOT an
    engagement-bait dark pattern (draft-side)."""
    # No engagement-bait pattern matches "unpopular opinion".
    for pattern, _label in lint.ENGAGEMENT_BAIT_PATTERNS:
        assert not re.search(
            pattern, "Unpopular opinion: most founders are LARPing.",
            flags=re.IGNORECASE,
        ), f"'unpopular opinion' should not match dark-pattern {pattern!r}"
    # Ragebait pattern DOES match.
    assert any(
        re.search(p, "Unpopular opinion: most founders are LARPing.", re.IGNORECASE)
        for p, _ in lint.RAGEBAIT_PATTERNS
    )


def test_reply_quality_self_promo_pattern_fires():
    """Pin the §28.18 'check out my stuff' detection.
    Phase 10 W5: REPLY_QUALITY_PATTERNS entries are 3-tuples now."""
    sample = "Great post! Check out my product."
    assert any(
        re.search(entry[0], sample, re.IGNORECASE)
        for entry in lint.REPLY_QUALITY_PATTERNS
    )


def test_low_quality_thread_rt_bait_pattern_fires():
    """RT-bait must surface as low-quality thread signal."""
    sample = "RT if you agree that React is overrated!"
    assert any(
        re.search(p, sample, re.IGNORECASE)
        for p, _ in lint.LOW_QUALITY_THREAD_PATTERNS
    )
