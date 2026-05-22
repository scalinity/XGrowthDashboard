-- migrations/013_strategic_analysis.sql — Phase 5.10 Strategic Analysis
-- Pack.
--
-- Four concerns, one migration: Brain Dump capture-first interface
-- (§28.22), Coach citation-allowlist surface (§28.23), Account
-- Researcher target-account analysis (§28.24), and Profile Audit
-- periodic comprehensive review (§28.25). All four ship as one
-- transactional unit because they share the §10 `agent_messages.
-- evidence_citations_json` column extension and the §28.23 Coach
-- discipline reads it from boot.
--
-- All statements are idempotent. ADD COLUMN runs unconditionally per
-- the existing migration pattern (apply_migrations records each file
-- in schema_migrations so we never re-run); CREATE TABLE / INDEX use
-- IF NOT EXISTS; settings rows use INSERT OR IGNORE.

-- ---------------------------------------------------------------------------
-- 1. brain_dumps — capture-first interface (§10, §28.22).
-- ---------------------------------------------------------------------------
-- raw_text is IMMUTABLE after insert per §28.22. Enforced by the
-- application layer (Streamlit view + brain_dump.process()); no
-- database-level immutability constraint because SQLite lacks
-- column-level WORM and a row-level trigger here would prevent
-- legitimate processing-result writes to the same row.
--
-- status transitions: unprocessed → processing → processed | failed.
-- Retry rewrites results on the SAME row (no duplicate rows per §28.22).
CREATE TABLE IF NOT EXISTS brain_dumps (
    id                          INTEGER PRIMARY KEY,
    created_at_utc              TEXT NOT NULL DEFAULT (datetime('now')),
    raw_text                    TEXT NOT NULL,
    session_id                  TEXT,
    status                      TEXT NOT NULL DEFAULT 'unprocessed'
        CHECK (status IN ('unprocessed', 'processing', 'processed', 'failed')),
    processed_at_utc            TEXT,
    clarifying_questions_json   TEXT,
    candidate_drafts_json       TEXT,
    model_used                  TEXT,
    tokens_used                 INTEGER
        CHECK (tokens_used IS NULL OR tokens_used >= 0),
    notes                       TEXT
);

CREATE INDEX IF NOT EXISTS idx_brain_dumps_status_created
    ON brain_dumps (status, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_brain_dumps_session_id
    ON brain_dumps (session_id) WHERE session_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. account_research_reports — target-account analysis (§10, §28.24).
-- ---------------------------------------------------------------------------
-- Versioned history per handle: unique(target_handle, created_at_utc)
-- permits multiple reports per handle but blocks accidental duplicate
-- timestamps. linked_reply_target_id is the bidirectional bridge to
-- §29.7 Reply Target Queue (set when "Generate reply target from this
-- research" is clicked).
--
-- target_recent_posts_text is treated as untrusted external content;
-- the prompt builder wraps it in --- BEGIN_UNTRUSTED_DATA ... ---
-- markers per §28.2 before sending to Claude.
CREATE TABLE IF NOT EXISTS account_research_reports (
    id                          INTEGER PRIMARY KEY,
    target_handle               TEXT NOT NULL,
    target_url                  TEXT,
    target_display_name         TEXT,
    target_bio_snapshot         TEXT,
    target_recent_posts_text    TEXT,
    created_at_utc              TEXT NOT NULL DEFAULT (datetime('now')),
    analysis_json               TEXT NOT NULL,
    model_used                  TEXT NOT NULL,
    tokens_used                 INTEGER
        CHECK (tokens_used IS NULL OR tokens_used >= 0),
    session_id                  TEXT,
    linked_reply_target_id      INTEGER
        REFERENCES reply_targets(id) ON DELETE SET NULL,
    notes                       TEXT,
    UNIQUE (target_handle, created_at_utc)
);

CREATE INDEX IF NOT EXISTS idx_account_research_handle_created
    ON account_research_reports (target_handle, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_account_research_linked_target
    ON account_research_reports (linked_reply_target_id)
    WHERE linked_reply_target_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. profile_audits — periodic comprehensive review (§10, §28.25).
-- ---------------------------------------------------------------------------
-- Append-only history. No is_active flag: the "current" audit is
-- implicitly the most recent row. superseded_by_audit_id is a back-
-- reference set when a later audit lands — for joining only; doesn't
-- disable the prior row.
--
-- FKs ON DELETE SET NULL because the audit's snapshot of bio +
-- pinned-post + voice profile is the ground truth even if the
-- referenced rows drift later. pinned_post_text and the niche/voice
-- snapshot columns carry the immutable copy.
CREATE TABLE IF NOT EXISTS profile_audits (
    id                              INTEGER PRIMARY KEY,
    audited_at_utc                  TEXT NOT NULL DEFAULT (datetime('now')),
    bio_snapshot                    TEXT NOT NULL,
    pinned_post_id                  INTEGER
        REFERENCES posts(id) ON DELETE SET NULL,
    pinned_post_text                TEXT,
    recent_posts_window_days        INTEGER NOT NULL DEFAULT 30
        CHECK (recent_posts_window_days > 0),
    recent_post_ids_json            TEXT NOT NULL DEFAULT '[]',
    active_voice_profile_id         INTEGER
        REFERENCES voice_profiles(id) ON DELETE SET NULL,
    niche_problem_snapshot          TEXT,
    niche_person_snapshot           TEXT,
    audit_json                      TEXT NOT NULL,
    model_used                      TEXT NOT NULL,
    tokens_used                     INTEGER
        CHECK (tokens_used IS NULL OR tokens_used >= 0),
    superseded_by_audit_id          INTEGER
        REFERENCES profile_audits(id) ON DELETE SET NULL,
    daniel_notes                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_profile_audits_audited
    ON profile_audits (audited_at_utc DESC);

-- ---------------------------------------------------------------------------
-- 4. agent_messages.evidence_citations_json — Coach citation provenance (§28.23).
-- ---------------------------------------------------------------------------
-- JSON array of surviving citations after §28.23 allowlist validation.
-- NULL on legacy rows and on messages that don't carry citations
-- (system messages, plain user prompts, non-Coach assistant messages).
-- Stripped citations are NOT persisted here — only those that
-- resolved against a real (record_type, record_id). The strip count
-- is logged to agent_tool_calls.notes of the parent tool call so the
-- audit trail of what the agent emitted vs. what survived is complete.
ALTER TABLE agent_messages ADD COLUMN evidence_citations_json TEXT;

-- ---------------------------------------------------------------------------
-- 5. New settings rows (§25 Phase 5.10 migration checklist).
-- ---------------------------------------------------------------------------
-- INSERT OR IGNORE keeps re-application idempotent. Defaults match
-- the §25 checklist verbatim. coach_refuse_without_evidence ships ON
-- by default — the cited-or-refuse behavior is the out-of-box
-- experience per the §28.23 contract.
INSERT OR IGNORE INTO settings (key, value_json, note) VALUES
    ('coach_refuse_without_evidence', 'true',
     'When true (default), Coach messages with zero surviving citations + analytical claims are replaced with a canonical refusal before persistence (§28.23). Disabling lets uncited speculation through.'),
    ('coach_citation_strip_log_threshold', '3',
     'Average citations stripped per message over the last 20 Coach messages; exceeding this surfaces a "strip rate high" banner in Settings (§28.23).'),
    ('brain_dump_max_candidate_drafts', '5',
     'Hard ceiling on candidate drafts returned per Brain Dump processing pass (§28.22). The prompt enforces a soft ≤5 limit; the orchestrator truncates to this value before persistence.'),
    ('profile_audit_recent_posts_window_days', '30',
     'Days of recent posts fed into the §28.25 Profile Audit when Daniel doesn''t override on the form.'),
    ('profile_audit_cadence_reminder_days', '90',
     'After this many days since last audit, §14.7 field 12 surfaces a yellow "time for a fresh audit" banner. Reminder only — audits NEVER auto-run (§28.25).');
