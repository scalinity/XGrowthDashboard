/**
 * Content Performance — faithful port of app/pages/4_Content_Performance.py (spec §14.4).
 *
 * Layout mirrors the Streamlit page:
 *   1. Best-lane callout (gated on ≥3 rankable lanes)
 *   2. Lane grid (custom table — NOT Plotly)
 *   3. V/G/P/P content-type table (§28.17)
 *   4. Raw evidence scatter chart (Plotly, lane-colored)
 *   5. Pre-publish scorer calibration table
 *   6. What this view can / can't tell you
 *   7. Agent integration buttons
 *
 * No useEffect.
 */
import { useQuery } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { ConfidenceBadge, type ConfidenceTier } from "../components/badges";
import { PlotlyChart } from "../components/PlotlyChart";
import { apiFetch } from "../lib/api";
import { useNav } from "../lib/nav";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface LaneRow {
  pillar: string;
  audience: string;
  cta: string;
  post_count: number;
  days_covered: number;
  median_display: string;
  total_bookmarks: number;
  total_replies: number;
  stir_signal_count: number;
  ui_label: string;
  chip_bg: string;
}

interface BestLane {
  lane: string;
  median_impressions: number;
  iqr_low: number;
  iqr_high: number;
  ui_label: string;
  chip_bg: string;
}

interface ContentTypeRow {
  content_type: string;
  post_count: number;
  days_covered: number;
  median_impressions: number | null;
  median_engagement_rate: number | null;
  ui_label: string;
  chip_bg: string;
}

interface CalibrationRow {
  composite_label: string;
  n: number;
  avg_impressions: number | null;
  avg_engagement_rate: number | null;
  avg_screenshot_test_score: number | null;
  n_with_screenshot_score: number;
}

interface ContentPerfData {
  lanes: LaneRow[];
  rankable_count: number;
  best_lane: BestLane | null;
  content_types: ContentTypeRow[];
  calibration: CalibrationRow[];
}

const UI_TO_TIER: Record<string, ConfidenceTier> = {
  insufficient: "insufficient",
  directional: "directional",
  tentative: "tentative",
  confident: "confident",
};

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const ContentPerformanceView = () => {
  const nav = useNav();
  const { data, isLoading, error } = useQuery({
    queryKey: ["content-performance"],
    queryFn: () => apiFetch<ContentPerfData>("/views/content-performance"),
    retry: 1,
  });
  const { data: scatterData, isLoading: scatterLoading } = useQuery({
    queryKey: ["chart-lane-scatter"],
    queryFn: () => apiFetch<{ data: unknown[]; layout: Record<string, unknown> }>("/charts/lane-scatter"),
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

  return (
    <>
      {/* Header */}
      <Kicker>LANE ANALYSIS · §14.4 / §11</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Content performance</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.82rem" }}>
        Lanes are scored at four confidence tiers:{" "}
        <strong>insufficient</strong> (n&lt;5 or days&lt;3) ·{" "}
        <strong>directional</strong> (n 5–14) ·{" "}
        <strong>tentative</strong> (n 15–29, days ≥7) ·{" "}
        <strong>confident</strong> (n ≥30, days ≥14).
        Ranking is only allowed at tentative or above.
      </p>

      {/* Best-lane callout */}
      {d.rankable_count >= 3 && d.best_lane ? (
        <Callout>
          <em>Best lane (provisional):</em>{" "}
          <span className="numeric">{d.best_lane.lane}</span> with median impressions{" "}
          <span className="numeric">{d.best_lane.median_impressions.toLocaleString()}</span>{" "}
          (IQR <span className="numeric">
            {d.best_lane.iqr_low.toLocaleString()}–{d.best_lane.iqr_high.toLocaleString()}
          </span>).{" "}
          <ConfidenceBadge tier={UI_TO_TIER[d.best_lane.ui_label] ?? "insufficient"} label={d.best_lane.ui_label} />
        </Callout>
      ) : (
        <Callout>
          <em>No best-lane callout.</em> Fewer than 3 lanes are at{" "}
          <strong>tentative</strong> or above; ranking would be premature (§14.4 anti-overfitting rule).
          Read the grid and scatter below as evidence-in-progress, not a leaderboard.
        </Callout>
      )}

      {/* Lane grid */}
      <h2>Lane grid</h2>
      {d.lanes.length === 0 ? (
        <p className="faint">
          No classified posts yet. Classify a few from <strong>Manual entry → Needs tagging</strong> to populate this grid.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Lane</th>
              <th>Posts</th>
              <th>Days</th>
              <th>Median imp [IQR]</th>
              <th>Bkmks</th>
              <th>Repl</th>
              <th>Stir</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            {d.lanes.map((l, i) => (
              <tr key={i}>
                <td className="numeric" style={{ fontSize: "0.92rem" }}>
                  {l.pillar} · {l.audience} · {l.cta}
                </td>
                <td className="numeric">{l.post_count}</td>
                <td className="numeric">{l.days_covered}</td>
                <td className="numeric">{l.median_display}</td>
                <td className="numeric">{l.total_bookmarks.toLocaleString()}</td>
                <td className="numeric">{l.total_replies.toLocaleString()}</td>
                <td className="numeric">{l.stir_signal_count.toLocaleString()}</td>
                <td>
                  <ConfidenceBadge tier={UI_TO_TIER[l.ui_label] ?? "insufficient"} label={l.ui_label} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Hairline />

      {/* V/G/P/P content type table (§28.17) */}
      <h2>Content type — V/G/P/P (§28.17)</h2>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Performance sliced by <em>purpose</em> (value / growth / personality / proof) —
        orthogonal to pillar (topic). Same confidence ladder as the lane grid.
        Rows with content_type='unspecified' are excluded.
      </p>
      {d.content_types.length === 0 ? (
        <p className="faint" style={{ fontSize: "0.85rem" }}>
          No classified posts yet. As you log posts with a V/G/P/P content_type, this table fills in.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>type</th>
              <th style={{ textAlign: "right" }}>n</th>
              <th style={{ textAlign: "right" }}>days</th>
              <th style={{ textAlign: "right" }}>median impressions</th>
              <th style={{ textAlign: "right" }}>median ER</th>
              <th>confidence</th>
            </tr>
          </thead>
          <tbody>
            {d.content_types.map((ct) => (
              <tr key={ct.content_type}>
                <td><code>{ct.content_type}</code></td>
                <td className="numeric" style={{ textAlign: "right" }}>{ct.post_count}</td>
                <td className="numeric" style={{ textAlign: "right" }}>{ct.days_covered}</td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {ct.median_impressions != null ? Math.round(ct.median_impressions).toLocaleString() : "—"}
                </td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {ct.median_engagement_rate != null ? ct.median_engagement_rate.toFixed(3) : "—"}
                </td>
                <td>
                  <ConfidenceBadge tier={UI_TO_TIER[ct.ui_label] ?? "insufficient"} label={ct.ui_label} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Hairline />

      {/* Raw evidence scatter */}
      <h2>Raw evidence — last 30 days</h2>
      <p className="faint">
        Every classified post in the last 30 days, colored by lane. When the lane grid is below threshold,
        this scatter is the honest read.
      </p>
      {scatterLoading ? (
        <p className="dim">Loading chart…</p>
      ) : scatterData ? (
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        <PlotlyChart figure={scatterData as any} style={{ minHeight: 440 }} />
      ) : (
        <p className="faint">Chart data unavailable.</p>
      )}

      <Hairline />

      {/* Pre-publish scorer calibration */}
      <h2>Pre-publish scorer calibration</h2>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Shipped agent drafts grouped by their §28.11 pre-publish composite_label,
        paired with what actually happened. The scorer is well-calibrated when 'strong'
        rows average above 'viable' above 'weak'.
      </p>
      {d.calibration.length === 0 ? (
        <p className="faint" style={{ fontSize: "0.85rem" }}>
          No shipped agent drafts with impressions yet. The calibration table fills in as you ship agent-assisted posts.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>label</th>
              <th style={{ textAlign: "right" }}>n</th>
              <th style={{ textAlign: "right" }}>avg impressions</th>
              <th style={{ textAlign: "right" }}>avg engagement rate</th>
              <th style={{ textAlign: "right" }}>avg screenshot test</th>
            </tr>
          </thead>
          <tbody>
            {d.calibration.map((r) => (
              <tr key={r.composite_label}>
                <td>{r.composite_label}</td>
                <td className="numeric" style={{ textAlign: "right" }}>{r.n}</td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {r.avg_impressions != null ? Math.round(r.avg_impressions).toLocaleString() : "—"}
                </td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {r.avg_engagement_rate != null ? r.avg_engagement_rate.toFixed(3) : "—"}
                </td>
                <td className="numeric" style={{ textAlign: "right" }}>
                  {r.avg_screenshot_test_score != null && r.n_with_screenshot_score > 0
                    ? `${r.avg_screenshot_test_score.toFixed(2)} (n=${r.n_with_screenshot_score})`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Hairline />

      {/* What this view can and can't tell you */}
      <h2>What this view can and can't tell you</h2>
      <table>
        <thead>
          <tr>
            <th>What it can</th>
            <th>What it can't</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Surface medians + IQR per lane once n≥5.</td>
            <td>Establish causation. Lanes correlate with outcomes; nothing here proves a lane <em>caused</em> a follower.</td>
          </tr>
          <tr>
            <td>Refuse to rank below the threshold.</td>
            <td>Tell you what to post next — only what category needs more data. See <strong>Next rep</strong>.</td>
          </tr>
          <tr>
            <td>Show outliers via IQR width.</td>
            <td>Adjust for time-of-day, platform algorithm shifts, cohort effects.</td>
          </tr>
        </tbody>
      </table>

      <Hairline />

      {/* Agent integration */}
      <h3>Ask the agent</h3>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button style={{ flex: 1 }} onClick={() => nav("agent-chat")}>
          why is this lane underperforming? →
        </button>
        <button style={{ flex: 1 }} onClick={() => nav("agent-chat")}>
          extract lesson from a post →
        </button>
      </div>
    </>
  );
};
