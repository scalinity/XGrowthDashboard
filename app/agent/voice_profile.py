"""Generated voice profile (§28.12) — Haiku-synthesized fingerprint of
Daniel's writing voice, spliced into the system prompt alongside the
hand-picked `voice_samples`.

This module is the backend half. The Settings → Growth Agent → Voice
Profile panel (app/pages/7_Settings.py) wires the regenerate button.

Read-scope discipline (§28.2, §28.12):

  * Reads `posts` only. NEVER touches `stir_testers`,
    `stir_conversion_events.qualitative_feedback`, or `agent_messages`.
  * The Haiku prompt enumerates the read scope explicitly (see
    `config/voice_profile_prompt.md`); the prompt itself is the
    documentation of what the small model sees.

Atomic activation: a fresh profile and the deactivation of the prior
active row land in a single transaction. There is never a moment when
0 or 2 rows are active.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
VOICE_PROFILE_PROMPT_PATH: Path = PROJECT_ROOT / "config" / "voice_profile_prompt.md"

DEFAULT_WINDOW_DAYS: int = 90
DEFAULT_MIN_SOURCE_POSTS: int = 10
DEFAULT_MODEL: str = "claude-haiku-4-5-20251001"

# Top-level keys the model must return — validated on every generation.
_REQUIRED_PROFILE_KEYS: tuple[str, ...] = (
    "hook_patterns",
    "cadence",
    "vocabulary_signatures",
    "tone_markers",
    "stop_phrases",
    "self_description",
)


class VoiceProfileGenerationError(RuntimeError):
    """Raised when generation cannot produce a saveable profile.

    Distinct from a network/API error so the UI can render
    `not enough posts / model returned junk / API unavailable` as
    distinct banners.
    """


@dataclass(frozen=True)
class VoiceProfile:
    id: int
    generated_at_utc: str
    is_active: bool
    source_post_window_days: int
    source_post_count: int
    profile_json: dict
    model_used: str
    tokens_used: int
    superseded_by_profile_id: int | None

    def self_description(self) -> str:
        return str(self.profile_json.get("self_description") or "").strip()

    def cadence(self) -> dict:
        val = self.profile_json.get("cadence")
        return val if isinstance(val, dict) else {}

    def vocabulary_signatures(self) -> list[str]:
        val = self.profile_json.get("vocabulary_signatures")
        return [str(x) for x in val] if isinstance(val, list) else []

    def stop_phrases(self) -> list[str]:
        val = self.profile_json.get("stop_phrases")
        return [str(x) for x in val] if isinstance(val, list) else []


# ---------------------------------------------------------------------------
# Settings helpers.
# ---------------------------------------------------------------------------
def _get_int_setting(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return int(json.loads(row["value_json"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return default


def get_window_days(conn: sqlite3.Connection) -> int:
    return _get_int_setting(conn, "voice_profile_window_days", DEFAULT_WINDOW_DAYS)


def get_min_source_posts(conn: sqlite3.Connection) -> int:
    return _get_int_setting(conn, "voice_profile_min_source_posts", DEFAULT_MIN_SOURCE_POSTS)


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------
def get_active(conn: sqlite3.Connection) -> VoiceProfile | None:
    """Return the single active profile, or None if no row is marked active."""
    row = conn.execute(
        """
        SELECT id, generated_at_utc, is_active, source_post_window_days,
               source_post_count, profile_json, model_used, tokens_used,
               superseded_by_profile_id
        FROM voice_profiles
        WHERE is_active = 1
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return _row_to_profile(row)


def list_all(conn: sqlite3.Connection, *, limit: int = 20) -> list[VoiceProfile]:
    rows = conn.execute(
        """
        SELECT id, generated_at_utc, is_active, source_post_window_days,
               source_post_count, profile_json, model_used, tokens_used,
               superseded_by_profile_id
        FROM voice_profiles
        ORDER BY generated_at_utc DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [_row_to_profile(r) for r in rows]


def _row_to_profile(row: sqlite3.Row) -> VoiceProfile:
    try:
        profile_json = json.loads(row["profile_json"]) if row["profile_json"] else {}
    except json.JSONDecodeError:
        profile_json = {}
    return VoiceProfile(
        id=int(row["id"]),
        generated_at_utc=str(row["generated_at_utc"]),
        is_active=bool(row["is_active"]),
        source_post_window_days=int(row["source_post_window_days"]),
        source_post_count=int(row["source_post_count"]),
        profile_json=profile_json,
        model_used=str(row["model_used"]),
        tokens_used=int(row["tokens_used"] or 0),
        superseded_by_profile_id=(
            int(row["superseded_by_profile_id"])
            if row["superseded_by_profile_id"] is not None else None
        ),
    )


# ---------------------------------------------------------------------------
# Source post selection.
# ---------------------------------------------------------------------------
def _select_source_posts(
    conn: sqlite3.Connection, *, window_days: int
) -> list[dict]:
    """Return rows from `posts` that fit the synthesis read scope.

    Read scope is intentionally narrow per §28.12:
      - Only posts Daniel has actually shipped (`x_post_id IS NOT NULL`).
      - Only posts with body text (`text IS NOT NULL AND text != ''`).
      - Only within the lookback window.
    No tester PII, no agent_messages, no qualitative feedback.
    """
    rows = conn.execute(
        """
        SELECT id, text, type, created_date, created_at_utc
        FROM posts
        WHERE x_post_id IS NOT NULL
          AND text IS NOT NULL
          AND text != ''
          AND date(COALESCE(created_at_utc, created_date))
              >= date('now', ?)
        ORDER BY COALESCE(created_at_utc, created_date) DESC
        LIMIT 500
        """,
        (f"-{int(window_days)} days",),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "text": str(r["text"]),
            "type": r["type"],
            "created_date": r["created_date"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------
def validate_profile_json(payload: dict) -> None:
    """Hard schema check. Raises VoiceProfileGenerationError on any violation.

    The schema mirrors §10 `voice_profiles.profile_json`. Lists may be
    empty but must be lists; cadence must be a dict; self_description must
    be a non-empty string.
    """
    if not isinstance(payload, dict):
        raise VoiceProfileGenerationError(
            f"profile_json must be a JSON object, got {type(payload).__name__}"
        )
    missing = [k for k in _REQUIRED_PROFILE_KEYS if k not in payload]
    if missing:
        raise VoiceProfileGenerationError(
            f"profile_json missing required keys: {missing}"
        )
    for list_key in ("hook_patterns", "vocabulary_signatures", "tone_markers", "stop_phrases"):
        val = payload[list_key]
        if not isinstance(val, list):
            raise VoiceProfileGenerationError(
                f"profile_json.{list_key} must be a list, got {type(val).__name__}"
            )
        for i, item in enumerate(val):
            if not isinstance(item, str):
                raise VoiceProfileGenerationError(
                    f"profile_json.{list_key}[{i}] must be a string"
                )
    cadence = payload["cadence"]
    if not isinstance(cadence, dict):
        raise VoiceProfileGenerationError(
            f"profile_json.cadence must be an object, got {type(cadence).__name__}"
        )
    self_desc = payload["self_description"]
    if not isinstance(self_desc, str) or not self_desc.strip():
        raise VoiceProfileGenerationError(
            "profile_json.self_description must be a non-empty string"
        )


# ---------------------------------------------------------------------------
# Generation.
# ---------------------------------------------------------------------------
ModelCaller = Callable[[str, str, str], tuple[str, int, int]]
"""Signature: (system_prompt, user_message, model) -> (text, input_tokens, output_tokens)."""


def _default_model_caller(system_prompt: str, user_message: str, model: str) -> tuple[str, int, int]:
    """Live Anthropic call. Imported lazily so the offline path stays cheap.

    Raises VoiceProfileGenerationError on missing API key (same pattern
    as the rest of `app/agent/`).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise VoiceProfileGenerationError(
            "ANTHROPIC_API_KEY is not set. See spec §28.8 for env setup."
        )
    import anthropic  # local import — keeps the cold path free of the dep
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    # Concatenate every text block in the response (Anthropic returns a list).
    text_parts: list[str] = []
    for block in resp.content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", ""))
    in_tok = int(getattr(resp.usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(resp.usage, "output_tokens", 0) or 0)
    return ("".join(text_parts), in_tok, out_tok)


def _build_user_message(posts: Iterable[dict]) -> str:
    """Format the post sample for the synthesis prompt.

    One post per `<post id="N">…</post>` block. Newlines preserved so the
    model can read cadence accurately.
    """
    blocks: list[str] = []
    for p in posts:
        kind = p.get("type") or "standalone"
        blocks.append(f"<post id=\"{p['id']}\" type=\"{kind}\">\n{p['text']}\n</post>")
    return (
        "Synthesize Daniel's voice profile from the posts below. Return ONLY "
        "the JSON object specified in your system prompt — no prose wrapper, "
        "no code fence.\n\n" + "\n\n".join(blocks)
    )


def _read_synthesis_prompt() -> str:
    if not VOICE_PROFILE_PROMPT_PATH.exists():
        raise VoiceProfileGenerationError(
            f"Synthesis prompt missing at {VOICE_PROFILE_PROMPT_PATH}. "
            "Phase 5.8 install incomplete."
        )
    return VOICE_PROFILE_PROMPT_PATH.read_text(encoding="utf-8")


def _strip_code_fence(text: str) -> str:
    """Tolerate Haiku occasionally wrapping JSON in ```json … ``` fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # drop the first fence line (```json or ```)
        nl = stripped.find("\n")
        if nl != -1:
            stripped = stripped[nl + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def generate(
    conn: sqlite3.Connection,
    *,
    window_days: int | None = None,
    model: str = DEFAULT_MODEL,
    model_caller: ModelCaller | None = None,
) -> VoiceProfile:
    """Generate and activate a new voice profile. Returns the new row.

    Atomic activation: the deactivate-prior + insert-new pair lands in a
    single transaction. On any synthesis failure (bad JSON, schema
    violation, API error), the prior active row is untouched.
    """
    effective_window = int(window_days if window_days is not None else get_window_days(conn))
    min_posts = get_min_source_posts(conn)
    posts = _select_source_posts(conn, window_days=effective_window)
    if len(posts) < min_posts:
        raise VoiceProfileGenerationError(
            f"Not enough posts in the last {effective_window} days "
            f"({len(posts)} found, {min_posts} required). Try a longer "
            f"window or wait until you've shipped more."
        )

    system_prompt = _read_synthesis_prompt()
    user_message = _build_user_message(posts)
    caller = model_caller or _default_model_caller
    raw_text, in_tok, out_tok = caller(system_prompt, user_message, model)
    cleaned = _strip_code_fence(raw_text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise VoiceProfileGenerationError(
            f"Model returned non-JSON: {exc.msg} at char {exc.pos}. "
            f"First 200 chars: {cleaned[:200]!r}"
        ) from exc
    validate_profile_json(payload)

    # Atomic activation. Wrap in BEGIN/COMMIT so a failed insert leaves
    # the prior active row alone.
    prior_id_row = conn.execute(
        "SELECT id FROM voice_profiles WHERE is_active = 1 LIMIT 1"
    ).fetchone()
    prior_id = int(prior_id_row["id"]) if prior_id_row is not None else None

    # SQLite implicit-tx is fine; explicit savepoint is overkill for two writes.
    with conn:
        if prior_id is not None:
            conn.execute(
                "UPDATE voice_profiles SET is_active = 0 WHERE id = ?",
                (prior_id,),
            )
        cur = conn.execute(
            """
            INSERT INTO voice_profiles
              (is_active, source_post_window_days, source_post_count,
               profile_json, model_used, tokens_used)
            VALUES (1, ?, ?, ?, ?, ?)
            """,
            (
                effective_window,
                len(posts),
                json.dumps(payload, ensure_ascii=False),
                model,
                int(in_tok + out_tok),
            ),
        )
        new_id = int(cur.lastrowid)
        if prior_id is not None:
            conn.execute(
                "UPDATE voice_profiles SET superseded_by_profile_id = ? WHERE id = ?",
                (new_id, prior_id),
            )

    new_row = conn.execute(
        """
        SELECT id, generated_at_utc, is_active, source_post_window_days,
               source_post_count, profile_json, model_used, tokens_used,
               superseded_by_profile_id
        FROM voice_profiles WHERE id = ?
        """,
        (new_id,),
    ).fetchone()
    return _row_to_profile(new_row)


# ---------------------------------------------------------------------------
# Diff (Settings UI helper).
# ---------------------------------------------------------------------------
def diff(old_profile_json: dict, new_profile_json: dict) -> dict:
    """Side-by-side diff of two profile JSON payloads.

    Returns a dict with `added`, `removed`, and `changed` keys per
    top-level profile field. Pure function — no DB access — so the
    Settings UI can render the diff without re-fetching.
    """
    out: dict = {"added": {}, "removed": {}, "changed": {}}
    keys = set(old_profile_json) | set(new_profile_json)
    for k in sorted(keys):
        if k not in old_profile_json:
            out["added"][k] = new_profile_json[k]
            continue
        if k not in new_profile_json:
            out["removed"][k] = old_profile_json[k]
            continue
        before = old_profile_json[k]
        after = new_profile_json[k]
        if isinstance(before, list) and isinstance(after, list):
            added_items = [x for x in after if x not in before]
            removed_items = [x for x in before if x not in after]
            if added_items or removed_items:
                out["changed"][k] = {
                    "added": added_items,
                    "removed": removed_items,
                }
        elif before != after:
            out["changed"][k] = {"before": before, "after": after}
    return out
