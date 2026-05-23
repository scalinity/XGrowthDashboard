"""Shared infrastructure for blog-pipeline agent modules.

Both ``app/agent/blog_drafting.py`` and ``app/agent/blog_repurposing.py``
have the same scaffolding: a default Anthropic caller with explicit
timeout, an identity-context renderer that splices niche + voice
profile + voice samples + active personality lore into the user
message, a structured-JSON response parser, and a confidence-label
validator.

P6R-18 (review fix from /review-2 on Phase 6) consolidates these
helpers here so the drift between the drafting and repurposing
variants — different sample limits, different inclusion of voice
cadence / vocab signatures / stop phrases — happens through explicit
parameters rather than implicit code drift.

Why module-private with a leading underscore: this is implementation
detail for the blog-pipeline agent modules, not a stable cross-app
API. The agent modules that ship in later phases should NOT import
from this module directly — they should grow their own helpers or
explicitly opt into this module via PR review.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Literal

from app.agent import niche as _niche
from app.agent import personality_lore as _personality_lore
from app.agent import voice as _voice
from app.agent import voice_profile as _voice_profile
from app.agent.untrusted_wrap import strip_code_fence as _strip_code_fence


# ---------------------------------------------------------------------------
# Shared types.
# ---------------------------------------------------------------------------
ConfidenceLabel = Literal["fact", "inference", "speculation", "mixed"]
VALID_CONFIDENCE_LABELS: frozenset[str] = frozenset(
    {"fact", "inference", "speculation", "mixed"}
)

# (system_prompt, user_message, model) -> (response_text, in_tokens, out_tokens)
ModelCaller = Callable[[str, str, str], tuple[str, int, int]]


# ---------------------------------------------------------------------------
# Default Anthropic caller.
# ---------------------------------------------------------------------------
def make_default_caller(
    *,
    api_key_missing_exc: type[Exception],
    max_tokens: int,
    timeout_seconds: float,
) -> ModelCaller:
    """Construct a default Anthropic ModelCaller bound to a per-module
    exception type, ``max_tokens`` cap, and explicit timeout.

    Per-module exception types let the caller surface a typed failure
    that the orchestrator (or tool adapter) can match on; the
    surrounding error path is the only reason this can't be a single
    flat function across modules.
    """
    def _caller(
        system_prompt: str, user_message: str, model: str
    ) -> tuple[str, int, int]:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise api_key_missing_exc(
                "ANTHROPIC_API_KEY is not set. See spec §28.8 for env setup."
            )
        import anthropic
        # Explicit timeout matches the timeout discipline applied across
        # other agent caller sites (P511R-20). The SDK's default is 10
        # minutes, which would block the Streamlit thread for that
        # entire window on a hung network call.
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
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
    return _caller


# ---------------------------------------------------------------------------
# Identity context renderer.
# ---------------------------------------------------------------------------
def render_identity_context(
    conn,
    *,
    sample_limit: int = 6,
    include_voice_structural: bool = True,
) -> str:
    """Render the niche × voice × samples × lore identity block.

    Lives in the USER message rather than the system prompt so each
    tool call gets a fresh read — the editor's identity readout binds
    to the same source-of-truth, so a voice-profile regen in Settings
    is visible on the next agent call without restart.

    Parameters mirror the drift between drafting and repurposing:
    drafting wants 6 samples + voice cadence/vocab/stops;
    repurposing wants 4 samples without the structural voice block.
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
        parts.append("(niche is not defined — refuse and ask Daniel to fill it in)")
    parts.append("")

    if profile is not None:
        desc = profile.self_description()
        if desc:
            parts.append(f"_Voice self-description:_ {desc}")
        if include_voice_structural:
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
                parts.append(
                    "_Vocabulary signatures:_ "
                    + ", ".join(f"`{v}`" for v in vocab)
                )
            if stops:
                parts.append(
                    "_Stop phrases (avoid):_ "
                    + ", ".join(f"`{s}`" for s in stops)
                )
        parts.append("")
    else:
        parts.append("(no active voice profile — operate from niche + samples alone)")
        parts.append("")

    if samples:
        parts.append("### Voice samples")
        for s in samples[:sample_limit]:
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


# ---------------------------------------------------------------------------
# Structured-JSON response parsing.
# ---------------------------------------------------------------------------
def parse_json_response(text: str, *, model_error_exc: type[Exception]) -> dict[str, Any]:
    """Parse a model response as JSON. Tolerates a leading/trailing
    code fence. Raises ``model_error_exc`` on bad shape.

    Per-module exception types are passed in so the orchestrator can
    distinguish "drafting failed because the model returned bad JSON"
    from "repurposing failed because the model returned bad JSON".
    """
    cleaned = _strip_code_fence(text).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise model_error_exc(f"model returned non-JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise model_error_exc("model returned non-object JSON")
    return payload


def require_confidence(
    payload: dict[str, Any],
    key: str = "confidence_label",
    *,
    model_error_exc: type[Exception],
) -> ConfidenceLabel:
    v = payload.get(key)
    if v not in VALID_CONFIDENCE_LABELS:
        raise model_error_exc(
            f"{key} must be one of {sorted(VALID_CONFIDENCE_LABELS)}; got {v!r}"
        )
    return v  # type: ignore[return-value]
