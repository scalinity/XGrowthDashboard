-- migrations/019_x_api_writes.sql
-- Phase 8 — X API writes (spec §25 Phase 8; §28.10 Phase 5.5 → Phase 8
-- transition; §29.1 Phase 8 block; §29.6 Phase 8 settings rows).
--
-- This migration is intentionally tiny. Phase 5.5 already shipped the
-- schema surface for the publish flow (``publish_confirmation_tokens``,
-- ``posts.published_to_x_at`` / ``posts.publish_method`` / etc.); Phase
-- 7 (migration 018) shipped the xurl read wrapper and OAuth state.
-- Phase 8 only adds:
--
--   1. ``publish_via_api_enabled`` (boolean, default TRUE) — the
--      per-publish gate that branches §28.10's atomic-transaction
--      wrapper between the new ``POST /2/tweets`` path and the
--      original manual-clipboard path.
--
--   2. ``x_write_rate_limit_per_15min`` (integer, default 50) and
--      ``x_write_rate_limit_per_24h`` (integer, default 1000) — the
--      sliding-window write quotas honored by
--      ``app.x_client.check_write_rate_capacity()``. Defaults match
--      §25 Phase 8 verbatim; Daniel can tune live in Settings as his
--      X API tier changes (no code change needed).
--
-- Final step: a ``migration_applied_019`` row in ``audit_logs`` so the
-- application's own application history is traceable.
--
-- All statements are idempotent. ``INSERT OR IGNORE`` for the settings
-- rows means re-running this migration manually (outside
-- ``apply_migrations``) won't double-insert; the audit row uses a
-- guarded ``NOT EXISTS`` insert for the same reason.
--
-- The §28.10 contract (six-check chain, atomic transaction, token-
-- consumption discipline, raw-token redaction, IWH self-score check,
-- dark-pattern lint preflight, payload-hash discipline) is UNCHANGED
-- by this migration. Phase 8 only adds the API-vs-manual branch
-- inside the existing wrapper; the wrapper itself is sacrosanct.

-- ---------------------------------------------------------------------------
-- 1. settings — Phase 8 rows per §29.6.
-- ---------------------------------------------------------------------------
-- All three new keys are pure INSERT OR IGNORE — they didn't exist
-- pre-Phase-8. Defaults match §29.6 + §25 Phase 8 verbatim.
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('publish_via_api_enabled', 'true',
     'Phase 8 gate (§28.10 transition; §29.6). TRUE: publish flow takes the ' ||
     'real POST /2/tweets branch. FALSE: publish flow takes the manual-clipboard ' ||
     'fallback branch (always available, Settings-selectable, never deprecated).'),
    ('x_write_rate_limit_per_15min', '50',
     'Sliding-window cap on X API writes per 15 minutes. Honored by ' ||
     'app.x_client.check_write_rate_capacity() before each publish call. ' ||
     'Default matches §25 Phase 8; tune live as X API tier allows.'),
    ('x_write_rate_limit_per_24h', '1000',
     'Sliding-window cap on X API writes per 24 hours. Honored by ' ||
     'app.x_client.check_write_rate_capacity() before each publish call. ' ||
     'Default matches §25 Phase 8; tune live as X API tier allows.');

-- ---------------------------------------------------------------------------
-- 2. Final audit row — migration history is itself a state-change (§28.30).
-- ---------------------------------------------------------------------------
-- Guarded by NOT EXISTS so a manual re-run won't double-log.
INSERT INTO audit_logs
    (event_category, event_type, target_type, target_id, details_json, success)
SELECT
    'migration', 'migration_applied_019', 'migration', '019',
    '{"migration":"019_x_api_writes",' ||
    '"settings_seeded":["publish_via_api_enabled",' ||
    '"x_write_rate_limit_per_15min",' ||
    '"x_write_rate_limit_per_24h"]}',
    1
WHERE NOT EXISTS (
    SELECT 1 FROM audit_logs
    WHERE event_category = 'migration'
      AND event_type     = 'migration_applied_019'
);
