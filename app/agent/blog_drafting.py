"""Blog drafting agent tools — Phase 6 §28.32.

Four registered tools cover the blog production pipeline:

* ``outline_blog(blog_id)`` — generates a structured outline.
* ``draft_blog(blog_id, target_length_words=None)`` — full draft body.
* ``suggest_blog_edits(blog_id)`` — per-paragraph edit suggestions
  (NEVER auto-applies; UI surfaces Accept / Reject / Modify).
* ``generate_blog_seo_metadata(blog_id)`` — SEO sidecar fields.

All four respect §28.6 cost cap and emit ``<confidence>`` tags per
§28.14. Each tool persists its result via the §28.31 schema discipline
in ``app/agent/blogs.py`` — outline/draft go through ``save_blog``
(version row appended); SEO goes through ``set_seo_metadata`` (no
version row — sidecar). ``suggest_blog_edits`` does NOT persist
anything by itself; the caller applies accepted edits via a separate
``save_blog`` call.

Unified identity stack (the entire point of putting blogs in
XGrowth — §28.31):

* Active niche definition (§28.16).
* Active voice profile (§28.12) — self-description + structural read.
* Top-N voice samples (§28.5).
* Top-N active personality lore rows (§28.21).

If the niche is undefined, all four tools refuse and return the
§28.16 canonical refusal message. Voice profile / samples / lore
are optional — the tool runs without them but the system prompt
notes their absence (and the prompt template warns the model).

Untrusted-data discipline (§28.2 rule #1):

The blog's own body, outline, title, and Daniel's notes are wrapped
in ``--- BEGIN_UNTRUSTED_DATA … --- END_UNTRUSTED_DATA ---`` markers
so prompt injection from the body can't redirect the model. This
mirrors the inspiration / brain_dump / account_research convention.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from app.agent import audit_log as _audit_log
from app.agent import blogs as _blogs
from app.agent import niche as _niche
from app.agent import personality_lore as _personality_lore
from app.agent import voice as _voice
from app.agent import voice_profile as _voice_profile
from app.agent.untrusted_wrap import (
    strip_code_fence as _strip_code_fence,
    wrap_untrusted as _wrap_untrusted,
)

_LOG = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
CONFIG_DIR: Path = PROJECT_ROOT / "config"

PROMPT_PATHS: dict[str, Path] = {
    "outline": CONFIG_DIR / "blog_outline_prompt.md",
    "draft": CONFIG_DIR / "blog_draft_prompt.md",
    "edit_suggestions": CONFIG_DIR / "blog_edit_suggestions_prompt.md",
    "seo": CONFIG_DIR / "blog_seo_prompt.md",
}

DEFAULT_MODEL: str = "claude-opus-4-7"
DEFAULT_MAX_TOKENS: int = 4096
DEFAULT_TIMEOUT_SECONDS: float = 90.0


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------
class BlogDraftingError(RuntimeError):
    """Base for blog-drafting errors surfaced to the orchestrator."""


class BlogDraftingNicheUndefinedError(BlogDraftingError):
    """Raised when the niche stack is empty — tools refuse to run."""


class BlogDraftingModelError(BlogDraftingError):
    """Raised when the model call fails or returns un-parseable JSON."""


# ---------------------------------------------------------------------------
# Dataclasses (tool return shapes).
# ---------------------------------------------------------------------------
ConfidenceLabel = Literal["fact", "inference", "speculation", "mixed"]
_VALID_CONFIDENCE_LABELS: frozenset[str] = frozenset(
    {"fact", "inference", "speculation", "mixed"}
)


@dataclass(frozen=True, slots=True)
class OutlineResult:
    blog_id: int
    version_id: int
    version_number: int
    outline_markdown: str
    section_count: int
    estimated_length_words: int
    confidence_label: ConfidenceLabel
    rationale: str
    tokens_used: int


@dataclass(frozen=True, slots=True)
class DraftResult:
    blog_id: int
    version_id: int
    version_number: int
    body_markdown: str
    word_count: int
    sections_used: tuple[str, ...]
    confidence_label: ConfidenceLabel
    notes: str | None
    tokens_used: int


@dataclass(frozen=True, slots=True)
class EditSuggestion:
    paragraph_anchor: str
    suggested_replacement: str
    rationale: str
    confidence_label: ConfidenceLabel


@dataclass(frozen=True, slots=True)
class EditSuggestionsResult:
    blog_id: int
    suggestions: tuple[EditSuggestion, ...]
    overall_confidence_label: ConfidenceLabel
    summary: str
    tokens_used: int


@dataclass(frozen=True, slots=True)
class SeoMetadataResult:
    blog_id: int
    seo_title: str
    seo_description: str
    seo_tags: tuple[str, ...]
    confidence_label: ConfidenceLabel
    rationale: str
    tokens_used: int


# ---------------------------------------------------------------------------
# ModelCaller injection point + default Anthropic caller.
# ---------------------------------------------------------------------------
# (system_prompt, user_message, model) -> (response_text, in_tokens, out_tokens)
ModelCaller = Callable[[str, str, str], tuple[str, int, int]]


def _default_caller(
    system_prompt: str, user_message: str, model: str
) -> tuple[str, int, int]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise BlogDraftingModelError(
            "ANTHROPIC_API_KEY is not set. See spec §28.8 for env setup."
        )
    import anthropic
    # Explicit timeout. The Anthropic SDK defaults to 10 minutes; long
    # blog drafts can legitimately run ~30-60 seconds, so 90s gives
    # headroom without leaving the Streamlit thread blocked for ten
    # minutes if the network hangs. Mirrors the timeout discipline
    # applied across other agent caller sites (P511R-20).
    client = anthropic.Anthropic(api_key=api_key, timeout=DEFAULT_TIMEOUT_SECONDS)
    resp = client.messages.create(
        model=model,
        max_tokens=DEFAULT_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    text_parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    in_tok = int(getattr(resp.usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(resp.usage, "output_tokens", 0) or 0)
    return ("".join(text_parts), in_tok, out_tok)


# ---------------------------------------------------------------------------
# Prompt loading + identity-context rendering.
# ---------------------------------------------------------------------------
def _load_prompt(kind: str) -> str:
    path = PROMPT_PATHS[kind]
    if not path.exists():
        raise BlogDraftingError(f"prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def _render_identity_context(conn) -> str:
    """Render the niche × voice × samples × lore identity block.

    Lives in the USER message rather than the system prompt so each
    tool call gets a fresh read — the editor's identity readout is
    bound to the same source-of-truth, so a voice-profile regen in
    Settings is visible to the next agent call without restarting.
    """
    nd = _niche.get_niche(conn)
    profile = _voice_profile.get_active(conn)
    samples = _voice.get_active_voice_samples(conn)
    splice_n = _personality_lore.get_splice_count(conn)
    active_lore = _personality_lore.list_active(conn, limit=splice_n)

    parts: list[str] = ["## Identity context", ""]
    if nd.is_defined():
        parts.append(f"You help **{nd.person}** solve **{nd.problem}**.")
    else:
        # Should be caught upstream by the refuse-on-undefined-niche
        # check, but include the fallback for completeness.
        parts.append("(niche is not defined — refuse and ask Daniel to fill it in)")
    parts.append("")

    if profile is not None:
        desc = profile.self_description()
        if desc:
            parts.append(f"_Voice self-description:_ {desc}")
        cadence = profile.cadence() or {}
        vocab = profile.vocabulary_signatures()[:5]
        stops = profile.stop_phrases()[:5]
        bits: list[str] = []
        if cadence:
            for k in ("avg_chars", "avg_sentences", "one_idea_per_line_rate"):
                v = cadence.get(k)
                if v is not None:
                    bits.append(f"{k}={v}")
        if bits:
            parts.append(f"_Voice cadence:_ {', '.join(bits)}")
        if vocab:
            parts.append("_Vocabulary signatures:_ " + ", ".join(f"`{v}`" for v in vocab))
        if stops:
            parts.append("_Stop phrases (avoid):_ " + ", ".join(f"`{s}`" for s in stops))
        parts.append("")
    else:
        parts.append("(no active voice profile — operate from niche + samples alone)")
        parts.append("")

    if samples:
        parts.append("### Voice samples")
        for s in samples[:6]:
            header = f"_Sample (priority {s.priority}"
            if s.pillar:
                header += f", pillar={s.pillar}"
            header += "):_"
            parts.append(header)
            parts.append("> " + s.text.replace("\n", "\n> "))
            parts.append("")
    else:
        parts.append("(no active voice samples)")
        parts.append("")

    lore_block = _personality_lore.render_splice_block(active_lore)
    if lore_block:
        parts.append("### Personality lore (active)")
        parts.append(lore_block)
        parts.append("")

    return "\n".join(parts).strip()


def _refuse_if_niche_undefined(conn) -> None:
    if not _niche.is_niche_defined(conn):
        raise BlogDraftingNicheUndefinedError(_niche.CANONICAL_REFUSAL)


# ---------------------------------------------------------------------------
# Response parsing.
# ---------------------------------------------------------------------------
def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fence(text).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise BlogDraftingModelError(f"model returned non-JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BlogDraftingModelError("model returned non-object JSON")
    return payload


def _require_str(payload: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    v = payload.get(key)
    if not isinstance(v, str) or (not allow_empty and not v.strip()):
        raise BlogDraftingModelError(f"response missing/invalid {key!r}")
    return v


def _require_int(payload: dict[str, Any], key: str) -> int:
    v = payload.get(key)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise BlogDraftingModelError(f"response missing/invalid {key!r}")
    return int(v)


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    v = payload.get(key)
    if not isinstance(v, list):
        raise BlogDraftingModelError(f"response missing/invalid {key!r}")
    return v


def _require_confidence(payload: dict[str, Any], key: str = "confidence_label") -> str:
    v = payload.get(key)
    if v not in _VALID_CONFIDENCE_LABELS:
        raise BlogDraftingModelError(
            f"{key} must be one of {sorted(_VALID_CONFIDENCE_LABELS)}; got {v!r}"
        )
    return v


_H2_RE = re.compile(r"^##\s+", re.MULTILINE)


def _count_h2_headings(markdown: str) -> int:
    return len(_H2_RE.findall(markdown or ""))


# ---------------------------------------------------------------------------
# Tool #25 — outline_blog
# ---------------------------------------------------------------------------
def outline_blog(
    conn,
    *,
    blog_id: int,
    daniel_notes: str | None = None,
    model: str = DEFAULT_MODEL,
    model_caller: ModelCaller | None = None,
) -> OutlineResult:
    _refuse_if_niche_undefined(conn)
    blog = _blogs.get_blog(conn, blog_id)

    identity = _render_identity_context(conn)
    blog_block_parts = [
        f"Title: {blog.title}",
        f"Pillar: {blog.pillar or '(unset)'}",
        f"Audience: {blog.audience or '(unset)'}",
        f"Status: {blog.status}",
        f"Target length (words): {blog.target_length_words or '(unset)'}",
    ]
    if daniel_notes:
        blog_block_parts.append("")
        blog_block_parts.append("Daniel's notes for this blog:")
        blog_block_parts.append(_wrap_untrusted(daniel_notes))
    if blog.outline_markdown:
        blog_block_parts.append("")
        blog_block_parts.append("Prior outline (data only — feel free to revise):")
        blog_block_parts.append(_wrap_untrusted(blog.outline_markdown))

    user_message = (
        f"{identity}\n\n"
        "## Blog metadata\n\n"
        + "\n".join(blog_block_parts)
        + "\n\nProduce the outline now. Return only the JSON object."
    )

    caller = model_caller or _default_caller
    system_prompt = _load_prompt("outline")
    response_text, in_tok, out_tok = caller(system_prompt, user_message, model)
    payload = _parse_json_response(response_text)

    outline_markdown = _require_str(payload, "outline_markdown")
    estimated_length = _require_int(payload, "estimated_length_words")
    confidence = _require_confidence(payload)
    rationale = _require_str(payload, "rationale", allow_empty=True)

    # Cross-check section_count against actual H2 headings.
    declared_count = _require_int(payload, "section_count")
    actual_count = _count_h2_headings(outline_markdown)
    if declared_count != actual_count:
        _LOG.warning(
            "outline_blog #%d: model claimed section_count=%d but outline has %d H2 headings",
            blog_id, declared_count, actual_count,
        )
        declared_count = actual_count  # trust the parser.

    version = _blogs.save_blog(
        conn,
        blog_id,
        outline_markdown=outline_markdown,
        created_by="agent",
        agent_action="outline",
        confidence_label_at_version=confidence,
    )
    if version is None:
        # No-op: model produced the same outline that's already current.
        # Build a synthetic result from the existing current version.
        current = _blogs.list_versions(conn, blog_id)
        cur = current[0] if current else None
        if cur is None:
            raise BlogDraftingError(
                f"blog #{blog_id} has no versions after outline_blog (impossible)"
            )
        version_id = cur.id
        version_number = cur.version_number
    else:
        version_id = version.id
        version_number = version.version_number

    _audit_log.log(
        conn,
        event_category="data",
        event_type="blog_agent_outline",
        target_type="blog",
        target_id=blog_id,
        details={
            "version_number": version_number,
            "confidence_label": confidence,
            "section_count": actual_count,
            "tokens_used": in_tok + out_tok,
        },
    )

    return OutlineResult(
        blog_id=blog_id,
        version_id=version_id,
        version_number=version_number,
        outline_markdown=outline_markdown,
        section_count=actual_count,
        estimated_length_words=estimated_length,
        confidence_label=confidence,
        rationale=rationale,
        tokens_used=in_tok + out_tok,
    )


# ---------------------------------------------------------------------------
# Tool #26 — draft_blog
# ---------------------------------------------------------------------------
def draft_blog(
    conn,
    *,
    blog_id: int,
    target_length_words: int | None = None,
    model: str = DEFAULT_MODEL,
    model_caller: ModelCaller | None = None,
) -> DraftResult:
    _refuse_if_niche_undefined(conn)
    blog = _blogs.get_blog(conn, blog_id)

    if not (blog.outline_markdown and blog.outline_markdown.strip()):
        raise BlogDraftingError(
            f"blog #{blog_id} has no outline — call outline_blog first"
        )

    identity = _render_identity_context(conn)
    target = target_length_words or blog.target_length_words

    blog_block_parts = [
        f"Title: {blog.title}",
        f"Pillar: {blog.pillar or '(unset)'}",
        f"Audience: {blog.audience or '(unset)'}",
        f"Target length (words): {target or '(unset)'}",
        "",
        "Outline (data only):",
        _wrap_untrusted(blog.outline_markdown),
    ]
    if blog.current_body_markdown:
        blog_block_parts.append("")
        blog_block_parts.append("Prior draft (data only — feel free to replace entirely):")
        blog_block_parts.append(_wrap_untrusted(blog.current_body_markdown))

    user_message = (
        f"{identity}\n\n"
        "## Blog metadata\n\n"
        + "\n".join(blog_block_parts)
        + "\n\nProduce the full draft now. Return only the JSON object."
    )

    caller = model_caller or _default_caller
    system_prompt = _load_prompt("draft")
    response_text, in_tok, out_tok = caller(system_prompt, user_message, model)
    payload = _parse_json_response(response_text)

    body_markdown = _require_str(payload, "body_markdown")
    declared_word_count = _require_int(payload, "word_count")
    confidence = _require_confidence(payload)
    sections_used_raw = _require_list(payload, "sections_used")
    notes = payload.get("notes") if isinstance(payload.get("notes"), str) else None

    actual_word_count = len(body_markdown.split())
    if abs(declared_word_count - actual_word_count) > max(actual_word_count * 0.10, 20):
        _LOG.warning(
            "draft_blog #%d: declared word_count=%d but actual=%d",
            blog_id, declared_word_count, actual_word_count,
        )

    sections_used = tuple(str(s) for s in sections_used_raw if isinstance(s, str))

    version = _blogs.save_blog(
        conn,
        blog_id,
        body_markdown=body_markdown,
        created_by="agent",
        agent_action="draft",
        confidence_label_at_version=confidence,
    )
    if version is None:
        current = _blogs.list_versions(conn, blog_id)
        cur = current[0] if current else None
        if cur is None:
            raise BlogDraftingError(
                f"blog #{blog_id} has no versions after draft_blog (impossible)"
            )
        version_id = cur.id
        version_number = cur.version_number
    else:
        version_id = version.id
        version_number = version.version_number

    _audit_log.log(
        conn,
        event_category="data",
        event_type="blog_agent_draft",
        target_type="blog",
        target_id=blog_id,
        details={
            "version_number": version_number,
            "confidence_label": confidence,
            "word_count": actual_word_count,
            "tokens_used": in_tok + out_tok,
        },
    )

    return DraftResult(
        blog_id=blog_id,
        version_id=version_id,
        version_number=version_number,
        body_markdown=body_markdown,
        word_count=actual_word_count,
        sections_used=sections_used,
        confidence_label=confidence,
        notes=notes,
        tokens_used=in_tok + out_tok,
    )


# ---------------------------------------------------------------------------
# Tool #27 — suggest_blog_edits
# ---------------------------------------------------------------------------
def suggest_blog_edits(
    conn,
    *,
    blog_id: int,
    model: str = DEFAULT_MODEL,
    model_caller: ModelCaller | None = None,
) -> EditSuggestionsResult:
    """Per-paragraph edit suggestions. NEVER auto-applies.

    Returns structured suggestions for the UI to surface with Accept /
    Reject / Modify buttons. The caller is responsible for applying
    accepted edits via ``blogs.save_blog(..., agent_action='edit_suggestion_applied')``.
    """
    _refuse_if_niche_undefined(conn)
    blog = _blogs.get_blog(conn, blog_id)
    if not (blog.current_body_markdown and blog.current_body_markdown.strip()):
        raise BlogDraftingError(
            f"blog #{blog_id} has no body — nothing to suggest edits on"
        )

    identity = _render_identity_context(conn)
    user_message = (
        f"{identity}\n\n"
        "## Blog metadata\n\n"
        f"Title: {blog.title}\n"
        f"Pillar: {blog.pillar or '(unset)'}\n"
        f"Audience: {blog.audience or '(unset)'}\n\n"
        "Body (data only — propose per-paragraph rewrites):\n"
        f"{_wrap_untrusted(blog.current_body_markdown)}\n\n"
        "Return only the JSON object."
    )

    caller = model_caller or _default_caller
    system_prompt = _load_prompt("edit_suggestions")
    response_text, in_tok, out_tok = caller(system_prompt, user_message, model)
    payload = _parse_json_response(response_text)

    suggestions_raw = _require_list(payload, "suggestions")
    overall = _require_confidence(payload, "overall_confidence_label")
    summary = _require_str(payload, "summary", allow_empty=True)

    suggestions: list[EditSuggestion] = []
    for item in suggestions_raw:
        if not isinstance(item, dict):
            continue
        anchor = item.get("paragraph_anchor")
        replacement = item.get("suggested_replacement")
        rationale = item.get("rationale", "")
        label = item.get("confidence_label", "inference")
        if not isinstance(anchor, str) or not anchor.strip():
            continue
        if not isinstance(replacement, str) or not replacement.strip():
            continue
        if label not in _VALID_CONFIDENCE_LABELS:
            label = "inference"
        suggestions.append(
            EditSuggestion(
                paragraph_anchor=anchor,
                suggested_replacement=replacement,
                rationale=str(rationale) if isinstance(rationale, str) else "",
                confidence_label=label,
            )
        )

    _audit_log.log(
        conn,
        event_category="data",
        event_type="blog_agent_suggest_edits",
        target_type="blog",
        target_id=blog_id,
        details={
            "suggestion_count": len(suggestions),
            "overall_confidence_label": overall,
            "tokens_used": in_tok + out_tok,
        },
    )

    return EditSuggestionsResult(
        blog_id=blog_id,
        suggestions=tuple(suggestions),
        overall_confidence_label=overall,
        summary=summary,
        tokens_used=in_tok + out_tok,
    )


# ---------------------------------------------------------------------------
# Tool #28 — generate_blog_seo_metadata
# ---------------------------------------------------------------------------
def generate_blog_seo_metadata(
    conn,
    *,
    blog_id: int,
    model: str = DEFAULT_MODEL,
    model_caller: ModelCaller | None = None,
) -> SeoMetadataResult:
    """Generate + persist SEO sidecar fields. No version row created.

    SEO metadata is NOT content (§28.32) — versioning it would clutter
    the timeline with cosmetic deltas. The audit row still fires.
    """
    _refuse_if_niche_undefined(conn)
    blog = _blogs.get_blog(conn, blog_id)

    identity = _render_identity_context(conn)
    user_message = (
        f"{identity}\n\n"
        "## Blog metadata\n\n"
        f"Title: {blog.title}\n"
        f"Pillar: {blog.pillar or '(unset)'}\n"
        f"Audience: {blog.audience or '(unset)'}\n\n"
        "Body (data only — generate SEO metadata from this):\n"
        f"{_wrap_untrusted(blog.current_body_markdown or '')}\n\n"
        "Return only the JSON object."
    )

    caller = model_caller or _default_caller
    system_prompt = _load_prompt("seo")
    response_text, in_tok, out_tok = caller(system_prompt, user_message, model)
    payload = _parse_json_response(response_text)

    seo_title = _require_str(payload, "seo_title", allow_empty=True)
    seo_description = _require_str(payload, "seo_description", allow_empty=True)
    seo_tags_raw = _require_list(payload, "seo_tags")
    confidence = _require_confidence(payload)
    rationale = _require_str(payload, "rationale", allow_empty=True)

    seo_tags = [
        s.strip().lower()
        for s in seo_tags_raw
        if isinstance(s, str) and s.strip()
    ]
    # Length checks per §28.32 prompt rules — log mismatches but don't
    # reject the response; the editor can re-prompt if Daniel disagrees.
    if seo_title and len(seo_title) > 60:
        _LOG.warning("seo_title > 60 chars for blog #%d (len=%d)", blog_id, len(seo_title))
    if seo_description and not (120 <= len(seo_description) <= 160):
        _LOG.warning(
            "seo_description outside 120-160 for blog #%d (len=%d)",
            blog_id, len(seo_description),
        )

    _blogs.set_seo_metadata(
        conn,
        blog_id,
        seo_title=seo_title or None,
        seo_description=seo_description or None,
        seo_tags=seo_tags or None,
    )

    _audit_log.log(
        conn,
        event_category="data",
        event_type="blog_agent_seo_metadata",
        target_type="blog",
        target_id=blog_id,
        details={
            "confidence_label": confidence,
            "seo_tag_count": len(seo_tags),
            "tokens_used": in_tok + out_tok,
        },
    )

    return SeoMetadataResult(
        blog_id=blog_id,
        seo_title=seo_title,
        seo_description=seo_description,
        seo_tags=tuple(seo_tags),
        confidence_label=confidence,
        rationale=rationale,
        tokens_used=in_tok + out_tok,
    )
