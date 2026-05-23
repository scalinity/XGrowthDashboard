"""Blog exports — Phase 6 §28.33.

Four format renderers (Markdown / HTML / JSON / MDX). Atomic
write-then-record contract: file write succeeds BEFORE DB writes,
and the file is written to a temp path then renamed (os.replace)
so a partial file never appears at the target path.

Re-exporting overwrites the file at ``target_path`` but inserts a
NEW ``blog_exports`` row — prior export rows are preserved as audit
history. ``content_sha256`` is the audit anchor for detecting later
disk-side tampering.

Auto-status-transition (load-bearing): a blog with ``status='ready'``
that successfully exports transitions to ``'exported'``. A blog
already at ``'exported'`` stays put (re-export). The transition to
``'published_externally'`` is MANUAL — Daniel sets it after he
actually publishes externally.

Partial-state contract (§28.33 banner UX):

If the file write succeeds but the DB insert fails, the file exists
on disk but the export isn't recorded. ``export()`` raises
:class:`ExportRecordFailedError` with the path of the orphan file —
the caller (editor) surfaces a "file written but export record
failed" banner so Daniel can manually mark resolved. We deliberately
do NOT delete the orphan file because it represents real work
Daniel may want to use.

The DB write itself wraps the row insert + audit_logs row + optional
status transition in a single transaction so partial DB state isn't
possible.

No platform-integration. The export writes to disk and stops. Daniel
publishes externally on his blog platform by hand. There is no
Substack / Ghost / WordPress / Medium API path here, ever (§7.1, §0).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.agent import audit_log as _audit_log
from app.agent import blogs as _blogs
from app.db import transaction


_LOG = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

ExportFormat = Literal["markdown", "html", "json", "mdx"]
VALID_FORMATS: frozenset[str] = frozenset({"markdown", "html", "json", "mdx"})

_FORMAT_EXTENSIONS: dict[str, frozenset[str]] = {
    "markdown": frozenset({".md", ".markdown"}),
    "html":     frozenset({".html", ".htm"}),
    "json":     frozenset({".json"}),
    "mdx":      frozenset({".mdx"}),
}


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------
class BlogExportError(RuntimeError):
    """Base for blog-export errors."""


class InvalidExportFormatError(BlogExportError):
    """Raised when ``format`` is not one of the four supported formats."""


class ExportFileWriteError(BlogExportError):
    """Raised when the file write itself fails (permissions, full disk, etc.)."""


class ExportRecordFailedError(BlogExportError):
    """Raised when the file write succeeded but the DB insert failed.

    Carries the path of the orphan file so the caller can surface a
    "file written but export record failed" reconciliation banner.
    """

    def __init__(self, target_path: str, *, original: Exception) -> None:
        self.target_path = target_path
        self.original = original
        super().__init__(
            f"file written to {target_path} but export record failed: {original}"
        )


class ExportPathOutsideRootError(BlogExportError):
    """Raised when ``target_path`` resolves outside the configured
    ``blog_export_default_directory`` root (CWE-22 / CWE-73).

    The export writer constrains every path to the configured root so a
    fat-fingered ``/etc/hosts`` or `..`-traversal from the editor's
    free-text target field can't clobber arbitrary files under
    Daniel's user account.
    """


class ExportPathExtensionMismatchError(BlogExportError):
    """Raised when ``target_path``'s extension does not match the
    requested ``format`` (e.g. ``format='markdown'`` with
    ``target_path='out.json'`` — almost certainly a typo, and worth
    refusing rather than silently writing the wrong-suffix file)."""


# ---------------------------------------------------------------------------
# Result dataclass.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class BlogExport:
    id: int
    blog_id: int
    blog_version_id: int | None
    format: str
    target_path: str
    file_size_bytes: int
    content_sha256: str
    seo_metadata_included: bool
    repurposing_links_included: bool
    exported_at_utc: str


# ---------------------------------------------------------------------------
# Format renderers.
# ---------------------------------------------------------------------------
def _seo_data_for_blog(conn: sqlite3.Connection, blog_id: int) -> dict:
    row = conn.execute(
        """
        SELECT seo_title, seo_description, seo_tags_json, slug, pillar,
               audience, created_at_utc
        FROM blogs WHERE id = ?
        """,
        (blog_id,),
    ).fetchone()
    tags: list[str] = []
    if row and row["seo_tags_json"]:
        try:
            parsed = json.loads(row["seo_tags_json"])
            if isinstance(parsed, list):
                tags = [str(t) for t in parsed]
        except (json.JSONDecodeError, TypeError):
            tags = []
    return {
        "title": row["seo_title"] if row else None,
        "description": row["seo_description"] if row else None,
        "tags": tags,
        "slug": row["slug"] if row else None,
        "pillar": row["pillar"] if row else None,
        "audience": row["audience"] if row else None,
        "created_at_utc": row["created_at_utc"] if row else None,
    }


def _linked_posts_summary(conn: sqlite3.Connection, blog_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT btpl.direction, btpl.relationship_kind, p.id AS post_id,
               p.text, p.x_post_id, p.published_to_x_at
        FROM blog_to_post_links btpl
        JOIN posts p ON p.id = btpl.post_id
        WHERE btpl.blog_id = ?
        ORDER BY btpl.created_at_utc ASC
        """,
        (blog_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "post_id": r["post_id"],
            "direction": r["direction"],
            "relationship_kind": r["relationship_kind"],
            "text_excerpt": (r["text"][:120] + "…") if r["text"] and len(r["text"]) > 120 else (r["text"] or ""),
            "x_post_id": r["x_post_id"],
            "published_to_x_at": r["published_to_x_at"],
        })
    return out


def _yaml_frontmatter(seo: dict, exported_at_utc: str) -> str:
    """Render a minimal YAML frontmatter block.

    Hand-rolled (no PyYAML dep) — Daniel's SEO fields are simple
    strings + a flat list. Strings are JSON-escaped for safety against
    embedded quotes / newlines.
    """
    def _e(v) -> str:
        return json.dumps(v) if v is not None else '""'

    lines = ["---"]
    if seo.get("title"):
        lines.append(f"title: {_e(seo['title'])}")
    if seo.get("description"):
        lines.append(f"description: {_e(seo['description'])}")
    if seo.get("tags"):
        tags_inline = ", ".join(_e(t) for t in seo["tags"])
        lines.append(f"tags: [{tags_inline}]")
    if seo.get("slug"):
        lines.append(f"slug: {_e(seo['slug'])}")
    if seo.get("pillar"):
        lines.append(f"pillar: {_e(seo['pillar'])}")
    if seo.get("audience"):
        lines.append(f"audience: {_e(seo['audience'])}")
    if seo.get("created_at_utc"):
        lines.append(f"created_at_utc: {_e(seo['created_at_utc'])}")
    lines.append(f"exported_at_utc: {_e(exported_at_utc)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _mdx_frontmatter(seo: dict, exported_at_utc: str) -> str:
    """Render an MDX-compatible ``export const meta = { … }`` block."""
    payload: dict = {}
    for k in ("title", "description", "slug", "pillar", "audience", "created_at_utc"):
        if seo.get(k):
            payload[k] = seo[k]
    if seo.get("tags"):
        payload["tags"] = list(seo["tags"])
    payload["exported_at_utc"] = exported_at_utc
    body = json.dumps(payload, indent=2)
    return f"export const meta = {body};\n\n"


def _repurposing_footer(linked: list[dict]) -> str:
    """Markdown footer summarizing blog_to_post_links rows."""
    if not linked:
        return ""
    lines = ["", "---", "**Repurposing notes (excluded from public publish):**"]
    for row in linked:
        direction = row["direction"].replace("_", " ")
        kind = row["relationship_kind"].replace("_", " ")
        excerpt = row["text_excerpt"]
        x_id = row["x_post_id"]
        if x_id:
            url = f"https://x.com/i/web/status/{x_id}"
            lines.append(f"- {direction} ({kind}): [post {row['post_id']}]({url}) — {excerpt}")
        else:
            lines.append(f"- {direction} ({kind}): post #{row['post_id']} — {excerpt}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Minimal Markdown → HTML for the html format. Scope-minimal on purpose:
# Daniel's blogs use paragraphs, headings, and inline emphasis. Lists
# / tables / images can land in a later pass if he asks.
# ---------------------------------------------------------------------------
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _render_inline(text: str) -> str:
    """Apply inline transforms after the line/block layer has escaped."""
    out = _INLINE_CODE_RE.sub(lambda m: f"<code>{_escape_html(m.group(1))}</code>", text)
    out = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    out = _LINK_RE.sub(
        lambda m: f'<a href="{_escape_html(m.group(2))}">{m.group(1)}</a>', out
    )
    return out


def _markdown_to_html_body(markdown_text: str) -> str:
    """Convert markdown body to an HTML fragment.

    Skips code fences entirely — emits a ``<pre><code>`` block with the
    fence contents escaped. Headings (#-######) become h1-h6. Blank-
    line-separated blocks of non-heading text become paragraphs with
    inline markup applied.
    """
    lines = markdown_text.splitlines()
    blocks: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Code fence (```).
        if stripped.startswith("```"):
            j = i + 1
            code_lines: list[str] = []
            while j < n and not lines[j].strip().startswith("```"):
                code_lines.append(lines[j])
                j += 1
            body = "\n".join(code_lines)
            blocks.append(f"<pre><code>{_escape_html(body)}</code></pre>")
            i = j + 1 if j < n else j
            continue

        # Heading.
        if stripped.startswith("#"):
            m = _HEADING_RE.match(stripped)
            if m:
                level = len(m.group(1))
                text = _render_inline(_escape_html(m.group(2).strip()))
                blocks.append(f"<h{level}>{text}</h{level}>")
                i += 1
                continue

        # Blank line — block separator.
        if not stripped:
            i += 1
            continue

        # Paragraph: gather contiguous non-blank, non-heading, non-fence lines.
        para_lines: list[str] = []
        while i < n:
            line2 = lines[i]
            s2 = line2.strip()
            if not s2:
                break
            if s2.startswith("```"):
                break
            if _HEADING_RE.match(s2):
                break
            para_lines.append(line2)
            i += 1
        joined = " ".join(p.strip() for p in para_lines)
        blocks.append(f"<p>{_render_inline(_escape_html(joined))}</p>")

    return "\n".join(blocks)


def _wrap_html_document(
    *, body_html: str, seo: dict, include_seo: bool
) -> str:
    """Wrap the rendered body in a minimal <html><head><body> shell."""
    title = (seo.get("title") if include_seo else None) or "Untitled"
    description = seo.get("description") if include_seo else None
    tags = seo.get("tags") if include_seo else None
    head_parts: list[str] = [
        '<meta charset="utf-8">',
        f"<title>{_escape_html(title)}</title>",
    ]
    if include_seo and description:
        head_parts.append(
            f'<meta name="description" content="{_escape_html(description)}">'
        )
    if include_seo and tags:
        keywords = ", ".join(tags)
        head_parts.append(
            f'<meta name="keywords" content="{_escape_html(keywords)}">'
        )
    head = "\n  ".join(head_parts)
    return (
        f"<!DOCTYPE html>\n"
        f"<html>\n<head>\n  {head}\n</head>\n<body>\n"
        f"{body_html}\n"
        f"</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# Per-format renderers.
# ---------------------------------------------------------------------------
def _render_markdown(
    *, body: str, seo: dict, exported_at_utc: str,
    include_seo: bool, repurposing_footer: str,
) -> str:
    frontmatter = _yaml_frontmatter(seo, exported_at_utc) if include_seo else ""
    return frontmatter + body + repurposing_footer


def _render_mdx(
    *, body: str, seo: dict, exported_at_utc: str,
    include_seo: bool, repurposing_footer: str,
) -> str:
    frontmatter = _mdx_frontmatter(seo, exported_at_utc) if include_seo else ""
    return frontmatter + body + repurposing_footer


def _render_html(
    *, body: str, seo: dict, include_seo: bool, repurposing_footer: str,
) -> str:
    body_with_footer = body + repurposing_footer
    body_html = _markdown_to_html_body(body_with_footer)
    return _wrap_html_document(body_html=body_html, seo=seo, include_seo=include_seo)


def _render_json(
    *, blog_row: sqlite3.Row, body: str, seo: dict, exported_at_utc: str,
    include_seo: bool, include_repurposing_links: bool,
    linked_posts: list[dict], version_number: int | None,
) -> str:
    body_html = _markdown_to_html_body(body)
    payload: dict = {
        "title": blog_row["title"],
        "slug": blog_row["slug"],
        "status": blog_row["status"],
        "body_markdown": body,
        "body_html": body_html,
        "pillar": blog_row["pillar"],
        "audience": blog_row["audience"],
        "created_at_utc": blog_row["created_at_utc"],
        "exported_at_utc": exported_at_utc,
        "version_number": version_number,
    }
    if include_seo:
        payload["seo"] = {
            "title": seo.get("title"),
            "description": seo.get("description"),
            "tags": seo.get("tags") or [],
        }
    if include_repurposing_links:
        payload["repurposing_links"] = linked_posts
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# Public export()
# ---------------------------------------------------------------------------
def _now_utc_iso(conn: sqlite3.Connection) -> str:
    """Single source of truth for exported_at_utc — uses sqlite datetime('now')
    so audit / export rows / file content all carry the same timestamp."""
    return conn.execute("SELECT datetime('now')").fetchone()[0]


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _allowed_export_root(conn: sqlite3.Connection) -> Path:
    """Return the resolved absolute path of the configured export root.

    Reads the ``blog_export_default_directory`` setting; falls back to
    the migration default ``data/blog_exports/`` (relative to repo
    root). Always resolves to an absolute, fully-resolved path so the
    ``relative_to`` check in :func:`_validate_target_path` cannot be
    fooled by symlinks or ``..`` components in the configured root.
    """
    raw = "data/blog_exports/"
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = 'blog_export_default_directory'"
    ).fetchone()
    if row is not None and row[0]:
        try:
            parsed = json.loads(row[0])
            if isinstance(parsed, str) and parsed.strip():
                raw = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    root = Path(raw)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    # mkdir before resolve so symlink targets exist; the resolve() then
    # collapses symlinks so the relative_to() check is meaningful.
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _validate_target_path(
    conn: sqlite3.Connection, target: Path, *, format: str,
) -> Path:
    """Resolve and constrain ``target`` against the configured export root.

    P6R-3: pre-fix, ``target_path`` went verbatim into ``Path()`` →
    ``mkdir(parents=True)`` + ``os.replace``, so a free-text input of
    ``/etc/hosts`` or ``../../README.md`` would overwrite arbitrary
    files. Post-fix: resolve to an absolute path, then verify it lives
    under :func:`_allowed_export_root`. Path traversal, absolute paths
    outside the root, and ``..``-laden relatives all raise
    :class:`ExportPathOutsideRootError`. Also validates that the
    extension matches the requested format to catch typos.
    """
    allowed_root = _allowed_export_root(conn)
    try:
        # Treat relative target paths as relative to the export root,
        # NOT to the process cwd — Streamlit's cwd is configurable and
        # cwd-relative paths surprise readers.
        candidate = target.expanduser()
        if not candidate.is_absolute():
            candidate = allowed_root / candidate
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise BlogExportError(
            f"could not resolve target_path {target!r}: {exc}"
        ) from exc

    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        raise ExportPathOutsideRootError(
            f"target_path {resolved} is outside the configured export "
            f"root {allowed_root}. Set blog_export_default_directory in "
            "Settings if you want a different root; per-export "
            "out-of-root writes are not permitted."
        )

    expected_exts = _FORMAT_EXTENSIONS.get(format, frozenset())
    if expected_exts and resolved.suffix.lower() not in expected_exts:
        raise ExportPathExtensionMismatchError(
            f"target_path {resolved.name!r} extension does not match "
            f"format={format!r} (expected one of {sorted(expected_exts)})"
        )

    return resolved


def _atomic_write_file(target_path: Path, contents: str) -> int:
    """Write ``contents`` to ``target_path`` via temp-then-rename.

    Returns the file size in bytes. Creates parent dirs on demand.
    The temp file lives in the same directory as the target so the
    rename is atomic on the same filesystem.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = contents.encode("utf-8")
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{target_path.name}.", suffix=".tmp", dir=str(target_path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path_str, str(target_path))
    except Exception:
        # Best-effort cleanup of the temp file if rename failed.
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise
    return len(payload)


def export(
    conn: sqlite3.Connection,
    *,
    blog_id: int,
    format: ExportFormat,
    target_path: str | Path,
    include_seo_metadata: bool = True,
    include_repurposing_links: bool = False,
    daniel_notes: str | None = None,
) -> BlogExport:
    """Render + write + record one blog export.

    Atomicity contract per §28.33:

    1. Render content to bytes.
    2. Write file via temp-then-rename. If this fails, raise
       :class:`ExportFileWriteError` — no DB row is written.
    3. Insert ``blog_exports`` row + ``audit_logs`` row + optional
       status transition in a single transaction. If THIS fails, the
       file exists on disk but no DB row — raise
       :class:`ExportRecordFailedError` so the caller surfaces a
       reconciliation banner. We deliberately do NOT delete the file
       (it represents real work Daniel may want to use).
    """
    if format not in VALID_FORMATS:
        raise InvalidExportFormatError(
            f"unknown format {format!r}. Allowed: {sorted(VALID_FORMATS)}"
        )

    # Read everything we need from the DB BEFORE any file I/O.
    blog_row = conn.execute(
        "SELECT * FROM blogs WHERE id = ?", (blog_id,)
    ).fetchone()
    if blog_row is None:
        raise _blogs.BlogNotFoundError(f"blog #{blog_id} not found")

    current_version = conn.execute(
        """
        SELECT id, version_number
        FROM blog_versions
        WHERE blog_id = ? AND is_current_for_blog = 1
        """,
        (blog_id,),
    ).fetchone()
    current_version_id = int(current_version["id"]) if current_version else None
    current_version_number = (
        int(current_version["version_number"]) if current_version else None
    )

    seo = _seo_data_for_blog(conn, blog_id)
    linked_posts = (
        _linked_posts_summary(conn, blog_id) if include_repurposing_links else []
    )
    repurposing_footer = (
        _repurposing_footer(linked_posts) if include_repurposing_links else ""
    )
    exported_at_utc = _now_utc_iso(conn)
    body = blog_row["current_body_markdown"] or ""

    if format == "markdown":
        contents = _render_markdown(
            body=body, seo=seo, exported_at_utc=exported_at_utc,
            include_seo=include_seo_metadata,
            repurposing_footer=repurposing_footer,
        )
    elif format == "mdx":
        contents = _render_mdx(
            body=body, seo=seo, exported_at_utc=exported_at_utc,
            include_seo=include_seo_metadata,
            repurposing_footer=repurposing_footer,
        )
    elif format == "html":
        contents = _render_html(
            body=body, seo=seo, include_seo=include_seo_metadata,
            repurposing_footer=repurposing_footer,
        )
    else:  # json
        contents = _render_json(
            blog_row=blog_row, body=body, seo=seo,
            exported_at_utc=exported_at_utc,
            include_seo=include_seo_metadata,
            include_repurposing_links=include_repurposing_links,
            linked_posts=linked_posts,
            version_number=current_version_number,
        )

    # P6R-3: constrain target_path to the configured export root BEFORE
    # any filesystem operation. Path-traversal + absolute-out-of-root
    # paths raise ExportPathOutsideRootError; format/extension typos
    # raise ExportPathExtensionMismatchError. No file is written, no
    # DB row is inserted.
    target = _validate_target_path(conn, Path(target_path), format=format)
    try:
        file_size_bytes = _atomic_write_file(target, contents)
    except OSError as exc:
        raise ExportFileWriteError(
            f"file write to {target} failed: {exc}"
        ) from exc

    content_sha256 = _sha256_hex(contents.encode("utf-8"))

    # DB writes — atomic via single transaction.
    try:
        with transaction(conn):
            cur = conn.execute(
                """
                INSERT INTO blog_exports
                  (blog_id, blog_version_id, format, target_path,
                   file_size_bytes, content_sha256,
                   seo_metadata_included, repurposing_links_included,
                   exported_at_utc, daniel_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    blog_id,
                    current_version_id,
                    format,
                    str(target.resolve()),
                    file_size_bytes,
                    content_sha256,
                    1 if include_seo_metadata else 0,
                    1 if include_repurposing_links else 0,
                    exported_at_utc,
                    daniel_notes,
                ),
            )
            export_id = int(cur.fetchone()[0])
            _audit_log.log(
                conn,
                event_category="export",
                event_type=f"blog_export_{format}",
                target_type="blog",
                target_id=blog_id,
                details={
                    "export_id": export_id,
                    "target_path": str(target.resolve()),
                    "file_size_bytes": file_size_bytes,
                    "content_sha256": content_sha256,
                    "seo_metadata_included": include_seo_metadata,
                    "repurposing_links_included": include_repurposing_links,
                    "version_number": current_version_number,
                },
            )
    except sqlite3.Error as exc:
        raise ExportRecordFailedError(str(target.resolve()), original=exc) from exc

    # Auto-transition ready → exported, but ONLY ready → exported.
    # Subsequent re-exports leave status untouched.
    if blog_row["status"] == "ready":
        try:
            _blogs.transition_status(conn, blog_id, "exported")
        except _blogs.BlogError:
            # The export row + audit row already landed; a transition
            # failure shouldn't roll those back. Log and continue.
            _LOG.warning(
                "blog #%d: export succeeded but ready→exported transition failed",
                blog_id,
                exc_info=True,
            )

    return BlogExport(
        id=export_id,
        blog_id=blog_id,
        blog_version_id=current_version_id,
        format=format,
        target_path=str(target.resolve()),
        file_size_bytes=file_size_bytes,
        content_sha256=content_sha256,
        seo_metadata_included=include_seo_metadata,
        repurposing_links_included=include_repurposing_links,
        exported_at_utc=exported_at_utc,
    )


def list_exports(conn: sqlite3.Connection, blog_id: int) -> list[BlogExport]:
    rows = conn.execute(
        """
        SELECT *
        FROM blog_exports
        WHERE blog_id = ?
        ORDER BY exported_at_utc DESC, id DESC
        """,
        (blog_id,),
    ).fetchall()
    return [
        BlogExport(
            id=int(r["id"]),
            blog_id=int(r["blog_id"]),
            blog_version_id=int(r["blog_version_id"]) if r["blog_version_id"] is not None else None,
            format=r["format"],
            target_path=r["target_path"],
            file_size_bytes=int(r["file_size_bytes"]),
            content_sha256=r["content_sha256"],
            seo_metadata_included=bool(r["seo_metadata_included"]),
            repurposing_links_included=bool(r["repurposing_links_included"]),
            exported_at_utc=r["exported_at_utc"],
        )
        for r in rows
    ]
