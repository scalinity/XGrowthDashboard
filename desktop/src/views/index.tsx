/**
 * View registry + components (spec §31.7). The 18 views, grouped to mirror the
 * Streamlit page order. Core read-only views are wired to the sidecar API via
 * TanStack Query (no useEffect — per the project React rules). Views whose
 * read endpoints land in later increments render a design-system scaffold so
 * the app is fully navigable now.
 */
import type { FC, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { Callout, Hairline, Kicker } from "../components";
import { TodayView } from "./TodayView";
import { ProgressView } from "./ProgressView";
import { ContentPerformanceView } from "./ContentPerformanceView";
import { NextRepView } from "./NextRepView";
import { FunnelView } from "./FunnelView";

export interface ViewDef {
  id: string;
  label: string;
  group: "Analytics" | "Manual" | "Agent" | "Growth";
  Component: FC;
}

// --- shared render helpers ---------------------------------------------------
function ViewHeader({ kicker, title, blurb }: { kicker: string; title: string; blurb?: string }) {
  return (
    <header style={{ marginBottom: "0.4rem" }}>
      <Kicker>{kicker}</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>{title}</h1>
      {blurb && (
        <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
          {blurb}
        </p>
      )}
    </header>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

function DataTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows || rows.length === 0) return <p className="dim">No rows yet.</p>;
  const cols = Object.keys(rows[0]);
  return (
    <table>
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c}>{c.replace(/_/g, " ")}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {cols.map((c) => (
              <td key={c} className={typeof r[c] === "number" ? "numeric" : undefined}>
                {fmt(r[c])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** useQuery wrapper with design-system loading/error states (no useEffect). */
function QueryBody<T>({
  qKey,
  qFn,
  children,
}: {
  qKey: string;
  qFn: () => Promise<T>;
  children: (data: T) => ReactNode;
}) {
  const { data, isLoading, error } = useQuery({ queryKey: [qKey], queryFn: qFn, retry: 1 });
  if (isLoading) return <p className="dim">Reading the local service…</p>;
  if (error) {
    return (
      <Callout>
        Couldn't reach the local service. <em>{String((error as Error).message ?? error)}</em>
      </Callout>
    );
  }
  return <>{children(data as T)}</>;
}

// --- wired views -------------------------------------------------------------
// TodayView, ProgressView, ContentPerformanceView, NextRepView imported above.

const SettingsView: FC = () => (
  <>
    <ViewHeader kicker="§14.7 · configuration" title="Settings" blurb="Account, goals, daily reps, data sources, and the Growth Agent." />
    <Hairline />
    <QueryBody
      qKey="settings"
      qFn={api.settings}
      children={(d: { settings?: Record<string, unknown> }) => {
        const rows = Object.entries(d.settings ?? {}).map(([key, value]) => ({
          key,
          value: typeof value === "object" ? JSON.stringify(value) : value,
        }));
        return <DataTable rows={rows} />;
      }}
    />
  </>
);

// --- scaffold for views whose endpoints land in later increments ------------
function scaffold(kicker: string, title: string, blurb: string): FC {
  const Scaffolded: FC = () => (
    <>
      <ViewHeader kicker={kicker} title={title} blurb={blurb} />
      <Hairline />
      <Callout>
        This view's data wiring lands in a later Phase 11.4+ increment.{" "}
        <em>The shell, navigation, and instrument-panel design system are in place.</em>
      </Callout>
    </>
  );
  return Scaffolded;
}

// --- registry (Streamlit page order) ----------------------------------------
export const VIEWS: ViewDef[] = [
  { id: "today", label: "Today", group: "Analytics", Component: TodayView },
  { id: "next-rep", label: "Next Rep", group: "Analytics", Component: NextRepView },
  { id: "progress", label: "Progress", group: "Analytics", Component: ProgressView },
  { id: "content-performance", label: "Content Performance", group: "Analytics", Component: ContentPerformanceView },
  { id: "funnel", label: "Funnel", group: "Analytics", Component: FunnelView },
  { id: "weekly-review", label: "Weekly Review", group: "Analytics", Component: scaffold("§14.6", "Weekly Review", "The same questions every week + Markdown export.") },
  { id: "manual-entry", label: "Manual Entry", group: "Manual", Component: scaffold("§15", "Manual Entry", "Daily snapshot, post/reply logging, corrections.") },
  { id: "settings", label: "Settings", group: "Manual", Component: SettingsView },
  { id: "agent-chat", label: "Agent Chat", group: "Agent", Component: scaffold("§14.8", "Agent Chat", "Streaming chat, visible tool calls, §28.10 confirm modal.") },
  { id: "reply-queue", label: "Reply Target Queue", group: "Agent", Component: scaffold("§29.7", "Reply Target Queue", "Scored candidates, R/E/S/O cluster, recommended action.") },
  { id: "brain-dump", label: "Brain Dump", group: "Agent", Component: scaffold("§14.9", "Brain Dump", "Capture-first specimen → candidate drafts.") },
  { id: "coach", label: "Coach", group: "Agent", Component: scaffold("§14.10", "Coach", "Advice-only, citation-grounded.") },
  { id: "account-researcher", label: "Account Researcher", group: "Agent", Component: scaffold("§28.24", "Account Researcher", "Analyze a target account into the reply queue.") },
  { id: "content-calendar", label: "Content Calendar", group: "Growth", Component: scaffold("§14.11", "Content Calendar", "Posted + drafted + planned in one grid.") },
  { id: "campaigns", label: "Campaigns", group: "Growth", Component: scaffold("§14.12", "Campaigns", "Dual-stream success criteria, item state machine.") },
  { id: "inspiration", label: "Inspiration Library", group: "Growth", Component: scaffold("§14.13", "Inspiration Library", "Saved posts, seven transform modes, plagiarism guard.") },
  { id: "blogs", label: "Blogs", group: "Growth", Component: scaffold("§14.14", "Blogs", "Long-form pipeline + repurposing.") },
  { id: "blog-editor", label: "Blog Editor", group: "Growth", Component: scaffold("§14.15", "Blog Editor", "3-panel outline / body / agent + version history.") },
];
