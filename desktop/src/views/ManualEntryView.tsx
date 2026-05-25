/**
 * Manual Entry — faithful port of app/pages/8_Manual_Entry.py (spec §15).
 *
 * Tabbed hub for all data-entry forms. Uses existing POST endpoints:
 * /forms/snapshot, /forms/post, /forms/correction, /forms/classify,
 * /forms/daily-activity, /forms/stir-event, /forms/stir-tester.
 * Queue tabs use GET endpoints: /views/needs-tagging, /views/needs-post-id.
 *
 * No useEffect.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { SnapshotForm } from "../components/SnapshotForm";
import { apiFetch } from "../lib/api";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Tab definitions
// ---------------------------------------------------------------------------
const TABS = [
  "Snapshot",
  "Correction",
  "Post / Reply",
  "Classify",
  "Daily reps",
  "Stir event",
  "Tester",
  "Needs tagging",
  "Needs post ID",
] as const;

type TabId = (typeof TABS)[number];

// ---------------------------------------------------------------------------
// Snapshot tab — delegates to the shared SnapshotForm component (RV5-W6 dedup).
// Defaults are fetched from /views/today automatically (RV5-W1 fix).
// ---------------------------------------------------------------------------
function SnapshotTab() {
  return <SnapshotForm />;
}

// ---------------------------------------------------------------------------
// Post / Reply form (mirrors post_log.render)
// ---------------------------------------------------------------------------
function PostReplyTab() {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [postType, setPostType] = useState("post");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ post_id: number }>("/forms/post", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["today"] });
      setText("");
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      text,
      type: postType,
      created_date: new Date().toISOString().slice(0, 10),
    });
  };

  return (
    <div>
      <h3>Log a post or reply</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Paste the text you published. Classification happens in the Classify tab.
      </p>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Type</label>
        <select value={postType} onChange={(e) => setPostType(e.target.value)}>
          <option value="post">standalone</option>
          <option value="reply">reply</option>
          <option value="quote">quote</option>
        </select>
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Text</label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={5}
          style={{ width: "100%", resize: "vertical" }}
          placeholder="Paste the exact text you posted or replied…"
        />
      </div>
      <div className="numeric" style={{ fontSize: "0.82rem", color: palette.boneDim, marginBottom: "0.5rem" }}>
        {text.length} / 280 characters
      </div>
      <button onClick={handleSubmit} disabled={!text.trim() || mutation.isPending} className={text.trim() ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Log post"}
      </button>
      {mutation.isSuccess && <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>Post logged.</p>}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Correction tab
// ---------------------------------------------------------------------------
function CorrectionTab() {
  const [snapshotId, setSnapshotId] = useState("");
  const [field, setField] = useState("");
  const [oldValue, setOldValue] = useState("");
  const [newValue, setNewValue] = useState("");
  const [reason, setReason] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ correction_id: number }>("/forms/correction", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setSnapshotId(""); setField(""); setOldValue(""); setNewValue(""); setReason("");
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      snapshot_id: parseInt(snapshotId, 10),
      field_name: field,
      old_value: oldValue || null,
      new_value: newValue,
      reason,
    });
  };

  const valid = snapshotId && field && newValue && reason;

  return (
    <div>
      <h3>Record a correction</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Snapshots are immutable. Corrections are additive (original preserved).
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Snapshot ID</label>
          <input type="number" value={snapshotId} onChange={(e) => setSnapshotId(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Field name</label>
          <input value={field} onChange={(e) => setField(e.target.value)} style={{ width: "100%" }} placeholder="e.g. followers_count" />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Old value</label>
          <input value={oldValue} onChange={(e) => setOldValue(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>New value</label>
          <input value={newValue} onChange={(e) => setNewValue(e.target.value)} style={{ width: "100%" }} />
        </div>
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Reason</label>
        <input value={reason} onChange={(e) => setReason(e.target.value)} style={{ width: "100%" }} placeholder="Why the correction is needed" />
      </div>
      <button onClick={handleSubmit} disabled={!valid || mutation.isPending} className={valid ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Record correction"}
      </button>
      {mutation.isSuccess && <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>Correction recorded.</p>}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Classify tab — POST /forms/classify
// ---------------------------------------------------------------------------
function ClassifyTab() {
  const [postId, setPostId] = useState("");
  const [pillar, setPillar] = useState("stir");
  const [audience, setAudience] = useState("icp");
  const [cta, setCta] = useState("none");
  const [qualityScore, setQualityScore] = useState("");
  const [lastClassifiedId, setLastClassifiedId] = useState<number | null>(null);

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ post_id: number }>("/forms/classify", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: (_data, variables) => {
      setLastClassifiedId(variables.post_id as number);
      setPostId("");
      setQualityScore("");
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      post_id: parseInt(postId, 10),
      pillar,
      audience,
      cta,
      quality_score: qualityScore ? parseInt(qualityScore, 10) : null,
    });
  };

  const valid = postId && !isNaN(parseInt(postId, 10));

  return (
    <div>
      <h3>Classify a post</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Assign pillar, audience, CTA, and optional quality score to a logged post.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Post ID</label>
          <input
            type="number"
            value={postId}
            onChange={(e) => setPostId(e.target.value)}
            style={{ width: "100%" }}
            placeholder="e.g. 42"
          />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Quality score (1–5)</label>
          <input
            type="number"
            min={1}
            max={5}
            value={qualityScore}
            onChange={(e) => setQualityScore(e.target.value)}
            style={{ width: "100%" }}
            placeholder="Optional"
          />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Pillar</label>
          <select value={pillar} onChange={(e) => setPillar(e.target.value)} style={{ width: "100%" }}>
            <option value="stir">stir</option>
            <option value="build">build</option>
            <option value="self">self</option>
          </select>
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Audience</label>
          <select value={audience} onChange={(e) => setAudience(e.target.value)} style={{ width: "100%" }}>
            <option value="icp">icp</option>
            <option value="other">other</option>
          </select>
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>CTA</label>
          <select value={cta} onChange={(e) => setCta(e.target.value)} style={{ width: "100%" }}>
            <option value="ask">ask</option>
            <option value="none">none</option>
          </select>
        </div>
      </div>
      <button onClick={handleSubmit} disabled={!valid || mutation.isPending} className={valid ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Classify"}
      </button>
      {mutation.isSuccess && (
        <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>
          Classified post #{lastClassifiedId}
        </p>
      )}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Daily reps tab — POST /forms/daily-activity
// ---------------------------------------------------------------------------
function DailyRepsTab() {
  const today = new Date().toISOString().slice(0, 10);
  const [activityDate, setActivityDate] = useState(today);
  const [postsShipped, setPostsShipped] = useState("");
  const [repliesShipped, setRepliesShipped] = useState("");
  const [quotesShipped, setQuotesShipped] = useState("");
  const [replySessionsCompleted, setReplySessionsCompleted] = useState("");
  const [highQualityTargets, setHighQualityTargets] = useState("");
  const [timeSpent, setTimeSpent] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<void>("/forms/daily-activity", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setPostsShipped("");
      setRepliesShipped("");
      setQuotesShipped("");
      setReplySessionsCompleted("");
      setHighQualityTargets("");
      setTimeSpent("");
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      activity_date: activityDate,
      posts_shipped: postsShipped ? parseInt(postsShipped, 10) : 0,
      replies_shipped: repliesShipped ? parseInt(repliesShipped, 10) : 0,
      quotes_shipped: quotesShipped ? parseInt(quotesShipped, 10) : 0,
      reply_sessions_completed: replySessionsCompleted ? parseInt(replySessionsCompleted, 10) : 0,
      high_quality_reply_targets_found: highQualityTargets ? parseInt(highQualityTargets, 10) : 0,
      time_spent_minutes: timeSpent ? parseInt(timeSpent, 10) : 0,
    });
  };

  return (
    <div>
      <h3>Daily reps</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Log today's publishing activity. All numeric fields default to 0 if left empty.
      </p>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Activity date</label>
        <input
          type="date"
          value={activityDate}
          onChange={(e) => setActivityDate(e.target.value)}
          style={{ width: "100%" }}
        />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Posts shipped</label>
          <input type="number" min={0} value={postsShipped} onChange={(e) => setPostsShipped(e.target.value)} style={{ width: "100%" }} placeholder="0" />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Replies shipped</label>
          <input type="number" min={0} value={repliesShipped} onChange={(e) => setRepliesShipped(e.target.value)} style={{ width: "100%" }} placeholder="0" />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Quotes shipped</label>
          <input type="number" min={0} value={quotesShipped} onChange={(e) => setQuotesShipped(e.target.value)} style={{ width: "100%" }} placeholder="0" />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Reply sessions</label>
          <input type="number" min={0} value={replySessionsCompleted} onChange={(e) => setReplySessionsCompleted(e.target.value)} style={{ width: "100%" }} placeholder="0" />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>HQ targets found</label>
          <input type="number" min={0} value={highQualityTargets} onChange={(e) => setHighQualityTargets(e.target.value)} style={{ width: "100%" }} placeholder="0" />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Time spent (min)</label>
          <input type="number" min={0} value={timeSpent} onChange={(e) => setTimeSpent(e.target.value)} style={{ width: "100%" }} placeholder="0" />
        </div>
      </div>
      <button onClick={handleSubmit} disabled={!activityDate || mutation.isPending} className={activityDate ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Save daily activity"}
      </button>
      {mutation.isSuccess && <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>Daily activity saved.</p>}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stir event tab — POST /forms/stir-event
// ---------------------------------------------------------------------------
function StirEventTab() {
  const [eventCategory, setEventCategory] = useState("acquisition");
  const [eventType, setEventType] = useState("");
  const [occurredAt, setOccurredAt] = useState("");
  const [attributionMethod, setAttributionMethod] = useState("unknown");
  const [sourceHandle, setSourceHandle] = useState("");
  const [sourcePostId, setSourcePostId] = useState("");
  const [notes, setNotes] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ event_id: number }>("/forms/stir-event", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setEventType("");
      setOccurredAt("");
      setSourceHandle("");
      setSourcePostId("");
      setNotes("");
    },
  });

  const handleSubmit = () => {
    // Convert local datetime-local to ISO-8601 UTC
    const utcISO = occurredAt ? new Date(occurredAt).toISOString() : new Date().toISOString();
    mutation.mutate({
      event_category: eventCategory,
      event_type: eventType,
      occurred_at_utc: utcISO,
      attribution_method: attributionMethod,
      source_account_handle: sourceHandle || undefined,
      source_post_id: sourcePostId ? parseInt(sourcePostId, 10) : undefined,
      notes: notes || undefined,
    });
  };

  const valid = eventType.trim().length > 0;

  return (
    <div>
      <h3>Log a Stir conversion event</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Record when a reader crosses a funnel boundary (acquisition → activation → usage → feedback).
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Event category</label>
          <select value={eventCategory} onChange={(e) => setEventCategory(e.target.value)} style={{ width: "100%" }}>
            <option value="acquisition">acquisition</option>
            <option value="activation">activation</option>
            <option value="usage">usage</option>
            <option value="feedback">feedback</option>
          </select>
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Attribution method</label>
          <select value={attributionMethod} onChange={(e) => setAttributionMethod(e.target.value)} style={{ width: "100%" }}>
            <option value="self_reported">self_reported</option>
            <option value="utm">utm</option>
            <option value="referrer_header">referrer_header</option>
            <option value="inferred">inferred</option>
            <option value="unknown">unknown</option>
          </select>
        </div>
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Event type</label>
        <input
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
          style={{ width: "100%" }}
          placeholder="e.g. signed_up, first_cook, left_review"
        />
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Occurred at</label>
        <input
          type="datetime-local"
          value={occurredAt}
          onChange={(e) => setOccurredAt(e.target.value)}
          style={{ width: "100%" }}
        />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Source handle</label>
          <input value={sourceHandle} onChange={(e) => setSourceHandle(e.target.value)} style={{ width: "100%" }} placeholder="Optional @handle" />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Source post ID</label>
          <input type="number" value={sourcePostId} onChange={(e) => setSourcePostId(e.target.value)} style={{ width: "100%" }} placeholder="Optional" />
        </div>
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Notes</label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          style={{ width: "100%", resize: "vertical" }}
          placeholder="Optional context"
        />
      </div>
      <button onClick={handleSubmit} disabled={!valid || mutation.isPending} className={valid ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Log event"}
      </button>
      {mutation.isSuccess && (
        <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>
          Event #{(mutation.data as { event_id: number })?.event_id ?? ""} logged.
        </p>
      )}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tester tab — POST /forms/stir-tester
// ---------------------------------------------------------------------------
function TesterTab() {
  const [alias, setAlias] = useState("");
  const [firstSeenDate, setFirstSeenDate] = useState(new Date().toISOString().slice(0, 10));
  const [status, setStatus] = useState("lead");
  const [notes, setNotes] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<void>("/forms/stir-tester", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setAlias("");
      setNotes("");
    },
  });

  const handleSubmit = () => {
    mutation.mutate({
      alias,
      first_seen_date: firstSeenDate,
      status,
      notes: notes || undefined,
    });
  };

  const valid = alias.trim().length > 0 && firstSeenDate.length > 0;

  return (
    <div>
      <h3>Log a Stir tester</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Track people testing Stir through their lifecycle.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Alias</label>
          <input
            value={alias}
            onChange={(e) => setAlias(e.target.value)}
            style={{ width: "100%" }}
            placeholder="Unique alias for this tester"
          />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>First seen date</label>
          <input
            type="date"
            value={firstSeenDate}
            onChange={(e) => setFirstSeenDate(e.target.value)}
            style={{ width: "100%" }}
          />
        </div>
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Status</label>
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: "100%" }}>
          <option value="lead">lead</option>
          <option value="downloaded">downloaded</option>
          <option value="activated">activated</option>
          <option value="cook_mode_used">cook_mode_used</option>
          <option value="churned">churned</option>
          <option value="unknown">unknown</option>
        </select>
      </div>
      <div style={{ marginBottom: "0.5rem" }}>
        <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Notes</label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          style={{ width: "100%", resize: "vertical" }}
          placeholder="Optional context"
        />
      </div>
      <button onClick={handleSubmit} disabled={!valid || mutation.isPending} className={valid ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Log tester"}
      </button>
      {mutation.isSuccess && <p style={{ color: palette.phosphor, marginTop: "0.3rem" }}>Tester logged.</p>}
      {mutation.isError && (
        <p style={{ color: palette.warnAmber, marginTop: "0.3rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Needs tagging tab — GET /views/needs-tagging
// ---------------------------------------------------------------------------
interface TaggingPost {
  id: number;
  text_preview: string;
  created_at: string;
}

function NeedsTaggingTab() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["needs-tagging"],
    queryFn: () => apiFetch<{ posts: TaggingPost[] }>("/views/needs-tagging"),
  });

  const posts = data?.posts ?? [];

  return (
    <div>
      <h3>Posts needing tags</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Posts that haven't been classified yet. Use the Classify tab to tag them.
      </p>
      {isLoading && <p style={{ color: palette.boneDim }}>Loading…</p>}
      {isError && (
        <p style={{ color: palette.warnAmber }}>
          {String((error as Error).message ?? error)}
        </p>
      )}
      {!isLoading && !isError && posts.length === 0 && (
        <Callout>All posts are tagged.</Callout>
      )}
      {posts.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
          {posts.map((post) => (
            <div
              key={post.id}
              style={{
                background: palette.surfaceRaised,
                borderRadius: 6,
                padding: "0.5rem 0.75rem",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span className="numeric" style={{ color: palette.phosphor, minWidth: "3rem" }}>
                #{post.id}
              </span>
              <span style={{ flex: 1, color: palette.bone, marginLeft: "0.5rem", fontSize: "0.85rem" }}>
                {post.text_preview}
              </span>
              <span style={{ color: palette.boneDim, fontSize: "0.75rem", marginLeft: "0.5rem", whiteSpace: "nowrap" }}>
                {post.created_at}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Needs post ID tab — GET /views/needs-post-id
// ---------------------------------------------------------------------------
interface NeedsIdPost {
  id: number;
  text_preview: string;
}

function NeedsPostIdTab() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["needs-post-id"],
    queryFn: () => apiFetch<{ posts: NeedsIdPost[] }>("/views/needs-post-id"),
  });

  const posts = data?.posts ?? [];

  return (
    <div>
      <h3>Posts needing X post ID</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Posts logged locally that still need their X platform post ID attached.
      </p>
      {isLoading && <p style={{ color: palette.boneDim }}>Loading…</p>}
      {isError && (
        <p style={{ color: palette.warnAmber }}>
          {String((error as Error).message ?? error)}
        </p>
      )}
      {!isLoading && !isError && posts.length === 0 && (
        <Callout>All posts have IDs.</Callout>
      )}
      {posts.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
          {posts.map((post) => (
            <div
              key={post.id}
              style={{
                background: palette.surfaceRaised,
                borderRadius: 6,
                padding: "0.5rem 0.75rem",
                display: "flex",
                alignItems: "center",
              }}
            >
              <span className="numeric" style={{ color: palette.phosphor, minWidth: "3rem" }}>
                #{post.id}
              </span>
              <span style={{ flex: 1, color: palette.bone, marginLeft: "0.5rem", fontSize: "0.85rem" }}>
                {post.text_preview}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const ManualEntryView = () => {
  const [activeTab, setActiveTab] = useState<TabId>("Snapshot");

  const tabContent: Record<TabId, React.ReactNode> = {
    Snapshot: <SnapshotTab />,
    Correction: <CorrectionTab />,
    "Post / Reply": <PostReplyTab />,
    Classify: <ClassifyTab />,
    "Daily reps": <DailyRepsTab />,
    "Stir event": <StirEventTab />,
    Tester: <TesterTab />,
    "Needs tagging": <NeedsTaggingTab />,
    "Needs post ID": <NeedsPostIdTab />,
  };

  return (
    <>
      <Kicker>DATA ENTRY HUB</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Manual entry</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.82rem" }}>
        Every form here writes directly to the SQLite store.
      </p>

      {/* Tab bar */}
      <div className="tab-list" style={{ marginBottom: "1rem" }}>
        {TABS.map((tab) => (
          <button
            key={tab}
            className="tab"
            aria-selected={tab === activeTab}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tabContent[activeTab]}

      <Hairline />
    </>
  );
};
