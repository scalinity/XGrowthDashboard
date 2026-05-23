"""Background jobs scoped to single-user / local-only operation.

Two flavors live here:

* Boot-time idempotent jobs (Phase 5.6+) — ``reply_target_maintenance``.
  Called on every Streamlit boot; safe to re-enter. Not scheduled.

* Phase 7 scheduled jobs — ``post_metrics_refresh`` and
  ``reply_target_metrics_refresh``. Driven by launchd plists (see
  ``docs/SCHEDULED_JOBS.md``). Each writes a ``scheduled_job`` audit-log
  row at run-end with success/failure, rows touched, rate-limit hits,
  and runtime duration.

The other two Phase 7 scheduled jobs live under ``scripts/`` because
they're naturally one-shot CLI invocations:
``scripts/collect_account_snapshot.py`` and
``scripts/import_recent_posts.py``.
"""
