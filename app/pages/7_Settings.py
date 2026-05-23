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

import html
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app import grok_client as _grok_client  # P9R-41: hoist from mid-file
from app.agent import cost as _agent_cost
from app.agent import recovery as _agent_recovery
from app.agent import repetition_guard as _repetition_guard
from app.agent import voice as _agent_voice
from app.agent import voice_profile as _voice_profile
from app.backup import (
    BACKUP_FILENAME_GLOB,
    BackupIntegrityError,
    DEFAULT_BACKUPS_DIR,
    DEFAULT_RETENTION_DAYS,
    backup_database,
)
from app.components.theme import PALETTE, apply_theme, hairline, kicker, readout_card
from app.components.theme import cost_meter as _cost_meter
from app.db import DEFAULT_DB_PATH
from app.exports import (
    ALLOWLISTS,
    CounterfactualMissingError,
    export_database_to_json,
    export_table_to_csv,
    export_weekly_report,
)
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
            ("data_collection_mode", True, "manual | api — Phase 7 default 'api' (§17 / §29.1). Toggle to 'manual' to disable scheduled jobs; manual paths always remain available."),
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


@st.cache_data(ttl=2)
def _counterfactual_filled_for_week(_conn, week_iso: str) -> tuple[bool, str | None]:
    """Return (ready, week_start_date) — `ready` is True when a saved
    weekly_reviews row for the week has a non-blank counterfactual_note.

    Used to enable/disable the export button BEFORE the user clicks it.
    Cached with a 2s TTL keyed by ``week_iso`` so per-keystroke reruns in
    the ISO-week ``st.text_input`` don't hammer the DB. The leading-
    underscore ``_conn`` arg is excluded from the cache key per Streamlit
    convention — Connection objects aren't hashable and the connection
    is single-user-single-DB anyway. (/review-2 🔵 S7.)
    """
    try:
        from app.exports.markdown_weekly import _iso_week_to_dates

        monday, _sunday = _iso_week_to_dates(week_iso)
    except ValueError:
        return (False, None)
    week_start = monday.isoformat()
    row = _conn.execute(
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

_ready, _week_start = _counterfactual_filled_for_week(conn, _week_iso)
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
            # /review-2 S8: escape every DB-sourced interpolation before
            # injecting it into unsafe_allow_html markdown. Inputs are
            # controlled in this single-user local tool, but a future
            # output path or table name containing `<` / `"` would otherwise
            # silently break the layout (best case) or open an HTML-injection
            # vector (worst case).
            exported_at_html = html.escape(str(r["exported_at_utc"]))
            kind_html = html.escape(str(r["kind"]))
            table_html = html.escape(str(r["table_name"])) if r["table_name"] else "—"
            file_name_html = html.escape(file_name)
            row_count_html = html.escape(str(r["row_count"])) if r["row_count"] is not None else "—"
            opt_in_html = html.escape(opt_in_flag)
            st.markdown(
                f"""<div style='display:grid; grid-template-columns:1.2fr 0.7fr 1fr 1.5fr auto auto;
                                gap:1rem; padding:0.28rem 0;
                                border-bottom:1px solid {PALETTE['hairline']};'>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']};'>{exported_at_html}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone']};'>{kind_html}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']};'>{table_html}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone']}; overflow:hidden; text-overflow:ellipsis;'>{file_name_html}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']}; text-align:right;'>{row_count_html}</span>
                    <span class='numeric' style='font-size:0.78rem; color:{PALETTE['bone_dim']}; text-align:right;'>{opt_in_html}</span>
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


# ---------------------------------------------------------------------------
# Growth Agent panel — §28.6 cost + §28.2 IWH policy + voice/target accounts.
# (Agent / cost_meter imports moved to module top in W24.)
# ---------------------------------------------------------------------------
hairline()
kicker("growth-agent · §28")
st.markdown("## Growth Agent")
st.caption(
    "Phase 5.5 panel. Cost ceiling (§28.6), IWH revision policy "
    "(§28.2 rule #13), voice samples (§28.5), and curated reply-target "
    "accounts (§28.4 #8). Anthropic API key is read from `.env` and is "
    "never editable from this page."
)

# Cost meter at the top of the agent panel.
st.markdown("### Cost")
_mtd = _agent_cost.month_to_date_spend_usd(conn)
_cap = _agent_cost.get_monthly_ceiling_usd(conn)
readout_card(
    label="MONTH-TO-DATE SPEND",
    value=f"${_mtd:0.2f}",
    caption=f"cap ${_cap:0.2f} · {(_mtd / _cap * 100 if _cap > 0 else 0):0.0f}% used",
)
_cost_meter(_mtd, _cap)

for key, editable, helptext in [
    ("agent_default_model", True, "Default Anthropic model (§28.4)."),
    # RV2-3: the Phase 7 combined Anthropic + xAI ceiling is the canonical
    # cost cap going forward ($30 default per §28.6 + migration 018). The
    # legacy ``agent_monthly_cost_cap_usd`` key still works as a fallback
    # for pre-migration-018 DBs but is no longer surfaced here — the
    # cost.get_monthly_ceiling_usd() reader prefers the new key.
    ("combined_ai_monthly_cost_ceiling_usd", True,
     "Combined Anthropic + xAI monthly USD ceiling (§28.6). Raise carefully."),
]:
    _render_setting(conn, key, editable, helptext)

# ---------------------------------------------------------------------------
# Publishing — Phase 8 (§28.10 Phase 5.5 → Phase 8 transition; §29.1
# "Manual workflows remain inviolable as Settings-selectable fallbacks
# forever"). publish_via_api_enabled gates the §28.10 atomic-transaction
# wrapper's branch: TRUE → real POST /2/tweets; FALSE → manual-clipboard
# fallback path Daniel completes via the existing "Mark posted" UI.
# ---------------------------------------------------------------------------
st.markdown("### Publishing")
st.caption(
    "Phase 8 (§28.10): the publish flow's atomic transaction calls the X API "
    "directly via xurl when ON. When OFF, every publish takes the manual-"
    "clipboard fallback path — the agent stages an intent URL and you finish "
    "the post via the existing Mark posted form. Manual fallback is never "
    "deprecated (§29.1). Rate-limit windows count manual AND API publishes "
    "globally — Daniel is rate-limited per X account, not per branch."
)

_publish_api_row = conn.execute(
    "SELECT value_json FROM settings WHERE key = 'publish_via_api_enabled'"
).fetchone()
_publish_via_api_current = True
if _publish_api_row and _publish_api_row[0]:
    try:
        _publish_via_api_current = bool(json.loads(_publish_api_row[0]))
    except (TypeError, json.JSONDecodeError):
        _publish_via_api_current = True

_publish_api_new = st.toggle(
    "publish_via_api_enabled — call POST /2/tweets via xurl on publish",
    value=_publish_via_api_current,
    help=(
        "ON (default): the §28.10 wrapper calls the X API on Publish. "
        "OFF: the wrapper opens an intent URL and you finish the post manually "
        "via Mark posted. Toggle persists across sessions."
    ),
    key="publish_via_api_enabled_toggle",
)
if _publish_api_new != _publish_via_api_current:
    conn.execute(
        "UPDATE settings SET value_json = ? WHERE key = 'publish_via_api_enabled'",
        ("true" if _publish_api_new else "false",),
    )
    conn.commit()
    st.toast(
        "publish_via_api_enabled = " + ("ON · X API writes active" if _publish_api_new else "OFF · manual-clipboard mode active")
    )
    st.rerun()

if not _publish_via_api_current:
    st.markdown(
        f"<div style='border-left: 2px solid {PALETTE['warn_amber']}; "
        f"padding: 0.55rem 0.85rem; margin: 0.4rem 0 0.6rem 0; "
        f"background: {PALETTE['surface']};'>"
        f"<div class='numeric' style='font-size: 0.75rem; color: {PALETTE['warn_amber']}; "
        f"letter-spacing: 0.08em; text-transform: uppercase;'>"
        f"MANUAL-CLIPBOARD MODE · X API WRITES DISABLED"
        f"</div>"
        f"<div style='font-family: Fraunces, serif; font-size: 0.95rem; color: {PALETTE['bone']}; "
        f"margin-top: 0.3rem;'>"
        f"Every publish opens the intent URL and waits for you to confirm on X."
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

for key, editable, helptext in [
    (
        "x_write_rate_limit_per_15min",
        True,
        "Sliding-window cap on X API writes per 15 minutes. Honored by "
        "check_write_rate_capacity() before each publish call. Default 50 "
        "matches §25 Phase 8; tune as your X API tier allows.",
    ),
    (
        "x_write_rate_limit_per_24h",
        True,
        "Sliding-window cap on X API writes per 24 hours. Default 1000 per §25 Phase 8.",
    ),
]:
    _render_setting(conn, key, editable, helptext)

# ---------------------------------------------------------------------------
# Niche definition (§28.16, Phase 5.9) — load-bearing identity anchor.
# Two settings rows spliced into Section 1 of the system prompt; empty
# values BLOCK drafting via §28.2 rule #15 (orchestrator-enforced).
# ---------------------------------------------------------------------------
from app.agent import niche as _agent_niche  # noqa: E402 — keep panel-local
st.markdown("### Niche definition")
st.caption(
    "Two sentences that tell the agent who it's helping and what it's "
    "helping with. Spliced verbatim into Section 1 of the system prompt "
    "(§28.16). When either field is empty the orchestrator REFUSES every "
    "save_draft_* call — the agent will return a structured 'fill out "
    "your niche first' message (§28.2 rule #15)."
)

_current_niche = _agent_niche.get_niche(conn)
if not _current_niche.is_defined():
    st.markdown(
        f"<div style='border-left: 2px solid {PALETTE['warn_amber']}; "
        f"padding: 0.55rem 0.85rem; margin: 0.4rem 0; "
        f"background: {PALETTE['surface']};'>"
        f"<div class='numeric' style='font-size: 0.75rem; color: {PALETTE['warn_amber']}; "
        f"letter-spacing: 0.08em; text-transform: uppercase;'>"
        f"LOW-POWER MODE · drafting disabled"
        f"</div>"
        f"<div style='font-family: Fraunces, serif; font-size: 1.0rem; color: {PALETTE['bone']}; "
        f"margin-top: 0.35rem;'>"
        f"Your agent is in low-power mode — define your niche to unlock drafting."
        f"</div>"
        f"<div class='faint' style='font-size: 0.85rem; color: {PALETTE['bone_dim']}; "
        f"margin-top: 0.4rem;'>"
        f"Examples: <em>problem</em> — \"how to grow on X\" · "
        f"<em>person</em> — \"educational creators\". Write your own below."
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

with st.form("niche_definition_form", clear_on_submit=False):
    _niche_problem = st.text_area(
        "niche_problem — the problem you solve (one sentence)",
        value=_current_niche.problem,
        max_chars=400,
        height=80,
        help="What you help your audience do, or what pain you remove.",
    )
    _niche_person = st.text_area(
        "niche_person — the person you solve it for (one sentence)",
        value=_current_niche.person,
        max_chars=400,
        height=80,
        help="Who they are. ICP-level specificity beats role labels.",
    )
    if st.form_submit_button("save niche"):
        try:
            _saved = _agent_niche.set_niche(
                conn,
                problem=_niche_problem,
                person=_niche_person,
            )
            if _saved.is_defined():
                st.toast("niche saved — drafting unlocked.")
            else:
                st.toast("niche cleared — drafting will be refused.")
            st.rerun()
        except sqlite3.DatabaseError as exc:
            st.error(f"could not save niche: {exc}")

# "Test against bio" affordance — read-only Haiku critique. Never edits
# the X bio itself. Disabled when niche isn't set; shows a hint instead.
with st.expander("Test against bio (Haiku critique — read-only)", expanded=False):
    if not _current_niche.is_defined():
        st.caption("Save both niche fields first; the critique compares them to a bio.")
    else:
        with st.form("niche_alignment_form", clear_on_submit=False):
            _bio_text = st.text_area(
                "Paste your current X bio",
                max_chars=600,
                height=110,
                help="The panel never edits the bio — it only critiques alignment.",
            )
            if st.form_submit_button("Critique alignment"):
                try:
                    _critique = _agent_niche.critique_alignment(
                        bio_text=_bio_text,
                        niche=_current_niche,
                    )
                    _border = (
                        PALETTE["phosphor"] if _critique.aligned
                        else PALETTE["warn_amber"]
                    )
                    _verdict = "ALIGNED" if _critique.aligned else "NOT ALIGNED"
                    st.markdown(
                        f"<div style='border-left: 2px solid {_border}; "
                        f"padding: 0.55rem 0.85rem; margin: 0.4rem 0; "
                        f"background: {PALETTE['surface']};'>"
                        f"<div class='numeric' style='font-size: 0.75rem; color: {_border}; "
                        f"letter-spacing: 0.08em; text-transform: uppercase;'>"
                        f"{_verdict} · {_critique.tokens_used:,} tokens"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
                    if _critique.gaps:
                        st.markdown("**Gaps:**")
                        for g in _critique.gaps:
                            st.markdown(f"- {html.escape(g)}")
                    if _critique.suggestions:
                        st.markdown("**Suggestions:**")
                        for s in _critique.suggestions:
                            st.markdown(f"- {html.escape(s)}")
                except _agent_niche.NicheAlignmentError as exc:
                    st.error(f"alignment critique failed: {exc}")

# IWH policy.
st.markdown("### IWH revision policy")
for key, editable, helptext in [
    ("iwh_self_score_minimum", True, "Minimum per-axis score required (§28.2 rule #13)."),
    ("iwh_max_revision_attempts", True, "Refuse save on attempt N+1 (§28.2 rule #13)."),
    ("agent_voice_sample_count", True, "Top-N voice samples spliced into prompt (§28.5)."),
]:
    _render_setting(conn, key, editable, helptext)

st.caption(
    "Dark-pattern lint always runs (§28.2 rule #12 — non-bypassable). "
    "Lint behavior is owned by `app/agent/lint.py` and the offline "
    "pattern matcher; the lint pass cannot be disabled from this panel "
    "by design."
)

# Voice samples CRUD.
st.markdown("### Voice samples")
st.caption(
    "Top-N active samples (by priority) are injected into Section 5 of "
    "the system prompt at each conversation start (§28.5). Without "
    "samples the agent runs on the base prompt only — Daniel should "
    "seed at least 3-5 strong examples before first use."
)

_voice_rows = conn.execute(
    """
    SELECT id, text, context_note, pillar, priority, is_active, last_used_at_utc
    FROM voice_samples
    ORDER BY is_active DESC, priority ASC, id DESC
    """
).fetchall()
st.markdown(
    f"<div class='numeric' style='color:{PALETTE['bone_dim']}; font-size:0.85rem;'>"
    f"{sum(1 for r in _voice_rows if r['is_active'])} active · "
    f"{len(_voice_rows)} total"
    f"</div>",
    unsafe_allow_html=True,
)

with st.expander("+ add voice sample", expanded=False):
    with st.form("add_voice_sample", clear_on_submit=True):
        _vs_text = st.text_area("text", height=120, max_chars=600)
        _vs_pillar = st.selectbox("pillar", options=["stir", "build", "self", None])
        _vs_context = st.text_input("context note (optional)")
        _vs_priority = st.number_input("priority (lower = earlier in prompt)", value=5, step=1)
        if st.form_submit_button("add"):
            if _vs_text.strip():
                _agent_voice.add_voice_sample(
                    conn,
                    text=_vs_text.strip(),
                    pillar=_vs_pillar,
                    context_note=_vs_context or None,
                    priority=int(_vs_priority),
                )
                st.toast("voice sample added.")
                st.rerun()

for r in _voice_rows:
    border_color = PALETTE["phosphor"] if r["is_active"] else PALETTE["hairline"]
    st.markdown(
        f"<div style='border-left: 2px solid {border_color}; padding: 0.4rem 0.8rem; "
        f"margin: 0.4rem 0; background: {PALETTE['surface']};'>"
        f"<div class='numeric' style='font-size: 0.75rem; color: {PALETTE['bone_faint']};'>"
        f"#{r['id']} · priority {r['priority']} · pillar={r['pillar'] or '—'} · "
        f"{'active' if r['is_active'] else 'inactive'}"
        f"</div>"
        f"<div style='font-family: Fraunces, serif; font-size: 1.0rem; color: {PALETTE['bone']}; "
        f"margin-top: 0.3rem;'>"
        f"{html.escape(r['text'])}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if r["is_active"]:
        if st.button("deactivate", key=f"vs_deactivate_{r['id']}"):
            _agent_voice.deactivate_voice_sample(conn, sample_id=int(r["id"]))
            st.rerun()

# ---------------------------------------------------------------------------
# Voice profile (§28.12) — generated structural read of Daniel's writing,
# spliced into the system prompt alongside the hand-picked voice samples.
# ---------------------------------------------------------------------------
st.markdown("### Voice profile (generated)")
st.caption(
    "A small-model synthesis of how you actually write — cadence, hooks, "
    "vocabulary, stop phrases — built from the last N days of posts. "
    "Complements voice samples (raw exemplars). Regeneration is manual: "
    "click when your voice has shifted enough to warrant a refresh "
    "(§28.12)."
)

_active_profile = _voice_profile.get_active(conn)
_default_window = _voice_profile.get_window_days(conn)
_min_source_posts = _voice_profile.get_min_source_posts(conn)

if _active_profile is None:
    st.markdown(
        f"<div class='faint' style='font-size: 0.85rem; color: {PALETTE['bone_dim']}; "
        f"padding: 0.4rem 0.8rem; border-left: 2px solid {PALETTE['hairline']}; "
        f"background: {PALETTE['surface']};'>"
        f"No active voice profile. Generate one from at least "
        f"{_min_source_posts} posts in your chosen window."
        f"</div>",
        unsafe_allow_html=True,
    )
else:
    _generated_at = _active_profile.generated_at_utc
    _age_days_row = conn.execute(
        "SELECT CAST(julianday('now') - julianday(?) AS INTEGER)",
        (_generated_at,),
    ).fetchone()
    _age_days = int(_age_days_row[0]) if _age_days_row and _age_days_row[0] is not None else 0
    st.markdown(
        f"<div style='border-left: 2px solid {PALETTE['phosphor']}; "
        f"padding: 0.5rem 0.8rem; margin: 0.3rem 0; background: {PALETTE['surface']};'>"
        f"<div class='numeric' style='font-size: 0.75rem; color: {PALETTE['bone_faint']}; "
        f"letter-spacing: 0.06em; text-transform: uppercase;'>"
        f"PROFILE #{_active_profile.id} · last regenerated {_age_days}d ago · "
        f"{_active_profile.source_post_count} posts · model={html.escape(_active_profile.model_used)} · "
        f"tokens_used={_active_profile.tokens_used:,}"
        f"</div>"
        f"<div style='font-family: Fraunces, serif; font-size: 1.0rem; color: {PALETTE['bone']}; "
        f"margin-top: 0.4rem;'>"
        f"{html.escape(_active_profile.self_description())}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    _cadence = _active_profile.cadence()
    _vocab = _active_profile.vocabulary_signatures()[:5]
    _stops = _active_profile.stop_phrases()[:5]
    if _cadence or _vocab or _stops:
        with st.expander("structural details", expanded=False):
            if _cadence:
                st.markdown(
                    "<div class='numeric' style='font-size: 0.8rem; color: "
                    f"{PALETTE['bone_dim']};'>cadence: "
                    + ", ".join(
                        html.escape(f"{k}={v}") for k, v in _cadence.items()
                    )
                    + "</div>",
                    unsafe_allow_html=True,
                )
            if _vocab:
                st.markdown(
                    "**Vocabulary signatures:** " + ", ".join(f"`{v}`" for v in _vocab)
                )
            if _stops:
                st.markdown(
                    "**Stop phrases (avoid):** " + ", ".join(f"`{s}`" for s in _stops)
                )

with st.form("regenerate_voice_profile", clear_on_submit=False):
    _window_input = st.number_input(
        "Source-post window (days)",
        min_value=7, max_value=365,
        value=int(_default_window),
        step=1,
        help=(
            "Posts within this many days back feed the synthesis. "
            "Default lives in `voice_profile_window_days`."
        ),
    )
    _regen_clicked = st.form_submit_button("Regenerate from posts")
    if _regen_clicked:
        try:
            new_profile = _voice_profile.generate(conn, window_days=int(_window_input))
            st.session_state["voice_profile_regen_result"] = {
                "status": "success",
                "profile_id": new_profile.id,
                "post_count": new_profile.source_post_count,
            }
            st.rerun()
        except _voice_profile.VoiceProfileGenerationError as exc:
            st.session_state["voice_profile_regen_result"] = {
                "status": "error",
                "message": str(exc),
            }
            st.rerun()

_result = st.session_state.pop("voice_profile_regen_result", None)
if _result is not None:
    if _result["status"] == "success":
        st.success(
            f"Profile #{_result['profile_id']} activated · "
            f"{_result['post_count']} source posts. Prior profile (if any) "
            f"has been deactivated."
        )
    else:
        st.error(_result["message"])

# ---------------------------------------------------------------------------
# Personality lore (§28.21, Phase 5.9) — Daniel-curated registry of
# recurring jokes, running bits, personal motifs spliced into Section 5
# of the system prompt after voice samples. Agent has NO write access.
# ---------------------------------------------------------------------------
from app.agent import personality_lore as _personality_lore  # noqa: E402 — page-local
st.markdown("### Personality lore")
st.caption(
    "Recurring jokes, running bits, and motifs the agent should draw "
    "on when drafting `content_type = personality` posts (§28.21). "
    "Spliced into Section 5 of the system prompt AFTER voice samples. "
    "The agent has no write access — this is Daniel-only curation. "
    "Auto-extracted lore would warp drafts; the 5-minute hand-curation "
    "task once a quarter is the correct trade."
)

_lore_rows = _personality_lore.list_all(conn)
_lore_overuse = _personality_lore.get_overuse_threshold(conn)
_lore_splice_n = _personality_lore.get_splice_count(conn)
st.markdown(
    f"<div class='numeric' style='color:{PALETTE['bone_dim']}; font-size:0.85rem;'>"
    f"{sum(1 for r in _lore_rows if r.is_active)} active · "
    f"{len(_lore_rows)} total · top {_lore_splice_n} spliced into prompt"
    f"</div>",
    unsafe_allow_html=True,
)

with st.expander("+ add lore", expanded=False):
    with st.form("add_personality_lore", clear_on_submit=True):
        _lore_theme = st.text_input(
            "theme (short name)",
            help="e.g. 'water bottle in frame', 'kitchen-scanner fail'",
            max_chars=80,
        )
        _lore_desc = st.text_area(
            "description (one paragraph)",
            help="What the bit is, why it's recurring, when it shows up.",
            height=110,
            max_chars=600,
        )
        _lore_examples = st.text_input(
            "example_posts_json (optional — comma-separated post IDs)",
            help="JSON array Daniel can paste from Content Performance. "
                 "Leave blank if you don't have examples yet.",
        )
        _lore_priority = st.number_input(
            "priority (lower = earlier in prompt)",
            value=100, step=1,
        )
        if st.form_submit_button("add lore"):
            try:
                _examples_json = None
                if _lore_examples.strip():
                    _parts = [p.strip() for p in _lore_examples.split(",") if p.strip()]
                    _examples_json = json.dumps(_parts)
                _personality_lore.add(
                    conn,
                    theme=_lore_theme,
                    description=_lore_desc,
                    example_posts_json=_examples_json,
                    priority=int(_lore_priority),
                )
                st.toast("lore added.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

for _r in _lore_rows:
    _border = PALETTE["phosphor"] if _r.is_active else PALETTE["hairline"]
    _over_relied = _personality_lore.is_over_relied_on(
        _r, overuse_threshold=_lore_overuse
    )
    # P59A-S14: use the shared last_invoked_suffix helper so the
    # Settings panel matches the prompt-splice presentation
    # ("last invoked N days ago" instead of a raw ISO string).
    _last = _personality_lore.last_invoked_suffix(_r.last_invoked_at_utc)
    _meta = (
        f"#{_r.id} · priority {_r.priority} · "
        f"{'active' if _r.is_active else 'inactive'} · "
        f"invoked {_r.invocation_count}×{html.escape(_last)}"
    )
    st.markdown(
        f"<div style='border-left: 2px solid {_border}; "
        f"padding: 0.45rem 0.85rem; margin: 0.4rem 0; "
        f"background: {PALETTE['surface']};'>"
        f"<div class='numeric' style='font-size: 0.75rem; color: {PALETTE['bone_faint']};'>"
        f"{_meta}"
        f"</div>"
        f"<div style='font-family: Fraunces, serif; font-size: 1.0rem; "
        f"color: {PALETTE['bone']}; margin-top: 0.3rem;'>"
        f"<strong>{html.escape(_r.theme)}</strong>"
        f"</div>"
        f"<div style='font-size: 0.9rem; color: {PALETTE['bone_dim']}; "
        f"margin-top: 0.2rem;'>"
        f"{html.escape(_r.description)}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if _over_relied:
        st.markdown(
            f"<div style='border-left: 2px solid {PALETTE['warn_amber']}; "
            f"padding: 0.35rem 0.85rem; margin: 0.15rem 0 0.4rem 0; "
            f"background: {PALETTE['surface']};'>"
            f"<span class='numeric' style='color:{PALETTE['warn_amber']}; "
            f"font-size:0.75rem; letter-spacing:0.08em; text-transform:uppercase;'>"
            f"LEANING HARD ON THIS BIT"
            f"</span> — "
            f"<span style='color:{PALETTE['bone']};'>"
            f"invoked {_r.invocation_count}× and last seen recently. "
            f"Doesn't disable lore; just informs."
            f"</span></div>",
            unsafe_allow_html=True,
        )
    _col_a, _col_b = st.columns(2)
    if _r.is_active:
        if _col_a.button("deactivate", key=f"lore_off_{_r.id}"):
            _personality_lore.set_active(conn, lore_id=_r.id, is_active=False)
            st.rerun()
    else:
        if _col_a.button("activate", key=f"lore_on_{_r.id}"):
            _personality_lore.set_active(conn, lore_id=_r.id, is_active=True)
            st.rerun()
    _new_priority = _col_b.number_input(
        "priority",
        value=int(_r.priority),
        step=1,
        key=f"lore_prio_{_r.id}",
        label_visibility="collapsed",
    )
    if _new_priority != _r.priority:
        _personality_lore.set_priority(conn, lore_id=_r.id, priority=int(_new_priority))
        st.rerun()

# ---------------------------------------------------------------------------
# Repetition guard (§28.13) — embedding-similarity status panel + backfill.
# ---------------------------------------------------------------------------
st.markdown("### Repetition guard")
st.caption(
    "Embedding cosine scan that flags `near_duplicate` and `close_echo` "
    "drafts at save time (§28.13). Soft check — never blocks Publish. "
    "Provider is an adapter, not a setting: swap by editing "
    "`app/agent/embeddings.py` and re-running the backfill."
)
_guard_status = _repetition_guard.status(conn)
st.markdown(
    f"<div style='border-left: 2px solid {PALETTE['phosphor']}; "
    f"padding: 0.5rem 0.8rem; margin: 0.3rem 0; background: {PALETTE['surface']};'>"
    f"<div class='numeric' style='font-size: 0.75rem; color: {PALETTE['bone_faint']}; "
    f"letter-spacing: 0.06em; text-transform: uppercase;'>"
    f"PROVIDER · {html.escape(str(_guard_status['provider']))} · "
    f"DIM {_guard_status['embedding_dim']}"
    f"</div>"
    f"<div style='font-family: JetBrains Mono, monospace; font-size: 0.85rem; "
    f"color: {PALETTE['bone']}; margin-top: 0.4rem;'>"
    f"{_guard_status['embedded_count']} / {_guard_status['shipped_post_count']} "
    f"shipped posts embedded · "
    f"lookback {_guard_status['lookback_days']}d · "
    f"near_dup ≥ {_guard_status['near_duplicate_threshold']} · "
    f"echo ≥ {_guard_status['close_echo_threshold']}"
    f"</div>"
    f"</div>",
    unsafe_allow_html=True,
)
if _guard_status["embedded_count"] < _guard_status["shipped_post_count"]:
    st.markdown(
        f"<div class='faint' style='font-size: 0.85rem; color: {PALETTE['bone_dim']}; "
        f"padding: 0.2rem 0;'>"
        f"Backfill is incomplete. Run "
        f"<code>uv run python scripts/embed_posts.py</code> from a terminal."
        f"</div>",
        unsafe_allow_html=True,
    )

# Curated agent_target_accounts.
st.markdown("### Curated reply-target accounts")
st.caption(
    "Accounts the agent draws from when find_reply_targets is invoked "
    "(§28.4 #8). Phase 5.6's reply-target queue will reference these "
    "rows for the relevance prior (§29.2)."
)

_target_rows = conn.execute(
    """
    SELECT id, x_handle, display_name, notes, lane, priority,
           last_engaged_at, is_active
    FROM agent_target_accounts
    ORDER BY is_active DESC, priority ASC, id DESC
    """
).fetchall()

with st.expander("+ add curated account", expanded=False):
    with st.form("add_target_account", clear_on_submit=True):
        _ta_handle = st.text_input("x handle (without @)")
        _ta_name = st.text_input("display name (optional)")
        _ta_notes = st.text_input("notes")
        _ta_lane = st.text_input("lane (e.g. build_icp, stir_icp)")
        _ta_priority = st.number_input("priority (lower = higher)", value=5, step=1)
        if st.form_submit_button("add"):
            if _ta_handle.strip():
                try:
                    conn.execute(
                        """
                        INSERT INTO agent_target_accounts
                            (x_handle, display_name, notes, lane, priority, is_active)
                        VALUES (?, ?, ?, ?, ?, 1)
                        """,
                        (
                            _ta_handle.strip().lstrip("@"),
                            _ta_name or None,
                            _ta_notes or None,
                            _ta_lane or None,
                            int(_ta_priority),
                        ),
                    )
                    st.toast("target account added.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error(f"@{_ta_handle.strip().lstrip('@')} is already in the list.")

if not _target_rows:
    st.markdown(
        f"<div class='faint' style='font-size: 0.85rem; color: {PALETTE['bone_dim']};'>"
        f"No curated accounts yet. find_reply_targets will return empty until "
        f"at least one is added."
        f"</div>",
        unsafe_allow_html=True,
    )
for r in _target_rows:
    border_color = PALETTE["phosphor"] if r["is_active"] else PALETTE["hairline"]
    st.markdown(
        f"<div style='border-left: 2px solid {border_color}; padding: 0.4rem 0.8rem; "
        f"margin: 0.3rem 0; background: {PALETTE['surface']};'>"
        f"<div class='numeric' style='font-size: 0.85rem; color: {PALETTE['bone']};'>"
        f"@{html.escape(r['x_handle'])}"
        f"<span class='faint' style='margin-left: 0.6rem; font-size: 0.75rem;'>"
        f"priority {r['priority']} · lane={r['lane'] or '—'}</span>"
        f"</div>"
        f"<div style='font-size: 0.85rem; color: {PALETTE['bone_dim']}; margin-top: 0.2rem;'>"
        f"{html.escape(r['notes'] or '')}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

# Orphan recovery.
st.markdown("### Orphan-post recovery")
st.caption(
    "Posts where the publish flow began but never completed (§28.10 "
    "step 8). MVP reconciliation is manual: paste the live X URL to "
    "mark as posted, or mark as failed to flag the row for re-draft."
)
_orphans = _agent_recovery.detect_orphans(conn)
if not _orphans:
    st.markdown(
        f"<div class='faint' style='font-size: 0.85rem; color: {PALETTE['bone_dim']};'>"
        f"No orphans. The publish flow has settled cleanly."
        f"</div>",
        unsafe_allow_html=True,
    )
for orphan in _orphans:
    with st.expander(
        f"orphan post #{orphan.post_id} · attempts {orphan.publish_attempt_count}"
    ):
        st.markdown(
            f"<div style='font-family: Fraunces, serif; font-size: 1.0rem; "
            f"color: {PALETTE['bone']};'>"
            f"{html.escape(orphan.text)}"
            f"</div>"
            f"<div class='faint' style='font-size: 0.75rem;'>"
            f"intent staged at {orphan.published_to_x_at} · "
            f"method={orphan.publish_method}"
            f"</div>",
            unsafe_allow_html=True,
        )
        _live_url = st.text_input(
            "live X URL (if posted)", key=f"orphan_url_{orphan.post_id}"
        )
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("mark posted", key=f"orphan_posted_{orphan.post_id}"):
                if _live_url.strip():
                    _xid = _live_url.rstrip("/").rsplit("/", 1)[-1]
                    _agent_recovery.mark_orphan_posted(
                        conn,
                        post_id=orphan.post_id,
                        x_post_id=_xid,
                        x_post_url=_live_url.strip(),
                    )
                    st.toast("orphan reconciled.")
                    st.rerun()
                else:
                    st.error("paste the live X URL first.")
        with col_b:
            if st.button("mark failed", key=f"orphan_failed_{orphan.post_id}"):
                _agent_recovery.mark_orphan_failed(
                    conn,
                    post_id=orphan.post_id,
                    reason="manually flagged in Settings",
                )
                st.toast("orphan flagged as failed.")
                st.rerun()


# ===========================================================================
# Profile Audit (§28.25, Phase 5.10) — §14.7 field 12.
# ===========================================================================
# Daniel runs a comprehensive review of his X surface — bio + pinned
# post + recent posts + active voice profile + niche definition — read
# as a unified surface. Append-only history; the audit never auto-runs.
# A yellow reminder banner surfaces when the most recent audit is
# older than profile_audit_cadence_reminder_days (default 90).
hairline()
from app.agent import profile_audit as _profile_audit  # noqa: E402 — keep panel-local
from app.agent import coach as _coach_mod  # noqa: E402 — page-local; used only for the toggle

st.markdown("### Profile audit")
st.caption(
    "§28.25 — periodic comprehensive review of the surface a new "
    "follower sees: bio + pinned post + recent posts + active voice "
    "profile + niche. The audit's load-bearing output is `top_three_"
    "actions`. Append-only history; never auto-runs."
)

_pa_last_days = _profile_audit.days_since_last_audit(conn)
_pa_cadence = _profile_audit.get_cadence_reminder_days(conn)
_pa_audits = _profile_audit.list_audits(conn, limit=20)
_pa_window_default = _profile_audit.get_recent_posts_window_days(conn)

# Last-audit readout + cadence reminder.
if _pa_last_days is None:
    readout_card(
        "Last audit",
        "no audits yet",
        caption="Run your first audit when bio + pinned post are stable.",
        empty=True,
    )
elif _pa_last_days > _pa_cadence:
    st.markdown(
        f"""<div style='border-left:2px solid {PALETTE['warn_amber']};
                       background:{PALETTE['surface']};
                       padding:0.65rem 0.9rem; margin:0.5rem 0 1rem 0;
                       border-radius:2px;'>
            <div class='kicker' style='color:{PALETTE['warn_amber']};'>
                CADENCE REMINDER — §28.25
            </div>
            <div style='margin-top:0.2rem; color:{PALETTE['bone']};'>
                Last audit was
                <span class='numeric'>{_pa_last_days}</span> days ago
                (threshold:
                <span class='numeric'>{_pa_cadence}</span>).
                Worth a fresh read of the surface.
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
else:
    readout_card(
        "Last audit",
        f"{_pa_last_days} day{'s' if _pa_last_days != 1 else ''} ago",
        caption=f"Cadence reminder threshold: {_pa_cadence} days.",
        accent="phosphor",
    )

# coach_refuse_without_evidence toggle (mirrors §14.10 Coach view).
# get_setting() already JSON-decodes — no double-decode needed.
_coach_refuse_current = bool(
    get_setting(conn, "coach_refuse_without_evidence", default=True)
)

_coach_refuse_new = st.toggle(
    "Coach refuses without evidence (§28.23 / §14.10)",
    value=_coach_refuse_current,
    key="settings_coach_refuse_toggle",
    help=(
        "When on (default), Coach messages with zero surviving "
        "citations + analytical claims are replaced with a canonical "
        "refusal before persistence. Off → uncited claims pass through."
    ),
)
if _coach_refuse_new != _coach_refuse_current:
    # set_setting() does the JSON encode — pass the raw Python bool.
    set_setting(conn, "coach_refuse_without_evidence", _coach_refuse_new)
    st.toast(
        f"coach_refuse_without_evidence = {_coach_refuse_new}"
    )
    st.rerun()
# Silence "unused" — the import documents the §28.23 link.
_ = _coach_mod

# Run-audit form.
with st.expander("＋  run profile audit now", expanded=(_pa_last_days is None)):
    _niche = _agent_niche.get_niche(conn)
    _active_voice = _voice_profile.get_active(conn)
    st.markdown(
        f"""<div class='callout'>
            <strong>read scope.</strong> The audit reads your active
            niche, the active voice profile, the bio + pinned-post
            text you paste below, and the most recent
            <span class='numeric'>{_pa_window_default}</span> day(s)
            of shipped posts (override below if needed).
        </div>""",
        unsafe_allow_html=True,
    )
    with st.form("profile_audit_run_form", clear_on_submit=False, border=False):
        pa_bio = st.text_area(
            "current X bio",
            value=get_setting(conn, "bio_text_snapshot") or "",
            height=80,
            help="paste your bio verbatim. Wrapped as untrusted data per §28.2.",
        )
        pa_pinned = st.text_area(
            "pinned post text (required)",
            value="",
            height=120,
            help="paste the text of your pinned post. The audit weights it heavily.",
        )
        pa_window = st.number_input(
            "recent posts window (days)",
            min_value=7,
            max_value=365,
            value=int(_pa_window_default),
            step=1,
        )
        pa_submitted = st.form_submit_button(
            "Run audit",
            type="primary",
        )
        if pa_submitted:
            if not pa_bio.strip():
                st.error("Bio is required.")
            elif not pa_pinned.strip():
                st.error("Pinned post text is required.")
            else:
                try:
                    pa_analysis, pa_snapshot = _profile_audit.audit(
                        conn,
                        bio_text=pa_bio,
                        pinned_post_text=pa_pinned,
                        recent_post_window_days=int(pa_window),
                    )
                    pa_new_id = _profile_audit.save(
                        conn,
                        analysis=pa_analysis,
                        bio_snapshot=pa_bio,
                        pinned_post_id=None,
                        pinned_post_text=pa_pinned,
                        snapshot=pa_snapshot,
                    )
                    st.toast(f"audit #{pa_new_id} saved.")
                    st.rerun()
                except _profile_audit.ProfileAuditError as exc:
                    st.error(f"audit failed: {exc}")

# Past audits table + diff view.
if _pa_audits:
    st.markdown(
        "<div class='kicker' style='margin-top:0.8rem;'>PAST AUDITS</div>",
        unsafe_allow_html=True,
    )
    for i, _pa_row in enumerate(_pa_audits):
        _pa_data = _pa_row.get("audit") or {}
        _pa_overall = int(_pa_data.get("overall_consistency_score", 0))
        _pa_actions = _pa_data.get("top_three_actions") or []
        with st.expander(
            f"#{_pa_row['id']} · {_pa_row['audited_at_utc'][:10]} · "
            f"overall {_pa_overall} / 3",
            expanded=(i == 0),
        ):
            if not _pa_data:
                st.warning("audit_json failed to parse — see sqlite-utils.")
                continue
            # Top three actions — the load-bearing field.
            st.markdown(
                "<div class='kicker'>TOP THREE ACTIONS</div>",
                unsafe_allow_html=True,
            )
            if _pa_actions:
                for _idx, _action in enumerate(_pa_actions, start=1):
                    st.markdown(
                        f"<div style='padding:0.3rem 0.7rem; margin:0.2rem 0;"
                        f"border-left:2px solid {PALETTE['phosphor']};"
                        f"font-family: Fraunces, IBM Plex Serif, Georgia, serif;"
                        f"font-style: italic; color:{PALETTE['bone']};"
                        f"font-size:0.97rem;'>"
                        f"<span class='numeric'>{_idx}.</span> "
                        f"{html.escape(str(_action))}</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    "<div class='faint'>(no actions returned)</div>",
                    unsafe_allow_html=True,
                )

            # Sub-section scores in a compact strip.
            cols = st.columns(4)
            sub_scores = [
                ("BIO ALIGN", _pa_data.get("bio_alignment", {}).get("score", 0)),
                ("PINNED ALIGN", _pa_data.get("pinned_post_alignment", {}).get("score", 0)),
                ("VOICE CONSIST.", _pa_data.get("voice_consistency_with_profile", {}).get("score", 0)),
                ("NICHE COHER.", _pa_data.get("niche_coherence", {}).get("score", 0)),
            ]
            for col, (label, score) in zip(cols, sub_scores):
                with col:
                    readout_card(
                        label,
                        f"{int(score)} / 3",
                        accent="phosphor" if int(score) >= 2 else "bone_dim",
                    )

            # Full audit_json as a collapsible payload.
            st.markdown(
                "<div class='kicker' style='margin-top:0.4rem;'>"
                "FULL AUDIT JSON</div>",
                unsafe_allow_html=True,
            )
            st.json(_pa_data, expanded=False)

            # Compare-to-previous when applicable.
            if i + 1 < len(_pa_audits):
                _pa_prev = _pa_audits[i + 1]
                _pa_prev_data = _pa_prev.get("audit") or {}
                _pa_prev_overall = int(_pa_prev_data.get("overall_consistency_score", 0))
                _pa_delta = _pa_overall - _pa_prev_overall
                _pa_delta_str = (
                    f"+{_pa_delta}" if _pa_delta > 0 else str(_pa_delta)
                )
                st.markdown(
                    f"<div class='kicker' style='margin-top:0.5rem;'>"
                    f"COMPARE TO PREVIOUS</div>"
                    f"<div class='dim' style='font-size:0.86rem;'>"
                    f"vs #{_pa_prev['id']} ({_pa_prev['audited_at_utc'][:10]}): "
                    f"overall <span class='numeric'>{_pa_prev_overall}</span> "
                    f"→ <span class='numeric'>{_pa_overall}</span> "
                    f"(delta <span class='numeric'>{_pa_delta_str}</span>)"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # Daniel's editable notes for THIS audit row.
            _notes_key = f"pa_notes_{_pa_row['id']}"
            if _notes_key not in st.session_state:
                st.session_state[_notes_key] = _pa_row.get("daniel_notes") or ""
            st.text_area(
                "your notes (what you acted on, what you deferred)",
                key=_notes_key,
                height=80,
            )
            if st.button("save notes", key=f"pa_save_notes_{_pa_row['id']}"):
                _profile_audit.update_notes(
                    conn,
                    audit_id=_pa_row["id"],
                    notes=st.session_state[_notes_key],
                )
                st.toast("notes saved.")
                st.rerun()

# ---------------------------------------------------------------------------
# Phase 7 — X API data sources & scheduled-job health.
# ---------------------------------------------------------------------------
# Three things in one place: which mode the dashboard is in (manual vs.
# API), when each scheduled job last touched its target table, and any
# X API failures from the last `x_api_recent_failures_visible_days`
# days. The toggle is the global setting; the launchd plists honor it
# at run-time (data_collection_mode='manual' → jobs no-op).
hairline()
st.subheader("Data sources & X API health (Phase 7)")
st.markdown(
    "<div class='dim' style='margin-bottom:0.6rem;font-size:0.86rem;'>"
    "Scheduled jobs (account snapshot, post import, post + reply-target "
    "metrics refresh) run only when launchd plists are loaded "
    "(<code>docs/SCHEDULED_JOBS.md</code>). When <code>data_collection_mode</code> "
    "is set to <code>manual</code> the jobs no-op; manual entry paths remain "
    "available regardless of the toggle.</div>",
    unsafe_allow_html=True,
)

# RV2-29: batch the three Phase-7-panel settings reads into one SQL call
# to cut per-rerun round-trips. Streamlit reruns on every interaction;
# 3 round-trips → 1 keeps the panel responsive.
_phase7_settings_rows = conn.execute(
    "SELECT key, value_json FROM settings WHERE key IN (?, ?, ?)",
    ("data_collection_mode", "x_handle", "x_api_recent_failures_visible_days"),
).fetchall()
_phase7_settings: dict[str, str | None] = {
    row["key"]: row["value_json"] for row in _phase7_settings_rows
}

# Mode toggle — the load-bearing flag the four jobs check before any
# API call. The existing _SETTING_ROWS["Data sources"] panel above is
# the canonical write surface; this is a read-only echo for visibility.
_mode_value = "(unset)"
_mode_raw = _phase7_settings.get("data_collection_mode")
if _mode_raw:
    try:
        _mode_value = str(json.loads(_mode_raw) or "")
    except (TypeError, json.JSONDecodeError):
        _mode_value = "(unparseable)"
_mode_color = "#7ec97e" if _mode_value == "api" else "#d9a86b"
st.markdown(
    f"<div style='margin:0.4rem 0 0.8rem 0;font-size:0.92rem;'>"
    f"<strong>Mode:</strong> "
    f"<span style='color:{_mode_color};font-family:\"JetBrains Mono\", monospace;'>"
    f"{html.escape(_mode_value)}</span>"
    f"<span class='dim' style='margin-left:0.6rem;'>"
    f"(change above under 'Data sources')</span></div>",
    unsafe_allow_html=True,
)

# ----- Per-job last-refresh timestamps -----
# RV2-16: filter by Daniel's x_handle so a future multi-account schema
# doesn't surface another account's snapshot timestamp as "Daniel's".
# Matches the discipline of _already_have_manual_snapshot_today in
# scripts/collect_account_snapshot.py.
_daniel_handle = "dannyscalant"
_handle_raw = _phase7_settings.get("x_handle")  # RV2-29 batched read
if _handle_raw:
    try:
        _daniel_handle = str(
            json.loads(_handle_raw) or _daniel_handle
        ).lstrip("@").strip()
    except (TypeError, json.JSONDecodeError):
        pass

_last_refresh_rows: list[tuple[str, str | None]] = []
_acct_row = conn.execute(
    "SELECT collected_at_utc FROM account_snapshots "
    "WHERE source = 'api' AND username = ? "
    "ORDER BY collected_at_utc DESC LIMIT 1",
    (_daniel_handle,),
).fetchone()
_last_refresh_rows.append(
    ("collect_account_snapshot", _acct_row[0] if _acct_row else None)
)
_post_row = conn.execute(
    "SELECT MAX(collected_at_utc) FROM post_metric_snapshots WHERE source = 'api'"
).fetchone()
_last_refresh_rows.append(
    ("post_metrics_refresh", _post_row[0] if _post_row else None)
)
_rt_row = conn.execute(
    "SELECT MAX(checked_at_utc) FROM reply_target_snapshots"
).fetchone()
_last_refresh_rows.append(
    ("reply_target_metrics_refresh", _rt_row[0] if _rt_row else None)
)
_import_row = conn.execute(
    "SELECT MAX(occurred_at_utc) FROM audit_logs "
    "WHERE event_category = 'scheduled_job' "
    "  AND event_type IN ('import_recent_posts', 'import_recent_posts_backfill')"
).fetchone()
_last_refresh_rows.append(
    ("import_recent_posts", _import_row[0] if _import_row else None)
)

st.markdown(
    "<div style='font-family:\"JetBrains Mono\", monospace;font-size:0.86rem;"
    "margin:0.2rem 0 0.8rem 0;'>"
    "<strong style='font-family:\"IBM Plex Sans\", sans-serif;'>"
    "Last-refresh timestamps</strong>"
    "</div>",
    unsafe_allow_html=True,
)
for _job_name, _ts in _last_refresh_rows:
    _ts_display = _ts or "(never)"
    _ts_color = "#7ec97e" if _ts else "#a39d92"
    st.markdown(
        f"<div style='font-family:\"JetBrains Mono\", monospace;"
        f"font-size:0.84rem;line-height:1.5;'>"
        f"<span style='color:#a39d92;'>· {html.escape(_job_name):<32}</span>"
        f"<span style='color:{_ts_color};'>{html.escape(str(_ts_display))}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

# ----- Recent X API failures -----
# RV2-29: read from the batched _phase7_settings dict instead of a 4th SELECT.
_failures_window_raw = _phase7_settings.get("x_api_recent_failures_visible_days")
_failures_window_days = 7
if _failures_window_raw:
    try:
        _failures_window_days = int(json.loads(_failures_window_raw))
    except (TypeError, json.JSONDecodeError, ValueError):
        pass

_failure_rows = conn.execute(
    """
    SELECT id, source, endpoint_or_command, status_code,
           collected_at_utc, notes
      FROM raw_api_responses
     WHERE source IN ('xurl', 'x_api')
       AND status_code IS NOT NULL
       AND status_code >= 400
       AND collected_at_utc >= datetime('now', ?)
     ORDER BY collected_at_utc DESC
     LIMIT 50
    """,
    (f"-{_failures_window_days} days",),
).fetchall()

st.markdown(
    f"<div style='margin:0.8rem 0 0.3rem 0;font-size:0.92rem;'>"
    f"<strong style='font-family:\"IBM Plex Sans\", sans-serif;'>"
    f"Recent X API failures</strong>"
    f"<span class='dim'> · last {_failures_window_days} days · "
    f"{len(_failure_rows)} event(s)</span>"
    f"</div>",
    unsafe_allow_html=True,
)
if not _failure_rows:
    st.markdown(
        "<div class='dim' style='font-size:0.86rem;margin-bottom:0.5rem;'>"
        "(no X API failures recorded in the window)</div>",
        unsafe_allow_html=True,
    )
else:
    for _fail in _failure_rows:
        _badge_color = "#d97e7e" if _fail[3] >= 500 else "#d9a86b"
        st.markdown(
            f"<div style='font-family:\"JetBrains Mono\", monospace;"
            f"font-size:0.82rem;line-height:1.55;'>"
            f"<span style='color:#a39d92;'>{html.escape(str(_fail[4]))}</span> · "
            f"<span style='color:{_badge_color};'>HTTP {int(_fail[3])}</span> · "
            f"<span>{html.escape(str(_fail[1] or ''))}</span> · "
            f"<span class='dim'>{html.escape(str(_fail[2] or '')[:80])}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Phase 9 — Grok firehose discovery (§29.12).
# ---------------------------------------------------------------------------
# Three panels under one hairline:
#
#   1. Grok queries panel: CRUD over grok_query_list_json + the kill
#      switch + cadence + "Run sweep now" affordance.
#   2. Combined AI spend this month (§28.6) — Anthropic + xAI split
#      with progress bar against combined_ai_monthly_cost_ceiling_usd.
#   3. Recent Grok failures (last 7 days) — joined from grok_api_responses.
hairline()
st.subheader("Grok firehose discovery (Phase 9)")
st.markdown(
    "<div class='dim' style='margin-bottom:0.6rem;font-size:0.86rem;'>"
    "Grok finds X posts matching natural-language queries Daniel maintains. "
    "Every candidate verifies against the X API before scoring "
    "(§29.2 — Grok is discovery, not measurement). Combined Anthropic + xAI "
    "spend is gated by <code>combined_ai_monthly_cost_ceiling_usd</code> "
    "(§28.6).</div>",
    unsafe_allow_html=True,
)

# ----- Phase 9 settings batch read -----
_phase9_settings_rows = conn.execute(
    # P9R-36: dropped the unused combined_ai_monthly_cost_ceiling_usd
    # from the batched read — the ceiling panel reads it via
    # _agent_cost.get_monthly_ceiling_usd directly.
    "SELECT key, value_json FROM settings WHERE key IN (?, ?, ?)",
    (
        "grok_api_enabled",
        "grok_query_list_json",
        "grok_discovery_sweep_interval_minutes",
    ),
).fetchall()
_phase9_settings: dict[str, str | None] = {
    row["key"]: row["value_json"] for row in _phase9_settings_rows
}

# ----- XAI_API_KEY status indicator (never displays the key value) -----
# P9R-60: route through grok_client.is_configured() so the placeholder-
# detection hardening (P9R-5) applies uniformly to both the runtime
# call site AND the Settings UI indicator. Otherwise the launchd
# plist placeholder shows green "configured" here while the sweep
# refuses to call. P9R-41: _grok_client is hoisted to page-top imports.
_xai_key_configured = _grok_client.is_configured()
_xai_key_color = "#7ec97e" if _xai_key_configured else "#d9a86b"
_xai_key_label = "configured" if _xai_key_configured else "not set"
st.markdown(
    f"<div style='margin:0.4rem 0 0.8rem 0;font-size:0.92rem;'>"
    f"<strong>XAI_API_KEY:</strong> "
    f"<span style='color:{_xai_key_color};font-family:\"JetBrains Mono\", monospace;'>"
    f"{html.escape(_xai_key_label)}</span>"
    f"<span class='dim' style='margin-left:0.6rem;'>"
    f"(set in .env; see .env.example for the line)</span></div>",
    unsafe_allow_html=True,
)

# ----- Phase 9 settings: explicit-handler form (P9R-10) -----
# Pre-fix, the checkbox/number-input/text-area each used the
# read-widget→compare-to-old→INSERT-if-changed→st.rerun() anti-
# pattern. Per project CLAUDE.md (Streamlit side-effects discipline),
# mutations belong in explicit handlers — on_change callbacks or
# form-submit handlers. Wrap the three controls in a single
# st.form so saves are a deliberate user action, not a render-time
# side effect.
_grok_enabled_raw = _phase9_settings.get("grok_api_enabled")
_grok_enabled_value = True  # comprehensive default per §29.12
if _grok_enabled_raw:
    try:
        _grok_enabled_value = bool(json.loads(_grok_enabled_raw))
    except (TypeError, json.JSONDecodeError):
        pass

_grok_interval_raw = _phase9_settings.get("grok_discovery_sweep_interval_minutes")
_grok_interval_value = 120
if _grok_interval_raw:
    try:
        _grok_interval_value = int(json.loads(_grok_interval_raw))
    except (TypeError, json.JSONDecodeError, ValueError):
        pass

_grok_queries_raw = _phase9_settings.get("grok_query_list_json")
_grok_queries: list[str] = []
if _grok_queries_raw:
    try:
        _parsed = json.loads(_grok_queries_raw)
        if isinstance(_parsed, list):
            _grok_queries = [str(q) for q in _parsed if isinstance(q, str)]
    except (TypeError, json.JSONDecodeError):
        _grok_queries = []

# P9R-21 + P9R-22: bounds the queries panel enforces on save. The
# length cap stops Daniel from pasting a 100KB string (which would
# ship full to xAI but get truncated to 1KB in the audit row); the
# secret patterns refuse-on-save with a warning if Daniel ever pastes
# an API key into a query string.
_MAX_GROK_QUERY_LEN: int = 500
_SECRET_REGEXES = (
    re.compile(r"sk-[A-Za-z0-9\-_]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[A-Z0-9]{12,}"),
    re.compile(r"xai-[A-Za-z0-9\-_]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_]{16,}"),
)


def _scrub_secrets_in_query(text: str) -> str | None:
    """Return the matching secret-pattern label if ``text`` looks like it
    embeds a secret, else None."""
    for pat in _SECRET_REGEXES:
        if pat.search(text):
            return pat.pattern
    return None


with st.form("grok_settings_form"):
    _form_cols = st.columns([1, 1])
    with _form_cols[0]:
        st.checkbox(
            "Grok API enabled",
            value=_grok_enabled_value,
            key="settings_grok_api_enabled",
            help=(
                "When OFF the discovery sweep aborts at start; manual + "
                "X API search paths still work. Default ON per §29.12."
            ),
        )
    with _form_cols[1]:
        st.number_input(
            "Sweep interval (minutes)",
            min_value=15,
            max_value=24 * 60,
            value=int(_grok_interval_value),
            step=15,
            key="settings_grok_sweep_interval",
            help=(
                "launchd plist StartInterval default (120 min). Edit "
                "the plist after changing here."
            ),
        )
    st.markdown(
        "<div style='margin:0.6rem 0 0.2rem 0;font-size:0.92rem;'>"
        "<strong style='font-family:\"IBM Plex Sans\", sans-serif;'>"
        "Grok queries</strong>"
        "<span class='dim'> · one query per line · max "
        f"{_MAX_GROK_QUERY_LEN} chars · empty list = no Grok calls</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.text_area(
        "Queries (one per line)",
        value="\n".join(_grok_queries),
        height=160,
        key="settings_grok_queries_textarea",
        label_visibility="collapsed",
        help=(
            "Example: 'home cooks frustrated with meal planning'. Each "
            "line becomes one Grok firehose search per sweep."
        ),
    )
    _submitted = st.form_submit_button("Save Grok settings", type="primary")
    if _submitted:
        _enabled_new = bool(st.session_state["settings_grok_api_enabled"])
        _interval_new = int(st.session_state["settings_grok_sweep_interval"])
        _raw_queries = st.session_state.get(
            "settings_grok_queries_textarea", ""
        ) or ""
        # P9R-21 length cap + P9R-22 secret-scrub. Reject the whole save
        # rather than silently truncating; Daniel sees the offending line.
        _parsed_queries: list[str] = []
        _reject_reason: str | None = None
        for _ln_idx, _line in enumerate(_raw_queries.splitlines(), start=1):
            _q = _line.strip()
            if not _q:
                continue
            if len(_q) > _MAX_GROK_QUERY_LEN:
                _reject_reason = (
                    f"line {_ln_idx} is {len(_q)} chars — exceeds "
                    f"{_MAX_GROK_QUERY_LEN}-char cap"
                )
                break
            _secret_pat = _scrub_secrets_in_query(_q)
            if _secret_pat is not None:
                _reject_reason = (
                    f"line {_ln_idx} matches a known secret pattern "
                    f"({_secret_pat}); remove the secret before saving"
                )
                break
            _parsed_queries.append(_q)

        if _reject_reason is not None:
            st.error(f"refused to save: {_reject_reason}")
        else:
            # Three rows up-serted in one go; the form-submit handler is
            # the single explicit mutation site per CLAUDE.md.
            conn.execute(
                "INSERT INTO settings (key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                ("grok_api_enabled", json.dumps(_enabled_new)),
            )
            conn.execute(
                "INSERT INTO settings (key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (
                    "grok_discovery_sweep_interval_minutes",
                    json.dumps(_interval_new),
                ),
            )
            conn.execute(
                "INSERT INTO settings (key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                ("grok_query_list_json", json.dumps(_parsed_queries)),
            )
            conn.commit()
            st.toast(
                f"saved · enabled={_enabled_new} · interval={_interval_new}m "
                f"· {len(_parsed_queries)} query/queries"
            )
            st.rerun()

# ----- Run sweep now button (P9R-25 in-flight guard) -----
# Lives OUTSIDE the form so the sweep button doesn't conflict with the
# Save-Grok-settings submit. The session-state flag disables the button
# while a synchronous sweep is in flight — pre-fix, Streamlit would queue
# a second click and double-spend at xAI.
if "grok_sweep_in_flight" not in st.session_state:
    st.session_state["grok_sweep_in_flight"] = False

if st.button(
    "Run sweep now",
    key="settings_grok_run_sweep_now",
    disabled=st.session_state["grok_sweep_in_flight"],
    help=(
        "Runs app/jobs/grok_discovery_sweep.py synchronously. Honors the "
        "kill switch + ceiling + query list. Button is disabled while a "
        "prior sweep is still in flight."
    ),
):
    st.session_state["grok_sweep_in_flight"] = True
    try:
        with st.spinner("Running Grok sweep (this may take 30–60 seconds)…"):
            try:
                from app.jobs.grok_discovery_sweep import (  # noqa: E402
                    run as _run_grok_sweep,
                )
                _sweep_summary = _run_grok_sweep(conn)
                if _sweep_summary.get("error"):
                    st.warning(
                        f"sweep finished with note: {_sweep_summary['error']} "
                        f"(discovered={_sweep_summary.get('candidates_discovered', 0)}, "
                        f"inserted={_sweep_summary.get('candidates_inserted', 0)})"
                    )
                else:
                    st.success(
                        f"sweep OK · queries_run={_sweep_summary.get('queries_run', 0)} · "
                        f"discovered={_sweep_summary.get('candidates_discovered', 0)} · "
                        f"verified={_sweep_summary.get('candidates_verified', 0)} · "
                        f"inserted={_sweep_summary.get('candidates_inserted', 0)} · "
                        f"rejected_404={_sweep_summary.get('candidates_rejected_404', 0)}"
                    )
            except Exception as _sweep_err:
                # Surface the exception class explicitly so Daniel can
                # disambiguate ceiling vs rate-limit vs network from the toast.
                st.error(
                    f"sweep failed ({type(_sweep_err).__name__}): {_sweep_err}"
                )
    finally:
        st.session_state["grok_sweep_in_flight"] = False

# ----- Combined AI spend this month (§28.6) -----
hairline()
st.markdown(
    "<div style='margin:0.4rem 0 0.3rem 0;font-size:0.95rem;'>"
    "<strong style='font-family:\"IBM Plex Sans\", sans-serif;'>"
    "Combined AI spend this month</strong>"
    "<span class='dim'> · Anthropic + xAI Grok · §28.6 ceiling</span>"
    "</div>",
    unsafe_allow_html=True,
)

from app.agent import cost as _cost_module  # noqa: E402

_anthropic_mtd = _cost_module.month_to_date_spend_usd(conn)
_xai_mtd = _cost_module.xai_month_to_date_spend_usd(conn)
_combined_mtd = _anthropic_mtd + _xai_mtd
_combined_cap = _cost_module.get_monthly_ceiling_usd(conn)
_combined_pct = (_combined_mtd / _combined_cap) if _combined_cap > 0 else 0.0
_combined_pct_capped = max(0.0, min(_combined_pct, 1.0))
_combined_color = (
    "#d97e7e" if _combined_pct >= 1.0
    else "#d9a86b" if _combined_pct >= 0.8
    else "#7ec97e"
)
st.progress(
    _combined_pct_capped,
    text=(
        f"${_combined_mtd:.2f} of ${_combined_cap:.2f} "
        f"({_combined_pct * 100:.1f}%)"
    ),
)
st.markdown(
    f"<div style='font-family:\"JetBrains Mono\", monospace;"
    f"font-size:0.86rem;color:{_combined_color};margin-bottom:0.5rem;'>"
    f"Anthropic: ${_anthropic_mtd:.4f} · xAI Grok: ${_xai_mtd:.4f}"
    f"</div>",
    unsafe_allow_html=True,
)
if _combined_pct >= 1.0:
    st.error(
        "Combined AI ceiling reached. Anthropic agent calls AND Grok sweep "
        "are both paused. Raise the cap above (Growth Agent → "
        "combined_ai_monthly_cost_ceiling_usd) or wait for the next month."
    )
elif _combined_pct >= 0.8:
    st.warning(
        f"Combined AI spend at {_combined_pct * 100:.0f}% of the ceiling — "
        "yellow banner per §28.6."
    )

# ----- Recent Grok failures (last 7 days) -----
_grok_fail_window_days = 7
try:
    _grok_fail_rows = conn.execute(
        """
        SELECT id, query, response_status_code, rejection_reason,
               created_at_utc
          FROM grok_api_responses
         WHERE (rejection_reason IS NOT NULL
                OR (response_status_code IS NOT NULL AND response_status_code >= 400))
           AND created_at_utc >= datetime('now', ?)
         ORDER BY created_at_utc DESC
         LIMIT 50
        """,
        (f"-{_grok_fail_window_days} days",),
    ).fetchall()
except sqlite3.OperationalError:
    # grok_api_responses missing — pre-migration-021 DB. Render empty.
    _grok_fail_rows = []

st.markdown(
    f"<div style='margin:0.8rem 0 0.3rem 0;font-size:0.92rem;'>"
    f"<strong style='font-family:\"IBM Plex Sans\", sans-serif;'>"
    f"Recent Grok failures</strong>"
    f"<span class='dim'> · last {_grok_fail_window_days} days · "
    f"{len(_grok_fail_rows)} event(s)</span>"
    f"</div>",
    unsafe_allow_html=True,
)
if not _grok_fail_rows:
    st.markdown(
        "<div class='dim' style='font-size:0.86rem;margin-bottom:0.5rem;'>"
        "(no Grok failures recorded in the window)</div>",
        unsafe_allow_html=True,
    )
else:
    for _gfail in _grok_fail_rows:
        _gf_status = _gfail[2]
        _gf_reason = _gfail[3] or ""
        _gf_badge_color = "#d97e7e"
        if _gf_reason == "verification_404":
            _gf_badge_color = "#a39d92"  # informational, not failure
        elif _gf_reason in ("rate_limit_429", "cost_ceiling_hit"):
            _gf_badge_color = "#d9a86b"
        elif _gf_status is not None and _gf_status >= 500:
            _gf_badge_color = "#d97e7e"
        _gf_status_label = (
            f"HTTP {int(_gf_status)}" if _gf_status is not None else "—"
        )
        st.markdown(
            f"<div style='font-family:\"JetBrains Mono\", monospace;"
            f"font-size:0.82rem;line-height:1.55;'>"
            f"<span style='color:#a39d92;'>{html.escape(str(_gfail[4]))}</span> · "
            f"<span style='color:{_gf_badge_color};'>{html.escape(_gf_status_label)}</span> · "
            f"<span style='color:{_gf_badge_color};'>{html.escape(_gf_reason or 'http_error')}</span> · "
            f"<span class='dim'>{html.escape(str(_gfail[1] or '')[:80])}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# §14.7 / §28.30 — Audit log viewer (Phase 5.11).
# ---------------------------------------------------------------------------
# Read-only window onto every state-changing event in the system. The
# agent has NO read access to audit_logs; this surface is Daniel's
# debugging + recovery tool only.
hairline()
st.subheader("Audit log")
st.markdown(
    "<div class='dim' style='margin-bottom:0.6rem;font-size:0.86rem;'>"
    "Append-only canonical record of state-changing events "
    "(settings, exports, publishes, backups, migrations, data writes). "
    "The agent has no access to this table — Daniel-only.</div>",
    unsafe_allow_html=True,
)

from app.agent import audit_log as _audit_log  # noqa: E402

_AUDIT_CAT_OPTIONS: list[str] = ["all"] + sorted(_audit_log.ALLOWED_CATEGORIES)
if "audit_log_filter_category" not in st.session_state:
    st.session_state["audit_log_filter_category"] = "all"
if "audit_log_filter_limit" not in st.session_state:
    st.session_state["audit_log_filter_limit"] = 50

_audit_filter_cols = st.columns([2, 1, 1])
with _audit_filter_cols[0]:
    st.selectbox(
        "category",
        options=_AUDIT_CAT_OPTIONS,
        key="audit_log_filter_category",
    )
with _audit_filter_cols[1]:
    st.number_input(
        "limit",
        min_value=10,
        max_value=500,
        step=10,
        key="audit_log_filter_limit",
    )
with _audit_filter_cols[2]:
    st.write("")  # spacer
    if st.button("refresh", key="audit_log_refresh"):
        st.rerun()

_audit_category_selected = st.session_state["audit_log_filter_category"]
_audit_rows = _audit_log.query(
    conn,
    category=None if _audit_category_selected == "all" else _audit_category_selected,
    limit=int(st.session_state["audit_log_filter_limit"]),
)

st.markdown(
    f"<div class='kicker'>{len(_audit_rows)} event(s)</div>",
    unsafe_allow_html=True,
)

for _arow in _audit_rows:
    _arow_emoji = "✓" if _arow.success else "✗"
    _arow_target = (
        f"{_arow.target_type}#{_arow.target_id}"
        if _arow.target_type and _arow.target_id
        else (_arow.target_type or "")
    )
    with st.expander(
        f"{_arow.occurred_at_utc}  ·  [{_arow.event_category}] "
        f"{_arow.event_type}  ·  {_arow_emoji}  {_arow_target}".strip()
    ):
        st.markdown(
            f"<div class='dim' style='font-size:0.86rem;'>"
            f"id <span class='numeric'>{_arow.id}</span> · "
            f"actor <span class='numeric'>{html.escape(_arow.actor)}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if _arow.error_message:
            st.markdown(
                f"<div class='dim' style='font-size:0.86rem;color:#d97e7e;'>"
                f"error: {html.escape(_arow.error_message)}</div>",
                unsafe_allow_html=True,
            )
        if _arow.details:
            st.json(_arow.details, expanded=False)
