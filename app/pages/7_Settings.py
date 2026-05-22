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
from datetime import date, datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.components.theme import PALETTE, apply_theme, hairline, kicker, readout_card
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
from app.exports import (
    ALLOWLISTS,
    CounterfactualMissingError,
    export_database_to_json,
    export_table_to_csv,
    export_weekly_report,
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


_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 60 * _SECONDS_PER_MINUTE
_SECONDS_PER_DAY = 24 * _SECONDS_PER_HOUR


def _humanise_age(seconds: float) -> str:
    """Compact `Nh Mm ago` / `Nd ago` caption for the last-backup readout."""
    seconds = max(0, int(seconds))
    if seconds < 2:
        return "just now"
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds}s ago"
    if seconds < _SECONDS_PER_HOUR:
        return f"{seconds // _SECONDS_PER_MINUTE}m ago"
    if seconds < _SECONDS_PER_DAY:
        h, m = divmod(seconds // _SECONDS_PER_MINUTE, 60)
        return f"{h}h {m}m ago"
    days = seconds // _SECONDS_PER_DAY
    return f"{days}d ago"


def _backups_dir_from_settings() -> Path:
    """Resolve ``settings.backup_dir`` against PROJECT_ROOT, not CWD.

    Mirrors ``app.backup._anchor_on_project_root`` so the manifest expander
    points at the same directory the runner writes to, regardless of the
    Streamlit process's CWD at boot.
    """
    from app.db import PROJECT_ROOT

    seeded = get_setting(conn, "backup_dir", default=str(DEFAULT_BACKUPS_DIR))
    path = Path(seeded) if seeded else DEFAULT_BACKUPS_DIR
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


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
        # Attach tzinfo so the subtraction below is timezone-aware on both
        # sides; the rest of the codebase already uses datetime.now(timezone.utc)
        # (see app/forms/__init__.py:51 and app/backup.py:_now_utc_iso).
        # datetime.utcnow() is deprecated in Python 3.12+ and the previous
        # naive-vs-naive subtraction only worked by coincidence.
        _parsed = datetime.strptime(
            _last_backup, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        _age_caption = _humanise_age(
            (datetime.now(timezone.utc) - _parsed).total_seconds()
        )
    except ValueError:
        # Surface a corrupted setting rather than hiding behind a blank
        # caption. The raw value still renders below; the caption tells
        # the user the timestamp didn't parse so they know to inspect
        # settings.value_json.
        _age_caption = "(unparseable timestamp)"
        st.warning(
            f"`last_backup_at_utc` value `{_last_backup!r}` could not be "
            f"parsed as ISO-8601 UTC. Either re-run a backup or edit the "
            f"row directly."
        )

if _last_backup:
    readout_card(
        label="Last backup",
        value=_last_backup,
        caption=_age_caption,
        accent="phosphor",
    )
else:
    readout_card(
        label="Last backup",
        value="—",
        caption="No backups yet · click below to run the first one.",
        empty=True,
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
# The expander's open/closed state is pinned in session_state so the
# panel doesn't snap closed after a "Back up now" rerun — Streamlit
# reruns the whole script top-to-bottom, and st.expander by default
# resets to its initial `expanded` value on each rerun.
if "backups_manifest_open" not in st.session_state:
    st.session_state.backups_manifest_open = False

_backups_dir = _backups_dir_from_settings()
_backups = _list_backups(_backups_dir)
with st.expander(
    f"Manifest · {len(_backups)} on disk",
    expanded=st.session_state.backups_manifest_open,
):
    # Streamlit doesn't expose an open/closed callback on st.expander
    # itself, so we approximate persistence: render a small "keep open
    # after the next rerun" toggle inside the expander. Checking it once
    # is enough — the toggle's session_state key feeds back into
    # `expanded=` above on the next render.
    st.checkbox(
        "Keep open across reruns",
        key="backups_manifest_open",
        help="If checked, this Manifest panel stays open after every "
             "rerun (e.g. after running 'Back up now').",
    )
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

# ---------------------------------------------------------------------------
# Exports — Phase 5 instrument-panel sub-readout (§16, §14.6, §18).
#
# Aesthetic discipline mirrors the Backups section above:
#   - `kicker()` brands with the spec anchor.
#   - per-export-kind cards with paired button + parameter where applicable.
#   - a console-log manifest for the audit table.
# No new PALETTE keys or fonts; reuse the locked instrument-panel tokens.
# ---------------------------------------------------------------------------


def _exports_dir_from_settings() -> Path:
    """Resolve the export folder against PROJECT_ROOT — same pattern as backups."""
    from app.db import PROJECT_ROOT

    seeded = get_setting(conn, "export_dir", default="data/exports")
    # /review-2 W3: get_setting JSON-decodes value_json — coerce to str
    # before Path() so a hand-edited non-string value doesn't TypeError.
    path = Path(str(seeded)) if seeded else Path("data/exports")
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _current_iso_week() -> str:
    today = date.today()
    year, week, _day = today.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _counterfactual_filled_for_week(week_iso: str) -> tuple[bool, str | None]:
    """Return (ready, week_start_date) — `ready` is True when a saved
    weekly_reviews row for the week has a non-blank counterfactual_note.

    Used to enable/disable the export button BEFORE the user clicks it.
    """
    try:
        from app.exports.markdown_weekly import _iso_week_to_dates

        monday, _sunday = _iso_week_to_dates(week_iso)
    except ValueError:
        return (False, None)
    week_start = monday.isoformat()
    row = conn.execute(
        "SELECT counterfactual_note FROM weekly_reviews WHERE week_start_date = ?",
        (week_start,),
    ).fetchone()
    if row is None:
        return (False, week_start)
    note = row["counterfactual_note"]
    if note is None or not str(note).strip():
        return (False, week_start)
    return (True, week_start)


kicker("DATA EXPORTS · §16")
st.markdown("## Exports")
st.caption(
    "Three formats: per-table CSV (column allowlist), Markdown weekly review "
    "(gated by the counterfactual note — §14.6), and a raw JSON archive with "
    "secret redaction (§18). Output files land under `export_dir`; the "
    "Markdown weekly report uses `weekly_report_export_path`."
)

readout_card(
    label="Export folder",
    value=str(_exports_dir_from_settings()),
    caption="Configure via the `export_dir` setting above.",
    accent="phosphor",
)

# --- CSV section -----------------------------------------------------------
st.markdown("### Per-table CSV")
st.caption(
    "Adding a new column to a table does NOT auto-include it — the allowlist "
    "in `app/exports/allowlists.py` is the canonical surface."
)
_csv_table_col, _csv_optin_col = st.columns([2, 1], gap="large")
with _csv_table_col:
    _csv_table = st.selectbox(
        "Table",
        options=sorted(ALLOWLISTS.keys()),
        index=sorted(ALLOWLISTS.keys()).index("posts"),
        key="export_csv_table",
        help="Each table has its own column allowlist in app/exports/allowlists.py.",
    )
with _csv_optin_col:
    _csv_opt_in = st.checkbox(
        "Include opt-in columns",
        value=False,
        key="export_csv_opt_in",
        help=(
            "Opt-in columns are documented sensitive fields that ride along "
            "only when explicitly requested. Empty in MVP; Phase 5.5 populates "
            "them for posts."
        ),
    )

if st.button("Export CSV", key="run_export_csv", type="primary"):
    output = _exports_dir_from_settings() / f"{_csv_table}.csv"
    with st.spinner("Writing CSV…"):
        try:
            result = export_table_to_csv(
                _csv_table, output, include_opt_in=_csv_opt_in, conn=conn,
            )
        except Exception as exc:  # noqa: BLE001 — surface to UI
            st.error(f"CSV export failed: {exc}")
        else:
            st.toast(
                f"CSV · {result.table_name} · {result.row_count} rows → "
                f"{result.path.name}",
                icon="✅",
            )
            st.rerun()

hairline()

# --- Markdown weekly section ----------------------------------------------
st.markdown("### Markdown weekly review")
_default_week = _current_iso_week()
_md_col_a, _md_col_b = st.columns([2, 1], gap="large")
with _md_col_a:
    _week_iso = st.text_input(
        "ISO week",
        value=_default_week,
        key="export_weekly_iso",
        help="Format `YYYY-Www` (e.g. 2026-W21). Defaults to the current ISO week.",
    )
with _md_col_b:
    st.caption(
        "The report is BLOCKED until the counterfactual_note for the week is "
        "filled in via the Weekly Review form. This is intentional (§14.6)."
    )

_ready, _week_start = _counterfactual_filled_for_week(_week_iso)
_disabled_help = (
    "Counterfactual note for that week is empty. Open Weekly Review and fill it in first."
    if not _ready
    else None
)
if st.button(
    "Export Markdown weekly",
    key="run_export_weekly",
    type="primary",
    disabled=not _ready,
    help=_disabled_help,
):
    with st.spinner("Rendering Markdown report…"):
        try:
            result = export_weekly_report(_week_iso, conn=conn)
        except CounterfactualMissingError as exc:
            st.error(str(exc))
        except ValueError as exc:
            st.error(f"Invalid week: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Markdown export failed: {exc}")
        else:
            st.toast(
                f"Markdown · {result.week_iso} → {result.path.name} "
                f"({result.byte_count:,} bytes)",
                icon="✅",
            )
            st.rerun()

if not _ready and _week_start is not None:
    st.markdown(
        f"<span class='faint' style='color:{PALETTE['bone_faint']};'>"
        f"No saved counterfactual note for week of <code>{_week_start}</code>."
        f"</span>",
        unsafe_allow_html=True,
    )

hairline()

# --- Raw JSON section -----------------------------------------------------
st.markdown("### Raw JSON archive")
st.caption(
    "Dumps every table to a single JSON document. Redacts column names "
    "matching `*_token`, `*_key`, `*_secret`, plus Authorization-style headers "
    "inside `raw_api_responses` blobs. Tester PII is excluded by default."
)
_json_confirm_col, _json_pii_col = st.columns([1, 1], gap="large")
with _json_confirm_col:
    _json_confirm = st.checkbox(
        "I understand this dumps everything",
        value=False,
        key="export_json_confirm",
    )
with _json_pii_col:
    _json_pii = st.checkbox(
        "Include stir_testers PII",
        value=False,
        key="export_json_pii",
        help=(
            "Off by default per §18 rules 4-6. Use only when archiving locally "
            "and never sharing the resulting file."
        ),
    )

_json_disabled_help = (
    None if _json_confirm else "Check the confirmation box above to enable the JSON dump."
)
if st.button(
    "Export raw JSON",
    key="run_export_json",
    type="primary",
    disabled=not _json_confirm,
    help=_json_disabled_help,
):
    output = _exports_dir_from_settings() / f"x_growth_dump_{_current_iso_week()}.json"
    with st.spinner("Writing JSON dump…"):
        try:
            result = export_database_to_json(
                output, conn=conn, include_stir_pii=_json_pii,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"JSON export failed: {exc}")
        else:
            total = sum(result.table_row_counts.values())
            st.toast(
                f"JSON · {result.path.name} · {total:,} rows across "
                f"{len(result.table_row_counts)} tables · "
                f"{len(result.redactions)} redactions",
                icon="✅",
            )
            st.rerun()

# --- Recent exports manifest ----------------------------------------------
if "exports_manifest_open" not in st.session_state:
    st.session_state.exports_manifest_open = False

_recent = conn.execute(
    """
    SELECT exported_at_utc, kind, table_name, output_path, row_count, include_opt_in, notes
      FROM data_exports
     ORDER BY id DESC
     LIMIT 20
    """
).fetchall()

with st.expander(
    f"Recent exports · {len(_recent)} on record",
    expanded=st.session_state.exports_manifest_open,
):
    st.checkbox(
        "Keep open across reruns",
        key="exports_manifest_open",
        help="Pin the manifest open after the next rerun.",
    )
    if not _recent:
        st.markdown(
            f"<span class='faint' style='color:{PALETTE['bone_dim']};'>"
            f"No exports recorded yet. Run one of the actions above."
            f"</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""<div style='display:grid; grid-template-columns:1.2fr 0.7fr 1fr 1.5fr auto auto;
                            gap:1rem; padding:0.3rem 0;
                            border-bottom:1px solid {PALETTE['hairline']};'>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']};'>when</span>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']};'>kind</span>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']};'>table</span>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']};'>file</span>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']}; text-align:right;'>rows</span>
                <span class='faint' style='font-size:0.7rem; letter-spacing:0.08em;
                                            text-transform:uppercase; color:{PALETTE['bone_faint']}; text-align:right;'>opt-in</span>
            </div>""",
            unsafe_allow_html=True,
        )
        for r in _recent:
            opt_in_flag = "—" if r["include_opt_in"] is None else ("yes" if r["include_opt_in"] else "no")
            file_name = Path(r["output_path"]).name if r["output_path"] else "—"
            st.markdown(
                f"""<div style='display:grid; grid-template-columns:1.2fr 0.7fr 1fr 1.5fr auto auto;
                                gap:1rem; padding:0.28rem 0;
                                border-bottom:1px solid {PALETTE['hairline']};'>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']};'>{r['exported_at_utc']}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone']};'>{r['kind']}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']};'>{r['table_name'] or '—'}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone']}; overflow:hidden; text-overflow:ellipsis;'>{file_name}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']}; text-align:right;'>{r['row_count'] if r['row_count'] is not None else '—'}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']}; text-align:right;'>{opt_in_flag}</span>
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
