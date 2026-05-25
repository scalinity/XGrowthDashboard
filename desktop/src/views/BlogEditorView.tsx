/**
 * Blog Editor — faithful port of the §14.15 blog editing surface.
 *
 * Provides inline editing of title + body_markdown, status transitions,
 * version history, and save feedback. No useEffect — useQuery + useMutation.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { StatusChip } from "../components/badges";
import { ProgressBar } from "../components";
import { apiFetch } from "../lib/api";
import { useNavParams as __useNavParams } from "../lib/nav";
import { palette, fonts } from "../theme/tokens";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface BlogDetail {
  id: number;
  slug: string;
  title: string;
  status: string;
  pillar: string | null;
  audience: string | null;
  current_body_markdown: string;
  outline_markdown: string | null;
  actual_length_words: number;
  target_length_words: number | null;
  agent_assisted: boolean;
  created_at_utc: string;
  updated_at_utc: string;
}

interface BlogVersion {
  version_number: number;
  created_at: string;
  created_by: string;
  status_at_version: string;
  title_at_version: string;
  is_current: boolean;
}

interface BlogDetailData {
  blog: BlogDetail;
  versions: BlogVersion[];
}

interface SaveResponse {
  saved: boolean;
  version_number?: number;
  reason?: string;
}

interface StatusTransitionResponse {
  new_status: string;
  version_number: number;
}

// Ordered status pipeline per spec.
const STATUS_PIPELINE = [
  "idea",
  "outline",
  "draft",
  "review",
  "final",
  "exported",
  "published_externally",
] as const;

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const BlogEditorView = () => {
  const qc = useQueryClient();

  // RV5-W10: read blog ID from nav params (passed by BlogsView on click).
  const navParams = __useNavParams();
  const paramBlogId = typeof navParams.blogId === "number" ? navParams.blogId : null;

  const { data: blogsData } = useQuery({
    queryKey: ["blogs"],
    queryFn: () => apiFetch<{ blogs: Array<{ blog_id: number }> }>("/views/blogs"),
    retry: 1,
    enabled: paramBlogId === null,
  });

  const firstBlogId = paramBlogId ?? blogsData?.blogs?.[0]?.blog_id ?? null;

  const { data, isLoading, error } = useQuery({
    queryKey: ["blog-detail", firstBlogId],
    queryFn: () => apiFetch<BlogDetailData>(`/views/blog/${firstBlogId}`),
    enabled: firstBlogId != null,
    retry: 1,
  });

  // Version history (separate endpoint for freshness after saves).
  const { data: versionsData } = useQuery({
    queryKey: ["blog-versions", firstBlogId],
    queryFn: () =>
      apiFetch<{ versions: BlogVersion[] }>(`/blogs/${firstBlogId}/versions`),
    enabled: firstBlogId != null,
    retry: 1,
  });

  // --- Local editing state ---
  const [editTitle, setEditTitle] = useState<string | null>(null);
  const [editBody, setEditBody] = useState<string | null>(null);
  const [targetStatus, setTargetStatus] = useState<string>("");
  const [feedback, setFeedback] = useState<{ type: "ok" | "err"; msg: string } | null>(null);

  // --- Mutations ---
  const saveMutation = useMutation({
    mutationFn: async (payload: { title?: string; body_markdown?: string }) =>
      apiFetch<SaveResponse>(`/blogs/${firstBlogId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: (res) => {
      if (res.saved) {
        setFeedback({ type: "ok", msg: `Saved (v${res.version_number}).` });
        setEditTitle(null);
        setEditBody(null);
        qc.invalidateQueries({ queryKey: ["blog-detail", firstBlogId] });
        qc.invalidateQueries({ queryKey: ["blog-versions", firstBlogId] });
      } else {
        setFeedback({ type: "err", msg: res.reason ?? "Save rejected." });
      }
    },
    onError: (err: Error) => {
      setFeedback({ type: "err", msg: err.message });
    },
  });

  const statusMutation = useMutation({
    mutationFn: async (newStatus: string) =>
      apiFetch<StatusTransitionResponse>(`/blogs/${firstBlogId}/status`, {
        method: "PUT",
        body: JSON.stringify({ new_status: newStatus }),
      }),
    onSuccess: (res) => {
      setFeedback({ type: "ok", msg: `Status → ${res.new_status} (v${res.version_number}).` });
      setTargetStatus("");
      qc.invalidateQueries({ queryKey: ["blog-detail", firstBlogId] });
      qc.invalidateQueries({ queryKey: ["blog-versions", firstBlogId] });
    },
    onError: (err: Error) => {
      setFeedback({ type: "err", msg: err.message });
    },
  });

  // --- Handlers ---
  const handleSave = () => {
    const payload: { title?: string; body_markdown?: string } = {};
    if (editTitle !== null) payload.title = editTitle;
    if (editBody !== null) payload.body_markdown = editBody;
    if (Object.keys(payload).length === 0) return;
    setFeedback(null);
    saveMutation.mutate(payload);
  };

  const handleAdvanceStatus = () => {
    if (!targetStatus) return;
    setFeedback(null);
    statusMutation.mutate(targetStatus);
  };

  // --- Guards ---
  if (!firstBlogId && !isLoading) {
    return (
      <>
        <Kicker>BLOG EDITOR</Kicker>
        <h1 style={{ fontSize: "2.1rem" }}>Blog Editor</h1>
        <Callout>
          <em>No blogs exist yet.</em> Create one via the Blogs view first.
        </Callout>
      </>
    );
  }

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

  const { blog } = data;
  const versions = versionsData?.versions ?? data.versions ?? [];
  const lengthPct =
    blog.target_length_words && blog.actual_length_words
      ? Math.min(1, blog.actual_length_words / blog.target_length_words)
      : null;

  // Determine available next statuses (only forward transitions).
  const currentIdx = STATUS_PIPELINE.indexOf(blog.status as typeof STATUS_PIPELINE[number]);
  const availableStatuses = currentIdx >= 0 ? STATUS_PIPELINE.slice(currentIdx + 1) : [];

  // Resolve displayed values (local edits take priority).
  const displayTitle = editTitle ?? blog.title;
  const displayBody = editBody ?? blog.current_body_markdown;

  return (
    <>
      <Kicker>BLOG EDITOR</Kicker>

      {/* Editable title */}
      <input
        type="text"
        value={displayTitle}
        onChange={(e) => setEditTitle(e.target.value)}
        style={{
          fontSize: "2.1rem",
          fontFamily: fonts.display,
          color: palette.bone,
          background: palette.surface,
          border: `1px solid ${palette.hairline}`,
          borderRadius: "3px",
          padding: "0.3rem 0.5rem",
          width: "100%",
          outline: "none",
        }}
      />

      {/* Meta strip */}
      <div
        style={{
          display: "flex",
          gap: "0.6rem",
          alignItems: "baseline",
          flexWrap: "wrap",
          marginTop: "0.4rem",
        }}
      >
        <StatusChip label={blog.status} tone="active" />
        {blog.pillar && (
          <span className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
            {blog.pillar}
          </span>
        )}
        {blog.audience && (
          <span className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
            · {blog.audience}
          </span>
        )}
        <span className="numeric" style={{ fontSize: "0.78rem", color: palette.boneDim }}>
          · {blog.actual_length_words} words
          {blog.target_length_words && ` / ${blog.target_length_words} target`}
        </span>
        {blog.agent_assisted && (
          <span className="numeric" style={{ fontSize: "0.72rem", color: palette.phosphorDim }}>
            · agent-assisted
          </span>
        )}
      </div>

      {/* Length progress */}
      {lengthPct != null && (
        <div style={{ marginTop: "0.5rem", maxWidth: 400 }}>
          <ProgressBar value={lengthPct} />
          <span className="numeric" style={{ fontSize: "0.72rem", color: palette.boneDim }}>
            {Math.round(lengthPct * 100)}% of target length
          </span>
        </div>
      )}

      <Hairline />

      {/* Status transition */}
      {availableStatuses.length > 0 && (
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.6rem" }}>
          <label style={{ fontSize: "0.82rem", color: palette.boneDim }}>Advance to:</label>
          <select
            value={targetStatus}
            onChange={(e) => setTargetStatus(e.target.value)}
            style={{
              background: palette.surfaceRaised,
              color: palette.bone,
              border: `1px solid ${palette.hairline}`,
              borderRadius: "3px",
              padding: "0.3rem 0.5rem",
              fontFamily: fonts.body,
              fontSize: "0.85rem",
            }}
          >
            <option value="">—</option>
            {availableStatuses.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button
            onClick={handleAdvanceStatus}
            disabled={!targetStatus || statusMutation.isPending}
            style={{
              background: palette.phosphor,
              color: palette.ink,
              border: "none",
              borderRadius: "3px",
              padding: "0.35rem 0.9rem",
              fontFamily: fonts.body,
              fontWeight: 600,
              fontSize: "0.82rem",
              cursor: targetStatus ? "pointer" : "not-allowed",
              opacity: targetStatus ? 1 : 0.5,
            }}
          >
            {statusMutation.isPending ? "..." : "Advance"}
          </button>
        </div>
      )}

      {/* Feedback banner */}
      {feedback && (
        <div
          style={{
            padding: "0.4rem 0.7rem",
            borderRadius: "3px",
            fontSize: "0.82rem",
            fontFamily: fonts.mono,
            marginBottom: "0.5rem",
            background: feedback.type === "ok" ? palette.phosphorDim : palette.warnAmber,
            color: feedback.type === "ok" ? palette.bone : palette.ink,
          }}
        >
          {feedback.msg}
        </div>
      )}

      {/* Outline (read-only for now) */}
      {blog.outline_markdown && (
        <>
          <h2>Outline</h2>
          <div
            style={{
              padding: "0.6rem 0.8rem",
              background: palette.surfaceRaised,
              borderRadius: "2px",
              fontSize: "0.88rem",
              color: palette.bone,
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
              maxHeight: "12rem",
              overflowY: "auto",
            }}
          >
            {blog.outline_markdown}
          </div>
          <Hairline />
        </>
      )}

      {/* Editable body */}
      <h2>Body</h2>
      <textarea
        value={displayBody}
        onChange={(e) => setEditBody(e.target.value)}
        rows={16}
        style={{
          width: "100%",
          padding: "0.7rem 0.9rem",
          background: palette.surface,
          borderLeft: `2px solid ${palette.hairline}`,
          border: `1px solid ${palette.hairline}`,
          borderRadius: "2px",
          fontSize: "0.92rem",
          color: palette.bone,
          lineHeight: 1.6,
          fontFamily: fonts.body,
          resize: "vertical",
          outline: "none",
        }}
        placeholder="Start writing..."
      />

      {/* Save button */}
      <div style={{ marginTop: "0.5rem" }}>
        <button
          onClick={handleSave}
          disabled={saveMutation.isPending || (editTitle === null && editBody === null)}
          style={{
            background: palette.phosphor,
            color: palette.ink,
            border: "none",
            borderRadius: "3px",
            padding: "0.45rem 1.2rem",
            fontFamily: fonts.body,
            fontWeight: 600,
            fontSize: "0.88rem",
            cursor:
              editTitle !== null || editBody !== null ? "pointer" : "not-allowed",
            opacity: editTitle !== null || editBody !== null ? 1 : 0.5,
          }}
        >
          {saveMutation.isPending ? "Saving..." : "Save"}
        </button>
        <span
          className="numeric"
          style={{ marginLeft: "0.7rem", fontSize: "0.75rem", color: palette.boneDim }}
        >
          {editTitle !== null || editBody !== null ? "unsaved changes" : ""}
        </span>
      </div>

      <Hairline />

      {/* Version history */}
      <h2>Version history</h2>
      {versions.length === 0 ? (
        <p className="faint">No versions recorded yet.</p>
      ) : (
        <div>
          {versions.map((v) => (
            <div
              key={v.version_number}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                padding: "0.3rem 0",
                borderBottom: `1px solid ${palette.hairline}`,
              }}
            >
              <div style={{ display: "flex", gap: "0.4rem", alignItems: "baseline" }}>
                <span className="numeric" style={{ color: palette.bone }}>
                  v{v.version_number}
                </span>
                <span style={{ fontSize: "0.82rem", color: palette.boneDim }}>
                  {v.title_at_version}
                </span>
                <StatusChip label={v.status_at_version} tone="neutral" />
                {v.is_current && (
                  <span
                    style={{
                      fontSize: "0.7rem",
                      color: palette.phosphor,
                      fontFamily: fonts.mono,
                    }}
                  >
                    current
                  </span>
                )}
              </div>
              <span className="numeric" style={{ fontSize: "0.72rem", color: palette.boneFaint }}>
                {v.created_by} · {v.created_at?.slice(0, 16)}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
};
