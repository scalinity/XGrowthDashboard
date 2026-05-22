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

from app.agent import lint

DEFAULT_IWH_SELF_SCORE_MINIMUM: int = 2
DEFAULT_IWH_MAX_REVISION_ATTEMPTS: int = 3


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
    """Extract the first ``<iwh_self_score>`` JSON object from assistant text.

    Returns ``None`` if no tag is present or it can't be parsed — the
    caller treats this as a failed IWH check (defensive default).
    """
    match = _IWH_TAG_RE.search(assistant_text)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return IwhScore(
            intelligence=int(data["intelligence"]),
            wisdom=int(data["wisdom"]),
            humility=int(data["humility"]),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Settings helpers.
# ---------------------------------------------------------------------------
def _setting_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return int(json.loads(row["value_json"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return default


def get_iwh_self_score_minimum(conn: sqlite3.Connection) -> int:
    return _setting_int(conn, "iwh_self_score_minimum", DEFAULT_IWH_SELF_SCORE_MINIMUM)


def get_iwh_max_revision_attempts(conn: sqlite3.Connection) -> int:
    return _setting_int(
        conn, "iwh_max_revision_attempts", DEFAULT_IWH_MAX_REVISION_ATTEMPTS
    )


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


def decide_save_or_revise(
    conn: sqlite3.Connection,
    *,
    assistant_text: str,
    draft_text: str,
    current_attempt_index: int,
) -> Decision:
    """Run IWH + lint preflight; return whether to save / revise / refuse.

    The orchestrator never trusts the agent's self-reported attempt count.
    The current_attempt_index here is computed from agent_drafts.iwh_attempt_index
    on the durable side (see app/agent/tools.py::_revise_draft).
    """
    minimum = get_iwh_self_score_minimum(conn)
    max_attempts = get_iwh_max_revision_attempts(conn)

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
        )

    iwh_score = parse_iwh_self_score(assistant_text)
    lint_result = lint.lint_draft(draft_text)

    # If no IWH tag was emitted, treat as failed IWH check.
    iwh_failed = iwh_score is None or iwh_score.min_score() < minimum
    lint_failed = lint_result.dark_pattern_detected

    if iwh_failed or lint_failed:
        reasons = []
        if iwh_score is None:
            reasons.append("no <iwh_self_score> tag emitted")
        elif iwh_score.min_score() < minimum:
            reasons.append(
                f"IWH score below minimum ({iwh_score.to_dict()} vs min={minimum})"
            )
        if lint_failed:
            reasons.append(f"dark-pattern lint: {lint_result.rationale}")
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
        )

    return Decision(
        action="save",
        iwh_score=iwh_score,
        lint_result=lint_result,
        rationale="IWH + lint preflight passed",
        next_attempt_index=current_attempt_index,
    )
