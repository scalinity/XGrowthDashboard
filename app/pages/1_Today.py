"""Today / Weigh-In — spec.md §14.1.

The daily operating cockpit. Renders, in order:

1. Pinned snapshot form (collapses to "edit" link once today's snapshot exists).
2. Follower weigh-in cards: current count + Δ yesterday + Δ baseline +
   distance to current milestone, with the §13 noise-floor framing applied
   when |Δ| is below the band.
3. Daily reps progress from ``v_daily_reps``.
4. Recent activity (last 5 posts logged today).
5. Quick-link buttons into the Manual Entry tabs.

Per CLAUDE.md "Streamlit side-effects discipline", everything is computed
each rerun from a fresh DB read — no syncing flags between widgets.
"""

from __future__ import annotations

import html
import sys
from datetime import date as _date_t
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.components.theme import PALETTE, apply_theme, callout, hairline, kicker
from app.forms import get_setting, snapshot as snapshot_form
from app.pages import open_connection


def _format_delta(value: int | None, *, noise_floor: int = 2) -> tuple[str, str]:
    """Render a delta as (big_value, caption) for st.metric(value=…, delta=…).

    Streamlit's metric value box is narrow (~6 chars in mono), so the
    "(noise)" qualifier from §12 lives on the small caption line below
    the number rather than inside the big value. Out-of-band values get
    an empty caption.
    """
    if value is None:
        return "—", ""
    if abs(value) <= noise_floor:
        return f"{value:+d}", f"within ±{noise_floor}/day"
    return f"{value:+d}", ""


def _today_followers_snapshot(conn):
    """Return the v_account_daily row for today, or None."""
    return conn.execute(
        "SELECT * FROM v_account_daily WHERE snapshot_date = ?",
        (_date_t.today().isoformat(),),
    ).fetchone()


def _today_reps(conn):
    return conn.execute(
        "SELECT * FROM v_daily_reps WHERE activity_date = ?",
        (_date_t.today().isoformat(),),
    ).fetchone()


def _recent_posts(conn, limit: int = 5):
    return conn.execute(
        """
        SELECT
            p.id, p.created_at_utc, p.text, p.type,
            p.manual_confirmation_status,
            pc.pillar, pc.audience, pc.cta
        FROM posts p
        LEFT JOIN post_classifications pc ON pc.post_id = p.id
        WHERE p.created_date = ?
        ORDER BY p.created_at_utc DESC
        LIMIT ?
        """,
        (_date_t.today().isoformat(), limit),
    ).fetchall()


def _current_milestone(conn):
    """The current distribution milestone row (from the `milestones` table)."""
    current_target = get_setting(conn, "current_milestone")
    if current_target is None:
        return None
    return conn.execute(
        "SELECT * FROM milestones WHERE category = 'distribution' AND target_value = ? LIMIT 1",
        (current_target,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
apply_theme()
conn = open_connection()

today = _date_t.today()
today_row = _today_followers_snapshot(conn)
baseline = get_setting(conn, "baseline_followers", 61)

kicker(f"{today.strftime('%A · %B %-d, %Y').upper()} · WEIGH-IN")
st.title("Today")
st.caption(
    "Daily operating cockpit per §14.1. Numbers, not narratives. "
    "Trend judgements live in **Progress**; this view is the morning ritual."
)

# 1. Snapshot form — pinned until today's snapshot exists.
if today_row is None:
    callout(
        "<em>Pin today's snapshot first.</em> The rest of the dashboard "
        "reads from the canonical daily row; without it everything else "
        "below shows yesterday's last-known state."
    )
    snapshot_form.render(conn, key_prefix="today_snapshot")
    st.markdown(
        "<p class='faint'>Once saved, this form collapses. "
        "Use the Manual Entry tab to record additional snapshots or corrections.</p>",
        unsafe_allow_html=True,
    )
else:
    with st.expander("Today's snapshot is logged — view / edit", expanded=False):
        st.markdown(
            f"<span class='numeric'>followers={today_row['followers_count']:,} · "
            f"following={today_row['following_count']:,} · "
            f"posts={today_row['post_count']:,} · "
            f"listed={today_row['listed_count']:,}</span>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Snapshots are immutable per §13 hard rule 2. Use **Manual entry → "
            "Correction** to record a fix; the original row is preserved."
        )

hairline()

# 2. Weigh-in cards.
st.markdown("## Weigh-in")
if today_row is None:
    st.markdown(
        "<p class='faint'>Cards appear once today's snapshot is logged.</p>",
        unsafe_allow_html=True,
    )
else:
    followers = int(today_row["followers_count"])
    delta_yest = today_row["delta_vs_yesterday"]
    delta_base = today_row["delta_vs_baseline"] or 0
    distance_ms = today_row["distance_to_current_milestone"]
    milestone = _current_milestone(conn)
    target = int(get_setting(conn, "current_milestone", 100))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Followers", f"{followers:,}")
    delta_y_value, delta_y_caption = _format_delta(delta_yest)
    # `delta_color="off"` keeps the noise caption styled as a neutral
    # annotation rather than a gain/loss signal.
    c2.metric("Δ yesterday", delta_y_value, delta=delta_y_caption or None, delta_color="off")
    c3.metric("Δ baseline", f"{delta_base:+,}")
    c4.metric(f"To {target}", f"{int(distance_ms or 0):,}" if distance_ms is not None else "—")

    if milestone is not None:
        progress_pct = (
            (followers - int(milestone["start_value"] or baseline))
            / max(1, int(milestone["target_value"] or target) - int(milestone["start_value"] or baseline))
        )
        progress_pct = max(0.0, min(1.0, progress_pct))
        st.markdown(
            f"<div class='kicker'>Distribution milestone · {milestone['name']}</div>",
            unsafe_allow_html=True,
        )
        st.progress(progress_pct, text=f"{progress_pct * 100:.1f}%")

    if today_row["delta_7d"] is None or abs(today_row["delta_7d"]) < 10:
        callout(
            "<em>7-day velocity not yet measurable.</em> Per §13 rule 6, "
            "velocity displays only when |Δ7d| ≥ 10. Judge the week, "
            "not the morning."
        )
    else:
        v7 = today_row["velocity_7d_per_day"]
        st.markdown(
            f"<div class='callout'><em>7-day velocity:</em> "
            f"<span class='numeric'>{v7:+.1f} followers/day</span> over the last week.</div>",
            unsafe_allow_html=True,
        )

hairline()

# Phase 5.9 / §28.17 — content-type recommendation. Surfaces the
# under-represented V/G/P/P slice over the rolling window so Daniel
# sees the gap before he picks today's draft. NOT a daily-cadence
# prescription (the source video pushes V/G/P/P every day; XGrowth
# explicitly rejects that — see §13 hard rule 5).
from app.agent.content_types import (  # noqa: E402 — page-local import
    get_content_type_gaps as _get_ct_gaps,
    get_recommendation_window_days as _get_ct_window,
)
_ct_window = _get_ct_window(conn)
_ct_gap = _get_ct_gaps(conn, window_days=_ct_window)
if _ct_gap["under_represented"]:
    callout(
        f"<em>Today's content-type recommendation:</em> "
        f"<span class='numeric'>{_ct_gap['under_represented']}</span> · "
        f"<span class='faint'>{_ct_gap['rationale']}</span>"
    )
else:
    callout(
        f"<em>Today's content-type recommendation:</em> "
        f"<span class='faint'>{_ct_gap['rationale']}</span>"
    )

hairline()

# 3. Daily reps progress.
st.markdown("## Daily reps")
reps_row = _today_reps(conn)
post_target = int(get_setting(conn, "daily_post_target", 1))
reply_target = int(get_setting(conn, "daily_reply_target", 12))
session_target = int(get_setting(conn, "daily_reply_session_target", 1))

if reps_row is None:
    st.markdown(
        "<p class='faint'>No `daily_activity` row for today yet. "
        "Log it from <strong>Manual entry → Daily reps</strong> "
        "to track adherence.</p>",
        unsafe_allow_html=True,
    )
else:
    posts_shipped = int(reps_row["posts_shipped"] or 0)
    replies_shipped = int(reps_row["replies_shipped"] or 0)
    sessions = int(reps_row["reply_sessions_completed"] or 0)
    minimum_met = int(reps_row["minimum_reps_completed"] or 0)

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Posts", f"{posts_shipped} / {post_target}", delta="✓ target met" if reps_row["post_target_met"] else "")
    r2.metric("Replies", f"{replies_shipped} / {reply_target}", delta="✓ target met" if reps_row["reply_target_met"] else "")
    r3.metric("Sessions", f"{sessions} / {session_target}", delta="✓ target met" if reps_row["session_target_met"] else "")
    r4.metric("Minimum reps", "Complete" if minimum_met else "Incomplete")

    # §29.9 — sub-counters under "Replies today: X / 12". Renders as a
    # dim-mono caption block so the row breathes; matches the rest of the
    # cockpit's "numbers, not narratives" rule.
    high_eng       = int(reps_row["high_engagement_replies_shipped"] or 0)
    icp_intent     = int(reps_row["icp_intent_replies_shipped"]      or 0)
    candidates_rev = int(reps_row["candidates_reviewed_today"]       or 0)
    high_eng_target_pct = float(get_setting(conn, "reply_high_engagement_mix_pct", 0.5))
    cand_target         = int(get_setting(conn, "reply_candidate_review_daily_target", 15))

    high_eng_target = max(1, int(round(high_eng_target_pct * max(1, replies_shipped))))
    high_eng_met = replies_shipped > 0 and high_eng >= high_eng_target

    st.markdown(
        f"""<div style='margin:-0.4rem 0 0.5rem 0;
                       padding:0.5rem 0.85rem;
                       background:{PALETTE['surface']};
                       border-left:2px solid {PALETTE['hairline']};
                       border-radius:2px;'>
            <div class='kicker' style='margin-bottom:0.25rem;'>§29.9 · REPLY-TARGET MIX</div>
            <div class='numeric' style='font-size:0.85rem; color:{PALETTE['bone']};
                                          line-height:1.55;'>
                · <strong>{high_eng}</strong> high-engagement
                  <span class='faint'>(engagement_surface_score ≥ 2)</span>
                  <span class='faint'>· target {int(high_eng_target_pct*100)}% of shipped
                  → {high_eng_target}</span>{' ✓' if high_eng_met else ''}<br/>
                · <strong>{icp_intent}</strong> icp_discovery<br/>
                · <strong>{candidates_rev}</strong> candidates reviewed
                  <span class='faint'>· target {cand_target}</span>
                  {' ✓' if candidates_rev >= cand_target else ''}
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

    if not minimum_met:
        callout(
            "<em>Minimum reps not yet complete.</em> Logging behavior is "
            "the only signal you control today; impressions and follower "
            "movement are downstream."
        )

hairline()

# 3.5 Pending agent drafts (today) — Phase 5.8 / §28.11. Surfaces any
# agent_drafts proposed today with their pre-publish composite_label chip.
# Empty rendering when there are none — informational only, never gates.
_pending_drafts = conn.execute(
    """
    SELECT ad.id, ad.text, ad.draft_kind, ad.created_at,
           ad.similarity_warning_json,
           ps.composite_label
    FROM agent_drafts ad
    LEFT JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
    WHERE date(ad.created_at) = date('now')
      AND ad.status = 'proposed'
    ORDER BY ad.id DESC
    LIMIT 5
    """
).fetchall()
if _pending_drafts:
    from app.components.badges import prepublish_chip as _today_prepublish_chip
    from app.components.badges import repetition_banner as _today_repetition_banner
    st.markdown("## Pending agent drafts")
    st.caption(
        "Agent-generated drafts from today's sessions that haven't been "
        "accepted, rejected, or shipped yet. The chip is the §28.11 "
        "pre-publish read — informational, never gates Publish."
    )
    for _d in _pending_drafts:
        _preview = (_d["text"] or "").strip().replace("\n", " ")
        if len(_preview) > 160:
            _preview = _preview[:157] + "…"
        st.markdown(
            f"<div style='padding: 0.5rem 0; border-bottom: 1px solid "
            f"{PALETTE['hairline']};'>"
            f"<div class='numeric' style='font-size: 0.78rem; color: "
            f"{PALETTE['bone_dim']};'>draft #{_d['id']} · "
            f"{html.escape(str(_d['draft_kind']))}</div>"
            f"<div style='margin-top: 0.25rem; color: {PALETTE['bone']};'>"
            f"{html.escape(_preview)}</div></div>",
            unsafe_allow_html=True,
        )
        _today_repetition_banner(_d["similarity_warning_json"])
        _today_prepublish_chip(_d["composite_label"])
    hairline()

# 4. Recent activity.
st.markdown("## Recent activity")
recent = _recent_posts(conn)
if not recent:
    st.markdown(
        "<p class='faint'>No posts logged today yet. "
        "Use <strong>Manual entry → Post / Reply</strong> to log one.</p>",
        unsafe_allow_html=True,
    )
else:
    for row in recent:
        confirm = row["manual_confirmation_status"]
        confirm_color = {
            "confirmed":     PALETTE["confidence_confident_bg"],
            "needs_id":      PALETTE["confidence_directional_bg"],
            "needs_metrics": PALETTE["confidence_directional_bg"],
            "draft":         PALETTE["confidence_insufficient_bg"],
        }.get(confirm, PALETTE["surface_raised"])
        lane = (
            f"{row['pillar']} · {row['audience']} · {row['cta']}"
            if row["pillar"] else "<span class='faint'>(unclassified)</span>"
        )
        text_preview = (row["text"] or "").strip().replace("\n", " ")
        if len(text_preview) > 120:
            text_preview = text_preview[:117] + "…"
        st.markdown(
            f"""<div style='padding: 0.6rem 0; border-bottom: 1px solid {PALETTE['hairline']};'>
                <div style='display:flex; justify-content:space-between; align-items:baseline;'>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']};'>
                        {row['type']} · {lane}
                    </span>
                    <span style='font-family:JetBrains Mono,monospace; font-size:0.7rem;
                                  letter-spacing:0.06em; text-transform:uppercase;
                                  background:{confirm_color}; color:{PALETTE['ink']};
                                  padding:1px 6px; border-radius:2px;'>{confirm}</span>
                </div>
                <div style='margin-top:0.25rem; color:{PALETTE['bone']};'>{text_preview}</div>
            </div>""",
            unsafe_allow_html=True,
        )

hairline()

# 5. Quick links — buttons set the active-tab hint and switch to Manual Entry.
st.markdown("## Quick actions")
b1, b2, b3 = st.columns(3)

# Each button: clicking sets the active-tab session-state hint and
# navigates to the Manual Entry page in one click. `st.switch_page`
# (Streamlit 1.30+) handles the navigation; the hint primes the right
# tab once Manual Entry boots.
if b1.button("Log a post", width="stretch"):
    st.session_state.manual_entry_active_tab = "Post / Reply"
    st.switch_page("pages/8_Manual_Entry.py")
if b2.button("Classify untagged", width="stretch"):
    st.session_state.manual_entry_active_tab = "Needs tagging"
    st.switch_page("pages/8_Manual_Entry.py")
if b3.button("Log Stir tester", width="stretch"):
    st.session_state.manual_entry_active_tab = "Tester"
    st.switch_page("pages/8_Manual_Entry.py")

st.markdown(
    "<p class='faint'>Each button jumps straight to the Manual Entry page "
    "with the right tab pre-flagged.</p>",
    unsafe_allow_html=True,
)

# Agent integration (§14.1 + §28.7). Sets a context seed and jumps to chat.
hairline()
st.markdown("### Ask the agent")
ag_a, ag_b = st.columns(2)
if ag_a.button("draft today's post →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "today_draft"
    st.switch_page("pages/9_Agent_Chat.py")
if ag_b.button("start reply session →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "today_reply_session"
    st.switch_page("pages/9_Agent_Chat.py")
