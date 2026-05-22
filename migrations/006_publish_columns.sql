-- migrations/006_publish_columns.sql — Phase 5.5 publish surface (§28.10).
--
-- ALTERs `posts` with publish-flow columns and creates the
-- `publish_confirmation_tokens` table. The token table is unreachable from
-- the agent's tool registry by construction — no agent-callable tool reads
-- it. Validation/consumption is performed inside app/agent/confirmation.py
-- and the publish-flow click-handler only.

-- ---------------------------------------------------------------------------
-- posts ALTERs — publish surface (§28.4 #10, §28.10, §10.2 posts additions).
-- SQLite ALTER ADD COLUMN is non-transactional and one column per statement.
-- ---------------------------------------------------------------------------
ALTER TABLE posts ADD COLUMN agent_draft_id INTEGER
    REFERENCES agent_drafts(id) ON DELETE SET NULL;

ALTER TABLE posts ADD COLUMN published_via_agent_message_id INTEGER
    REFERENCES agent_messages(id) ON DELETE SET NULL;

-- Intent timestamp — set when the click-handler mints a token. Distinct
-- from `created_at_utc` (post creation) and from the resulting `x_post_id`
-- which only lands after the X API call succeeds (or after crash-recovery
-- reconciliation).
ALTER TABLE posts ADD COLUMN published_to_x_at TEXT;

-- publish_method: 'manual_clipboard' = MVP path (Daniel pastes the live
-- URL via Mark posted form). 'agent_confirmed' = V1.2 direct-write path.
-- 'failed' = atomic transaction aborted with last_error populated.
-- 'unknown' = crash recovery flagged this row for manual reconciliation.
ALTER TABLE posts ADD COLUMN publish_method TEXT
    CHECK (publish_method IS NULL OR publish_method IN
        ('manual_clipboard', 'agent_confirmed', 'failed', 'unknown'));

ALTER TABLE posts ADD COLUMN publish_last_error TEXT;

ALTER TABLE posts ADD COLUMN publish_attempt_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_posts_agent_draft
    ON posts (agent_draft_id);
CREATE INDEX IF NOT EXISTS idx_posts_publish_method
    ON posts (publish_method);
-- Crash-recovery query: orphan posts have publish_attempt_count > 0 and
-- published_to_x_at IS NOT NULL but no x_post_id yet.
CREATE INDEX IF NOT EXISTS idx_posts_orphan_recovery
    ON posts (publish_attempt_count, published_to_x_at) WHERE x_post_id IS NULL;

-- ---------------------------------------------------------------------------
-- publish_confirmation_tokens — single-use UUIDs gating publish (§28.10).
-- Raw tokens NEVER persisted; only SHA-256 hashes. The agent's tool registry
-- contains no tool that reads this table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS publish_confirmation_tokens (
    id                          INTEGER PRIMARY KEY,
    token_hash                  TEXT NOT NULL UNIQUE,
    post_id                     INTEGER NOT NULL
        REFERENCES posts(id) ON DELETE CASCADE,
    draft_text_hash_at_issue    TEXT NOT NULL,
    created_at_utc              TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at_utc              TEXT NOT NULL,
    consumed_at_utc             TEXT,
    consumed_by_x_post_id       TEXT
);

CREATE INDEX IF NOT EXISTS idx_pubtokens_post_created
    ON publish_confirmation_tokens (post_id, created_at_utc);
-- Partial index for the cleanup/expiry sweep.
CREATE INDEX IF NOT EXISTS idx_pubtokens_unconsumed_expiry
    ON publish_confirmation_tokens (expires_at_utc)
    WHERE consumed_at_utc IS NULL;
