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
    mutationFn: () => apiFetch<Record<string, unknown>>("/agent/find-reply-targets", { method: "POST" }),
    onSuccess: refreshAutomationData,
  });

  const scoreCandidates = useMutation({
    mutationFn: () => apiFetch<Record<string, unknown>>("/agent/score-candidates", { method: "POST" }),
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
        title="Find reply targets from the agent context"
        body="Use the existing agent target-account and discovery tools to propose new reply candidates without hand-logging a thread."
        button="Find targets"
        pending={findReplyTargets.isPending}
        onClick={() => findReplyTargets.mutate()}
        result={
          <>
            {findReplyTargets.isSuccess && <ResultLine>Reply discovery completed.</ResultLine>}
            {findReplyTargets.isError && <ResultLine tone="warn">{String((findReplyTargets.error as Error).message ?? findReplyTargets.error)}</ResultLine>}
          </>
        }
      />

      <ActionCard
        kicker="QUEUE SCORING"
        title="Score candidate replies"
        body="Run the scoring model and thread-classifier lint over pending candidates so Reply Target Queue can rank the next best action."
        button="Score queue"
        pending={scoreCandidates.isPending}
        onClick={() => scoreCandidates.mutate()}
        result={
          <>
            {scoreCandidates.isSuccess && <ResultLine>Candidate scoring completed.</ResultLine>}
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
