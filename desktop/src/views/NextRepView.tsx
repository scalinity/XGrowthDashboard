/**
 * Next Rep — faithful port of app/pages/2_Next_Rep.py (spec §14.2).
 *
 * Sections: lane coverage scoreboard, open hypotheses, reply target candidates
 * (ScoreBank + RecommendedActionBadge), account leads, pending drafts, agent buttons.
 * No useEffect.
 */
import { useQuery } from "@tanstack/react-query";

import {
  Callout,
  Hairline,
  Kicker,
  PrepublishChip,
  RepetitionBanner,
} from "../components";
import { RecommendedActionBadge, ConfidenceBadge } from "../components/badges";
import { ScoreBank } from "../components/meters";
import { apiFetch } from "../lib/api";
import { useNav } from "../lib/nav";
import { palette } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface CoverageRow { lane: string; count: number }
interface Hypothesis {
  id: number; name: string; hypothesis: string; content_lane: string | null;
  target_audience: string | null; success_metric: string;
  minimum_sample_size: number | null; start_date: string;
  posts_in_lane: number;
}
interface ReplyTarget {
  id: number; handle: string; text_excerpt: string | null;
  relevance_score: number | null; engagement_surface_score: number | null;
  saturation_score: number | null; reply_opportunity_score: number | null;
  recommended_action_label: string | null; engagement_footnote: string | null;
}
interface AccountLead { x_handle: string; lane: string | null; priority: number }
interface PendingDraft {
  id: number; text_preview: string; draft_kind: string; pillar: string | null;
  composite_label: string | null; similarity_warning_json: string | null;
}
interface NextRepData {
  coverage: CoverageRow[];
  biggest_gap_lane: string | null;
  biggest_gap_pillar: string | null;
  hypotheses: Hypothesis[];
  reply_targets: ReplyTarget[];
  account_leads: AccountLead[];
  pending_drafts: PendingDraft[];
}

// Action label → keyline color (matches theme.py recommended_action_keyline_color)
const ACTION_KEYLINE: Record<string, string> = {
  reply_now: palette.phosphor,
  reply_if_time: palette.phosphorDim,
  consider: palette.hairline,
  skip: palette.hairline,
};

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const NextRepView = () => {
  const nav = useNav();
  const { data, isLoading, error } = useQuery({
    queryKey: ["next-rep"],
    queryFn: () => apiFetch<NextRepData>("/views/next-rep"),
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
      <Kicker>WHAT SHOULD I POST NEXT · §14.2</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Next rep</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem", fontStyle: "italic", fontSize: "0.82rem" }}>
        Measurement + generation, one click apart. The lane scoreboard below looks at the last 7 days;
        pick the lane with the lowest count to reduce uncertainty in your strongest hypothesis area.
      </p>

      {/* Lane coverage scoreboard */}
      <h2>This week — lane coverage</h2>
      {d.coverage.length === 0 ? (
        <p className="faint">
          No classified posts yet. Classify a few from <strong>Manual entry → Needs tagging</strong> so
          the lane scoreboard has something to read.
        </p>
      ) : (
        <>
          {d.coverage.map((c) => {
            const isGap = c.lane === d.biggest_gap_lane;
            const accent = isGap ? palette.phosphor : palette.boneDim;
            return (
              <div
                key={c.lane}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "0.4rem 0",
                  borderBottom: `1px solid ${palette.hairline}`,
                }}
              >
                <span className="numeric" style={{ color: palette.bone }}>
                  {c.lane.replace(/·/g, " · ")}
                </span>
                <span className="numeric" style={{ color: accent }}>
                  {c.count} post{c.count !== 1 ? "s" : ""}
                  {isGap ? " · biggest gap" : ""}
                </span>
              </div>
            );
          })}
          {d.biggest_gap_lane && (
            <Callout>
              <em>Suggested next rep:</em> a{" "}
              <span className="numeric">{d.biggest_gap_lane.replace(/·/g, " · ")}</span> post.
              This is the lane with the lowest 7-day count — shipping one here meaningfully
              reduces uncertainty in your strongest hypothesis area.
            </Callout>
          )}
        </>
      )}

      <Hairline />

      {/* Open hypotheses */}
      <h2>Open hypotheses needing data</h2>
      {d.hypotheses.length === 0 ? (
        <p className="faint">
          No running experiments. Start one in <strong>Weekly review → next week's experiment</strong>,
          then manually flip its status to <code>running</code> in the experiments table.
        </p>
      ) : (
        d.hypotheses.map((h) => {
          const remaining = (h.minimum_sample_size ?? 0) - h.posts_in_lane;
          const met = h.minimum_sample_size != null && h.posts_in_lane >= h.minimum_sample_size;
          return (
            <div key={h.id} style={{ padding: "0.5rem 0", borderBottom: `1px solid ${palette.hairline}` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ color: palette.bone, fontWeight: 500 }}>{h.name}</span>
                <span className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
                  started {h.start_date}
                </span>
              </div>
              <p style={{ margin: "0.3rem 0", color: palette.bone }}>{h.hypothesis}</p>
              <p className="faint" style={{ margin: 0 }}>
                Lane: <span className="numeric">{h.content_lane ?? "—"}</span> ·
                success metric: <span className="numeric">{h.success_metric}</span>
              </p>
              <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginTop: "0.3rem" }}>
                <ConfidenceBadge
                  tier={met ? "confident" : h.posts_in_lane >= 5 ? "directional" : "insufficient"}
                  label={`n=${h.posts_in_lane}${h.minimum_sample_size ? `/${h.minimum_sample_size}` : ""}`}
                />
                {met ? (
                  <span className="numeric" style={{ color: palette.phosphor }}>
                    Minimum sample reached — log a result in Weekly Review.
                  </span>
                ) : h.minimum_sample_size ? (
                  <span className="numeric" style={{ color: palette.boneDim }}>
                    {remaining} more post{remaining !== 1 ? "s" : ""} needed to reach minimum sample.
                  </span>
                ) : null}
              </div>
            </div>
          );
        })
      )}

      <Hairline />

      {/* Reply targets */}
      <h2>Reply targets</h2>
      <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
        Top candidates from the Reply Target Queue. Filtered to the biggest-gap pillar above when
        computable. §29.2 — one source of truth, not a parallel list.
      </p>
      {d.reply_targets.length === 0 ? (
        <div
          style={{
            border: `1px dashed ${palette.hairline}`,
            padding: "1rem 1.2rem",
            borderRadius: "3px",
            background: palette.surface,
          }}
        >
          <p style={{ margin: 0, color: palette.bone }}>
            No candidates yet — <em>add one from the queue</em>.
          </p>
        </div>
      ) : (
        <>
          {d.reply_targets.map((r) => {
            const keyline = ACTION_KEYLINE[r.recommended_action_label ?? ""] ?? palette.hairline;
            return (
              <div key={r.id}>
                <div
                  style={{
                    borderLeft: `3px solid ${keyline}`,
                    padding: "0.55rem 0.85rem 0.45rem",
                    margin: "0.45rem 0 0.15rem",
                    background: palette.surface,
                    borderRadius: "2px",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                    <span style={{ color: palette.bone, fontWeight: 500 }}>@{r.handle}</span>
                    <span className="numeric" style={{ fontSize: "0.75rem", color: palette.boneFaint }}>
                      #{r.id}
                    </span>
                  </div>
                  <div style={{ marginTop: "0.2rem", color: palette.bone, fontSize: "0.9rem", lineHeight: 1.35 }}>
                    {r.text_excerpt ?? <span className="faint">(no target text saved)</span>}
                  </div>
                </div>
                <ScoreBank
                  relevance={r.relevance_score}
                  engagementSurface={r.engagement_surface_score}
                  saturation={r.saturation_score}
                  replyOpportunity={r.reply_opportunity_score}
                  engagementFootnote={r.engagement_footnote ?? undefined}
                />
                <div style={{ margin: "-0.15rem 0 0.65rem" }}>
                  <RecommendedActionBadge label={r.recommended_action_label} />
                </div>
              </div>
            );
          })}
          <p className="faint" style={{ marginTop: "0.5rem" }}>
            Showing top <span className="numeric">{d.reply_targets.length}</span> candidates
            {d.biggest_gap_pillar ? (
              <> in pillar <span className="numeric">{d.biggest_gap_pillar}</span>.</>
            ) : "."}
          </p>
        </>
      )}
      <button onClick={() => nav("reply-queue")} style={{ marginTop: "0.3rem" }}>
        {d.reply_targets.length === 0 ? "Open Reply Target Queue →" : "See full queue →"}
      </button>

      {/* Account leads */}
      {d.account_leads.length > 0 && (
        <>
          <Hairline />
          <h3>Account leads</h3>
          {d.account_leads.map((a) => (
            <div
              key={a.x_handle}
              style={{
                borderLeft: `2px solid ${palette.phosphor}`,
                padding: "0.3rem 0.7rem",
                margin: "0.2rem 0",
                background: palette.surface,
              }}
            >
              <span className="numeric" style={{ color: palette.bone }}>@{a.x_handle}</span>{" "}
              <span className="faint" style={{ fontSize: "0.78rem" }}>
                · {a.lane ?? "—"} · priority {a.priority}
              </span>
            </div>
          ))}
        </>
      )}

      {/* Pending drafts */}
      {d.pending_drafts.length > 0 && (
        <>
          <Hairline />
          <h3>Pending agent drafts</h3>
          <p className="faint" style={{ fontSize: "0.82rem", fontStyle: "italic" }}>
            Drafts the agent has proposed but you haven't shipped or rejected.
          </p>
          {d.pending_drafts.map((draft) => (
            <div key={draft.id}>
              <div style={{ padding: "0.4rem 0", borderBottom: `1px solid ${palette.hairline}` }}>
                <span className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
                  draft #{draft.id} · {draft.draft_kind} · {draft.pillar ?? "—"}
                </span>
                <div style={{ marginTop: "0.25rem", color: palette.bone }}>{draft.text_preview}</div>
              </div>
              <RepetitionBanner warningJson={draft.similarity_warning_json} />
              <PrepublishChip label={draft.composite_label} />
            </div>
          ))}
        </>
      )}

      <Hairline />

      {/* Agent integration */}
      <h3>Ask the agent</h3>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button style={{ flex: 1 }} onClick={() => nav("agent-chat")}>
          draft for this lane →
        </button>
        <button style={{ flex: 1 }} onClick={() => nav("agent-chat")}>
          score reply candidates →
        </button>
      </div>
    </>
  );
};
