"""Cost tracking and monthly ceiling enforcement (§28.6).

Per §28.6 + Phase 7 install (RV2-3) + Phase 9 (Grok):

* Per-call cost is estimated from token counts and a per-provider rate
  snapshot taken at call time (so retroactive auditing isn't broken if
  pricing changes). Anthropic spend is reconstructed from
  ``agent_messages.input_tokens/output_tokens × rate_snapshot_json``; xAI
  Grok spend is reconstructed from ``grok_api_responses.rate_snapshot_json``
  (Phase 9, migration 021).
* Monthly cap is the COMBINED Anthropic + xAI ceiling — read from
  ``combined_ai_monthly_cost_ceiling_usd`` (Phase 7 default $30) with a
  fallback to the legacy ``agent_monthly_cost_cap_usd`` key (default $25)
  for pre-migration-018 databases.
* At 80% → yellow banner. At 100% → red banner; agent AND Grok sweep
  disabled (both providers refuse new calls).
* Enforcement at the client layer — ``check_ceiling_or_raise`` is called
  before any ``messages.create`` round trip in ``app.agent.client`` AND
  before any ``POST /v1/chat/completions`` call in ``app.grok_client``.

The rate table here is a snapshot of public Anthropic + xAI prices as of
2026-05-23 (USD per million tokens). It is versioned via
``RATE_TABLE_VERSION``; ``agent_messages.rate_snapshot_json`` and
``grok_api_responses.rate_snapshot_json`` should record the entry used at
call time so cost audits remain accurate after future repricing.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

# Per-million-token pricing snapshot. Update RATE_TABLE_VERSION when refreshed.
RATE_TABLE_VERSION = "2026-05-snapshot"

# input_per_million_usd, output_per_million_usd
_MODEL_RATES: dict[str, tuple[float, float]] = {
    # Opus tier (Anthropic)
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
    # Sonnet tier (Anthropic)
    "claude-sonnet-4-6": (3.0, 15.0),
    # Haiku tier (Anthropic; used by lint pass)
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # xAI Grok — Phase 9. Pricing per https://docs.x.ai/docs/models
    # (verified 2026-05-23): grok-4.3 = $1.25 input / $2.50 output per
    # million tokens, 1M-token context window. Grok-4.3 is the
    # recommended general-purpose model and supports Live Search with
    # source type "x" for X firehose discovery (§29.12).
    "grok-4.3": (1.25, 2.50),
    # P9R-35: 'grok-4' alias kept as a defensive fallback ONLY for
    # historical rate-snapshot rows whose model field carried that
    # value before Phase 9. No production caller picks 'grok-4' —
    # DEFAULT_GROK_MODEL is 'grok-4.3'. Mapped to grok-4.3 rates so a
    # historical row reconstructs at the documented Phase-9 price (the
    # actual grok-4 base model was retired by Phase 9 land date).
    "grok-4": (1.25, 2.50),
}

# RV2-3: Phase 7 raised the historical Anthropic-only ceiling ($25) to a
# combined Anthropic + xAI ceiling ($30). Code reads
# ``combined_ai_monthly_cost_ceiling_usd`` first, falling back to the
# legacy ``agent_monthly_cost_cap_usd`` for pre-migration-018 databases.
COMBINED_CEILING_SETTING_KEY: str = "combined_ai_monthly_cost_ceiling_usd"
LEGACY_CEILING_SETTING_KEY: str = "agent_monthly_cost_cap_usd"
DEFAULT_MONTHLY_CEILING_USD: float = 30.0  # Phase 7 default
DEFAULT_CEILING_WARN_FRACTION: float = 0.80

# S3: coarse projected per-call cost used by the cost-ceiling preflight.
# Calibrated for a typical Opus tool-use round trip (a few thousand input
# tokens + small output). The real cost is recorded post-call from the
# token counts the API returns; this constant is only the "would this
# call breach the cap?" guess.
PROJECTED_CALL_COST_GUESS_USD: float = 0.05


class MonthlyCostCeilingExceeded(RuntimeError):
    """Raised when the next agent call would breach the monthly USD ceiling."""


@dataclass(frozen=True)
class CostEstimate:
    input_tokens: int
    output_tokens: int
    model: str
    input_cost_usd: float
    output_cost_usd: float
    total_usd: float
    rate_snapshot: dict


def get_model_rates(model: str) -> tuple[float, float]:
    """Return (input_per_million, output_per_million) for ``model``.

    Falls back to the Opus rate (the most expensive) if the model is
    unknown — defensive against typos so cost is never under-estimated.
    """
    if model not in _MODEL_RATES:
        return _MODEL_RATES["claude-opus-4-7"]
    return _MODEL_RATES[model]


def estimate_cost(
    *, input_tokens: int, output_tokens: int, model: str
) -> CostEstimate:
    """Compute a ``CostEstimate`` from raw token counts."""
    rate_in, rate_out = get_model_rates(model)
    in_cost = (input_tokens / 1_000_000.0) * rate_in
    out_cost = (output_tokens / 1_000_000.0) * rate_out
    snapshot = {
        "version": RATE_TABLE_VERSION,
        "model": model,
        "input_per_million_usd": rate_in,
        "output_per_million_usd": rate_out,
    }
    return CostEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        input_cost_usd=in_cost,
        output_cost_usd=out_cost,
        total_usd=in_cost + out_cost,
        rate_snapshot=snapshot,
    )


def month_to_date_spend_usd(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> float:
    """Sum per-message reconstructed Anthropic cost for the current month.

    Single source of truth: agent_messages.input_tokens/output_tokens ×
    rate_snapshot_json. Rows without a snapshot fall back to model
    default rates (defensive — same as get_model_rates).

    The agent_tool_calls.cost_usd column is intentionally NOT summed in
    here. It exists as a per-call cost stamp (e.g. for the future Haiku
    lint pass which doesn't anchor to an agent_messages row), but the
    lint cost is rounding against the §28.6 monthly cap and including
    it here would silently double-count any future caller that anchors
    a tool call to the same message that already recorded the round-
    trip cost on agent_messages.

    Anthropic-only by design. Phase 9 Grok spend is summed by
    ``xai_month_to_date_spend_usd``; the §28.6 combined cap reads both
    via ``combined_month_to_date_spend_usd``.
    """
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_iso = month_start.strftime("%Y-%m-%d %H:%M:%S")

    msg_rows = conn.execute(
        """
        SELECT input_tokens, output_tokens, model, rate_snapshot_json
        FROM agent_messages
        WHERE created_at_utc >= ?
          AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL)
        """,
        (month_start_iso,),
    ).fetchall()
    msg_spend = 0.0
    for row in msg_rows:
        in_tok = int(row["input_tokens"] or 0)
        out_tok = int(row["output_tokens"] or 0)
        snapshot = None
        if row["rate_snapshot_json"]:
            try:
                snapshot = json.loads(row["rate_snapshot_json"])
            except (json.JSONDecodeError, TypeError):
                snapshot = None
        if snapshot is not None:
            rate_in = float(snapshot.get("input_per_million_usd", 0.0))
            rate_out = float(snapshot.get("output_per_million_usd", 0.0))
        else:
            rate_in, rate_out = get_model_rates(row["model"] or "claude-opus-4-7")
        msg_spend += (in_tok / 1_000_000.0) * rate_in
        msg_spend += (out_tok / 1_000_000.0) * rate_out

    return msg_spend


def get_monthly_ceiling_usd(conn: sqlite3.Connection) -> float:
    """Read the §28.6 combined Anthropic + xAI ceiling.

    RV2-3: prefers the Phase 7 ``combined_ai_monthly_cost_ceiling_usd``
    key. Falls back to the legacy ``agent_monthly_cost_cap_usd`` key so
    a fresh DB initialized before migration 018 ran still works. If
    neither row exists OR both rows have unparseable JSON, the default
    ($30 Phase-7 ceiling) is returned.

    The two-key fallback also handles the upgrade scenario: an existing
    DB with the legacy $25 row continues to apply the legacy value
    until migration 018 inserts the new $30 row. Phase 7 install seeds
    the new row, after which it dominates.
    """
    for key in (COMBINED_CEILING_SETTING_KEY, LEGACY_CEILING_SETTING_KEY):
        row = conn.execute(
            "SELECT value_json FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None or row["value_json"] is None:
            continue
        try:
            return float(json.loads(row["value_json"]))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return DEFAULT_MONTHLY_CEILING_USD


def xai_month_to_date_spend_usd(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> float:
    """Sum per-Grok-call reconstructed cost for the current month (§29.12).

    Source of truth: ``grok_api_responses.rate_snapshot_json``. Each
    successful call writes a JSON blob with ``input_tokens``,
    ``output_tokens``, ``input_per_million_usd``, and
    ``output_per_million_usd``. Rate-limited / cost-ceiling-hit /
    verification-rejected rows have NO token counts to bill for and
    contribute $0 to spend.

    Returns 0.0 when the table doesn't exist (pre-migration-021
    databases — defensive against running on an older DB).

    P9R-30: narrowed the OperationalError catch to "no such table"
    only — a locked DB / disk error / SQL syntax error now propagates
    instead of silently returning $0.0 spend and letting Daniel slip
    one extra Grok call past the cap.

    P9R-29: row access uses field-name only (matching
    ``month_to_date_spend_usd`` directly above). The project sets
    ``conn.row_factory = sqlite3.Row`` globally in ``app/db.py::
    connect``; the prior ``isinstance(row, sqlite3.Row) else row[0]``
    fallback was dead defensive code asymmetric with the Anthropic
    helper.

    P9R-51: aggregation now happens SQL-side via ``json_extract`` +
    ``SUM`` for cleaner reads and constant-memory at the audit-table
    scale we'll grow to. Falls back to the prior Python-side sum on
    older SQLite builds (extremely unlikely — sqlite3 has shipped
    JSON1 by default for years).
    """
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_iso = month_start.strftime("%Y-%m-%d %H:%M:%S")

    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(
                ((COALESCE(json_extract(rate_snapshot_json, '$.input_tokens'), 0) * 1.0)
                 * COALESCE(json_extract(rate_snapshot_json, '$.input_per_million_usd'), 0.0)
                 +
                 (COALESCE(json_extract(rate_snapshot_json, '$.output_tokens'), 0) * 1.0)
                 * COALESCE(json_extract(rate_snapshot_json, '$.output_per_million_usd'), 0.0)
                ) / 1000000.0
            ), 0.0) AS spend_usd
              FROM grok_api_responses
             WHERE created_at_utc >= ?
               AND rate_snapshot_json IS NOT NULL
            """,
            (month_start_iso,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        # P9R-30: narrow on missing-table specifically. Any other
        # OperationalError ("database is locked", "disk I/O error",
        # SQL syntax error) must propagate so we don't silently
        # under-bill spend and slip past the ceiling.
        if "no such table" not in str(exc).lower():
            raise
        return 0.0

    if row is None or row["spend_usd"] is None:
        return 0.0
    return float(row["spend_usd"])


def combined_month_to_date_spend_usd(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> float:
    """Combined Anthropic + xAI spend for the current month (§28.6 / §29.12).

    The §28.6 ceiling is one number across both providers; this helper
    is the single source for "what's been spent so far this month
    against the combined cap." Both ``app/agent/client.py`` and
    ``app/grok_client.py`` route their preflight check through here.

    P9R-14: resolve ``now`` ONCE and pass the materialized timestamp
    into both inner functions. Pre-fix, when ``now=None`` each inner
    call independently called ``datetime.now(timezone.utc)``; at a
    UTC month boundary the two halves could read different months and
    spend briefly appeared to halve.
    """
    now = now or datetime.now(timezone.utc)
    return month_to_date_spend_usd(conn, now=now) + xai_month_to_date_spend_usd(
        conn, now=now
    )


def is_combined_ceiling_breached(
    conn: sqlite3.Connection,
    *,
    projected_call_cost_usd: float = 0.0,
    now: datetime | None = None,
) -> bool:
    """Return True if next call (projected USD) would reach or exceed the cap.

    Cheap predicate used by the §28.6 100%-ceiling banner and by the
    pre-call gates in both Anthropic + xAI clients. Mirrors the strict
    ``>=`` comparison in ``check_ceiling_or_raise`` (W21 wording).
    """
    # P9R-14: materialize `now` here too so the inner combined-spend
    # call doesn't compute a second clock read on a month boundary.
    now = now or datetime.now(timezone.utc)
    combined = combined_month_to_date_spend_usd(conn, now=now)
    cap = get_monthly_ceiling_usd(conn)
    return combined + projected_call_cost_usd >= cap


def check_ceiling_or_raise(
    conn: sqlite3.Connection,
    *,
    projected_call_cost_usd: float = 0.0,
    now: datetime | None = None,
) -> None:
    """Raise ``MonthlyCostCeilingExceeded`` if combined MTD + projected exceeds cap.

    Callers pass an estimate of what THIS call will add; the check is
    pessimistic (cap considered breached if even one more call would push
    past). The agent client estimates the upcoming call's cost from the
    most recent message's token shape; lint passes pass a small constant
    (Haiku cost is rounding).

    Phase 9 change: enforces the COMBINED Anthropic + xAI ceiling per
    §28.6. Pre-Phase-9 this read Anthropic spend only; now both providers
    accumulate into one number and both refuse calls at 100%.
    """
    anthropic_mtd = month_to_date_spend_usd(conn, now=now)
    xai_mtd = xai_month_to_date_spend_usd(conn, now=now)
    combined = anthropic_mtd + xai_mtd
    cap = get_monthly_ceiling_usd(conn)
    if combined + projected_call_cost_usd >= cap:
        # W21: wording clarified — `>=` refuses at the cap exactly, not
        # only over it. "would reach or exceed" matches the comparison.
        raise MonthlyCostCeilingExceeded(
            f"month-to-date combined AI spend ${combined:.2f} "
            f"(Anthropic ${anthropic_mtd:.2f} + xAI ${xai_mtd:.2f}) "
            f"+ projected ${projected_call_cost_usd:.4f} would reach or "
            f"exceed cap ${cap:.2f}. Raise the cap in Settings → Growth "
            f"Agent or wait until the next month."
        )
