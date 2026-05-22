"""Unit tests for ``app/agent/blog_exports.py`` — Phase 6 §28.33.

Covers all four formats (markdown / html / json / mdx), the atomic
write-then-record contract, the ready→exported auto-transition,
SHA256 audit anchor, partial-state path when the DB insert fails,
and the re-export-appends-row behavior.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agent import blog_exports as be
from app.agent import blogs as bm


def _seed_blog_with_body(
    db_conn: sqlite3.Connection, *, body: str = "# Title\n\nFirst paragraph.\n\n## Section\n\nMore text.",
    status: str = "ready",
) -> int:
    b = bm.create_blog(db_conn, title="export me")
    # Walk to the requested status via legal edges so the state
    # machine is satisfied.
    path = {
        "idea": [],
        "outlining": ["outlining"],
        "drafting": ["outlining", "drafting"],
        "editing": ["outlining", "drafting", "editing"],
        "ready": ["outlining", "drafting", "editing", "ready"],
        "exported": ["outlining", "drafting", "editing", "ready", "exported"],
    }[status]
    bm.save_blog(db_conn, b.id, body_markdown=body, created_by="daniel")
    for s in path:
        bm.transition_status(db_conn, b.id, s)
    bm.set_seo_metadata(
        db_conn, b.id,
        seo_title="Export Title",
        seo_description="d" * 130,
        seo_tags=["a", "b"],
    )
    return b.id


# ---------------------------------------------------------------------------
# Format renderers.
# ---------------------------------------------------------------------------
def test_export_markdown_writes_file_and_inserts_row(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "out.md"
    result = be.export(
        db_conn, blog_id=blog_id, format="markdown", target_path=target
    )
    assert target.exists()
    contents = target.read_text(encoding="utf-8")
    assert contents.startswith("---\n")
    assert "Export Title" in contents
    assert "First paragraph." in contents
    # DB row exists with matching content hash.
    row = db_conn.execute(
        "SELECT format, content_sha256, file_size_bytes FROM blog_exports "
        "WHERE id = ?", (result.id,)
    ).fetchone()
    assert row["format"] == "markdown"
    assert row["content_sha256"] == result.content_sha256
    assert row["file_size_bytes"] == len(contents.encode("utf-8"))


def test_export_markdown_content_hash_matches_file(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "hash.md"
    result = be.export(db_conn, blog_id=blog_id, format="markdown", target_path=target)
    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.content_sha256 == expected


def test_export_html_includes_seo_meta_when_requested(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "out.html"
    be.export(
        db_conn, blog_id=blog_id, format="html", target_path=target,
        include_seo_metadata=True,
    )
    html = target.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert '<meta name="description"' in html
    assert "<h1>Title</h1>" in html
    assert "<h2>Section</h2>" in html
    assert "<p>First paragraph.</p>" in html


def test_export_html_omits_seo_meta_when_disabled(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "out2.html"
    be.export(
        db_conn, blog_id=blog_id, format="html", target_path=target,
        include_seo_metadata=False,
    )
    html = target.read_text(encoding="utf-8")
    assert '<meta name="description"' not in html
    assert '<meta name="keywords"' not in html


def test_export_json_carries_body_html_and_seo(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "out.json"
    be.export(db_conn, blog_id=blog_id, format="json", target_path=target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "body_markdown" in payload
    assert "body_html" in payload
    assert "<p>First paragraph.</p>" in payload["body_html"]
    assert payload["seo"]["title"] == "Export Title"
    assert payload["seo"]["tags"] == ["a", "b"]
    assert payload["version_number"] is not None


def test_export_mdx_uses_export_const_meta(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "out.mdx"
    be.export(db_conn, blog_id=blog_id, format="mdx", target_path=target)
    mdx = target.read_text(encoding="utf-8")
    assert mdx.startswith("export const meta =")
    assert '"title": "Export Title"' in mdx
    assert "First paragraph." in mdx


def test_export_unknown_format_rejected(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    with pytest.raises(be.InvalidExportFormatError):
        be.export(db_conn, blog_id=blog_id, format="rst",  # type: ignore[arg-type]
                  target_path=tmp_path / "x.rst")


def test_export_unknown_blog_rejected(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    with pytest.raises(bm.BlogNotFoundError):
        be.export(db_conn, blog_id=99_999, format="markdown",
                  target_path=tmp_path / "missing.md")


# ---------------------------------------------------------------------------
# Status auto-transition.
# ---------------------------------------------------------------------------
def test_export_auto_transitions_ready_to_exported(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn, status="ready")
    assert bm.get_blog(db_conn, blog_id).status == "ready"
    be.export(db_conn, blog_id=blog_id, format="markdown", target_path=tmp_path / "a.md")
    assert bm.get_blog(db_conn, blog_id).status == "exported"


def test_re_export_does_not_change_status_back(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn, status="exported")
    assert bm.get_blog(db_conn, blog_id).status == "exported"
    be.export(db_conn, blog_id=blog_id, format="markdown", target_path=tmp_path / "b.md")
    assert bm.get_blog(db_conn, blog_id).status == "exported"


def test_re_export_appends_row_and_overwrites_file(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "overwrite.md"
    first = be.export(db_conn, blog_id=blog_id, format="markdown", target_path=target)
    # Mutate body, re-export — file overwritten, row appended.
    bm.save_blog(db_conn, blog_id, body_markdown="# New title\n\nNew body.",
                 created_by="daniel")
    second = be.export(db_conn, blog_id=blog_id, format="markdown", target_path=target)
    assert second.id != first.id
    assert second.content_sha256 != first.content_sha256
    rows = db_conn.execute(
        "SELECT id, content_sha256 FROM blog_exports WHERE blog_id = ? "
        "ORDER BY exported_at_utc ASC", (blog_id,),
    ).fetchall()
    assert len(rows) == 2
    # File contents reflect the second export.
    assert "New body." in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Audit log row + repurposing footer.
# ---------------------------------------------------------------------------
def test_export_writes_audit_log_row(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    be.export(db_conn, blog_id=blog_id, format="markdown", target_path=tmp_path / "a.md")
    row = db_conn.execute(
        """
        SELECT event_category, event_type FROM audit_logs
        WHERE event_type = 'blog_export_markdown' AND target_id = ?
        """,
        (str(blog_id),),
    ).fetchone()
    assert row is not None
    assert row["event_category"] == "export"


def test_repurposing_footer_included_when_requested(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    # Seed a linked X post.
    post_id = db_conn.execute(
        """
        INSERT INTO posts (created_date, text, type, posted_via,
                           manual_confirmation_status, x_post_id, published_to_x_at)
        VALUES ('2026-05-22', 'derived thread root', 'standalone', 'manual',
                'confirmed', '12345', '2026-05-22T10:00:00')
        RETURNING id
        """
    ).fetchone()[0]
    db_conn.execute(
        """
        INSERT INTO blog_to_post_links
          (blog_id, post_id, direction, relationship_kind)
        VALUES (?, ?, 'blog_to_post', 'thread_root')
        """,
        (blog_id, post_id),
    )
    target = tmp_path / "with_footer.md"
    be.export(
        db_conn, blog_id=blog_id, format="markdown",
        target_path=target, include_repurposing_links=True,
    )
    contents = target.read_text(encoding="utf-8")
    assert "Repurposing notes" in contents
    assert "derived thread root" in contents


def test_repurposing_footer_omitted_by_default(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "no_footer.md"
    be.export(db_conn, blog_id=blog_id, format="markdown", target_path=target)
    contents = target.read_text(encoding="utf-8")
    assert "Repurposing notes" not in contents


# ---------------------------------------------------------------------------
# Atomic / partial-state contract.
# ---------------------------------------------------------------------------
def test_file_write_failure_raises_and_writes_no_row(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    # Point target_path at a non-existent + non-writable parent. After
    # mkdir(parents=True) handles the missing-dir case, force an OSError
    # by patching the temp-write path to raise.
    with patch("app.agent.blog_exports._atomic_write_file", side_effect=OSError("disk full")):
        with pytest.raises(be.ExportFileWriteError):
            be.export(db_conn, blog_id=blog_id, format="markdown",
                      target_path=tmp_path / "wont_exist.md")
    count = db_conn.execute(
        "SELECT COUNT(*) FROM blog_exports WHERE blog_id = ?", (blog_id,)
    ).fetchone()[0]
    assert count == 0


def test_db_insert_failure_preserves_file_and_raises_record_failed(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    target = tmp_path / "orphan.md"

    # Simulate a DB-side failure AFTER the file write by patching the
    # module-level audit_log.log to raise — it's called inside the
    # blog_exports INSERT transaction, so the simulated failure rolls
    # back the export row AND the audit row together (atomic), leaving
    # only the file on disk.
    with patch(
        "app.agent.blog_exports._audit_log.log",
        side_effect=sqlite3.OperationalError("simulated DB failure"),
    ):
        with pytest.raises(be.ExportRecordFailedError) as ei:
            be.export(db_conn, blog_id=blog_id, format="markdown",
                      target_path=target)
    # File must still exist — Daniel's work isn't deleted.
    assert target.exists()
    assert str(target.resolve()) == ei.value.target_path
    # No DB row for this export.
    count = db_conn.execute(
        "SELECT COUNT(*) FROM blog_exports WHERE blog_id = ?", (blog_id,)
    ).fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# list_exports + accessor helpers.
# ---------------------------------------------------------------------------
def test_list_exports_returns_newest_first(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    blog_id = _seed_blog_with_body(db_conn)
    be.export(db_conn, blog_id=blog_id, format="markdown", target_path=tmp_path / "1.md")
    be.export(db_conn, blog_id=blog_id, format="json", target_path=tmp_path / "1.json")
    be.export(db_conn, blog_id=blog_id, format="html", target_path=tmp_path / "1.html")
    exports = be.list_exports(db_conn, blog_id=blog_id)
    assert len(exports) == 3
    # IDs should be DESC by exported_at_utc; the latest insertion
    # should be first.
    assert exports[0].id > exports[1].id > exports[2].id
