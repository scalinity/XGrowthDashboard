"""Phase 3 view-level tests — spec.md §14 acceptance gates.

Two layers:

1. **Pure-function tests** for the components (`ui_label_for_db_label`,
   `count_rankable_lanes`, `build_funnel_stages`). No Streamlit runtime
   needed. These are the load-bearing accuracy-rule assertions.

2. **AppTest smoke tests** for each page — boots Streamlit's headless
   harness, points it at a seeded DB, and confirms the page renders
   without exception. The Phase 3 acceptance gate ("all seven sidebar
   pages render without errors with a populated dev DB") is enforced here.

Run with: `uv run pytest tests/test_dashboard_views.py -v`.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Make project root importable before app.* imports.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.components.badges.confidence_label import (  # noqa: E402
    DB_LABEL_TO_UI,
    UI_LABEL_PRESENTATION,
    ui_label_for_db_label,
)
from app.components.charts.funnel import (  # noqa: E402
    APP_STORE_GAP_LABEL,
    APP_STORE_GAP_ICON,
    build_funnel_stages,
)
from app.components.charts.lane_grid import (  # noqa: E402
    LaneRow,
    count_rankable_lanes,
)


# ===========================================================================
# Pure-function tests — confidence label mapping (Phase 3 acceptance gate).
# ===========================================================================

@pytest.mark.parametrize(
    "db_label, expected_ui_label",
    [
        ("insufficient sample",                "insufficient"),
        ("low — show scatter, do not rank",    "directional"),
        ("moderate",                            "tentative"),
        ("stronger",                            "confident"),
    ],
)
def test_ui_label_maps_each_db_label_to_phase3_ui_label(
    db_label: str, expected_ui_label: str
) -> None:
    """The four DB labels surface as the four user-facing names per the
    Phase 3 prompt: insufficient / directional / tentative / confident."""
    assert ui_label_for_db_label(db_label) == expected_ui_label


def test_ui_label_unknown_input_falls_back_to_insufficient() -> None:
    """A future DB change must not silently produce a confidently-ranked lane."""
    assert ui_label_for_db_label("some-future-string") == "insufficient"
    assert ui_label_for_db_label(None) == "insufficient"
    assert ui_label_for_db_label("") == "insufficient"


def test_db_to_ui_mapping_covers_every_db_label() -> None:
    """The four-tier mapping is complete — no DB label leaks through unmapped."""
    expected_db_labels = {
        "insufficient sample",
        "low — show scatter, do not rank",
        "moderate",
        "stronger",
    }
    assert set(DB_LABEL_TO_UI.keys()) == expected_db_labels


def test_ui_label_presentation_has_every_ui_label() -> None:
    """Every UI label has a colour/description; the badge component can't crash."""
    expected_ui_labels = {"insufficient", "directional", "tentative", "confident"}
    assert set(UI_LABEL_PRESENTATION.keys()) == expected_ui_labels


# ===========================================================================
# count_rankable_lanes — gates the "best lane" callout in Content Performance.
# ===========================================================================

def _lane_row_with(db_label: str) -> LaneRow:
    """Build a minimal LaneRow with just a confidence label."""
    return LaneRow(
        pillar="stir", audience="icp", cta="ask",
        post_count=1, days_covered=1,
        median_impressions=100.0, iqr_low=50.0, iqr_high=150.0,
        total_bookmarks=0, total_replies=0,
        stir_signal_count=0,
        db_confidence_label=db_label,
    )


def test_count_rankable_lanes_counts_only_tentative_and_above() -> None:
    rows = [
        _lane_row_with("insufficient sample"),
        _lane_row_with("low — show scatter, do not rank"),
        _lane_row_with("moderate"),
        _lane_row_with("stronger"),
        _lane_row_with("moderate"),
    ]
    # tentative (moderate) + confident (stronger) + tentative → 3.
    assert count_rankable_lanes(rows) == 3


def test_count_rankable_lanes_zero_when_only_insufficient_and_directional() -> None:
    rows = [
        _lane_row_with("insufficient sample"),
        _lane_row_with("low — show scatter, do not rank"),
        _lane_row_with("low — show scatter, do not rank"),
    ]
    assert count_rankable_lanes(rows) == 0


# ===========================================================================
# Funnel stages — the App Store gap row is always present.
# ===========================================================================

def test_funnel_stages_contain_visible_app_store_gap() -> None:
    stages = build_funnel_stages(
        impressions=1000,
        profile_visits_self_reported=50,
        app_store_clicks_self_reported=20,
        downloads=5,
        icp_testers_self_reported=2,
    )
    # The gap stage exists and carries the §14.5 marker text.
    gap_stages = [s for s in stages if s.is_gap]
    assert len(gap_stages) == 1, "exactly one gap row required (§14.5)"
    assert APP_STORE_GAP_LABEL in gap_stages[0].label
    assert APP_STORE_GAP_ICON in gap_stages[0].label
    # Gap row never carries a numeric value.
    assert gap_stages[0].value == 0


def test_funnel_stages_app_store_gap_sits_between_clicks_and_downloads() -> None:
    """Order is load-bearing — the gap separates the two epistemic categories."""
    stages = build_funnel_stages(
        impressions=0,
        profile_visits_self_reported=0,
        app_store_clicks_self_reported=10,
        downloads=2,
        icp_testers_self_reported=0,
    )
    labels = [s.label for s in stages]
    click_idx = next(i for i, label in enumerate(labels) if "App-store-click" in label)
    gap_idx = next(i for i, label in enumerate(labels) if APP_STORE_GAP_LABEL in label)
    dl_idx = next(i for i, label in enumerate(labels) if "Downloads" in label)
    assert click_idx < gap_idx < dl_idx


# ===========================================================================
# AppTest smoke — every page renders without exception with a seeded DB.
# ===========================================================================

@pytest.fixture
def seeded_db_path(tmp_path: Path) -> Path:
    """Build a temporary DB with migrations + seeds + a few rows the pages need."""
    from app.db import apply_migrations, connect
    from scripts.seed_milestones import seed_milestones
    from scripts.seed_settings import seed_settings

    db_path = tmp_path / "viewtest.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    seed_milestones(conn)

    # Seed a snapshot for today so Today view renders the weigh-in cards.
    today_iso = date.today().isoformat()
    conn.execute(
        """
        INSERT INTO account_snapshots
          (snapshot_date, collected_at_utc, username, profile_url,
           followers_count, following_count, post_count, listed_count,
           baseline_followers, source, data_quality)
        VALUES (?, ?, 'dannyscalant', 'https://x.com/dannyscalant',
                75, 100, 5, 0, 61, 'manual', 'manual')
        """,
        (today_iso, f"{today_iso}T09:00:00Z"),
    )
    # Daily activity for today.
    conn.execute(
        """
        INSERT INTO daily_activity (activity_date, posts_shipped, replies_shipped,
                                    reply_sessions_completed, minimum_reps_completed)
        VALUES (?, 1, 12, 1, 1)
        """,
        (today_iso,),
    )
    conn.close()
    return db_path


def _run_apptest(page_path: Path, db_path: Path):
    """Run an AppTest for a given page, redirecting open_connection() at our temp DB.

    `app.pages.open_connection` re-imported DEFAULT_DB_PATH from app.db at
    package-import time, so it has its own binding in the `app.pages`
    namespace. Patching `app.db.DEFAULT_DB_PATH` alone does not reach it —
    we patch both bindings to keep them in sync for the run.
    """
    from streamlit.testing.v1 import AppTest

    import app.db as db_module
    import app.pages as pages_module
    original_db = db_module.DEFAULT_DB_PATH
    original_pages = pages_module.DEFAULT_DB_PATH
    db_module.DEFAULT_DB_PATH = db_path
    pages_module.DEFAULT_DB_PATH = db_path
    try:
        at = AppTest.from_file(str(page_path), default_timeout=30)
        at.run()
        return at
    finally:
        db_module.DEFAULT_DB_PATH = original_db
        pages_module.DEFAULT_DB_PATH = original_pages


_PAGES = [
    ("1_Today.py",                  "Today"),
    ("2_Next_Rep.py",               "Next rep"),
    ("3_Progress.py",               "Progress"),
    ("4_Content_Performance.py",    "Content performance"),
    ("5_Funnel.py",                 "Funnel"),
    ("6_Weekly_Review.py",          "Weekly review"),
    ("7_Settings.py",               "Settings"),
]


@pytest.mark.parametrize("page_file, expected_title", _PAGES)
def test_each_page_renders_without_exception(
    seeded_db_path: Path, page_file: str, expected_title: str
) -> None:
    """Phase 3 acceptance gate: every sidebar page renders cleanly on a populated DB."""
    page_path = PROJECT_ROOT / "app" / "pages" / page_file
    at = _run_apptest(page_path, seeded_db_path)
    assert not at.exception, f"{page_file} raised: {[str(e) for e in at.exception]}"
    titles = [t.value for t in at.title]
    assert expected_title in titles, f"{page_file} missing title '{expected_title}', got {titles}"


# ===========================================================================
# Acceptance gate — confidence labels at (3, 5, 15, 30) post sample sizes
# render as insufficient / directional / tentative / confident.
# ===========================================================================

def _insert_lane(conn: sqlite3.Connection, lane: tuple[str, str, str], post_count: int, days: int) -> None:
    """Insert `post_count` posts into a lane across `days` distinct dates."""
    pillar, audience, cta = lane
    base = date(2026, 4, 1)
    next_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM posts"
    ).fetchone()[0] + 1
    for i in range(post_count):
        day_offset = i if i < days else days - 1
        d = (base + timedelta(days=day_offset)).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO posts
              (x_post_id, created_at_utc, created_date, text, type,
               posted_via, manual_confirmation_status)
            VALUES (?, ?, ?, ?, 'standalone', 'manual', 'confirmed')
            """,
            (f"x{next_id}_{i}", f"{d}T12:00:00Z", d, f"post {i}"),
        )
        post_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO post_classifications (post_id, pillar, audience, cta) VALUES (?, ?, ?, ?)",
            (post_id, pillar, audience, cta),
        )
        conn.execute(
            """
            INSERT INTO post_metric_snapshots
              (post_id, x_post_id, collected_at_utc, impressions, source, data_quality)
            VALUES (?, ?, ?, ?, 'manual', 'manual')
            """,
            (post_id, f"x{next_id}_{i}", f"{d}T13:00:00Z", 100 * (i + 1)),
        )
    next_id += post_count


@pytest.mark.parametrize(
    "post_count, days_covered, expected_ui_label",
    [
        (3,  3,  "insufficient"),  # n<5 → insufficient regardless of days
        (5,  5,  "directional"),   # 5..14 → directional
        (15, 7,  "tentative"),     # 15..29 with 7+ days → tentative
        (30, 14, "confident"),     # ≥30 with 14+ days → confident
    ],
)
def test_phase3_acceptance_gate_confidence_labels_at_boundary_sample_sizes(
    db_conn: sqlite3.Connection,
    post_count: int,
    days_covered: int,
    expected_ui_label: str,
) -> None:
    """The Phase 3 acceptance gate names these exact sample sizes — seed
    (3, 5, 15, 30) across four lanes, then check the UI label mapping
    surfaces (insufficient, directional, tentative, confident)."""
    lane = (f"pillar_{post_count}", f"audience_{post_count}", f"cta_{post_count}")
    _insert_lane(db_conn, lane, post_count, days_covered)
    row = db_conn.execute(
        "SELECT confidence_label FROM v_lane_performance WHERE pillar = ?",
        (lane[0],),
    ).fetchone()
    assert row is not None, f"lane {lane} did not appear in v_lane_performance"
    ui_label = ui_label_for_db_label(row["confidence_label"])
    assert ui_label == expected_ui_label, (
        f"n={post_count}, days={days_covered} → expected UI label "
        f"{expected_ui_label!r}, got {ui_label!r} (DB label: {row['confidence_label']!r})"
    )


def test_phase3_acceptance_gate_insufficient_lane_grid_shows_dash(
    db_conn: sqlite3.Connection,
) -> None:
    """The 3-post lane appears in the grid as "—" not as a numeric median."""
    from app.components.charts.lane_grid import _format_median_with_iqr, lane_rows_from_sql

    _insert_lane(db_conn, ("stir", "icp", "ask"), 3, 3)
    rows = db_conn.execute(
        "SELECT * FROM v_lane_performance WHERE pillar='stir'"
    ).fetchall()
    lane_rows = lane_rows_from_sql(rows)
    assert lane_rows, "lane should appear in v_lane_performance"
    assert _format_median_with_iqr(lane_rows[0]) == "—"


def test_phase3_acceptance_gate_no_best_lane_callout_below_three_rankable(
    db_conn: sqlite3.Connection,
) -> None:
    """With only one tentative+ lane seeded, count_rankable_lanes < 3 → no callout."""
    from app.components.charts.lane_grid import lane_rows_from_sql

    # One tentative lane only.
    _insert_lane(db_conn, ("stir", "icp", "ask"), 15, 7)
    # Two more lanes below threshold.
    _insert_lane(db_conn, ("build", "other", "none"), 3, 3)
    _insert_lane(db_conn, ("self", "icp", "none"), 4, 3)
    rows = db_conn.execute("SELECT * FROM v_lane_performance").fetchall()
    lane_rows = lane_rows_from_sql(rows)
    assert count_rankable_lanes(lane_rows) == 1
    # 1 < 3 → callout would not render in the view per the gate.


def test_phase3_acceptance_gate_best_lane_callout_when_three_rankable(
    db_conn: sqlite3.Connection,
) -> None:
    """Three tentative+ lanes → count_rankable_lanes >= 3 → callout renders."""
    from app.components.charts.lane_grid import lane_rows_from_sql

    _insert_lane(db_conn, ("stir", "icp", "ask"), 15, 7)
    _insert_lane(db_conn, ("build", "other", "none"), 20, 8)
    _insert_lane(db_conn, ("self", "icp", "none"), 18, 9)
    rows = db_conn.execute("SELECT * FROM v_lane_performance").fetchall()
    lane_rows = lane_rows_from_sql(rows)
    assert count_rankable_lanes(lane_rows) >= 3


# ===========================================================================
# Funnel acceptance gate — App Store gap label is rendered.
# ===========================================================================

def test_funnel_view_renders_app_store_gap_label(seeded_db_path: Path) -> None:
    """The funnel page must render the §14.5 gap marker in user-visible markdown."""
    page_path = PROJECT_ROOT / "app" / "pages" / "5_Funnel.py"
    at = _run_apptest(page_path, seeded_db_path)
    assert not at.exception, f"funnel raised: {at.exception}"

    # Collect every markdown body the page emitted.
    md_bodies = [md.value for md in at.markdown]
    blob = "\n".join(md_bodies)
    assert APP_STORE_GAP_LABEL in blob, (
        "App Store gap label missing from rendered markdown — §14.5 not surfaced."
    )


# ===========================================================================
# Weekly Review acceptance gate — export disabled until counterfactual filled.
# ===========================================================================

def test_weekly_review_export_button_disabled_when_no_counterfactual(
    seeded_db_path: Path,
) -> None:
    """No `weekly_reviews` row for this week → counterfactual missing → export disabled."""
    page_path = PROJECT_ROOT / "app" / "pages" / "6_Weekly_Review.py"
    at = _run_apptest(page_path, seeded_db_path)
    assert not at.exception, f"weekly_review raised: {at.exception}"

    # Find the Markdown export button — it's labelled per the page.
    export_buttons = [
        b for b in at.button
        if "Export weekly report" in (b.label or "")
    ]
    assert len(export_buttons) == 1, (
        f"expected exactly one 'Export weekly report' button, got {len(export_buttons)}"
    )
    assert export_buttons[0].disabled is True, (
        "Export button must be disabled when the current week has no "
        "weekly_reviews row with a non-empty counterfactual_note (§14.6)."
    )


def test_weekly_review_export_enabled_when_counterfactual_filled(
    seeded_db_path: Path,
) -> None:
    """Insert a weekly_reviews row with a counterfactual; export becomes enabled."""
    # Seed a saved weekly review row for the current ISO week.
    from app.db import connect
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    conn = connect(seeded_db_path)
    conn.execute(
        """
        INSERT INTO weekly_reviews
          (week_start_date, week_end_date, posts_shipped, replies_shipped,
           reply_sessions_completed, daily_reps_days_completed,
           downloads, qualified_icp_testers, counterfactual_note)
        VALUES (?, ?, 0, 0, 0, 0, 0, 0, 'algorithm shift this week — DAU on a similar account fell ~12%')
        """,
        (monday.isoformat(), sunday.isoformat()),
    )
    conn.close()

    page_path = PROJECT_ROOT / "app" / "pages" / "6_Weekly_Review.py"
    at = _run_apptest(page_path, seeded_db_path)
    assert not at.exception, f"weekly_review raised: {at.exception}"

    export_buttons = [
        b for b in at.button
        if "Export weekly report" in (b.label or "")
    ]
    assert len(export_buttons) == 1
    assert export_buttons[0].disabled is False, (
        "Export button must be enabled once the current week's "
        "counterfactual_note is non-empty."
    )


# ===========================================================================
# Settings acceptance gate — every §10.2 settings key visible.
# ===========================================================================

def test_settings_page_surfaces_every_seeded_settings_key(
    seeded_db_path: Path,
) -> None:
    """The §10.2 keys exercised by Phase 1 seed all appear in the rendered page."""
    from scripts.seed_settings import documented_keys

    page_path = PROJECT_ROOT / "app" / "pages" / "7_Settings.py"
    at = _run_apptest(page_path, seeded_db_path)
    assert not at.exception, f"settings raised: {at.exception}"

    md_blob = "\n".join(md.value for md in at.markdown)
    # The page renders each key as `<span class='numeric'>{key}</span>` so the
    # plain-text key string is visible in the markdown body.
    missing = [k for k in documented_keys() if k not in md_blob]
    # `x_user_id` defaults to NULL and may render via the read-only path —
    # so use a tolerant assertion: at minimum every editable key must surface.
    editable_keys = {
        "x_handle", "profile_url", "baseline_followers", "timezone",
        "daily_snapshot_time",
        "operational_ceiling", "long_arc_reminder", "current_milestone",
        "daily_post_target", "daily_reply_target", "daily_reply_session_target",
        "target_calibration_review_date",
        "lane_sample_size_insufficient", "lane_sample_size_low",
        "lane_sample_size_stronger", "lane_days_covered_minimum",
        "velocity_7d_display_threshold", "counterfactual_required",
        "data_collection_mode", "backup_dir", "export_dir",
        "weekly_report_export_path",
    }
    missing_editable = sorted(k for k in editable_keys if k not in md_blob)
    assert not missing_editable, (
        f"Settings page missing editable keys: {missing_editable}. "
        f"(All-key delta: {missing})"
    )
