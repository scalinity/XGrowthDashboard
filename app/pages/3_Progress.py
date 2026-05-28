"""Progress — spec.md §14.3.

Long-arc trend view. Visually equal weight given to the distribution ladder
(left column) and validation ladder (right column) per §4 and the §14.3
acceptance criterion. The follower line chart sits below with the §13
noise-floor band overlaid. Posts/replies-per-week mini-bars cap the page.
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

from app.components.charts.follower_trend import FollowerPoint, follower_trend_chart
from app.components.theme import PALETTE, apply_theme, hairline, kicker
from app.forms import get_setting
from app.pages import open_connection
from app.read_models.progress import build_progress_read_model


def _milestones_by_category(conn, category: str):
    return conn.execute(
        """
        SELECT id, category, ladder_position, name, start_value, target_value,
               current_value_override, status, achieved_at
        FROM milestones
        WHERE category = ?
        ORDER BY ladder_position ASC
        """,
        (category,),
    ).fetchall()


def _current_followers(conn) -> int | None:
    row = conn.execute(
        "SELECT followers_count FROM v_account_daily ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()
    return int(row["followers_count"]) if row else None


def _follower_history(conn) -> list[FollowerPoint]:
    rows = conn.execute(
        "SELECT snapshot_date, followers_count FROM v_account_daily ORDER BY snapshot_date ASC"
    ).fetchall()
    return [FollowerPoint(r["snapshot_date"], int(r["followers_count"])) for r in rows]


def _weekly_post_counts(conn, weeks: int = 8) -> list[tuple[str, int, int]]:
    """Returns [(iso_week_start, posts, replies)] for the last `weeks` ISO weeks.

    Single grouped query bucketing posts by Monday-anchored ISO week, then a
    Python-side fill for weeks with zero posts so the chart shows the full
    timeline (not just the weeks that happen to have data).
    """
    today = _date_t.today()
    monday = today - timedelta(days=today.weekday())
    earliest = (monday - timedelta(weeks=weeks - 1)).isoformat()
    latest = (monday + timedelta(days=6)).isoformat()

    # SQLite's strftime('%w', d) returns weekday with Sunday=0, so to anchor
    # on Monday we subtract `(strftime('%w', d) + 6) % 7` days from each row.
    # NB: the inner arithmetic MUST be parenthesised before the `||` concat
    # — SQLite's `||` precedence is high enough that without parens the
    # modifier reduces to a bare integer and DATE() returns NULL.
    rows = conn.execute(
        """
        SELECT
            DATE(created_date,
                 '-' || ((CAST(strftime('%w', created_date) AS INTEGER) + 6) % 7)
                 || ' days') AS week_start,
            SUM(CASE WHEN type IN ('standalone', 'thread_root', 'thread_child', 'quote')
                      THEN 1 ELSE 0 END) AS posts,
            SUM(CASE WHEN type = 'reply' THEN 1 ELSE 0 END) AS replies
        FROM posts
        WHERE created_date BETWEEN ? AND ?
        GROUP BY week_start
        ORDER BY week_start ASC
        """,
        (earliest, latest),
    ).fetchall()
    by_week = {
        r["week_start"]: (int(r["posts"] or 0), int(r["replies"] or 0))
        for r in rows
    }
    out: list[tuple[str, int, int]] = []
    for w in range(weeks - 1, -1, -1):
        ws = (monday - timedelta(weeks=w)).isoformat()
        posts, replies = by_week.get(ws, (0, 0))
        out.append((ws, posts, replies))
    return out


def _milestone_progress(m, current_followers: int | None) -> tuple[float, str]:
    """Returns (fraction in [0,1], display label)."""
    target = m["target_value"]
    start = m["start_value"] or 0
    if m["status"] == "achieved":
        return 1.0, "achieved"
    if target is None:
        return 0.0, "—"
    # Distribution ladder uses follower-count progress; validation is binary.
    if current_followers is not None and target > start:
        progress = (current_followers - start) / (target - start)
        progress = max(0.0, min(1.0, progress))
        return progress, f"{progress * 100:.1f}%"
    return 0.0, "not yet"


def _render_ladder(rows, *, current_followers: int | None, ladder_kind: str) -> None:
    if not rows:
        st.markdown(
            "<p class='faint'>No milestones seeded for this ladder.</p>",
            unsafe_allow_html=True,
        )
        return
    for m in rows:
        target_str = (
            f"{m['target_value']:,}" if m["target_value"] is not None else "—"
        )
        progress, label = _milestone_progress(
            m,
            current_followers if ladder_kind == "distribution" else None,
        )
        achieved = m["status"] == "achieved"
        accent = PALETTE["phosphor"] if achieved else PALETTE["bone_dim"]
        st.markdown(
            f"""<div style='padding: 0.5rem 0; border-bottom: 1px solid {PALETTE['hairline']};'>
                <div style='display:flex; justify-content:space-between; align-items:baseline;'>
                    <span style='color:{PALETTE['bone']};'>{m['name']}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{accent};'>
                        {label}
                    </span>
                </div>
                <div style='display:flex; gap:0.6rem; align-items:center; margin-top:0.2rem;'>
                    <span class='numeric' style='font-size:0.74rem; color:{PALETTE['bone_faint']};'>
                        target {target_str}
                    </span>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
        if ladder_kind == "distribution":
            st.progress(progress)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
apply_theme()
conn = open_connection()
progress_model = build_progress_read_model(conn)

kicker("LONG-ARC TREND · §14.3")
st.title("Progress")
st.caption(
    "Distribution and validation ladders carry equal weight (§4). "
    "Follower trend below uses the §12 noise-floor band — judge the week, "
    "not the morning."
)

current_followers = _current_followers(conn)

# Dual ladders.
left, right = st.columns(2, gap="large")
with left:
    st.markdown("## Distribution ladder")
    st.markdown(
        f"<p class='faint'>Current followers: <span class='numeric'>"
        f"{(current_followers or 0):,}</span></p>",
        unsafe_allow_html=True,
    )
    _render_ladder(
        _milestones_by_category(conn, "distribution"),
        current_followers=current_followers,
        ladder_kind="distribution",
    )
with right:
    st.markdown("## Validation ladder")
    st.markdown(
        "<p class='faint'>Binary milestones; ranking by date achieved.</p>",
        unsafe_allow_html=True,
    )
    _render_ladder(
        _milestones_by_category(conn, "validation"),
        current_followers=None,
        ladder_kind="validation",
    )

hairline()

# Follower trend.
st.markdown("## Follower trend")
points = _follower_history(conn)
fig = follower_trend_chart(points)
st.plotly_chart(fig, width="stretch")
st.markdown(
    "<p class='faint'>Shaded band is the ±2/day noise floor (§12). "
    "Days within the band are visualised, not arrow-marked — at this "
    "sample size, daily deltas are statistically indistinguishable from "
    "zero.</p>",
    unsafe_allow_html=True,
)

hairline()

# Phase 5.9 / §28.19 — follower-velocity projection panel.
# Three states (per spec):
#   1. noise floor → "trend not yet measurable — projections suppressed".
#   2. measurable + positive → "current pace + projected hit date".
#   3. date-target widget — always visible; computes daily-followers-
#      needed to hit current milestone by a Daniel-picked date.
from app.agent import velocity as _velocity  # noqa: E402 — page-local
from datetime import timedelta as _timedelta  # noqa: E402

st.markdown("## Velocity projection")
_proj = _velocity.get_velocity_projection(conn)
_noise_floor = progress_model["noise_floor"]
if _proj is None:
    st.markdown(
        "<p class='faint' style='font-size:0.85rem;'>"
        "No account snapshots yet — velocity projection unavailable.</p>",
        unsafe_allow_html=True,
    )
elif _proj.in_noise_floor:
    # Suppressed state — never show a fabricated date.
    _delta_label = (
        f"Δ7d = {(_proj.velocity_7d_per_day * 7):+.0f}"
        if _proj.velocity_7d_per_day is not None else "Δ7d = —"
    )
    st.markdown(
        f"<div style='border-left: 2px solid {PALETTE['warn_amber']}; "
        f"padding: 0.55rem 0.85rem; margin: 0.4rem 0; "
        f"background: {PALETTE['surface']};'>"
        f"<div class='numeric' style='font-size: 0.75rem; color: {PALETTE['warn_amber']}; "
        f"letter-spacing: 0.08em; text-transform: uppercase;'>"
        f"NOISE FLOOR · projections suppressed"
        f"</div>"
        f"<div style='font-family: Fraunces, serif; font-size: 1.0rem; color: {PALETTE['bone']}; "
        f"margin-top: 0.35rem;'>"
        f"Trend not yet measurable — projections suppressed until "
        f"|Δ7d| ≥ {_noise_floor}. "
        f"<span class='numeric'>({_delta_label})</span>"
        f"</div></div>",
        unsafe_allow_html=True,
    )
else:
    _v7 = _proj.velocity_7d_per_day or 0.0
    _projected = _proj.projected_milestone_hit_date_at_7d_pace
    st.markdown(
        f"<div style='border-left: 2px solid {PALETTE['phosphor']}; "
        f"padding: 0.55rem 0.85rem; margin: 0.4rem 0; "
        f"background: {PALETTE['surface']};'>"
        f"<div class='numeric' style='font-size: 0.75rem; color: {PALETTE['bone_faint']}; "
        f"letter-spacing: 0.08em; text-transform: uppercase;'>"
        f"CURRENT PACE · {_v7:+.1f} FOLLOWERS / DAY (7D)"
        f"</div>"
        f"<div style='font-family: Fraunces, serif; font-size: 1.0rem; color: {PALETTE['bone']}; "
        f"margin-top: 0.35rem;'>"
        f"At this pace you'd reach <span class='numeric'>"
        f"{_proj.current_milestone_target or '—'}"
        f"</span> by <span class='numeric'>{_projected or '—'}</span>."
        f"</div></div>",
        unsafe_allow_html=True,
    )

# Date-target widget — always visible (even in noise floor; this is the
# "what would it take to hit X" question Daniel asks regardless of trend).
with st.expander("Date-target widget — what pace would hit the milestone by …", expanded=False):
    _default_target = _date_t.today() + _timedelta(days=30)
    _picked = st.date_input(
        "Target date",
        value=_default_target,
        min_value=_date_t.today() + _timedelta(days=1),
        key="velocity_target_date",
    )
    _needed = _velocity.daily_followers_needed_to_hit_milestone_by_date(
        conn, target_date=_picked
    )
    if _needed is None:
        if _proj is None:
            st.caption("No snapshot data yet.")
        elif _proj.distance_to_current_milestone is not None and _proj.distance_to_current_milestone <= 0:
            st.caption("Milestone already met — pick the next one in Settings.")
        else:
            st.caption("Target date is in the past.")
    else:
        st.markdown(
            f"<div class='callout'>To hit "
            f"<span class='numeric'>{_proj.current_milestone_target}</span> "
            f"by <span class='numeric'>{_picked.isoformat()}</span> "
            f"you need <span class='numeric'>+{_needed}</span> followers/day.</div>",
            unsafe_allow_html=True,
        )

hairline()

# Behaviour mini-bars.
st.markdown("## Behaviour (last 8 weeks)")
weekly = _weekly_post_counts(conn)
max_v = max((p + r for _, p, r in weekly), default=1) or 1
for week_start, posts, replies in weekly:
    posts_pct = posts / max_v if max_v else 0
    replies_pct = replies / max_v if max_v else 0
    st.markdown(
        f"""<div style='padding: 0.35rem 0;'>
            <div style='display:flex; justify-content:space-between; align-items:baseline;'>
                <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']};'>
                    Week of {week_start}
                </span>
                <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone']};'>
                    posts {posts} · replies {replies}
                </span>
            </div>
            <div style='display:flex; gap:1px; margin-top:0.25rem;
                         height:8px; background:{PALETTE['surface_raised']};
                         border-radius:1px; overflow:hidden;'>
                <div style='width:{posts_pct * 100:.1f}%; background:{PALETTE['phosphor']};'></div>
                <div style='width:{replies_pct * 100:.1f}%; background:{PALETTE['phosphor_dim']};'></div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

st.markdown(
    f"<p class='faint' style='margin-top:1rem;'>"
    f"Daily targets — posts {get_setting(conn, 'daily_post_target', 1)}, "
    f"replies {get_setting(conn, 'daily_reply_target', 12)}, "
    f"reply sessions {get_setting(conn, 'daily_reply_session_target', 1)}. "
    f"Bar lengths are normalised against the busiest week shown.</p>",
    unsafe_allow_html=True,
)

hairline()

# Long-arc footer.
operational_ceiling = get_setting(conn, "operational_ceiling", 5000)
long_arc = get_setting(conn, "long_arc_reminder", 500000)
st.markdown(
    f"""<p class='faint' style='font-size:0.78rem; text-align:center;'>
    Operational ceiling: <span class='numeric'>{operational_ceiling:,}</span>.
    Long-arc reminder: <span class='numeric'>{long_arc:,}</span> — not operational.
    </p>""",
    unsafe_allow_html=True,
)
