/**
 * Blogs index — faithful port of app/pages/17_Blogs.py (spec §14.14).
 *
 * Lists every blog with pipeline state: status, length-vs-target, version +
 * author, confidence chip. Click opens Blog Editor view.
 * No useEffect — useQuery only.
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Callout, Hairline, Kicker } from "../components";
import { ConfidenceBadge, StatusChip } from "../components/badges";
import { ProgressBar } from "../components";
import { apiFetch } from "../lib/api";
import { useNav } from "../lib/nav";
import { palette, fonts } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface BlogRow {
  blog_id: number;
  slug: string;
  title: string;
  status: string;
  pillar: string | null;
  audience: string | null;
  actual_length_words: number | null;
  target_length_words: number | null;
  current_version_number: number | null;
  last_edited_by: string | null;
  latest_confidence_label: string | null;
  last_edited_at_utc: string | null;
  agent_assisted: number | boolean | null;
}

interface BlogsData {
  blogs: BlogRow[];
}

// Status -> chip tone.
const STATUS_TONE: Record<string, "neutral" | "active" | "done" | "warn"> = {
  idea: "neutral",
  outlining: "active",
  drafting: "active",
  editing: "active",
  ready: "done",
  exported: "done",
  published_externally: "done",
  archived: "neutral",
};

// Confidence label -> badge tier.
function confidenceTier(label: string | null): "insufficient" | "directional" | "tentative" | "confident" {
  if (!label) return "insufficient";
  const lower = label.toLowerCase();
  if (lower.includes("confident")) return "confident";
  if (lower.includes("tentative")) return "tentative";
  if (lower.includes("directional")) return "directional";
  return "insufficient";
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const BlogsView = () => {
  const navigate = useNav();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["blogs"],
    queryFn: () => apiFetch<BlogsData>("/views/blogs"),
    retry: 1,
  });

  const createBlog = useMutation({
    mutationFn: (payload: { title: string; notes?: string }) =>
      apiFetch<{ blog_id: number; slug: string; status: string }>("/blogs", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setTitle("");
      setNotes("");
      setShowForm(false);
      queryClient.invalidateQueries({ queryKey: ["blogs"] });
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

  const { blogs } = data;

  return (
    <>
      <Kicker>BLOG PIPELINE</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>Blogs</h1>
      <p className="dim" style={{ maxWidth: 620, marginTop: "-0.2rem" }}>
        Long-form pipeline + repurposing. Same unified identity surface as X
        drafting — the agent's niche, voice profile, and personality lore feed
        blog drafting exactly as they feed X drafting.
      </p>

      <div style={{ margin: "0.6rem 0 1rem" }}>
        <button
          onClick={() => setShowForm((p) => !p)}
          style={{
            padding: "0.4rem 1rem",
            background: palette.phosphor,
            color: palette.ink,
            border: "none",
            borderRadius: "2px",
            fontFamily: fonts.body,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          {showForm ? "Cancel" : "+ New Blog"}
        </button>
        {showForm && (
          <div style={{ marginTop: "0.5rem", padding: "0.6rem", background: palette.surface, borderRadius: "2px" }}>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Blog title (required)"
              style={{ width: "100%", marginBottom: "0.4rem" }}
            />
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Notes (optional)"
              style={{ width: "100%", marginBottom: "0.4rem" }}
            />
            <button
              className="primary"
              onClick={() => title.trim() && createBlog.mutate({ title: title.trim(), notes: notes.trim() || undefined })}
              disabled={createBlog.isPending || !title.trim()}
            >
              {createBlog.isPending ? "Creating…" : "Create"}
            </button>
            {createBlog.isError && (
              <span style={{ color: palette.warnAmber, fontSize: "0.82rem", marginLeft: "0.5rem" }}>
                {String((createBlog.error as Error).message ?? createBlog.error)}
              </span>
            )}
          </div>
        )}
      </div>

      <Hairline />

      {blogs.length === 0 ? (
        <Callout>
          <em>No blogs yet.</em> Create one via the Streamlit Blogs page to get
          started with long-form content.
        </Callout>
      ) : (
        blogs.map((blog) => {
          const lengthPct =
            blog.target_length_words && blog.actual_length_words
              ? Math.min(1, blog.actual_length_words / blog.target_length_words)
              : null;

          return (
            <div
              key={blog.blog_id}
              onClick={() => navigate("blog-editor", { blogId: blog.blog_id })}
              style={{
                padding: "0.6rem 0.85rem",
                margin: "0.4rem 0",
                background: palette.surface,
                borderLeft: `2px solid ${blog.status === "drafting" || blog.status === "editing" ? palette.phosphor : palette.hairline}`,
                borderRadius: "2px",
                cursor: "pointer",
              }}
            >
              {/* Title + status */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                }}
              >
                <span style={{ color: palette.bone, fontWeight: 500, fontSize: "1rem" }}>
                  {blog.title}
                </span>
                <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                  <StatusChip
                    label={blog.status}
                    tone={STATUS_TONE[blog.status] ?? "neutral"}
                  />
                  {blog.latest_confidence_label && (
                    <ConfidenceBadge tier={confidenceTier(blog.latest_confidence_label)} label={blog.latest_confidence_label} />
                  )}
                </div>
              </div>

              {/* Meta row */}
              <div
                className="numeric"
                style={{
                  fontSize: "0.78rem",
                  color: palette.boneDim,
                  marginTop: "0.3rem",
                }}
              >
                {blog.pillar && `${blog.pillar} · `}
                {blog.audience && `${blog.audience} · `}
                {blog.actual_length_words != null && `${blog.actual_length_words} words`}
                {blog.target_length_words != null &&
                  ` / ${blog.target_length_words} target`}
                {blog.current_version_number != null &&
                  ` · v${blog.current_version_number}`}
                {blog.last_edited_by && ` by ${blog.last_edited_by}`}
                {blog.agent_assisted ? " · agent-assisted" : ""}
              </div>

              {/* Length progress */}
              {lengthPct != null && (
                <div style={{ marginTop: "0.3rem", maxWidth: 300 }}>
                  <ProgressBar value={lengthPct} />
                </div>
              )}

              {/* Last edited */}
              {blog.last_edited_at_utc && (
                <div
                  className="faint"
                  style={{ fontSize: "0.72rem", marginTop: "0.2rem" }}
                >
                  last edited {blog.last_edited_at_utc.slice(0, 16)}
                </div>
              )}
            </div>
          );
        })
      )}
    </>
  );
};
