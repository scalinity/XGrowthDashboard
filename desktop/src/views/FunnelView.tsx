/**
 * Funnel — faithful port of app/pages/5_Funnel.py (spec §14.5).
 *
 * Sections: funnel chart (Plotly), App Store gap callout, what-we-know table,
 * daily breakdown stacked bar (Plotly).
 * No useEffect.
 */
import { useQuery } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { PlotlyChart } from "../components/PlotlyChart";
import { apiFetch } from "../lib/api";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface WhatWeKnowRow { topic: string; rule: string }
interface FunnelData {
  aggregate: Record<string, number>;
  daily: Array<Record<string, unknown>>;
  app_store_gap_label: string;
  what_we_know: WhatWeKnowRow[];
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const FunnelView = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ["funnel"],
    queryFn: () => apiFetch<FunnelData>("/views/validation"),
    retry: 1,
  });
  const { data: funnelChart, isLoading: fc } = useQuery({
    queryKey: ["chart-funnel"],
    queryFn: () => apiFetch<{ data: unknown[]; layout: Record<string, unknown> }>("/charts/funnel"),
    retry: 1,
  });
  const { data: dailyChart, isLoading: dc } = useQuery({
    queryKey: ["chart-funnel-daily"],
    queryFn: () => apiFetch<{ data: unknown[]; layout: Record<string, unknown> }>("/charts/funnel-daily"),
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
      <Kicker>X → STIR</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Funnel</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.82rem" }}>
        Distribution signal (top of funnel) is one epistemic category; validation signal
        (downloads, testers) is another. There is no click-to-download conversion rate —
        the App Store does not report it.
      </p>

      {/* Funnel chart */}
      <h2>Last 30 days</h2>
      {fc ? (
        <p className="dim">Loading chart…</p>
      ) : funnelChart ? (
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        <PlotlyChart figure={funnelChart as any} style={{ minHeight: 360 }} />
      ) : (
        <p className="faint">Chart data unavailable.</p>
      )}

      <Callout>
        <em>{d.app_store_gap_label}.</em> Apple does not provide click-to-download
        attribution to publishers. Self-reported app-store clicks and downloads sit on
        either side of the gap; treating them as parts of a single conversion rate would
        invent a number the data cannot support.
      </Callout>

      <Hairline />

      {/* What we know / what we don't */}
      <h2>What we know · what we don't</h2>
      <p className="faint">
        The hard rules made visible. Hover any row in the funnel above to see the source
        of that stage's number.
      </p>
      <table>
        <thead>
          <tr>
            <th>Topic</th>
            <th>Rule</th>
          </tr>
        </thead>
        <tbody>
          {d.what_we_know.map((row, i) => (
            <tr key={i}>
              <td style={{ color: palette.bone }}>{row.topic}</td>
              <td style={{ color: palette.boneDim }}>{row.rule}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <Hairline />

      {/* Daily breakdown */}
      <h2>Daily breakdown</h2>
      {dc ? (
        <p className="dim">Loading chart…</p>
      ) : dailyChart ? (
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        <PlotlyChart figure={dailyChart as any} style={{ minHeight: 360 }} />
      ) : (
        <p className="faint">Chart data unavailable.</p>
      )}
      <p className="faint" style={{ marginTop: "0.3rem" }}>
        Stacked: profile visits, link clicks, getstir.app visits, downloads. The four series
        are independent — they do not compose into a single funnel because the App Store gap
        separates intent (clicks) from outcome (downloads).
      </p>
    </>
  );
};
