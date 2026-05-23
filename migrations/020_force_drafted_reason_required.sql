-- RV2-7 (🟡): defense-in-depth — DB-level enforcement that
-- reply_targets.force_drafted=1 requires a non-empty force_drafted_reason.
--
-- Pre-fix, the UI (app/pages/10_Reply_Target_Queue.py) rejected empty
-- reasons but the schema had only `force_drafted INTEGER NOT NULL
-- DEFAULT 0` + `force_drafted_reason TEXT` (nullable, no CHECK). Any
-- future write path (an out-of-band script, a future code path the UI
-- doesn't own) could mint a row with force_drafted=1 + reason=NULL,
-- silently breaking the §29.10 audit promise.
--
-- Implementation: SQLite trigger pattern instead of the 12-step
-- ALTER-TABLE rebuild. Two triggers (BEFORE INSERT, BEFORE UPDATE)
-- raise an error when force_drafted=1 + reason is NULL/empty/whitespace.
-- A trigger is functionally equivalent to a CHECK constraint for this
-- invariant and avoids the disruption of rebuilding the ~50-column
-- reply_targets table just to add one constraint. Idempotent via
-- CREATE TRIGGER IF NOT EXISTS.

-- The trim() helper in SQLite's single-arg form removes only space
-- characters by default. To catch tabs and newlines we replace them
-- with spaces first, then trim. The double-replace handles tabs (char(9))
-- and newlines (char(10)); carriage returns (char(13)) are also folded.
CREATE TRIGGER IF NOT EXISTS trg_reply_targets_force_drafted_requires_reason_insert
BEFORE INSERT ON reply_targets
FOR EACH ROW
WHEN NEW.force_drafted = 1
 AND (NEW.force_drafted_reason IS NULL
      OR length(trim(replace(replace(replace(
              NEW.force_drafted_reason,
              char(9), ' '), char(10), ' '), char(13), ' '))) = 0)
BEGIN
    SELECT RAISE(ABORT,
        'reply_targets.force_drafted=1 requires non-empty force_drafted_reason (RV2-7)');
END;

CREATE TRIGGER IF NOT EXISTS trg_reply_targets_force_drafted_requires_reason_update
BEFORE UPDATE OF force_drafted, force_drafted_reason ON reply_targets
FOR EACH ROW
WHEN NEW.force_drafted = 1
 AND (NEW.force_drafted_reason IS NULL
      OR length(trim(replace(replace(replace(
              NEW.force_drafted_reason,
              char(9), ' '), char(10), ' '), char(13), ' '))) = 0)
BEGIN
    SELECT RAISE(ABORT,
        'reply_targets.force_drafted=1 requires non-empty force_drafted_reason (RV2-7)');
END;

INSERT INTO audit_logs
    (event_category, event_type, target_type, target_id, details_json, success)
VALUES
    ('migration', 'migration_applied_020', 'migration', '020',
     '{"migration":"020_force_drafted_reason_required",' ||
     '"triggers_added":["trg_reply_targets_force_drafted_requires_reason_insert",' ||
     '"trg_reply_targets_force_drafted_requires_reason_update"]}', 1);
