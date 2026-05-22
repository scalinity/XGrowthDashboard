"""Replier-pool candidate discovery (§28.20, Phase 5.9).

The third reply-target discovery path: replier-under-thread. Daniel
mines the reply section of a big account's post and pastes the
replier handles + text excerpts. Each candidate is scored on the
existing §29.3 4-dim model PLUS a new dimension
``thread_context_fit_score`` (0-3, deterministic) measuring how well
the replier's text matches Daniel's ``niche_person`` definition from
§28.16.

V1.1+ deferred path: programmatic scan of top-N replies via X API.
Spec'd in §28.20 so the MVP paste flow isn't a dead end.

Candidates land in ``reply_targets`` with
``source='replier_under_thread'`` (column added by migration 012). The
deterministic resolver in §29.3 still owns the recommended_action
label — this module only computes the per-dimension scores and
delegates the persistence to the existing ``reply_targets`` machinery.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field

from app.db import transaction
from app.agent import niche as _niche
from app.agent.reply_targets import (
    ACTION_TO_SCORE,
    resolve_recommended_action,
)


# Same alpha-token regex shape personality_lore uses — len >=3 keeps
# function-words out of the match without an NLP library.
_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{2,}")

# A small stopword list. Kept minimal — Daniel's niche definitions are
# short noun phrases; aggressive filtering would erase signal.
_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "from", "by", "is", "are", "was", "were", "be",
    "as", "it", "its", "this", "that", "these", "those", "my",
    "your", "our", "their", "his", "her", "do", "does", "did",
    "i", "we", "you", "they", "he", "she",
    "about", "into", "over", "than", "then", "when", "where", "which",
    "would", "could", "should", "have", "has", "had", "not", "what",
    "some", "any", "all", "more", "most", "very", "just", "also",
})


# ---------------------------------------------------------------------------
# Parsing — the paste textarea is intentionally lenient.
# ---------------------------------------------------------------------------
@dataclass
class ReplierExcerpt:
    """One pasted replier item — a handle, an excerpt, or both."""

    handle: str | None = None
    text: str | None = None


def parse_replier_paste(payload: str) -> list[ReplierExcerpt]:
    """Parse Daniel's paste into per-replier records.

    Accepts:
      * One @handle per line.
      * One "@handle: excerpt text" per line.
      * Blank-line-separated multi-line excerpts where the FIRST line
        starts with @handle.

    Stays lenient on purpose — Daniel pastes from X mobile / web copy-
    paste, both of which mangle whitespace in mildly different ways.
    """
    out: list[ReplierExcerpt] = []
    if not payload or not payload.strip():
        return out

    # Split on blank lines (>=1 consecutive newlines preceding/following
    # whitespace) to give multi-line excerpts a chance.
    blocks = re.split(r"\n\s*\n", payload.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        # If every line in this block independently starts with @, treat
        # each line as its own record (handles both '@handle' and
        # '@handle: excerpt' shapes). This is the common case Daniel
        # produces by pasting from X's reply list without blank-line
        # separators.
        handle_re = re.compile(r"^@[A-Za-z0-9_]{1,15}(?:\s*[:\-—]|\s*$)")
        if all(handle_re.match(ln) for ln in lines):
            for ln in lines:
                m = re.match(r"^@([A-Za-z0-9_]{1,15})\s*[:\-—]?\s*(.*)$", ln)
                if m:
                    h = m.group(1)
                    t = m.group(2).strip() or None
                    out.append(ReplierExcerpt(handle=h, text=t))
            continue
        # Otherwise: this block is a multi-line excerpt; first line may
        # carry the handle, rest is body.
        first = lines[0]
        handle: str | None = None
        body_lines = list(lines)
        m = re.match(r"^@?([A-Za-z0-9_]{1,15})\s*[:\-—]\s*(.*)$", first)
        if m:
            handle = m.group(1)
            remainder = m.group(2).strip()
            body_lines = [remainder] if remainder else []
            body_lines += lines[1:]
        elif first.startswith("@"):
            handle = first.lstrip("@").strip()
            body_lines = lines[1:]
        text = "\n".join(b for b in body_lines if b).strip() or None
        out.append(ReplierExcerpt(handle=handle, text=text))
    return out


# ---------------------------------------------------------------------------
# Scoring.
# ---------------------------------------------------------------------------
def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {m.group(0) for m in _TOKEN_RE.finditer(text.lower())} - _STOPWORDS


def thread_context_fit_score(
    excerpt_text: str | None, niche_person: str
) -> int:
    """Deterministic 0..3 score for "how well does this replier match
    Daniel's niche person?"

    Match rule: count of non-stopword tokens in ``niche_person`` that
    appear in the excerpt. The ladder:

      * 0 — no tokens overlap OR excerpt is empty.
      * 1 — 1 token overlaps.
      * 2 — 2 tokens overlap.
      * 3 — 3+ tokens overlap.

    A short niche_person ("educational creators" → ['educational',
    'creators']) caps the achievable score at 2; that's fine — the
    ladder is "how strong is the signal," not "how many words did
    Daniel use."
    """
    if not excerpt_text:
        return 0
    niche_tokens = _tokens(niche_person)
    if not niche_tokens:
        return 0
    excerpt_tokens = _tokens(excerpt_text)
    overlap = len(niche_tokens & excerpt_tokens)
    if overlap >= 3:
        return 3
    return overlap


@dataclass
class ReplyTargetCandidate:
    """One scored candidate ready to land in reply_targets.

    Attribute names mirror the §29.3 dimensions + the new §28.20
    dimension so the orchestrator can map them onto the existing
    reply_targets columns without re-validation.
    """

    handle: str | None
    excerpt: str | None
    relevance_score: int
    engagement_surface_score: int
    saturation_score: int
    reply_opportunity_score: int
    thread_context_fit_score: int
    recommended_action_label: str
    score_rationale: str
    rationale_components: dict[str, str] = field(default_factory=dict)


def _candidate_relevance(thread_fit: int) -> int:
    """At MVP we project relevance from thread-context fit only.

    The original post's pillar match would be a separate signal, but
    the replier-pool case scores the replier — not the original post.
    A high thread-context fit is the best proxy for "this replier is
    in-niche enough to be worth a reply."
    """
    return thread_fit


def _default_engagement_surface() -> int:
    """No author-level metrics in paste flow — default to mid-tier (2).

    V1.1+ programmatic scan will replace this with the same §29.4
    threshold math the rest of §29 uses. At MVP we record the value
    and surface "estimated" in the rationale.
    """
    return 2


def _default_saturation() -> int:
    """Same rationale — no per-thread reply_count in paste flow."""
    return 2


def _default_reply_opportunity(thread_fit: int) -> int:
    """At MVP, derive from thread-context fit: a strong on-niche
    replier almost certainly opens a reply opportunity Daniel can
    work with."""
    if thread_fit >= 3:
        return 3
    if thread_fit >= 1:
        return 2
    return 1


def score_replier(
    excerpt: ReplierExcerpt, *, niche_person: str
) -> ReplyTargetCandidate:
    """Score one replier deterministically — no API calls.

    Caller-visible from tests + the orchestrator. The composite
    recommended_action_label is the §29.3 resolver output (the same
    ladder the rest of §29 uses).
    """
    thread_fit = thread_context_fit_score(excerpt.text, niche_person)
    rel = _candidate_relevance(thread_fit)
    eng = _default_engagement_surface()
    sat = _default_saturation()
    opp = _default_reply_opportunity(thread_fit)
    label = resolve_recommended_action(rel, eng, sat, opp)
    components = {
        "relevance": (
            f"derived from thread-context fit ({thread_fit}/3)"
        ),
        "engagement_surface": (
            "estimated mid-tier (2/3) — paste flow lacks author metrics; "
            "V1.1+ programmatic scan replaces this"
        ),
        "saturation": (
            "estimated mid-tier (2/3) — paste flow lacks thread reply_count"
        ),
        "reply_opportunity": (
            f"derived from thread-context fit ({thread_fit}/3)"
        ),
        "thread_context_fit": (
            "non-stopword token overlap between excerpt and "
            f"niche_person='{niche_person}'"
        ),
    }
    return ReplyTargetCandidate(
        handle=excerpt.handle,
        excerpt=excerpt.text,
        relevance_score=rel,
        engagement_surface_score=eng,
        saturation_score=sat,
        reply_opportunity_score=opp,
        thread_context_fit_score=thread_fit,
        recommended_action_label=label,
        score_rationale=(
            f"replier_under_thread paste-flow scoring "
            f"(thread_context_fit={thread_fit}). "
            "Engagement / saturation are placeholder estimates per "
            "§28.20 V1.1+ deferral."
        ),
        rationale_components=components,
    )


# ---------------------------------------------------------------------------
# Persistence — write candidates into reply_targets.
# ---------------------------------------------------------------------------
def score_replier_pool(
    conn: sqlite3.Connection,
    *,
    thread_url: str,
    replier_handles_or_excerpts: str,
    lookback_minutes: int = 60,  # noqa: ARG001 — V1.1+ uses this
) -> dict:
    """Score every pasted replier + land each as a reply_targets row.

    Returns a dict with the candidates list + counts. Each row gets
    ``source='replier_under_thread'``. Idempotent on
    (thread_url, handle): re-pasting the same combo refreshes the
    existing row's scores in place.
    """
    nd = _niche.get_niche(conn)
    if not nd.is_defined():
        return {
            "error": (
                "niche must be defined before scoring a replier pool — "
                "Settings → Growth Agent → Niche (§28.16)."
            ),
            "candidates": [],
            "created_count": 0,
            "updated_count": 0,
        }

    parsed = parse_replier_paste(replier_handles_or_excerpts)
    if not parsed:
        return {
            "error": "no replier handles or excerpts parsed from payload",
            "candidates": [],
            "created_count": 0,
            "updated_count": 0,
        }

    # P59A-W11: single transaction for the whole batch with per-excerpt
    # error capture. The prior shape opened BEGIN IMMEDIATE / COMMIT per
    # excerpt — 30 sequential write-lock acquisitions for a 30-replier
    # paste, and if excerpt 15 failed (CHECK violation, etc.) the prior
    # 14 had already committed with no per-row error surface. The new
    # shape mirrors _score_reply_candidates: collect errors in a list,
    # write rows that succeed inside the outer transaction, and return
    # the breakdown. CHECK violations now become entries in `errors`
    # rather than partial commits + silent under-counts.
    created = 0
    updated = 0
    out_candidates: list[dict] = []
    errors: list[str] = []
    with transaction(conn):
        for excerpt in parsed:
            try:
                scored = score_replier(excerpt, niche_person=nd.person)
                # Derive a per-row target_post_url so the unique index
                # doesn't collide across pasted batches for the same
                # thread. Anchor on the thread URL + #replier=<handle>
                # fragment when a handle is known; otherwise on a hashed
                # excerpt suffix.
                # P59A-C2: hashlib.sha1 (process-stable) instead of
                # Python's built-in hash() (PYTHONHASHSEED-randomized).
                anchor = scored.handle or (
                    "_" + hashlib.sha1(
                        (scored.excerpt or "").encode("utf-8")
                    ).hexdigest()[:12]
                )
                target_post_url = f"{thread_url}#replier={anchor}"
                author_handle = scored.handle or "unknown"
                action_score = ACTION_TO_SCORE.get(
                    scored.recommended_action_label, 0
                )

                existing = conn.execute(
                    "SELECT id FROM reply_targets WHERE target_post_url = ?",
                    (target_post_url,),
                ).fetchone()
                if existing is None:
                    rt_id = int(conn.execute(
                        """
                        INSERT INTO reply_targets
                            (discovered_via, source, target_post_url,
                             target_author_handle, target_text,
                             relevance_score, engagement_surface_score,
                             saturation_score, reply_opportunity_score,
                             recommended_action_label,
                             recommended_action_score, score_rationale)
                        VALUES ('manual', 'replier_under_thread', ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?)
                        RETURNING id
                        """,
                        (
                            target_post_url, author_handle, scored.excerpt,
                            scored.relevance_score,
                            scored.engagement_surface_score,
                            scored.saturation_score,
                            scored.reply_opportunity_score,
                            scored.recommended_action_label, action_score,
                            scored.score_rationale,
                        ),
                    ).fetchone()[0])
                    created += 1
                else:
                    rt_id = int(existing["id"])
                    conn.execute(
                        """
                        UPDATE reply_targets
                        SET source                   = 'replier_under_thread',
                            target_author_handle     = ?,
                            target_text              = COALESCE(?, target_text),
                            relevance_score          = ?,
                            engagement_surface_score = ?,
                            saturation_score         = ?,
                            reply_opportunity_score  = ?,
                            recommended_action_label = ?,
                            recommended_action_score = ?,
                            score_rationale          = ?,
                            last_checked_at_utc      = datetime('now')
                        WHERE id = ?
                        """,
                        (
                            author_handle, scored.excerpt,
                            scored.relevance_score,
                            scored.engagement_surface_score,
                            scored.saturation_score,
                            scored.reply_opportunity_score,
                            scored.recommended_action_label, action_score,
                            scored.score_rationale, rt_id,
                        ),
                    )
                    updated += 1
            except Exception as exc:  # noqa: BLE001 — wrap per-excerpt
                identity = excerpt.handle or "<no-handle>"
                errors.append(f"replier {identity!r}: {type(exc).__name__}: {exc}")
                continue
            out_candidates.append({
                "reply_target_id": rt_id,
                "handle": scored.handle,
                "excerpt": scored.excerpt,
                "relevance_score": scored.relevance_score,
                "engagement_surface_score": scored.engagement_surface_score,
                "saturation_score": scored.saturation_score,
                "reply_opportunity_score": scored.reply_opportunity_score,
                "thread_context_fit_score": scored.thread_context_fit_score,
                "recommended_action_label": scored.recommended_action_label,
                "score_rationale": scored.score_rationale,
            })

    return {
        "thread_url": thread_url,
        "candidates": out_candidates,
        "created_count": created,
        "updated_count": updated,
        "errors": errors,
    }


__all__ = [
    "ReplierExcerpt",
    "ReplyTargetCandidate",
    "parse_replier_paste",
    "score_replier",
    "score_replier_pool",
    "thread_context_fit_score",
]
