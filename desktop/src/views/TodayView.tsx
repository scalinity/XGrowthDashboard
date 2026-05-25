/**
 * Today / Weigh-In — faithful port of app/pages/1_Today.py (spec §14.1).
 *
 * Layout mirrors the Streamlit page top-to-bottom:
 *   1. Pinned snapshot form (collapses once today's snapshot exists)
 *   2. Weigh-in metric tiles (followers + deltas + milestone progress)
 *   3. Content-type recommendation callout
 *   4. Daily reps progress + §29.9 reply-target mix
 *   5. Pending agent drafts (today, with prepublish + repetition chips)
 *   6. Recent activity (today's posts)
 *   7. Quick actions + agent integration buttons
 *
 * Data: single useQuery to GET /views/today (all reads are server-side per §31.10).
 * Writes: useMutation to POST /forms/snapshot.
 * No useEffect — per the project React rules.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  Callout,
  Hairline,
  Kicker,
  MetricRow,
  MetricTile,
  PrepublishChip,
  ProgressBar,
  RepetitionBanner,
} from "../components";
import { apiFetch } from "../lib/api";
import { useNav } from "../lib/nav";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types (mirrors the expanded /views/today response shape)
// ---------------------------------------------------------------------------
interface Snapshot {
  followers_count: number;
  following_count: number;
  post_count: number;
  listed_count: number;
  delta_vs_yesterday: number | null;
  delta_vs_baseline: number | null;
  delta_7d: number | null;
  velocity_7d_per_day: number | null;
  distance_to_current_milestone: number | null;
}

interface Milestone {
  name: string;
  start_value: number | null;
  target_value: number | null;
}

interface RepsRow {
  posts_shipped: number;
  replies_shipped: number;
  reply_sessions_completed: number;
  minimum_reps_completed: number;
  post_target_met: boolean;
  reply_target_met: boolean;
  session_target_met: boolean;
  [k: string]: unknown;
}

interface RepsTargets {
  post_target: number;
  reply_target: number;
  session_target: number;
  high_engagement_mix_pct: number;
  candidate_review_daily_target: number;
}

interface RepsMix {
  high_eng: number;
  icp_intent: number;
  candidates_rev: number;
  high_eng_target: number;
  high_eng_met: boolean;
  cand_target: number;
  cand_met: boolean;
}

interface PendingDraft {
  id: number;
  text_preview: string;
  draft_kind: string;
  composite_label: string | null;
  similarity_warning_json: string | null;
}

interface RecentPost {
  id: number;
  type: string;
  text_preview: string;
  pillar: string | null;
  audience: string | null;
  cta: string | null;
  confirm_status: string;
}

interface TodayData {
  today_iso: string;
  snapshot: Snapshot | null;
  baseline_followers: number;
  current_milestone_target: number;
  milestone: Milestone | null;
  milestone_progress_pct: number | null;
  velocity_measurable: boolean;
  velocity_7d_per_day: number | null;
  content_type_reco: { under_represented: string | null; rationale: string };
  daily_reps: {
    row: RepsRow | null;
    targets: RepsTargets;
    mix: RepsMix | Record<string, never>;
  };
  pending_drafts: PendingDraft[];
  recent_posts: RecentPost[];
  snapshot_defaults: {
    username: string;
    profile_url: string;
    baseline_followers: number;
    x_user_id: string | null;
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatDelta(value: number | null, noiseFloor = 2): { big: string; caption: string } {
  if (value === null || value === undefined) return { big: "—", caption: "" };
  if (Math.abs(value) <= noiseFloor) return { big: `${value > 0 ? "+" : ""}${value}`, caption: `within ±${noiseFloor}/day` };
  return { big: `${value > 0 ? "+" : ""}${value}`, caption: "" };
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  const weekday = d.toLocaleDateString("en-US", { weekday: "long" }).toUpperCase();
  const month = d.toLocaleDateString("en-US", { month: "long" }).toUpperCase();
  const day = d.getDate();
  const year = d.getFullYear();
  return `${weekday} · ${month} ${day}, ${year}`;
}

const CONFIRM_COLORS: Record<string, string> = {
  confirmed: palette.confidenceConfidentBg,
  needs_id: palette.confidenceDirectionalBg,
  needs_metrics: palette.confidenceDirectionalBg,
  draft: palette.confidenceInsufficientBg,
};

// ---------------------------------------------------------------------------
// Snapshot form (shared; reused by Manual Entry)
// ---------------------------------------------------------------------------
function SnapshotForm({
  defaults,
  onSuccess,
}: {
  defaults: TodayData["snapshot_defaults"];
  onSuccess: () => void;
}) {
  const [followers, setFollowers] = useState("");
  const [following, setFollowing] = useState("");
  const [posts, setPosts] = useState("");
  const [listed, setListed] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ snapshot_id: number }>("/forms/snapshot", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess,
  });

  const handleSubmit = () => {
    mutation.mutate({
      snapshot_date: new Date().toISOString().slice(0, 10),
      username: defaults.username,
      profile_url: defaults.profile_url,
      baseline_followers: defaults.baseline_followers,
      x_user_id: defaults.x_user_id,
      followers_count: parseInt(followers, 10),
      following_count: parseInt(following, 10),
      post_count: parseInt(posts, 10),
      listed_count: parseInt(listed, 10) || 0,
    });
  };

  const valid =
    followers !== "" &&
    following !== "" &&
    posts !== "" &&
    !isNaN(parseInt(followers)) &&
    !isNaN(parseInt(following)) &&
    !isNaN(parseInt(posts));

  return (
    <div style={{ margin: "0.6rem 0 1rem" }}>
      <h3>Pinned daily snapshot</h3>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Spec §15.1 — designed to take 30 seconds. Sets source='manual', data_quality='manual'.
        Corrections never overwrite (§13 hard rule 2).
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: "0.5rem", marginBottom: "0.6rem" }}>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Followers</label>
          <input type="number" value={followers} onChange={(e) => setFollowers(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Following</label>
          <input type="number" value={following} onChange={(e) => setFollowing(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Posts</label>
          <input type="number" value={posts} onChange={(e) => setPosts(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div>
          <label className="kicker" style={{ display: "block", marginBottom: "0.2rem" }}>Listed</label>
          <input type="number" value={listed} onChange={(e) => setListed(e.target.value)} style={{ width: "100%" }} />
        </div>
      </div>
      <button onClick={handleSubmit} disabled={!valid || mutation.isPending} className={valid ? "primary" : undefined}>
        {mutation.isPending ? "Saving…" : "Save daily snapshot"}
      </button>
      {mutation.isError && (
        <div style={{ color: palette.warnAmber, fontSize: "0.85rem", marginTop: "0.4rem" }}>
          {String((mutation.error as Error).message ?? mutation.error)}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------
export const TodayView = () => {
  const nav = useNav();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["today"],
    queryFn: () => apiFetch<TodayData>("/views/today"),
    retry: 1,
  });

  if (isLoading) return <p className="dim">Reading the local service…</p>;
  if (error) {
    return (
      <Callout>
        Couldn't reach the local service. <em>{String((error as Error).message ?? error)}</em>
      </Callout>
    );
  }
  if (!data) return null;

  const d = data;
  const snap = d.snapshot;
  const target = d.current_milestone_target;

  return (
    <>
      {/* Header */}
      <Kicker>{formatDate(d.today_iso)} · WEIGH-IN</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Today</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.82rem" }}>
        Daily operating cockpit per §14.1. Numbers, not narratives.
        Trend judgements live in <strong>Progress</strong>; this view is the morning ritual.
      </p>

      {/* 1. Snapshot form (pinned until today's snapshot exists) */}
      {!snap ? (
        <>
          <Callout>
            <em>Pin today's snapshot first.</em> The rest of the dashboard reads from the canonical
            daily row; without it everything else below shows yesterday's last-known state.
          </Callout>
          <SnapshotForm
            defaults={d.snapshot_defaults}
            onSuccess={() => queryClient.invalidateQueries({ queryKey: ["today"] })}
          />
          <p className="faint">
            Once saved, this form collapses. Use the Manual Entry tab to record additional snapshots or corrections.
          </p>
        </>
      ) : (
        <details style={{ margin: "0.5rem 0" }}>
          <summary style={{ cursor: "pointer", color: palette.boneDim, fontSize: "0.88rem" }}>
            Today's snapshot is logged — view / edit
          </summary>
          <div className="numeric" style={{ fontSize: "0.88rem", padding: "0.4rem 0", color: palette.bone }}>
            followers={snap.followers_count.toLocaleString()} · following={snap.following_count.toLocaleString()} ·
            posts={snap.post_count.toLocaleString()} · listed={snap.listed_count.toLocaleString()}
          </div>
          <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
            Snapshots are immutable per §13 hard rule 2. Use <strong>Manual entry → Correction</strong> to record
            a fix; the original row is preserved.
          </p>
        </details>
      )}

      <Hairline />

      {/* 2. Weigh-in cards */}
      <h2>Weigh-in</h2>
      {!snap ? (
        <p className="faint">Cards appear once today's snapshot is logged.</p>
      ) : (
        <>
          <MetricRow>
            <MetricTile label="Followers" value={snap.followers_count.toLocaleString()} />
            <MetricTile
              label="Δ yesterday"
              value={formatDelta(snap.delta_vs_yesterday).big}
              deltaCaption={formatDelta(snap.delta_vs_yesterday).caption}
            />
            <MetricTile label="Δ baseline" value={`${(snap.delta_vs_baseline ?? 0) >= 0 ? "+" : ""}${snap.delta_vs_baseline ?? 0}`} />
            <MetricTile
              label={`To ${target}`}
              value={snap.distance_to_current_milestone != null ? snap.distance_to_current_milestone.toLocaleString() : "—"}
            />
          </MetricRow>

          {/* Milestone progress */}
          {d.milestone && d.milestone_progress_pct != null && (
            <>
              <Kicker>Distribution milestone · {d.milestone.name}</Kicker>
              <ProgressBar value={d.milestone_progress_pct} label={`${(d.milestone_progress_pct * 100).toFixed(1)}%`} />
            </>
          )}

          {/* Velocity */}
          {!d.velocity_measurable ? (
            <Callout>
              <em>7-day velocity not yet measurable.</em> Per §13 rule 6, velocity displays only when |Δ7d| ≥ 10.
              Judge the week, not the morning.
            </Callout>
          ) : (
            <Callout>
              <em>7-day velocity:</em>{" "}
              <span className="numeric">
                {d.velocity_7d_per_day != null ? `${d.velocity_7d_per_day > 0 ? "+" : ""}${d.velocity_7d_per_day.toFixed(1)}` : "—"}{" "}
                followers/day
              </span>{" "}
              over the last week.
            </Callout>
          )}
        </>
      )}

      <Hairline />

      {/* Content-type recommendation (§28.17) */}
      <Callout>
        <em>Today's content-type recommendation:</em>{" "}
        {d.content_type_reco.under_represented && (
          <span className="numeric">{d.content_type_reco.under_represented}</span>
        )}
        {d.content_type_reco.under_represented && " · "}
        <span className="faint">{d.content_type_reco.rationale}</span>
      </Callout>

      <Hairline />

      {/* 3. Daily reps */}
      <h2>Daily reps</h2>
      {!d.daily_reps.row ? (
        <p className="faint">
          No <code>daily_activity</code> row for today yet. Log it from{" "}
          <strong>Manual entry → Daily reps</strong> to track adherence.
        </p>
      ) : (
        <>
          <MetricRow>
            <MetricTile
              label="Posts"
              value={`${d.daily_reps.row.posts_shipped} / ${d.daily_reps.targets.post_target}`}
              delta={d.daily_reps.row.post_target_met ? "✓ target met" : undefined}
            />
            <MetricTile
              label="Replies"
              value={`${d.daily_reps.row.replies_shipped} / ${d.daily_reps.targets.reply_target}`}
              delta={d.daily_reps.row.reply_target_met ? "✓ target met" : undefined}
            />
            <MetricTile
              label="Sessions"
              value={`${d.daily_reps.row.reply_sessions_completed} / ${d.daily_reps.targets.session_target}`}
              delta={d.daily_reps.row.session_target_met ? "✓ target met" : undefined}
            />
            <MetricTile
              label="Minimum reps"
              value={d.daily_reps.row.minimum_reps_completed ? "Complete" : "Incomplete"}
            />
          </MetricRow>

          {/* §29.9 reply-target mix block */}
          {"high_eng" in d.daily_reps.mix && (
            <div
              style={{
                marginTop: "-0.4rem",
                marginBottom: "0.5rem",
                padding: "0.5rem 0.85rem",
                background: palette.surface,
                borderLeft: `2px solid ${palette.hairline}`,
                borderRadius: "2px",
              }}
            >
              <Kicker>§29.9 · REPLY-TARGET MIX</Kicker>
              <div
                className="numeric"
                style={{
                  fontSize: "0.85rem",
                  color: palette.bone,
                  lineHeight: 1.55,
                  marginTop: "0.25rem",
                }}
              >
                · <strong>{(d.daily_reps.mix as RepsMix).high_eng}</strong> high-engagement{" "}
                <span className="faint">(engagement_surface_score ≥ 2)</span>{" "}
                <span className="faint">
                  · target {Math.round((d.daily_reps.targets.high_engagement_mix_pct) * 100)}% of shipped →{" "}
                  {(d.daily_reps.mix as RepsMix).high_eng_target}
                </span>
                {(d.daily_reps.mix as RepsMix).high_eng_met ? " ✓" : ""}
                <br />
                · <strong>{(d.daily_reps.mix as RepsMix).icp_intent}</strong> icp_discovery
                <br />
                · <strong>{(d.daily_reps.mix as RepsMix).candidates_rev}</strong> candidates reviewed{" "}
                <span className="faint">· target {(d.daily_reps.mix as RepsMix).cand_target}</span>
                {(d.daily_reps.mix as RepsMix).cand_met ? " ✓" : ""}
              </div>
            </div>
          )}

          {!d.daily_reps.row.minimum_reps_completed && (
            <Callout>
              <em>Minimum reps not yet complete.</em> Logging behavior is the only signal you control today;
              impressions and follower movement are downstream.
            </Callout>
          )}
        </>
      )}

      <Hairline />

      {/* 4. Pending agent drafts */}
      {d.pending_drafts.length > 0 && (
        <>
          <h2>Pending agent drafts</h2>
          <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
            Agent-generated drafts from today's sessions that haven't been accepted, rejected,
            or shipped yet. The chip is the §28.11 pre-publish read — informational, never gates Publish.
          </p>
          {d.pending_drafts.map((draft) => (
            <div key={draft.id}>
              <div style={{ padding: "0.5rem 0", borderBottom: `1px solid ${palette.hairline}` }}>
                <div className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
                  draft #{draft.id} · {draft.draft_kind}
                </div>
                <div style={{ marginTop: "0.25rem", color: palette.bone }}>{draft.text_preview}</div>
              </div>
              <RepetitionBanner warningJson={draft.similarity_warning_json} />
              <PrepublishChip label={draft.composite_label} />
            </div>
          ))}
          <Hairline />
        </>
      )}

      {/* 5. Recent activity */}
      <h2>Recent activity</h2>
      {d.recent_posts.length === 0 ? (
        <p className="faint">
          No posts logged today yet. Use <strong>Manual entry → Post / Reply</strong> to log one.
        </p>
      ) : (
        d.recent_posts.map((post) => {
          const lane = post.pillar
            ? `${post.pillar} · ${post.audience} · ${post.cta}`
            : "(unclassified)";
          const confirmColor = CONFIRM_COLORS[post.confirm_status] ?? palette.surfaceRaised;
          return (
            <div key={post.id} style={{ padding: "0.6rem 0", borderBottom: `1px solid ${palette.hairline}` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
                  {post.type} · {lane}
                </span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "0.7rem",
                    letterSpacing: "0.06em",
                    textTransform: "uppercase",
                    background: confirmColor,
                    color: palette.ink,
                    padding: "1px 6px",
                    borderRadius: "2px",
                  }}
                >
                  {post.confirm_status}
                </span>
              </div>
              <div style={{ marginTop: "0.25rem", color: palette.bone }}>{post.text_preview}</div>
            </div>
          );
        })
      )}

      <Hairline />

      {/* 6. Quick actions */}
      <h2>Quick actions</h2>
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.6rem" }}>
        <button style={{ flex: 1 }} onClick={() => nav("manual-entry")}>
          Log a post
        </button>
        <button style={{ flex: 1 }} onClick={() => nav("manual-entry")}>
          Classify untagged
        </button>
        <button style={{ flex: 1 }} onClick={() => nav("manual-entry")}>
          Log Stir tester
        </button>
      </div>
      <p className="faint" style={{ fontSize: "0.82rem" }}>
        Each button jumps straight to the Manual Entry page with the right tab pre-flagged.
      </p>

      <Hairline />

      {/* Ask the agent */}
      <h3>Ask the agent</h3>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button style={{ flex: 1 }} onClick={() => nav("agent-chat")}>
          draft today's post →
        </button>
        <button style={{ flex: 1 }} onClick={() => nav("agent-chat")}>
          start reply session →
        </button>
      </div>
    </>
  );
};
