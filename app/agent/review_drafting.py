"""Model-backed weekly and monthly review section drafting (§28.4, §28.27)."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from typing import Any

from app.agent import monthly_review as _monthly_review
from app.agent import prompt_builder
from app.forms import get_setting

ReviewModelCaller = Callable[[str, str, str], tuple[str, int, int]]

_WEEKLY_SECTIONS = {
    "interpretation",
    "lesson",
    "counterfactual",
    "next_week_experiment",
}
_MONTHLY_SECTIONS = {
    "interpretation",
    "lesson",
    "counterfactual",
    "next_month_experiment",
    "campaigns_retro",
}


def _default_model_caller(system_prompt: str, user_message: str, model: str) -> tuple[str, int, int]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Anthropic API key not configured. Set ANTHROPIC_API_KEY in Settings → API keys."
        )
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=90.0)
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    parts = [
        getattr(block, "text", "")
        for block in resp.content
        if getattr(block, "type", None) == "text"
    ]
    in_tok = int(getattr(resp.usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(resp.usage, "output_tokens", 0) or 0)
    return ("".join(parts).strip(), in_tok, out_tok)


def _model_name(conn: sqlite3.Connection) -> str:
    return str(get_setting(conn, "agent_default_model", "claude-sonnet-4-20250514"))


def _weekly_context(conn: sqlite3.Connection, week_id: int) -> dict[str, Any] | dict[str, str]:
    row = conn.execute(
        "SELECT * FROM weekly_reviews WHERE id = ?",
        (int(week_id),),
    ).fetchone()
    if row is None:
        return {"error": f"weekly review id {week_id} not found"}
    return dict(row)


def _build_weekly_prompt(section_name: str, context: dict[str, Any]) -> str:
    return (
        f"Draft the weekly review section `{section_name}` for week starting "
        f"{context.get('week_start_date')}. Use dashboard facts only. "
        f"Include a confidence tag line. Context JSON:\n"
        f"{json.dumps(context, default=str)}"
    )


def draft_weekly_review_section(
    conn: sqlite3.Connection,
    *,
    section_name: str,
    week_id: int,
    model_caller: ReviewModelCaller | None = None,
) -> dict[str, Any]:
    if section_name not in _WEEKLY_SECTIONS:
        return {"error": f"unknown section_name {section_name!r}"}
    context = _weekly_context(conn, week_id)
    if "error" in context:
        return context
    caller = model_caller or _default_model_caller
    try:
        system_prompt = prompt_builder.build_system_prompt(conn)
        draft_text, in_tok, out_tok = caller(
            system_prompt,
            _build_weekly_prompt(section_name, context),
            _model_name(conn),
        )
    except RuntimeError as exc:
        return {
            "section_name": section_name,
            "week_id": int(week_id),
            "draft_text": None,
            "status": "degraded",
            "error": str(exc),
            "manual_fallback": "Write this section manually in Weekly Review.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "section_name": section_name,
            "week_id": int(week_id),
            "draft_text": None,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "section_name": section_name,
        "week_id": int(week_id),
        "draft_text": draft_text,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "status": "success",
    }


def draft_monthly_review_section(
    conn: sqlite3.Connection,
    *,
    section_name: str,
    iso_month: str,
    model_caller: ReviewModelCaller | None = None,
) -> dict[str, Any]:
    if section_name not in _MONTHLY_SECTIONS:
        return {"error": f"unknown section_name {section_name!r}"}
    try:
        _monthly_review.parse_iso_month(iso_month)
    except _monthly_review.InvalidIsoMonthError as exc:
        return {"error": str(exc)}
    auto_filled = _monthly_review.compute_auto_filled_fields(conn, iso_month)
    context = {
        "iso_month": iso_month,
        "auto_filled": {
            "follower_delta": auto_filled.follower_delta,
            "posts_shipped": auto_filled.posts_shipped,
            "downloads": auto_filled.downloads,
            "strongest_pillar_candidate": auto_filled.strongest_pillar_candidate,
            "strongest_content_type": auto_filled.strongest_content_type,
            "weakest_content_type": auto_filled.weakest_content_type,
            "campaigns_completed_json": auto_filled.campaigns_completed_json,
        },
    }
    caller = model_caller or _default_model_caller
    try:
        system_prompt = prompt_builder.build_system_prompt(conn)
        user_prompt = (
            f"Draft the monthly review section `{section_name}` for {iso_month}. "
            f"Use dashboard facts only and include a confidence tag line. "
            f"Context JSON:\n{json.dumps(context, default=str)}"
        )
        draft_text, in_tok, out_tok = caller(
            system_prompt,
            user_prompt,
            _model_name(conn),
        )
    except RuntimeError as exc:
        return {
            "section_name": section_name,
            "iso_month": iso_month,
            "draft_text": None,
            "auto_filled": context["auto_filled"],
            "status": "degraded",
            "error": str(exc),
            "manual_fallback": "Write this section manually in Weekly Review → Monthly tab.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "section_name": section_name,
            "iso_month": iso_month,
            "draft_text": None,
            "auto_filled": context["auto_filled"],
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "section_name": section_name,
        "iso_month": iso_month,
        "draft_text": draft_text,
        "auto_filled": context["auto_filled"],
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "status": "success",
    }
