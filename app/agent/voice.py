"""Voice sample utilities (§28.5).

Voice samples anchor the agent's drafting voice. The top-N active samples
(default 5, configurable via ``settings.agent_voice_sample_count``) are
injected into the system prompt's Voice Samples section at conversation
start.

Daniel adds samples via the Settings page or per-post "Mark as voice
sample" button (Content Performance view). Samples are rotated by
priority; ``last_used_at_utc`` lets him see if one sample is being
over-relied on so he can deprioritize manually.

This module is pure DB helpers. The Settings page wires the CRUD UI.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


DEFAULT_VOICE_SAMPLE_COUNT: int = 5


@dataclass(frozen=True)
class VoiceSample:
    id: int
    text: str
    context_note: str | None
    pillar: str | None
    priority: int


def get_voice_sample_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'agent_voice_sample_count'"
    ).fetchone()
    if row is None:
        return DEFAULT_VOICE_SAMPLE_COUNT
    try:
        return int(json.loads(row["value_json"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return DEFAULT_VOICE_SAMPLE_COUNT


def get_active_voice_samples(
    conn: sqlite3.Connection, *, limit: int | None = None
) -> list[VoiceSample]:
    """Return up to ``limit`` active samples ordered by priority asc.

    Lower priority value = higher priority (matches §10.2: "lower = earlier").
    """
    effective_limit = limit if limit is not None else get_voice_sample_count(conn)
    rows = conn.execute(
        """
        SELECT id, text, context_note, pillar, priority
        FROM voice_samples
        WHERE is_active = 1
        ORDER BY priority ASC, id ASC
        LIMIT ?
        """,
        (int(effective_limit),),
    ).fetchall()
    return [
        VoiceSample(
            id=int(r["id"]),
            text=r["text"],
            context_note=r["context_note"],
            pillar=r["pillar"],
            priority=int(r["priority"]),
        )
        for r in rows
    ]


def add_voice_sample(
    conn: sqlite3.Connection,
    *,
    text: str,
    pillar: str | None = None,
    context_note: str | None = None,
    post_id: int | None = None,
    priority: int = 5,
    is_active: bool = True,
) -> int:
    """Insert a new voice sample. Returns the new row id."""
    cur = conn.execute(
        """
        INSERT INTO voice_samples (post_id, text, context_note, pillar,
                                   is_active, priority)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            post_id,
            text,
            context_note,
            pillar,
            1 if is_active else 0,
            int(priority),
        ),
    )
    return int(cur.lastrowid)


def deactivate_voice_sample(conn: sqlite3.Connection, *, sample_id: int) -> None:
    conn.execute(
        "UPDATE voice_samples SET is_active = 0 WHERE id = ?",
        (int(sample_id),),
    )


def touch_last_used_at(
    conn: sqlite3.Connection, *, sample_ids: list[int]
) -> None:
    """Mark samples as used at ``now`` so Daniel can spot overuse."""
    if not sample_ids:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    qmarks = ",".join(["?"] * len(sample_ids))
    conn.execute(
        f"UPDATE voice_samples SET last_used_at_utc = ? WHERE id IN ({qmarks})",
        (now, *sample_ids),
    )
