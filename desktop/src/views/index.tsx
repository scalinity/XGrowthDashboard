/**
 * View registry (spec §31.7). The 18 views, grouped to mirror the
 * Streamlit page order. Each ported view lives in its own file under
 * src/views/; all 18 views are now wired to their implementations.
 */
import type { FC } from "react";

import { TodayView } from "./TodayView";
import { ProgressView } from "./ProgressView";
import { ContentPerformanceView } from "./ContentPerformanceView";
import { NextRepView } from "./NextRepView";
import { FunnelView } from "./FunnelView";
import { WeeklyReviewView } from "./WeeklyReviewView";
import { ManualEntryView } from "./ManualEntryView";
import { SettingsView } from "./SettingsView";
import { AgentChatView } from "./AgentChatView";
import { ReplyQueueView } from "./ReplyQueueView";
import { BrainDumpView } from "./BrainDumpView";
import { CoachView } from "./CoachView";
import { AccountResearcherView } from "./AccountResearcherView";
import { ContentCalendarView } from "./ContentCalendarView";
import { CampaignsView } from "./CampaignsView";
import { InspirationView } from "./InspirationView";
import { BlogsView } from "./BlogsView";
import { BlogEditorView } from "./BlogEditorView";

export interface ViewDef {
  id: string;
  label: string;
  group: "Analytics" | "Manual" | "Agent" | "Growth";
  Component: FC;
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
  { id: "agent-chat", label: "Agent Chat", group: "Agent", Component: AgentChatView },
  { id: "reply-queue", label: "Reply Target Queue", group: "Agent", Component: ReplyQueueView },
  { id: "brain-dump", label: "Brain Dump", group: "Agent", Component: BrainDumpView },
  { id: "coach", label: "Coach", group: "Agent", Component: CoachView },
  { id: "account-researcher", label: "Account Researcher", group: "Agent", Component: AccountResearcherView },
  { id: "content-calendar", label: "Content Calendar", group: "Growth", Component: ContentCalendarView },
  { id: "campaigns", label: "Campaigns", group: "Growth", Component: CampaignsView },
  { id: "inspiration", label: "Inspiration Library", group: "Growth", Component: InspirationView },
  { id: "blogs", label: "Blogs", group: "Growth", Component: BlogsView },
  { id: "blog-editor", label: "Blog Editor", group: "Growth", Component: BlogEditorView },
];
