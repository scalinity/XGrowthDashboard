"""Weekly Review — Phase 2 hosts the form; Phase 3 adds the auto-filled summary."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.forms import weekly_review
from app.pages import open_connection

st.title("Weekly review")
st.caption(
    "Spec §15.5. This phase ships the form; Phase 3 auto-fills the "
    "quantitative summary fields from `v_account_daily` and `v_daily_reps`."
)

conn = open_connection()
weekly_review.render(conn)
