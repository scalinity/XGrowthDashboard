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


@pytest.fixture(autouse=True)
def _point_exports_root_at_tmp(
    db_conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """P6R-3: blog_exports.export now constrains target_path to the
    configured root. Tests write to ``tmp_path``, so we update the
    setting AND the PROJECT_ROOT-relative fallback for the duration of
    each test."""
    import json as _json
    db_conn.execute(
        "UPDATE settings SET value_json = ? "
        "WHERE key = 'blog_export_default_directory'",
        (_json.dumps(str(tmp_path)),),
    )
    return tmp_path


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


# ---------------------------------------------------------------------------
# P6R-3 — path-traversal regression tests.
# ---------------------------------------------------------------------------
def test_export_rejects_absolute_path_outside_root(
    db_conn: sqlite3.Connection,
) -> None:
    """An absolute target path outside the configured export root
    must raise ExportPathOutsideRootError; no file written, no DB row."""
    blog_id = _seed_blog_with_body(db_conn)
    # /tmp is not the configured tmp_path root (autouse fixture points
    # the root at tmp_path), so /tmp/escape.md is outside.
    bad_path = "/tmp/p6r3_should_not_exist.md"
    with pytest.raises(be.ExportPathOutsideRootError):
        be.export(db_conn, blog_id=blog_id, format="markdown",
                  target_path=bad_path)
    assert not Path(bad_path).exists()
    assert db_conn.execute(
        "SELECT COUNT(*) FROM blog_exports WHERE blog_id = ?", (blog_id,)
    ).fetchone()[0] == 0


def test_export_rejects_dotdot_traversal(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """Relative paths with .. that escape the root must raise."""
    blog_id = _seed_blog_with_body(db_conn)
    with pytest.raises(be.ExportPathOutsideRootError):
        be.export(db_conn, blog_id=blog_id, format="markdown",
                  target_path="../escape.md")
    # And /etc/passwd-shape attempt.
    with pytest.raises(be.ExportPathOutsideRootError):
        be.export(db_conn, blog_id=blog_id, format="markdown",
                  target_path="/etc/p6r3_should_not_exist.md")
    assert db_conn.execute(
        "SELECT COUNT(*) FROM blog_exports WHERE blog_id = ?", (blog_id,)
    ).fetchone()[0] == 0


def test_export_rejects_extension_mismatch(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """format='markdown' + target_path='out.json' must raise."""
    blog_id = _seed_blog_with_body(db_conn)
    with pytest.raises(be.ExportPathExtensionMismatchError):
        be.export(db_conn, blog_id=blog_id, format="markdown",
                  target_path=tmp_path / "wrong_suffix.json")
    assert db_conn.execute(
        "SELECT COUNT(*) FROM blog_exports WHERE blog_id = ?", (blog_id,)
    ).fetchone()[0] == 0


def test_html_export_does_not_double_escape_link_url(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """P6R-4: previously _render_inline re-escaped captured groups
    that were already HTML-escaped by _markdown_to_html_body. URLs
    containing & became ?a=1&amp;amp;b=2 in the rendered href —
    browsers displayed literal &amp; and the link broke."""
    b = bm.create_blog(db_conn, title="link-test")
    bm.save_blog(
        db_conn, b.id,
        body_markdown="See [docs](https://example.com?a=1&b=2) for more.",
        created_by="daniel",
    )
    for s in ("outlining", "drafting", "editing", "ready"):
        bm.transition_status(db_conn, b.id, s)
    target = tmp_path / "link.html"
    be.export(db_conn, blog_id=b.id, format="html", target_path=target)
    html = target.read_text(encoding="utf-8")
    # The href must carry a SINGLE-escaped & (i.e. &amp;), not the
    # double-escaped &amp;amp;.
    assert 'href="https://example.com?a=1&amp;b=2"' in html
    assert "&amp;amp;" not in html


def test_html_export_strips_javascript_link_scheme(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """P6R-6: javascript:/data:/file: URLs in markdown links must be
    collapsed to # in the exported HTML."""
    b = bm.create_blog(db_conn, title="js-link")
    bm.save_blog(
        db_conn, b.id,
        body_markdown=(
            "Bad [click here](javascript:alert(1)) and "
            "[data link](data:text/html,<h1>x</h1>) and "
            "[file](file:///etc/passwd) — all should be neutralized.\n\n"
            "Good [docs](https://example.com) survives.\n\n"
            "Relative [anchor](#section) and [mailto](mailto:a@b.com) also survive."
        ),
        created_by="daniel",
    )
    for s in ("outlining", "drafting", "editing", "ready"):
        bm.transition_status(db_conn, b.id, s)
    target = tmp_path / "js.html"
    be.export(db_conn, blog_id=b.id, format="html", target_path=target)
    html = target.read_text(encoding="utf-8")
    # Bad schemes: replaced with "#".
    assert 'href="javascript:' not in html
    assert 'href="data:' not in html
    assert 'href="file:' not in html
    # Good schemes survive.
    assert 'href="https://example.com"' in html
    assert 'href="#section"' in html
    assert 'href="mailto:a@b.com"' in html


def test_safe_href_helper_unit() -> None:
    """Pin _safe_href's allow-list behavior independent of the export
    pipeline."""
    from app.agent.blog_exports import _safe_href
    # Allowed.
    assert _safe_href("https://example.com") == "https://example.com"
    assert _safe_href("HTTPS://EXAMPLE.COM") == "HTTPS://EXAMPLE.COM"
    assert _safe_href("mailto:a@b.com") == "mailto:a@b.com"
    assert _safe_href("/local/path") == "/local/path"
    assert _safe_href("#anchor") == "#anchor"
    assert _safe_href("?q=1") == "?q=1"
    assert _safe_href("./relative") == "./relative"
    assert _safe_href("../up") == "../up"
    assert _safe_href("plain-path") == "plain-path"
    # Refused — collapse to "#".
    assert _safe_href("javascript:alert(1)") == "#"
    assert _safe_href("JaVaScRiPt:x") == "#"
    assert _safe_href("data:text/html,<h1>x</h1>") == "#"
    assert _safe_href("file:///etc/passwd") == "#"
    assert _safe_href("vbscript:msgbox") == "#"
    assert _safe_href("") == "#"


def test_html_export_does_not_double_escape_inline_code(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """P6R-4: inline code spans containing reserved chars previously
    rendered as <code>&amp;lt;foo&amp;gt;</code> (visible &lt; in
    browser) instead of <code>&lt;foo&gt;</code>."""
    b = bm.create_blog(db_conn, title="code-test")
    bm.save_blog(
        db_conn, b.id,
        body_markdown="Use the `<Foo>` tag carefully.",
        created_by="daniel",
    )
    for s in ("outlining", "drafting", "editing", "ready"):
        bm.transition_status(db_conn, b.id, s)
    target = tmp_path / "code.html"
    be.export(db_conn, blog_id=b.id, format="html", target_path=target)
    html = target.read_text(encoding="utf-8")
    assert "<code>&lt;Foo&gt;</code>" in html
    assert "&amp;lt;" not in html


def test_export_accepts_relative_path_inside_root(
    db_conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    """Relative target paths resolve against the configured root, not
    the process cwd. Daniel typing `mypost.md` writes to
    `<allowed_root>/mypost.md`, not to wherever Streamlit happens to be
    running from."""
    blog_id = _seed_blog_with_body(db_conn)
    result = be.export(db_conn, blog_id=blog_id, format="markdown",
                       target_path="relative_inside.md")
    expected = (tmp_path / "relative_inside.md").resolve()
    assert Path(result.target_path) == expected
    assert expected.exists()
