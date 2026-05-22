"""Structured niche definition (§28.16) + §28.2 rule #15 orchestrator gate.

Daniel's niche is a (problem, person) pair stored as two settings rows:

  * ``niche_problem`` — one sentence: the problem you solve.
  * ``niche_person``  — one sentence: the person you solve it for.

Why this lives at module scope (not in ``session.py``): the niche check
is structurally orthogonal to IWH/lint. IWH measures draft quality; the
niche gate measures whether the agent has any business drafting at all.
The dispatcher runs the niche gate BEFORE ``decide_save_or_revise`` so
no IWH/lint code runs when there's no niche to draft against.

Rule #15 is intentionally enforced HERE (Python, orchestrator-owned),
NOT in the agent's system prompt. A prompt-injected reply target asking
the agent to "skip the niche check" cannot bypass this gate — the gate
runs on every ``save_draft_*`` regardless of what's in the assistant
text. The system prompt also describes the rule, but the prompt is
documentation; the gate is enforcement.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.db import transaction

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
NICHE_ALIGNMENT_PROMPT_PATH: Path = PROJECT_ROOT / "config" / "niche_alignment_prompt.md"

DEFAULT_ALIGNMENT_MODEL: str = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class NicheDefinition:
    """The two load-bearing settings rows from §28.16."""

    problem: str
    person: str

    def is_defined(self) -> bool:
        return bool(self.problem.strip()) and bool(self.person.strip())


CANONICAL_REFUSAL: str = (
    "niche must be defined before drafting — open Settings → Growth Agent "
    "→ Niche and fill in both `niche_problem` (the problem you solve) and "
    "`niche_person` (the person you solve it for). The orchestrator refuses "
    "every save_draft_* call until both are set (§28.2 rule #15)."
)


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------
def _read_setting_str(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return ""
    try:
        val = json.loads(row["value_json"])
    except (json.JSONDecodeError, TypeError):
        return ""
    if val is None:
        return ""
    return str(val)


def get_niche(conn: sqlite3.Connection) -> NicheDefinition:
    """Return the active niche definition (possibly empty)."""
    return NicheDefinition(
        problem=_read_setting_str(conn, "niche_problem"),
        person=_read_setting_str(conn, "niche_person"),
    )


def is_niche_defined(conn: sqlite3.Connection) -> bool:
    return get_niche(conn).is_defined()


# ---------------------------------------------------------------------------
# Writes (Settings panel — Daniel-only path).
# ---------------------------------------------------------------------------
def set_niche(
    conn: sqlite3.Connection,
    *,
    problem: str,
    person: str,
) -> NicheDefinition:
    """Upsert both settings rows. Returns the canonicalized result.

    Whitespace is stripped on write — leading/trailing spaces would
    bypass the empty-string check elsewhere.
    """
    p_clean = problem.strip()
    pn_clean = person.strip()
    # P59A-W2: BEGIN IMMEDIATE wrapper. Both UPSERTs must land together;
    # otherwise a mid-flight error leaves Daniel with half-defined niche
    # (rule #15 then refuses with a confusing "set both" while the UI
    # shows the half-saved state as if intentional).
    # P59A-W13: also update `note` on conflict so the Python writer's
    # note text wins consistently — otherwise migration-seeded text
    # drifts from in-app-edited text based on order of operations.
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO settings (key, value_json, note)
            VALUES ('niche_problem', ?, 'One-sentence: the problem you solve. Empty BLOCKS agent drafting (§28.2 rule #15).')
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                note = excluded.note
            """,
            (json.dumps(p_clean),),
        )
        conn.execute(
            """
            INSERT INTO settings (key, value_json, note)
            VALUES ('niche_person', ?, 'One-sentence: the person you solve it for. Empty BLOCKS agent drafting (§28.2 rule #15).')
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                note = excluded.note
            """,
            (json.dumps(pn_clean),),
        )
    return NicheDefinition(problem=p_clean, person=pn_clean)


# ---------------------------------------------------------------------------
# §28.16 "Test against bio" Haiku affordance.
# ---------------------------------------------------------------------------
AlignmentCaller = Callable[[str, str, str], tuple[str, int, int]]
"""Signature: (system_prompt, user_message, model) -> (text, input_tokens, output_tokens)."""


class NicheAlignmentError(RuntimeError):
    """Raised when critique_alignment cannot produce structured output.

    Distinct from a network/API error so the Settings panel can render
    `no API key / model returned junk / API unavailable` separately.
    """


@dataclass(frozen=True)
class AlignmentCritique:
    """Structured output from the §28.16 'Test against bio' affordance.

    Mirrors the JSON the Haiku prompt is asked to return:
        {"aligned": bool, "gaps": [str], "suggestions": [str]}
    """

    aligned: bool
    gaps: list[str]
    suggestions: list[str]
    tokens_used: int = 0


# P59A-S5: memoize the Anthropic client per api_key so repeated
# "Test against bio" presses don't pile up HTTP clients + connection
# pools. Keyed on api_key so a key rotation produces a fresh client.
_CLIENT_CACHE: dict[str, Any] = {}


def _get_anthropic_client(api_key: str) -> Any:
    if api_key not in _CLIENT_CACHE:
        import anthropic  # local import — keeps the cold path free of the dep
        _CLIENT_CACHE[api_key] = anthropic.Anthropic(api_key=api_key)
    return _CLIENT_CACHE[api_key]


def _default_alignment_caller(
    system_prompt: str, user_message: str, model: str
) -> tuple[str, int, int]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise NicheAlignmentError(
            "ANTHROPIC_API_KEY is not set. See spec §28.8 for env setup."
        )
    client = _get_anthropic_client(api_key)
    # P59A-W4: catch any SDK / httpx exception and re-raise as
    # NicheAlignmentError so the Settings panel (which only catches
    # NicheAlignmentError) doesn't dump a full Streamlit traceback —
    # the traceback would include the request body and leak Daniel's
    # bio + niche to the UI. Do NOT include str(exc) here in case the
    # SDK echoes the payload in its repr.
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise NicheAlignmentError(
            f"alignment call failed: {type(exc).__name__}"
        ) from exc
    text_parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    in_tok = int(getattr(resp.usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(resp.usage, "output_tokens", 0) or 0)
    return ("".join(text_parts), in_tok, out_tok)


def _strip_code_fence(text: str) -> str:
    """Tolerate Haiku occasionally wrapping JSON in ```json … ``` fences.

    P59A-W5+S7: single-regex form matching ``lint.py:_HAIKU_LINT_PROMPT``
    parser. Removes the prior multi-step lstrip/rstrip/slice chain whose
    single-line ```json{...}``` edge case was brittle (could lop the
    leading `{`). The regex matches optional leading ```[json] and
    optional trailing ``` in one pass.
    """
    return re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text.strip(),
        flags=re.DOTALL,
    ).strip()


def _read_alignment_prompt() -> str:
    if not NICHE_ALIGNMENT_PROMPT_PATH.exists():
        raise NicheAlignmentError(
            f"Alignment prompt missing at {NICHE_ALIGNMENT_PROMPT_PATH}. "
            "Phase 5.9 install incomplete."
        )
    return NICHE_ALIGNMENT_PROMPT_PATH.read_text(encoding="utf-8")


def critique_alignment(
    *,
    bio_text: str,
    niche: NicheDefinition,
    model: str = DEFAULT_ALIGNMENT_MODEL,
    model_caller: AlignmentCaller | None = None,
) -> AlignmentCritique:
    """Read-only critique. Never edits the bio itself.

    Returns ``AlignmentCritique`` parsed from the Haiku response. Raises
    ``NicheAlignmentError`` if the model returns non-JSON or a malformed
    payload — the Settings UI surfaces that as an inline banner.
    """
    if not niche.is_defined():
        raise NicheAlignmentError(
            "Cannot critique alignment: niche is not defined. Save both "
            "niche_problem and niche_person first."
        )
    if not bio_text.strip():
        raise NicheAlignmentError("bio_text is empty")

    system_prompt = _read_alignment_prompt()
    user_message = (
        f"Niche problem: {niche.problem}\n"
        f"Niche person: {niche.person}\n\n"
        f"X bio:\n{bio_text.strip()}\n\n"
        "Return ONLY the JSON object specified in your system prompt — "
        "no prose wrapper, no code fence."
    )
    caller = model_caller or _default_alignment_caller
    raw_text, in_tok, out_tok = caller(system_prompt, user_message, model)
    cleaned = _strip_code_fence(raw_text)
    try:
        payload: Any = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise NicheAlignmentError(
            f"Model returned non-JSON: {exc.msg} at char {exc.pos}. "
            f"First 200 chars: {cleaned[:200]!r}"
        ) from exc

    if not isinstance(payload, dict):
        raise NicheAlignmentError(
            f"Expected JSON object, got {type(payload).__name__}"
        )
    if "aligned" not in payload or not isinstance(payload["aligned"], bool):
        raise NicheAlignmentError("Missing or non-boolean `aligned` field")
    for list_key in ("gaps", "suggestions"):
        if list_key not in payload or not isinstance(payload[list_key], list):
            raise NicheAlignmentError(f"Missing or non-list `{list_key}` field")
        for item in payload[list_key]:
            if not isinstance(item, str):
                raise NicheAlignmentError(
                    f"`{list_key}` items must be strings; got "
                    f"{type(item).__name__}"
                )

    return AlignmentCritique(
        aligned=bool(payload["aligned"]),
        gaps=[str(x) for x in payload["gaps"]],
        suggestions=[str(x) for x in payload["suggestions"]],
        tokens_used=int(in_tok + out_tok),
    )


__all__ = [
    "AlignmentCritique",
    "CANONICAL_REFUSAL",
    "NicheAlignmentError",
    "NicheDefinition",
    "critique_alignment",
    "get_niche",
    "is_niche_defined",
    "set_niche",
]
