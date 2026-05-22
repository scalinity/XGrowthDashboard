"""SessionState — IWH counter + orchestrator decision logic (§28.2 rule #13).

The IWH (intelligence / wisdom / humility) revision counter lives here
and in ``agent_drafts.iwh_attempt_index``. It is NEVER passed into the
agent's prompt as a number the agent can read and mutate. The agent
emits ``<iwh_self_score>`` tags; the orchestrator parses them, runs the
lint pass, and increments the counter when either:

  * any of the three IWH scores < ``iwh_self_score_minimum`` (default 2), OR
  * the dark-pattern lint returns ``dark_pattern_detected = true``.

On attempt ``iwh_max_revision_attempts + 1`` (default 3+1=4), the
orchestrator refuses to call ``save_draft_*`` and emits a refusal back
into the conversation.

This module is pure — no Streamlit dependency. The chat view in Session 2
calls into ``decide_save_or_revise`` to know whether the agent's draft
should be persisted, revised, or refused.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from app.agent import lint, niche
from app.agent.confidence_patterns import (
    ANALYTICAL_PATTERNS,
    find_analytical_claim_spans,
)

DEFAULT_IWH_SELF_SCORE_MINIMUM: int = 2
DEFAULT_IWH_MAX_REVISION_ATTEMPTS: int = 3

# Phase 5.8 / §28.14 — confidence label parsing.
# P58R-19 — bake the four labels into the regex so the parser fails
# closed at the regex layer; the post-allowlist check in
# extract_confidence_labels remains as defense-in-depth.
CONFIDENCE_LABELS: tuple[str, ...] = ("fact", "inference", "speculation", "mixed")
_CONFIDENCE_TAG_RE = re.compile(
    r"<confidence>\s*(fact|inference|speculation|mixed)\s*</confidence>",
    flags=re.IGNORECASE,
)
# Tie-breaking when multiple labels appear in the same message: least
# confident wins, which favors humility. Ordering reads bottom-up at
# tie time — speculation > inference > mixed > fact.
_TIE_BREAK_ORDER: tuple[str, ...] = ("speculation", "inference", "mixed", "fact")


# ---------------------------------------------------------------------------
# IWH self-score parsing — the only path the agent has to communicate its
# scores. The parser is deliberately strict so an agent that omits or
# malforms the tag is treated as failing the IWH check (defensive default).
# ---------------------------------------------------------------------------
_IWH_TAG_RE = re.compile(
    r"<iwh_self_score>\s*(\{[^<]+?\})\s*</iwh_self_score>",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class IwhScore:
    intelligence: int
    wisdom: int
    humility: int

    def min_score(self) -> int:
        return min(self.intelligence, self.wisdom, self.humility)

    def to_dict(self) -> dict[str, int]:
        return {
            "intelligence": self.intelligence,
            "wisdom": self.wisdom,
            "humility": self.humility,
        }


def parse_iwh_self_score(assistant_text: str) -> IwhScore | None:
    """Extract the IWH self-score from assistant text.

    The system prompt encourages the agent to propose 2-3 variants with
    one ``<iwh_self_score>`` tag per variant. When multiple tags are
    present we take the per-axis MINIMUM across all parsed tags — this
    is defensive: the orchestrator gates on the chosen draft_text, but
    can't reliably match a tag to a variant from text alone, so the
    conservative-floor reading prevents an unrelated 3/3/3 tag from
    rescuing a 1/1/1 score elsewhere in the message.

    Returns ``None`` if no tag is present or none can be parsed — the
    caller treats this as a failed IWH check (defensive default).
    """
    parsed: list[IwhScore] = []
    for match in _IWH_TAG_RE.finditer(assistant_text):
        try:
            data = json.loads(match.group(1))
            parsed.append(
                IwhScore(
                    intelligence=int(data["intelligence"]),
                    wisdom=int(data["wisdom"]),
                    humility=int(data["humility"]),
                )
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue
    if not parsed:
        return None
    if len(parsed) == 1:
        return parsed[0]
    return IwhScore(
        intelligence=min(s.intelligence for s in parsed),
        wisdom=min(s.wisdom for s in parsed),
        humility=min(s.humility for s in parsed),
    )


# ---------------------------------------------------------------------------
# Settings helpers — delegate to app.agent.settings_io (P59A-W8 DRY).
# ---------------------------------------------------------------------------
def get_iwh_self_score_minimum(conn: sqlite3.Connection) -> int:
    from app.agent import settings_io
    return settings_io.get_int(
        conn, "iwh_self_score_minimum", DEFAULT_IWH_SELF_SCORE_MINIMUM
    )


def get_iwh_max_revision_attempts(conn: sqlite3.Connection) -> int:
    from app.agent import settings_io
    return settings_io.get_int(
        conn, "iwh_max_revision_attempts", DEFAULT_IWH_MAX_REVISION_ATTEMPTS
    )


# ---------------------------------------------------------------------------
# Phase 5.8 / §28.14 — confidence label extraction + untagged-claim detection.
# ---------------------------------------------------------------------------
def extract_confidence_labels(message_text: str) -> list[str]:
    """Pull every `<confidence>label</confidence>` from `message_text`.

    Validates each label against the CONFIDENCE_LABELS allowlist.
    Unknown labels are silently dropped — the corresponding IWH
    humility-failure increment is owned by `detect_untagged_claims`, so
    we don't double-count here.
    """
    labels: list[str] = []
    for m in _CONFIDENCE_TAG_RE.finditer(message_text):
        cand = m.group(1).strip().lower()
        if cand in CONFIDENCE_LABELS:
            labels.append(cand)
    return labels


def dominant_confidence_label(labels: list[str]) -> str | None:
    """Most frequent label; ties broken by _TIE_BREAK_ORDER (least
    confident wins). Returns None when the list is empty.
    """
    if not labels:
        return None
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    max_count = max(counts.values())
    contenders = [label for label, c in counts.items() if c == max_count]
    if len(contenders) == 1:
        return contenders[0]
    for ordered in _TIE_BREAK_ORDER:
        if ordered in contenders:
            return ordered
    return contenders[0]


def detect_untagged_claims(message_text: str) -> int:
    """Count analytical-claim regex matches that are NOT inside any
    `<confidence>...</confidence>` tag span.

    A claim is "tagged" if any of these hold:
      * the claim sits immediately before a `<confidence>` tag (within
        80 chars — agents typically write `claim <confidence>fact</confidence>`),
      * the claim sits immediately after a `<confidence>` tag (within
        80 chars — leading-tag pattern `<confidence>fact</confidence>: claim`
        is plausible even if not dominant),
      * OR the claim is inside a `<confidence>...</confidence>` span.

    The 80-char proximity heuristic forgives natural punctuation/spacing
    between the claim and its tag without forgiving wide gaps that would
    let an unrelated tag rescue a stray claim.
    """
    if not message_text:
        return 0
    spans = find_analytical_claim_spans(message_text)
    if not spans:
        return 0
    tag_spans = [m.span() for m in _CONFIDENCE_TAG_RE.finditer(message_text)]
    untagged = 0
    for start, end, _name in spans:
        tagged = False
        for tag_start, tag_end in tag_spans:
            # Trailing tag: claim ... <confidence>fact</confidence>
            if tag_start >= end and tag_start - end <= 80:
                tagged = True
                break
            # Leading tag: <confidence>fact</confidence> ... claim
            if tag_end <= start and start - tag_end <= 80:
                tagged = True
                break
            # Wrap-around: claim sits inside the tag span itself
            if start >= tag_start and end <= tag_end:
                tagged = True
                break
        if not tagged:
            untagged += 1
    return untagged


def humility_penalty_for_untagged(untagged_count: int) -> int:
    """One humility-point drop per untagged claim, per spec §28.14.

    Surfaced here as a named function so `decide_save_or_revise` can
    apply it consistently and tests can pin the policy.
    """
    return int(untagged_count) if untagged_count > 0 else 0


# ---------------------------------------------------------------------------
# Phase 5.9 / §28.2 rule #15 — niche must be defined before drafting.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NicheGateResult:
    """Outcome of the §28.2 rule #15 orchestrator check.

    ``passed`` is True when both ``niche_problem`` and ``niche_person``
    settings are non-empty. ``rationale`` is the canonical refusal
    message the dispatcher echoes back to the conversation.
    """

    passed: bool
    rationale: str


def niche_gate(conn: sqlite3.Connection) -> NicheGateResult:
    """Run the §28.2 rule #15 check.

    Per spec §28.16 + spec §25 Phase 5.9: this gate is the orchestrator-
    owned enforcement of rule #15. The check consults only the settings
    rows — it does NOT read ``assistant_text``. A prompt-injected request
    to "skip the niche check" cannot bypass it.

    Returns a ``NicheGateResult``. The dispatcher
    (``app.agent.client.dispatch_tool_call``) calls this BEFORE
    ``decide_save_or_revise`` for any ``save_draft_*`` tool name; on
    ``passed=False`` it refuses without touching the handler.
    """
    nd = niche.get_niche(conn)
    if nd.is_defined():
        return NicheGateResult(passed=True, rationale="niche defined")
    return NicheGateResult(passed=False, rationale=niche.CANONICAL_REFUSAL)


# Re-export ANALYTICAL_PATTERNS so callers don't have to import from two
# modules. Pattern definitions still live in confidence_patterns.py.
__all__ = [
    "ANALYTICAL_PATTERNS",
    "CONFIDENCE_LABELS",
    "Decision",
    "IwhScore",
    "NicheGateResult",
    "SessionState",
    "decide_save_or_revise",
    "detect_untagged_claims",
    "dominant_confidence_label",
    "extract_confidence_labels",
    "get_iwh_max_revision_attempts",
    "get_iwh_self_score_minimum",
    "humility_penalty_for_untagged",
    "niche_gate",
    "parse_iwh_self_score",
]


# ---------------------------------------------------------------------------
# SessionState — per-chat-session orchestrator state. Lives in memory; the
# durable mirror is agent_drafts.iwh_attempt_index per row.
# ---------------------------------------------------------------------------
@dataclass
class SessionState:
    """In-memory mirror of per-draft iwh attempt counters."""

    conversation_id: int | None = None
    session_id: str | None = None
    iwh_per_draft: dict[int, int] = field(default_factory=dict)

    def attempts_for(self, draft_id: int) -> int:
        return self.iwh_per_draft.get(draft_id, 1)

    def bump(self, draft_id: int) -> int:
        new_val = self.iwh_per_draft.get(draft_id, 1) + 1
        self.iwh_per_draft[draft_id] = new_val
        return new_val


# ---------------------------------------------------------------------------
# decide_save_or_revise — the orchestrator's policy gate.
#
# Inputs: the agent's full assistant message, the draft text it intends
# to save, and the current attempt index (1 for first attempt, N+1 for
# revisions).
#
# Output: a structured Decision the caller can act on.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    action: Literal["save", "revise", "refuse"]
    iwh_score: IwhScore | None
    lint_result: lint.LintResult | None
    rationale: str
    next_attempt_index: int
    # Phase 5.8 / §28.14 — populated on every decision so the orchestrator
    # can persist agent_drafts.confidence_label (when the message produced
    # a draft) or agent_messages.confidence_label (otherwise).
    confidence_label: str | None = None
    untagged_analytical_claims: int = 0
    # Phase 5.9 / §28.18 — reply-quality lint result. Only populated when
    # draft_kind='reply' was passed to decide_save_or_revise. The handler
    # writes the boolean into agent_drafts.reply_quality_lint_passed.
    reply_quality_result: lint.ReplyQualityResult | None = None


def _read_reply_quality_lint_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?",
        ("reply_quality_lint_enabled",),
    ).fetchone()
    if row is None:
        return True
    return lint.is_reply_quality_lint_enabled(row["value_json"])


def decide_save_or_revise(
    conn: sqlite3.Connection,
    *,
    assistant_text: str,
    draft_text: str,
    current_attempt_index: int,
    draft_kind: str = "standalone",
    target_post_text: str | None = None,
) -> Decision:
    """Run IWH + lint preflight; return whether to save / revise / refuse.

    The orchestrator never trusts the agent's self-reported attempt count.
    The current_attempt_index here is computed from agent_drafts.iwh_attempt_index
    on the durable side (see app/agent/tools.py::_revise_draft).

    Phase 5.9 / §28.18 — when ``draft_kind='reply'`` we also run the
    reply-quality lint AFTER the dark-pattern lint and treat its failure
    the same way: failed IWH revision, attempt counter bumped, refuse
    on the (N+1)th attempt.
    """
    minimum = get_iwh_self_score_minimum(conn)
    max_attempts = get_iwh_max_revision_attempts(conn)

    # Phase 5.8 / §28.14 — confidence-label extraction up front so every
    # Decision branch carries the label + untagged count.
    labels = extract_confidence_labels(assistant_text)
    dominant = dominant_confidence_label(labels)
    untagged = detect_untagged_claims(assistant_text)

    if current_attempt_index > max_attempts:
        return Decision(
            action="refuse",
            iwh_score=None,
            lint_result=None,
            rationale=(
                f"draft has hit attempt #{current_attempt_index} which exceeds "
                f"iwh_max_revision_attempts={max_attempts}. The orchestrator "
                f"refuses to call save_draft. Start a fresh draft if needed."
            ),
            next_attempt_index=current_attempt_index,
            confidence_label=dominant,
            untagged_analytical_claims=untagged,
        )

    iwh_score = parse_iwh_self_score(assistant_text)
    lint_result = lint.lint_draft(draft_text)

    # Phase 5.9 / §28.18 — reply-quality lint AFTER dark-pattern lint.
    # Only runs for replies. The result is exposed on the Decision so
    # the handler can persist agent_drafts.reply_quality_lint_passed.
    reply_quality_result: lint.ReplyQualityResult | None = None
    if draft_kind == "reply":
        rq_enabled = _read_reply_quality_lint_enabled(conn)
        reply_quality_result = lint.reply_quality_lint(
            draft_text, target_post_text, enabled=rq_enabled
        )

    # If no IWH tag was emitted, treat as failed IWH check.
    iwh_failed = iwh_score is None or iwh_score.min_score() < minimum
    lint_failed = lint_result.dark_pattern_detected
    reply_quality_failed = bool(
        reply_quality_result is not None and not reply_quality_result.passed
    )

    # Phase 5.8 / §28.14 — untagged analytical claims drop humility by one
    # per claim. We apply the penalty by lowering iwh_score.humility for
    # the gating decision. The raw score the agent emitted is preserved on
    # the Decision so callers can still see what was claimed vs. earned.
    effective_iwh = iwh_score
    if iwh_score is not None and untagged > 0:
        penalty = humility_penalty_for_untagged(untagged)
        effective_iwh = IwhScore(
            intelligence=iwh_score.intelligence,
            wisdom=iwh_score.wisdom,
            humility=max(0, iwh_score.humility - penalty),
        )
        iwh_failed = iwh_failed or effective_iwh.min_score() < minimum

    if iwh_failed or lint_failed or reply_quality_failed:
        reasons = []
        # Three+ independent gates can each contribute a reason.
        # Independent `if`s instead of `elif` so an IWH miss AND an
        # untagged-claim penalty BOTH surface — audit reviewers lost the
        # humility signal when only the first elif fired.
        if iwh_score is None:
            reasons.append("no <iwh_self_score> tag emitted")
        elif iwh_score.min_score() < minimum:
            reasons.append(
                f"IWH score below minimum ({iwh_score.to_dict()} vs min={minimum})"
            )
        if (
            untagged > 0
            and effective_iwh is not None
            and effective_iwh.min_score() < minimum
        ):
            reasons.append(
                f"{untagged} untagged analytical claim(s) dropped humility "
                f"below minimum"
            )
        if lint_failed:
            reasons.append(f"dark-pattern lint: {lint_result.rationale}")
        if reply_quality_failed and reply_quality_result is not None:
            reasons.append(
                f"reply-quality lint ({reply_quality_result.failure_mode}): "
                f"{reply_quality_result.rationale}"
            )
        next_idx = current_attempt_index + 1
        action: Literal["save", "revise", "refuse"]
        if next_idx > max_attempts:
            action = "refuse"
        else:
            action = "revise"
        return Decision(
            action=action,
            iwh_score=iwh_score,
            lint_result=lint_result,
            rationale="; ".join(reasons),
            next_attempt_index=next_idx,
            confidence_label=dominant,
            untagged_analytical_claims=untagged,
            reply_quality_result=reply_quality_result,
        )

    return Decision(
        action="save",
        iwh_score=iwh_score,
        lint_result=lint_result,
        rationale="IWH + lint preflight passed",
        next_attempt_index=current_attempt_index,
        confidence_label=dominant,
        untagged_analytical_claims=untagged,
        reply_quality_result=reply_quality_result,
    )
