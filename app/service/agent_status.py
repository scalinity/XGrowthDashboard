"""Agent mode and capability payloads for native UI (§28)."""

from __future__ import annotations

import os
import shutil
import sqlite3
from typing import Any

from app.forms import get_setting
from app.secret_store import resolve_secret
from app.service.settings_schema import MANAGED_SECRETS


def build_agent_mode(conn: sqlite3.Connection) -> dict[str, Any]:
    mode = str(get_setting(conn, "data_collection_mode", "api") or "api").lower()
    niche_problem = str(get_setting(conn, "niche_problem", "") or "").strip()
    niche_person = str(get_setting(conn, "niche_person", "") or "").strip()
    secrets = {
        name: {"configured": bool(resolve_secret(name))}
        for name in sorted(MANAGED_SECRETS)
    }
    return {
        "data_collection_mode": mode,
        "api_read": mode == "api",
        "publish_mode": "api" if bool(get_setting(conn, "publish_via_api_enabled", True)) else "manual",
        "niche_gate": {
            "blocked": not (niche_problem and niche_person),
            "niche_problem_set": bool(niche_problem),
            "niche_person_set": bool(niche_person),
        },
        "lint_gate": {
            "reply_quality_lint_enabled": bool(
                get_setting(conn, "reply_quality_lint_enabled", True)
            ),
            "reply_intent_required": bool(get_setting(conn, "reply_intent_required", True)),
        },
        "secret_state": secrets,
        "tool_permissions": {
            "read_dashboard": True,
            "read_x_api": mode == "api",
            "write_drafts": True,
            "publish": False,
            "secrets": False,
        },
    }


def build_capabilities(conn: sqlite3.Connection) -> dict[str, Any]:  # noqa: ARG001
    anthropic = bool(resolve_secret("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    xai = bool(resolve_secret("XAI_API_KEY") or os.environ.get("XAI_API_KEY"))
    xurl = shutil.which("xurl") is not None
    return {
        "anthropic": {"available": anthropic, "label": "Growth Agent drafting"},
        "voyage": {
            "available": bool(
                resolve_secret("VOYAGE_API_KEY") or os.environ.get("VOYAGE_API_KEY")
            ),
            "label": "Embeddings / repetition guard",
        },
        "x_api": {
            "available": xurl and str(get_setting(conn, "data_collection_mode", "api")) == "api",
            "label": "X API reads",
        },
        "grok": {"available": xai, "label": "Grok discovery"},
        "xurl": {"available": xurl, "label": "X API CLI"},
        "keychain": {"available": True, "label": "Native secret storage"},
    }
