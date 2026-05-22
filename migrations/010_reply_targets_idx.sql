-- Phase 5.6 follow-up — /review-2 🟡 #8.
--
-- The `v_daily_reps.candidates_reviewed_today` subquery (added in
-- 009_reply_targets.sql) OR's three date checks against `reply_targets`:
--   discovered_at_utc  ON activity_date
--   last_checked_at_utc ON activity_date
--   expired_at_utc      ON activity_date
--
-- The `Today` view reads `v_daily_reps` on every Streamlit rerun. At
-- ~5,000 candidates × 365 historical daily_activity rows × 3 OR'd
-- predicates, the subquery becomes a full scan per activity_date row
-- per page load. This is bounded at single-user scale today but
-- recompiles on every page load.
--
-- One covering index on `last_checked_at_utc` is enough — `discovered_at_utc`
-- and `expired_at_utc` are sparse / monotonic enough that the planner's
-- existing strategies cover them well.

CREATE INDEX IF NOT EXISTS idx_reply_targets_last_checked
    ON reply_targets (last_checked_at_utc);

-- Partial index for the sparse expired-today predicate. Most rows have
-- expired_at_utc IS NULL, so a partial index is the right shape.
CREATE INDEX IF NOT EXISTS idx_reply_targets_expired_at
    ON reply_targets (expired_at_utc)
    WHERE expired_at_utc IS NOT NULL;
