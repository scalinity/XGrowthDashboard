-- migrations/007_post_classifications_unique.sql
--
-- Phase 5.5 /review-2 W12: enforce one post_classifications row per post.
-- The original 001_initial.sql schema had no UNIQUE constraint on
-- post_id; repeated save_draft_post calls (or future classify-untagged
-- retries) could insert N rows for the same post, breaking the join
-- semantics in v_post_latest_metrics and v_lane_performance.
--
-- We add a UNIQUE index instead of trying to ALTER the table — SQLite's
-- ALTER TABLE doesn't support adding UNIQUE constraints on existing
-- columns, but a CREATE UNIQUE INDEX achieves the same correctness
-- guarantee at the storage layer (insert/update fails on conflict).
--
-- Before creating the index we dedupe any existing duplicates by keeping
-- the most recently-classified row per post_id. The DELETE keeps the
-- highest id (most recent insertion order). This is idempotent: a fresh
-- DB has zero rows; an upgraded DB collapses duplicates exactly once.

DELETE FROM post_classifications
WHERE id NOT IN (
    SELECT MAX(id) FROM post_classifications GROUP BY post_id
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_post_class_post
    ON post_classifications (post_id);
