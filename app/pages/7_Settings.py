"""Settings — spec.md §14.7.

Surfaces every settings key from §10.2, grouped per §14.7 section. Phase 2
deliberately surfaced only the Phase-2 subset; Phase 3 expands to every
seeded key. Read-only keys (schema_version, db_path, last_backup_at) are
shown but not editable. Configurable keys persist on click via
``set_setting()``.

The dual-ladder milestone summary at the bottom is read-only — milestones
are managed via the `milestones` table (seeded by
``scripts/seed_milestones.py``) and become editable in V1.1+ per §10.2.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.components.theme import PALETTE, apply_theme, hairline, kicker
from app.db import DEFAULT_DB_PATH
from app.forms import get_setting, set_setting
from app.pages import open_connection

# ---------------------------------------------------------------------------
# Group definitions. Each entry: (group label, [(key, editable, helptext)]).
# Keys missing from `settings` raise a warning so the user knows the seed
# script needs a re-run.
# ---------------------------------------------------------------------------
_GROUPS: list[tuple[str, list[tuple[str, bool, str]]]] = [
    (
        "Account",
        [
            ("x_handle", True, "Public X handle without @ (§2)."),
            ("x_user_id", True, "Stable X user identifier; populated once known."),
            ("profile_url", True, "Public profile URL."),
            ("baseline_followers", True, "Followers at project start (§2)."),
            ("timezone", True, "Daily snapshot ritual timezone (§14.7)."),
            ("daily_snapshot_time", True, "Default snapshot capture time (§14.7)."),
        ],
    ),
    (
        "Goals",
        [
            ("operational_ceiling", True, "Operational anchor (default 5,000) (§27)."),
            ("long_arc_reminder", True, "Display-only long-arc reminder (§27)."),
            ("current_milestone", True, "Active distribution-ladder target."),
        ],
    ),
    (
        "Daily reps",
        [
            ("daily_post_target", True, "Posts/day target (§14.1)."),
            ("daily_reply_target", True, "Replies/day target (default 12, experimental)."),
            ("daily_reply_session_target", True, "Reply sessions/day target (§14.1)."),
            ("target_calibration_review_date", True, "Review reply-target adherence on this date."),
        ],
    ),
    (
        "Accuracy thresholds",
        [
            ("lane_sample_size_insufficient", True, "post_count<X → insufficient (§11)."),
            ("lane_sample_size_low", True, "post_count<X → low / scatter-only (§11)."),
            ("lane_sample_size_stronger", True, "post_count≥X AND days≥14 → confident (§11)."),
            ("lane_days_covered_minimum", True, "days_covered<X → insufficient (§11)."),
            ("velocity_7d_display_threshold", True, "|Δ7d|≥X required to show velocity (§13)."),
            ("counterfactual_required", True, "Weekly review blocks export until counterfactual filled (§14.6)."),
        ],
    ),
    (
        "Data sources",
        [
            ("data_collection_mode", True, "manual | xurl | api — MVP default per §17."),
        ],
    ),
    (
        "Exports & backups",
        [
            ("backup_dir", True, "VACUUM INTO target directory (§18 rule 10)."),
            ("export_dir", True, "CSV/Markdown export output folder (§14.7)."),
            ("weekly_report_export_path", True, "Folder for Markdown weekly reports (§14.7)."),
        ],
    ),
]


# Read-only environment values surfaced for visibility.
def _readonly_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    schema = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0]
    return [
        ("db_path", str(DEFAULT_DB_PATH)),
        ("schema_migrations_applied", str(schema)),
    ]


def _setting_row(conn, key: str):
    return conn.execute(
        "SELECT key, value_json, note, updated_at FROM settings WHERE key = ?",
        (key,),
    ).fetchone()


def _render_setting(conn, key: str, editable: bool, helptext: str) -> None:
    row = _setting_row(conn, key)
    if row is None:
        st.warning(f"Setting `{key}` missing — re-run `scripts.seed_settings`.")
        return
    current = json.loads(row["value_json"])

    # Label row with the key (mono) + helptext.
    st.markdown(
        f"""<div style='display:flex; justify-content:space-between; align-items:baseline;
                          padding-top:0.5rem;'>
            <span class='numeric' style='font-size:0.92rem; color:{PALETTE['bone']};'>
                {key}
            </span>
            <span class='faint' style='font-size:0.78rem;'>updated {row['updated_at']}</span>
        </div>
        <p class='faint' style='margin:0.1rem 0 0.4rem 0; font-size:0.82rem;'>{helptext}</p>""",
        unsafe_allow_html=True,
    )

    if not editable:
        st.markdown(
            f"<span class='numeric' style='color:{PALETTE['bone_dim']};'>{current!r}</span>"
            f" <span class='faint'>· read-only</span>",
            unsafe_allow_html=True,
        )
        return

    # isinstance(True, int) is True; check bool first.
    if isinstance(current, bool):
        new_value: object = st.toggle("", value=current, key=f"set_{key}", label_visibility="collapsed")
    elif isinstance(current, int):
        new_value = int(
            st.number_input(
                "",
                value=current,
                step=1,
                key=f"set_{key}",
                label_visibility="collapsed",
            )
        )
    else:
        new_value = st.text_input(
            "",
            value="" if current is None else str(current),
            key=f"set_{key}",
            label_visibility="collapsed",
        )

    save = st.button("Save", key=f"save_{key}")
    if save:
        try:
            # Treat blank text → None for nullable text fields.
            payload = None if (isinstance(new_value, str) and new_value.strip() == "") else new_value
            set_setting(conn, key, payload)
        except Exception as exc:  # noqa: BLE001 — surface in UI
            st.error(f"Save failed: {exc}")
            return
        st.toast(f"{key} saved.")
        st.rerun()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
apply_theme()
conn = open_connection()

kicker("CONFIG · §14.7")
st.title("Settings")
st.caption(
    "Every settings key from §10.2 grouped by §14.7 section. Configurable "
    "keys save on click; read-only keys are surfaced for visibility but "
    "not editable from the UI."
)

for group_label, keys in _GROUPS:
    st.markdown(f"## {group_label}")
    for key, editable, helptext in keys:
        _render_setting(conn, key, editable, helptext)
    hairline()

# Read-only environment surface.
st.markdown("## Environment (read-only)")
for label, value in _readonly_rows(conn):
    st.markdown(
        f"""<div style='display:flex; justify-content:space-between; padding:0.3rem 0;
                         border-bottom:1px solid {PALETTE['hairline']};'>
            <span class='numeric' style='color:{PALETTE['bone']};'>{label}</span>
            <span class='numeric' style='color:{PALETTE['bone_dim']};'>{value}</span>
        </div>""",
        unsafe_allow_html=True,
    )

hairline()

# Milestone read-only summary.
st.markdown("## Milestones (read-only at MVP)")
st.caption(
    "Milestones are seeded by `scripts/seed_milestones.py` and become "
    "editable in V1.1+ per §10.2. The dual-ladder structure is fixed at MVP."
)
ms_rows = conn.execute(
    """
    SELECT category, ladder_position, name, start_value, target_value, status
    FROM milestones
    ORDER BY category ASC, ladder_position ASC
    """
).fetchall()
by_cat: dict[str, list] = {}
for r in ms_rows:
    by_cat.setdefault(r["category"], []).append(r)

ladders_left, ladders_right = st.columns(2)
for col, category in zip([ladders_left, ladders_right], ["distribution", "validation"]):
    with col:
        st.markdown(f"**{category.capitalize()}**")
        for m in by_cat.get(category, []):
            target = f"{m['target_value']:,}" if m["target_value"] else "—"
            st.markdown(
                f"<span class='numeric' style='font-size:0.84rem; color:{PALETTE['bone']};'>"
                f"#{m['ladder_position']} {m['name']} · target {target} · {m['status']}"
                f"</span>",
                unsafe_allow_html=True,
            )
