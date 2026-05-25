/**
 * Content Calendar — faithful port of app/pages/14_Content_Calendar.py (spec §14.11).
 *
 * Visual planning grid: POSTED + DRAFTED + PLANNED across a 4-week window
 * (2 weeks back + 2 weeks forward). Grouped by date, AM/PM slots.
 * No useEffect — useQuery only.
 */
import { useQuery } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { StatusChip } from "../components/badges";
import { apiFetch } from "../lib/api";
import { palette, fonts } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface CalendarCell {
  provenance: "posted" | "drafted_for_future" | "agent_drafted" | "planned";
  source_id: number;
  slot: "am" | "pm";
  pillar: string | null;
  content_type: string | null;
  title: string;
  campaign_id: number | null;
}

interface ActiveCampaign {
  name: string;
  start_date: string;
  end_date: string;
  items_shipped: number;
  items_planned: number;
}

interface ContentCalendarData {
  window_start: string;
  window_end: string;
  by_date: Record<string, CalendarCell[]>;
  active_campaigns: ActiveCampaign[];
}

// Provenance -> display chip.
const PROVENANCE_CHIP: Record<string, { label: string; tone: "done" | "active" | "neutral" }> = {
  posted: { label: "POSTED", tone: "done" },
  drafted_for_future: { label: "DRAFTED", tone: "active" },
  agent_drafted: { label: "DRAFTED", tone: "active" },
  planned: { label: "PLANNED", tone: "neutral" },
};

function formatDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function generateDateRange(start: string, end: string): string[] {
  const dates: string[] = [];
  const current = new Date(start + "T12:00:00");
  const endDate = new Date(end + "T12:00:00");
  while (current <= endDate) {
    dates.push(current.toISOString().slice(0, 10));
    current.setDate(current.getDate() + 1);
  }
  return dates;
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const ContentCalendarView = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ["content-calendar"],
    queryFn: () => apiFetch<ContentCalendarData>("/views/content-calendar"),
    retry: 1,
  });

  if (isLoading) return <p className="dim">Reading the local service...</p>;
  if (error) {
    return (
      <Callout>
        Couldn't reach the local service.{" "}
        <em>{String((error as Error).message ?? error)}</em>
      </Callout>
    );
  }
  if (!data) return null;

  const { window_start, window_end, by_date, active_campaigns } = data;
  const dates = generateDateRange(window_start, window_end);
  const today = new Date().toISOString().slice(0, 10);

  return (
    <>
      <Kicker>CONTENT CALENDAR</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Content Calendar</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Visual planning grid — POSTED + DRAFTED + PLANNED across the window.
        This calendar shows schedules; it does not publish.
      </p>

      <div className="kicker" style={{ marginTop: "0.6rem" }}>
        WINDOW: {window_start} — {window_end}
      </div>

      <Hairline />

      {/* Calendar grid — one row per day */}
      <div style={{ marginTop: "0.5rem" }}>
        {dates.map((dateIso) => {
          const cells = by_date[dateIso] ?? [];
          const amCells = cells.filter((c) => c.slot === "am");
          const pmCells = cells.filter((c) => c.slot === "pm");
          const isToday = dateIso === today;

          return (
            <div
              key={dateIso}
              style={{
                padding: "0.5rem 0.7rem",
                marginBottom: "0.3rem",
                background: isToday ? palette.surfaceRaised : "transparent",
                borderLeft: isToday ? `2px solid ${palette.phosphor}` : `2px solid transparent`,
                borderRadius: "2px",
              }}
            >
              <div
                className="kicker"
                style={{ color: isToday ? palette.phosphor : palette.boneDim }}
              >
                {formatDate(dateIso)}
                {isToday && " · TODAY"}
              </div>
              <div style={{ display: "flex", gap: "2rem", marginTop: "0.2rem" }}>
                {/* AM */}
                <div style={{ flex: 1 }}>
                  <span
                    style={{
                      fontFamily: fonts.mono,
                      fontSize: "0.68rem",
                      color: palette.boneFaint,
                      letterSpacing: "0.08em",
                    }}
                  >
                    AM
                  </span>
                  {amCells.length === 0 ? (
                    <div className="faint" style={{ fontSize: "0.82rem" }}>—</div>
                  ) : (
                    amCells.map((cell, i) => {
                      const chip = PROVENANCE_CHIP[cell.provenance] ?? PROVENANCE_CHIP.planned;
                      return (
                        <div
                          key={`${cell.source_id}-${i}`}
                          style={{ fontSize: "0.85rem", color: palette.bone, marginTop: "0.1rem" }}
                        >
                          <StatusChip label={chip.label} tone={chip.tone} />{" "}
                          <span className="numeric" style={{ color: palette.boneDim, fontSize: "0.75rem" }}>
                            {cell.pillar ?? "—"} · {cell.content_type ?? "—"}
                          </span>{" "}
                          {cell.title}
                        </div>
                      );
                    })
                  )}
                </div>
                {/* PM */}
                <div style={{ flex: 1 }}>
                  <span
                    style={{
                      fontFamily: fonts.mono,
                      fontSize: "0.68rem",
                      color: palette.boneFaint,
                      letterSpacing: "0.08em",
                    }}
                  >
                    PM
                  </span>
                  {pmCells.length === 0 ? (
                    <div className="faint" style={{ fontSize: "0.82rem" }}>—</div>
                  ) : (
                    pmCells.map((cell, i) => {
                      const chip = PROVENANCE_CHIP[cell.provenance] ?? PROVENANCE_CHIP.planned;
                      return (
                        <div
                          key={`${cell.source_id}-${i}`}
                          style={{ fontSize: "0.85rem", color: palette.bone, marginTop: "0.1rem" }}
                        >
                          <StatusChip label={chip.label} tone={chip.tone} />{" "}
                          <span className="numeric" style={{ color: palette.boneDim, fontSize: "0.75rem" }}>
                            {cell.pillar ?? "—"} · {cell.content_type ?? "—"}
                          </span>{" "}
                          {cell.title}
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <Hairline />

      {/* Active campaigns strip */}
      <Kicker>Active campaigns in this window</Kicker>
      {active_campaigns.length === 0 ? (
        <p className="faint">No active campaigns overlap this window.</p>
      ) : (
        active_campaigns.map((ac, i) => (
          <div
            key={i}
            style={{
              padding: "0.3rem 0",
              color: palette.bone,
              fontSize: "0.88rem",
              borderBottom: `1px solid ${palette.hairline}`,
            }}
          >
            <strong>{ac.name}</strong>{" "}
            <span className="numeric" style={{ color: palette.boneDim }}>
              ({ac.start_date} — {ac.end_date}) · shipped {ac.items_shipped} · planned{" "}
              {ac.items_planned}
            </span>
          </div>
        ))
      )}
    </>
  );
};
