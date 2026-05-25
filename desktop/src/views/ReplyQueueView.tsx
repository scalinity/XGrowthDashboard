/**
 * Reply Target Queue — faithful port of app/pages/10_Reply_Target_Queue.py (spec §29.7).
 *
 * Scored candidates with R/E/S/O cluster, recommended action badge, engagement footnote.
 * Actions: Skip, Mark posted (mutations that update status server-side).
 * No useEffect — useQuery + useMutation only.
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

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

const SKIP_REASONS = [
  { value: "not_relevant", label: "Not relevant" },
  { value: "too_old", label: "Too old" },
  { value: "already_replied", label: "Already replied" },
  { value: "low_quality", label: "Low quality" },
  { value: "other", label: "Other" },
] as const;

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const ReplyQueueView = () => {
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["reply-queue"],
    queryFn: () => apiFetch<ReplyQueueData>("/views/reply-queue"),
    retry: 1,
  });

  // --- Mutations -----------------------------------------------------------

  const scoreMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ ok: boolean; scored_count: number }>("/agent/score-candidates", {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reply-queue"] });
    },
  });

  const skipMutation = useMutation({
    mutationFn: ({ id, skip_reason }: { id: number; skip_reason: string }) =>
      apiFetch<{ ok: boolean; status: string }>(`/reply-targets/${id}/skip`, {
        method: "PUT",
        body: JSON.stringify({ skip_reason }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reply-queue"] });
    },
  });

  const markPostedMutation = useMutation({
    mutationFn: ({ id, posted_url }: { id: number; posted_url?: string }) =>
      apiFetch<{ ok: boolean; status: string }>(`/reply-targets/${id}/mark-posted`, {
        method: "PUT",
        body: JSON.stringify({ posted_url: posted_url || undefined }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reply-queue"] });
    },
  });

  // --- Local UI state (no effects — driven by handlers only) ---------------

  const [skipOpen, setSkipOpen] = useState<number | null>(null);
  const [skipReason, setSkipReason] = useState("not_relevant");
  const [markPostedOpen, setMarkPostedOpen] = useState<number | null>(null);
  const [postedUrl, setPostedUrl] = useState("");

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
      <Kicker>REPLY TARGET QUEUE</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Reply target queue</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Candidates to reply under, scored on four dimensions.
        The recommended action is deterministic from the scores. Sort order is
        reply_now, reply_if_time, consider, skip — then by recency.
      </p>

      {/* Score Candidates action */}
      <div style={{ margin: "0.5rem 0 0.7rem" }}>
        <button
          onClick={() => scoreMutation.mutate()}
          disabled={scoreMutation.isPending}
          style={{
            padding: "0.45rem 1rem",
            background: palette.phosphor,
            color: palette.ink,
            border: "none",
            borderRadius: "3px",
            fontFamily: fonts.body,
            fontWeight: 600,
            fontSize: "0.85rem",
            cursor: scoreMutation.isPending ? "wait" : "pointer",
            opacity: scoreMutation.isPending ? 0.6 : 1,
          }}
        >
          {scoreMutation.isPending ? "Scoring..." : "Score candidates"}
        </button>
        {scoreMutation.isSuccess && (
          <span
            style={{
              marginLeft: "0.6rem",
              color: palette.phosphor,
              fontSize: "0.82rem",
            }}
          >
            Scored {scoreMutation.data.scored_count} candidates
          </span>
        )}
        {scoreMutation.isError && (
          <span
            style={{
              marginLeft: "0.6rem",
              color: palette.warnAmber,
              fontSize: "0.82rem",
            }}
          >
            {String((scoreMutation.error as Error).message ?? "Scoring failed")}
          </span>
        )}
      </div>

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
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.3rem", flexWrap: "wrap", alignItems: "center" }}>
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

                {/* Skip button + dropdown */}
                {skipOpen === item.id ? (
                  <span style={{ display: "inline-flex", gap: "0.3rem", alignItems: "center" }}>
                    <select
                      value={skipReason}
                      onChange={(e) => setSkipReason(e.target.value)}
                      style={{
                        padding: "0.25rem 0.4rem",
                        background: palette.surfaceRaised,
                        color: palette.bone,
                        border: `1px solid ${palette.hairline}`,
                        borderRadius: "2px",
                        fontSize: "0.78rem",
                        fontFamily: fonts.body,
                      }}
                    >
                      {SKIP_REASONS.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={() => {
                        skipMutation.mutate({ id: item.id, skip_reason: skipReason });
                        setSkipOpen(null);
                      }}
                      disabled={skipMutation.isPending}
                      style={{
                        padding: "0.25rem 0.5rem",
                        background: palette.warnAmber,
                        color: palette.ink,
                        border: "none",
                        borderRadius: "2px",
                        fontSize: "0.78rem",
                        fontFamily: fonts.body,
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => setSkipOpen(null)}
                      style={{
                        padding: "0.25rem 0.5rem",
                        background: "transparent",
                        color: palette.boneDim,
                        border: `1px solid ${palette.hairline}`,
                        borderRadius: "2px",
                        fontSize: "0.78rem",
                        fontFamily: fonts.body,
                        cursor: "pointer",
                      }}
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => {
                      setSkipOpen(item.id);
                      setMarkPostedOpen(null);
                    }}
                    style={{
                      padding: "0.3rem 0.7rem",
                      background: palette.surfaceRaised,
                      color: palette.boneDim,
                      border: `1px solid ${palette.hairline}`,
                      borderRadius: "2px",
                      fontSize: "0.8rem",
                      fontFamily: fonts.body,
                      cursor: "pointer",
                    }}
                  >
                    Skip
                  </button>
                )}

                {/* Mark posted button + URL input */}
                {markPostedOpen === item.id ? (
                  <span style={{ display: "inline-flex", gap: "0.3rem", alignItems: "center" }}>
                    <input
                      type="text"
                      placeholder="Posted URL (optional)"
                      value={postedUrl}
                      onChange={(e) => setPostedUrl(e.target.value)}
                      style={{
                        padding: "0.25rem 0.4rem",
                        background: palette.surfaceRaised,
                        color: palette.bone,
                        border: `1px solid ${palette.hairline}`,
                        borderRadius: "2px",
                        fontSize: "0.78rem",
                        fontFamily: fonts.body,
                        width: "14rem",
                      }}
                    />
                    <button
                      onClick={() => {
                        markPostedMutation.mutate({
                          id: item.id,
                          posted_url: postedUrl || undefined,
                        });
                        setMarkPostedOpen(null);
                        setPostedUrl("");
                      }}
                      disabled={markPostedMutation.isPending}
                      style={{
                        padding: "0.25rem 0.5rem",
                        background: palette.phosphor,
                        color: palette.ink,
                        border: "none",
                        borderRadius: "2px",
                        fontSize: "0.78rem",
                        fontFamily: fonts.body,
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      Confirm
                    </button>
                    <button
                      onClick={() => {
                        setMarkPostedOpen(null);
                        setPostedUrl("");
                      }}
                      style={{
                        padding: "0.25rem 0.5rem",
                        background: "transparent",
                        color: palette.boneDim,
                        border: `1px solid ${palette.hairline}`,
                        borderRadius: "2px",
                        fontSize: "0.78rem",
                        fontFamily: fonts.body,
                        cursor: "pointer",
                      }}
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => {
                      setMarkPostedOpen(item.id);
                      setSkipOpen(null);
                    }}
                    style={{
                      padding: "0.3rem 0.7rem",
                      background: palette.surfaceRaised,
                      color: palette.phosphor,
                      border: `1px solid ${palette.phosphorDim}`,
                      borderRadius: "2px",
                      fontSize: "0.8rem",
                      fontFamily: fonts.body,
                      cursor: "pointer",
                    }}
                  >
                    Mark posted
                  </button>
                )}
              </div>

              {/* Inline mutation feedback */}
              {skipMutation.isError && (
                <div style={{ color: palette.warnAmber, fontSize: "0.78rem", marginTop: "0.2rem" }}>
                  Skip failed: {String((skipMutation.error as Error).message ?? "unknown error")}
                </div>
              )}
              {markPostedMutation.isError && (
                <div style={{ color: palette.warnAmber, fontSize: "0.78rem", marginTop: "0.2rem" }}>
                  Mark-posted failed: {String((markPostedMutation.error as Error).message ?? "unknown error")}
                </div>
              )}

              <Hairline />
            </div>
          );
        })
      )}
    </>
  );
};
