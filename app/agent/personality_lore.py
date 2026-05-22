"""Personality lore registry (§28.21, Phase 5.9).

A small Daniel-curated table of recurring jokes, running bits, and
personal motifs. Spliced into the system prompt's voice section so the
agent draws on existing narrative threads when drafting
``content_type='personality'`` posts instead of inventing fresh bits
each turn.

Access control: the agent has NO write access. No ``AGENT_TOOLS`` entry
references ``personality_lore``. A startup-time assertion in
``app/main.py`` (``_assert_personality_lore_unreachable``) verifies the
exclusion the same way ``_assert_publish_tools_unreachable`` guards the
publish tools.

Invocation tracking: when an agent draft is saved with
``content_type='personality'``, the orchestrator calls
:func:`scan_and_increment_invocations` with the draft text. The scan is
case-insensitive and fuzzy — substring match on theme name OR
keyword-token match on the description. Over-counting is acceptable per
§28.21; under-counting is not.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.agent import settings_io


# A reasonable stopword list keeps description-tokenization signal high.
# Kept small on purpose — the source pool is Daniel-authored short
# descriptions, not arbitrary corpora.
_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "being", "as", "it", "its", "this", "that", "these", "those", "my",
    "your", "our", "their", "his", "her", "do", "does", "did", "i", "we",
    "you", "they", "he", "she",
    # Common high-frequency words that would otherwise saturate matches.
    "about", "into", "over", "than", "then", "when", "where", "which",
    "would", "could", "should", "have", "has", "had", "not", "what",
    "some", "any", "all", "more", "most", "many", "much", "very", "just",
    "also", "even", "only", "still", "yet", "out", "up", "down", "off",
    "now", "today", "yesterday", "tomorrow", "here", "there",
})
# Match alpha tokens of length >= 3 — short tokens are too generic for
# fuzzy matching ('a', 'is', 'on' would saturate counts).
_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{2,}")


@dataclass(frozen=True)
class LoreRow:
    id: int
    theme: str
    description: str
    example_posts_json: str | None
    invocation_count: int
    last_invoked_at_utc: str | None
    is_active: bool
    priority: int
    added_at_utc: str

    def example_post_ids(self) -> list[int]:
        if not self.example_posts_json:
            return []
        try:
            payload = json.loads(self.example_posts_json)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(payload, list):
            return []
        return [int(x) for x in payload if isinstance(x, (int, str)) and str(x).isdigit()]


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------
def _row_to_lore(row: sqlite3.Row) -> LoreRow:
    return LoreRow(
        id=int(row["id"]),
        theme=str(row["theme"]),
        description=str(row["description"]),
        example_posts_json=row["example_posts_json"],
        invocation_count=int(row["invocation_count"] or 0),
        last_invoked_at_utc=row["last_invoked_at_utc"],
        is_active=bool(row["is_active"]),
        priority=int(row["priority"] or 100),
        added_at_utc=str(row["added_at_utc"]),
    )


# P59A-S12: single source of truth for the SELECT projection; both
# list helpers and any future read query reference it.
_LORE_COLUMNS = (
    "id, theme, description, example_posts_json, invocation_count, "
    "last_invoked_at_utc, is_active, priority, added_at_utc"
)


def list_all(conn: sqlite3.Connection) -> list[LoreRow]:
    """All lore rows ordered by (is_active DESC, priority ASC, id ASC)."""
    rows = conn.execute(
        f"""
        SELECT {_LORE_COLUMNS}
        FROM personality_lore
        ORDER BY is_active DESC, priority ASC, id ASC
        """
    ).fetchall()
    return [_row_to_lore(r) for r in rows]


def list_active(
    conn: sqlite3.Connection, *, limit: int | None = None
) -> list[LoreRow]:
    """Active rows ordered by priority ASC, id ASC."""
    sql = (
        f"SELECT {_LORE_COLUMNS} FROM personality_lore "
        "WHERE is_active = 1 ORDER BY priority ASC, id ASC"
    )
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_lore(r) for r in rows]


def get_splice_count(conn: sqlite3.Connection) -> int:
    """Read ``personality_lore_splice_count`` (default 5)."""
    return settings_io.get_int(conn, "personality_lore_splice_count", 5)


def get_overuse_threshold(conn: sqlite3.Connection) -> int:
    """Read ``personality_lore_overuse_threshold`` (default 8)."""
    return settings_io.get_int(conn, "personality_lore_overuse_threshold", 8)


# ---------------------------------------------------------------------------
# Writes (Daniel-only — agent has no write access).
# ---------------------------------------------------------------------------
def add(
    conn: sqlite3.Connection,
    *,
    theme: str,
    description: str,
    example_posts_json: str | None = None,
    priority: int = 100,
    is_active: bool = True,
) -> int:
    theme_clean = theme.strip()
    desc_clean = description.strip()
    if not theme_clean or not desc_clean:
        raise ValueError("theme and description are both required")
    cur = conn.execute(
        """
        INSERT INTO personality_lore
            (theme, description, example_posts_json, priority, is_active)
        VALUES (?, ?, ?, ?, ?)
        """,
        (theme_clean, desc_clean, example_posts_json, int(priority),
         1 if is_active else 0),
    )
    return int(cur.lastrowid)


def set_active(conn: sqlite3.Connection, *, lore_id: int, is_active: bool) -> None:
    conn.execute(
        "UPDATE personality_lore SET is_active = ? WHERE id = ?",
        (1 if is_active else 0, int(lore_id)),
    )


def set_priority(conn: sqlite3.Connection, *, lore_id: int, priority: int) -> None:
    conn.execute(
        "UPDATE personality_lore SET priority = ? WHERE id = ?",
        (int(priority), int(lore_id)),
    )


def edit(
    conn: sqlite3.Connection,
    *,
    lore_id: int,
    theme: str | None = None,
    description: str | None = None,
) -> None:
    if theme is None and description is None:
        return
    sets: list[str] = []
    vals: list = []
    if theme is not None:
        sets.append("theme = ?")
        vals.append(theme.strip())
    if description is not None:
        sets.append("description = ?")
        vals.append(description.strip())
    vals.append(int(lore_id))
    conn.execute(
        f"UPDATE personality_lore SET {', '.join(sets)} WHERE id = ?",
        vals,
    )


def delete(conn: sqlite3.Connection, *, lore_id: int) -> None:
    conn.execute("DELETE FROM personality_lore WHERE id = ?", (int(lore_id),))


# ---------------------------------------------------------------------------
# Invocation scan — orchestrator-owned.
# ---------------------------------------------------------------------------
def _tokenize_description(description: str) -> set[str]:
    """Lowercase alpha tokens >=3 chars, minus stopwords.

    The match is fuzzy by design — Daniel's descriptions are short
    paragraphs; tokenizing on whitespace + stripping function words gets
    the right signal without an NLP library.
    """
    cleaned = description.lower()
    tokens = {m.group(0) for m in _TOKEN_RE.finditer(cleaned)}
    return tokens - _STOPWORDS


def _draft_text_lower(draft_text: str) -> tuple[str, set[str]]:
    """Return (lowercased draft, set of alpha tokens) for matching."""
    low = draft_text.lower()
    tokens = {m.group(0) for m in _TOKEN_RE.finditer(low)}
    return low, tokens


DESCRIPTION_TOKEN_MATCH_MINIMUM: int = 2
"""P59A-W10: require >=2 description-token overlaps before treating a
description match as evidence of invocation. The prior single-token
rule over-counted in practice — a draft mentioning a common noun
('kitchen', 'build', 'scanner') would light up every lore row that
happened to use the same noun in its description. With the §28.21
over-reliance banner firing at invocation_count > 8 within 30 days,
the looser rule tripped banners spuriously. Theme substring match is
still single-hit because the theme is the canonical handle for the
bit; description tokens are softer signal."""


def detect_invoked_lore(
    active_lore: list[LoreRow], draft_text: str
) -> list[int]:
    """Return the ids of every active lore row referenced by the draft.

    Match rule (case-insensitive; P59A-W10 + P59A-S2):
      * Substring match on the theme name (whole theme as a phrase), OR
      * At least ``DESCRIPTION_TOKEN_MATCH_MINIMUM`` description keyword
        tokens (length >=3, non-stopword) present in the draft.

    Both checks are evaluated independently (S2 — union, no short-
    circuit) so a row matching BOTH still gets counted exactly once
    via natural dedup (each row.id appears at most once in `invoked`).
    Over-counting is acceptable per §28.21; under-counting is not.
    """
    if not active_lore or not draft_text:
        return []
    draft_low, draft_tokens = _draft_text_lower(draft_text)
    invoked: list[int] = []
    for row in active_lore:
        theme_low = row.theme.lower().strip()
        theme_hit = bool(theme_low and theme_low in draft_low)
        desc_tokens = _tokenize_description(row.description)
        desc_overlap = len(desc_tokens & draft_tokens)
        desc_hit = desc_overlap >= DESCRIPTION_TOKEN_MATCH_MINIMUM
        if theme_hit or desc_hit:
            invoked.append(row.id)
    return invoked


def scan_and_increment_invocations(
    conn: sqlite3.Connection, *, draft_text: str
) -> list[int]:
    """Scan ``draft_text`` against active lore + update counters.

    The orchestrator calls this AFTER a personality draft is saved (see
    ``app/agent/tools.py::_save_draft_post`` when content_type ==
    'personality'). Increments ``invocation_count`` and sets
    ``last_invoked_at_utc`` for each matched row in a single UPDATE.
    Returns the list of matched lore ids for audit / test inspection.
    """
    active = list_active(conn)
    invoked = detect_invoked_lore(active, draft_text)
    if not invoked:
        return []
    placeholders = ",".join("?" * len(invoked))
    conn.execute(
        f"""
        UPDATE personality_lore
        SET invocation_count = invocation_count + 1,
            last_invoked_at_utc = datetime('now')
        WHERE id IN ({placeholders})
        """,
        invoked,
    )
    return invoked


# ---------------------------------------------------------------------------
# Over-reliance flag — Settings panel "leaning hard on this bit" banner.
# ---------------------------------------------------------------------------
def is_over_relied_on(
    row: LoreRow, *, overuse_threshold: int, now_iso: str | None = None
) -> bool:
    """True when invocation_count > threshold AND last invoked < 30 days.

    The 30-day recency window is per §28.21 — old high-count lore
    doesn't trigger the banner; we only flag bits Daniel is leaning on
    right now.
    """
    if row.invocation_count <= overuse_threshold:
        return False
    if not row.last_invoked_at_utc:
        return False
    # Pure date math — sqlite would also work, but this stays test-friendly.
    try:
        last = datetime.fromisoformat(
            row.last_invoked_at_utc.replace("Z", "+00:00")
        )
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    now = (
        datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if now_iso is not None
        else datetime.now(timezone.utc)
    )
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    # P59A-S1: defensive lower bound. A future-dated last_invoked_at
    # (clock skew, test fixtures, manual edit) would otherwise pass the
    # `< 30` check trivially since (now - future).days is negative.
    delta_days = (now - last).days
    return 0 <= delta_days < 30


# ---------------------------------------------------------------------------
# Splice rendering — used by prompt_builder.
# ---------------------------------------------------------------------------
def render_splice_block(active_lore: list[LoreRow]) -> str:
    """Render the §28.21 splice — empty string when no active rows.

    Format (mirrors the §28.21 example):

        **Personal lore (running bits to draw on when
        content_type = personality):**
        - water bottle in frame: ...long-running self-deprecating joke...
          (last invoked 19 days ago)
        - kitchen-scanner fail story: ...
    """
    if not active_lore:
        return ""
    lines: list[str] = [
        "**Personal lore (running bits to draw on when "
        "content_type = personality):**",
    ]
    for row in active_lore:
        suffix = _last_invoked_suffix(row.last_invoked_at_utc)
        lines.append(
            f"- {row.theme}: {row.description}{suffix}"
        )
    return "\n".join(lines)


def _last_invoked_suffix(last_iso: str | None) -> str:
    if not last_iso:
        return " (not yet invoked)"
    try:
        last = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    now = datetime.now(timezone.utc)
    days = (now - last).days
    if days <= 0:
        return " (last invoked today)"
    if days == 1:
        return " (last invoked 1 day ago)"
    return f" (last invoked {days} days ago)"


__all__ = [
    "LoreRow",
    "add",
    "delete",
    "detect_invoked_lore",
    "edit",
    "get_overuse_threshold",
    "get_splice_count",
    "is_over_relied_on",
    "list_active",
    "list_all",
    "render_splice_block",
    "scan_and_increment_invocations",
    "set_active",
    "set_priority",
]
