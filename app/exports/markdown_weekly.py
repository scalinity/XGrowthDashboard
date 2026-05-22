"""Markdown weekly-report exporter — spec.md §16, §14.6, §24.

The single load-bearing rule: the report is gated on
``weekly_reviews.counterfactual_note`` regardless of the
``counterfactual_required`` settings toggle. The toggle is for transitional
dev-mode form entry; the export is the artifact a future-Daniel reads back
in 6 months and the counterfactual note is the only thing standing between
his interpretation and a causal claim he can't support. So gating happens
in this module, not (only) in the form layer.

Output shape closely follows §24's example. We deliberately don't render
every {{placeholder}} from §24 verbatim — some require data the MVP doesn't
collect yet (e.g. ``best_post_lesson``). Sections that lack data render as
"—" rather than failing; the §14.6 acceptance criteria require the report
to *audit* the interpretation, which means showing absences too.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from app.db import DEFAULT_DB_PATH, PROJECT_ROOT, apply_migrations, connect
from app.exports._audit import EXPORT_KIND_MARKDOWN_WEEKLY, record_export


class CounterfactualMissingError(RuntimeError):
    """Raised when ``weekly_reviews.counterfactual_note`` is missing or empty.

    Per §14.6 / §16, the weekly Markdown report MUST include the
    counterfactual note — that's the whole epistemic point of the §14.6
    review. The exporter checks for NULL and whitespace-only and refuses to
    proceed; the caller surfaces this to the user via ``st.error`` in the UI
    or stderr at the CLI.
    """

    def __init__(self, week_iso: str, week_start_date: str | None = None) -> None:
        if week_start_date:
            msg = (
                f"Cannot export weekly report for {week_iso}: the "
                f"`counterfactual_note` for the week of {week_start_date} is "
                f"empty. Fill it in via the Weekly Review form (§14.6) and "
                f"re-run the export."
            )
        else:
            msg = (
                f"Cannot export weekly report for {week_iso}: no weekly_reviews "
                f"row exists for that ISO week. Save the Weekly Review for that "
                f"week before exporting."
            )
        super().__init__(msg)
        self.week_iso = week_iso
        self.week_start_date = week_start_date


@dataclass(frozen=True)
class MarkdownWeeklyExportResult:
    path: Path
    week_iso: str
    week_start_date: str
    week_end_date: str
    byte_count: int


# Strict regex; rejects "2026-W5", "2026W21", "2026-W00", and "2026-W54+".
# 4-digit year, "-W", zero-padded ISO week in [01, 53]. Range-checking at
# the regex level (rather than the body of _iso_week_to_dates) keeps the
# error path uniform: malformed input gets one error message
# ("Invalid ISO week"), Gregorian-impossible input gets a different one
# ("does not exist in the Gregorian calendar"). /review-2 W6.
_ISO_WEEK_RE: re.Pattern[str] = re.compile(r"^(\d{4})-W(0[1-9]|[1-4]\d|5[0-3])$")


def _anchor_on_project_root(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _iso_week_to_dates(week_iso: str) -> tuple[date, date]:
    """Convert ``YYYY-Www`` to (Monday, Sunday) calendar dates.

    Uses :meth:`datetime.date.fromisocalendar` (3.8+) which guarantees ISO
    8601 week semantics. Day 1 is Monday, day 7 is Sunday.
    """
    match = _ISO_WEEK_RE.match(week_iso)
    if not match:
        raise ValueError(
            f"Invalid ISO week {week_iso!r}; expected format 'YYYY-Www' "
            "(e.g. '2026-W21')."
        )
    year, week = int(match.group(1)), int(match.group(2))
    try:
        monday = date.fromisocalendar(year, week, 1)
        sunday = date.fromisocalendar(year, week, 7)
    except ValueError as exc:
        # date.fromisocalendar raises when the year+week combination is
        # invalid (e.g. 2026-W53 doesn't exist).
        raise ValueError(
            f"ISO week {week_iso!r} does not exist in the Gregorian calendar."
        ) from exc
    return monday, sunday


def _fetch_review(conn: sqlite3.Connection, week_start_date: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
          FROM weekly_reviews
         WHERE week_start_date = ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (week_start_date,),
    ).fetchone()


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _fmt_int(value: object, fallback: str = "—") -> str:
    if value is None:
        return fallback
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return fallback


def _fmt_str(value: object, fallback: str = "—") -> str:
    if _is_blank(value):
        return fallback
    return str(value)


def _fmt_signed_int(value: object, fallback: str = "—") -> str:
    if value is None:
        return fallback
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    if n == 0:
        return "0"
    return f"{n:+,}"


def _fetch_funnel_totals(
    conn: sqlite3.Connection,
    week_start_date: str,
    week_end_date: str,
) -> dict[str, int]:
    """Aggregate funnel signals for the week from ``v_funnel_daily``.

    The view returns ``COALESCE`` zeros for the per-event columns, so a
    week with no events still returns an all-zeros row. Returns a dict
    keyed by funnel stage name — caller renders.

    /review-2 W4 (PRE-EXISTING): ``v_funnel_daily`` is anchored on
    ``stir_conversion_events``, so a tester who downloaded on a date
    with NO event row is silently absent from the view — and the
    week's parent count drops to zero even when ``stir_testers`` has
    matching rows. Rather than touching the immutable applied view
    (Phase 1 surface), we query ``stir_testers`` independently here
    and override the view-derived ``working_parent_home_cook_testers``
    with the count from the testers table itself.
    """
    rows = conn.execute(
        """
        SELECT
            COALESCE(SUM(x_impressions_estimate),   0) AS x_impressions_estimate,
            COALESCE(SUM(profile_visits),           0) AS profile_visits,
            COALESCE(SUM(link_clicks),              0) AS link_clicks,
            COALESCE(SUM(getstir_visits),           0) AS getstir_visits,
            COALESCE(SUM(downloads),                0) AS downloads,
            COALESCE(SUM(waitlist_signups),         0) AS waitlist_signups,
            COALESCE(SUM(kitchen_scans),            0) AS kitchen_scans,
            COALESCE(SUM(three_options_generated),  0) AS three_options_generated,
            COALESCE(SUM(cook_mode_started),        0) AS cook_mode_started,
            COALESCE(SUM(qualified_icp_testers),    0) AS qualified_icp_testers,
            COALESCE(SUM(working_parent_home_cook_testers), 0)
                                                        AS working_parent_home_cook_testers
        FROM v_funnel_daily
        WHERE event_date BETWEEN ? AND ?
        """,
        (week_start_date, week_end_date),
    ).fetchone()
    totals = {k: int(rows[k] or 0) for k in rows.keys()}

    # Override the view's parent count with a direct stir_testers query
    # so event-less download dates are still counted.
    parent_row = conn.execute(
        """
        SELECT COUNT(*) AS parents
          FROM stir_testers
         WHERE is_working_parent_home_cook = 1
           AND downloaded_app_at IS NOT NULL
           AND DATE(downloaded_app_at) BETWEEN ? AND ?
        """,
        (week_start_date, week_end_date),
    ).fetchone()
    totals["working_parent_home_cook_testers"] = int(parent_row["parents"] or 0)
    return totals


def _fetch_top_lanes(conn: sqlite3.Connection, limit: int = 3) -> list[sqlite3.Row]:
    """Top lanes by median impressions among non-insufficient confidence.

    v_lane_performance is computed across all time, not just the week — the
    weekly report's "content lanes" section is meant to summarise which
    lanes are working AT ALL, with the confidence label visible so the
    reader can judge.
    """
    return conn.execute(
        """
        SELECT pillar, audience, cta, post_count, days_covered,
               median_impressions, median_engagement_rate, confidence_label
          FROM v_lane_performance
         WHERE confidence_label IS NOT NULL
           AND confidence_label <> 'insufficient sample'
         ORDER BY COALESCE(median_impressions, 0) DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _fetch_open_hypotheses(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT name, hypothesis, content_lane, target_audience, success_metric,
               minimum_sample_size, start_date, end_date
          FROM experiments
         WHERE status = 'running'
         ORDER BY start_date ASC
        """
    ).fetchall()


# §13 hard rules surfaced verbatim in "What we know / what we don't know".
# Phase 5 prompt explicitly asks for these; the alternative ("link to spec.md
# §13") is only allowed if the resulting report exceeds 1,000 lines.
_HARD_RULES_BULLETS: tuple[str, ...] = (
    "Follower count is a *stock*; posts/replies/downloads are *flow*. "
    "Don't compare the two as if they were the same kind of number.",
    "Velocity (`velocity_7d_per_day`) is suppressed when `|Δ7d| < 10` — below "
    "that, the displayed trend is noise, not signal.",
    "Engagement rate is computed by us when X's `engagements_total` is null; "
    "it's labeled `approx`. Don't read the labeled value as exact.",
    "A follower who appears after a post is NOT attributed to that post "
    "unless they explicitly self-reported it.",
    "App Store downloads are NEVER auto-attributed to a specific X post or "
    "reply — the UTM chain doesn't survive the App Store jump. Attribution "
    "for downloads is self-reported only. (§14.5)",
    "ICP / working-parent / home-cook classification is stored ONLY when "
    "self-reported. The dashboard does not infer.",
    "Sample-size labels frame a question about evidence, not failure. "
    "`insufficient sample` means *we don't know yet*, not *this didn't work*.",
)


def _render_app_store_gap_block(funnel: dict[str, int]) -> str:
    """Render the funnel with the App-Store-attribution-gap label visible.

    Per §14.5: "site visits: 47 (UTM-attributed)" and "downloads: 3
    (self-reported source)" are different epistemic categories. The report
    must show this asymmetry rather than hiding it.
    """
    lines = [
        "**Distribution signal (X-side)**",
        "",
        f"- X impressions (estimate): `{funnel['x_impressions_estimate']:,}`",
        f"- Profile visits: `{funnel['profile_visits']:,}`",
        f"- Link clicks: `{funnel['link_clicks']:,}`",
        f"- getstir.app visits (UTM-attributed): `{funnel['getstir_visits']:,}`",
        "",
        "*App Store attribution gap (§14.5):* UTM tagging works fine for "
        "getstir.app visits but does NOT survive the jump to the App Store. "
        "Everything below is self-reported by testers, not auto-attributed.",
        "",
        "**Validation signal (Stir-side, self-reported)**",
        "",
        f"- Downloads: `{funnel['downloads']:,}` (self-reported source)",
        f"- Waitlist signups: `{funnel['waitlist_signups']:,}`",
        f"- Kitchen scans: `{funnel['kitchen_scans']:,}`",
        f"- 3 plausible dinners generated: `{funnel['three_options_generated']:,}`",
        f"- Cook Mode starts: `{funnel['cook_mode_started']:,}`",
        f"- Qualified ICP testers (self-reported): `{funnel['qualified_icp_testers']:,}`",
        f"- Working-parent / home-cook testers (self-reported): "
        f"`{funnel['working_parent_home_cook_testers']:,}`",
    ]
    return "\n".join(lines)


def _render_top_lanes(lanes: list[sqlite3.Row]) -> str:
    if not lanes:
        return (
            "No lanes have enough data to rank yet. "
            "Per §13, `insufficient sample` means *we don't know yet*, "
            "not *nothing is working*."
        )
    lines: list[str] = []
    lines.append("| # | Pillar | Audience | CTA | Posts | Days | Median impressions | Confidence |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, row in enumerate(lanes, start=1):
        lines.append(
            f"| {i}"
            f" | {_fmt_str(row['pillar'])}"
            f" | {_fmt_str(row['audience'])}"
            f" | {_fmt_str(row['cta'])}"
            f" | {_fmt_int(row['post_count'])}"
            f" | {_fmt_int(row['days_covered'])}"
            f" | {_fmt_int(row['median_impressions'])}"
            f" | {_fmt_str(row['confidence_label'])} |"
        )
    return "\n".join(lines)


def _render_open_hypotheses(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "No open hypotheses. Consider seeding next week's experiment in the Weekly Review form."
    lines: list[str] = []
    for r in rows:
        end_segment = f" → {_fmt_str(r['end_date'])}" if r["end_date"] else ""
        lines.append(
            f"- **{_fmt_str(r['name'])}** "
            f"(started {_fmt_str(r['start_date'])}{end_segment})"
        )
        lines.append(f"  *Hypothesis:* {_fmt_str(r['hypothesis'])}  ")
        lines.append(
            f"  *Lane:* {_fmt_str(r['content_lane'])} · "
            f"*Audience:* {_fmt_str(r['target_audience'])}  "
        )
        lines.append(
            f"  *Success metric:* {_fmt_str(r['success_metric'])} · "
            f"*Min sample size:* {_fmt_int(r['minimum_sample_size'])}"
        )
        lines.append("")
    # Trim the trailing empty separator added after the last block so the
    # caller's section boundary isn't doubled.
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _render_hard_rules() -> str:
    return "\n".join(f"- {bullet}" for bullet in _HARD_RULES_BULLETS)


def _format_report(
    *,
    week_iso: str,
    week_start_date: str,
    week_end_date: str,
    review: sqlite3.Row,
    funnel: dict[str, int],
    top_lanes: list[sqlite3.Row],
    open_hypotheses: list[sqlite3.Row],
) -> str:
    follower_delta = review["follower_delta"]
    daily_reps = review["daily_reps_days_completed"]

    sections: list[str] = []

    sections.append(f"# X Growth Weekly Review — {week_iso} ({week_start_date} → {week_end_date})")
    sections.append("")
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sections.append(f"*Exported {exported_at} from spec.md §14.6 / §16 / §24.*")
    sections.append("")

    sections.append("## 1. Summary")
    sections.append("")
    sections.append(f"- Followers · start `{_fmt_int(review['followers_start'])}` → end `{_fmt_int(review['followers_end'])}` (Δ `{_fmt_signed_int(follower_delta)}`)")
    sections.append(f"- Posts shipped: `{_fmt_int(review['posts_shipped'])}`")
    sections.append(f"- Replies shipped: `{_fmt_int(review['replies_shipped'])}`")
    sections.append(f"- Reply sessions completed: `{_fmt_int(review['reply_sessions_completed'])}`")
    sections.append(f"- Daily reps days completed: `{_fmt_int(daily_reps)} / 7`")
    sections.append(f"- Downloads: `{_fmt_int(review['downloads'])}`")
    sections.append(f"- Qualified ICP testers (self-reported): `{_fmt_int(review['qualified_icp_testers'])}`")
    sections.append("")

    sections.append("## 2. Reps shipped")
    sections.append("")
    sections.append(
        f"- Posts: `{_fmt_int(review['posts_shipped'])}` · "
        f"Replies: `{_fmt_int(review['replies_shipped'])}` · "
        f"Reply sessions: `{_fmt_int(review['reply_sessions_completed'])}`."
    )
    sections.append(
        f"- Strongest pillar (self-call): {_fmt_str(review['strongest_pillar'])}."
    )
    sections.append(
        f"- Weakest pillar (self-call): {_fmt_str(review['weakest_pillar'])}."
    )
    sections.append("")

    sections.append("## 3. Content performance — top lanes")
    sections.append("")
    sections.append(_render_top_lanes(top_lanes))
    sections.append("")

    sections.append("## 4. Stir funnel — App-Store-attribution-gap visible")
    sections.append("")
    sections.append(_render_app_store_gap_block(funnel))
    sections.append("")

    sections.append("## 5. What moved? / What got stuck?")
    sections.append("")
    sections.append("**What moved:**")
    sections.append("")
    sections.append(_fmt_str(review["what_moved"], "(not filled in)"))
    sections.append("")
    sections.append("**What got stuck:**")
    sections.append("")
    sections.append(_fmt_str(review["what_got_stuck"], "(not filled in)"))
    sections.append("")

    sections.append("## 6. Lesson")
    sections.append("")
    sections.append(_fmt_str(review["lesson"], "(not filled in)"))
    sections.append("")

    sections.append("## 7. Next week's experiment")
    sections.append("")
    sections.append(_fmt_str(review["next_week_experiment"], "(not filled in)"))
    sections.append("")

    sections.append("## 8. Counterfactual — what this tool could not measure")
    sections.append("")
    sections.append(
        "*This section is the whole epistemic point of the weekly review per §14.6. "
        "Read it before reading anything else above as a causal claim.*"
    )
    sections.append("")
    sections.append(_fmt_str(review["counterfactual_note"]))
    sections.append("")

    sections.append("## 9. What we know / what we don't know (§13 hard rules)")
    sections.append("")
    sections.append(_render_hard_rules())
    sections.append("")

    sections.append("## 10. Open hypotheses")
    sections.append("")
    sections.append(_render_open_hypotheses(open_hypotheses))
    sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def export_weekly_report(
    week_iso: str,
    output_path: str | Path | None = None,
    *,
    db_path: str | Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> MarkdownWeeklyExportResult:
    """Render the §14.6 / §16 / §24 weekly Markdown report.

    Parameters
    ----------
    week_iso
        ISO week (``YYYY-Www``, e.g. ``2026-W21``).
    output_path
        Destination file. When None, defaults to
        ``<weekly_report_export_path>/weekly_report_<week>.md`` resolved
        against the settings table; falls back to ``data/exports/`` if
        the setting is unset.
    db_path, conn
        Same semantics as the other exporters.

    Raises
    ------
    ValueError
        Malformed ``week_iso``.
    CounterfactualMissingError
        No weekly_reviews row exists, or the counterfactual_note is empty.
    """
    monday, sunday = _iso_week_to_dates(week_iso)
    week_start_date = monday.isoformat()
    week_end_date = sunday.isoformat()

    own_conn = conn is None
    active = conn if conn is not None else connect(db_path)
    try:
        if own_conn:
            apply_migrations(active)

        review = _fetch_review(active, week_start_date)
        if review is None:
            raise CounterfactualMissingError(week_iso, None)
        if _is_blank(review["counterfactual_note"]):
            raise CounterfactualMissingError(week_iso, week_start_date)

        target = _resolve_output_path(active, output_path, week_iso)
        target.parent.mkdir(parents=True, exist_ok=True)

        funnel = _fetch_funnel_totals(active, week_start_date, week_end_date)
        top_lanes = _fetch_top_lanes(active, limit=3)
        hypotheses = _fetch_open_hypotheses(active)

        document = _format_report(
            week_iso=week_iso,
            week_start_date=week_start_date,
            week_end_date=week_end_date,
            review=review,
            funnel=funnel,
            top_lanes=top_lanes,
            open_hypotheses=hypotheses,
        )

        # /review-2 W1: atomic file + UPDATE + audit-INSERT.
        # Three-phase write so a DB failure can never leave an orphan
        # markdown file on disk:
        #   1. write the document to target.tmp (durable but invisible)
        #   2. BEGIN IMMEDIATE + UPDATE weekly_reviews + record_export + COMMIT
        #   3. os.replace(tmp, target) — atomic on POSIX
        # If any step fails before the rename, the tmp file is removed.
        # If the rename succeeds, the audit row's output_path is valid.
        tmp_target = target.with_suffix(target.suffix + ".tmp")
        tmp_target.write_text(document, encoding="utf-8")
        try:
            active.execute("BEGIN IMMEDIATE")
            try:
                active.execute(
                    """
                    UPDATE weekly_reviews
                       SET exported_markdown_path = ?,
                           updated_at = datetime('now')
                     WHERE id = ?
                    """,
                    (str(target), review["id"]),
                )
                record_export(
                    active,
                    kind=EXPORT_KIND_MARKDOWN_WEEKLY,
                    output_path=target,
                    row_count=1,
                    notes=f"week={week_iso}",
                )
                active.execute("COMMIT")
            except Exception:
                # Any failure between BEGIN and COMMIT — including the
                # IntegrityError re-raise from record_export — rolls back
                # both writes so the DB stays consistent with disk.
                active.execute("ROLLBACK")
                raise
            os.replace(tmp_target, target)
        except Exception:
            # File-write or rename failure after a successful commit is
            # rare (mkdir already ran, parent exists), but if it happens
            # we still want the orphan tmp gone.
            try:
                tmp_target.unlink()
            except FileNotFoundError:
                pass
            raise
        byte_count = target.stat().st_size
    finally:
        if own_conn:
            active.close()

    return MarkdownWeeklyExportResult(
        path=target,
        week_iso=week_iso,
        week_start_date=week_start_date,
        week_end_date=week_end_date,
        byte_count=byte_count,
    )


def _resolve_output_path(
    conn: sqlite3.Connection,
    output_path: str | Path | None,
    week_iso: str,
) -> Path:
    if output_path is not None:
        return _anchor_on_project_root(Path(output_path))
    from app.forms import get_setting

    seeded = get_setting(conn, "weekly_report_export_path", default="data/exports")
    # get_setting JSON-decodes value_json, so seeded can be None, str, int,
    # list, etc. Coerce to str before Path() — a hand-edited integer in the
    # settings row would otherwise crash with TypeError("argument should be
    # a str…").
    base = Path(str(seeded)) if seeded else Path("data/exports")
    base = _anchor_on_project_root(base)
    return base / f"weekly_report_{week_iso}.md"


def _resolve_default_week(today: date | None = None) -> str:
    """Return the current calendar week as a `YYYY-Www` string.

    Used by the CLI when no ``--week`` is provided. ISO weeks roll over on
    Monday, so on Sunday the "current" week is still the past one.
    """
    today = today or date.today()
    year, week, _day = today.isocalendar()
    return f"{year:04d}-W{week:02d}"


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m app.exports.markdown_weekly --week 2026-W21 [--output ...]``."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Export the Markdown weekly review for a given ISO week.",
    )
    parser.add_argument(
        "--week",
        default=_resolve_default_week(),
        help="ISO week in YYYY-Www form. Defaults to the current week.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path. Defaults to <weekly_report_export_path>/weekly_report_<week>.md.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=f"Source DB path. Defaults to {DEFAULT_DB_PATH}.",
    )

    args = parser.parse_args(argv)
    try:
        result = export_weekly_report(
            args.week, args.output, db_path=args.db_path,
        )
    except CounterfactualMissingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Invalid week argument: {exc}", file=sys.stderr)
        return 2

    print(
        f"Markdown weekly export · {result.week_iso} → {result.path} "
        f"({result.byte_count:,} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
