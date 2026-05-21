"""Settings — Phase 2 scope per phase-2-manual-workflows.md.

Surfaces the Phase 2 subset of seeded keys:

- Daily reps target (post / reply / reply-session)
- Sample-size thresholds for confidence labels
- Velocity-suppression window
- Calibration window date
- Counterfactual-required toggle
- Backup directory path
- Export directory path

Agent + X-posting settings rows are deliberately not surfaced (their backing
code paths land in later phases — exposing them now would mis-document state).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.forms import get_setting, set_setting
from app.pages import open_connection

st.title("Settings")
st.caption(
    "Phase 2 — only settings whose semantics already exist. Each save is a "
    "single row UPSERT (`settings.value_json`). Agent + X-API rows surface in "
    "later phases."
)

conn = open_connection()

PHASE_2_SETTINGS_KEYS = [
    "daily_post_target",
    "daily_reply_target",
    "daily_reply_session_target",
    "target_calibration_review_date",
    "lane_sample_size_insufficient",
    "lane_sample_size_low",
    "lane_sample_size_stronger",
    "lane_days_covered_minimum",
    "velocity_7d_display_threshold",
    "counterfactual_required",
    "backup_dir",
    "export_dir",
]


def _row(conn, key: str):
    return conn.execute(
        "SELECT key, value_json, note, updated_at FROM settings WHERE key = ?",
        (key,),
    ).fetchone()


for key in PHASE_2_SETTINGS_KEYS:
    row = _row(conn, key)
    if row is None:
        st.warning(f"Setting `{key}` missing — re-run `scripts.seed_settings`.")
        continue
    current = json.loads(row["value_json"])
    with st.expander(f"`{key}` — {row['note']}", expanded=False):
        st.caption(f"Last updated: {row['updated_at']}")
        if isinstance(current, bool):
            new_value: object = st.toggle(
                f"{key} value", value=current, key=f"set_{key}"
            )
        elif isinstance(current, int):
            new_value = st.number_input(
                f"{key} value", value=current, step=1, key=f"set_{key}"
            )
        else:
            new_value = st.text_input(
                f"{key} value",
                value="" if current is None else str(current),
                key=f"set_{key}",
            )
        if st.button("Save", key=f"save_{key}", type="primary"):
            try:
                set_setting(conn, key, new_value)
            except Exception as exc:  # noqa: BLE001 - surface to UI
                st.error(f"Save failed: {exc}")
                continue
            st.success(f"`{key}` saved.")
            st.rerun()
