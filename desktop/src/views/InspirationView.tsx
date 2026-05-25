/**
 * Inspiration Library — faithful port of app/pages/16_Inspiration_Library.py (spec §14.13).
 *
 * Saved posts with source text, author, tags, and available transforms.
 * Each transform shows the output text + plagiarism risk label.
 * No useEffect — useQuery + useMutation only.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { StatusChip } from "../components/badges";
import { SpecimenBlock } from "../components/cards";
import { apiFetch } from "../lib/api";
import { palette, fonts } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Transform {
  id: number;
  transform_mode: string;
  output_text: string;
  plagiarism_risk_label: string | null;
  created_at_utc: string;
}

interface InspirationItem {
  id: number;
  source_url: string | null;
  source_author: string | null;
  source_post_text: string;
  tags: string[];
  saved_at_utc: string;
  notes: string | null;
  status: string;
  transforms: Transform[];
}

interface InspirationData {
  items: InspirationItem[];
}

// Plagiarism risk -> chip tone.
const RISK_TONE: Record<string, "done" | "active" | "warn" | "neutral"> = {
  low: "done",
  medium: "active",
  high: "warn",
};

// ---------------------------------------------------------------------------
// Save Inspiration Form
// ---------------------------------------------------------------------------
const SaveInspirationForm = ({ onSaved }: { onSaved: () => void }) => {
  const [sourceText, setSourceText] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceAuthor, setSourceAuthor] = useState("");
  const [notes, setNotes] = useState("");
  const [feedback, setFeedback] = useState<{ type: "ok" | "err"; msg: string } | null>(null);

  const saveMutation = useMutation({
    mutationFn: (payload: {
      source_post_text: string;
      source_url?: string;
      source_author?: string;
      notes?: string;
    }) =>
      apiFetch<{ inspiration_id: number }>("/inspirations", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: ({ inspiration_id }) => {
      setSourceText("");
      setSourceUrl("");
      setSourceAuthor("");
      setNotes("");
      setFeedback({ type: "ok", msg: `Saved inspiration #${inspiration_id}` });
      onSaved();
    },
    onError: (err: Error) => {
      setFeedback({ type: "err", msg: err.message });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!sourceText.trim()) return;
    saveMutation.mutate({
      source_post_text: sourceText.trim(),
      ...(sourceUrl.trim() ? { source_url: sourceUrl.trim() } : {}),
      ...(sourceAuthor.trim() ? { source_author: sourceAuthor.trim() } : {}),
      ...(notes.trim() ? { notes: notes.trim() } : {}),
    });
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "0.45rem 0.6rem",
    fontFamily: fonts.body,
    fontSize: "0.88rem",
    color: palette.bone,
    background: palette.surfaceRaised,
    border: `1px solid ${palette.hairline}`,
    borderRadius: "2px",
    outline: "none",
  };

  return (
    <form onSubmit={handleSubmit} style={{ marginBottom: "1.2rem" }}>
      <div
        className="kicker"
        style={{ fontSize: "0.65rem", color: palette.boneFaint, marginBottom: "0.4rem" }}
      >
        SAVE INSPIRATION
      </div>

      <textarea
        value={sourceText}
        onChange={(e) => setSourceText(e.target.value)}
        placeholder="Paste the source post text (required)..."
        rows={4}
        style={{ ...inputStyle, resize: "vertical", marginBottom: "0.4rem" }}
        required
      />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.4rem", marginBottom: "0.4rem" }}>
        <input
          value={sourceUrl}
          onChange={(e) => setSourceUrl(e.target.value)}
          placeholder="Source URL (optional)"
          style={inputStyle}
        />
        <input
          value={sourceAuthor}
          onChange={(e) => setSourceAuthor(e.target.value)}
          placeholder="Author handle (optional)"
          style={inputStyle}
        />
      </div>

      <input
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes (optional)"
        style={{ ...inputStyle, marginBottom: "0.5rem" }}
      />

      <div style={{ display: "flex", alignItems: "center", gap: "0.7rem" }}>
        <button
          type="submit"
          disabled={saveMutation.isPending || !sourceText.trim()}
          style={{
            padding: "0.4rem 1rem",
            fontFamily: fonts.body,
            fontSize: "0.85rem",
            fontWeight: 500,
            color: palette.ink,
            background: palette.phosphor,
            border: "none",
            borderRadius: "2px",
            cursor: saveMutation.isPending ? "wait" : "pointer",
            opacity: saveMutation.isPending || !sourceText.trim() ? 0.5 : 1,
          }}
        >
          {saveMutation.isPending ? "Saving..." : "Save Inspiration"}
        </button>

        {feedback && (
          <span
            style={{
              fontSize: "0.8rem",
              color: feedback.type === "ok" ? palette.phosphor : palette.warnAmber,
            }}
          >
            {feedback.msg}
          </span>
        )}
      </div>
    </form>
  );
};

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const InspirationView = () => {
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["inspiration"],
    queryFn: () => apiFetch<InspirationData>("/views/inspiration"),
    retry: 1,
  });

  const archiveMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<{ ok: true }>(`/inspirations/${id}/archive`, { method: "PUT" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inspiration"] });
    },
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

  const { items } = data;

  return (
    <>
      <Kicker>INSPIRATION LIBRARY</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Inspiration Library</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Capture-then-remix. Saved external posts with seven transform modes
        and deterministic plagiarism risk reads.
      </p>

      <Hairline />

      <SaveInspirationForm onSaved={() => qc.invalidateQueries({ queryKey: ["inspiration"] })} />

      <Hairline />

      {items.length === 0 ? (
        <Callout>
          <em>No inspiration posts saved yet.</em> Use the form above to save
          your first inspiration.
        </Callout>
      ) : (
        items.map((item) => (
          <div
            key={item.id}
            style={{
              marginBottom: "1rem",
              padding: "0.7rem 0.9rem",
              background: palette.surface,
              borderLeft: `2px solid ${palette.phosphorDim}`,
              borderRadius: "2px",
            }}
          >
            {/* Header */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
              }}
            >
              <span style={{ color: palette.bone, fontWeight: 500 }}>
                {item.source_author ? `@${item.source_author}` : "Unknown source"}
              </span>
              <div style={{ display: "flex", alignItems: "baseline", gap: "0.5rem" }}>
                <span className="numeric" style={{ fontSize: "0.72rem", color: palette.boneFaint }}>
                  #{item.id} · saved {item.saved_at_utc?.slice(0, 10) ?? "—"}
                </span>
                {item.status !== "archived" && (
                  <button
                    onClick={() => archiveMutation.mutate(item.id)}
                    disabled={archiveMutation.isPending}
                    style={{
                      padding: "2px 8px",
                      fontFamily: fonts.mono,
                      fontSize: "0.68rem",
                      color: palette.boneDim,
                      background: palette.surfaceRaised,
                      border: `1px solid ${palette.hairline}`,
                      borderRadius: "2px",
                      cursor: archiveMutation.isPending ? "wait" : "pointer",
                    }}
                  >
                    Archive
                  </button>
                )}
              </div>
            </div>

            {/* Source text */}
            <SpecimenBlock text={item.source_post_text} maxHeightRem={8} />

            {/* Tags */}
            {item.tags.length > 0 && (
              <div style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap", marginBottom: "0.3rem" }}>
                {item.tags.map((tag) => (
                  <span
                    key={tag}
                    style={{
                      fontFamily: fonts.mono,
                      fontSize: "0.7rem",
                      color: palette.phosphor,
                      background: palette.surfaceRaised,
                      padding: "1px 6px",
                      borderRadius: "2px",
                    }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {/* Notes */}
            {item.notes && (
              <div style={{ fontSize: "0.85rem", color: palette.boneDim, fontStyle: "italic", marginBottom: "0.3rem" }}>
                {item.notes}
              </div>
            )}

            {/* Source URL */}
            {item.source_url && (
              <a
                href={item.source_url}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  fontSize: "0.78rem",
                  color: palette.phosphorDim,
                  textDecoration: "none",
                }}
              >
                {item.source_url}
              </a>
            )}

            {/* Transforms */}
            {item.transforms.length > 0 && (
              <div style={{ marginTop: "0.5rem" }}>
                <div
                  className="kicker"
                  style={{ fontSize: "0.65rem", color: palette.boneFaint }}
                >
                  TRANSFORMS ({item.transforms.length})
                </div>
                {item.transforms.map((t) => (
                  <div
                    key={t.id}
                    style={{
                      padding: "0.4rem 0.6rem",
                      margin: "0.25rem 0",
                      background: palette.surfaceRaised,
                      borderRadius: "2px",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "baseline",
                        marginBottom: "0.2rem",
                      }}
                    >
                      <span
                        className="numeric"
                        style={{ fontSize: "0.75rem", color: palette.phosphor }}
                      >
                        {t.transform_mode}
                      </span>
                      <div style={{ display: "flex", gap: "0.3rem", alignItems: "center" }}>
                        {t.plagiarism_risk_label && (
                          <StatusChip
                            label={`risk: ${t.plagiarism_risk_label}`}
                            tone={RISK_TONE[t.plagiarism_risk_label] ?? "neutral"}
                          />
                        )}
                        <span
                          className="numeric"
                          style={{ fontSize: "0.68rem", color: palette.boneFaint }}
                        >
                          {t.created_at_utc?.slice(0, 10)}
                        </span>
                      </div>
                    </div>
                    <div
                      style={{
                        fontSize: "0.88rem",
                        color: palette.bone,
                        lineHeight: 1.4,
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {t.output_text}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))
      )}
    </>
  );
};
