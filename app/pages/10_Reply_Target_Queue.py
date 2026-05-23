"""Reply Target Queue — spec.md §29.7.

The ninth and final MVP view (§19). The Queue is where Daniel reviews
candidate posts to reply *under*. Each row carries:

* The four §29.3 dimension scores rendered as a four-strip score bank.
* The deterministic recommended_action label (§29.3 resolver).
* Per-row operations: Open original / Draft reply / Skip / Mark posted.

The view also hosts the "Add candidate" form (manual URL paste + optional
metric snapshot) and a sticky banner for §29.11 stale-drafted rows.

Design language: instrument-panel readouts. The score bank component lives
in ``app.components.theme`` next to ``iwh_meter`` because it shares the
same step-color ladder discipline (§28.2 IWH meter pattern, §29.3 here).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent.reply_targets import (
    REPLY_INTENT_ENUM,
    SKIP_REASON_ENUM,
    engagement_footnote as _engagement_footnote,
)
from app.agent import audit_log
from app.agent.tools import (
    _load_engagement_surface_settings,
    _parse_x_post_id,
    _record_reply_target,
    _score_reply_candidates,
)
from app.components.badges.grok_semantic import (
    render_grok_badge_html as _render_grok_badge_html,
)
from app.components.theme import (
    PALETTE,
    apply_theme,
    callout,
    hairline,
    kicker,
    readout_card,
    recommended_action_badge,
    recommended_action_keyline_color,
    score_bank,
)
from app.db import transaction
from app.jobs.reply_target_maintenance import (
    expire_stale_candidates,
    stale_drafted_candidates,
)
from app.pages import UNSELECTED, open_connection


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _counters(conn: sqlite3.Connection) -> dict[str, int]:
    """Header counter strip — candidates / drafted / posted today / skipped today."""
    rows = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'candidate'                              THEN 1 ELSE 0 END) AS candidates,
            SUM(CASE WHEN status = 'drafted'                                THEN 1 ELSE 0 END) AS drafted,
            SUM(CASE WHEN status = 'posted'
                      AND DATE(last_checked_at_utc) = DATE('now')           THEN 1 ELSE 0 END) AS posted_today,
            SUM(CASE WHEN status = 'skipped'
                      AND DATE(last_checked_at_utc) = DATE('now')           THEN 1 ELSE 0 END) AS skipped_today
        FROM reply_targets
        """
    ).fetchone()
    return {
        "candidates":    int(rows["candidates"]    or 0),
        "drafted":       int(rows["drafted"]       or 0),
        "posted_today":  int(rows["posted_today"]  or 0),
        "skipped_today": int(rows["skipped_today"] or 0),
    }


def _query_rows(
    conn: sqlite3.Connection,
    *,
    status: str,
    pillar: str | None,
    reply_intent: str | None,
    recommended_action: str | None,
    author: str | None,
    discovered_via: str | None = None,
) -> list[sqlite3.Row]:
    """Apply the filter bar — every filter is optional.

    Phase 9 adds ``discovered_via`` so Daniel can filter the Queue by
    discovery source (manual / agent_score / next_rep_seed /
    v1.1_api_search / grok_semantic). Composes cumulatively with the
    other filters.
    """
    sql = "SELECT * FROM reply_targets WHERE 1=1"
    params: list[object] = []
    if status and status != "all":
        sql += " AND status = ?"
        params.append(status)
    if pillar:
        sql += " AND pillar = ?"
        params.append(pillar)
    if reply_intent:
        # /review-2 🔵 #3 — reply_intent is set at draft/post time per §29.5,
        # so most fresh candidates carry NULL. Hiding untagged rows when
        # Daniel filters by intent makes the queue feel emptier than it is;
        # treat the filter as "matches this intent OR is still untagged".
        sql += " AND (reply_intent = ? OR reply_intent IS NULL)"
        params.append(reply_intent)
    if recommended_action:
        sql += " AND recommended_action_label = ?"
        params.append(recommended_action)
    if discovered_via:
        sql += " AND discovered_via = ?"
        params.append(discovered_via)
    if author:
        # /review-2 🟡 #6 — user-typed `%` and `_` would otherwise become LIKE
        # wildcards (`100_users` matches `100Xusers`). Escape them. The value
        # is already a bind parameter, so this is a UX surprise, not SQL
        # injection.
        raw = author.strip().lstrip("@")
        escaped = (
            raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        sql += r" AND target_author_handle LIKE ? ESCAPE '\'"
        params.append(f"%{escaped}%")
    sql += " ORDER BY COALESCE(recommended_action_score, -1) DESC, last_checked_at_utc DESC"
    return conn.execute(sql, params).fetchall()


def _row_age(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """Render a short relative-age label for a row (e.g. "74 min ago")."""
    out = conn.execute(
        "SELECT CAST((julianday('now') - julianday(?)) * 1440 AS INTEGER) AS m",
        (row["discovered_at_utc"],),
    ).fetchone()
    m = int(out["m"] or 0)
    if m < 60:
        return f"{m} min ago"
    h = m // 60
    if h < 48:
        return f"{h} h ago"
    return f"{h // 24} d ago"


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = s.strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
apply_theme()
conn = open_connection()

# Boot-time maintenance — idempotent. §29.11 expiry transition.
_expired = expire_stale_candidates(conn)
_stale_drafted_rows = stale_drafted_candidates(conn)

kicker("§29 · INSTRUMENT 9 / 9 · REPLY TARGET QUEUE")
st.title("Reply target queue")
st.caption(
    "Candidates to reply *under*, scored on four dimensions per §29.3. "
    "The recommended action is deterministic from the scores — no hidden "
    "composite. Sort order is "
    "reply_now → reply_if_time → consider → skip, then by recency."
)

# ---------------------------------------------------------------------------
# Stale-drafted banner — §29.11 row 3.
# ---------------------------------------------------------------------------
if _stale_drafted_rows:
    n = len(_stale_drafted_rows)
    st.markdown(
        f"""<div style='border-left:2px solid {PALETTE['warn_amber']};
                       background:{PALETTE['surface']};
                       padding:0.65rem 0.9rem; margin:0.5rem 0 1rem 0;
                       border-radius:2px;'>
            <div class='kicker' style='color:{PALETTE['warn_amber']};'>
                STALE DRAFTED — §29.11
            </div>
            <div style='margin-top:0.2rem; color:{PALETTE['bone']};'>
                {n} candidate{'s' if n != 1 else ''} sitting in
                <span class='numeric'>drafted</span> for more than 24h.
                Did you post them? Use <em>Mark posted</em> on each row to
                record the URL, or <em>Skip</em> to close them out.
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Counter strip
# ---------------------------------------------------------------------------
counts = _counters(conn)
c1, c2, c3, c4 = st.columns(4)
with c1:
    readout_card("Candidates", str(counts["candidates"]))
with c2:
    readout_card("Drafted", str(counts["drafted"]), accent="phosphor_dim")
with c3:
    readout_card("Posted · today", str(counts["posted_today"]), accent="phosphor")
with c4:
    readout_card(
        "Skipped · today",
        str(counts["skipped_today"]),
        accent="bone_dim",
        empty=(counts["skipped_today"] == 0),
    )

# ---------------------------------------------------------------------------
# Filter bar — five horizontal dropdowns.
# ---------------------------------------------------------------------------
st.markdown(
    "<div class='kicker' style='margin-top:0.4rem;'>FILTERS</div>",
    unsafe_allow_html=True,
)
f1, f2, f3, f4, f5, f6 = st.columns(6)
status_options = ["all", "candidate", "drafted", "posted", "skipped", "expired", "target_deleted"]
flt_status = f1.selectbox(
    "status", status_options, index=1, key="rtq_filter_status", label_visibility="visible"
)
pillar_options = [UNSELECTED, "stir", "build", "self"]
flt_pillar_raw = f2.selectbox(
    "pillar", pillar_options, index=0, key="rtq_filter_pillar"
)
intent_options = [UNSELECTED, *REPLY_INTENT_ENUM]
flt_intent_raw = f3.selectbox(
    "reply intent", intent_options, index=0, key="rtq_filter_intent"
)
action_options = [UNSELECTED, "reply_now", "reply_if_time", "consider", "skip"]
flt_action_raw = f4.selectbox(
    "recommended action", action_options, index=0, key="rtq_filter_action"
)
# Phase 9: discovered_via filter per §29.7. Options match the
# reply_targets.discovered_via CHECK constraint (migration 021 added
# 'grok_semantic'). UNSELECTED is the "(all)" default per §29.12.
discovered_via_options = [
    UNSELECTED, "manual", "agent_score", "next_rep_seed",
    "v1.1_api_search", "grok_semantic",
]
flt_discovered_via_raw = f5.selectbox(
    "discovered via", discovered_via_options, index=0, key="rtq_filter_discovered_via"
)
flt_author = f6.text_input("author handle", "", placeholder="@handle", key="rtq_filter_author")

flt_pillar = None if flt_pillar_raw == UNSELECTED else flt_pillar_raw
flt_intent = None if flt_intent_raw == UNSELECTED else flt_intent_raw
flt_action = None if flt_action_raw == UNSELECTED else flt_action_raw
flt_discovered_via = (
    None if flt_discovered_via_raw == UNSELECTED else flt_discovered_via_raw
)

# ---------------------------------------------------------------------------
# Add candidate — collapsible expander; the Queue is for review, not capture.
# ---------------------------------------------------------------------------
with st.expander("＋  add candidate (paste URL)", expanded=False):
    with st.form("rtq_add_candidate", clear_on_submit=True):
        url = st.text_input(
            "target post URL",
            placeholder="https://x.com/{handle}/status/{id}",
        )
        ac_a, ac_b = st.columns(2)
        author_handle = ac_a.text_input("author handle", placeholder="@handle (optional)")
        author_followers = ac_b.number_input(
            "author follower count (optional)", min_value=0, value=0, step=100
        )
        target_text = st.text_area(
            "target text (optional)",
            placeholder="paste the post text so the score rationale has context",
            height=80,
        )
        m1, m2, m3 = st.columns(3)
        like_count = m1.number_input("likes", min_value=0, value=0, step=1)
        reply_count = m2.number_input("replies", min_value=0, value=0, step=1)
        repost_count = m3.number_input("reposts", min_value=0, value=0, step=1)
        p1, p2 = st.columns(2)
        c_pillar = p1.selectbox(
            "pillar (optional)", [UNSELECTED, "stir", "build", "self"], index=0
        )
        c_intent = p2.selectbox(
            "reply intent (optional)", [UNSELECTED, *REPLY_INTENT_ENUM], index=0
        )

        submitted = st.form_submit_button("Add to queue", width="stretch")
        if submitted:
            url_clean = (url or "").strip()
            if not url_clean:
                st.error("URL is required.")
            else:
                parsed_handle = ""
                p = urlparse(url_clean)
                if p.netloc.endswith(("x.com", "twitter.com")) and "/status/" in p.path:
                    parsed_handle = p.path.split("/")[1] if len(p.path.split("/")) > 1 else ""
                final_handle = (author_handle.strip().lstrip("@") or parsed_handle or "unknown")
                # Duplicate-URL guard surfaces the existing row id (§29.11 row 6).
                already = conn.execute(
                    "SELECT id FROM reply_targets WHERE target_post_url = ?",
                    (url_clean,),
                ).fetchone()
                if already:
                    st.warning(
                        f"Already in queue (id {already['id']}). "
                        "Use the filter bar above to find the existing row."
                    )
                else:
                    # /review-2 🟡 #3 — `or None` coerces a real 0 to NULL.
                    # A candidate genuinely at 0 likes / 0 replies is data,
                    # not "no value provided". Only the author follower
                    # count uses the `0 → None` mapping because 0 followers
                    # is functionally unknown (and the number_input's default
                    # is 0).
                    rec = _record_reply_target(
                        conn,
                        target_post_url=url_clean,
                        target_post_text=target_text or None,
                        target_user=final_handle,
                        target_author_follower_count=(
                            int(author_followers) if author_followers else None
                        ),
                        like_count=int(like_count),
                        reply_count=int(reply_count),
                        repost_count=int(repost_count),
                        pillar=None if c_pillar == UNSELECTED else c_pillar,
                        reply_intent=None if c_intent == UNSELECTED else c_intent,
                        discovered_via="manual",
                    )
                    rt_id = rec.get("reply_target_id")
                    if rt_id:
                        # Auto-score on save (no agent judgment yet — relevance
                        # and reply_opportunity stay NULL until Daniel or the
                        # agent supplies them).
                        _score_reply_candidates(conn, reply_target_id=rt_id)
                        st.success(f"Added candidate #{rt_id}.")
                        st.rerun()
                    else:
                        st.error(f"Could not record: {rec.get('error', 'unknown')}")

# ---------------------------------------------------------------------------
# Phase 5.9 / §28.20 — Add replier pool (paste flow).
# ---------------------------------------------------------------------------
from app.agent import replier_pool as _replier_pool  # noqa: E402 — page-local

with st.expander(
    "＋  add replier pool (paste big-account thread + reply excerpts)",
    expanded=False,
):
    st.caption(
        "The third discovery path (§28.20): niche-relevant audiences "
        "cluster in the reply sections of big accounts. Paste the "
        "thread URL plus replier handles or '@handle: excerpt' lines "
        "(one per line; blank-line-separated for multi-line excerpts). "
        "Each replier is scored deterministically against your niche "
        "definition; candidates land with source='replier_under_thread'."
    )
    with st.form("rtq_replier_pool", clear_on_submit=True):
        rp_thread_url = st.text_input(
            "thread URL (the big-account post you're mining)",
            placeholder="https://x.com/{handle}/status/{id}",
        )
        rp_payload = st.text_area(
            "replier handles / excerpts",
            height=180,
            placeholder=(
                "@firstreplier\n"
                "@secondreplier: A short excerpt of what they said.\n"
                "\n"
                "@thirdreplier:\n"
                "Multi-line excerpt — pasted from the thread.\n"
                "Second line of the same excerpt."
            ),
        )
        rp_lookback = st.number_input(
            "lookback minutes (V1.1+ uses this; MVP records for calibration)",
            min_value=1, max_value=60 * 24, value=60, step=15,
        )
        if st.form_submit_button("Score replier pool", width="stretch"):
            url_clean = (rp_thread_url or "").strip()
            payload_clean = (rp_payload or "").strip()
            if not url_clean:
                st.error("Thread URL is required.")
            elif not payload_clean:
                st.error("Replier handles / excerpts payload is required.")
            else:
                result = _replier_pool.score_replier_pool(
                    conn,
                    thread_url=url_clean,
                    replier_handles_or_excerpts=payload_clean,
                    lookback_minutes=int(rp_lookback),
                )
                if "error" in result:
                    st.error(result["error"])
                else:
                    st.success(
                        f"Scored {len(result['candidates'])} replier(s) — "
                        f"{result['created_count']} new, "
                        f"{result['updated_count']} updated."
                    )
                    st.rerun()

# ---------------------------------------------------------------------------
# Candidate rows
# ---------------------------------------------------------------------------
hairline()

rows = _query_rows(
    conn,
    status=flt_status,
    pillar=flt_pillar,
    reply_intent=flt_intent,
    recommended_action=flt_action,
    author=flt_author,
    discovered_via=flt_discovered_via,
)

if not rows:
    callout(
        "<em>No rows for these filters.</em> Loosen the status filter to "
        "<span class='numeric'>all</span> or drop the author search to see "
        "everything."
    )

for row in rows:
    rt_id = int(row["id"])
    age = _row_age(conn, row)
    handle = (row["target_author_handle"] or "unknown").lstrip("@")
    keyline = recommended_action_keyline_color(row["recommended_action_label"])
    # Phase 9 §29.7 grok_semantic badge — surfaces when Grok firehose
    # discovery (§29.12) inserted this row. P9R-47: badge HTML lives in
    # app/components/badges/grok_semantic.py so it can be unit-tested.
    _grok_badge_html = _render_grok_badge_html(row["discovered_via"])
    # /review-2 🟡 #1 — also label when the absolute floor (rather than the
    # %-of-followers calc) is the binding threshold; a 200-follower author's
    # pct calc rounds below the 15-likes floor, so the floor wins silently.
    eng_footnote = _engagement_footnote(
        row["target_author_follower_count"],
        _load_engagement_surface_settings(conn),
    )

    # Card surface: keyline color matches the action ladder. Strikethrough
    # is reserved for skipped rows (the badge already renders strike).
    st.markdown(
        f"""<div style='border-left:3px solid {keyline};
                        padding:0.7rem 0.95rem 0.5rem 0.95rem;
                        margin:0.6rem 0;
                        background:{PALETTE['surface']};
                        border-radius:2px;'>
            <div style='display:flex; justify-content:space-between; align-items:baseline;'>
                <span style='color:{PALETTE['bone']}; font-weight:500;
                              font-family: "IBM Plex Sans", sans-serif;'>@{handle}{_grok_badge_html}</span>
                <span class='numeric' style='font-size:0.75rem; color:{PALETTE['bone_faint']};'>
                    {age} · #{rt_id}
                </span>
            </div>
            <div style='margin:0.3rem 0 0.2rem 0; color:{PALETTE['bone']};
                         font-family: "IBM Plex Sans", sans-serif; line-height:1.4;'>
                {_truncate(row['target_text'], 220) or "<span class='faint'>(no target text saved)</span>"}
            </div>
            <div class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']};
                                          margin-top:0.2rem;'>
                {row['like_count'] or 0} likes · {row['reply_count'] or 0} replies ·
                {row['repost_count'] or 0} reposts · velocity: —
                <span class='faint'>(V1.1)</span>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
    # Score bank (rendered after the card surface so the gap between scores
    # and metrics breathes; Streamlit doesn't let us embed it inline).
    score_bank(
        row["relevance_score"],
        row["engagement_surface_score"],
        row["saturation_score"],
        row["reply_opportunity_score"],
        engagement_footnote=eng_footnote,
    )
    # Recommended-action ribbon + pillar/intent.
    pillar_text = row["pillar"] or "—"
    intent_text = row["reply_intent"] or "—"
    st.markdown(
        f"""<div style='display:flex; align-items:baseline; gap:0.6rem;
                        margin:0 0 0.35rem 0;'>
            {recommended_action_badge(row['recommended_action_label'])}
            <span class='faint' style='font-size:0.78rem;'>
                pillar = <span class='numeric'>{pillar_text}</span> ·
                intent = <span class='numeric'>{intent_text}</span>
            </span>
        </div>""",
        unsafe_allow_html=True,
    )
    if row["score_rationale"]:
        st.markdown(
            f"<div style='color:{PALETTE['bone_dim']}; font-style:italic; "
            f"font-family: \"IBM Plex Sans\", sans-serif; font-size:0.88rem; "
            f"margin:0.05rem 0 0.45rem 0;'>"
            f"{_truncate(row['score_rationale'], 320)}</div>",
            unsafe_allow_html=True,
        )

    # ----- §29.10 lint-blocked banner (Phase 7) -----
    # When lint_blocked=1 (set by tool #6 score_reply_candidates after
    # the thread-classifier lint fires), the row's "Draft reply" button
    # is disabled and the lint rationale + category surface as a
    # warning. Daniel can override via the "Force-draft" affordance.
    lint_blocked = bool(row["lint_blocked"]) if "lint_blocked" in row.keys() else False
    lint_category = row["lint_category"] if "lint_category" in row.keys() else None
    if lint_blocked:
        lint_classification_raw = (
            row["lint_thread_classification_json"]
            if "lint_thread_classification_json" in row.keys() else None
        )
        lint_rationale = ""
        if lint_classification_raw:
            try:
                import json as _json
                _classif = _json.loads(lint_classification_raw)
                lint_rationale = str(_classif.get("rationale") or "")
            except (_json.JSONDecodeError, TypeError, ValueError):
                lint_rationale = ""
        # Phase 10 / §29.7 — tighter badge format: "Lint: <category>"
        # instead of "Lint blocked — <category>". The category itself
        # is the failure mode (ragebait / hijacking_required / etc.)
        # so Daniel sees the *reason* immediately rather than the
        # state ("blocked") + reason. Force-draft override flow is
        # unchanged.
        _badge_category = lint_category or "unknown"
        st.markdown(
            f"<div style='border-left:3px solid {PALETTE['flag_amber']};"
            f"padding:0.55rem 0.85rem;margin:0.4rem 0 0.5rem 0;"
            f"background:{PALETTE.get('bg_soft', '#1b1a18')};color:{PALETTE['text']};"
            f"font-family:\"IBM Plex Sans\", sans-serif;'>"
            f"<strong style='color:{PALETTE['flag_amber']};'>Lint: {_badge_category}</strong><br>"
            f"<span style='color:{PALETTE.get('text_muted', PALETTE['text'])};'>{lint_rationale or '(no rationale recorded)'}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Per-row actions.
    a1, a2, a3, a4 = st.columns([1.1, 1.1, 1.1, 1.4])
    with a1:
        st.link_button("Open original", row["target_post_url"], width="stretch")
    with a2:
        if lint_blocked:
            # Disabled-state Draft reply + dedicated Force-draft affordance.
            st.button(
                "Draft reply",
                key=f"rtq_draft_{rt_id}",
                width="stretch",
                disabled=True,
                help=f"Lint: {lint_category}. Use Force-draft to override.",
            )
            force_key = f"rtq_force_draft_open_{rt_id}"
            if st.button(
                "Force-draft (overrides lint)",
                key=f"rtq_force_btn_{rt_id}",
                width="stretch",
            ):
                st.session_state[force_key] = True
            if st.session_state.get(force_key):
                with st.form(f"rtq_force_form_{rt_id}"):
                    reason = st.text_input(
                        "Why are you overriding the lint? (required)",
                        key=f"rtq_force_reason_{rt_id}",
                        placeholder="Briefly explain the rationale Daniel saw the lint missed",
                    )
                    col_x, col_y = st.columns(2)
                    confirm = col_x.form_submit_button("Override and draft")
                    cancel = col_y.form_submit_button("Cancel")
                    if confirm:
                        clean_reason = (reason or "").strip()
                        if not clean_reason:
                            st.error(
                                "A non-empty reason is required to override the lint."
                            )
                        else:
                            with transaction(conn):
                                conn.execute(
                                    """
                                    UPDATE reply_targets
                                       SET force_drafted = 1,
                                           force_drafted_reason = ?
                                     WHERE id = ?
                                    """,
                                    (clean_reason, rt_id),
                                )
                                audit_log.log(
                                    conn,
                                    event_category="data",
                                    event_type="lint_force_drafted",
                                    target_type="reply_target",
                                    target_id=str(rt_id),
                                    details={
                                        "reply_target_id": rt_id,
                                        "lint_category": lint_category,
                                        "reason": clean_reason,
                                    },
                                    success=True,
                                )
                            st.session_state.pop(force_key, None)
                            st.session_state.agent_conversation_id = None
                            st.session_state.agent_context_seed = (
                                "reply_target_queue_draft"
                            )
                            st.session_state.agent_pre_armed_reply_target_id = rt_id
                            st.switch_page("pages/9_Agent_Chat.py")
                    if cancel:
                        st.session_state.pop(force_key, None)
                        st.rerun()
        else:
            if st.button("Draft reply", key=f"rtq_draft_{rt_id}", width="stretch"):
                st.session_state.agent_conversation_id = None
                st.session_state.agent_context_seed = "reply_target_queue_draft"
                st.session_state.agent_pre_armed_reply_target_id = rt_id
                st.switch_page("pages/9_Agent_Chat.py")
    with a3:
        skip_key = f"rtq_skip_open_{rt_id}"
        if st.button("Skip", key=f"rtq_skip_btn_{rt_id}", width="stretch"):
            st.session_state[skip_key] = True
        if st.session_state.get(skip_key):
            with st.form(f"rtq_skip_form_{rt_id}"):
                reason = st.selectbox(
                    "skip reason", SKIP_REASON_ENUM, key=f"rtq_skip_reason_{rt_id}"
                )
                col_x, col_y = st.columns(2)
                confirm = col_x.form_submit_button("Confirm skip")
                cancel = col_y.form_submit_button("Cancel")
                if confirm:
                    with transaction(conn):
                        conn.execute(
                            """
                            UPDATE reply_targets
                            SET status = 'skipped',
                                skip_reason = ?,
                                last_checked_at_utc = datetime('now')
                            WHERE id = ?
                            """,
                            (reason, rt_id),
                        )
                    st.session_state.pop(skip_key, None)
                    st.rerun()
                if cancel:
                    st.session_state.pop(skip_key, None)
                    st.rerun()
    with a4:
        mp_key = f"rtq_mp_open_{rt_id}"
        if st.button("Mark posted", key=f"rtq_mp_btn_{rt_id}", width="stretch"):
            st.session_state[mp_key] = True
        if st.session_state.get(mp_key):
            with st.form(f"rtq_mp_form_{rt_id}"):
                posted_url = st.text_input(
                    "posted reply URL",
                    placeholder="https://x.com/{your_handle}/status/{id}",
                    key=f"rtq_mp_url_{rt_id}",
                )
                # /review-2 🔴 #1 — without this field the INSERT below was
                # writing the *target* post's text into posts.text, masquerading
                # the original author's words as Daniel's reply. The reply
                # text is now a required input.
                reply_text = st.text_area(
                    "your reply text (as posted)",
                    placeholder="paste the reply you actually posted",
                    key=f"rtq_mp_text_{rt_id}",
                    height=120,
                )
                mp_intent_default = list(REPLY_INTENT_ENUM).index(row["reply_intent"]) \
                    if row["reply_intent"] in REPLY_INTENT_ENUM else 0
                mp_intent = st.selectbox(
                    "reply intent (locked in on the posted row)",
                    REPLY_INTENT_ENUM,
                    index=mp_intent_default,
                    key=f"rtq_mp_intent_{rt_id}",
                )
                col_x, col_y = st.columns(2)
                confirm = col_x.form_submit_button("Record posted")
                cancel = col_y.form_submit_button("Cancel")
                if confirm:
                    posted_url_clean = (posted_url or "").strip()
                    reply_text_clean = (reply_text or "").strip()
                    if not posted_url_clean:
                        st.error("Posted URL is required.")
                    elif not reply_text_clean:
                        st.error(
                            "Reply text is required — paste the reply you posted "
                            "on X. Without it, downstream views would read the "
                            "target's words as yours."
                        )
                    else:
                        # §29.11 — atomic three-write transaction:
                        #   1. INSERT posts row (with Daniel's REPLY text — not the target)
                        #   2. UPDATE reply_targets.status='posted' + posted_reply_post_id
                        #   3. (the post row carries in_reply_to_reply_target_id)
                        try:
                            with transaction(conn):
                                x_id = _parse_x_post_id(posted_url_clean)
                                cur = conn.execute(
                                    """
                                    INSERT INTO posts
                                        (created_at_utc, created_date, text, type,
                                         posted_via, manual_confirmation_status,
                                         x_post_id, url, in_reply_to_reply_target_id,
                                         reply_intent)
                                    VALUES
                                        (datetime('now'), date('now'), ?, 'reply',
                                         'manual', 'needs_metrics', ?, ?, ?, ?)
                                    """,
                                    (
                                        reply_text_clean,
                                        x_id,
                                        posted_url_clean,
                                        rt_id,
                                        mp_intent,
                                    ),
                                )
                                new_post_id = int(cur.lastrowid)
                                conn.execute(
                                    """
                                    UPDATE reply_targets
                                    SET status = 'posted',
                                        posted_reply_post_id = ?,
                                        reply_intent = ?,
                                        last_checked_at_utc = datetime('now')
                                    WHERE id = ?
                                    """,
                                    (new_post_id, mp_intent, rt_id),
                                )
                            st.session_state.pop(mp_key, None)
                            st.success(
                                f"Marked posted (post #{new_post_id} ↔ candidate #{rt_id})."
                            )
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Mark-posted failed (rolled back): {exc}")
                if cancel:
                    st.session_state.pop(mp_key, None)
                    st.rerun()

    hairline()
