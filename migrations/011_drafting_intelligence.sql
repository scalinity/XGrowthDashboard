-- migrations/011_drafting_intelligence.sql — Phase 5.8 Drafting Intelligence Pack.
--
-- Implements the schema half of §28.11 (pre-publish scorer), §28.12 (generated
-- voice profile), §28.13 (repetition guard), and §28.14 (confidence labels).
-- Five concerns, one migration, because they ship together and the FK fan-out
-- between agent_drafts and the new tables is easier to reason about applied
-- atomically.

-- ---------------------------------------------------------------------------
-- voice_profiles — generated voice fingerprints (§28.12, §10).
-- ---------------------------------------------------------------------------
-- Complements `voice_samples` (hand-picked exemplars). Both feed the system
-- prompt; the profile carries cadence + vocabulary signatures, samples carry
-- tone-by-example. Exactly one row may be active at a time — partial unique
-- index on is_active = 1 enforces this; activation must be a single-tx
-- deactivate-then-insert (see app/agent/voice_profile.py).
CREATE TABLE IF NOT EXISTS voice_profiles (
    id                          INTEGER PRIMARY KEY,
    generated_at_utc            TEXT NOT NULL DEFAULT (datetime('now')),
    is_active                   INTEGER NOT NULL DEFAULT 0
        CHECK (is_active IN (0, 1)),
    source_post_window_days     INTEGER NOT NULL,
    source_post_count           INTEGER NOT NULL,
    profile_json                TEXT NOT NULL,
    model_used                  TEXT NOT NULL,
    tokens_used                 INTEGER NOT NULL DEFAULT 0,
    superseded_by_profile_id    INTEGER REFERENCES voice_profiles(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_voice_profiles_active
    ON voice_profiles (is_active) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_voice_profiles_generated_at
    ON voice_profiles (generated_at_utc DESC);

-- ---------------------------------------------------------------------------
-- post_embeddings — embedding vectors keyed to posts.id (§28.13, §10).
-- ---------------------------------------------------------------------------
-- SQLite has no native vector type; raw float32 little-endian BLOB stored
-- inline. Brute-force in-memory cosine scan is fine for low-thousands volume.
-- source_text_hash lets the guard detect post-edit drift and re-embed.
CREATE TABLE IF NOT EXISTS post_embeddings (
    post_id            INTEGER PRIMARY KEY
        REFERENCES posts(id) ON DELETE CASCADE,
    embedding_blob     BLOB NOT NULL,
    embedding_dim      INTEGER NOT NULL,
    model_name         TEXT NOT NULL,
    model_version      TEXT,
    created_at_utc     TEXT NOT NULL DEFAULT (datetime('now')),
    source_text_hash   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_post_embeddings_model
    ON post_embeddings (model_name, model_version);
CREATE INDEX IF NOT EXISTS idx_post_embeddings_created_at
    ON post_embeddings (created_at_utc);

-- ---------------------------------------------------------------------------
-- prepublish_scores — per-draft heuristic scores (§28.11, §10).
-- ---------------------------------------------------------------------------
-- One row per scored agent_drafts row. Unique(agent_draft_id) — a draft is
-- scored exactly once at save_draft_* time. composite_label is derived from
-- the dimension scores per the spec's "Composite label derivation" table.
CREATE TABLE IF NOT EXISTS prepublish_scores (
    id                       INTEGER PRIMARY KEY,
    agent_draft_id           INTEGER NOT NULL UNIQUE
        REFERENCES agent_drafts(id) ON DELETE CASCADE,
    scored_at_utc            TEXT NOT NULL DEFAULT (datetime('now')),
    clarity_score            INTEGER NOT NULL CHECK (clarity_score BETWEEN 0 AND 3),
    hook_strength_score      INTEGER NOT NULL CHECK (hook_strength_score BETWEEN 0 AND 3),
    specificity_score        INTEGER NOT NULL CHECK (specificity_score BETWEEN 0 AND 3),
    length_fit_score         INTEGER NOT NULL CHECK (length_fit_score BETWEEN 0 AND 3),
    format_fit_score         INTEGER NOT NULL CHECK (format_fit_score BETWEEN 0 AND 3),
    topic_fit_score          INTEGER NOT NULL CHECK (topic_fit_score BETWEEN 0 AND 3),
    reply_substance_score    INTEGER CHECK (reply_substance_score BETWEEN 0 AND 3),
    cta_strength_score       INTEGER CHECK (cta_strength_score BETWEEN 0 AND 3),
    voice_fit_score          INTEGER CHECK (voice_fit_score BETWEEN 0 AND 3),
    composite_label          TEXT NOT NULL
        CHECK (composite_label IN ('weak', 'viable', 'strong')),
    warnings_json            TEXT,
    scorer_version           TEXT NOT NULL,
    tokens_used              INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_prepublish_scores_scored_at
    ON prepublish_scores (scored_at_utc);
CREATE INDEX IF NOT EXISTS idx_prepublish_scores_composite
    ON prepublish_scores (composite_label);

-- ---------------------------------------------------------------------------
-- agent_drafts extensions (§28.11, §28.13, §28.14).
-- ---------------------------------------------------------------------------
-- Three new columns. Existing rows backfill to NULL — no fix-up data write,
-- since pre-Phase-5.8 drafts predate the scorer / guard / labels by design.
-- The scorer FK is intentionally cyclical with prepublish_scores.agent_draft_id;
-- both can be NULL at insert time and are wired up in a single transaction
-- inside _save_draft_*.
ALTER TABLE agent_drafts ADD COLUMN prepublish_score_id INTEGER
    REFERENCES prepublish_scores(id) ON DELETE SET NULL;

ALTER TABLE agent_drafts ADD COLUMN confidence_label TEXT
    CHECK (confidence_label IS NULL
           OR confidence_label IN ('fact', 'inference', 'speculation', 'mixed'));

ALTER TABLE agent_drafts ADD COLUMN similarity_warning_json TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_drafts_prepublish_score
    ON agent_drafts (prepublish_score_id);
CREATE INDEX IF NOT EXISTS idx_agent_drafts_confidence_label
    ON agent_drafts (confidence_label);

-- ---------------------------------------------------------------------------
-- agent_messages extension (§28.14).
-- ---------------------------------------------------------------------------
-- For analytical assistant messages that don't produce a draft.
ALTER TABLE agent_messages ADD COLUMN confidence_label TEXT
    CHECK (confidence_label IS NULL
           OR confidence_label IN ('fact', 'inference', 'speculation', 'mixed'));

CREATE INDEX IF NOT EXISTS idx_agent_messages_confidence_label
    ON agent_messages (confidence_label);

-- ---------------------------------------------------------------------------
-- New settings rows (§25 Phase 5.8 migration checklist).
-- ---------------------------------------------------------------------------
-- INSERT OR IGNORE keeps re-application idempotent and lets a user-edited
-- value survive a re-run.
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('voice_profile_window_days', '90',
     'Default days of posts sampled to build a voice_profile (§28.12)'),
    ('voice_profile_min_source_posts', '10',
     'Minimum source_post_count required to save a profile (§28.12)'),
    ('repetition_guard_lookback_days', '180',
     'Cosine-scan lookback window for the repetition guard (§28.13)'),
    ('repetition_guard_near_duplicate_threshold', '0.92',
     'Cosine >= this → label=near_duplicate (§28.13)'),
    ('repetition_guard_close_echo_threshold', '0.78',
     'Cosine >= this → label=close_echo (§28.13)'),
    ('prepublish_scorer_llm_augmentation_enabled', 'false',
     'When true, layer a small-model warnings_json pass over the deterministic scorer (§28.11)'),
    ('modal_hash_recheck_debounce_ms', '300',
     'Debounce for the confirmation modal''s re-hash on edit (§28.15)'),
    ('modal_edit_settle_seconds', '2',
     'Seconds to disable Publish after each modal-text edit (§28.15)');
