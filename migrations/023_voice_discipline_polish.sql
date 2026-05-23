-- Phase 10 — Voice Discipline Polish Pack (migration 023).
--
-- Five additive features that tighten the Growth Agent's voice fidelity
-- and intent discipline. This migration carries the two schema bumps:
--
--   1. prepublish_scores.screenshot_test_score INTEGER NULL
--      (§28.11 10th dimension — 0..3, NULL when not scored).
--      Composite_label derivation tolerates NULL per the existing pattern.
--   2. agent_drafts.reply_quality_lint_failure_mode TEXT NULL
--      (§28.18 failure-mode label — populated only when
--      reply_quality_lint_passed = 0). Eleven-value enum extending the
--      original three (forced / ai_tasting / selfishly_self_promoting).
--
-- Plus two new settings rows seeded via INSERT OR IGNORE (mirrors the
-- scripts/seed_settings.py rows that ship for fresh init_db installs):
--
--   * screenshot_test_minimum_for_strong (default 2)
--     — composite_label cannot be 'strong' if screenshot_test_score is
--       non-NULL and below this floor (§28.11 gating).
--   * reply_intent_required (default 1)
--     — boolean toggle: when 1, dispatch_tool_call refuses save_draft_reply
--       without a valid §29.5 reply_intent; when 0, NULL passes through
--       (escape hatch during calibration).
--
-- All changes are pure additive — no data rewrite required. ALTER TABLE
-- ADD COLUMN with NULLable + defaultless columns is the canonical
-- forward-compatible recipe in SQLite; existing rows stay NULL.

-- ---------------------------------------------------------------------------
-- 1. prepublish_scores.screenshot_test_score (§28.11 10th dimension).
-- ---------------------------------------------------------------------------
ALTER TABLE prepublish_scores ADD COLUMN screenshot_test_score INTEGER
    CHECK (screenshot_test_score IS NULL
           OR (screenshot_test_score BETWEEN 0 AND 3));

-- ---------------------------------------------------------------------------
-- 2. agent_drafts.reply_quality_lint_failure_mode (§28.18 expanded enum).
-- ---------------------------------------------------------------------------
-- Eleven-value enum: the original three from Phase 5.9
-- (forced / ai_tasting / selfishly_self_promoting) plus the eight new
-- failure modes from Daniel's voice anchor (engagement_bait, ragebait,
-- manipulative_question, fake_authority, performative_threading,
-- diving_preamble, emoji_as_personality, hedging_that_erases).
--
-- NOTE: this 'ragebait' value is the DRAFT-side failure mode (Daniel's
-- reply text reads as ragebait). It is distinct from the THREAD-side
-- 'ragebait' on reply_targets.lint_category (§29.10 thread-classifier
-- lint), which categorizes the target post's thread quality before any
-- draft exists. Same label, different scopes — see app/agent/lint.py
-- module-level disambiguation comment for the full picture.
ALTER TABLE agent_drafts ADD COLUMN reply_quality_lint_failure_mode TEXT
    CHECK (reply_quality_lint_failure_mode IS NULL
           OR reply_quality_lint_failure_mode IN (
                'forced',
                'ai_tasting',
                'selfishly_self_promoting',
                'engagement_bait',
                'ragebait',
                'manipulative_question',
                'fake_authority',
                'performative_threading',
                'diving_preamble',
                'emoji_as_personality',
                'hedging_that_erases'
           ));

-- ---------------------------------------------------------------------------
-- 3. Seed the two new settings rows (idempotent — same INSERT OR IGNORE
--    discipline as every other settings-bearing migration).
--    Mirrors the scripts/seed_settings.py rows so a fresh DB initialized
--    via init_db agrees with one initialized via raw migrations only.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('screenshot_test_minimum_for_strong',
     '2',
     'composite_label cannot be ''strong'' if screenshot_test_score is non-NULL and below this floor (§28.11 Phase 10 gating). NULL passes through (calibration period or model unavailable). Default 2 matches the §28.11 ladder: a screenshot-worthy draft scores 2 or 3.'),
    ('reply_intent_required',
     'true',
     'When true (default), dispatch_tool_call refuses save_draft_reply without a valid §29.5 reply_intent. When false, NULL passes through (escape hatch during calibration when forced enforcement creates friction). Phase 10 Section 6 promotion.');

-- ---------------------------------------------------------------------------
-- 4. Audit-log tail row — matches the §28.30 write-through pattern from
--    every migration since Phase 5.11.
-- ---------------------------------------------------------------------------
INSERT INTO audit_logs
    (event_category, event_type, target_type, target_id, details_json, success)
VALUES
    ('migration', 'migration_applied_023', 'migration', '023',
     '{"migration":"023_voice_discipline_polish",'                          ||
     '"columns_added":'                                                     ||
     '{"prepublish_scores":["screenshot_test_score"],'                      ||
     ' "agent_drafts":["reply_quality_lint_failure_mode"]},'                ||
     '"settings_rows_added":["screenshot_test_minimum_for_strong",'         ||
     ' "reply_intent_required"],'                                           ||
     '"reply_quality_failure_modes_extended_from":3,'                       ||
     '"reply_quality_failure_modes_extended_to":11}',
     1);
