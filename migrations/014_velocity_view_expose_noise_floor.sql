-- migrations/014_velocity_view_expose_noise_floor.sql — P59A-W6.
--
-- The Phase 5.9 v_follower_velocity view (migration 012) suppresses
-- projections correctly but did not surface the inputs the UI needs
-- to render the "trend not yet measurable" state. The Python wrapper
-- (app/agent/velocity.py::get_velocity_projection) had to issue a
-- second SELECT against v_account_daily to derive in_noise_floor for
-- the UI — extra round-trip per Streamlit rerun, plus a risk the
-- view and wrapper drift on the suppression rule.
--
-- This migration drops + recreates the view with two new columns:
--   delta_7d                  (passed through from v_account_daily)
--   in_noise_floor (0|1)      (computed from delta_7d + the noise-floor
--                              setting — single source of truth)
--
-- The four projection columns retain their existing suppression CASE
-- expressions unchanged so any consumer still using the old shape
-- continues to read NULL on the noise-floor path. Adding columns is
-- a backward-compatible change (SELECT * grows, named SELECTs are
-- unaffected).

DROP VIEW IF EXISTS v_follower_velocity;
CREATE VIEW v_follower_velocity AS
WITH latest AS (
    SELECT *
    FROM v_account_daily
    ORDER BY snapshot_date DESC
    LIMIT 1
),
noise_floor AS (
    SELECT COALESCE(
        (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
           FROM settings WHERE key = 'velocity_projection_noise_floor_followers'),
        10
    ) AS threshold
),
milestone AS (
    SELECT COALESCE(
        (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
           FROM settings WHERE key = 'current_milestone'),
        100
    ) AS current_milestone_target
)
SELECT
    l.snapshot_date,
    l.followers_count,
    l.delta_7d,
    l.velocity_7d_per_day,
    l.velocity_30d_per_day,
    m.current_milestone_target,
    (m.current_milestone_target - l.followers_count) AS distance_to_current_milestone,
    -- The 7d delta is the canonical noise anchor for both 7d- AND 30d-
    -- pace projections (the 30d branches below intentionally gate on
    -- |delta_7d| < threshold, NOT |delta_30d|). The argument:
    -- velocity_30d is statistically tighter, but if the 7d signal
    -- itself is in the noise floor we don't trust ANY projection yet,
    -- and showing a 30d-pace date alongside a suppressed 7d-pace date
    -- would create false confidence asymmetry. P59A-W7.
    CASE
        WHEN l.delta_7d IS NULL OR ABS(l.delta_7d) < n.threshold
        THEN 1 ELSE 0
    END                                              AS in_noise_floor,

    CASE
        WHEN l.delta_7d IS NULL
          OR ABS(l.delta_7d) < n.threshold
          OR l.velocity_7d_per_day IS NULL
          OR l.velocity_7d_per_day <= 0
          OR (m.current_milestone_target - l.followers_count) <= 0
        THEN NULL
        ELSE DATE('now', '+' ||
                  CAST(ROUND(
                      (m.current_milestone_target - l.followers_count) * 1.0
                      / l.velocity_7d_per_day
                  ) AS INTEGER) || ' days')
    END                                              AS projected_milestone_hit_date_at_7d_pace,

    CASE
        WHEN l.delta_30d IS NULL
          OR ABS(l.delta_7d) < n.threshold
          OR l.velocity_30d_per_day IS NULL
          OR l.velocity_30d_per_day <= 0
          OR (m.current_milestone_target - l.followers_count) <= 0
        THEN NULL
        ELSE DATE('now', '+' ||
                  CAST(ROUND(
                      (m.current_milestone_target - l.followers_count) * 1.0
                      / l.velocity_30d_per_day
                  ) AS INTEGER) || ' days')
    END                                              AS projected_milestone_hit_date_at_30d_pace,

    CASE
        WHEN l.delta_7d IS NULL
          OR ABS(l.delta_7d) < n.threshold
          OR l.velocity_7d_per_day IS NULL
          OR l.velocity_7d_per_day <= 0
          OR (m.current_milestone_target - l.followers_count) <= 0
        THEN NULL
        ELSE CAST(ROUND(
                (m.current_milestone_target - l.followers_count) * 1.0
                / l.velocity_7d_per_day
             ) AS INTEGER)
    END                                              AS days_until_milestone_at_7d_pace,

    CASE
        WHEN l.delta_30d IS NULL
          OR ABS(l.delta_7d) < n.threshold
          OR l.velocity_30d_per_day IS NULL
          OR l.velocity_30d_per_day <= 0
          OR (m.current_milestone_target - l.followers_count) <= 0
        THEN NULL
        ELSE CAST(ROUND(
                (m.current_milestone_target - l.followers_count) * 1.0
                / l.velocity_30d_per_day
             ) AS INTEGER)
    END                                              AS days_until_milestone_at_30d_pace
FROM latest l
CROSS JOIN noise_floor n
CROSS JOIN milestone m;
