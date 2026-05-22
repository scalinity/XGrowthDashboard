"""Per-table CSV export column allowlists — single source of truth.

Why this file exists
--------------------

Phase 5 ships an export module that must survive two future expansions
without becoming a fan-out of scattered edits:

- **Phase 5.5 (Growth Agent)** adds publish-flow columns to ``posts``:
  ``agent_draft_id``, ``published_via_agent_message_id``, ``publish_to_x_at``
  (note: spec §10.2 names the publish-timestamp column ``published_to_x_at``;
  the prompt's "publish_to_x_at" appears to be the same column), ``publish_method``,
  ``publish_attempt_count``, and the sensitive ``publish_last_error``.
- **Phase 5.6 (Reply Target Discovery)** adds ``in_reply_to_reply_target_id``
  and ``reply_intent`` to ``posts``.

Per spec §16 (7), some of those new columns are EXCLUDED from the default
CSV export and require an explicit opt-in toggle:

* ``publish_last_error`` — may contain X API diagnostic strings / credential-
  adjacent error text. Default: **excluded** entirely.
* ``published_via_agent_message_id`` — joins to ``agent_messages`` which holds
  agent chat content. Default: **opt-in only**.

The Phase 5.5 / 5.6 prompts in ``spec.md`` §25 explicitly point at this file
as the canonical surface. Both future runs should append to the marked
``# PHASE 5.5 INSERT HERE`` and ``# PHASE 5.6 INSERT HERE`` lines below and
extend the corresponding tests in ``tests/test_exports.py``.

Allowlist, not blocklist
------------------------

Adding a new column to a table does NOT auto-include it in exports. The
exporter reads only the columns in ``default_columns`` (or
``default_columns + opt_in_columns`` when the caller passes
``include_opt_in=True``). ``excluded_columns`` is documentary — a paper
trail of columns whose absence is intentional, surfaced in code review.

The column order in ``default_columns`` is the CSV column order (header
and rows). The order is `id` first (where applicable), then the spec §16
default-allowlist order for ``posts``, then alphabetical for everything
else — predictable and diff-friendly.

See also
--------

- ``spec.md`` §16 (import/export), §16 (7) ("Excluded from default export"),
  §18 rule 18 ("Publish-flow export carve-out"), §25 Phase 5 / Phase 5.5
  checklists.
- ``docs/ARCHITECTURE.md`` "Export allowlist contract" section.
"""

from __future__ import annotations

from typing import TypedDict


class TableAllowlist(TypedDict):
    """Per-table export column policy.

    Attributes
    ----------
    default_columns
        Columns always included in CSV export. Order matters — the exporter
        emits them as the CSV header in this order, then writes each row's
        values in the same order.
    opt_in_columns
        Columns included only when the caller passes ``include_opt_in=True``
        to ``export_table_to_csv``. Appended after ``default_columns`` in the
        header order. Phase 5 ships with several empty lists here; Phase 5.5
        and Phase 5.6 populate them. The exporter MUST handle the empty case
        without changing CSV shape (i.e. ``default_columns + opt_in_columns``
        with an empty opt-in list equals ``default_columns``).
    excluded_columns
        Documentary — columns that exist in the table but must NEVER appear
        in any CSV export under any flag. The exporter asserts that no
        ``default_columns`` or ``opt_in_columns`` entry collides with this
        list (fail-fast against a misconfiguration). Phase 5.5 populates
        this for ``posts.publish_last_error``.
    """

    default_columns: list[str]
    opt_in_columns: list[str]
    excluded_columns: list[str]


class UnknownTableError(ValueError):
    """Raised by exporters when asked to export a table not in the registry.

    Inherits from :class:`ValueError` (not :class:`KeyError`) so
    ``str(err)`` returns the plain message text. ``KeyError`` calls
    :func:`repr` on its message argument, which would surface the
    multi-line message as a single double-quoted string in CLI / UI
    output — confusing to read.

    The "allowlisted tables" list is computed lazily at ``__init__``
    time (rather than baked into a frozen string at class-definition
    time) so this class can be defined BEFORE :data:`ALLOWLISTS` is
    populated. Earlier revisions captured the list eagerly and depended
    on the module's load order for correctness — a future circular
    import would have NameError'd. The lazy lookup tolerates a
    partially-initialised module.
    """

    def __init__(self, table_name: str) -> None:
        try:
            known = ", ".join(sorted(ALLOWLISTS.keys()))
        except NameError:
            known = "(registry not yet loaded)"
        super().__init__(
            f"Unknown table {table_name!r}. "
            f"Allowlisted tables: {known}. "
            f"Add an entry to app/exports/allowlists.py::ALLOWLISTS to "
            f"expose a new table to CSV export."
        )
        self.table_name = table_name


# ---------------------------------------------------------------------------
# account_snapshots — immutable daily account snapshots (§10, §16 (1)).
# raw_response_id is included for joins; the JSON exporter handles the
# sensitive raw_api_responses table separately with redaction.
# ---------------------------------------------------------------------------
ACCOUNT_SNAPSHOTS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "snapshot_date",
        "collected_at_utc",
        "x_user_id",
        "username",
        "profile_url",
        "followers_count",
        "following_count",
        "post_count",
        "listed_count",
        "like_count",
        "media_count",
        "bio_text",
        "baseline_followers",
        "source",
        "data_quality",
        "raw_response_id",
        "created_at",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# posts — §16 (7) verbatim default allowlist for currently-existing columns.
#
# Phase 5 ships only the Phase-1 column subset. Phase 5.5 will append the
# publish-flow columns; the Phase 5.5 prompt in §25 explicitly cites this
# file.
#
# Why some §16 (7) columns aren't here yet:
#   - agent_draft_id, published_to_x_at, publish_method, publish_attempt_count
#     are added by migrations in Phase 5.5 — including them in default_columns
#     before the column exists would crash csv_exporter at SELECT time.
#   - publish_last_error and published_via_agent_message_id are also Phase 5.5
#     and live under opt_in_columns / excluded_columns respectively (§16 (7),
#     §18 rule 18).
#
# Column order matches §16 (7) for the columns that do exist today.
# ---------------------------------------------------------------------------
POSTS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "x_post_id",
        "created_at_utc",
        "created_date",
        "text",
        "url",
        "type",
        "conversation_id",
        "in_reply_to_post_id",
        "in_reply_to_user",
        "posted_via",
        "manual_confirmation_status",
        "contains_link",
        "expanded_urls_json",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "created_in_app_at",
        # Phase 5.5 — non-sensitive publish-flow columns (§16 (7)).
        "agent_draft_id",
        "published_to_x_at",
        "publish_method",
        "publish_attempt_count",
        # PHASE 5.6 INSERT HERE — once Reply Target Discovery lands, append
        # the non-sensitive reply-target columns:
        #     "in_reply_to_reply_target_id",
        #     "reply_intent",
    ],
    "opt_in_columns": [
        # Phase 5.5 — opt-in because joining to agent_messages would leak
        # chat content (§18 rule 18). FK only; no chat text exported here.
        "published_via_agent_message_id",
    ],
    "excluded_columns": [
        # Phase 5.5 — NEVER exported under any flag. May contain X API
        # diagnostic strings / credential-adjacent error text from
        # publish_post_to_x failure paths (§16 (7), §18 rule 18).
        "publish_last_error",
    ],
}


# ---------------------------------------------------------------------------
# post_metric_snapshots — immutable per-post metric history (§10).
# Included for completeness; the CSV view of posts with latest metrics is
# usually preferred but the raw history is occasionally useful for offline
# analysis. Has no sensitive columns.
# ---------------------------------------------------------------------------
POST_METRIC_SNAPSHOTS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "post_id",
        "x_post_id",
        "collected_at_utc",
        "impressions",
        "likes",
        "replies",
        "reposts",
        "quotes",
        "bookmarks",
        "engagements_total",
        "profile_clicks",
        "url_link_clicks",
        "source",
        "data_quality",
        "raw_response_id",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# post_classifications — content metadata + learning notes (§10).
# All fields are user-authored interpretive notes — safe to export.
# ---------------------------------------------------------------------------
POST_CLASSIFICATIONS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "post_id",
        "pillar",
        "audience",
        "cta",
        "quality_score",
        "why_posted",
        "hypothesis",
        "expected_signal",
        "actual_signal",
        "lesson",
        "classified_at",
        "updated_at",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# daily_activity — daily reps + behavior tracking (§10, §16 (3)).
# ---------------------------------------------------------------------------
DAILY_ACTIVITY_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "activity_date",
        "planned_posts",
        "planned_replies",
        "planned_quotes",
        "posts_shipped",
        "replies_shipped",
        "quotes_shipped",
        "high_quality_reply_targets_found",
        "reply_sessions_completed",
        "minimum_reps_completed",
        "time_spent_minutes",
        "manual_actions_count",
        "api_actions_count",
        "avoidance_notes",
        "daily_note",
        "created_at",
        "updated_at",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# reply_sessions — per-session reply workouts (§10).
# notes is user-authored; no sensitive content.
# ---------------------------------------------------------------------------
REPLY_SESSIONS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "session_date",
        "started_at",
        "duration_minutes",
        "target_lane",
        "target_accounts_json",
        "targets_found",
        "replies_shipped",
        "best_reply_post_id",
        "session_quality_score",
        "notes",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# stir_conversion_events — event-level Stir funnel (§10, §16 (4)).
# qualitative_feedback CAN contain free-text from testers; per §18 rule 4
# it should not be published. The CSV export is for Daniel's offline
# analysis only, so it's included by default (Daniel owns the data), but
# never bundled into the JSON dump which is more likely to be shared.
# ---------------------------------------------------------------------------
STIR_CONVERSION_EVENTS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "occurred_at_utc",
        "event_date",
        "event_category",
        "event_type",
        "source",
        "medium",
        "campaign",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "referring_post_id",
        "referring_x_handle",
        "attribution_method",
        "is_likely_icp",
        "qualitative_feedback",
        "source_data_quality",
        "raw_response_id",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# stir_testers — person-level tester records (§10, §16 (6)).
# Per §18 rule 5, testers should use aliases by default — the `alias` field
# IS the public-facing identifier, so it's safe to export. `contact_ref` is
# free-text and may hold an email or DM handle; included by default since
# this CSV is for Daniel's offline analysis (the JSON exporter does NOT
# include this table by default — see json_exporter.py).
# ---------------------------------------------------------------------------
STIR_TESTERS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "alias",
        "x_handle",
        "contact_ref",
        "source",
        "first_seen_date",
        "is_working_parent_home_cook",
        "icp_notes",
        "downloaded_app_at",
        "scanned_kitchen_at",
        "got_plausible_dinners_at",
        "used_cook_mode_at",
        "feedback_summary",
        "status",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# milestones — distribution + validation + content + reps ladders (§10).
# Public-safe configuration.
# ---------------------------------------------------------------------------
MILESTONES_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "category",
        "ladder_position",
        "name",
        "start_value",
        "target_value",
        "current_value_override",
        "status",
        "achieved_at",
        "notes",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# weekly_reviews — weekly postmortems (§10, §16 (5)).
# counterfactual_note is critical and always included — that's the whole
# epistemic point of the weekly review per §14.6.
# ---------------------------------------------------------------------------
WEEKLY_REVIEWS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "week_start_date",
        "week_end_date",
        "followers_start",
        "followers_end",
        "follower_delta",
        "posts_shipped",
        "replies_shipped",
        "reply_sessions_completed",
        "daily_reps_days_completed",
        "best_post_id",
        "worst_post_id",
        "strongest_pillar",
        "weakest_pillar",
        "downloads",
        "qualified_icp_testers",
        "what_moved",
        "what_got_stuck",
        "lesson",
        "next_week_experiment",
        "counterfactual_note",
        "exported_markdown_path",
        "created_at",
        "updated_at",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# experiments — optional named hypotheses (§10).
# All user-authored; no sensitive content.
# ---------------------------------------------------------------------------
EXPERIMENTS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "name",
        "start_date",
        "end_date",
        "hypothesis",
        "content_lane",
        "target_audience",
        "success_metric",
        "minimum_sample_size",
        "result_summary",
        "status",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# Phase 5.5 — Growth Agent tables.
#
# Agent tables are NOT in the §16 default-export set because most rows
# carry conversational content (`agent_messages.content`, `agent_drafts.
# text`) that is high-volume + free-text. Daniel's audit-export action
# in §16 (8) calls these allowlists explicitly when needed; the CSV
# exporter rejects them otherwise.
#
# A recurring rule: `agent_tool_calls.arguments_json` and `.result_json`
# are NEVER on default_columns or opt_in_columns. Those blobs may
# contain quoted X API payloads, in-flight error stacks, and (pre-
# redaction) confirmation tokens for publish tools. The JSON dump path
# is the only way to export them, and it runs through the existing
# secret-redaction in app/exports/json_exporter.py.
# ---------------------------------------------------------------------------

AGENT_CONVERSATIONS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "started_at_utc",
        "last_message_at_utc",
        "title",
        "context_seed",
        "status",
        "message_count",
        "total_input_tokens",
        "total_output_tokens",
        "estimated_cost_usd",
        "model_default",
        "created_at",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}

AGENT_MESSAGES_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "conversation_id",
        "role",
        "model",
        "input_tokens",
        "output_tokens",
        "resulted_in_published_post_id",
        "created_at_utc",
    ],
    "opt_in_columns": [
        # Free-text chat content. Opt-in only because Daniel may not want
        # entire conversations leaving the local DB during a normal export.
        "content",
        "tool_calls_json",
        "tool_call_id",
        "rate_snapshot_json",
    ],
    "excluded_columns": [],
}

AGENT_TOOL_CALLS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "message_id",
        "tool_name",
        "redacted_arguments",
        "status",
        "duration_ms",
        "cost_input_tokens",
        "cost_output_tokens",
        "cost_usd",
        "created_at_utc",
    ],
    "opt_in_columns": [
        "notes",
    ],
    "excluded_columns": [
        # arguments_json may carry pre-redaction confirmation_token strings
        # for publish tools (the redaction is defense in depth, not the only
        # gate — never export the raw JSON via CSV). result_json may
        # contain X API responses with credential-adjacent metadata.
        # error_message may include stack traces.
        "arguments_json",
        "result_json",
        "error_message",
    ],
}

AGENT_TARGET_ACCOUNTS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "x_handle",
        "display_name",
        "notes",
        "lane",
        "priority",
        "last_engaged_at",
        "is_active",
        "created_at",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}

VOICE_SAMPLES_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "post_id",
        "text",
        "context_note",
        "pillar",
        "is_active",
        "priority",
        "added_at_utc",
        "last_used_at_utc",
    ],
    "opt_in_columns": [],
    "excluded_columns": [],
}

AGENT_DRAFTS_ALLOWLIST: TableAllowlist = {
    "default_columns": [
        "id",
        "created_at",
        "session_id",
        "conversation_id",
        "draft_kind",
        "pillar",
        "audience",
        "cta",
        "hypothesis_id",
        "target_post_url",
        "iwh_attempt_index",
        "status",
        "final_post_id",
        "revision_of",
    ],
    "opt_in_columns": [
        # Free-text draft content. Same reasoning as agent_messages.content.
        "text",
        "target_post_text",
        "agent_reasoning",
        "voice_self_score",
        "user_feedback",
    ],
    "excluded_columns": [],
}


# ---------------------------------------------------------------------------
# Registry. Keys are the CSV-export table names accepted by
# ``export_table_to_csv(table_name, ...)``. Adding a new table to MVP
# scope requires adding it here AND extending tests/test_exports.py.
#
# NOTE: ``raw_api_responses``, ``account_snapshot_corrections``, and
# ``schema_migrations`` are intentionally NOT in this registry — they are
# either covered by the JSON dump (raw_api_responses) or operational
# bookkeeping with no offline-analysis value.
#
# Phase 5.5 agent tables are registered so the audit-export action in
# §16 (8) can call ``export_table_to_csv("agent_messages", ..., include_
# opt_in=True)`` etc. when Daniel explicitly opts in.
# ---------------------------------------------------------------------------
ALLOWLISTS: dict[str, TableAllowlist] = {
    "account_snapshots": ACCOUNT_SNAPSHOTS_ALLOWLIST,
    "posts": POSTS_ALLOWLIST,
    "post_metric_snapshots": POST_METRIC_SNAPSHOTS_ALLOWLIST,
    "post_classifications": POST_CLASSIFICATIONS_ALLOWLIST,
    "daily_activity": DAILY_ACTIVITY_ALLOWLIST,
    "reply_sessions": REPLY_SESSIONS_ALLOWLIST,
    "stir_conversion_events": STIR_CONVERSION_EVENTS_ALLOWLIST,
    "stir_testers": STIR_TESTERS_ALLOWLIST,
    "milestones": MILESTONES_ALLOWLIST,
    "weekly_reviews": WEEKLY_REVIEWS_ALLOWLIST,
    "experiments": EXPERIMENTS_ALLOWLIST,
    "agent_conversations": AGENT_CONVERSATIONS_ALLOWLIST,
    "agent_messages": AGENT_MESSAGES_ALLOWLIST,
    "agent_tool_calls": AGENT_TOOL_CALLS_ALLOWLIST,
    "agent_target_accounts": AGENT_TARGET_ACCOUNTS_ALLOWLIST,
    "voice_samples": VOICE_SAMPLES_ALLOWLIST,
    "agent_drafts": AGENT_DRAFTS_ALLOWLIST,
}


def _get(table_name: str) -> TableAllowlist:
    try:
        return ALLOWLISTS[table_name]
    except KeyError as exc:
        raise UnknownTableError(table_name) from exc


def columns_for_export(table_name: str, *, include_opt_in: bool = False) -> list[str]:
    """Return the ordered list of columns for a CSV export.

    Combines ``default_columns`` with ``opt_in_columns`` (in that order) when
    ``include_opt_in`` is true. Asserts that no column appears in both
    ``default_columns``/``opt_in_columns`` AND ``excluded_columns`` — that
    would be a misconfiguration and the resulting CSV would leak excluded
    data.

    Raises
    ------
    UnknownTableError
        If ``table_name`` is not in :data:`ALLOWLISTS`.
    ValueError
        If the allowlist for ``table_name`` is internally inconsistent (a
        column appears in both an inclusion list and ``excluded_columns``).
    """
    allowlist = _get(table_name)
    excluded = set(allowlist["excluded_columns"])
    chosen: list[str] = list(allowlist["default_columns"])
    if include_opt_in:
        chosen.extend(allowlist["opt_in_columns"])
    collisions = [c for c in chosen if c in excluded]
    if collisions:
        raise ValueError(
            f"Allowlist for {table_name!r} is internally inconsistent: "
            f"columns {collisions!r} appear in both an inclusion list and "
            f"excluded_columns. Fix app/exports/allowlists.py before exporting."
        )
    return chosen


def get_opt_in_columns(table_name: str) -> list[str]:
    """Return ``opt_in_columns`` for ``table_name`` (may be empty)."""
    return list(_get(table_name)["opt_in_columns"])


def get_excluded_columns(table_name: str) -> list[str]:
    """Return ``excluded_columns`` for ``table_name`` (may be empty)."""
    return list(_get(table_name)["excluded_columns"])
