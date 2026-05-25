/**
 * Weekly Review — faithful port of app/pages/6_Weekly_Review.py (spec §14.6).
 *
 * Layout mirrors the Streamlit page top-to-bottom:
 *   1. Header (kicker + title + caption)
 *   2. Summary metric tiles (auto-filled from server)
 *   3. Strongest-pillar callout (conditional)
 *   4. Review form (text areas + confidence select + submit)
 *   5. Export section (gated on counterfactual)
 *   6. Past reviews (collapsible list)
 *   7. Agent buttons (counterfactual, interpretation, experiment)
 *
 * Data: single useQuery to GET /views/weekly-review (all reads server-side §31.10).
 * Writes: useMutation to POST /forms/weekly-review.
 * No useEffect — per the project React rules.
 */
import type { FC } from "react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker, MetricRow, MetricTile } from "../components";
import { apiFetch } from "../lib/api";
import { useNav } from "../lib/nav";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types (mirrors the /views/weekly-review response shape)
// ---------------------------------------------------------------------------
interface WeeklyReviewSummary {
  follower_delta: number | null;
  posts_shipped: number;
  replies_shipped: number;
  reply_sessions_completed: number;
  daily_reps_days_completed: number;
  downloads: number;
  qualified_icp_testers: number;
  strongest_pillar_candidate: string | null;
}

interface ExistingReview {
  id: number;
  week_start_date: string;
  week_end_date: string;
  followers_start: number | null;
  followers_end: number | null;
  follower_delta: number | null;
  posts_shipped: number;
  replies_shipped: number;
  reply_sessions_completed: number;
  daily_reps_days_completed: number;
  downloads: number;
  qualified_icp_testers: number;
  what_moved: string | null;
  what_got_stuck: string | null;
  lesson: string | null;
  next_week_experiment: string | null;
  counterfactual_note: string | null;
  strongest_pillar: string | null;
  weakest_pillar: string | null;
}

interface PastReview {
  id: number;
  week_start_date: string;
  week_end_date: string;
  follower_delta: number | null;
  posts_shipped: number;
  replies_shipped: number;
  downloads: number;
  counterfactual_note: string | null;
  lesson: string | null;
}

interface WeeklyReviewData {
  slice: string;
  week_start: string;
  week_end: string;
  summary: WeeklyReviewSummary;
  existing_review: ExistingReview | null;
  counterfactual_required: boolean;
  past_reviews: PastReview[];
}

// ---------------------------------------------------------------------------
// Form sub-component (keyed by review ID to reset when week changes)
// ---------------------------------------------------------------------------
function ReviewForm({
  existing,
  weekStart,
  weekEnd,
  counterfactualRequired,
}: {
  existing: ExistingReview | null;
  weekStart: string;
  weekEnd: string;
  counterfactualRequired: boolean;
}) {
  const queryClient = useQueryClient();

  const [lesson, setLesson] = useState(existing?.lesson ?? "");
  const [whatWorked, setWhatWorked] = useState(existing?.what_moved ?? "");
  const [whatDidnt, setWhatDidnt] = useState(existing?.what_got_stuck ?? "");
  const [experiment, setExperiment] = useState(existing?.next_week_experiment ?? "");
  const [counterfactualNote, setCounterfactualNote] = useState(
    existing?.counterfactual_note ?? "",
  );
  const [confidenceLabel, setConfidenceLabel] = useState<string>("inference");
  const [danielNotes, setDanielNotes] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<{ review_id: number }>("/forms/weekly-review", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["weekly-review"] });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate({
      week_start_date: weekStart,
      week_end_date: weekEnd,
      lesson: lesson || null,
      what_moved: whatWorked || null,
      what_got_stuck: whatDidnt || null,
      next_week_experiment: experiment || null,
      counterfactual_note: counterfactualNote.trim() || null,
      confidence_label: confidenceLabel,
      daniel_notes: danielNotes || null,
      // Auto-filled metrics pass through from summary (server already has them).
      followers_start: existing?.followers_start ?? null,
      followers_end: existing?.followers_end ?? null,
      posts_shipped: existing?.posts_shipped ?? 0,
      replies_shipped: existing?.replies_shipped ?? 0,
      reply_sessions_completed: existing?.reply_sessions_completed ?? 0,
      daily_reps_days_completed: existing?.daily_reps_days_completed ?? 0,
      downloads: existing?.downloads ?? 0,
      qualified_icp_testers: existing?.qualified_icp_testers ?? 0,
    });
  };

  return (
    <form onSubmit={handleSubmit}>
      <h2 style={{ fontSize: "1.4rem", marginBottom: "0.8rem" }}>Write the review</h2>

      <FieldLabel label="One-sentence lesson" />
      <textarea
        value={lesson}
        onChange={(e) => setLesson(e.target.value)}
        rows={3}
        style={textAreaStyle}
      />

      <FieldLabel label="What worked this week?" />
      <textarea
        value={whatWorked}
        onChange={(e) => setWhatWorked(e.target.value)}
        rows={3}
        style={textAreaStyle}
      />

      <FieldLabel label="What didn't work?" />
      <textarea
        value={whatDidnt}
        onChange={(e) => setWhatDidnt(e.target.value)}
        rows={3}
        style={textAreaStyle}
      />

      <FieldLabel label="Next week's experiment" />
      <textarea
        value={experiment}
        onChange={(e) => setExperiment(e.target.value)}
        rows={3}
        style={textAreaStyle}
      />

      <FieldLabel
        label={`Counterfactual note${counterfactualRequired ? " *" : ""}`}
        help="What couldn't this tool measure this week? (Required for export)"
      />
      <textarea
        value={counterfactualNote}
        onChange={(e) => setCounterfactualNote(e.target.value)}
        rows={4}
        style={textAreaStyle}
      />

      <FieldLabel label="Confidence label" />
      <select
        value={confidenceLabel}
        onChange={(e) => setConfidenceLabel(e.target.value)}
        style={selectStyle}
      >
        <option value="fact">fact</option>
        <option value="inference">inference</option>
        <option value="speculation">speculation</option>
        <option value="mixed">mixed</option>
      </select>

      <FieldLabel label="Daniel's notes" />
      <textarea
        value={danielNotes}
        onChange={(e) => setDanielNotes(e.target.value)}
        rows={2}
        style={textAreaStyle}
      />

      <div style={{ marginTop: "1rem" }}>
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? "Saving..." : "Save weekly review"}
        </button>
        {mutation.isSuccess && (
          <span style={{ marginLeft: "0.8rem", color: palette.phosphor, fontSize: "0.85rem" }}>
            Saved.
          </span>
        )}
        {mutation.isError && (
          <span style={{ marginLeft: "0.8rem", color: palette.warnAmber, fontSize: "0.85rem" }}>
            {(mutation.error as Error).message}
          </span>
        )}
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
function FieldLabel({ label, help }: { label: string; help?: string }) {
  return (
    <label
      style={{
        display: "block",
        fontFamily: "var(--font-body)",
        fontSize: "0.82rem",
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        color: palette.boneDim,
        marginTop: "0.8rem",
        marginBottom: "0.25rem",
      }}
    >
      {label}
      {help && (
        <span style={{ textTransform: "none", fontSize: "0.78rem", marginLeft: "0.5rem", color: palette.boneFaint }}>
          {help}
        </span>
      )}
    </label>
  );
}

const textAreaStyle: React.CSSProperties = {
  width: "100%",
  background: palette.surface,
  border: `1px solid ${palette.hairline}`,
  borderRadius: "4px",
  color: palette.bone,
  fontFamily: "var(--font-body)",
  fontSize: "0.92rem",
  padding: "0.5rem 0.6rem",
  resize: "vertical",
};

const selectStyle: React.CSSProperties = {
  background: palette.surface,
  border: `1px solid ${palette.hairline}`,
  borderRadius: "4px",
  color: palette.bone,
  fontFamily: "var(--font-mono)",
  fontSize: "0.88rem",
  padding: "0.4rem 0.6rem",
};

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------
export const WeeklyReviewView: FC = () => {
  const nav = useNav();
  const { data, isLoading, error } = useQuery({
    queryKey: ["weekly-review"],
    queryFn: () => apiFetch<WeeklyReviewData>("/views/weekly-review"),
    retry: 1,
  });

  if (isLoading) return <p className="dim">Reading the local service...</p>;
  if (error) {
    return (
      <Callout>
        Couldn't reach the local service. <em>{String((error as Error).message ?? error)}</em>
      </Callout>
    );
  }
  if (!data) return null;

  const { week_start, week_end, summary, existing_review, counterfactual_required, past_reviews } = data;

  const weekStartDisplay = formatDateLabel(week_start);
  const weekEndDisplay = formatDateLabel(week_end);

  const hasCounterfactual = Boolean(
    existing_review?.counterfactual_note?.trim(),
  );
  const exportDisabled = counterfactual_required && !hasCounterfactual;

  return (
    <>
      {/* 1. Header */}
      <header style={{ marginBottom: "0.4rem" }}>
        <Kicker>{`WEEK OF ${weekStartDisplay} – ${weekEndDisplay}`}</Kicker>
        <h1 style={{ fontSize: "2.1rem" }}>Weekly review</h1>
        <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
          Turn raw activity into learning. Auto-filled numbers sit above the form as
          prompts — Daniel writes the interpretation.
        </p>
      </header>

      <Hairline />

      {/* 2. Summary metrics */}
      <h2 style={{ fontSize: "1.3rem", marginBottom: "0.6rem" }}>This week — at a glance</h2>
      <MetricRow>
        <MetricTile
          label="Followers Δ"
          value={summary.follower_delta != null ? `${summary.follower_delta > 0 ? "+" : ""}${summary.follower_delta}` : "—"}
        />
        <MetricTile label="Posts shipped" value={String(summary.posts_shipped)} />
        <MetricTile label="Replies shipped" value={String(summary.replies_shipped)} />
        <MetricTile label="Stir downloads" value={String(summary.downloads)} />
      </MetricRow>
      <MetricRow>
        <MetricTile label="Reply sessions" value={String(summary.reply_sessions_completed)} />
        <MetricTile label="Rep-complete days" value={`${summary.daily_reps_days_completed} / 7`} />
        <MetricTile label="Qualified ICP testers" value={String(summary.qualified_icp_testers)} />
      </MetricRow>

      {/* 3. Strongest-pillar callout */}
      {summary.strongest_pillar_candidate ? (
        <Callout>
          <em>Strongest-pillar candidate (provisional):</em> {summary.strongest_pillar_candidate}.
          This is a prompt, not a conclusion — Daniel writes the interpretation.
        </Callout>
      ) : (
        <Callout>
          <em>No strongest-pillar candidate this week.</em> No lane has reached{" "}
          <strong>tentative</strong> confidence yet.
        </Callout>
      )}

      <Hairline />

      {/* 4. Review form — keyed by existing review ID to reset state on week change */}
      <div key={existing_review?.id ?? "new"}>
        <ReviewForm
          existing={existing_review}
          weekStart={week_start}
          weekEnd={week_end}
          counterfactualRequired={counterfactual_required}
        />
      </div>

      <Hairline />

      {/* 5. Export section */}
      <h2 style={{ fontSize: "1.3rem", marginBottom: "0.6rem" }}>Export</h2>
      <button
        className="btn-secondary"
        disabled={exportDisabled}
        title={
          exportDisabled
            ? "Counterfactual note required (§14.6). Fill the form above and save first."
            : "Export weekly report"
        }
      >
        Export weekly report (Markdown)
      </button>
      {exportDisabled && (
        <p className="dim" style={{ fontSize: "0.82rem", marginTop: "0.4rem" }}>
          Counterfactual note required. Fill the form above and save first.
        </p>
      )}

      <Hairline />

      {/* 6. Past reviews */}
      <h2 style={{ fontSize: "1.3rem", marginBottom: "0.6rem" }}>Past reviews</h2>
      {past_reviews.length === 0 ? (
        <p className="dim">No saved reviews yet.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
          {past_reviews.map((r) => (
            <details key={r.id} style={{ background: palette.surface, border: `1px solid ${palette.hairline}`, borderRadius: "4px", padding: "0.6rem 0.8rem" }}>
              <summary style={{ cursor: "pointer", fontFamily: "var(--font-mono)", fontSize: "0.88rem", color: palette.bone }}>
                Week of {r.week_start_date} — {"Δ"}followers{" "}
                {r.follower_delta != null ? `${r.follower_delta > 0 ? "+" : ""}${r.follower_delta}` : "—"},{" "}
                posts {r.posts_shipped}, replies {r.replies_shipped}, downloads {r.downloads}
              </summary>
              <div style={{ marginTop: "0.5rem", fontSize: "0.88rem", color: palette.boneDim }}>
                <p style={{ margin: "0.2rem 0" }}>
                  <strong>Period:</strong> {r.week_start_date} → {r.week_end_date}
                </p>
                {r.lesson && (
                  <p style={{ margin: "0.2rem 0" }}>
                    <strong>Lesson:</strong> {r.lesson}
                  </p>
                )}
                {r.counterfactual_note ? (
                  <Callout>
                    <em>Counterfactual:</em> {r.counterfactual_note}
                  </Callout>
                ) : (
                  <p className="dim" style={{ fontSize: "0.82rem" }}>
                    No counterfactual recorded.
                  </p>
                )}
              </div>
            </details>
          ))}
        </div>
      )}

      <Hairline />

      {/* 7. Agent buttons */}
      <h3 style={{ fontSize: "1.1rem", marginBottom: "0.6rem" }}>Ask the agent</h3>
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <button
          className="btn-secondary"
          onClick={() => nav("agent-chat")}
        >
          draft counterfactual →
        </button>
        <button
          className="btn-secondary"
          onClick={() => nav("agent-chat")}
        >
          draft interpretation →
        </button>
        <button
          className="btn-secondary"
          onClick={() => nav("agent-chat")}
        >
          suggest next experiment →
        </button>
      </div>
    </>
  );
};

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
function formatDateLabel(isoDate: string): string {
  const d = new Date(isoDate + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }).toUpperCase();
}
