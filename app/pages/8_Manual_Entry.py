"""Agent Ops — automation cockpit replacing the old manual-entry hub."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.agent.tools import _find_reply_targets, _score_reply_candidates
from app.components.theme import apply_theme, callout, hairline, kicker
from app.forms import queues
from app.jobs import post_classification_sync, x_activity_sync
from app.jobs.grok_discovery_sweep import format_sweep_summary_for_ui, run as run_grok_sweep
from app.pages import open_connection

st.title("Agent Ops")
apply_theme()
st.caption(
    "The old manual journal is now an automation cockpit: sync X activity, "
    "classify imported posts, run Grok discovery, and score reply work."
)

conn = open_connection()


def _remember(kind: str, message: str) -> None:
    st.session_state[f"agent_ops_{kind}"] = message


for _kind, _renderer in (("success", st.success), ("warning", st.warning), ("error", st.error)):
    _key = f"agent_ops_{_kind}"
    if _key in st.session_state:
        _renderer(st.session_state.pop(_key))

needs_tagging = queues.needs_tagging(conn)
needs_post_id = queues.needs_post_id(conn)
queue_debt = len(needs_tagging) + len(needs_post_id)

callout(
    "Run the collectors first, then let the agent resolve and score the work. "
    "Publishing still requires the explicit per-post confirmation flow."
)

m1, m2, m3 = st.columns(3)
m1.metric("Automation debt", queue_debt)
m2.metric("Needs tags", len(needs_tagging))
m3.metric("Needs X ID", len(needs_post_id))

hairline()
st.markdown("## Collectors")

c1, c2, c3 = st.columns(3)
if c1.button("Sync X activity", type="primary", width="stretch"):
    try:
        with st.spinner("Syncing X activity…"):
            summary = x_activity_sync.run(conn)
        activity = summary["activity"]["daily_activity"]
        warnings = summary.get("warnings") or []
        _remember(
            "warning" if warnings else "success",
            "X sync complete: "
            f"+{summary['import_posts']['posts_inserted']} imported, "
            f"{summary['metrics']['posts_refreshed']} metrics refreshed, "
            f"reps {activity['posts_shipped']}/{activity['replies_shipped']}/"
            f"{activity['quotes_shipped']}."
            + (" Notes: " + " · ".join(warnings) if warnings else ""),
        )
    except Exception as exc:  # noqa: BLE001
        _remember("error", f"X activity sync failed: {type(exc).__name__}: {exc}")
    st.rerun()

if c2.button("Classify queue", width="stretch"):
    try:
        with st.spinner("Classifying imported posts…"):
            result = post_classification_sync.run(conn)
        _remember(
            "warning" if result["errors"] else "success",
            f"Classified {result['classified_count']}/{result['considered']} untagged posts.",
        )
    except Exception as exc:  # noqa: BLE001
        _remember("error", f"Classification failed: {type(exc).__name__}: {exc}")
    st.rerun()

if c3.button("Run Grok sweep", width="stretch"):
    try:
        with st.spinner("Running Grok sweep…"):
            summary = run_grok_sweep(conn)
        severity, message = format_sweep_summary_for_ui(summary)
        _remember("warning" if severity == "warning" else "success", message)
    except Exception as exc:  # noqa: BLE001
        _remember("error", f"Grok sweep failed: {type(exc).__name__}: {exc}")
    st.rerun()

st.markdown("## Agent work")
a1, a2, a3 = st.columns(3)
if a1.button("Find reply targets", type="primary", width="stretch"):
    try:
        with st.spinner("Finding reply targets…"):
            _find_reply_targets(conn)
        _remember("success", "Reply target discovery completed.")
    except Exception as exc:  # noqa: BLE001
        _remember("error", f"Reply discovery failed: {type(exc).__name__}: {exc}")
    st.rerun()

if a2.button("Score candidate queue", width="stretch"):
    try:
        with st.spinner("Scoring reply candidates…"):
            _score_reply_candidates(conn)
        _remember("success", "Candidate scoring completed.")
    except Exception as exc:  # noqa: BLE001
        _remember("error", f"Candidate scoring failed: {type(exc).__name__}: {exc}")
    st.rerun()

if a3.button("Open Agent Chat", width="stretch"):
    st.switch_page("pages/9_Agent_Chat.py")

hairline()
st.markdown("## Automation debt")

if not needs_tagging and not needs_post_id:
    st.success("No manual cleanup queue right now.")
else:
    if needs_tagging:
        kicker("Needs classification")
        st.dataframe(needs_tagging, width="stretch", hide_index=True)
    if needs_post_id:
        kicker("Needs X post ID")
        st.dataframe(needs_post_id, width="stretch", hide_index=True)

hairline()
st.markdown("## Related surfaces")
r1, r2, r3, r4 = st.columns(4)
if r1.button("Reply Queue", width="stretch"):
    st.switch_page("pages/10_Reply_Target_Queue.py")
if r2.button("Brain Dump", width="stretch"):
    st.switch_page("pages/11_Brain_Dump.py")
if r3.button("Account Researcher", width="stretch"):
    st.switch_page("pages/13_Account_Researcher.py")
if r4.button("Settings", width="stretch"):
    st.switch_page("pages/7_Settings.py")
