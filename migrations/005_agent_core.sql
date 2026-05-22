-- migrations/005_agent_core.sql — Phase 5.5 Growth Agent core tables (§10.2).
--
-- Creates the six agent-domain tables. Publish-surface columns on `posts`
-- and the `publish_confirmation_tokens` table land in 006_publish_columns.sql
-- so the two responsibilities migrate independently and one can be rolled
-- back without touching the other.

-- ---------------------------------------------------------------------------
-- agent_conversations — persistent chat sessions (§14.8, §28).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_conversations (
    id                   INTEGER PRIMARY KEY,
    started_at_utc       TEXT NOT NULL DEFAULT (datetime('now')),
    last_message_at_utc  TEXT,
    title                TEXT,
    context_seed         TEXT,
    status               TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    message_count        INTEGER NOT NULL DEFAULT 0,
    total_input_tokens   INTEGER NOT NULL DEFAULT 0,
    total_output_tokens  INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd   REAL NOT NULL DEFAULT 0.0,
    model_default        TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_conversations_status
    ON agent_conversations (status, last_message_at_utc DESC);

-- ---------------------------------------------------------------------------
-- agent_messages — append-only message history.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_messages (
    id                              INTEGER PRIMARY KEY,
    conversation_id                 INTEGER NOT NULL
        REFERENCES agent_conversations(id) ON DELETE CASCADE,
    role                            TEXT NOT NULL
        CHECK (role IN ('user', 'assistant', 'system', 'tool_result')),
    content                         TEXT NOT NULL,
    tool_calls_json                 TEXT,
    tool_call_id                    TEXT,
    model                           TEXT,
    input_tokens                    INTEGER,
    output_tokens                   INTEGER,
    rate_snapshot_json              TEXT,
    -- FK populated when this assistant message led to a successful publish.
    -- ON DELETE SET NULL — deleting a post must NOT cascade-delete chat
    -- history. The orphan message stays in the audit trail; the link breaks.
    resulted_in_published_post_id   INTEGER
        REFERENCES posts(id) ON DELETE SET NULL,
    created_at_utc                  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_conv_created
    ON agent_messages (conversation_id, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_agent_messages_pub_post
    ON agent_messages (resulted_in_published_post_id);

-- ---------------------------------------------------------------------------
-- agent_tool_calls — audit log of every tool invocation (§28.2 rule #11).
-- The dispatcher MUST redact raw confirmation_token from arguments_json
-- BEFORE inserting any publish-tool row. redacted_arguments = 1 marks rows
-- that underwent redaction.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id                  INTEGER PRIMARY KEY,
    message_id          INTEGER NOT NULL
        REFERENCES agent_messages(id) ON DELETE CASCADE,
    tool_name           TEXT NOT NULL,
    arguments_json      TEXT NOT NULL,
    redacted_arguments  INTEGER NOT NULL DEFAULT 0
        CHECK (redacted_arguments IN (0, 1)),
    result_json         TEXT,
    status              TEXT NOT NULL
        CHECK (status IN ('success', 'error', 'partial')),
    error_message       TEXT,
    duration_ms         INTEGER,
    cost_input_tokens   INTEGER,
    cost_output_tokens  INTEGER,
    cost_usd            REAL,
    notes               TEXT,
    created_at_utc      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_message
    ON agent_tool_calls (message_id);
CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_name
    ON agent_tool_calls (tool_name, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_redacted
    ON agent_tool_calls (redacted_arguments);

-- ---------------------------------------------------------------------------
-- agent_target_accounts — curated accounts for find_reply_targets (§28.4 #8).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_target_accounts (
    id              INTEGER PRIMARY KEY,
    x_handle        TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    notes           TEXT,
    lane            TEXT,
    priority        INTEGER NOT NULL DEFAULT 5,
    last_engaged_at TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_target_accounts_active
    ON agent_target_accounts (lane, priority) WHERE is_active = 1;

-- ---------------------------------------------------------------------------
-- voice_samples — Daniel's voice exemplars for system-prompt injection.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS voice_samples (
    id                INTEGER PRIMARY KEY,
    post_id           INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    text              TEXT NOT NULL,
    context_note      TEXT,
    pillar            TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    priority          INTEGER NOT NULL DEFAULT 5,
    added_at_utc      TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at_utc  TEXT
);

CREATE INDEX IF NOT EXISTS idx_voice_samples_active
    ON voice_samples (priority) WHERE is_active = 1;

-- ---------------------------------------------------------------------------
-- agent_drafts — agent-generated drafts before they become posts.
-- The orchestrator-tracked iwh_attempt_index lives here (§28.2 rule #13).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_drafts (
    id                  INTEGER PRIMARY KEY,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    session_id          TEXT,
    conversation_id     INTEGER REFERENCES agent_conversations(id) ON DELETE SET NULL,
    draft_kind          TEXT NOT NULL
        CHECK (draft_kind IN ('standalone', 'reply', 'quote', 'thread_root')),
    text                TEXT NOT NULL,
    pillar              TEXT,
    audience            TEXT,
    cta                 TEXT,
    hypothesis_id       INTEGER REFERENCES experiments(id) ON DELETE SET NULL,
    target_post_url     TEXT,
    target_post_text    TEXT,
    agent_reasoning     TEXT,
    -- voice_self_score is JSON: {"intelligence": 0-3, "wisdom": 0-3, "humility": 0-3}.
    -- NOT the enforcement counter — that's iwh_attempt_index below.
    voice_self_score    TEXT,
    -- iwh_attempt_index lives outside agent context. Increments on every
    -- save_draft_* call when any IWH score < minimum OR dark-pattern lint
    -- returns yes. On iwh_max_revision_attempts + 1 the orchestrator
    -- refuses the save and emits a refusal back to the conversation.
    iwh_attempt_index   INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'accepted_as_is', 'accepted_with_edits',
                          'rejected', 'superseded')),
    final_post_id       INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    user_feedback       TEXT,
    revision_of         INTEGER REFERENCES agent_drafts(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_drafts_session_created
    ON agent_drafts (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_drafts_status
    ON agent_drafts (status);
CREATE INDEX IF NOT EXISTS idx_agent_drafts_final_post
    ON agent_drafts (final_post_id);
CREATE INDEX IF NOT EXISTS idx_agent_drafts_conversation
    ON agent_drafts (conversation_id, created_at);
