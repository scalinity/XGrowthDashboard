"""Coach mode citation discipline (§28.23, Phase 5.10).

The Coach is a SECOND conversational surface, structurally identical to
§14.8 Agent Chat but with a hard discipline layered on every assistant
message: each inline citation token of the form ``〔record_type
id_or_filter〕`` is extracted, validated against a CLOSED allowlist of
record types, and either preserved (resolves to a real DB row) or
stripped (with reason logged). When ``coach_refuse_without_evidence ==
true`` AND a message contains analytical claims but no surviving
citations, the orchestrator REPLACES the assistant text with a
canonical refusal before persisting.

This module is the citation post-filter. It owns the regex, the
allowlist, the per-record-type resolvers, and the enforcement
orchestration. It DOES NOT own:

* The system prompt — that lives in ``config/agent_system_prompt.md``
  Section 9 (added separately).
* The tool registry — ``coach_tool_registry()`` returns a filtered
  copy of ``AGENT_TOOLS`` excluding every write tool. The startup
  invariant in ``app/main.py`` enforces this on every boot.
* The view — §14.10 ``app/pages/12_Coach.py`` calls ``enforce()`` on
  every assistant turn before persisting to ``agent_messages``.

Citation format (load-bearing, mirrored in §28.23 spec table):

  ``〔post 142〕``                              — posts.id = 142
  ``〔experiment 4〕``                          — experiments.id = 4
  ``〔weekly_review 2026-W19〕``                — weekly_reviews row by week
  ``〔agent_draft 88〕``                        — agent_drafts.id = 88
  ``〔v_lane_performance row build/icp/value〕`` — view row by filter
  ``〔monthly_review 2026-05〕``                — (Phase 5.11 — defer-strip)

Any record_type not in the allowlist is stripped with reason
``unsupported_record_type``. Any allowlisted citation that fails its
resolver is stripped with reason ``not_found`` (or a more specific
failure for view rows).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from app.agent import confidence_patterns as _patterns

# ---------------------------------------------------------------------------
# Citation extraction — Unicode lenticular brackets 〔U+3014〕 and 〕U+3015〕.
# ---------------------------------------------------------------------------
_CITATION_RE: re.Pattern[str] = re.compile(r"〔([^〕]+)〕")


# ---------------------------------------------------------------------------
# Data types.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Citation:
    """One inline ``〔...〕`` citation extracted from an assistant message.

    ``raw`` is the verbatim original token (including the brackets) so
    ``enforce`` can substring-remove stripped citations from the
    rewritten text. ``record_type`` is the first whitespace-delimited
    token inside the brackets; ``record_id`` is everything after it. For
    view-row citations, ``filter_text`` is the slash-separated filter
    after the literal ``"row"`` token (e.g. ``"build/icp/value"``);
    ``record_id`` carries the same raw rest-of-bracket so the
    persistence format stays consistent.
    """

    raw: str
    record_type: str
    record_id: str
    filter_text: str | None = None
    excerpt: str = ""

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {
            "record_type": self.record_type,
            "record_id": self.record_id,
        }
        if self.filter_text is not None:
            d["filter"] = self.filter_text
        if self.excerpt:
            d["excerpt"] = self.excerpt
        return d


@dataclass(frozen=True)
class StrippedCitation:
    """A citation that failed validation. Carries the strip reason.

    ``reason`` is one of the documented sentinel strings:
      - ``unsupported_record_type`` — record_type not in the allowlist
      - ``not_found`` — id-based citation didn't resolve
      - ``view_not_found`` — view row citation referenced a missing view
      - ``view_filter_mismatch`` — view exists but no row matches the filter
      - ``malformed`` — bracket contents couldn't be parsed
    """

    raw: str
    record_type: str
    record_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "record_type": self.record_type,
            "record_id": self.record_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EnforceResult:
    """Output of one ``enforce`` pass.

    The §14.10 view persists ``clean_text`` to ``agent_messages.content``
    and ``[c.to_dict() for c in surviving]`` to
    ``agent_messages.evidence_citations_json``. The orchestrator logs
    ``len(stripped)`` and a compact reason summary to ``agent_tool_calls.
    notes`` of the message's parent tool call.

    ``refused`` is True when the canonical refusal was substituted in
    place of the original text (per ``coach_refuse_without_evidence`` =
    true + no surviving citations + analytical claim detected).
    """

    clean_text: str
    surviving: list[Citation]
    stripped: list[StrippedCitation]
    refused: bool = False
    refusal_reason: str | None = None
    original_text: str = field(repr=False, default="")


# ---------------------------------------------------------------------------
# Citation parsing.
# ---------------------------------------------------------------------------
def extract_citations(text: str) -> list[Citation]:
    """Pull every ``〔...〕`` citation from ``text`` in source order.

    Malformed brackets (empty contents, missing record_type) are
    skipped here and surface as stripped citations later — the strip
    log is the audit trail for what the agent tried to emit. We use
    ``[^〕]`` so nesting is impossible by construction.
    """
    citations: list[Citation] = []
    for m in _CITATION_RE.finditer(text):
        raw = m.group(0)
        inner = m.group(1).strip()
        if not inner:
            continue
        # First whitespace splits record_type from the rest. Then check
        # for the view-row shape ``<view> row <filter>``.
        parts = inner.split(None, 1)
        if len(parts) == 1:
            citations.append(
                Citation(raw=raw, record_type=parts[0], record_id="")
            )
            continue
        rt, rest = parts[0], parts[1].strip()
        # View-row detection: record_type starts with "v_" AND second
        # token is literal "row". Anything else falls through to the
        # generic id-based shape.
        if rt.startswith("v_"):
            sub = rest.split(None, 1)
            if sub and sub[0] == "row" and len(sub) == 2:
                citations.append(
                    Citation(
                        raw=raw,
                        record_type=rt,
                        record_id=rest,
                        filter_text=sub[1].strip(),
                    )
                )
                continue
        citations.append(Citation(raw=raw, record_type=rt, record_id=rest))
    return citations


# ---------------------------------------------------------------------------
# Per-record-type resolvers.
# ---------------------------------------------------------------------------
Resolver = Callable[[sqlite3.Connection, Citation], tuple[bool, str]]
"""Signature: (conn, citation) -> (resolves, reason).

``reason`` is "" on success; on failure it's one of the documented
StrippedCitation reason strings.
"""


# P510R-14: id-based resolvers were three line-for-line duplicates
# differing only by table name. Factor a single helper that returns
# a Resolver bound to the table — adding a new id-based record_type
# in Phase 5.11+ is now one entry in _ID_RESOLVERS instead of nine
# more lines of duplicated parse-and-query code. ``table_name`` is a
# static identifier passed at registration time (never user input),
# so the dynamic SQL is safe.
def _resolve_by_int_id(table_name: str) -> Resolver:
    def resolver(conn: sqlite3.Connection, c: Citation) -> tuple[bool, str]:
        try:
            row_id = int(c.record_id.strip())
        except (ValueError, TypeError):
            return False, "malformed"
        row = conn.execute(
            f"SELECT 1 FROM {table_name} WHERE id = ?", (row_id,)
        ).fetchone()
        return (bool(row), "" if row else "not_found")
    return resolver


def _resolve_weekly_review(
    conn: sqlite3.Connection, c: Citation
) -> tuple[bool, str]:
    """Resolve weekly_reviews by week_start_date OR ISO-week token.

    Spec example is ``〔weekly_review 2026-W19〕`` (ISO week notation).
    The DB column is ``week_start_date`` (YYYY-MM-DD). We accept both
    forms — the ISO-week string is converted to its Monday date.

    Spec divergence: §28.23 says ``weekly_reviews.iso_week = '2026-W19'``
    but the table has no ``iso_week`` column. We resolve by either
    interpretation rather than failing every legitimate citation; the
    spec should be amended in a future revision to acknowledge the
    actual column name.
    """
    token = c.record_id.strip()
    if not token:
        return False, "malformed"

    candidate_dates: list[str] = []
    if "W" in token.upper():
        try:
            from datetime import date

            year_str, week_str = token.upper().split("W", 1)
            year = int(year_str.rstrip("-"))
            week = int(week_str.split("-", 1)[0])
            monday = date.fromisocalendar(year, week, 1)
            candidate_dates.append(monday.isoformat())
        except (ValueError, IndexError):
            return False, "malformed"
    else:
        candidate_dates.append(token)

    placeholders = ",".join("?" * len(candidate_dates))
    row = conn.execute(
        f"SELECT 1 FROM weekly_reviews WHERE week_start_date IN ({placeholders})",
        candidate_dates,
    ).fetchone()
    return (bool(row), "" if row else "not_found")


def _resolve_monthly_review(
    conn: sqlite3.Connection, c: Citation  # noqa: ARG001 — Phase 5.11 hook
) -> tuple[bool, str]:
    """Monthly reviews are a Phase 5.11 feature — the table doesn't exist yet.

    Per the §28.23 spec table, ``monthly_review`` is in the allowlist
    but resolution is deferred to Phase 5.11. Until the table lands,
    every monthly_review citation strips with reason ``not_found`` —
    the record_type itself is recognized (no ``unsupported_record_
    type`` reason) so the agent's intent is preserved in the audit
    trail.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='monthly_reviews'"
    ).fetchone()
    if not row:
        return False, "not_found"
    # If the table arrives in Phase 5.11, this resolver gets a real
    # implementation. Stub the success path now so the conditional
    # only triggers once the table exists.
    return False, "not_found"


# Per-view filter parsers. The agent emits ``v_<name> row
# <slash-separated-tokens>``; each entry below maps a view to the SQL
# WHERE template. The tokens are positional placeholders matching the
# template's ``?`` count. Views not listed here strip with
# ``view_filter_mismatch``.
#
# P510R-21: previous shape stored (where_template, [columns]) tuples
# but the columns list was never consumed (the resolver unpacked it
# into ``_cols`` and discarded). Dropped the list — the where_template
# already encodes the expected token count via its ``?`` placeholders.
_VIEW_FILTER_TEMPLATES: dict[str, str] = {
    "v_lane_performance": "pillar = ? AND audience = ? AND cta = ?",
    "v_content_type_performance": "content_type = ?",
    "v_funnel_daily": "event_date = ?",
    "v_account_daily": "snapshot_date = ?",
    "v_post_latest_metrics": "post_id = ?",
    "v_daily_reps": "activity_date = ?",
    "v_follower_velocity": "snapshot_date = ?",
}


def _resolve_view_row(conn: sqlite3.Connection, c: Citation) -> tuple[bool, str]:
    """Validate a view-row citation against the closed view-filter map.

    Two layers of check: (1) the view name must be in our known set;
    (2) running ``SELECT 1 FROM <view> WHERE <filter_template>`` with
    the parsed tokens must return at least one row.
    """
    view_name = c.record_type
    filter_text = c.filter_text
    if filter_text is None:
        return False, "view_filter_mismatch"
    if view_name not in _VIEW_FILTER_TEMPLATES:
        return False, "view_not_found"

    # Confirm the view actually exists in the DB (defensive — covers
    # migration mismatches between dev/prod).
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
        (view_name,),
    ).fetchone()
    if not exists:
        return False, "view_not_found"

    where_template = _VIEW_FILTER_TEMPLATES[view_name]
    tokens = [t.strip() for t in filter_text.split("/")]
    expected = where_template.count("?")
    if len(tokens) != expected:
        return False, "view_filter_mismatch"

    try:
        row = conn.execute(
            f"SELECT 1 FROM {view_name} WHERE {where_template} LIMIT 1",
            tokens,
        ).fetchone()
    except sqlite3.DatabaseError:
        return False, "view_filter_mismatch"
    return (bool(row), "" if row else "view_filter_mismatch")


# Closed allowlist. Order matters for ``validate_against_allowlist``'s
# fast-path dispatch — view names are matched by prefix, the other six
# by exact key. Adding a record_type? Add the spec table entry FIRST
# (§28.23), then add to this dispatch.
_ID_RESOLVERS: dict[str, Resolver] = {
    "post": _resolve_by_int_id("posts"),
    "experiment": _resolve_by_int_id("experiments"),
    "agent_draft": _resolve_by_int_id("agent_drafts"),
    "weekly_review": _resolve_weekly_review,
    "monthly_review": _resolve_monthly_review,
}


SUPPORTED_RECORD_TYPES: tuple[str, ...] = tuple(_ID_RESOLVERS.keys()) + tuple(
    _VIEW_FILTER_TEMPLATES.keys()
)


def validate_against_allowlist(
    conn: sqlite3.Connection, citations: list[Citation]
) -> tuple[list[Citation], list[StrippedCitation]]:
    """Partition a list of citations into surviving + stripped.

    Per §28.23, citations with an unrecognized record_type are stripped
    with reason ``unsupported_record_type``. Citations with a
    recognized record_type but a non-resolvable id/filter are stripped
    with a more specific reason (``not_found`` / ``view_not_found`` /
    ``view_filter_mismatch`` / ``malformed``).
    """
    surviving: list[Citation] = []
    stripped: list[StrippedCitation] = []
    for c in citations:
        if not c.record_type or not c.record_id:
            stripped.append(
                StrippedCitation(
                    raw=c.raw,
                    record_type=c.record_type,
                    record_id=c.record_id,
                    reason="malformed",
                )
            )
            continue
        if c.record_type.startswith("v_"):
            ok, reason = _resolve_view_row(conn, c)
        else:
            resolver = _ID_RESOLVERS.get(c.record_type)
            if resolver is None:
                stripped.append(
                    StrippedCitation(
                        raw=c.raw,
                        record_type=c.record_type,
                        record_id=c.record_id,
                        reason="unsupported_record_type",
                    )
                )
                continue
            ok, reason = resolver(conn, c)
        if ok:
            surviving.append(c)
        else:
            stripped.append(
                StrippedCitation(
                    raw=c.raw,
                    record_type=c.record_type,
                    record_id=c.record_id,
                    reason=reason or "not_found",
                )
            )
    return surviving, stripped


# ---------------------------------------------------------------------------
# Text rewriting + refusal substitution.
# ---------------------------------------------------------------------------
def _strip_citation_tokens(text: str, stripped: list[StrippedCitation]) -> str:
    """Remove the verbatim ``〔...〕`` tokens of stripped citations.

    Multiple identical tokens collapse — re.sub of the literal raw is
    fine because the brackets bound the match. We also collapse any
    pair of spaces left behind into one so the rewritten text reads
    cleanly.
    """
    rewritten = text
    for s in stripped:
        rewritten = rewritten.replace(s.raw, "")
    rewritten = re.sub(r"[ \t]{2,}", " ", rewritten)
    rewritten = re.sub(r" +([.,;:!?])", r"\1", rewritten)
    return rewritten.strip()


_REFUSAL_PREFIX: str = (
    "I don't have data in your dashboard to answer this honestly."
)


def _format_refusal(gap_description: str) -> str:
    """Compose the canonical refusal carrying a one-line gap_description.

    Per §28.23: ``"I don't have data in your dashboard to answer this
    honestly. {gap_description}"``. The gap_description is generated by
    the orchestrator based on what the agent tried to claim (passed in
    by enforce). Defaults to a generic line when no specific hint is
    available.
    """
    desc = gap_description.strip() if gap_description else (
        "The Coach refuses to speculate without a cited row in your "
        "dashboard."
    )
    return f"{_REFUSAL_PREFIX} {desc}"


def _generate_gap_description(
    stripped: list[StrippedCitation], original_text: str
) -> str:
    """Build a short hint about what the agent tried to claim but couldn't.

    The hint enumerates up to three unique record_types the agent
    tried to cite. If nothing was tried (zero citations), falls back
    to a generic message that prompts Daniel to ask the same question
    in §14.8 Agent Chat where speculation is allowed.
    """
    if not stripped:
        # Pure speculation — no citations emitted at all.
        return (
            "The Coach refuses to speculate without a cited row in your "
            "dashboard. If you want exploratory thinking, switch to "
            "Agent Chat (§14.8)."
        )
    record_types = []
    seen: set[str] = set()
    for s in stripped:
        if s.record_type and s.record_type not in seen:
            record_types.append(s.record_type)
            seen.add(s.record_type)
        if len(record_types) >= 3:
            break
    tried = ", ".join(record_types)
    return (
        f"The agent tried to cite {tried}, but none of those resolved "
        f"to a real row. Either the data isn't in the dashboard yet or "
        f"the citation was malformed."
    )


# ---------------------------------------------------------------------------
# enforce — the orchestration entry point.
# ---------------------------------------------------------------------------
def enforce(
    text: str,
    conn: sqlite3.Connection,
    *,
    refuse_without_evidence: bool = True,
) -> EnforceResult:
    """Run the full §28.23 enforcement pipeline on one assistant message.

    Pipeline:

    1. Extract every ``〔...〕`` citation in source order.
    2. Validate each against the closed allowlist + per-record-type
       resolver. Stripped citations carry a specific reason.
    3. Rewrite the message to remove the stripped tokens; collapse
       leftover double-spaces and orphan punctuation.
    4. If ``refuse_without_evidence`` AND the rewritten text contains
       at least one analytical claim AND no citations survived,
       REPLACE the text with the canonical refusal carrying a
       gap_description hint.
    5. Return the EnforceResult — caller persists ``clean_text``,
       ``surviving`` (to ``evidence_citations_json``), and logs
       ``stripped`` counts/reasons to ``agent_tool_calls.notes`` of
       the parent tool call.
    """
    citations = extract_citations(text)
    surviving, stripped = validate_against_allowlist(conn, citations)
    clean_text = _strip_citation_tokens(text, stripped)

    # P510R-17: evaluate analytical-claim presence on the ORIGINAL
    # text, not the post-strip cleaned text. The §28.23 contract is
    # "uncited analytical claims → refusal" — if stripping took both
    # an analytical phrase and its adjacent citation, the cleaned
    # text could look innocuous and let the refusal gate silently
    # not fire even though the agent's original utterance was the
    # very thing the gate is meant to catch.
    refused = False
    refusal_reason: str | None = None
    if (
        refuse_without_evidence
        and not surviving
        and _patterns.has_analytical_claim(text)
    ):
        gap = _generate_gap_description(stripped, text)
        clean_text = _format_refusal(gap)
        refused = True
        refusal_reason = gap

    return EnforceResult(
        clean_text=clean_text,
        surviving=surviving,
        stripped=stripped,
        refused=refused,
        refusal_reason=refusal_reason,
        original_text=text,
    )


# ---------------------------------------------------------------------------
# Tool registry filtering — Coach mode excludes every write tool.
# ---------------------------------------------------------------------------
# Curated list of tool names that mutate ANY persistent state. The
# Coach is advice-only (§28.23 anti-feature) — these must NOT appear
# in its tool catalog. Adding a write tool to AGENT_TOOLS? Add its
# name here in the same commit.
COACH_FORBIDDEN_TOOLS: frozenset[str] = frozenset(
    {
        # Drafting writes.
        "save_draft_post",
        "save_draft_reply",
        "revise_draft",
        # Reply-target writes.
        "record_reply_target",
        "score_replier_pool",  # writes reply_targets rows
        # Brain-dump processing mutates brain_dumps.status + results.
        "process_brain_dump",
        # Reserved for Phase 5.10 follow-ups:
        "analyze_account",
        "audit_profile",
    }
)


def coach_tool_registry(all_tools):  # type: ignore[no-untyped-def]
    """Return a filtered copy of an AGENT_TOOLS-shaped iterable.

    Used by the §14.10 Coach view to assemble the model's tool catalog
    for that session. The view never instantiates a Coach client with
    the unfiltered registry; the assertion in ``app/main.py`` verifies
    this stays true.
    """
    return [t for t in all_tools if t.name not in COACH_FORBIDDEN_TOOLS]


def assert_coach_excludes_write_tools(all_tools) -> None:  # type: ignore[no-untyped-def]
    """Startup invariant: confirm the Coach registry excludes write tools.

    Called from ``app/main.py``. The check is shape-preserving: we
    take a Coach-filtered copy and verify it contains none of the
    forbidden names. If a future refactor accidentally exposes a
    write tool to Coach mode, the app refuses to boot.
    """
    filtered = coach_tool_registry(all_tools)
    leaked = {t.name for t in filtered} & COACH_FORBIDDEN_TOOLS
    assert not leaked, (
        "INVARIANT VIOLATION (§28.23): Coach tool registry leaks "
        f"write tools: {sorted(leaked)}. Coach is advice-only; "
        "promote intent goes to Agent Chat or Brain Dump."
    )


__all__ = [
    "COACH_FORBIDDEN_TOOLS",
    "Citation",
    "EnforceResult",
    "StrippedCitation",
    "SUPPORTED_RECORD_TYPES",
    "assert_coach_excludes_write_tools",
    "coach_tool_registry",
    "enforce",
    "extract_citations",
    "validate_against_allowlist",
]
