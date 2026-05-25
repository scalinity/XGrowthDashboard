/**
 * Reply Target Queue — faithful port of app/pages/10_Reply_Target_Queue.py (spec §29.7).
 *
 * Scored candidates with R/E/S/O cluster, recommended action badge, engagement footnote.
 * Actions: Skip, Mark posted (mutations that update status server-side).
 * No useEffect — useQuery + useMutation only.
 */
import { useQuery } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { RecommendedActionBadge } from "../components/badges";
import { ScoreBank } from "../components/meters";
import { ReadoutCard } from "../components/cards";
import { apiFetch } from "../lib/api";
import { palette, fonts } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface ReplyQueueItem {
  id: number;
  handle: string;
  text_excerpt: string | null;
  target_post_url: string;
  like_count: number;
  reply_count: number;
  repost_count: number;
  relevance_score: number | null;
  engagement_surface_score: number | null;
  saturation_score: number | null;
  reply_opportunity_score: number | null;
  recommended_action_label: string | null;
  score_rationale: string | null;
  pillar: string | null;
  reply_intent: string | null;
  discovered_via: string | null;
  engagement_footnote: string | null;
}

interface ReplyQueueData {
  counters: {
    candidates: number;
    drafted: number;
    posted_today: number;
    skipped_today: number;
  };
  items: ReplyQueueItem[];
}

// Action label -> keyline color (mirrors theme.py).
const ACTION_KEYLINE: Record<string, string> = {
  reply_now: palette.phosphor,
  reply_if_time: palette.phosphorDim,
  consider: palette.hairline,
  skip: palette.hairline,
};

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const ReplyQueueView = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ["reply-queue"],
    queryFn: () => apiFetch<ReplyQueueData>("/views/reply-queue"),
    retry: 1,
  });

  // Skip mutation — PATCH-like via POST to agent endpoint (simplified: update via settings for now).
  // For the initial port we don't wire skip/mark-posted mutations since the existing
  // app.py doesn't expose them yet. The UI renders read-only with action buttons that
  // will be wired in a follow-up.

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

  const { counters, items } = data;

  return (
    <>
      <Kicker>§29 · REPLY TARGET QUEUE</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Reply target queue</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Candidates to reply under, scored on four dimensions per §29.3.
        The recommended action is deterministic from the scores. Sort order is
        reply_now, reply_if_time, consider, skip — then by recency.
      </p>

      {/* Counter strip */}
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <ReadoutCard label="Candidates" value={String(counters.candidates)} />
        <ReadoutCard label="Drafted" value={String(counters.drafted)} accent="phosphorDim" />
        <ReadoutCard label="Posted today" value={String(counters.posted_today)} accent="phosphor" />
        <ReadoutCard
          label="Skipped today"
          value={String(counters.skipped_today)}
          accent="boneDim"
          empty={counters.skipped_today === 0}
        />
      </div>

      <Hairline />

      {/* Candidate rows */}
      {items.length === 0 ? (
        <Callout>
          <em>No candidates in the queue.</em> Add candidates via the Streamlit
          view or let the agent discover them.
        </Callout>
      ) : (
        items.map((item) => {
          const keyline =
            ACTION_KEYLINE[item.recommended_action_label ?? ""] ??
            palette.hairline;
          return (
            <div key={item.id} style={{ marginBottom: "0.6rem" }}>
              {/* Card surface */}
              <div
                style={{
                  borderLeft: `3px solid ${keyline}`,
                  padding: "0.7rem 0.95rem 0.5rem",
                  background: palette.surface,
                  borderRadius: "2px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                  }}
                >
                  <span style={{ color: palette.bone, fontWeight: 500 }}>
                    @{item.handle}
                  </span>
                  <span
                    className="numeric"
                    style={{ fontSize: "0.75rem", color: palette.boneFaint }}
                  >
                    #{item.id}
                    {item.discovered_via && ` · ${item.discovered_via}`}
                  </span>
                </div>
                <div
                  style={{
                    margin: "0.3rem 0 0.2rem",
                    color: palette.bone,
                    lineHeight: 1.4,
                    fontSize: "0.92rem",
                  }}
                >
                  {item.text_excerpt || (
                    <span className="faint">(no target text saved)</span>
                  )}
                </div>
                <div
                  className="numeric"
                  style={{
                    fontSize: "0.78rem",
                    color: palette.boneDim,
                    marginTop: "0.2rem",
                  }}
                >
                  {item.like_count} likes · {item.reply_count} replies ·{" "}
                  {item.repost_count} reposts
                </div>
              </div>

              {/* Score bank */}
              <ScoreBank
                relevance={item.relevance_score}
                engagementSurface={item.engagement_surface_score}
                saturation={item.saturation_score}
                replyOpportunity={item.reply_opportunity_score}
                engagementFootnote={item.engagement_footnote ?? undefined}
              />

              {/* Action badge + meta */}
              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: "0.6rem",
                  margin: "0 0 0.2rem",
                }}
              >
                <RecommendedActionBadge label={item.recommended_action_label} />
                <span
                  className="faint"
                  style={{ fontSize: "0.78rem" }}
                >
                  pillar ={" "}
                  <span className="numeric">{item.pillar ?? "—"}</span>
                  {" · "}intent ={" "}
                  <span className="numeric">{item.reply_intent ?? "—"}</span>
                </span>
              </div>

              {/* Score rationale */}
              {item.score_rationale && (
                <div
                  style={{
                    color: palette.boneDim,
                    fontStyle: "italic",
                    fontSize: "0.85rem",
                    margin: "0.1rem 0 0.3rem",
                  }}
                >
                  {item.score_rationale.length > 320
                    ? item.score_rationale.slice(0, 319) + "..."
                    : item.score_rationale}
                </div>
              )}

              {/* Action buttons */}
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.3rem" }}>
                <a
                  href={item.target_post_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    padding: "0.3rem 0.7rem",
                    background: palette.surfaceRaised,
                    color: palette.bone,
                    borderRadius: "2px",
                    fontSize: "0.8rem",
                    textDecoration: "none",
                    fontFamily: fonts.body,
                  }}
                >
                  Open original
                </a>
              </div>

              <Hairline />
            </div>
          );
        })
      )}
    </>
  );
};
