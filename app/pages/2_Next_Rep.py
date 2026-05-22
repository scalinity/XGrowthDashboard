"""Next rep — spec.md §14.2 (without the §29 reply-target panel).

Closes the loop between measurement and the daily generative act. The view
surfaces the lane Daniel is under-sampling and the open hypotheses needing
data. The §29 reply-target panel slot is rendered as a clearly labelled
placeholder so its location is stable through Phase 5.6.
"""

from __future__ import annotations

import sys
from datetime import date as _date_t
from datetime import timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.components.badges.confidence_label import ui_label_for_db_label
from app.components.badges.sample_size import sample_size_badge
from app.components.theme import PALETTE, apply_theme, callout, hairline, kicker
from app.pages import open_connection


_LANE_GAP_WINDOW_DAYS = 7


def _lane_coverage_this_week(conn) -> list[tuple[tuple[str, str, str], int]]:
    """How many posts hit each lane in the last 7 days, lowest first."""
    cutoff = (_date_t.today() - timedelta(days=_LANE_GAP_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT plm.pillar, plm.audience, plm.cta, COUNT(*) AS n
        FROM v_post_latest_metrics plm
        JOIN posts p ON p.id = plm.post_id
        WHERE p.created_date >= ?
          AND plm.pillar IS NOT NULL
        GROUP BY plm.pillar, plm.audience, plm.cta
        """,
        (cutoff,),
    ).fetchall()
    # Backfill known lanes from v_lane_performance so empty lanes appear too.
    known = conn.execute(
        "SELECT DISTINCT pillar, audience, cta FROM v_lane_performance"
    ).fetchall()
    counts: dict[tuple[str, str, str], int] = {}
    for r in known:
        counts[(r["pillar"], r["audience"], r["cta"])] = 0
    for r in rows:
        counts[(r["pillar"], r["audience"], r["cta"])] = int(r["n"])
    return sorted(counts.items(), key=lambda kv: kv[1])


def _open_hypotheses(conn):
    return conn.execute(
        """
        SELECT id, name, hypothesis, content_lane, target_audience,
               success_metric, minimum_sample_size, start_date
        FROM experiments
        WHERE status = 'running'
        ORDER BY start_date ASC
        """
    ).fetchall()


def _hypothesis_progress(conn, exp) -> tuple[int, int | None]:
    """Return (posts_in_lane_since_start, minimum_sample_size)."""
    minimum = exp["minimum_sample_size"]
    if not exp["content_lane"]:
        return 0, minimum
    n = conn.execute(
        """
        SELECT COUNT(*) FROM v_post_latest_metrics plm
        JOIN posts p ON p.id = plm.post_id
        WHERE plm.pillar = ?
          AND p.created_date >= ?
        """,
        (exp["content_lane"], exp["start_date"]),
    ).fetchone()[0]
    return int(n or 0), minimum


def _agent_target_accounts_available(conn) -> bool:
    """`agent_target_accounts` lands in Phase 5.5. Detect its presence."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_target_accounts'"
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
apply_theme()
conn = open_connection()

kicker("WHAT SHOULD I POST NEXT · §14.2")
st.title("Next rep")
st.caption(
    "Measurement + generation, one click apart. The lane scoreboard below "
    "looks at the last 7 days; pick the lane with the lowest count to "
    "reduce uncertainty in your strongest hypothesis area."
)

# Lane coverage scoreboard.
st.markdown("## This week — lane coverage")
coverage = _lane_coverage_this_week(conn)
if not coverage:
    st.markdown(
        "<p class='faint'>No classified posts yet. Classify a few from "
        "<strong>Manual entry → Needs tagging</strong> so the lane scoreboard "
        "has something to read.</p>",
        unsafe_allow_html=True,
    )
else:
    biggest_gap = coverage[0]
    for (lane, n) in coverage:
        is_gap = lane == biggest_gap[0]
        accent = PALETTE["phosphor"] if is_gap else PALETTE["bone_dim"]
        gap_label = " · biggest gap" if is_gap else ""
        st.markdown(
            f"""<div style='display:flex; justify-content:space-between; padding:0.4rem 0;
                            border-bottom:1px solid {PALETTE['hairline']};'>
                <span class='numeric' style='color:{PALETTE['bone']};'>
                    {lane[0]} · {lane[1]} · {lane[2]}
                </span>
                <span class='numeric' style='color:{accent};'>
                    {n} post{'s' if n != 1 else ''}{gap_label}
                </span>
            </div>""",
            unsafe_allow_html=True,
        )
    pillar, audience, cta = biggest_gap[0]
    callout(
        f"<em>Suggested next rep:</em> a "
        f"<span class='numeric'>{pillar} · {audience} · {cta}</span> post. "
        f"This is the lane with the lowest 7-day count — shipping one here "
        "meaningfully reduces uncertainty in your strongest hypothesis area."
    )

hairline()

# Open hypotheses.
st.markdown("## Open hypotheses needing data")
hyps = _open_hypotheses(conn)
if not hyps:
    st.markdown(
        "<p class='faint'>No running experiments. Start one in "
        "<strong>Weekly review → next week's experiment</strong>, then "
        "manually flip its status to <code>running</code> in the "
        "experiments table (a real UI lands in V1.1).</p>",
        unsafe_allow_html=True,
    )
else:
    for h in hyps:
        n_in_lane, minimum = _hypothesis_progress(conn, h)
        st.markdown(
            f"""<div style='padding:0.5rem 0; border-bottom:1px solid {PALETTE['hairline']};'>
                <div style='display:flex; justify-content:space-between; align-items:baseline;'>
                    <span style='color:{PALETTE['bone']}; font-weight:500;'>{h['name']}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']};'>
                        started {h['start_date']}
                    </span>
                </div>
                <p style='margin:0.3rem 0; color:{PALETTE['bone']};'>{h['hypothesis']}</p>
                <p class='faint' style='margin:0;'>
                    Lane: <span class='numeric'>{h['content_lane'] or '—'}</span> ·
                    success metric: <span class='numeric'>{h['success_metric']}</span>
                </p>
            </div>""",
            unsafe_allow_html=True,
        )
        cols = st.columns([1, 4])
        with cols[0]:
            sample_size_badge(n_in_lane, n_target=minimum)
        with cols[1]:
            if minimum and n_in_lane >= minimum:
                st.markdown(
                    f"<span class='numeric' style='color:{PALETTE['phosphor']};'>"
                    f"Minimum sample reached — log a result in Weekly Review.</span>",
                    unsafe_allow_html=True,
                )
            elif minimum:
                remaining = minimum - n_in_lane
                st.markdown(
                    f"<span class='numeric' style='color:{PALETTE['bone_dim']};'>"
                    f"{remaining} more post{'s' if remaining != 1 else ''} needed "
                    f"to reach minimum sample.</span>",
                    unsafe_allow_html=True,
                )

hairline()

# Reply-target panel placeholder — Phase 5.6 fills this in.
st.markdown("## Reply targets")
st.markdown(
    f"""<div style='border:1px dashed {PALETTE['hairline']};
                     padding:1.2rem; border-radius:3px;
                     background:{PALETTE['surface']};'>
        <div class='kicker'>PLACEHOLDER · PHASE 5.6</div>
        <p style='margin:0.4rem 0 0.2rem 0; color:{PALETTE['bone']};'>
            Reply-target queue panel lands here in Phase 5.6 (§29).
        </p>
        <p class='faint' style='margin:0;'>
            The MVP scoring (Relevance / Engagement surface / Saturation /
            Reply opportunity) and the deterministic
            <code>recommended_action_label</code> will render in this slot.
            Keeping the slot visible now prevents accidental rebuild in 5.6.
        </p>
    </div>""",
    unsafe_allow_html=True,
)

# Account leads — secondary list. Falls back to "no curated accounts yet"
# until the Phase 5.5 `agent_target_accounts` table lands.
if not _agent_target_accounts_available(conn):
    st.markdown(
        "<p class='faint' style='margin-top:0.8rem;'>"
        "<strong>Account leads:</strong> no curated accounts yet — "
        "<code>agent_target_accounts</code> lands in Phase 5.5.</p>",
        unsafe_allow_html=True,
    )
