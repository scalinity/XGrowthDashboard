/**
 * Agent Ops — autonomous replacement for the old Manual Entry tab wall.
 *
 * The route id remains "manual-entry" for compatibility with existing quick
 * links, but the product surface is now agent/API-first: X activity sync,
 * Grok discovery, reply-target work, and automation debt review.
 *
 * No useEffect.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { Callout, Hairline, Kicker } from "../components";
import { apiFetch } from "../lib/api";
import { useNav } from "../lib/nav";
import { palette } from "../theme/tokens";

interface TodaySyncResponse {
  ok: boolean;
  import_posts: {
    posts_inserted: number;
    posts_skipped_existing: number;
    skipped_reason?: string | null;
    error?: string | null;
  };
  metrics: {
    posts_refreshed: number;
    candidates_considered?: number;
    error?: string | null;
  };
  activity: {
    activity_date: string;
    source_counts: {
      posts_shipped: number;
      replies_shipped: number;
      quotes_shipped: number;
      api_actions_count: number;
    };
    daily_activity: {
      posts_shipped: number;
      replies_shipped: number;
      quotes_shipped: number;
      reply_sessions_completed: number;
    };
  };
  warnings: string[];
}

interface GrokSweepResponse {
  ok: boolean;
  severity: "success" | "warning" | "error";
  message: string;
  summary: {
    queries_run: number;
    candidates_discovered: number;
    candidates_verified: number;
    candidates_inserted: number;
    candidates_rejected_404: number;
    error?: string | null;
  };
}

interface NeedsTaggingPost {
  id: number;
  text_preview: string;
  created_at: string;
}

interface NeedsIdPost {
  id: number;
  text_preview: string;
  created_at: string;
}

interface FindReplyTargetsResponse {
  ok: boolean;
  account_count: number;
  accounts: Array<{
    x_handle: string;
    display_name?: string | null;
    lane?: string | null;
  }>;
}

interface ScoreCandidatesResponse {
  ok: boolean;
  considered: number;
  scored_count: number;
  errors: string[];
}

interface ActionCardProps {
  title: string;
  kicker: string;
  body: string;
  button: string;
  pending?: boolean;
  primary?: boolean;
  onClick: () => void;
  result?: ReactNode;
}

function ResultLine({ children, tone = "ok" }: { children: ReactNode; tone?: "ok" | "warn" }) {
  return (
    <div
      className="numeric"
      style={{
        color: tone === "ok" ? palette.phosphor : palette.warnAmber,
        fontSize: "0.82rem",
        marginTop: "0.65rem",
        lineHeight: 1.5,
      }}
    >
      {children}
    </div>
  );
}

function ActionCard({ title, kicker, body, button, pending, primary, onClick, result }: ActionCardProps) {
  return (
    <section
      style={{
        borderTop: `1px solid ${palette.hairline}`,
        padding: "1rem 0",
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) auto",
        gap: "1rem",
        alignItems: "start",
      }}
    >
      <div>
        <Kicker>{kicker}</Kicker>
        <h3 style={{ margin: "0.25rem 0 0.35rem", fontSize: "1.05rem" }}>{title}</h3>
        <p className="faint" style={{ margin: 0, fontSize: "0.84rem", maxWidth: 560 }}>
          {body}
        </p>
        {result}
      </div>
      <button className={primary ? "primary" : undefined} onClick={onClick} disabled={pending}>
        {pending ? "Running..." : button}
      </button>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <div className="numeric" style={{ fontSize: "1.35rem", color: palette.phosphor }}>
        {value}
      </div>
      <div className="kicker" style={{ marginTop: "0.15rem" }}>{label}</div>
    </div>
  );
}

function QueueList({
  title,
  empty,
  loading,
  error,
  items,
}: {
  title: string;
  empty: string;
  loading: boolean;
  error: unknown;
  items: Array<{ id: number; text_preview: string; created_at?: string }>;
}) {
  const errorMessage = error instanceof Error ? error.message : error ? String(error) : null;

  return (
    <section style={{ borderTop: `1px solid ${palette.hairline}`, paddingTop: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Kicker>{title}</Kicker>
        <span className="numeric" style={{ color: palette.boneDim, fontSize: "0.85rem" }}>
          {items.length}
        </span>
      </div>
      {loading && <p className="faint" style={{ fontSize: "0.84rem" }}>Loading...</p>}
      {errorMessage && (
        <p style={{ color: palette.warnAmber, fontSize: "0.84rem" }}>
          {errorMessage}
        </p>
      )}
      {!loading && !errorMessage && items.length === 0 && (
        <p className="faint" style={{ fontSize: "0.84rem" }}>{empty}</p>
      )}
      {items.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.45rem", marginTop: "0.6rem" }}>
          {items.slice(0, 4).map((item) => (
            <div
              key={item.id}
              style={{
                border: `1px solid ${palette.hairline}`,
                borderRadius: 6,
                padding: "0.55rem 0.7rem",
                background: palette.surface,
              }}
            >
              <div className="numeric" style={{ color: palette.phosphor, fontSize: "0.75rem" }}>
                #{item.id}{item.created_at ? ` · ${item.created_at}` : ""}
              </div>
              <div style={{ color: palette.bone, fontSize: "0.84rem", marginTop: "0.2rem", lineHeight: 1.4 }}>
                {item.text_preview}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <ResultLine tone="warn">{message}</ResultLine>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label style={{ display: "grid", gap: "0.3rem", color: palette.boneDim, fontSize: "0.78rem" }}>
      <span className="kicker">{label}</span>
      {children}
    </label>
  );
}

function ManualFallback({ onChanged }: { onChanged: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [postType, setPostType] = useState("post");
  const [postText, setPostText] = useState("");
  const [postXId, setPostXId] = useState("");
  const [postUrl, setPostUrl] = useState("");
  const [classifyPostId, setClassifyPostId] = useState("");
  const [pillar, setPillar] = useState("stir");
  const [audience, setAudience] = useState("icp");
  const [cta, setCta] = useState("none");
  const [allowOverwrite, setAllowOverwrite] = useState(false);
  const [idPostId, setIdPostId] = useState("");
  const [idXPostId, setIdXPostId] = useState("");
  const [idUrl, setIdUrl] = useState("");
  const [dailyDate, setDailyDate] = useState(today);
  const [dailyPosts, setDailyPosts] = useState("0");
  const [dailyReplies, setDailyReplies] = useState("0");
  const [dailyQuotes, setDailyQuotes] = useState("0");
  const [snapshotId, setSnapshotId] = useState("");
  const [correctionField, setCorrectionField] = useState("followers_count");
  const [correctionValue, setCorrectionValue] = useState("");
  const [correctionReason, setCorrectionReason] = useState("");
  const [testerAlias, setTesterAlias] = useState("");
  const [testerFirstSeen, setTesterFirstSeen] = useState(today);
  const [testerStatus, setTesterStatus] = useState("lead");

  const inputStyle = {
    width: "100%",
    boxSizing: "border-box" as const,
    minHeight: 34,
    border: `1px solid ${palette.hairline}`,
    borderRadius: 6,
    background: palette.ink,
    color: palette.bone,
    padding: "0.45rem 0.55rem",
  };

  const logPost = useMutation({
    mutationFn: () => apiFetch<{ post_id: number }>("/forms/post", {
      method: "POST",
      body: JSON.stringify({
        type: postType,
        text: postText,
        x_post_id: postXId,
        manual_url: postUrl,
      }),
    }),
    onSuccess: onChanged,
  });

  const classifyPost = useMutation({
    mutationFn: () => apiFetch<{ classification_id: number }>("/forms/classify", {
      method: "POST",
      body: JSON.stringify({
        post_id: Number(classifyPostId),
        pillar,
        audience,
        cta,
        allow_overwrite: allowOverwrite,
      }),
    }),
    onSuccess: onChanged,
  });

  const confirmPostId = useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>("/forms/post-id", {
      method: "PUT",
      body: JSON.stringify({ post_id: Number(idPostId), x_post_id: idXPostId, manual_url: idUrl }),
    }),
    onSuccess: onChanged,
  });

  const saveDaily = useMutation({
    mutationFn: () => apiFetch<{ result: string }>("/forms/daily-activity", {
      method: "POST",
      body: JSON.stringify({
        activity_date: dailyDate,
        posts_shipped: Number(dailyPosts),
        replies_shipped: Number(dailyReplies),
        quotes_shipped: Number(dailyQuotes),
        reply_sessions_completed: Number(dailyReplies) > 0 ? 1 : 0,
        high_quality_reply_targets_found: 0,
      }),
    }),
    onSuccess: onChanged,
  });

  const saveCorrection = useMutation({
    mutationFn: () => apiFetch<{ correction_id: number }>("/forms/correction", {
      method: "POST",
      body: JSON.stringify({
        snapshot_id: Number(snapshotId),
        field_name: correctionField,
        new_value: correctionValue,
        reason: correctionReason,
      }),
    }),
    onSuccess: onChanged,
  });

  const saveTester = useMutation({
    mutationFn: () => apiFetch<{ tester_id: number }>("/forms/stir-tester", {
      method: "POST",
      body: JSON.stringify({ alias: testerAlias, first_seen_date: testerFirstSeen, status: testerStatus }),
    }),
    onSuccess: onChanged,
  });

  return (
    <details style={{ borderTop: `1px solid ${palette.hairline}`, paddingTop: "1rem" }}>
      <summary style={{ cursor: "pointer", color: palette.bone, fontSize: "1rem" }}>
        Manual fallback
      </summary>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
          gap: "1rem",
          marginTop: "1rem",
        }}
      >
        <section style={{ border: `1px solid ${palette.hairline}`, borderRadius: 6, padding: "0.8rem" }}>
          <Kicker>POST LOG</Kicker>
          <div style={{ display: "grid", gap: "0.55rem", marginTop: "0.65rem" }}>
            <Field label="type">
              <select value={postType} onChange={(event) => setPostType(event.target.value)} style={inputStyle}>
                <option value="post">post</option>
                <option value="reply">reply</option>
                <option value="quote">quote</option>
              </select>
            </Field>
            <Field label="text">
              <textarea value={postText} onChange={(event) => setPostText(event.target.value)} rows={3} style={inputStyle} />
            </Field>
            <Field label="x post id">
              <input value={postXId} onChange={(event) => setPostXId(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="url">
              <input value={postUrl} onChange={(event) => setPostUrl(event.target.value)} style={inputStyle} />
            </Field>
            <button onClick={() => logPost.mutate()} disabled={logPost.isPending}>Log post</button>
            {logPost.isSuccess && <ResultLine>Post #{logPost.data.post_id} logged.</ResultLine>}
            <ErrorText error={logPost.error} />
          </div>
        </section>

        <section style={{ border: `1px solid ${palette.hairline}`, borderRadius: 6, padding: "0.8rem" }}>
          <Kicker>CLASSIFY</Kicker>
          <div style={{ display: "grid", gap: "0.55rem", marginTop: "0.65rem" }}>
            <Field label="post id">
              <input value={classifyPostId} onChange={(event) => setClassifyPostId(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="pillar">
              <select value={pillar} onChange={(event) => setPillar(event.target.value)} style={inputStyle}>
                <option value="stir">stir</option>
                <option value="build">build</option>
                <option value="self">self</option>
              </select>
            </Field>
            <Field label="audience">
              <select value={audience} onChange={(event) => setAudience(event.target.value)} style={inputStyle}>
                <option value="icp">icp</option>
                <option value="other">other</option>
              </select>
            </Field>
            <Field label="cta">
              <select value={cta} onChange={(event) => setCta(event.target.value)} style={inputStyle}>
                <option value="none">none</option>
                <option value="ask">ask</option>
              </select>
            </Field>
            <label style={{ color: palette.boneDim, fontSize: "0.82rem" }}>
              <input type="checkbox" checked={allowOverwrite} onChange={(event) => setAllowOverwrite(event.target.checked)} /> overwrite
            </label>
            <button onClick={() => classifyPost.mutate()} disabled={classifyPost.isPending}>Save classification</button>
            {classifyPost.isSuccess && <ResultLine>Classification #{classifyPost.data.classification_id} saved.</ResultLine>}
            <ErrorText error={classifyPost.error} />
          </div>
        </section>

        <section style={{ border: `1px solid ${palette.hairline}`, borderRadius: 6, padding: "0.8rem" }}>
          <Kicker>QUEUE FIX</Kicker>
          <div style={{ display: "grid", gap: "0.55rem", marginTop: "0.65rem" }}>
            <Field label="post id">
              <input value={idPostId} onChange={(event) => setIdPostId(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="x post id">
              <input value={idXPostId} onChange={(event) => setIdXPostId(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="url">
              <input value={idUrl} onChange={(event) => setIdUrl(event.target.value)} style={inputStyle} />
            </Field>
            <button onClick={() => confirmPostId.mutate()} disabled={confirmPostId.isPending}>Confirm X ID</button>
            {confirmPostId.isSuccess && <ResultLine>X ID saved.</ResultLine>}
            <ErrorText error={confirmPostId.error} />
          </div>
        </section>

        <section style={{ border: `1px solid ${palette.hairline}`, borderRadius: 6, padding: "0.8rem" }}>
          <Kicker>DAILY REPS</Kicker>
          <div style={{ display: "grid", gap: "0.55rem", marginTop: "0.65rem" }}>
            <Field label="date">
              <input value={dailyDate} onChange={(event) => setDailyDate(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="posts">
              <input value={dailyPosts} onChange={(event) => setDailyPosts(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="replies">
              <input value={dailyReplies} onChange={(event) => setDailyReplies(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="quotes">
              <input value={dailyQuotes} onChange={(event) => setDailyQuotes(event.target.value)} style={inputStyle} />
            </Field>
            <button onClick={() => saveDaily.mutate()} disabled={saveDaily.isPending}>Save reps</button>
            {saveDaily.isSuccess && <ResultLine>Daily reps saved.</ResultLine>}
            <ErrorText error={saveDaily.error} />
          </div>
        </section>

        <section style={{ border: `1px solid ${palette.hairline}`, borderRadius: 6, padding: "0.8rem" }}>
          <Kicker>CORRECTION</Kicker>
          <div style={{ display: "grid", gap: "0.55rem", marginTop: "0.65rem" }}>
            <Field label="snapshot id">
              <input value={snapshotId} onChange={(event) => setSnapshotId(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="field">
              <select value={correctionField} onChange={(event) => setCorrectionField(event.target.value)} style={inputStyle}>
                <option value="followers_count">followers_count</option>
                <option value="following_count">following_count</option>
                <option value="post_count">post_count</option>
                <option value="listed_count">listed_count</option>
                <option value="bio_text">bio_text</option>
              </select>
            </Field>
            <Field label="new value">
              <input value={correctionValue} onChange={(event) => setCorrectionValue(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="reason">
              <textarea value={correctionReason} onChange={(event) => setCorrectionReason(event.target.value)} rows={2} style={inputStyle} />
            </Field>
            <button onClick={() => saveCorrection.mutate()} disabled={saveCorrection.isPending}>Save correction</button>
            {saveCorrection.isSuccess && <ResultLine>Correction #{saveCorrection.data.correction_id} saved.</ResultLine>}
            <ErrorText error={saveCorrection.error} />
          </div>
        </section>

        <section style={{ border: `1px solid ${palette.hairline}`, borderRadius: 6, padding: "0.8rem" }}>
          <Kicker>TESTER</Kicker>
          <div style={{ display: "grid", gap: "0.55rem", marginTop: "0.65rem" }}>
            <Field label="alias">
              <input value={testerAlias} onChange={(event) => setTesterAlias(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="first seen">
              <input value={testerFirstSeen} onChange={(event) => setTesterFirstSeen(event.target.value)} style={inputStyle} />
            </Field>
            <Field label="status">
              <select value={testerStatus} onChange={(event) => setTesterStatus(event.target.value)} style={inputStyle}>
                <option value="lead">lead</option>
                <option value="downloaded">downloaded</option>
                <option value="activated">activated</option>
                <option value="cook_mode_used">cook_mode_used</option>
                <option value="churned">churned</option>
                <option value="unknown">unknown</option>
              </select>
            </Field>
            <button onClick={() => saveTester.mutate()} disabled={saveTester.isPending}>Save tester</button>
            {saveTester.isSuccess && <ResultLine>Tester #{saveTester.data.tester_id} saved.</ResultLine>}
            <ErrorText error={saveTester.error} />
          </div>
        </section>
      </div>
    </details>
  );
}

export const ManualEntryView = () => {
  const nav = useNav();
  const queryClient = useQueryClient();

  const needsTagging = useQuery({
    queryKey: ["needs-tagging"],
    queryFn: () => apiFetch<{ posts: NeedsTaggingPost[] }>("/views/needs-tagging"),
  });

  const needsPostId = useQuery({
    queryKey: ["needs-post-id"],
    queryFn: () => apiFetch<{ posts: NeedsIdPost[] }>("/views/needs-post-id"),
  });

  const refreshAutomationData = () => {
    queryClient.invalidateQueries({ queryKey: ["today"] });
    queryClient.invalidateQueries({ queryKey: ["needs-tagging"] });
    queryClient.invalidateQueries({ queryKey: ["needs-post-id"] });
    queryClient.invalidateQueries({ queryKey: ["reply-queue"] });
  };

  const syncToday = useMutation({
    mutationFn: () => apiFetch<TodaySyncResponse>("/api/sync-today", { method: "POST" }),
    onSuccess: refreshAutomationData,
  });

  const grokSweep = useMutation({
    mutationFn: () => apiFetch<GrokSweepResponse>("/agent/grok-sweep", { method: "POST" }),
    onSuccess: refreshAutomationData,
  });

  const classifyPosts = useMutation({
    mutationFn: () => apiFetch<{ classified_count: number; considered: number; errors: unknown[] }>("/agent/classify-posts", { method: "POST" }),
    onSuccess: refreshAutomationData,
  });

  const findReplyTargets = useMutation({
    mutationFn: () => apiFetch<FindReplyTargetsResponse>("/agent/find-reply-targets", { method: "POST" }),
    onSuccess: refreshAutomationData,
  });

  const scoreCandidates = useMutation({
    mutationFn: () => apiFetch<ScoreCandidatesResponse>("/agent/score-candidates", { method: "POST" }),
    onSuccess: refreshAutomationData,
  });

  const taggingItems = needsTagging.data?.posts ?? [];
  const idItems = needsPostId.data?.posts ?? [];
  const queueDebt = taggingItems.length + idItems.length;

  return (
    <>
      <Kicker>AGENT OPERATIONS</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Agent Ops</h1>
      <p className="dim" style={{ maxWidth: 700, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.86rem" }}>
        The old manual journal is now the control room for API collection, Grok discovery, and agent work. Manual write paths still exist as fallback primitives, but this screen starts with autonomous collection.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: "1rem",
          margin: "1rem 0 1.25rem",
        }}
      >
        <Stat label="automation debt" value={queueDebt} />
        <Stat label="needs tags" value={taggingItems.length} />
        <Stat label="needs X id" value={idItems.length} />
      </div>

      <Callout>
        Run the collectors first, then let the agent find and score reply work. Publishing still stays behind the explicit per-post confirmation flow.
      </Callout>

      <ActionCard
        kicker="X API"
        title="Sync today's owned activity"
        body="Fetch the account snapshot, import recent owned posts and replies, refresh metrics, and reconcile daily reps from the imported rows."
        button="Sync X activity"
        primary
        pending={syncToday.isPending}
        onClick={() => syncToday.mutate()}
        result={
          <>
            {syncToday.isSuccess && (
              <ResultLine tone={syncToday.data.warnings.length ? "warn" : "ok"}>
                +{syncToday.data.import_posts.posts_inserted} imported · {syncToday.data.metrics.posts_refreshed} metrics · reps {syncToday.data.activity.daily_activity.posts_shipped}/{syncToday.data.activity.daily_activity.replies_shipped}/{syncToday.data.activity.daily_activity.quotes_shipped}
                {syncToday.data.warnings.length > 0 && ` · ${syncToday.data.warnings.join(" · ")}`}
              </ResultLine>
            )}
            {syncToday.isError && <ResultLine tone="warn">{String((syncToday.error as Error).message ?? syncToday.error)}</ResultLine>}
          </>
        }
      />

      <ActionCard
        kicker="GROK + X VERIFY"
        title="Run semantic reply discovery"
        body="Ask Grok for configured firehose queries, verify every candidate against the X API, and insert only verified reply targets into the queue."
        button="Run Grok sweep"
        pending={grokSweep.isPending}
        onClick={() => grokSweep.mutate()}
        result={
          <>
            {grokSweep.isSuccess && (
              <ResultLine tone={grokSweep.data.severity === "success" ? "ok" : "warn"}>
                {grokSweep.data.message}
              </ResultLine>
            )}
            {grokSweep.isError && <ResultLine tone="warn">{String((grokSweep.error as Error).message ?? grokSweep.error)}</ResultLine>}
          </>
        }
      />

      <ActionCard
        kicker="AUTO CLASSIFY"
        title="Classify imported posts"
        body="Resolve untagged post debt with the v1 taxonomy so Content Performance and Next Rep can learn from API-imported activity."
        button="Classify queue"
        pending={classifyPosts.isPending}
        onClick={() => classifyPosts.mutate()}
        result={
          <>
            {classifyPosts.isSuccess && (
              <ResultLine tone={classifyPosts.data.errors.length ? "warn" : "ok"}>
                {classifyPosts.data.classified_count}/{classifyPosts.data.considered} posts classified
              </ResultLine>
            )}
            {classifyPosts.isError && <ResultLine tone="warn">{String((classifyPosts.error as Error).message ?? classifyPosts.error)}</ResultLine>}
          </>
        }
      />

      <ActionCard
        kicker="AGENT"
        title="Load target accounts from agent context"
        body="Read active target-account rows that guide reply discovery. Candidate creation happens through Grok discovery or the reply-target form."
        button="Load targets"
        pending={findReplyTargets.isPending}
        onClick={() => findReplyTargets.mutate()}
        result={
          <>
            {findReplyTargets.isSuccess && (
              <ResultLine>
                {findReplyTargets.data.account_count} target accounts loaded
                {findReplyTargets.data.accounts.length > 0 && ` · ${findReplyTargets.data.accounts.slice(0, 3).map((account) => account.x_handle).join(" · ")}`}
              </ResultLine>
            )}
            {findReplyTargets.isError && <ResultLine tone="warn">{String((findReplyTargets.error as Error).message ?? findReplyTargets.error)}</ResultLine>}
          </>
        }
      />

      <ActionCard
        kicker="QUEUE SCORING"
        title="Score candidate replies"
        body="Recompute scores for every pending reply target so Reply Target Queue can rank the next best action."
        button="Score queue"
        pending={scoreCandidates.isPending}
        onClick={() => scoreCandidates.mutate()}
        result={
          <>
            {scoreCandidates.isSuccess && (
              <ResultLine tone={scoreCandidates.data.errors.length ? "warn" : "ok"}>
                {scoreCandidates.data.scored_count}/{scoreCandidates.data.considered} candidates scored
                {scoreCandidates.data.errors.length > 0 && ` · ${scoreCandidates.data.errors.join(" · ")}`}
              </ResultLine>
            )}
            {scoreCandidates.isError && <ResultLine tone="warn">{String((scoreCandidates.error as Error).message ?? scoreCandidates.error)}</ResultLine>}
          </>
        }
      />

      <Hairline />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: "1.25rem",
        }}
      >
        <QueueList
          title="Needs classification"
          empty="No imported posts are waiting for tags."
          loading={needsTagging.isLoading}
          error={needsTagging.error}
          items={taggingItems}
        />
        <QueueList
          title="Needs X post ID"
          empty="No local rows are missing X IDs."
          loading={needsPostId.isLoading}
          error={needsPostId.error}
          items={idItems}
        />
      </div>

      <ManualFallback onChanged={refreshAutomationData} />

      <Hairline />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "0.5rem" }}>
        <button className="primary" onClick={() => nav("agent-chat")}>Open Agent Chat</button>
        <button onClick={() => nav("reply-queue")}>Reply Queue</button>
        <button onClick={() => nav("brain-dump")}>Brain Dump</button>
        <button onClick={() => nav("account-researcher")}>Account Researcher</button>
        <button onClick={() => nav("settings")}>API Settings</button>
      </div>
    </>
  );
};
