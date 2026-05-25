/**
 * Campaigns — faithful port of app/pages/15_Campaigns.py (spec §14.12).
 *
 * Multi-week themed pushes grouped by status (active/planning/completed/abandoned).
 * Each campaign: name, hypothesis, success criteria, items with StatusChip, progress bar.
 * No useEffect — useQuery only.
 */
import { useQuery } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { ProgressBar } from "../components";
import { StatusChip } from "../components/badges";
import { apiFetch } from "../lib/api";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface CampaignItem {
  id: number;
  item_type: string;
  status: string;
  planned_for_date: string | null;
  planned_text: string | null;
}

interface SuccessCriterion {
  stream: string;
  metric: string;
  target: string;
  actual: string | null;
}

interface Campaign {
  id: number;
  name: string;
  theme: string | null;
  hypothesis: string | null;
  start_date: string;
  end_date: string;
  status: string;
  pillar: string | null;
  content_type: string | null;
  items_shipped: number;
  items_total: number;
  percent_shipped: number | null;
  days_until_end: number | null;
  success_criteria: SuccessCriterion[];
  items: CampaignItem[];
  lesson: string | null;
  counterfactual_note: string | null;
  abandon_reason: string | null;
}

interface CampaignsData {
  by_status: Record<string, Campaign[]>;
  summary: Record<string, number>;
}

// Status -> tone mapping for items.
const ITEM_STATUS_TONE: Record<string, "neutral" | "active" | "done" | "warn"> = {
  planned: "neutral",
  drafted: "active",
  shipped: "done",
  skipped: "warn",
};

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const CampaignsView = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => apiFetch<CampaignsData>("/views/campaigns"),
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

  const { by_status, summary } = data;
  const sectionOrder = ["active", "planning", "completed", "abandoned"] as const;

  return (
    <>
      <Kicker>CAMPAIGNS</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Campaigns</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Multi-week themed pushes. Hypothesis + dual-stream success criteria +
        items + retro.
      </p>

      {/* Summary strip */}
      <div className="kicker" style={{ marginTop: "0.4rem" }}>
        {sectionOrder.map((s) => `${s}: ${summary[s] ?? 0}`).join("  ·  ")}
      </div>

      <Hairline />

      {/* Status sections */}
      {sectionOrder.map((status) => {
        const campaigns = by_status[status] ?? [];
        return (
          <div key={status} style={{ marginBottom: "1.5rem" }}>
            <h2 style={{ textTransform: "capitalize" }}>
              {status} ({campaigns.length})
            </h2>
            {campaigns.length === 0 ? (
              <p className="faint">No {status} campaigns.</p>
            ) : (
              campaigns.map((camp) => (
                <CampaignCard key={camp.id} campaign={camp} />
              ))
            )}
          </div>
        );
      })}
    </>
  );
};

// ---------------------------------------------------------------------------
// Campaign card sub-component
// ---------------------------------------------------------------------------
function CampaignCard({ campaign: c }: { campaign: Campaign }) {
  const pctLabel =
    c.percent_shipped != null
      ? `${Math.round(c.percent_shipped * 100)}%`
      : "—";

  return (
    <div
      style={{
        padding: "0.7rem 0.9rem",
        margin: "0.5rem 0",
        background: palette.surface,
        borderLeft: `2px solid ${c.status === "active" ? palette.phosphor : palette.hairline}`,
        borderRadius: "2px",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ color: palette.bone, fontWeight: 600, fontSize: "1rem" }}>
          {c.name}
        </span>
        <span className="numeric" style={{ fontSize: "0.75rem", color: palette.boneDim }}>
          {c.start_date} — {c.end_date}
        </span>
      </div>

      {/* Progress */}
      <div style={{ margin: "0.4rem 0" }}>
        <div className="numeric" style={{ fontSize: "0.8rem", color: palette.boneDim, marginBottom: "0.2rem" }}>
          shipped {c.items_shipped}/{c.items_total} ({pctLabel})
          {c.days_until_end != null && (
            <span>
              {c.days_until_end < 0
                ? ` · ended ${Math.abs(c.days_until_end)} day(s) ago`
                : ` · ${c.days_until_end} day(s) remaining`}
            </span>
          )}
        </div>
        {c.percent_shipped != null && (
          <ProgressBar value={c.percent_shipped} />
        )}
      </div>

      {/* Hypothesis / theme */}
      {c.hypothesis && (
        <p style={{ margin: "0.3rem 0", color: palette.bone, fontSize: "0.88rem", fontStyle: "italic" }}>
          Hypothesis: {c.hypothesis}
        </p>
      )}
      {c.theme && (
        <p style={{ margin: "0.2rem 0", color: palette.boneDim, fontSize: "0.85rem" }}>
          Theme: {c.theme}
        </p>
      )}

      {/* Success criteria */}
      {c.success_criteria.length > 0 && (
        <div style={{ marginTop: "0.4rem" }}>
          <div
            className="kicker"
            style={{ fontSize: "0.65rem", color: palette.boneFaint }}
          >
            SUCCESS CRITERIA
          </div>
          {c.success_criteria.map((sc, i) => (
            <div
              key={i}
              className="numeric"
              style={{ fontSize: "0.78rem", color: palette.boneDim, padding: "0.1rem 0" }}
            >
              [{sc.stream}] {sc.metric}: target {sc.target}
              {sc.actual ? ` · actual ${sc.actual}` : ""}
            </div>
          ))}
        </div>
      )}

      {/* Items */}
      {c.items.length > 0 && (
        <div style={{ marginTop: "0.5rem" }}>
          <div
            className="kicker"
            style={{ fontSize: "0.65rem", color: palette.boneFaint }}
          >
            ITEMS
          </div>
          {c.items.map((it) => (
            <div
              key={it.id}
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: "0.5rem",
                padding: "0.15rem 0",
                borderBottom: `1px solid ${palette.hairline}`,
              }}
            >
              <StatusChip
                label={it.status}
                tone={ITEM_STATUS_TONE[it.status] ?? "neutral"}
              />
              <span className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
                {it.item_type} · {it.planned_for_date ?? "no date"}
              </span>
              {it.planned_text && (
                <span style={{ fontSize: "0.82rem", color: palette.bone }}>
                  {it.planned_text}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Retro / abandon reason */}
      {c.status === "completed" && c.lesson && (
        <div style={{ marginTop: "0.4rem", fontSize: "0.85rem", color: palette.boneDim }}>
          <em>Lesson:</em> {c.lesson}
        </div>
      )}
      {c.status === "completed" && c.counterfactual_note && (
        <div style={{ fontSize: "0.85rem", color: palette.boneDim }}>
          <em>Counterfactual:</em> {c.counterfactual_note}
        </div>
      )}
      {c.status === "abandoned" && c.abandon_reason && (
        <div style={{ marginTop: "0.4rem", fontSize: "0.85rem", color: palette.warnAmber }}>
          <em>Abandon reason:</em> {c.abandon_reason}
        </div>
      )}
    </div>
  );
}
