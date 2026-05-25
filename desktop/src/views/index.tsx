/**
 * View registry (spec §31.7). The 18 views, grouped to mirror the
 * Streamlit page order. Each ported view lives in its own file under
 * src/views/; scaffolds remain for views whose endpoints aren't wired yet.
 */
import type { FC } from "react";

import { Callout, Hairline, Kicker } from "../components";
import { TodayView } from "./TodayView";
import { ProgressView } from "./ProgressView";
import { ContentPerformanceView } from "./ContentPerformanceView";
import { NextRepView } from "./NextRepView";
import { FunnelView } from "./FunnelView";
import { WeeklyReviewView } from "./WeeklyReviewView";
import { ManualEntryView } from "./ManualEntryView";
import { SettingsView } from "./SettingsView";

export interface ViewDef {
  id: string;
  label: string;
  group: "Analytics" | "Manual" | "Agent" | "Growth";
  Component: FC;
}

// --- scaffold for views whose endpoints land in later increments ------------
function scaffold(kicker: string, title: string, blurb: string): FC {
  const Scaffolded: FC = () => (
    <>
      <header style={{ marginBottom: "0.4rem" }}>
        <Kicker>{kicker}</Kicker>
        <h1 style={{ fontSize: "2.1rem" }}>{title}</h1>
        <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
          {blurb}
        </p>
      </header>
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
  { id: "weekly-review", label: "Weekly Review", group: "Analytics", Component: WeeklyReviewView },
  { id: "manual-entry", label: "Manual Entry", group: "Manual", Component: ManualEntryView },
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
