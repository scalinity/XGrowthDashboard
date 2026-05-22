"""Next rep — spec.md §14.2.

Closes the loop between measurement and the daily generative act. The view
surfaces the lane Daniel is under-sampling, the open hypotheses needing
data, and the §29 Reply Target Queue's top candidates — windowed onto the
canonical queue per §29.2, never a parallel list.
"""

from __future__ import annotations

import html
import sys
from datetime import date as _date_t
from datetime import timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent.reply_targets import engagement_footnote as _engagement_footnote
from app.agent.tools import _load_engagement_surface_settings
from app.components.badges.sample_size import sample_size_badge
from app.components.theme import (
    PALETTE,
    apply_theme,
    callout,
    hairline,
    kicker,
    recommended_action_badge,
    recommended_action_keyline_color,
    score_bank,
)
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
    # `experiments.content_lane` is interpreted as the pillar. When
    # `target_audience` is also set, narrow to posts matching BOTH pillar
    # AND audience — otherwise a "stir × icp" experiment would silently
    # count every "stir × other" post toward its sample.
    if exp["target_audience"]:
        sql = (
            "SELECT COUNT(*) FROM v_post_latest_metrics plm "
            "JOIN posts p ON p.id = plm.post_id "
            "WHERE plm.pillar = ? AND plm.audience = ? AND p.created_date >= ?"
        )
        params = (exp["content_lane"], exp["target_audience"], exp["start_date"])
    else:
        sql = (
            "SELECT COUNT(*) FROM v_post_latest_metrics plm "
            "JOIN posts p ON p.id = plm.post_id "
            "WHERE plm.pillar = ? AND p.created_date >= ?"
        )
        params = (exp["content_lane"], exp["start_date"])
    n = conn.execute(sql, params).fetchone()[0]
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

# Reply-target panel — §14.2 + §29.2. Windowed view onto reply_targets.
st.markdown("## Reply targets")
st.caption(
    "Top candidates from the Reply Target Queue. Filtered to the biggest-gap "
    "pillar above when computable. §29.2 — one source of truth, not a "
    "parallel list."
)

# Best-effort pillar bias: pick the pillar from the biggest gap above.
_biggest_pillar = None
if coverage:
    _biggest_pillar = coverage[0][0][0]  # (pillar, audience, cta)[pillar]

# Window onto the queue: top 5 candidates sorted by recommended_action_score.
_window_sql = (
    "SELECT * FROM reply_targets "
    "WHERE status = 'candidate' "
    "AND (? IS NULL OR pillar IS NULL OR pillar = ?) "
    "ORDER BY COALESCE(recommended_action_score, -1) DESC, "
    "         last_checked_at_utc DESC "
    "LIMIT 5"
)
_window_rows = conn.execute(_window_sql, (_biggest_pillar, _biggest_pillar)).fetchall()

if not _window_rows:
    st.markdown(
        f"""<div style='border:1px dashed {PALETTE['hairline']};
                         padding:1rem 1.2rem; border-radius:3px;
                         background:{PALETTE['surface']};'>
            <p style='margin:0; color:{PALETTE['bone']};'>
                No candidates yet — <em>add one from the queue</em>.
            </p>
        </div>""",
        unsafe_allow_html=True,
    )
    if st.button("Open Reply Target Queue →", key="next_rep_open_queue_empty"):
        st.switch_page("pages/10_Reply_Target_Queue.py")
else:
    for _r in _window_rows:
        _handle = (_r["target_author_handle"] or "unknown").lstrip("@")
        _keyline = recommended_action_keyline_color(_r["recommended_action_label"])
        _text_excerpt = (_r["target_text"] or "").strip().replace("\n", " ")
        if len(_text_excerpt) > 80:
            _text_excerpt = _text_excerpt[:79] + "…"
        if not _text_excerpt:
            _text_excerpt = "<span class='faint'>(no target text saved)</span>"
        # /review-2 🟡 #1 — also label when the floor is binding for a small
        # author, not just when the follower count is unknown.
        _eng_footnote = _engagement_footnote(
            _r["target_author_follower_count"],
            _load_engagement_surface_settings(conn),
        )
        st.markdown(
            f"""<div style='border-left:3px solid {_keyline};
                            padding:0.55rem 0.85rem 0.45rem 0.85rem;
                            margin:0.45rem 0 0.15rem 0;
                            background:{PALETTE['surface']};
                            border-radius:2px;'>
                <div style='display:flex; justify-content:space-between; align-items:baseline;'>
                    <span style='color:{PALETTE['bone']}; font-weight:500;
                                  font-family: "IBM Plex Sans", sans-serif;'>@{_handle}</span>
                    <span class='numeric' style='font-size:0.75rem; color:{PALETTE['bone_faint']};'>
                        #{int(_r['id'])}
                    </span>
                </div>
                <div style='margin-top:0.2rem; color:{PALETTE['bone']};
                            font-family: "IBM Plex Sans", sans-serif; line-height:1.35;
                            font-size:0.9rem;'>
                    {_text_excerpt}
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
        score_bank(
            _r["relevance_score"],
            _r["engagement_surface_score"],
            _r["saturation_score"],
            _r["reply_opportunity_score"],
            engagement_footnote=_eng_footnote,
        )
        st.markdown(
            f"<div style='margin:-0.15rem 0 0.65rem 0;'>"
            f"{recommended_action_badge(_r['recommended_action_label'])}"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        f"<p class='faint' style='margin-top:0.5rem;'>Showing top "
        f"<span class='numeric'>{len(_window_rows)}</span> candidates"
        + (
            f" in pillar <span class='numeric'>{_biggest_pillar}</span>."
            if _biggest_pillar
            else "."
        )
        + "</p>",
        unsafe_allow_html=True,
    )
    if st.button("See full queue →", key="next_rep_open_queue"):
        st.switch_page("pages/10_Reply_Target_Queue.py")

# Account leads — Phase 5.5 surfaces curated accounts from agent_target_accounts.
if _agent_target_accounts_available(conn):
    _lead_rows = conn.execute(
        """
        SELECT x_handle, display_name, lane, priority, notes
        FROM agent_target_accounts
        WHERE is_active = 1
        ORDER BY priority ASC, last_engaged_at ASC NULLS FIRST
        LIMIT 8
        """
    ).fetchall()
    if _lead_rows:
        st.markdown("### Account leads")
        for _r in _lead_rows:
            st.markdown(
                f"<div style='border-left: 2px solid {PALETTE['phosphor']}; "
                f"padding: 0.3rem 0.7rem; margin: 0.2rem 0; background: {PALETTE['surface']};'>"
                f"<span class='numeric' style='color: {PALETTE['bone']};'>@{_r['x_handle']}</span> "
                f"<span class='faint' style='font-size: 0.78rem;'>"
                f"· {_r['lane'] or '—'} · priority {_r['priority']}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            "<p class='faint' style='margin-top:0.8rem;'>"
            "<strong>Account leads:</strong> no curated accounts yet — "
            "add some in Settings → Growth Agent.</p>",
            unsafe_allow_html=True,
        )

# Pending agent drafts (Phase 5.8 / §28.11) — surfaces composite_label chips
# for any agent_drafts that are still 'proposed', so Daniel can see read
# quality for each lane-related draft without leaving Next Rep.
_pending = conn.execute(
    """
    SELECT ad.id, ad.text, ad.draft_kind, ad.pillar,
           ad.similarity_warning_json,
           ps.composite_label
    FROM agent_drafts ad
    LEFT JOIN prepublish_scores ps ON ps.id = ad.prepublish_score_id
    WHERE ad.status = 'proposed'
    ORDER BY ad.id DESC
    LIMIT 5
    """
).fetchall()
if _pending:
    from app.components.badges import prepublish_chip as _next_rep_prepublish_chip
    from app.components.badges import repetition_banner as _next_rep_repetition_banner
    hairline()
    st.markdown("### Pending agent drafts")
    st.caption(
        "Drafts the agent has proposed but you haven't shipped or rejected. "
        "Chip is the §28.11 pre-publish read; click into Agent Chat to "
        "publish or revise."
    )
    for _d in _pending:
        _text = (_d["text"] or "").strip().replace("\n", " ")
        if len(_text) > 140:
            _text = _text[:137] + "…"
        st.markdown(
            f"<div style='padding: 0.4rem 0; border-bottom: 1px solid "
            f"{PALETTE['hairline']};'>"
            f"<span class='numeric' style='font-size: 0.78rem; color: "
            f"{PALETTE['bone_dim']};'>draft #{_d['id']} · "
            f"{html.escape(str(_d['draft_kind']))} · "
            f"{html.escape(str(_d['pillar'] or '—'))}</span>"
            f"<div style='margin-top: 0.25rem; color: {PALETTE['bone']};'>"
            f"{html.escape(_text)}</div></div>",
            unsafe_allow_html=True,
        )
        _next_rep_repetition_banner(_d["similarity_warning_json"])
        _next_rep_prepublish_chip(_d["composite_label"])

# Agent integration (§14.2 + §28.7).
hairline()
st.markdown("### Ask the agent")
ag_a, ag_b = st.columns(2)
if ag_a.button("draft for this lane →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "next_rep_lane_gap"
    st.switch_page("pages/9_Agent_Chat.py")
if ag_b.button("score reply candidates →", width="stretch"):
    st.session_state.agent_conversation_id = None
    st.session_state.agent_context_seed = "next_rep_score_reply_candidates"
    st.switch_page("pages/9_Agent_Chat.py")
