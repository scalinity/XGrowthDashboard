"""Settings and secret allowlists for the FastAPI sidecar (§31.5, §14.7).

Every settings write must pass schema validation before persistence. Secret names
are strictly allowlisted and secret values are never returned in API responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

MANAGED_SECRETS: frozenset[str] = frozenset({"ANTHROPIC_API_KEY"})

# Explicit enum constraints for settings whose allowed values are fixed.
_SETTING_ENUMS: dict[str, frozenset[str]] = {
    "data_collection_mode": frozenset({"manual", "api"}),
    "calendar_default_view": frozenset({"week", "two_weeks", "month"}),
}

# Keys that may be stored as JSON null.
_NULLABLE_KEYS: frozenset[str] = frozenset({"x_user_id"})

# Expected Python types keyed by settings key (derived from seed defaults).
_SETTING_TYPES: dict[str, type] = {
    "x_handle": str,
    "x_user_id": str,
    "profile_url": str,
    "baseline_followers": int,
    "operational_ceiling": int,
    "long_arc_reminder": int,
    "current_milestone": int,
    "timezone": str,
    "daily_snapshot_time": str,
    "daily_post_target": int,
    "daily_reply_target": int,
    "daily_reply_session_target": int,
    "target_calibration_review_date": str,
    "weekly_report_export_path": str,
    "data_collection_mode": str,
    "publish_via_api_enabled": bool,
    "x_write_rate_limit_per_15min": int,
    "x_write_rate_limit_per_24h": int,
    "lane_sample_size_insufficient": int,
    "lane_sample_size_low": int,
    "lane_sample_size_stronger": int,
    "lane_days_covered_minimum": int,
    "velocity_7d_display_threshold": int,
    "backup_dir": str,
    "export_dir": str,
    "counterfactual_required": bool,
    "agent_default_model": str,
    "agent_monthly_cost_cap_usd": float,
    "agent_voice_sample_count": int,
    "iwh_self_score_minimum": int,
    "iwh_max_revision_attempts": int,
    "x_posting_confirmation_token_ttl_seconds": int,
    "niche_problem": str,
    "niche_person": str,
    "reply_quality_lint_enabled": bool,
    "personality_lore_overuse_threshold": int,
    "content_type_recommendation_window_days": int,
    "velocity_projection_noise_floor_followers": int,
    "personality_lore_splice_count": int,
    "coach_refuse_without_evidence": bool,
    "coach_citation_strip_log_threshold": int,
    "brain_dump_max_candidate_drafts": int,
    "profile_audit_recent_posts_window_days": int,
    "profile_audit_cadence_reminder_days": int,
    "inspiration_plagiarism_jaccard_high_threshold": float,
    "inspiration_plagiarism_jaccard_medium_threshold": float,
    "inspiration_plagiarism_ngram_high_threshold": int,
    "inspiration_plagiarism_ngram_medium_threshold": int,
    "monthly_review_auto_draft_enabled": bool,
    "audit_log_retention_days": int,
    "calendar_default_view": str,
    "calendar_am_cutoff_hour": int,
    "blog_stale_status_warning_days": int,
    "blog_default_target_length_words": int,
    "blog_export_default_directory": str,
    "blog_repurposing_plagiarism_check_enabled": bool,
    "blog_agent_max_draft_iterations": int,
    "screenshot_test_minimum_for_strong": int,
    "reply_intent_required": bool,
}

# Non-negative integer settings.
_NON_NEGATIVE_INT_KEYS: frozenset[str] = frozenset(
    key
    for key, typ in _SETTING_TYPES.items()
    if typ is int and key not in {"velocity_7d_display_threshold"}
)


@dataclass(frozen=True)
class SettingsValidationError(Exception):
    detail: str


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def validate_setting_value(key: str, value: Any, *, current_value: Any | None = None) -> None:
    """Validate a settings write. Raises SettingsValidationError on failure."""
    if key in _NULLABLE_KEYS and value is None:
        return

    expected = _SETTING_TYPES.get(key)
    if expected is None and current_value is not None:
        expected = type(current_value)

    if expected is bool:
        if not isinstance(value, bool):
            raise SettingsValidationError(
                f"Setting {key!r} requires a boolean value, got {_type_name(value)}"
            )
    elif expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsValidationError(
                f"Setting {key!r} requires an integer value, got {_type_name(value)}"
            )
        if key in _NON_NEGATIVE_INT_KEYS and value < 0:
            raise SettingsValidationError(
                f"Setting {key!r} must be >= 0, got {value}"
            )
    elif expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError(
                f"Setting {key!r} requires a numeric value, got {_type_name(value)}"
            )
        if key.endswith("_threshold") and float(value) < 0:
            raise SettingsValidationError(
                f"Setting {key!r} must be >= 0, got {value}"
            )
    elif expected is str:
        if not isinstance(value, str):
            raise SettingsValidationError(
                f"Setting {key!r} requires a string value, got {_type_name(value)}"
            )
    elif expected is None:
        raise SettingsValidationError(f"Unknown setting key: {key!r}")
    else:
        if not isinstance(value, expected):
            raise SettingsValidationError(
                f"Setting {key!r} requires {_type_name(expected)}, got {_type_name(value)}"
            )

    enum = _SETTING_ENUMS.get(key)
    if enum is not None and value not in enum:
        allowed = ", ".join(sorted(enum))
        raise SettingsValidationError(
            f"Setting {key!r} must be one of: {allowed}"
        )


def assert_known_setting_key(key: str, known_keys: set[str]) -> None:
    if key not in known_keys:
        raise HTTPException(status_code=400, detail=f"Unknown setting key: {key!r}")


def assert_valid_setting_value(
    key: str, value: Any, *, current_value: Any | None = None
) -> None:
    try:
        validate_setting_value(key, value, current_value=current_value)
    except SettingsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
        ) from exc


def assert_known_secret_name(name: str) -> None:
    if name not in MANAGED_SECRETS:
        raise HTTPException(status_code=400, detail=f"Unknown secret: {name!r}")
