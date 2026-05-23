"""X ↔ blog repurposing — Phase 6 §28.34.

Two agent tools cover bidirectional repurposing:

* ``repurpose_blog_to_x(blog_id, mode)`` — modes:
  ``thread_from_sections`` (one X post per H2), ``single_post_summary``
  (compress to one post), ``teaser_with_link`` (hook + URL).
* ``repurpose_x_to_blog_idea(post_id)`` — expand a shipped X post
  into a blog idea (status='idea', outline, pillar/audience
  recommendation).

Plagiarism guard (load-bearing for blog → X):

Every blog → X output runs through ``app.agent.inspiration.compute_plagiarism_risk``
against the source blog body. The deterministic floor (Jaccard +
n-gram, §28.29) catches high overlap that the model might
underreport. ``high`` overlap BLOCKS the drafts-pipeline insert
until Daniel passes ``override_plagiarism=True`` — the override is
audit-logged with the reason.

Linkage rows:

* blog → X: linkage rows in ``blog_to_post_links`` are created at
  SHIP time (when the resulting draft becomes a published post),
  NOT at draft time. Drafts may be discarded; we don't pollute
  ``blog_to_post_links`` with rows for drafts that never shipped.
  The draft's ``agent_drafts.notes`` carries an implicit reference
  ("derived from blog #N, mode=X") until ship time. See
  ``finalize_blog_to_post_link()`` below for the ship-time helper.
* X → blog: the linkage row is created IMMEDIATELY at idea creation
  — the linkage is unambiguous (one X post → one new blog row).

Read scope: each tool reads the source content + identity context
(niche + voice + samples + lore) and nothing else. No cross-blog
context, no other posts, no PII. Same exclusions as
``blog_drafting`` (§28.32).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from app.agent import audit_log as _audit_log
from app.agent import blogs as _blogs
from app.agent import inspiration as _inspiration
from app.agent import niche as _niche
from app.agent._blog_agent_helpers import (
    VALID_CONFIDENCE_LABELS as _VALID_CONFIDENCE_LABELS,
    make_default_caller as _make_default_caller,
    parse_json_response as _parse_json_response_shared,
    render_identity_context as _render_identity_context_shared,
    require_confidence as _require_confidence_shared,
)
from app.agent.untrusted_wrap import wrap_untrusted as _wrap_untrusted
from app.db import transaction


_LOG = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
CONFIG_DIR: Path = PROJECT_ROOT / "config"

PROMPT_PATHS: dict[str, Path] = {
    "thread_from_sections": CONFIG_DIR / "blog_to_x_thread_prompt.md",
    "single_post_summary": CONFIG_DIR / "blog_to_x_single_prompt.md",
    "teaser_with_link": CONFIG_DIR / "blog_to_x_teaser_prompt.md",
    "x_to_blog_idea": CONFIG_DIR / "x_to_blog_idea_prompt.md",
}

DEFAULT_MODEL: str = "claude-opus-4-7"
DEFAULT_MAX_TOKENS: int = 4096
DEFAULT_TIMEOUT_SECONDS: float = 90.0

VALID_REPURPOSE_MODES: frozenset[str] = frozenset(
    {"thread_from_sections", "single_post_summary", "teaser_with_link"}
)

RepurposeMode = Literal[
    "thread_from_sections", "single_post_summary", "teaser_with_link"
]


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------
class BlogRepurposingError(RuntimeError):
    """Base for repurposing-module errors."""


class BlogRepurposingNicheUndefinedError(BlogRepurposingError):
    """Raised when the niche stack is empty — tools refuse to run."""


class BlogRepurposingModelError(BlogRepurposingError):
    """Raised when the model call fails or returns un-parseable JSON."""


class PlagiarismBlockedError(BlogRepurposingError):
    """Raised when a blog → X output has ``high`` plagiarism overlap and
    ``override_plagiarism=False``.

    Carries the structured per-post risk read so the UI can surface
    per-draft "I've reviewed; accept anyway" checkboxes.
    """

    def __init__(self, blocked_outputs: list[dict]) -> None:
        self.blocked_outputs = blocked_outputs
        super().__init__(
            f"plagiarism guard blocked {len(blocked_outputs)} output(s) — "
            "override required to proceed"
        )


# ---------------------------------------------------------------------------
# Result dataclasses.
# ---------------------------------------------------------------------------
ConfidenceLabel = Literal["fact", "inference", "speculation", "mixed"]


@dataclass(frozen=True, slots=True)
class RepurposedDraft:
    """One agent_drafts row produced by repurpose_blog_to_x."""

    draft_id: int
    text: str
    section_anchor: str | None
    confidence_label: ConfidenceLabel
    plagiarism_risk_label: str  # low | medium | high
    jaccard_similarity: float
    longest_shared_ngram_length: int
    plagiarism_override_used: bool


@dataclass(frozen=True, slots=True)
class RepurposeBlogToXResult:
    blog_id: int
    mode: str
    drafts: tuple[RepurposedDraft, ...]
    overall_confidence_label: ConfidenceLabel
    rationale: str
    tokens_used: int


@dataclass(frozen=True, slots=True)
class RepurposeXToBlogIdeaResult:
    post_id: int
    new_blog_id: int
    title: str
    outline_markdown: str
    target_length_words: int
    pillar_recommendation: str | None
    audience_recommendation: str | None
    confidence_label: ConfidenceLabel
    rationale: str
    blog_to_post_link_id: int
    tokens_used: int


# ---------------------------------------------------------------------------
# Model caller injection.
# ---------------------------------------------------------------------------
ModelCaller = Callable[[str, str, str], tuple[str, int, int]]


# P6R-18: delegated to shared helper.
_default_caller = _make_default_caller(
    api_key_missing_exc=BlogRepurposingModelError,
    max_tokens=DEFAULT_MAX_TOKENS,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
)


# ---------------------------------------------------------------------------
# Helpers (shared with blog_drafting).
# ---------------------------------------------------------------------------
def _load_prompt(kind: str) -> str:
    path = PROMPT_PATHS[kind]
    if not path.exists():
        raise BlogRepurposingError(f"prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def _render_identity_context(conn) -> str:
    """Render the repurposing-flavored identity block (4 samples, no
    structural voice splice — repurposing prompts are shorter so we
    keep the identity context tight). P6R-18: delegates to shared
    helper with the repurposing flavor."""
    return _render_identity_context_shared(
        conn, sample_limit=4, include_voice_structural=False,
    )


def _refuse_if_niche_undefined(conn) -> None:
    if not _niche.is_niche_defined(conn):
        raise BlogRepurposingNicheUndefinedError(_niche.CANONICAL_REFUSAL)


def _parse_json_response(text: str) -> dict[str, Any]:
    return _parse_json_response_shared(text, model_error_exc=BlogRepurposingModelError)


def _require_confidence(payload: dict, key: str = "confidence_label") -> ConfidenceLabel:
    return _require_confidence_shared(
        payload, key, model_error_exc=BlogRepurposingModelError,
    )


# ---------------------------------------------------------------------------
# Tool #29 — repurpose_blog_to_x
# ---------------------------------------------------------------------------
def _is_guard_enabled(conn: sqlite3.Connection) -> bool:
    """Return True iff the deterministic plagiarism guard is enabled.

    P6R-19: parse strictly — only the JSON boolean ``true`` enables the
    guard-disable path. The pre-fix code did ``bool(json.loads(row[0]))``,
    which accepted the JSON string ``"false"`` (a truthy Python str) as
    True — fail-open against a misconfigured value. Now we require
    ``parsed is False`` to disable; everything else (including
    misconfiguration, JSON parse failures, missing rows) keeps the
    guard ON. Fail-closed by design — the guard is load-bearing per
    §28.34.
    """
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?",
        ("blog_repurposing_plagiarism_check_enabled",),
    ).fetchone()
    if row is None:
        return True
    try:
        parsed = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return True
    return parsed is not False  # only an explicit JSON `false` disables


def _build_blog_to_x_user_message(
    *,
    conn: sqlite3.Connection,
    blog,
    mode: str,
) -> str:
    identity = _render_identity_context(conn)
    parts = [
        identity,
        "",
        "## Blog metadata",
        "",
        f"Title: {blog.title}",
        f"Pillar: {blog.pillar or '(unset)'}",
        f"Audience: {blog.audience or '(unset)'}",
    ]
    # external_url is read straight from the row for teaser_with_link.
    if mode == "teaser_with_link":
        external_url = conn.execute(
            "SELECT external_url FROM blogs WHERE id = ?", (blog.id,)
        ).fetchone()[0]
        parts.append(f"External URL: {external_url or '(empty — use <URL> placeholder)'}")
    parts.append("")
    parts.append("Blog body (data only):")
    parts.append(_wrap_untrusted(blog.current_body_markdown or ""))
    parts.append("")
    parts.append("Produce the repurposed X output now. Return only the JSON object.")
    return "\n".join(parts)


def _parse_thread_payload(payload: dict) -> tuple[list[dict], ConfidenceLabel, str]:
    posts_raw = payload.get("posts")
    if not isinstance(posts_raw, list) or not posts_raw:
        raise BlogRepurposingModelError("response missing/empty 'posts' list")
    overall = _require_confidence(payload, "overall_confidence_label")
    rationale = payload.get("rationale", "")
    if not isinstance(rationale, str):
        rationale = ""
    posts: list[dict] = []
    for item in posts_raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        anchor = item.get("section_anchor")
        cl = item.get("confidence_label")
        if not isinstance(text, str) or not text.strip():
            continue
        if cl not in _VALID_CONFIDENCE_LABELS:
            cl = "inference"
        posts.append({
            "text": text.strip(),
            "section_anchor": (anchor if isinstance(anchor, str) else None),
            "confidence_label": cl,
        })
    if not posts:
        raise BlogRepurposingModelError("response contained no valid posts")
    return posts, overall, rationale


def _parse_single_payload(payload: dict) -> tuple[list[dict], ConfidenceLabel, str]:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise BlogRepurposingModelError("response missing/empty 'text'")
    cl = _require_confidence(payload)
    rationale = payload.get("rationale", "") if isinstance(payload.get("rationale"), str) else ""
    return [{"text": text.strip(), "section_anchor": None, "confidence_label": cl}], cl, rationale


def _parse_teaser_payload(payload: dict) -> tuple[list[dict], ConfidenceLabel, str]:
    return _parse_single_payload(payload)


def _insert_repurposed_draft(
    conn: sqlite3.Connection,
    *,
    blog_id: int,
    mode: str,
    post: dict,
    risk: _inspiration.PlagiarismRead,
    override_used: bool,
    pillar: str | None,
    audience: str | None,
) -> int:
    """Insert ONE agent_drafts row carrying the repurposed X output text.

    Notes carries the implicit linkage to the source blog + mode; the
    explicit blog_to_post_links row lands at SHIP time via
    finalize_blog_to_post_link below. similarity_warning_json carries
    the plagiarism read so the editor can surface the chip on the
    draft.
    """
    similarity_warning = {
        "kind": "blog_to_x_plagiarism",
        "source_blog_id": blog_id,
        "mode": mode,
        "jaccard_similarity": risk.jaccard_similarity,
        "longest_shared_ngram_length": risk.longest_shared_ngram_length,
        "deterministic_risk_label": risk.deterministic_risk_label,
        "override_used": override_used,
    }
    cur = conn.execute(
        """
        INSERT INTO agent_drafts
          (draft_kind, text, pillar, audience, content_type, status,
           confidence_label, similarity_warning_json,
           agent_reasoning, iwh_attempt_index)
        VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, 1)
        RETURNING id
        """,
        (
            "thread_root" if mode == "thread_from_sections" else "standalone",
            post["text"],
            pillar,
            audience,
            "value",  # repurposed blog snippets default to 'value' content type
            post["confidence_label"],
            json.dumps(similarity_warning),
            f"Derived from blog #{blog_id}, mode={mode}, "
            f"section={post['section_anchor'] or '(none)'}",
        ),
    )
    return int(cur.fetchone()[0])


def repurpose_blog_to_x(
    conn: sqlite3.Connection,
    *,
    blog_id: int,
    mode: RepurposeMode,
    override_plagiarism: bool = False,
    model: str = DEFAULT_MODEL,
    model_caller: ModelCaller | None = None,
) -> RepurposeBlogToXResult:
    if mode not in VALID_REPURPOSE_MODES:
        raise BlogRepurposingError(
            f"unknown mode {mode!r}. Allowed: {sorted(VALID_REPURPOSE_MODES)}"
        )
    _refuse_if_niche_undefined(conn)
    blog = _blogs.get_blog(conn, blog_id)
    if not (blog.current_body_markdown and blog.current_body_markdown.strip()):
        raise BlogRepurposingError(
            f"blog #{blog_id} has no body — nothing to repurpose"
        )

    caller = model_caller or _default_caller
    user_message = _build_blog_to_x_user_message(conn=conn, blog=blog, mode=mode)
    system_prompt = _load_prompt(mode)
    response_text, in_tok, out_tok = caller(system_prompt, user_message, model)
    payload = _parse_json_response(response_text)

    if mode == "thread_from_sections":
        posts, overall, rationale = _parse_thread_payload(payload)
    elif mode == "single_post_summary":
        posts, overall, rationale = _parse_single_payload(payload)
    else:
        posts, overall, rationale = _parse_teaser_payload(payload)

    guard_enabled = _is_guard_enabled(conn)

    # Score plagiarism for each output. Build the structured per-post
    # risk read; if guard is enabled AND any output is high AND override
    # is not set, raise PlagiarismBlockedError WITHOUT inserting drafts.
    risks: list[_inspiration.PlagiarismRead] = []
    for p in posts:
        risk = _inspiration.compute_plagiarism_risk(
            conn, blog.current_body_markdown, p["text"]
        )
        risks.append(risk)

    if guard_enabled and not override_plagiarism:
        high_risk = [
            {
                "index": i,
                "text_excerpt": p["text"][:120],
                "jaccard_similarity": r.jaccard_similarity,
                "longest_shared_ngram_length": r.longest_shared_ngram_length,
                "deterministic_risk_label": r.deterministic_risk_label,
            }
            for i, (p, r) in enumerate(zip(posts, risks))
            if r.deterministic_risk_label == "high"
        ]
        if high_risk:
            raise PlagiarismBlockedError(high_risk)

    # Insert drafts in a single transaction.
    drafts: list[RepurposedDraft] = []
    with transaction(conn):
        for p, risk in zip(posts, risks):
            override_used = (
                override_plagiarism and risk.deterministic_risk_label == "high"
            )
            draft_id = _insert_repurposed_draft(
                conn,
                blog_id=blog_id,
                mode=mode,
                post=p,
                risk=risk,
                override_used=override_used,
                pillar=blog.pillar,
                audience=blog.audience,
            )
            drafts.append(
                RepurposedDraft(
                    draft_id=draft_id,
                    text=p["text"],
                    section_anchor=p["section_anchor"],
                    confidence_label=p["confidence_label"],
                    plagiarism_risk_label=risk.deterministic_risk_label,
                    jaccard_similarity=risk.jaccard_similarity,
                    longest_shared_ngram_length=risk.longest_shared_ngram_length,
                    plagiarism_override_used=override_used,
                )
            )
        _audit_log.log(
            conn,
            event_category="data",
            event_type="blog_repurpose_to_x",
            target_type="blog",
            target_id=blog_id,
            details={
                "mode": mode,
                "draft_ids": [d.draft_id for d in drafts],
                "draft_count": len(drafts),
                "tokens_used": in_tok + out_tok,
                "override_plagiarism": override_plagiarism,
                "highest_risk_label": max(
                    (r.deterministic_risk_label for r in risks),
                    default="low",
                    key=lambda x: {"low": 0, "medium": 1, "high": 2}[x],
                ),
            },
        )

    return RepurposeBlogToXResult(
        blog_id=blog_id,
        mode=mode,
        drafts=tuple(drafts),
        overall_confidence_label=overall,
        rationale=rationale,
        tokens_used=in_tok + out_tok,
    )


# ---------------------------------------------------------------------------
# Ship-time linkage helper — called from the publish path.
# ---------------------------------------------------------------------------
def finalize_blog_to_post_link(
    conn: sqlite3.Connection,
    *,
    blog_id: int,
    post_id: int,
    mode: str,
) -> int | None:
    """Create the ``blog_to_post_links`` row when a repurposed draft ships.

    Called from the publish path (post publication) once the
    ``agent_drafts → posts`` row has a populated ``published_to_x_at``.
    No-op if the linkage row already exists (idempotent across retries).
    """
    # P6R-29: pre-fix used .get(mode, "summary_post") which silently
    # mapped typos (e.g. "thread_from_section" missing the trailing
    # 's') to "summary_post". Now an unknown mode raises so the typo
    # surfaces at the call site instead of corrupting the linkage
    # taxonomy.
    _MODE_TO_RELATIONSHIP = {
        "thread_from_sections": "thread_root",
        "single_post_summary": "summary_post",
        "teaser_with_link": "teaser_with_link",
    }
    if mode not in _MODE_TO_RELATIONSHIP:
        raise BlogRepurposingError(
            f"unknown mode {mode!r}. Allowed: "
            f"{sorted(_MODE_TO_RELATIONSHIP)}"
        )
    relationship_kind = _MODE_TO_RELATIONSHIP[mode]
    existing = conn.execute(
        "SELECT id FROM blog_to_post_links "
        "WHERE blog_id = ? AND post_id = ? AND direction = 'blog_to_post'",
        (blog_id, post_id),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])
    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO blog_to_post_links
              (blog_id, post_id, direction, relationship_kind, created_by)
            VALUES (?, ?, 'blog_to_post', ?, 'agent')
            RETURNING id
            """,
            (blog_id, post_id, relationship_kind),
        )
        link_id = int(cur.fetchone()[0])
        _audit_log.log(
            conn,
            event_category="data",
            event_type="blog_to_post_link_created",
            target_type="blog",
            target_id=blog_id,
            details={"post_id": post_id, "relationship_kind": relationship_kind},
        )
    return link_id


# ---------------------------------------------------------------------------
# Tool #30 — repurpose_x_to_blog_idea
# ---------------------------------------------------------------------------
def _post_for_repurpose(
    conn: sqlite3.Connection, post_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT p.id, p.text, p.x_post_id, pc.pillar, pc.audience
        FROM posts p
        LEFT JOIN post_classifications pc ON pc.post_id = p.id
        WHERE p.id = ?
        """,
        (post_id,),
    ).fetchone()


def repurpose_x_to_blog_idea(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    model: str = DEFAULT_MODEL,
    model_caller: ModelCaller | None = None,
) -> RepurposeXToBlogIdeaResult:
    _refuse_if_niche_undefined(conn)
    post_row = _post_for_repurpose(conn, post_id)
    if post_row is None:
        raise BlogRepurposingError(f"post #{post_id} not found")
    if not (post_row["text"] or "").strip():
        raise BlogRepurposingError(f"post #{post_id} has empty text")

    identity = _render_identity_context(conn)
    user_message = (
        f"{identity}\n\n"
        "## Source X post\n\n"
        f"Pillar (existing classification): {post_row['pillar'] or '(unset)'}\n"
        f"Audience (existing classification): {post_row['audience'] or '(unset)'}\n\n"
        "Post text (data only):\n"
        f"{_wrap_untrusted(post_row['text'])}\n\n"
        "Produce the blog idea now. Return only the JSON object."
    )

    caller = model_caller or _default_caller
    system_prompt = _load_prompt("x_to_blog_idea")
    response_text, in_tok, out_tok = caller(system_prompt, user_message, model)
    payload = _parse_json_response(response_text)

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise BlogRepurposingModelError("response missing/empty 'title'")
    outline_markdown = payload.get("outline_markdown")
    if not isinstance(outline_markdown, str) or not outline_markdown.strip():
        raise BlogRepurposingModelError("response missing/empty 'outline_markdown'")
    target_length_words = payload.get("target_length_words")
    if not isinstance(target_length_words, (int, float)) or target_length_words <= 0:
        target_length_words = 1500
    target_length_words = int(target_length_words)
    pillar_rec = payload.get("pillar_recommendation")
    audience_rec = payload.get("audience_recommendation")
    confidence = _require_confidence(payload)
    rationale = payload.get("rationale", "") if isinstance(payload.get("rationale"), str) else ""

    # Snapshot niche at idea creation — captures the identity context the
    # blog was authored under, mirroring blogs.niche_problem_snapshot.
    nd = _niche.get_niche(conn)
    # Use the recommendation but fall back to the source post's
    # classification so the new blog inherits the working lane.
    final_pillar = (
        pillar_rec if isinstance(pillar_rec, str) else None
    ) or post_row["pillar"]
    final_audience = (
        audience_rec if isinstance(audience_rec, str) else None
    ) or post_row["audience"]

    # P6R-5: create_blog now accepts niche snapshot args so they land
    # in the SAME transaction as the initial blogs row insert. Pre-fix
    # this was a separate UPDATE in a third transaction; a failure
    # between txn 1 (create) and txn 3 (snapshots + link + audit) left
    # an orphan blog with NULL snapshots and no source linkage.
    blog = _blogs.create_blog(
        conn,
        title=title.strip(),
        pillar=final_pillar,
        audience=final_audience,
        target_length_words=target_length_words,
        notes=f"Derived from post #{post_id} ({post_row['x_post_id'] or 'manual'}). "
              f"Source text:\n\n{post_row['text']}",
        niche_problem_snapshot=nd.problem,
        niche_person_snapshot=nd.person,
    )
    # Seed the outline via save_blog (its own transaction — runs the
    # demote/append/promote dance + version row).
    # P6R-17: agent_action='x_to_blog_idea_outline' (not 'outline') so
    # analytics can disambiguate "seed outline from X post repurposing"
    # from "outline produced by standalone outline_blog tool".
    _blogs.save_blog(
        conn,
        blog.id,
        outline_markdown=outline_markdown,
        created_by="agent",
        agent_action="x_to_blog_idea_outline",
        confidence_label_at_version=confidence,
    )

    # Link row + audit are atomic with each other in a SEPARATE
    # transaction (transaction() uses BEGIN IMMEDIATE so it can't nest
    # with save_blog's own transaction above). On failure of THIS
    # transaction the blog still exists with snapshots populated (from
    # create_blog) and the outline version (from save_blog) — a clean
    # recoverable state, not an orphan.
    with transaction(conn):
        link_cur = conn.execute(
            """
            INSERT INTO blog_to_post_links
              (blog_id, post_id, direction, relationship_kind, created_by, notes)
            VALUES (?, ?, 'post_to_blog', 'derived_outline', 'agent', ?)
            RETURNING id
            """,
            (blog.id, post_id, rationale[:500] if rationale else None),
        )
        link_id = int(link_cur.fetchone()[0])
        _audit_log.log(
            conn,
            event_category="data",
            event_type="post_repurposed_to_blog_idea",
            target_type="blog",
            target_id=blog.id,
            details={
                "source_post_id": post_id,
                "confidence_label": confidence,
                "link_id": link_id,
                "tokens_used": in_tok + out_tok,
            },
        )

    return RepurposeXToBlogIdeaResult(
        post_id=post_id,
        new_blog_id=blog.id,
        title=title.strip(),
        outline_markdown=outline_markdown,
        target_length_words=target_length_words,
        pillar_recommendation=final_pillar,
        audience_recommendation=final_audience,
        confidence_label=confidence,
        rationale=rationale,
        blog_to_post_link_id=link_id,
        tokens_used=in_tok + out_tok,
    )
