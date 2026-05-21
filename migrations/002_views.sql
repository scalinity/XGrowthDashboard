-- Phase 1 computed views — see spec.md §11.
-- These views use the user-defined aggregate `percentile(value, p)` registered
-- per-connection by app/db.py::connect(). Any connection that queries these
-- views must register it; otherwise SQLite raises "no such function".

-- ---------------------------------------------------------------------------
-- v_post_latest_metrics — latest metric snapshot + latest classification per
-- post (§11).
-- engagements_total falls back to the COALESCEd individual-field sum
-- (engagements_total_approx) per §12 — labeled in UI, not here.
-- Rates are NULL when impressions IS NULL OR impressions = 0 (§13 rule "if
-- impressions is null or zero, rates should display as N/A — not 0").
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_post_latest_metrics;
CREATE VIEW v_post_latest_metrics AS
WITH ranked_snapshots AS (
    SELECT
        pms.*,
        ROW_NUMBER() OVER (PARTITION BY pms.post_id ORDER BY pms.collected_at_utc DESC) AS rn
    FROM post_metric_snapshots pms
),
latest_snapshot AS (
    SELECT * FROM ranked_snapshots WHERE rn = 1
),
ranked_classifications AS (
    SELECT
        pc.*,
        ROW_NUMBER() OVER (PARTITION BY pc.post_id ORDER BY pc.classified_at DESC) AS rn
    FROM post_classifications pc
),
latest_classification AS (
    SELECT * FROM ranked_classifications WHERE rn = 1
)
SELECT
    p.id                                AS post_id,
    p.x_post_id,
    p.created_at_utc,
    p.text,
    p.type,
    lc.pillar,
    lc.audience,
    lc.cta,
    ls.impressions,
    ls.likes,
    ls.replies,
    ls.reposts,
    ls.quotes,
    ls.bookmarks,
    CASE
        WHEN ls.impressions IS NULL OR ls.impressions = 0 THEN NULL
        ELSE COALESCE(
            ls.engagements_total,
            COALESCE(ls.likes, 0) + COALESCE(ls.replies, 0)
            + COALESCE(ls.reposts, 0) + COALESCE(ls.quotes, 0)
            + COALESCE(ls.bookmarks, 0)
        ) * 1.0 / ls.impressions
    END                                 AS engagement_rate,
    CASE
        WHEN ls.impressions IS NULL OR ls.impressions = 0 THEN NULL
        ELSE COALESCE(ls.bookmarks, 0) * 1.0 / ls.impressions
    END                                 AS bookmark_rate,
    CASE
        WHEN ls.impressions IS NULL OR ls.impressions = 0 THEN NULL
        ELSE COALESCE(ls.replies, 0) * 1.0 / ls.impressions
    END                                 AS reply_rate,
    ls.profile_clicks,
    ls.url_link_clicks,
    CASE
        WHEN ls.impressions IS NULL OR ls.impressions = 0 THEN NULL
        ELSE COALESCE(ls.url_link_clicks, 0) * 1.0 / ls.impressions
    END                                 AS link_click_rate,
    ls.collected_at_utc                 AS latest_metrics_collected_at,
    ls.data_quality
FROM posts p
LEFT JOIN latest_snapshot       ls ON ls.post_id = p.id
LEFT JOIN latest_classification lc ON lc.post_id = p.id;

-- ---------------------------------------------------------------------------
-- v_account_daily — canonical daily account state (§11).
-- Picks the earliest snapshot per snapshot_date as the canonical row; the
-- "snapshot closest to configured daily snapshot time" refinement is deferred
-- to Phase 3 once the snapshot-time setting is consumed by the UI. The
-- canonical-row choice here matches the manual-ritual default (snapshot taken
-- at the start of the day).
-- Velocity is computed unconditionally; the noise-floor suppression (§13 rule
-- 6: "display only when |delta_7d| >= 10") is a UI concern, not a DB filter.
-- Corrections: latest correction per (snapshot_id, 'followers_count') is
-- applied to followers_count only; other-field corrections are deferred to
-- Phase 3 (UI surfaces them per-field).
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_account_daily;
CREATE VIEW v_account_daily AS
WITH ranked AS (
    SELECT
        a.*,
        ROW_NUMBER() OVER (PARTITION BY a.snapshot_date ORDER BY a.collected_at_utc ASC) AS rn
    FROM account_snapshots a
),
canonical AS (
    SELECT * FROM ranked WHERE rn = 1
),
followers_correction AS (
    SELECT
        c.id                AS canonical_id,
        c.snapshot_date,
        c.baseline_followers,
        c.following_count,
        c.post_count,
        c.listed_count,
        c.like_count,
        c.media_count,
        c.bio_text,
        COALESCE(
            (SELECT CAST(new_value AS INTEGER)
             FROM account_snapshot_corrections
             WHERE snapshot_id = c.id AND field_name = 'followers_count'
             ORDER BY id DESC LIMIT 1),
            c.followers_count
        ) AS followers_count
    FROM canonical c
),
with_deltas AS (
    SELECT
        snapshot_date,
        followers_count,
        following_count,
        post_count,
        listed_count,
        like_count,
        media_count,
        bio_text,
        baseline_followers,
        followers_count
            - LAG(followers_count, 1) OVER (ORDER BY snapshot_date) AS delta_vs_yesterday,
        followers_count - baseline_followers                         AS delta_vs_baseline,
        followers_count
            - LAG(followers_count, 7) OVER (ORDER BY snapshot_date) AS delta_7d,
        followers_count
            - LAG(followers_count, 30) OVER (ORDER BY snapshot_date) AS delta_30d
    FROM followers_correction
)
SELECT
    snapshot_date,
    followers_count,
    following_count,
    post_count,
    listed_count,
    like_count,
    media_count,
    bio_text,
    delta_vs_yesterday,
    delta_vs_baseline,
    delta_7d,
    delta_30d,
    CASE WHEN delta_7d IS NULL THEN NULL ELSE delta_7d / 7.0 END     AS velocity_7d_per_day,
    CASE WHEN delta_30d IS NULL THEN NULL ELSE delta_30d / 30.0 END  AS velocity_30d_per_day,
    (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
       FROM settings WHERE key = 'current_milestone')
        - followers_count                                            AS distance_to_current_milestone,
    CASE
        WHEN ((SELECT CAST(json_extract(value_json, '$') AS INTEGER)
                 FROM settings WHERE key = 'current_milestone')
              - baseline_followers) > 0
        THEN (followers_count - baseline_followers) * 100.0
             / ((SELECT CAST(json_extract(value_json, '$') AS INTEGER)
                   FROM settings WHERE key = 'current_milestone')
                - baseline_followers)
        ELSE NULL
    END                                                              AS current_milestone_progress_pct,
    (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
       FROM settings WHERE key = 'operational_ceiling')
        - followers_count                                            AS distance_to_operational_ceiling,
    (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
       FROM settings WHERE key = 'long_arc_reminder')
        - followers_count                                            AS distance_to_long_arc
FROM with_deltas;

-- ---------------------------------------------------------------------------
-- v_daily_reps — daily rep adherence (§11).
-- Source-of-truth columns come from daily_activity; *_target_met flags
-- evaluate against the corresponding settings rows (with documented defaults
-- if a setting row is somehow missing).
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_daily_reps;
CREATE VIEW v_daily_reps AS
SELECT
    da.activity_date,
    da.posts_shipped,
    da.replies_shipped,
    da.quotes_shipped,
    da.reply_sessions_completed,
    da.minimum_reps_completed,
    da.planned_posts,
    da.planned_replies,
    CASE
        WHEN da.posts_shipped >= COALESCE(
            (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
               FROM settings WHERE key = 'daily_post_target'),
            1
        ) THEN 1 ELSE 0
    END                                       AS post_target_met,
    CASE
        WHEN da.replies_shipped >= COALESCE(
            (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
               FROM settings WHERE key = 'daily_reply_target'),
            12
        ) THEN 1 ELSE 0
    END                                       AS reply_target_met,
    CASE
        WHEN da.reply_sessions_completed >= COALESCE(
            (SELECT CAST(json_extract(value_json, '$') AS INTEGER)
               FROM settings WHERE key = 'daily_reply_session_target'),
            1
        ) THEN 1 ELSE 0
    END                                       AS session_target_met,
    da.time_spent_minutes
FROM daily_activity da;

-- ---------------------------------------------------------------------------
-- v_funnel_daily — daily X → Stir funnel (§11, §14.5).
-- Per §14.5 App-Store attribution gap rule: downloads are counted from
-- stir_conversion_events with event_type = 'download' regardless of
-- attribution_method (the gap is the absence of inference, not a filter).
-- qualified_icp_testers is restricted to self_reported per §10.2 privacy
-- rule. working_parent_home_cook_testers counts distinct stir_testers
-- self-reporting the flag and downloading on that date.
-- x_impressions_estimate sums impressions across posts referenced by events
-- on that date; label "estimate" is enforced in the UI.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_funnel_daily;
CREATE VIEW v_funnel_daily AS
WITH events_by_date AS (
    SELECT
        event_date,
        SUM(CASE WHEN event_type = 'profile_visit'           THEN 1 ELSE 0 END) AS profile_visits,
        SUM(CASE WHEN event_type = 'link_click'              THEN 1 ELSE 0 END) AS link_clicks,
        SUM(CASE WHEN event_type = 'site_visit'              THEN 1 ELSE 0 END) AS getstir_visits,
        SUM(CASE WHEN event_type = 'download'                THEN 1 ELSE 0 END) AS downloads,
        SUM(CASE WHEN event_type = 'waitlist_signup'         THEN 1 ELSE 0 END) AS waitlist_signups,
        SUM(CASE WHEN event_type = 'kitchen_scan'            THEN 1 ELSE 0 END) AS kitchen_scans,
        SUM(CASE WHEN event_type = 'three_options_generated' THEN 1 ELSE 0 END) AS three_options_generated,
        SUM(CASE WHEN event_type = 'cook_mode_started'       THEN 1 ELSE 0 END) AS cook_mode_started,
        SUM(CASE
              WHEN is_likely_icp = 1 AND attribution_method = 'self_reported'
              THEN 1 ELSE 0 END
        )                                                                       AS qualified_icp_testers
    FROM stir_conversion_events
    GROUP BY event_date
),
impressions_by_date AS (
    SELECT
        sce.event_date,
        SUM(COALESCE(plm.impressions, 0)) AS x_impressions_estimate
    FROM stir_conversion_events sce
    LEFT JOIN v_post_latest_metrics plm ON plm.post_id = sce.referring_post_id
    GROUP BY sce.event_date
),
parents_by_date AS (
    SELECT
        DATE(downloaded_app_at) AS event_date,
        COUNT(*)                AS working_parent_home_cook_testers
    FROM stir_testers
    WHERE is_working_parent_home_cook = 1
      AND downloaded_app_at IS NOT NULL
    GROUP BY DATE(downloaded_app_at)
)
SELECT
    e.event_date,
    COALESCE(i.x_impressions_estimate, 0)            AS x_impressions_estimate,
    e.profile_visits,
    e.link_clicks,
    e.getstir_visits,
    e.downloads,
    e.waitlist_signups,
    e.kitchen_scans,
    e.three_options_generated,
    e.cook_mode_started,
    e.qualified_icp_testers,
    COALESCE(p.working_parent_home_cook_testers, 0)  AS working_parent_home_cook_testers
FROM events_by_date  e
LEFT JOIN impressions_by_date i ON i.event_date = e.event_date
LEFT JOIN parents_by_date     p ON p.event_date = e.event_date;

-- ---------------------------------------------------------------------------
-- v_lane_performance — per-lane medians + IQR + graduated confidence (§11).
-- Uses the user-defined `percentile(value, p)` aggregate registered by
-- app/db.py::connect(). NULL values are ignored by the aggregate.
--
-- Confidence label CASE ordering:
--   The spec text in §11 lists the moderate branch BEFORE the stronger branch
--   in Python form, which makes 'stronger' unreachable (precedence bug). The
--   intent — "insufficient → directional → confident" per the phase prompt
--   and §13 — and the boundary tests at sample sizes 4 / 5 / 14 / 15 / 29 / 30
--   require the stronger branch to fire when post_count >= 30 AND
--   days_covered >= 14. We therefore evaluate the stronger branch first.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_lane_performance;
CREATE VIEW v_lane_performance AS
WITH post_lane AS (
    -- v_post_latest_metrics already exposes the LATEST pillar/audience/cta
    -- per post, so joining post_classifications directly would multiply
    -- rows for posts with revised classifications. Source from plm instead.
    SELECT
        plm.pillar,
        plm.audience,
        plm.cta,
        plm.post_id,
        plm.impressions,
        plm.engagement_rate,
        plm.bookmarks,
        plm.replies,
        p.created_date
    FROM v_post_latest_metrics plm
    JOIN posts p ON p.id = plm.post_id
    WHERE plm.pillar IS NOT NULL
      AND plm.audience IS NOT NULL
      AND plm.cta IS NOT NULL
),
agg AS (
    SELECT
        pillar,
        audience,
        cta,
        COUNT(*)                                       AS post_count,
        COUNT(DISTINCT created_date)                   AS days_covered,
        percentile(impressions, 0.5)                   AS median_impressions,
        percentile(impressions, 0.25)                  AS iqr_impressions_low,
        percentile(impressions, 0.75)                  AS iqr_impressions_high,
        percentile(engagement_rate, 0.5)               AS median_engagement_rate,
        percentile(engagement_rate, 0.25)              AS iqr_engagement_rate_low,
        percentile(engagement_rate, 0.75)              AS iqr_engagement_rate_high,
        SUM(COALESCE(bookmarks, 0))                    AS total_bookmarks,
        SUM(COALESCE(replies, 0))                      AS total_replies
    FROM post_lane
    GROUP BY pillar, audience, cta
)
SELECT
    a.pillar,
    a.audience,
    a.cta,
    a.post_count,
    a.days_covered,
    a.median_impressions,
    a.iqr_impressions_low,
    a.iqr_impressions_high,
    a.median_engagement_rate,
    a.iqr_engagement_rate_low,
    a.iqr_engagement_rate_high,
    a.total_bookmarks,
    a.total_replies,
    (SELECT COUNT(*)
       FROM stir_conversion_events sce
       JOIN post_classifications   pc2 ON pc2.post_id = sce.referring_post_id
      WHERE pc2.pillar   = a.pillar
        AND pc2.audience = a.audience
        AND pc2.cta      = a.cta)                                       AS stir_signal_count,
    CASE
        WHEN a.post_count < 5 OR a.days_covered < 3 THEN 'insufficient sample'
        WHEN a.post_count < 15                      THEN 'low — show scatter, do not rank'
        WHEN a.post_count >= 30 AND a.days_covered >= 14 THEN 'stronger'
        WHEN a.post_count >= 15 AND a.days_covered >= 7  THEN 'moderate'
        ELSE 'moderate'
    END                                                                  AS confidence_label
FROM agg a;
