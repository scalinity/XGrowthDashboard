"""Long-form blog production discipline — Phase 6 §28.31.

Schema lives in ``migrations/016_blogs.sql``. This module is the
single application-layer entry point for every blog mutation: row
creation, content saves, status transitions, and revert-to-version.
The SQL CHECK constraints only validate column shape; legal
*transitions* between status values can only be enforced application-
side because SQLite CHECKs can't see prior row state. Every state
transition lives in :func:`transition_status` — UI affordances are
window dressing, not enforcement.

State machine (§28.31, load-bearing):

    idea                → outlining | archived
    outlining           → drafting | idea | archived
    drafting            → editing | outlining | archived
    editing             → ready | drafting | archived
    ready               → exported | editing | archived
    exported            → published_externally | ready | archived
    published_externally → archived
    archived            → (terminal — no forward transitions)

Why these edges:

* ``idea → outlining`` rather than directly to drafting — the outline
  is a separate artifact preserved through drafting so Daniel can
  compare draft to plan.
* ``drafting → outlining`` and ``editing → drafting`` are deliberate
  backward edges. Sometimes work reveals an earlier step was wrong.
* ``ready → exported`` is set by the export path on success, NOT by
  manual transition. Callers should not pass ``new_status='exported'``
  to :func:`transition_status` from app code other than the export
  module; the public surface still permits it (the export path needs
  it), but the export module is the only intended caller for that
  specific edge.
* ``exported → published_externally`` is the ONE manual transition
  that depends on an out-of-app fact (Daniel actually publishing).
  The editor enforces a populated ``external_url`` at this transition.
* ``archived`` is terminal. Re-activating a blog means duplicating it.

Versioning discipline:

Every content-changing save appends one ``blog_versions`` row. No-op
saves (body_text_hash AND outline_markdown_at_version AND
title_at_version AND status_at_version all match the current version)
skip the append. The partial unique index on ``(blog_id) where
is_current_for_blog = 1`` keeps the current-pointer honest — flipping
the pointer requires demoting the prior current row in the same
transaction.

Reverting to an older version is forward-only history: a NEW version
row with the older body but a fresh ``version_number``. The older
row's ``is_current_for_blog`` is NOT flipped back. This makes
"undo" auditable — the timeline shows you went back, not that you
silently rewrote history.

Audit log (§28.30):

Every state transition writes one ``audit_logs`` row via
:mod:`app.agent.audit_log`. Save-without-transition does not (the
``blog_versions`` row IS the per-save provenance ledger; mirroring
it into ``audit_logs`` would double-write without adding signal).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import urlparse

from app.agent import audit_log as _audit_log
from app.db import transaction


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------
class BlogError(RuntimeError):
    """Base for blogs-module errors."""


class BlogNotFoundError(BlogError):
    """Raised when a ``blog_id`` doesn't resolve."""


class BlogVersionNotFoundError(BlogError):
    """Raised when a ``version_id`` doesn't resolve to a row for the blog."""


class InvalidStatusTransitionError(BlogError):
    """Raised when a state machine move isn't permitted (§28.31)."""

    def __init__(self, blog_id: int, current: str, requested: str) -> None:
        self.blog_id = blog_id
        self.current = current
        self.requested = requested
        super().__init__(
            f"blog #{blog_id}: illegal status transition "
            f"{current!r} → {requested!r} (see §28.31 state machine)"
        )


class InvalidBlogFieldError(BlogError):
    """Raised on bad input — empty title, invalid slug, etc."""


# ---------------------------------------------------------------------------
# State machine reference.
# ---------------------------------------------------------------------------
VALID_STATUSES: frozenset[str] = frozenset(
    {
        "idea", "outlining", "drafting", "editing",
        "ready", "exported", "published_externally", "archived",
    }
)

VALID_AGENT_ACTIONS: frozenset[str] = frozenset(
    {
        "outline", "draft", "edit_suggestion_applied", "seo_metadata",
        # P6R-17: distinguish the seed outline produced by X→blog
        # repurposing from the standalone outline_blog tool — both
        # write a version row with agent_action, but the provenance
        # differs and analytics needs to disambiguate.
        "x_to_blog_idea_outline",
    }
)

# (from_status, to_status) — every transition the state machine permits.
# ``archived`` is terminal: nothing leaves it.
_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("idea", "outlining"),
        ("idea", "archived"),
        ("outlining", "drafting"),
        ("outlining", "idea"),
        ("outlining", "archived"),
        ("drafting", "editing"),
        ("drafting", "outlining"),
        ("drafting", "archived"),
        ("editing", "ready"),
        ("editing", "drafting"),
        ("editing", "archived"),
        ("ready", "exported"),
        ("ready", "editing"),
        ("ready", "archived"),
        ("exported", "published_externally"),
        ("exported", "ready"),
        ("exported", "archived"),
        ("published_externally", "archived"),
    }
)


def is_legal_transition(current: str, requested: str) -> bool:
    """Pure check — does the state machine permit ``current → requested``?

    Returns ``False`` if either side is unknown OR the edge is not in
    the allowed set. Identity transitions (``x → x``) return ``False``
    too — re-issuing the same status is not a state change.
    """
    if current not in VALID_STATUSES or requested not in VALID_STATUSES:
        return False
    return (current, requested) in _TRANSITIONS


# ---------------------------------------------------------------------------
# Dataclasses.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Blog:
    id: int
    slug: str
    title: str
    status: str
    pillar: str | None
    audience: str | None
    current_body_markdown: str
    outline_markdown: str | None
    actual_length_words: int
    target_length_words: int | None
    agent_assisted: bool
    created_at_utc: str
    updated_at_utc: str


@dataclass(frozen=True, slots=True)
class BlogVersion:
    id: int
    blog_id: int
    version_number: int
    body_markdown: str
    body_text_hash: str
    title_at_version: str
    outline_markdown_at_version: str | None
    status_at_version: str
    created_by: Literal["daniel", "agent"]
    agent_action: str | None
    confidence_label_at_version: str | None
    is_current_for_blog: bool
    daniel_revision_note: str | None
    created_at_utc: str


# ---------------------------------------------------------------------------
# Slug + hash helpers.
# ---------------------------------------------------------------------------
_SLUG_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LENGTH = 80


def _normalize_slug(title: str) -> str:
    """Lowercase + ASCII-only + kebab-case truncation of ``title``.

    Empty / all-punctuation titles fall back to ``'blog'`` so the slug
    is always non-empty. Disambiguation against existing rows (``-N``
    suffix) is done by the caller in :func:`create_blog` because it
    needs DB access.

    P6R-23: NFKD-normalize before the ASCII strip so non-Latin titles
    transliterate to their ASCII-decomposable form when possible
    (``café`` → ``cafe``, ``naïve`` → ``naive``, ``résumé`` → ``resume``).
    Titles with no ASCII-decomposable form (CJK ideographs, Devanagari,
    Arabic, etc.) still fall through to ``'blog'``, but a Latin-derived
    title with accent marks now produces a useful slug instead of
    collapsing to the fallback.
    """
    # NFKD decomposes accented characters into base+combining-mark
    # pairs; encode-then-decode through ASCII with errors='ignore'
    # drops the combining marks, leaving the unaccented base letter.
    s = (
        unicodedata.normalize("NFKD", title)
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
        .lower()
    )
    s = _SLUG_NORMALIZE_RE.sub("-", s)
    s = s.strip("-")
    if not s:
        s = "blog"
    if len(s) > _MAX_SLUG_LENGTH:
        s = s[:_MAX_SLUG_LENGTH].rstrip("-")
    return s or "blog"


def _resolve_unique_slug(conn: sqlite3.Connection, base_slug: str) -> str:
    """Append ``-2``, ``-3``, … until the slug doesn't collide.

    Called inside the same transaction as the ``blogs`` insert so two
    concurrent ``create_blog`` calls can't race to the same slug. In
    single-user MVP this is theoretical, but the discipline matters.
    """
    existing = {
        row[0] for row in conn.execute(
            "SELECT slug FROM blogs WHERE slug = ? OR slug LIKE ?",
            (base_slug, f"{base_slug}-%"),
        ).fetchall()
    }
    if base_slug not in existing:
        return base_slug
    counter = 2
    while True:
        candidate = f"{base_slug}-{counter}"
        if candidate not in existing:
            return candidate
        counter += 1


def _hash_body(body_markdown: str) -> str:
    return hashlib.sha256(body_markdown.encode("utf-8")).hexdigest()


def _count_words(body_markdown: str) -> int:
    """Token-style word count for ``actual_length_words``.

    Splits on whitespace after stripping Markdown headers/fences would
    be more accurate; the spec uses ``actual_length_words`` as an
    *informational* gauge against ``target_length_words``, so a
    cheap-and-fast split is plenty. Re-implement later if needed.
    """
    return len(body_markdown.split()) if body_markdown else 0


# ---------------------------------------------------------------------------
# Internal lookups.
# ---------------------------------------------------------------------------
def _fetch_blog_row(conn: sqlite3.Connection, blog_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM blogs WHERE id = ?", (blog_id,)
    ).fetchone()
    if row is None:
        raise BlogNotFoundError(f"blog #{blog_id} not found")
    return row


def _fetch_current_version(
    conn: sqlite3.Connection, blog_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM blog_versions
        WHERE blog_id = ? AND is_current_for_blog = 1
        """,
        (blog_id,),
    ).fetchone()


def _max_version_number(conn: sqlite3.Connection, blog_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) FROM blog_versions WHERE blog_id = ?",
        (blog_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def _row_to_version(row: sqlite3.Row) -> BlogVersion:
    return BlogVersion(
        id=int(row["id"]),
        blog_id=int(row["blog_id"]),
        version_number=int(row["version_number"]),
        body_markdown=row["body_markdown"] or "",
        body_text_hash=row["body_text_hash"],
        title_at_version=row["title_at_version"],
        outline_markdown_at_version=row["outline_markdown_at_version"],
        status_at_version=row["status_at_version"],
        created_by=row["created_by"],
        agent_action=row["agent_action"],
        confidence_label_at_version=row["confidence_label_at_version"],
        is_current_for_blog=bool(row["is_current_for_blog"]),
        daniel_revision_note=row["daniel_revision_note"],
        created_at_utc=row["created_at_utc"],
    )


def _row_to_blog(row: sqlite3.Row) -> Blog:
    return Blog(
        id=int(row["id"]),
        slug=row["slug"],
        title=row["title"],
        status=row["status"],
        pillar=row["pillar"],
        audience=row["audience"],
        current_body_markdown=row["current_body_markdown"] or "",
        outline_markdown=row["outline_markdown"],
        actual_length_words=int(row["actual_length_words"]),
        target_length_words=(
            int(row["target_length_words"])
            if row["target_length_words"] is not None
            else None
        ),
        agent_assisted=bool(row["agent_assisted"]),
        created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
    )


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def get_blog(conn: sqlite3.Connection, blog_id: int) -> Blog:
    """Return one ``Blog`` by id, or raise :class:`BlogNotFoundError`."""
    return _row_to_blog(_fetch_blog_row(conn, blog_id))


def list_blogs(
    conn: sqlite3.Connection, statuses: Optional[list[str]] = None
) -> list[dict]:
    """List rows from ``v_blog_pipeline`` (newest-edited first by default).

    If ``statuses`` is non-empty, filter to that set. View columns are
    returned as a list of dicts so the caller (UI) can render without
    converting to dataclasses — the view's column set is wider than
    :class:`Blog`.
    """
    if statuses:
        bad = {s for s in statuses if s not in VALID_STATUSES}
        if bad:
            raise InvalidBlogFieldError(f"unknown statuses: {sorted(bad)}")
        placeholders = ",".join(["?"] * len(statuses))
        sql = (
            f"SELECT * FROM v_blog_pipeline "
            f"WHERE status IN ({placeholders}) "
            "ORDER BY last_edited_at_utc DESC NULLS LAST, blog_id DESC"
        )
        params: tuple = tuple(statuses)
    else:
        sql = (
            "SELECT * FROM v_blog_pipeline "
            "ORDER BY last_edited_at_utc DESC NULLS LAST, blog_id DESC"
        )
        params = ()
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def create_blog(
    conn: sqlite3.Connection,
    *,
    title: str,
    pillar: str | None = None,
    audience: str | None = None,
    target_length_words: int | None = None,
    notes: str | None = None,
    niche_problem_snapshot: str | None = None,
    niche_person_snapshot: str | None = None,
) -> Blog:
    """Insert a new blog with ``status='idea'`` + an empty version 1 row.

    Slug is derived from ``title`` (lowercase, kebab-case, ASCII-only)
    and disambiguated against the existing ``blogs.slug`` set inside the
    same transaction. Version 1 is the immutable epistemic anchor — every
    subsequent save is a *change* relative to it, including the first
    real content edit.

    ``niche_problem_snapshot`` / ``niche_person_snapshot`` are optional
    snapshot columns that freeze the identity context the blog was
    authored under (P6R-5: previously the X→blog repurposing flow wrote
    these in a SECOND transaction after the create, leaving a window
    where a crash could orphan a blog with NULL snapshots; we now write
    them in the same transaction as the initial insert).
    """
    title_clean = (title or "").strip()
    if not title_clean:
        raise InvalidBlogFieldError("title is required and cannot be empty")
    if target_length_words is not None and target_length_words <= 0:
        raise InvalidBlogFieldError("target_length_words must be > 0")

    base_slug = _normalize_slug(title_clean)
    empty_body = ""
    body_hash = _hash_body(empty_body)

    with transaction(conn):
        slug = _resolve_unique_slug(conn, base_slug)
        cur = conn.execute(
            """
            INSERT INTO blogs
              (slug, title, current_body_markdown, status, pillar, audience,
               target_length_words, actual_length_words, notes,
               niche_problem_snapshot, niche_person_snapshot)
            VALUES (?, ?, '', 'idea', ?, ?, ?, 0, ?, ?, ?)
            RETURNING id
            """,
            (
                slug, title_clean, pillar, audience, target_length_words,
                notes, niche_problem_snapshot, niche_person_snapshot,
            ),
        )
        blog_id = int(cur.fetchone()[0])
        conn.execute(
            """
            INSERT INTO blog_versions
              (blog_id, version_number, body_markdown, body_text_hash,
               title_at_version, outline_markdown_at_version,
               status_at_version, created_by, is_current_for_blog)
            VALUES (?, 1, '', ?, ?, NULL, 'idea', 'daniel', 1)
            """,
            (blog_id, body_hash, title_clean),
        )
        _audit_log.log(
            conn,
            event_category="data",
            event_type="blog_created",
            target_type="blog",
            target_id=blog_id,
            details={"slug": slug, "title": title_clean},
        )

    return get_blog(conn, blog_id)


def save_blog(
    conn: sqlite3.Connection,
    blog_id: int,
    *,
    body_markdown: str | None = None,
    outline_markdown: str | None = None,
    title: str | None = None,
    status: str | None = None,
    created_by: Literal["daniel", "agent"] = "daniel",
    agent_message_id: int | None = None,
    agent_action: str | None = None,
    confidence_label_at_version: str | None = None,
    daniel_revision_note: str | None = None,
) -> BlogVersion | None:
    """Atomic save: updates ``blogs`` columns + appends ``blog_versions`` row.

    Only the fields the caller supplies are touched; ``None`` means
    "leave unchanged". The new version row carries the *resulting*
    state of every snapshot column — so ``status_at_version`` reflects
    the post-save status whether or not the caller passed it.

    No-op detection: when ``body_text_hash`` AND ``outline_markdown_at_version``
    AND ``title_at_version`` AND ``status_at_version`` all match the
    current version, NO version row is appended. The ``blogs`` row's
    ``updated_at_utc`` is still bumped (a save attempt is itself an
    event, even if no content changed). Returns ``None`` in the no-op
    case so callers can branch.

    The save runs in a single transaction. The flow:

    1. Read current blog row + current version row.
    2. Compute resulting values for body / outline / title / status.
    3. If everything matches the current version, bump
       ``updated_at_utc`` and return ``None``.
    4. Otherwise: demote prior current version, insert new version row,
       update the ``blogs`` row's columns + ``actual_length_words`` +
       ``updated_at_utc``, and set the new version as current.
    """
    if agent_action is not None and agent_action not in VALID_AGENT_ACTIONS:
        raise InvalidBlogFieldError(
            f"unknown agent_action: {agent_action!r}. "
            f"Allowed: {sorted(VALID_AGENT_ACTIONS)}."
        )
    if status is not None and status not in VALID_STATUSES:
        raise InvalidBlogFieldError(f"unknown status: {status!r}")
    if created_by not in {"daniel", "agent"}:
        raise InvalidBlogFieldError(f"unknown created_by: {created_by!r}")

    with transaction(conn):
        new_version_id = _save_blog_in_tx(
            conn,
            blog_id,
            body_markdown=body_markdown,
            outline_markdown=outline_markdown,
            title=title,
            status=status,
            created_by=created_by,
            agent_message_id=agent_message_id,
            agent_action=agent_action,
            confidence_label_at_version=confidence_label_at_version,
            daniel_revision_note=daniel_revision_note,
        )

    if new_version_id is None:
        return None

    row = conn.execute(
        "SELECT * FROM blog_versions WHERE id = ?", (new_version_id,)
    ).fetchone()
    return _row_to_version(row)


def _save_blog_in_tx(
    conn: sqlite3.Connection,
    blog_id: int,
    *,
    body_markdown: str | None = None,
    outline_markdown: str | None = None,
    title: str | None = None,
    status: str | None = None,
    created_by: Literal["daniel", "agent"] = "daniel",
    agent_message_id: int | None = None,
    agent_action: str | None = None,
    confidence_label_at_version: str | None = None,
    daniel_revision_note: str | None = None,
) -> int | None:
    """Same as :func:`save_blog` but assumes the caller already holds an
    open transaction. Returns the new ``blog_versions.id`` or ``None`` on
    no-op. The public :func:`save_blog` wraps this in ``with transaction(conn):``;
    callers that need to compose this with other writes (e.g.
    :func:`transition_status`, :func:`revert_to_version`) call this
    inner helper inside their OWN single transaction so the version row +
    surrounding writes commit together.

    Re-validates ``agent_action`` / ``status`` / ``created_by`` defensively
    because composing callers might pass through without going via
    :func:`save_blog`'s argument-validation gate.
    """
    if agent_action is not None and agent_action not in VALID_AGENT_ACTIONS:
        raise InvalidBlogFieldError(
            f"unknown agent_action: {agent_action!r}. "
            f"Allowed: {sorted(VALID_AGENT_ACTIONS)}."
        )
    if status is not None and status not in VALID_STATUSES:
        raise InvalidBlogFieldError(f"unknown status: {status!r}")
    if created_by not in {"daniel", "agent"}:
        raise InvalidBlogFieldError(f"unknown created_by: {created_by!r}")

    blog_row = _fetch_blog_row(conn, blog_id)
    current = _fetch_current_version(conn, blog_id)

    # Resulting values (caller-supplied OR carried-forward).
    new_body = (
        body_markdown
        if body_markdown is not None
        else (blog_row["current_body_markdown"] or "")
    )
    new_outline = (
        outline_markdown
        if outline_markdown is not None
        else blog_row["outline_markdown"]
    )
    new_title = title if title is not None else blog_row["title"]
    new_status = status if status is not None else blog_row["status"]

    new_body_hash = _hash_body(new_body)
    new_length = _count_words(new_body)

    # No-op detection — all four content/identity columns unchanged.
    # P6R-11: compare new_outline and current outline DIRECTLY, NOT via
    # `... or None` coalescence. Pre-fix, "" and None were treated as
    # equal — so clearing a prior `"# Old"` outline to "" was falsely
    # detected as no-op. None semantically means "don't change"; ""
    # semantically means "set to empty string", and those must be
    # distinguished.
    if current is not None:
        unchanged = (
            new_body_hash == current["body_text_hash"]
            and new_outline == current["outline_markdown_at_version"]
            and new_title == current["title_at_version"]
            and new_status == current["status_at_version"]
        )
        if unchanged:
            # P6R-10: drop the no-op updated_at_utc bump — v_blog_pipeline
            # reads last_edited_at from MAX(blog_versions.created_at_utc),
            # so updated_at_utc was dead-code for the surfaced UI.
            return None

    # Demote previous current.
    if current is not None:
        conn.execute(
            "UPDATE blog_versions SET is_current_for_blog = 0 WHERE id = ?",
            (current["id"],),
        )

    next_version_number = _max_version_number(conn, blog_id) + 1
    # P6R-22: agent_assisted is STICKY — once an agent draft/outline/edit
    # touches the blog, the flag stays 1 even if Daniel rewrites every
    # subsequent version manually. This is intentional disclosure: a
    # reader of the exported blog should know any version of it was
    # ever AI-touched, not just the current one. Recomputing "is any
    # current version still agent-touched?" would let the disclosure
    # disappear with a single manual save, which defeats the point.
    agent_assisted_new = (
        1 if (blog_row["agent_assisted"] or created_by == "agent") else 0
    )

    conn.execute(
        """
        UPDATE blogs
        SET current_body_markdown = ?,
            outline_markdown = ?,
            title = ?,
            status = ?,
            actual_length_words = ?,
            agent_assisted = ?,
            updated_at_utc = datetime('now')
        WHERE id = ?
        """,
        (
            new_body,
            new_outline,
            new_title,
            new_status,
            new_length,
            agent_assisted_new,
            blog_id,
        ),
    )

    cur = conn.execute(
        """
        INSERT INTO blog_versions
          (blog_id, version_number, body_markdown, body_text_hash,
           title_at_version, outline_markdown_at_version,
           status_at_version, created_by, agent_message_id,
           agent_action, daniel_revision_note,
           confidence_label_at_version, is_current_for_blog)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        RETURNING id
        """,
        (
            blog_id,
            next_version_number,
            new_body,
            new_body_hash,
            new_title,
            new_outline,
            new_status,
            created_by,
            agent_message_id,
            agent_action,
            daniel_revision_note,
            confidence_label_at_version,
        ),
    )
    return int(cur.fetchone()[0])


def transition_status(
    conn: sqlite3.Connection,
    blog_id: int,
    new_status: str,
    *,
    daniel_revision_note: str | None = None,
    external_url: str | None = None,
) -> BlogVersion:
    """Validate + apply a status transition; append a version row capturing it.

    Raises :class:`InvalidStatusTransitionError` when the edge is not in
    the state machine. The transition that requires an out-of-app fact
    (``exported → published_externally``) requires ``external_url`` to
    be supplied (or already populated on the blog row); the editor's
    selector enforces this at the UI level too.

    Returns the newly-appended :class:`BlogVersion` row.

    Audit: one ``audit_logs`` row per successful transition with
    category ``data`` and type ``blog_status_<from>_to_<to>``.
    """
    # P6R-1: pre-checks run OUTSIDE the transaction (read-only). The full
    # state mutation — version row + blogs UPDATE + external_url +
    # external_published_at + audit — commits or rolls back together inside
    # the single `with transaction(conn):` block below. If audit fails,
    # nothing else persists either.
    blog_row = _fetch_blog_row(conn, blog_id)
    current_status = blog_row["status"]

    if not is_legal_transition(current_status, new_status):
        raise InvalidStatusTransitionError(blog_id, current_status, new_status)

    # exported → published_externally requires external_url present.
    if (
        current_status == "exported"
        and new_status == "published_externally"
        and not (external_url or blog_row["external_url"])
    ):
        raise InvalidBlogFieldError(
            "published_externally requires external_url to be populated"
        )

    # P6R-7: external_url must be http(s) — refuse javascript:, data:,
    # file:, etc. so the editor surface cannot become a script-execution
    # sink via XSS-escape bypass + Daniel clicking the surfaced URL.
    # Mirrors P511R-19's http(s)-only discipline for inspiration.source_url.
    if external_url is not None:
        scheme = urlparse(external_url).scheme.lower()
        if scheme not in {"http", "https"}:
            raise InvalidBlogFieldError(
                f"external_url must use http or https scheme; got {scheme!r}"
            )

    with transaction(conn):
        new_version_id = _save_blog_in_tx(
            conn,
            blog_id,
            status=new_status,
            created_by="daniel",
            daniel_revision_note=daniel_revision_note,
        )
        if external_url is not None:
            conn.execute(
                "UPDATE blogs SET external_url = ? WHERE id = ?",
                (external_url, blog_id),
            )
        if new_status == "published_externally":
            conn.execute(
                """
                UPDATE blogs
                SET external_published_at = COALESCE(external_published_at, datetime('now'))
                WHERE id = ?
                """,
                (blog_id,),
            )
        _audit_log.log(
            conn,
            event_category="data",
            event_type=f"blog_status_{current_status}_to_{new_status}",
            target_type="blog",
            target_id=blog_id,
            details={
                "from_status": current_status,
                "to_status": new_status,
                "note": daniel_revision_note,
            },
        )

    if new_version_id is None:
        # Should not happen — status changed, so no-op detection cannot
        # have fired. Fall back to a query for safety.
        row = _fetch_current_version(conn, blog_id)
        assert row is not None
        return _row_to_version(row)
    row = conn.execute(
        "SELECT * FROM blog_versions WHERE id = ?", (new_version_id,)
    ).fetchone()
    return _row_to_version(row)


def revert_to_version(
    conn: sqlite3.Connection,
    blog_id: int,
    version_id: int,
    *,
    daniel_revision_note: str | None = None,
) -> BlogVersion:
    """Create a forward-moving revert: a new version carrying ``version_id``'s body.

    The new row's ``version_number`` is ``max+1`` and its
    ``is_current_for_blog`` is true. The target row's
    ``is_current_for_blog`` is NOT touched (history doesn't rewrite).
    The new row's ``daniel_revision_note`` records the revert reason
    plus Daniel's optional note, in the form
    ``"reverted to v{N}: {note}"`` or ``"reverted to v{N}"``.

    Reverting is recorded in ``audit_logs`` as a single ``data`` event
    with the from/to version numbers in ``details``.
    """
    # P6R-1: target lookup + pre-checks + version-row append + audit all
    # live inside ONE transaction so partial-failure leaves no orphan
    # state. (Previously the pre-checks were outside any transaction and
    # save_blog ran in its own transaction, leaving room for the audit
    # row to fail after the version row committed.)
    with transaction(conn):
        target = conn.execute(
            """
            SELECT *
            FROM blog_versions
            WHERE id = ? AND blog_id = ?
            """,
            (version_id, blog_id),
        ).fetchone()
        if target is None:
            raise BlogVersionNotFoundError(
                f"version #{version_id} not found for blog #{blog_id}"
            )

        # If the target is already the current row, there is nothing to
        # revert to — surface a clean error instead of writing a duplicate.
        if int(target["is_current_for_blog"]) == 1:
            raise InvalidBlogFieldError(
                f"version #{version_id} is already the current version "
                f"for blog #{blog_id}"
            )

        revert_note_pieces = [f"reverted to v{int(target['version_number'])}"]
        if daniel_revision_note:
            revert_note_pieces.append(daniel_revision_note.strip())
        revert_note = ": ".join(revert_note_pieces)

        current_before = _fetch_current_version(conn, blog_id)
        current_version_number_before = (
            int(current_before["version_number"]) if current_before is not None else None
        )

        new_version_id = _save_blog_in_tx(
            conn,
            blog_id,
            body_markdown=target["body_markdown"] or "",
            outline_markdown=target["outline_markdown_at_version"],
            title=target["title_at_version"],
            status=target["status_at_version"],
            created_by="daniel",
            daniel_revision_note=revert_note,
        )
        # _save_blog_in_tx returns None on no-op (target's content
        # matches current). We already rejected the "same row" case but
        # identical content across two distinct rows can still happen.
        # Raising here rolls back the whole transaction so no orphan
        # audit row is left behind.
        if new_version_id is None:
            raise InvalidBlogFieldError(
                f"revert to v{int(target['version_number'])} produced no change "
                f"(content matches current). Pick a different version."
            )

        new_version_number = conn.execute(
            "SELECT version_number FROM blog_versions WHERE id = ?",
            (new_version_id,),
        ).fetchone()[0]

        _audit_log.log(
            conn,
            event_category="data",
            event_type="blog_reverted",
            target_type="blog",
            target_id=blog_id,
            details={
                "from_version_number": current_version_number_before,
                "to_target_version_number": int(target["version_number"]),
                "new_version_number": int(new_version_number),
                "note": daniel_revision_note,
            },
        )

    row = conn.execute(
        "SELECT * FROM blog_versions WHERE id = ?", (new_version_id,)
    ).fetchone()
    return _row_to_version(row)


def list_versions(conn: sqlite3.Connection, blog_id: int) -> list[BlogVersion]:
    """Return all versions for ``blog_id``, newest first."""
    rows = conn.execute(
        """
        SELECT *
        FROM blog_versions
        WHERE blog_id = ?
        ORDER BY version_number DESC
        """,
        (blog_id,),
    ).fetchall()
    return [_row_to_version(r) for r in rows]


def get_version(
    conn: sqlite3.Connection, blog_id: int, version_id: int
) -> BlogVersion:
    """Fetch one version row scoped to ``blog_id``."""
    row = conn.execute(
        "SELECT * FROM blog_versions WHERE id = ? AND blog_id = ?",
        (version_id, blog_id),
    ).fetchone()
    if row is None:
        raise BlogVersionNotFoundError(
            f"version #{version_id} not found for blog #{blog_id}"
        )
    return _row_to_version(row)


def set_seo_metadata(
    conn: sqlite3.Connection,
    blog_id: int,
    *,
    seo_title: str | None,
    seo_description: str | None,
    seo_tags: list[str] | None,
) -> None:
    """Write SEO sidecar fields directly to ``blogs.seo_*`` — NO version row.

    SEO metadata is not content; per §28.32 it's an export sidecar.
    Versioning it would clutter the timeline with cosmetic deltas
    that have no editorial meaning. The audit row still fires so the
    change is recoverable.
    """
    import json as _json

    _fetch_blog_row(conn, blog_id)
    # P6R-21: coerce list elements to strings so a model that emits
    # mixed types (numbers, dicts) doesn't poison the stored JSON —
    # the read-side _seo_data_for_blog already calls str(t) on each
    # tag for safety, but coercing on write keeps the stored value
    # itself uniform and prevents future readers from being surprised.
    tags_json = (
        _json.dumps([str(t) for t in seo_tags]) if seo_tags is not None else None
    )
    with transaction(conn):
        conn.execute(
            """
            UPDATE blogs
            SET seo_title = ?, seo_description = ?, seo_tags_json = ?,
                updated_at_utc = datetime('now')
            WHERE id = ?
            """,
            (seo_title, seo_description, tags_json, blog_id),
        )
        _audit_log.log(
            conn,
            event_category="data",
            event_type="blog_seo_metadata_set",
            target_type="blog",
            target_id=blog_id,
            details={
                "seo_title": seo_title,
                "seo_description": seo_description,
                "seo_tags": seo_tags,
            },
        )
