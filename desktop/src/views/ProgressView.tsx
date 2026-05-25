/**
 * Progress — faithful port of app/pages/3_Progress.py (spec §14.3).
 *
 * Layout mirrors the Streamlit page:
 *   1. Dual ladders (distribution + validation) in two columns
 *   2. Follower trend chart (Plotly.js from the Python figure JSON)
 *   3. Velocity projection panel (noise-floor / measurable / no data)
 *   4. Behaviour mini-bars (last 8 weeks posts + replies)
 *   5. Long-arc footer
 *
 * Data: useQuery to GET /views/progress + GET /charts/follower-trend.
 * No useEffect.
 */
import { useQuery } from "@tanstack/react-query";

import { Callout, Hairline, Kicker, ProgressBar } from "../components";
import { PlotlyChart } from "../components/PlotlyChart";
import { apiFetch } from "../lib/api";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Milestone {
  name: string;
  start_value: number | null;
  target_value: number | null;
  status: string;
  progress: number;
  progress_label: string;
}

interface VelocityProjection {
  velocity_7d_per_day: number | null;
  current_milestone_target: number | null;
  projected_milestone_hit_date_at_7d_pace: string | null;
  in_noise_floor: boolean;
}

interface WeeklyCount {
  week_start: string;
  posts: number;
  replies: number;
}

interface ProgressData {
  current_followers: number | null;
  distribution_milestones: Milestone[];
  validation_milestones: Milestone[];
  velocity_projection: VelocityProjection | null;
  noise_floor: number;
  weekly_counts: WeeklyCount[];
  targets: { post_target: number; reply_target: number; session_target: number };
  operational_ceiling: number;
  long_arc_reminder: number;
}

// ---------------------------------------------------------------------------
// Ladder component
// ---------------------------------------------------------------------------
function LadderRow({
  milestone,
  showBar,
}: {
  milestone: Milestone;
  showBar: boolean;
}) {
  const achieved = milestone.status === "achieved";
  const accent = achieved ? palette.phosphor : palette.boneDim;
  const targetStr = milestone.target_value != null ? milestone.target_value.toLocaleString() : "—";

  return (
    <div style={{ padding: "0.5rem 0", borderBottom: `1px solid ${palette.hairline}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ color: palette.bone }}>{milestone.name}</span>
        <span className="numeric" style={{ fontSize: "0.78rem", color: accent }}>
          {milestone.progress_label}
        </span>
      </div>
      <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", marginTop: "0.2rem" }}>
        <span className="numeric" style={{ fontSize: "0.74rem", color: palette.boneFaint }}>
          target {targetStr}
        </span>
      </div>
      {showBar && <ProgressBar value={milestone.progress} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------
export const ProgressView = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ["progress"],
    queryFn: () => apiFetch<ProgressData>("/views/progress"),
    retry: 1,
  });

  const {
    data: chartData,
    isLoading: chartLoading,
  } = useQuery({
    queryKey: ["chart-follower-trend"],
    queryFn: () => apiFetch<{ data: unknown[]; layout: Record<string, unknown> }>("/charts/follower-trend"),
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
  const maxWeekly = Math.max(...d.weekly_counts.map((w) => w.posts + w.replies), 1);

  return (
    <>
      {/* Header */}
      <Kicker>LONG-ARC TREND · §14.3</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Progress</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.82rem" }}>
        Distribution and validation ladders carry equal weight (§4). Follower trend below
        uses the §12 noise-floor band — judge the week, not the morning.
      </p>

      {/* Dual ladders */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2rem", marginTop: "1rem" }}>
        <div>
          <h2>Distribution ladder</h2>
          <p className="faint">
            Current followers: <span className="numeric">{(d.current_followers ?? 0).toLocaleString()}</span>
          </p>
          {d.distribution_milestones.length === 0 ? (
            <p className="faint">No milestones seeded for this ladder.</p>
          ) : (
            d.distribution_milestones.map((m, i) => (
              <LadderRow key={i} milestone={m} showBar />
            ))
          )}
        </div>
        <div>
          <h2>Validation ladder</h2>
          <p className="faint">Binary milestones; ranking by date achieved.</p>
          {d.validation_milestones.length === 0 ? (
            <p className="faint">No milestones seeded for this ladder.</p>
          ) : (
            d.validation_milestones.map((m, i) => (
              <LadderRow key={i} milestone={m} showBar={false} />
            ))
          )}
        </div>
      </div>

      <Hairline />

      {/* Follower trend chart */}
      <h2>Follower trend</h2>
      {chartLoading ? (
        <p className="dim">Loading chart…</p>
      ) : chartData ? (
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        <PlotlyChart figure={chartData as any} style={{ minHeight: 320 }} />
      ) : (
        <p className="faint">Chart data unavailable.</p>
      )}
      <p className="faint" style={{ fontSize: "0.82rem", marginTop: "0.3rem" }}>
        Shaded band is the ±2/day noise floor (§12). Days within the band are visualised,
        not arrow-marked — at this sample size, daily deltas are statistically indistinguishable
        from zero.
      </p>

      <Hairline />

      {/* Velocity projection (§28.19) */}
      <h2>Velocity projection</h2>
      {!d.velocity_projection ? (
        <p className="faint" style={{ fontSize: "0.85rem" }}>
          No account snapshots yet — velocity projection unavailable.
        </p>
      ) : d.velocity_projection.in_noise_floor ? (
        <div
          style={{
            borderLeft: `2px solid ${palette.warnAmber}`,
            padding: "0.55rem 0.85rem",
            margin: "0.4rem 0",
            background: palette.surface,
          }}
        >
          <div
            className="numeric"
            style={{
              fontSize: "0.75rem",
              color: palette.warnAmber,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            NOISE FLOOR · projections suppressed
          </div>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "1.0rem",
              color: palette.bone,
              marginTop: "0.35rem",
            }}
          >
            Trend not yet measurable — projections suppressed until |Δ7d| ≥ {d.noise_floor}.{" "}
            <span className="numeric">
              (Δ7d ={" "}
              {d.velocity_projection.velocity_7d_per_day != null
                ? `${(d.velocity_projection.velocity_7d_per_day * 7).toFixed(0)}`
                : "—"}
              )
            </span>
          </div>
        </div>
      ) : (
        <div
          style={{
            borderLeft: `2px solid ${palette.phosphor}`,
            padding: "0.55rem 0.85rem",
            margin: "0.4rem 0",
            background: palette.surface,
          }}
        >
          <div
            className="numeric"
            style={{
              fontSize: "0.75rem",
              color: palette.boneFaint,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            CURRENT PACE · {(d.velocity_projection.velocity_7d_per_day ?? 0) > 0 ? "+" : ""}
            {(d.velocity_projection.velocity_7d_per_day ?? 0).toFixed(1)} FOLLOWERS / DAY (7D)
          </div>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "1.0rem",
              color: palette.bone,
              marginTop: "0.35rem",
            }}
          >
            At this pace you'd reach{" "}
            <span className="numeric">{d.velocity_projection.current_milestone_target ?? "—"}</span> by{" "}
            <span className="numeric">
              {d.velocity_projection.projected_milestone_hit_date_at_7d_pace ?? "—"}
            </span>
            .
          </div>
        </div>
      )}

      <Hairline />

      {/* Behaviour mini-bars */}
      <h2>Behaviour (last 8 weeks)</h2>
      {d.weekly_counts.map((w) => {
        const postsPct = w.posts / maxWeekly;
        const repliesPct = w.replies / maxWeekly;
        return (
          <div key={w.week_start} style={{ padding: "0.35rem 0" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <span className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
                Week of {w.week_start}
              </span>
              <span className="numeric" style={{ fontSize: "0.78rem", color: palette.bone }}>
                posts {w.posts} · replies {w.replies}
              </span>
            </div>
            <div
              style={{
                display: "flex",
                gap: "1px",
                marginTop: "0.25rem",
                height: "8px",
                background: palette.surfaceRaised,
                borderRadius: "1px",
                overflow: "hidden",
              }}
            >
              <div style={{ width: `${(postsPct * 100).toFixed(1)}%`, background: palette.phosphor }} />
              <div style={{ width: `${(repliesPct * 100).toFixed(1)}%`, background: palette.phosphorDim }} />
            </div>
          </div>
        );
      })}
      <p className="faint" style={{ marginTop: "1rem", fontSize: "0.82rem" }}>
        Daily targets — posts {d.targets.post_target}, replies {d.targets.reply_target},
        reply sessions {d.targets.session_target}. Bar lengths are normalised against the
        busiest week shown.
      </p>

      <Hairline />

      {/* Long-arc footer */}
      <p className="faint" style={{ fontSize: "0.78rem", textAlign: "center" }}>
        Operational ceiling: <span className="numeric">{d.operational_ceiling.toLocaleString()}</span>.
        Long-arc reminder: <span className="numeric">{d.long_arc_reminder.toLocaleString()}</span> — not
        operational.
      </p>
    </>
  );
};
