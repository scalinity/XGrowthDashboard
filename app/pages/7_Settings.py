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
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.components.theme import PALETTE, apply_theme, hairline, kicker
from app.db import DEFAULT_DB_PATH
from app.forms import get_setting, set_setting
from app.pages import open_connection
from app.backup import (
    BACKUP_FILENAME_GLOB,
    BackupIntegrityError,
    DEFAULT_BACKUPS_DIR,
    DEFAULT_RETENTION_DAYS,
    backup_database,
)

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

# ---------------------------------------------------------------------------
# Backups — instrument-panel sub-readout (Phase 4, §18 rule 10).
#
# Aesthetic discipline (per /frontend-design + project CLAUDE.md):
#   - `kicker()` to brand the sub-panel with the spec anchor.
#   - one tight readout block: big mono timestamp + de-emphasised caption.
#   - action + parameter live as paired columns (button | dial), echoing a
#     physical instrument console.
#   - the on-disk list reads as a console manifest: mono columns, right-
#     aligned size/mtime, hairline separators only.
# No new PALETTE keys; no new fonts.
# ---------------------------------------------------------------------------


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _humanise_age(seconds: float) -> str:
    """Compact `Nh Mm ago` / `Nd ago` caption for the last-backup readout."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        h, m = divmod(seconds // 60, 60)
        return f"{h}h {m}m ago"
    days = seconds // 86400
    return f"{days}d ago"


def _backups_dir_from_settings() -> Path:
    seeded = get_setting(conn, "backup_dir", default=str(DEFAULT_BACKUPS_DIR))
    return Path(seeded).resolve() if seeded else DEFAULT_BACKUPS_DIR.resolve()


def _list_backups(backups_dir: Path) -> list[tuple[Path, int, float]]:
    if not backups_dir.exists():
        return []
    items: list[tuple[Path, int, float]] = []
    for path in backups_dir.glob(BACKUP_FILENAME_GLOB):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        items.append((path, stat.st_size, stat.st_mtime))
    items.sort(key=lambda r: r[2], reverse=True)
    return items


kicker("DATA INTEGRITY · §18 RULE 10")
st.markdown("## Backups")
st.caption(
    "VACUUM INTO snapshots of the SQLite DB — the only safe way to copy "
    "an open SQLite file. Backups live next to the DB and prune themselves "
    "per the retention dial."
)

# --- Status readout: big mono timestamp + caption (or dimmed placeholder).
_last_backup = get_setting(conn, "last_backup_at_utc")
_age_caption = ""
if _last_backup:
    try:
        _parsed = datetime.strptime(_last_backup, "%Y-%m-%dT%H:%M:%SZ")
        _age_caption = _humanise_age(
            (datetime.utcnow() - _parsed).total_seconds()
        )
    except ValueError:
        _age_caption = ""

if _last_backup:
    st.markdown(
        f"""<div style='padding:0.6rem 0.9rem; margin:0.4rem 0 0.8rem 0;
                       background:{PALETTE['surface']}; border-left:2px solid {PALETTE['phosphor']};
                       border-radius:2px;'>
            <div class='faint' style='font-size:0.72rem; letter-spacing:0.08em;
                                       text-transform:uppercase; color:{PALETTE['bone_faint']};'>
                Last backup
            </div>
            <div class='numeric' style='font-size:1.25rem; color:{PALETTE['bone']};
                                          margin-top:0.15rem;'>
                {_last_backup}
            </div>
            <div class='faint' style='font-size:0.78rem; color:{PALETTE['bone_dim']};
                                       margin-top:0.1rem;'>
                {_age_caption}
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f"""<div style='padding:0.6rem 0.9rem; margin:0.4rem 0 0.8rem 0;
                       background:{PALETTE['surface']}; border-left:2px dashed {PALETTE['hairline']};
                       border-radius:2px;'>
            <div class='faint' style='font-size:0.72rem; letter-spacing:0.08em;
                                       text-transform:uppercase; color:{PALETTE['bone_faint']};'>
                Last backup
            </div>
            <div class='numeric' style='font-size:1.25rem; color:{PALETTE['bone_dim']};
                                          margin-top:0.15rem;'>
                —
            </div>
            <div class='faint' style='font-size:0.78rem; color:{PALETTE['bone_faint']};
                                       margin-top:0.1rem;'>
                No backups yet · click below to run the first one.
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

# --- Action + parameter row: button (primary) | retention dial (parameter).
_action_col, _retention_col = st.columns([1, 1], gap="large")

with _action_col:
    if st.button("Back up now", key="run_backup_now", type="primary"):
        with st.spinner("VACUUM INTO + integrity check…"):
            try:
                result = backup_database()
            except (BackupIntegrityError, RuntimeError, FileNotFoundError) as exc:
                st.error(f"Backup failed: {exc}")
            else:
                st.toast(
                    f"Backup written · {result.path.name} "
                    f"({_format_bytes(result.size_bytes)}, {result.duration_ms} ms)",
                    icon="✅",
                )
                st.rerun()

with _retention_col:
    _retention_row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'backup_retention_days'"
    ).fetchone()
    _current_retention = (
        int(json.loads(_retention_row["value_json"])) if _retention_row else DEFAULT_RETENTION_DAYS
    )
    _new_retention = st.number_input(
        "Retention · days",
        min_value=1,
        max_value=3650,
        value=_current_retention,
        step=1,
        key="set_backup_retention_days",
        help="Backups older than this many days are deleted at the end of each backup run.",
    )
    if st.button("Save retention", key="save_backup_retention_days"):
        try:
            set_setting(conn, "backup_retention_days", int(_new_retention))
        except Exception as exc:  # noqa: BLE001 — surface to UI
            st.error(f"Save failed: {exc}")
        else:
            st.toast("Retention saved.", icon="✅")
            st.rerun()

# --- On-disk manifest: console-log columns inside an expander.
_backups_dir = _backups_dir_from_settings()
_backups = _list_backups(_backups_dir)
with st.expander(f"Manifest · {len(_backups)} on disk"):
    if not _backups:
        st.markdown(
            f"<span class='faint' style='color:{PALETTE['bone_dim']};'>"
            f"No files matching <code>{BACKUP_FILENAME_GLOB}</code> "
            f"in <code>{_backups_dir}</code>.</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""<div style='display:grid; grid-template-columns:1fr auto auto;
                            gap:1.2rem; padding:0.3rem 0;
                            border-bottom:1px solid {PALETTE['hairline']};'>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']};'>
                    file
                </span>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']};
                                            text-align:right;'>
                    size
                </span>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']};
                                            text-align:right;'>
                    written
                </span>
            </div>""",
            unsafe_allow_html=True,
        )
        for path, size, mtime in _backups:
            when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d · %H:%M:%S")
            st.markdown(
                f"""<div style='display:grid; grid-template-columns:1fr auto auto;
                                gap:1.2rem; padding:0.28rem 0;
                                border-bottom:1px solid {PALETTE['hairline']};'>
                    <span class='numeric' style='font-size:0.82rem;
                                                  color:{PALETTE['bone']};
                                                  overflow:hidden; text-overflow:ellipsis;'>
                        {path.name}
                    </span>
                    <span class='numeric' style='font-size:0.78rem;
                                                  color:{PALETTE['bone_dim']};
                                                  text-align:right;'>
                        {_format_bytes(size)}
                    </span>
                    <span class='numeric' style='font-size:0.78rem;
                                                  color:{PALETTE['bone_dim']};
                                                  text-align:right;'>
                        {when}
                    </span>
                </div>""",
                unsafe_allow_html=True,
            )

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
