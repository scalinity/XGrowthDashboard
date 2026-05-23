"""Cost tracking and monthly ceiling enforcement (§28.6).

Per §28.6 + Phase 7 install (RV2-3):

* Per-call cost is estimated from token counts and a per-model rate snapshot
  taken at call time (so retroactive auditing isn't broken if Anthropic
  pricing changes).
* Monthly cap is the COMBINED Anthropic + xAI ceiling — read from
  ``combined_ai_monthly_cost_ceiling_usd`` (Phase 7 default $30) with a
  fallback to the legacy ``agent_monthly_cost_cap_usd`` key (default $25)
  for pre-migration-018 databases.
* At 80% → yellow banner. At 100% → red banner; agent disabled.
* Enforcement at the client layer — ``check_ceiling_or_raise`` is called
  before any ``messages.create`` round trip in ``app.agent.client``.

The rate table here is a snapshot of public Anthropic prices as of
2025-01 (USD per million tokens). It is versioned via ``RATE_TABLE_VERSION``;
``agent_messages.rate_snapshot_json`` should record the entry used at call
time so cost audits remain accurate after future repricing.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

# Per-million-token pricing snapshot. Update RATE_TABLE_VERSION when refreshed.
RATE_TABLE_VERSION = "2025-01-snapshot"

# input_per_million_usd, output_per_million_usd
_MODEL_RATES: dict[str, tuple[float, float]] = {
    # Opus tier
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
    # Sonnet tier
    "claude-sonnet-4-6": (3.0, 15.0),
    # Haiku tier (used by lint pass)
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
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
    """Sum per-message reconstructed cost for the current month.

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


def check_ceiling_or_raise(
    conn: sqlite3.Connection,
    *,
    projected_call_cost_usd: float = 0.0,
    now: datetime | None = None,
) -> None:
    """Raise ``MonthlyCostCeilingExceeded`` if MTD + projected exceeds cap.

    Callers pass an estimate of what THIS call will add; the check is
    pessimistic (cap considered breached if even one more call would push
    past). The agent client estimates the upcoming call's cost from the
    most recent message's token shape; lint passes pass a small constant
    (Haiku cost is rounding).
    """
    mtd = month_to_date_spend_usd(conn, now=now)
    cap = get_monthly_ceiling_usd(conn)
    if mtd + projected_call_cost_usd >= cap:
        # W21: wording clarified — `>=` refuses at the cap exactly, not
        # only over it. "would reach or exceed" matches the comparison.
        raise MonthlyCostCeilingExceeded(
            f"month-to-date spend ${mtd:.2f} + projected ${projected_call_cost_usd:.4f} "
            f"would reach or exceed cap ${cap:.2f}. Raise the cap in "
            f"Settings → Growth Agent or wait until the next month."
        )
