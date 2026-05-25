/**
 * Blog Editor — faithful port of the §14.15 blog editing surface.
 *
 * For the initial port: simplified single-column layout with blog fields,
 * version history, and body display. The full 3-panel layout (outline left,
 * body center, agent right) is a follow-up.
 * No useEffect — useQuery only.
 */
import { useQuery } from "@tanstack/react-query";

import { Callout, Hairline, Kicker } from "../components";
import { ConfidenceBadge, StatusChip } from "../components/badges";
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
  id: number;
  version_number: number;
  title_at_version: string;
  status_at_version: string;
  created_by: string;
  confidence_label_at_version: string | null;
  created_at_utc: string;
}

interface BlogDetailData {
  blog: BlogDetail;
  versions: BlogVersion[];
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
export const BlogEditorView = () => {
  // RV5-W10: read blog ID from nav params (passed by BlogsView on click).
  // Falls back to loading the first blog from the list if no param is set
  // (e.g. navigating to Blog Editor directly from the sidebar).
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

  if (!firstBlogId && !isLoading) {
    return (
      <>
        <Kicker>§14.15 · BLOG EDITOR</Kicker>
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

  const { blog, versions } = data;
  const lengthPct =
    blog.target_length_words && blog.actual_length_words
      ? Math.min(1, blog.actual_length_words / blog.target_length_words)
      : null;

  return (
    <>
      <Kicker>§14.15 · BLOG EDITOR</Kicker>
      <h1 style={{ fontSize: "2.1rem" }}>{blog.title}</h1>

      {/* Meta strip */}
      <div
        style={{
          display: "flex",
          gap: "0.6rem",
          alignItems: "baseline",
          flexWrap: "wrap",
          marginTop: "-0.2rem",
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

      {/* Outline (if present) */}
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

      {/* Body */}
      <h2>Body</h2>
      <div
        style={{
          padding: "0.7rem 0.9rem",
          background: palette.surface,
          borderLeft: `2px solid ${palette.hairline}`,
          borderRadius: "2px",
          fontSize: "0.92rem",
          color: palette.bone,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
          wordWrap: "break-word",
          maxHeight: "30rem",
          overflowY: "auto",
          fontFamily: fonts.body,
        }}
      >
        {blog.current_body_markdown || (
          <span className="faint">(empty body — start writing)</span>
        )}
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
              key={v.id}
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
                {v.confidence_label_at_version && (
                  <ConfidenceBadge
                    tier={
                      v.confidence_label_at_version.includes("confident")
                        ? "confident"
                        : v.confidence_label_at_version.includes("tentative")
                          ? "tentative"
                          : "insufficient"
                    }
                    label={v.confidence_label_at_version}
                  />
                )}
              </div>
              <span className="numeric" style={{ fontSize: "0.72rem", color: palette.boneFaint }}>
                {v.created_by} · {v.created_at_utc?.slice(0, 16)}
              </span>
            </div>
          ))}
        </div>
      )}

      <Hairline />

      <div className="faint" style={{ fontSize: "0.82rem", marginTop: "0.3rem" }}>
        Full 3-panel editor layout (outline / body / agent) is a follow-up
        increment. This port provides read + version history.
      </div>
    </>
  );
};
