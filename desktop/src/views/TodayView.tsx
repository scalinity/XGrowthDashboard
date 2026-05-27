/**
 * Today / Weigh-In — interactive dashboard redesign of app/pages/1_Today.py (spec §14.1).
 *
 * Bento-grid layout with instrument panels, SVG sparkline chart, ring gauges
 * for daily reps, and gradient milestone bar. All data from a single useQuery
 * to GET /views/today (§31.10). No useEffect.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  Callout,
  Kicker,
  PrepublishChip,
  RepetitionBanner,
} from "../components";
import { apiFetch } from "../lib/api";
import { useNav } from "../lib/nav";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
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
  high_engagement_replies_shipped: number;
  icp_intent_replies_shipped: number;
  candidates_reviewed_today: number;
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

interface SparklinePoint {
  date: string;
  count: number;
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
  follower_sparkline: SparklinePoint[];
}

interface UserMetricsRefresh {
  username: string | null;
  profile_url?: string | null;
  x_user_id?: string | null;
  followers_count: number | null;
  following_count: number | null;
  post_count: number | null;
  listed_count: number | null;
  snapshot_inserted?: boolean;
  skipped_reason?: string | null;
}

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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  const weekday = d.toLocaleDateString("en-US", { weekday: "long" }).toUpperCase();
  const month = d.toLocaleDateString("en-US", { month: "long" }).toUpperCase();
  const day = d.getDate();
  const year = d.getFullYear();
  return `${weekday} · ${month} ${day}, ${year}`;
}

function deltaColor(value: number | null, noiseFloor = 2): string {
  if (value === null || Math.abs(value) <= noiseFloor) return palette.boneDim;
  return value > 0 ? palette.phosphor : palette.warnAmber;
}

const CONFIRM_COLORS: Record<string, string> = {
  confirmed: palette.confidenceConfidentBg,
  needs_id: palette.confidenceDirectionalBg,
  needs_metrics: palette.confidenceDirectionalBg,
  draft: palette.confidenceInsufficientBg,
};

// ---------------------------------------------------------------------------
// SVG: Sparkline area chart
// ---------------------------------------------------------------------------
function Sparkline({ points }: { points: SparklinePoint[] }) {
  if (points.length < 2) {
    return (
      <div className="faint" style={{ fontSize: "0.82rem", padding: "1rem 0" }}>
        Not enough data for trend yet.
      </div>
    );
  }

  const W = 280, H = 72, PAD = 4;
  const counts = points.map((p) => p.count);
  const min = Math.min(...counts);
  const max = Math.max(...counts);
  const range = max - min || 1;

  const pts = points.map((p, i) => ({
    x: PAD + (i / (points.length - 1)) * (W - 2 * PAD),
    y: PAD + (1 - (p.count - min) / range) * (H - 2 * PAD),
  }));

  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = `${line} L${pts[pts.length - 1].x.toFixed(1)},${H} L${pts[0].x.toFixed(1)},${H} Z`;

  const last = pts[pts.length - 1];
  const delta = points[points.length - 1].count - points[0].count;
  const startLabel = points[0].date.slice(5);
  const endLabel = points[points.length - 1].date.slice(5);

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        preserveAspectRatio="none"
        style={{ display: "block" }}
      >
        <defs>
          <linearGradient id="today-spark-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={palette.phosphor} stopOpacity={0.3} />
            <stop offset="100%" stopColor={palette.phosphor} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#today-spark-fill)" />
        <path d={line} fill="none" stroke={palette.phosphor} strokeWidth={1.8} strokeLinejoin="round" />
        <circle cx={last.x} cy={last.y} r={3.5} fill={palette.phosphor} />
      </svg>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginTop: "0.3rem",
        }}
      >
        <span className="faint" style={{ fontSize: "0.7rem", fontFamily: "var(--font-mono)" }}>
          {startLabel}
        </span>
        <span
          className="numeric"
          style={{ fontSize: "0.82rem", fontWeight: 600, color: delta >= 0 ? palette.phosphor : palette.warnAmber }}
        >
          {delta >= 0 ? "+" : ""}
          {delta} net
        </span>
        <span className="faint" style={{ fontSize: "0.7rem", fontFamily: "var(--font-mono)" }}>
          {endLabel}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SVG: Ring gauge for daily reps
// ---------------------------------------------------------------------------
function RingGauge({
  value,
  max,
  label,
  met,
}: {
  value: number;
  max: number;
  label: string;
  met: boolean;
}) {
  const SIZE = 70;
  const STROKE = 5;
  const R = (SIZE - STROKE) / 2;
  const C = 2 * Math.PI * R;
  const pct = max > 0 ? Math.min(value / max, 1) : 0;
  const offset = C * (1 - pct);
  const color = met ? palette.phosphor : pct > 0 ? palette.phosphorDim : palette.hairline;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
      <div style={{ position: "relative", width: SIZE, height: SIZE }}>
        <svg width={SIZE} height={SIZE} style={{ transform: "rotate(-90deg)" }}>
          <circle
            cx={SIZE / 2} cy={SIZE / 2} r={R}
            fill="none" stroke={palette.hairline} strokeWidth={STROKE}
          />
          <circle
            cx={SIZE / 2} cy={SIZE / 2} r={R}
            fill="none" stroke={color} strokeWidth={STROKE}
            strokeDasharray={C} strokeDashoffset={offset}
            strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 800ms ease, stroke 300ms ease" }}
          />
        </svg>
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <span className="numeric" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
            {value}
            <span style={{ color: palette.boneFaint, fontWeight: 400 }}>/{max}</span>
          </span>
        </div>
      </div>
      <div
        className="kicker"
        style={{ marginTop: "0.35rem", color: met ? palette.phosphor : undefined }}
      >
        {label}
        {met && " ✓"}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
function DeltaLine({
  label,
  value,
  noiseFloor = 2,
}: {
  label: string;
  value: number | null;
  noiseFloor?: number;
}) {
  if (value === null || value === undefined) return null;
  const color = deltaColor(value, noiseFloor);
  const prefix = value > 0 ? "+" : "";
  return (
    <div className="numeric" style={{ fontSize: "0.88rem", color: palette.boneDim }}>
      <span style={{ color, fontWeight: 500 }}>
        {prefix}
        {value}
      </span>{" "}
      {label}
    </div>
  );
}

function MixBar({
  label,
  value,
  target,
  met,
}: {
  label: string;
  value: number;
  target: number | null;
  met: boolean;
}) {
  const pct = target && target > 0 ? Math.min(value / target, 1) : 0;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
        <span style={{ fontSize: "0.82rem", color: palette.boneDim }}>{label}</span>
        <span className="numeric" style={{ fontSize: "0.82rem", color: met ? palette.phosphor : palette.bone }}>
          {value}
          {target != null && <span style={{ color: palette.boneFaint }}>/{target}</span>}
          {met && " ✓"}
        </span>
      </div>
      {target != null && (
        <div style={{ height: 4, background: palette.surfaceRaised, borderRadius: 2 }}>
          <div
            style={{
              height: "100%",
              width: `${(pct * 100).toFixed(0)}%`,
              background: met ? palette.phosphor : palette.phosphorDim,
              borderRadius: 2,
              transition: "width 600ms ease",
            }}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Snapshot prompt (no-snapshot state)
// ---------------------------------------------------------------------------
function SnapshotPrompt({
  defaults,
  autoFetching,
  autoError,
  onSuccess,
}: {
  defaults: TodayData["snapshot_defaults"];
  autoFetching: boolean;
  autoError: string | null;
  onSuccess: () => void;
}) {
  const [followers, setFollowers] = useState("");
  const [following, setFollowing] = useState("");
  const [posts, setPosts] = useState("");
  const [listed, setListed] = useState("");

  const fetchMetrics = useMutation({
    mutationFn: () => apiFetch<UserMetricsRefresh>("/api/user-metrics"),
    onSuccess: (data) => {
      if (data.followers_count != null) setFollowers(String(data.followers_count));
      if (data.following_count != null) setFollowing(String(data.following_count));
      if (data.post_count != null) setPosts(String(data.post_count));
      if (data.listed_count != null) setListed(String(data.listed_count));
      onSuccess();
    },
  });

  const saveMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ snapshot_id: number }>("/forms/snapshot", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess,
  });

  const resolvedUsername = defaults.username || "";
  const resolvedProfileUrl = defaults.profile_url || "";

  const handleSubmit = () => {
    saveMutation.mutate({
      snapshot_date: new Date().toISOString().slice(0, 10),
      username: resolvedUsername,
      profile_url: resolvedProfileUrl,
      baseline_followers: defaults.baseline_followers || 0,
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
    <div
      className="today-panel accent"
      style={{ animation: "none", maxWidth: 560, margin: "1rem 0" }}
    >
      <h3 style={{ margin: "0 0 0.3rem" }}>Pin today's snapshot</h3>
      <p className="dim" style={{ fontSize: "0.85rem", margin: "0 0 1rem" }}>
        The dashboard reads from the canonical daily row. Pin it to unlock today's view.
        {autoFetching && (
          <span style={{ color: palette.phosphor }}> Syncing with X…</span>
        )}
        {autoError && (
          <span style={{ color: palette.warnAmber }}> {autoError}</span>
        )}
      </p>

      <button
        className="primary"
        onClick={() => fetchMetrics.mutate()}
        disabled={fetchMetrics.isPending}
        style={{
          padding: "0.6rem 1.6rem",
          fontSize: "0.9rem",
          width: "100%",
          opacity: fetchMetrics.isPending ? 0.6 : 1,
        }}
      >
        {fetchMetrics.isPending ? "Fetching…" : "Fetch from X"}
      </button>

      {fetchMetrics.isError && (
        <div style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.5rem" }}>
          {String((fetchMetrics.error as Error).message ?? fetchMetrics.error)}
        </div>
      )}
      {fetchMetrics.isSuccess && (
        <div style={{ color: palette.phosphor, fontSize: "0.82rem", marginTop: "0.5rem" }}>
          ✓ Fetched from @{(fetchMetrics.data as UserMetricsRefresh)?.username}
        </div>
      )}

      <details style={{ marginTop: "1rem" }}>
        <summary
          style={{
            cursor: "pointer",
            color: palette.boneDim,
            fontSize: "0.85rem",
            listStyle: "none",
          }}
        >
          ▸ Or enter manually
        </summary>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "0.5rem",
            marginTop: "0.6rem",
            marginBottom: "0.6rem",
          }}
        >
          {[
            { label: "Followers", value: followers, set: setFollowers },
            { label: "Following", value: following, set: setFollowing },
            { label: "Posts", value: posts, set: setPosts },
            { label: "Listed", value: listed, set: setListed },
          ].map(({ label, value, set }) => (
            <div key={label}>
              <label className="kicker" style={{ display: "block", marginBottom: "0.15rem" }}>
                {label}
              </label>
              <input
                type="number"
                value={value}
                onChange={(e) => set(e.target.value)}
                style={{ width: "100%" }}
              />
            </div>
          ))}
        </div>
        <button
          onClick={handleSubmit}
          disabled={!valid || saveMutation.isPending}
          className={valid ? "primary" : undefined}
          style={{ width: "100%" }}
        >
          {saveMutation.isPending ? "Saving…" : "Save daily snapshot"}
        </button>
        {saveMutation.isError && (
          <div style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.4rem" }}>
            {String((saveMutation.error as Error).message ?? saveMutation.error)}
          </div>
        )}
      </details>
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
  const autoMetrics = useQuery({
    queryKey: ["today-auto-user-metrics", data?.today_iso],
    queryFn: async () => {
      const result = await apiFetch<UserMetricsRefresh>("/api/user-metrics");
      await queryClient.invalidateQueries({ queryKey: ["today"] });
      return result;
    },
    enabled: Boolean(data && !data.snapshot),
    retry: false,
    staleTime: Infinity,
  });
  const syncToday = useMutation({
    mutationFn: () =>
      apiFetch<TodaySyncResponse>("/api/sync-today", { method: "POST" }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["today"] }),
        queryClient.invalidateQueries({ queryKey: ["needs-tagging"] }),
        queryClient.invalidateQueries({ queryKey: ["needs-post-id"] }),
      ]);
    },
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
  const reps = d.daily_reps.row;
  const targets = d.daily_reps.targets;
  const hasMix = "high_eng" in d.daily_reps.mix;

  return (
    <div>
      {/* ── Header ────────────────────────────────────────── */}
      <Kicker>{formatDate(d.today_iso)}</Kicker>
      <h1 style={{ fontSize: "2.1rem", marginBottom: "0.15rem" }}>Today</h1>

      {!snap ? (
        /* ── No snapshot: prompt ─────────────────────────── */
        <SnapshotPrompt
          defaults={d.snapshot_defaults}
          autoFetching={autoMetrics.isFetching}
          autoError={
            autoMetrics.isError
              ? String((autoMetrics.error as Error).message ?? autoMetrics.error)
              : null
          }
          onSuccess={() => queryClient.invalidateQueries({ queryKey: ["today"] })}
        />
      ) : (
        <>
          {/* Collapsed snapshot summary */}
          <details style={{ marginBottom: "0.4rem" }}>
            <summary style={{ cursor: "pointer", color: palette.boneFaint, fontSize: "0.8rem" }}>
              Snapshot logged — {snap.followers_count.toLocaleString()} followers
            </summary>
            <div
              className="numeric"
              style={{ fontSize: "0.82rem", padding: "0.3rem 0", color: palette.boneDim }}
            >
              followers={snap.followers_count.toLocaleString()} · following=
              {snap.following_count.toLocaleString()} · posts=
              {snap.post_count.toLocaleString()} · listed=
              {snap.listed_count.toLocaleString()}
            </div>
          </details>

          {/* ── Dashboard grid ──────────────────────────── */}
          <div className="today-grid">
            {/* ROW 1: Hero Followers + Sparkline */}
            <div className="today-panel accent" style={{ animationDelay: "0ms" }}>
              <Kicker>FOLLOWERS</Kicker>
              <div
                className="numeric"
                style={{
                  fontSize: "3.4rem",
                  fontWeight: 600,
                  letterSpacing: "-0.03em",
                  lineHeight: 1.05,
                  marginTop: "0.25rem",
                  color: palette.bone,
                }}
              >
                {snap.followers_count.toLocaleString()}
              </div>
              <div
                style={{
                  marginTop: "0.7rem",
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.15rem",
                }}
              >
                <DeltaLine label="vs yesterday" value={snap.delta_vs_yesterday} />
                <DeltaLine label="since baseline" value={snap.delta_vs_baseline} noiseFloor={0} />
                {snap.distance_to_current_milestone != null && (
                  <div className="numeric" style={{ fontSize: "0.88rem", color: palette.boneDim }}>
                    <span style={{ color: palette.bone, fontWeight: 500 }}>
                      {snap.distance_to_current_milestone.toLocaleString()}
                    </span>{" "}
                    to {target.toLocaleString()}
                  </div>
                )}
              </div>
            </div>

            <div className="today-panel" style={{ animationDelay: "60ms" }}>
              <Kicker>14-DAY TREND</Kicker>
              <div style={{ marginTop: "0.5rem" }}>
                <Sparkline points={d.follower_sparkline} />
              </div>
            </div>

            {/* ROW 2: Milestone progress (full width) */}
            {d.milestone && d.milestone_progress_pct != null && (
              <div className="today-panel full" style={{ animationDelay: "120ms" }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    marginBottom: "0.5rem",
                  }}
                >
                  <Kicker>MILESTONE · {d.milestone.name}</Kicker>
                  <span
                    className="numeric"
                    style={{ fontSize: "1.15rem", fontWeight: 600, color: palette.phosphor }}
                  >
                    {(d.milestone_progress_pct * 100).toFixed(1)}%
                  </span>
                </div>
                <div
                  style={{
                    height: "0.6rem",
                    background: palette.surfaceRaised,
                    borderRadius: "4px",
                    overflow: "hidden",
                    position: "relative",
                  }}
                >
                  <div
                    style={{
                      height: "100%",
                      width: `${(d.milestone_progress_pct * 100).toFixed(1)}%`,
                      background: `linear-gradient(90deg, ${palette.phosphorDim}, ${palette.phosphor})`,
                      borderRadius: "4px",
                      transition: "width 800ms ease",
                    }}
                  />
                </div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginTop: "0.3rem",
                  }}
                >
                  <span className="numeric faint" style={{ fontSize: "0.72rem" }}>
                    {(d.milestone.start_value ?? d.baseline_followers).toLocaleString()}
                  </span>
                  <span className="numeric faint" style={{ fontSize: "0.72rem" }}>
                    {(d.milestone.target_value ?? target).toLocaleString()}
                  </span>
                </div>
              </div>
            )}

            {/* ROW 3: Velocity + Content Reco */}
            <div className="today-panel" style={{ animationDelay: "180ms" }}>
              <Kicker>7-DAY VELOCITY</Kicker>
              {d.velocity_measurable && d.velocity_7d_per_day != null ? (
                <>
                  <div
                    className="numeric"
                    style={{
                      fontSize: "1.8rem",
                      fontWeight: 500,
                      marginTop: "0.2rem",
                      color: d.velocity_7d_per_day >= 0 ? palette.phosphor : palette.warnAmber,
                    }}
                  >
                    {d.velocity_7d_per_day > 0 ? "+" : ""}
                    {d.velocity_7d_per_day.toFixed(1)}
                  </div>
                  <div className="faint" style={{ fontSize: "0.78rem", marginTop: "0.1rem" }}>
                    followers/day
                  </div>
                </>
              ) : (
                <>
                  <div
                    className="numeric"
                    style={{
                      fontSize: "1.8rem",
                      fontWeight: 500,
                      marginTop: "0.2rem",
                      color: palette.boneFaint,
                    }}
                  >
                    &mdash;
                  </div>
                  <div className="faint" style={{ fontSize: "0.78rem", marginTop: "0.1rem" }}>
                    not yet measurable
                  </div>
                </>
              )}
            </div>

            <div className="today-panel" style={{ animationDelay: "240ms" }}>
              <Kicker>CONTENT RECO</Kicker>
              <div
                className="numeric"
                style={{
                  fontSize: "1.5rem",
                  fontWeight: 500,
                  marginTop: "0.2rem",
                  color: d.content_type_reco.under_represented ? palette.bone : palette.boneFaint,
                  textTransform: "capitalize",
                }}
              >
                {d.content_type_reco.under_represented ?? "—"}
              </div>
              <div className="faint" style={{ fontSize: "0.78rem", marginTop: "0.1rem" }}>
                {d.content_type_reco.rationale}
              </div>
            </div>

            {/* ROW 4: Daily Reps + Reply Mix */}
            <div
              className={`today-panel${hasMix ? "" : " full"}`}
              style={{ animationDelay: "300ms" }}
            >
              <Kicker>DAILY REPS</Kicker>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-around",
                  marginTop: "0.8rem",
                  marginBottom: "0.4rem",
                }}
              >
                <RingGauge
                  value={reps?.posts_shipped ?? 0}
                  max={targets.post_target}
                  label="POSTS"
                  met={reps?.post_target_met ?? false}
                />
                <RingGauge
                  value={reps?.replies_shipped ?? 0}
                  max={targets.reply_target}
                  label="REPLIES"
                  met={reps?.reply_target_met ?? false}
                />
                <RingGauge
                  value={reps?.reply_sessions_completed ?? 0}
                  max={targets.session_target}
                  label="SESSIONS"
                  met={reps?.session_target_met ?? false}
                />
              </div>
              {reps && (
                <div style={{ textAlign: "center" }}>
                  <span
                    className="kicker"
                    style={{
                      color: reps.minimum_reps_completed ? palette.phosphor : palette.warnAmber,
                    }}
                  >
                    {reps.minimum_reps_completed ? "✓ MINIMUM REPS COMPLETE" : "MINIMUM REPS INCOMPLETE"}
                  </span>
                </div>
              )}
              {!reps && (
                <div className="faint" style={{ textAlign: "center", fontSize: "0.8rem" }}>
                  No activity logged yet today.
                </div>
              )}
            </div>

            {hasMix && (
              <div className="today-panel" style={{ animationDelay: "360ms" }}>
                <Kicker>REPLY MIX</Kicker>
                <div
                  style={{
                    marginTop: "0.7rem",
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.7rem",
                  }}
                >
                  <MixBar
                    label="High-engagement"
                    value={(d.daily_reps.mix as RepsMix).high_eng}
                    target={(d.daily_reps.mix as RepsMix).high_eng_target}
                    met={(d.daily_reps.mix as RepsMix).high_eng_met}
                  />
                  <MixBar
                    label="ICP discovery"
                    value={(d.daily_reps.mix as RepsMix).icp_intent}
                    target={null}
                    met={false}
                  />
                  <MixBar
                    label="Candidates reviewed"
                    value={(d.daily_reps.mix as RepsMix).candidates_rev}
                    target={(d.daily_reps.mix as RepsMix).cand_target}
                    met={(d.daily_reps.mix as RepsMix).cand_met}
                  />
                </div>
              </div>
            )}
          </div>

          {/* ── Pending drafts (collapsible) ─────────────── */}
          {d.pending_drafts.length > 0 && (
            <details
              className="today-section"
              open
              style={{ animationDelay: "420ms", marginBottom: "1rem" }}
            >
              <summary>
                <h2 style={{ margin: 0, fontSize: "1.2rem" }}>
                  Pending drafts{" "}
                  <span className="faint" style={{ fontSize: "0.85rem", fontWeight: 400 }}>
                    ({d.pending_drafts.length})
                  </span>
                </h2>
              </summary>
              <div style={{ marginTop: "0.6rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {d.pending_drafts.map((draft) => (
                  <div
                    key={draft.id}
                    style={{
                      background: palette.surface,
                      border: `1px solid ${palette.hairline}`,
                      borderRadius: "4px",
                      padding: "0.75rem 1rem",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        marginBottom: "0.3rem",
                      }}
                    >
                      <span className="numeric" style={{ fontSize: "0.75rem", color: palette.boneFaint }}>
                        #{draft.id} · {draft.draft_kind}
                      </span>
                      <PrepublishChip label={draft.composite_label} />
                    </div>
                    <div style={{ color: palette.bone, fontSize: "0.9rem", lineHeight: 1.45 }}>
                      {draft.text_preview}
                    </div>
                    <RepetitionBanner warningJson={draft.similarity_warning_json} />
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* ── Recent activity (collapsible) ─────────────── */}
          <details
            className="today-section"
            open
            style={{ animationDelay: "480ms", marginBottom: "1rem" }}
          >
            <summary>
              <h2 style={{ margin: 0, fontSize: "1.2rem" }}>
                Recent activity{" "}
                <span className="faint" style={{ fontSize: "0.85rem", fontWeight: 400 }}>
                  ({d.recent_posts.length})
                </span>
              </h2>
            </summary>
            {d.recent_posts.length === 0 ? (
              <p className="faint" style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>
                No posts logged today yet.
              </p>
            ) : (
              <div style={{ marginTop: "0.6rem", display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                {d.recent_posts.map((post) => {
                  const lane = post.pillar
                    ? `${post.pillar} · ${post.audience} · ${post.cta}`
                    : "unclassified";
                  const confirmColor = CONFIRM_COLORS[post.confirm_status] ?? palette.surfaceRaised;
                  return (
                    <div
                      key={post.id}
                      style={{
                        background: palette.surface,
                        border: `1px solid ${palette.hairline}`,
                        borderRadius: "4px",
                        padding: "0.65rem 1rem",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          marginBottom: "0.2rem",
                        }}
                      >
                        <span className="numeric" style={{ fontSize: "0.72rem", color: palette.boneFaint }}>
                          {post.type} · {lane}
                        </span>
                        <span
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontSize: "0.68rem",
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
                      <div style={{ color: palette.bone, fontSize: "0.88rem", lineHeight: 1.4 }}>
                        {post.text_preview}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </details>

          {/* ── Quick actions ──────────────────────────────── */}
          <div
            className="today-panel full"
            style={{ animationDelay: "540ms", margin: "0.5rem 0" }}
          >
            <Kicker>QUICK ACTIONS</Kicker>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                gap: "0.5rem",
                marginTop: "0.6rem",
              }}
            >
              <button
                className="primary"
                onClick={() => syncToday.mutate()}
                disabled={syncToday.isPending}
                style={{ opacity: syncToday.isPending ? 0.65 : 1 }}
              >
                {syncToday.isPending ? "Syncing..." : "Sync X activity"}
              </button>
              <button onClick={() => nav("manual-entry")}>Manual fallback</button>
              <button onClick={() => nav("manual-entry")}>Classify untagged</button>
              <button onClick={() => nav("manual-entry")}>Log Stir tester</button>
            </div>
            {syncToday.isSuccess && (
              <div
                className="numeric"
                style={{ color: palette.phosphor, fontSize: "0.82rem", marginTop: "0.65rem" }}
              >
                X sync: +{syncToday.data.import_posts.posts_inserted} imported · {" "}
                {syncToday.data.metrics.posts_refreshed} metrics · reps {" "}
                {syncToday.data.activity.daily_activity.posts_shipped}/
                {syncToday.data.activity.daily_activity.replies_shipped}/
                {syncToday.data.activity.daily_activity.quotes_shipped}
                {syncToday.data.warnings.length > 0 && (
                  <span style={{ color: palette.warnAmber }}> · partial</span>
                )}
              </div>
            )}
            {syncToday.isError && (
              <div style={{ color: palette.warnAmber, fontSize: "0.82rem", marginTop: "0.65rem" }}>
                {String((syncToday.error as Error).message ?? syncToday.error)}
              </div>
            )}

            <div className="kicker" style={{ marginTop: "0.8rem" }}>ASK THE AGENT</div>
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
              <button className="primary" style={{ flex: 1 }} onClick={() => nav("agent-chat")}>
                Draft today's post →
              </button>
              <button className="primary" style={{ flex: 1 }} onClick={() => nav("agent-chat")}>
                Start reply session →
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
