"""Automatic post classification for imported X activity.

This job turns the old "classify by hand" queue into an agentic/automatic
maintenance action. It deliberately writes through the existing forms layer so
validation and duplicate semantics stay identical to manual classification.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.forms.classify import submit_classification

_STIR_TERMS = re.compile(
    r"\b(stir|dinner|cook|cooking|kitchen|meal|recipe|grocery|parent|home cook)\b",
    re.IGNORECASE,
)
_BUILD_TERMS = re.compile(
    r"\b(build|ship|startup|founder|product|launch|design|app|tauri|python|agent)\b",
    re.IGNORECASE,
)
_ICP_TERMS = re.compile(
    r"\b(parent|family|kids|dinner|cook|kitchen|meal|weeknight|home cook)\b",
    re.IGNORECASE,
)
_ASK_TERMS = re.compile(r"(\?|\b(reply|try|download|follow|dm|sign up|join|check out)\b)", re.IGNORECASE)


def _classify_text(text: str) -> dict[str, str]:
    normalized = " ".join((text or "").split())
    if _STIR_TERMS.search(normalized):
        pillar = "stir"
    elif _BUILD_TERMS.search(normalized):
        pillar = "build"
    else:
        pillar = "self"

    return {
        "pillar": pillar,
        "audience": "icp" if _ICP_TERMS.search(normalized) else "other",
        "cta": "ask" if _ASK_TERMS.search(normalized) else "none",
    }


def run(conn: sqlite3.Connection, *, limit: int = 50) -> dict[str, Any]:
    """Classify the newest untagged posts and return a UI-ready summary."""
    rows = conn.execute(
        """
        SELECT p.id, p.text, p.posted_via
          FROM posts p
          LEFT JOIN post_classifications c ON c.post_id = p.id
         WHERE c.id IS NULL
           AND p.posted_via IN ('api', 'xurl', 'imported')
         ORDER BY p.created_at_utc DESC, p.id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()

    classified: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row in rows:
        classification = _classify_text(row["text"] or "")
        payload: dict[str, Any] = {
            "post_id": int(row["id"]),
            **classification,
            "quality_score": None,
            "why_posted": "Automatic classification from Agent Ops.",
            "hypothesis": "Imported X activity should enter the learning loop without manual tagging.",
            "expected_signal": "Lane-level performance becomes visible after metric refresh.",
        }
        try:
            classification_id = submit_classification(conn, payload)
        except Exception as exc:  # noqa: BLE001
            errors.append({"post_id": int(row["id"]), "error": f"{type(exc).__name__}: {exc}"})
            continue
        classified.append({"post_id": int(row["id"]), "classification_id": classification_id, **classification})

    return {
        "ok": not errors,
        "considered": len(rows),
        "classified_count": len(classified),
        "classified": classified,
        "errors": errors,
    }
