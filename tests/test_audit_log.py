"""Tests for the §28.30 comprehensive audit log module.

Covers:
- ``log()`` writes a row with the expected shape.
- Append-only invariant — no UPDATE / DELETE path is exposed.
- ``query()`` filter composition.
- ``prune()`` deletes by retention AND self-audits.
- ``log_setting_change()`` produces the canonical settings diff payload.
- ``set_setting`` (in ``app/forms``) emits an audit row on every change
  and skips audit on no-op writes.
- ``record_export`` emits an audit row alongside the ``data_exports`` row.
- ``publish_post_atomic`` emits an audit row on success AND on each of
  the three failure branches.
- The agent registry exposes NO tool whose handler reads or writes
  ``audit_logs`` (§28.30 read-scope rule).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.agent import audit_log
from app.forms import get_setting, set_setting


# ---------------------------------------------------------------------------
# Core API — log() and query().
# ---------------------------------------------------------------------------
def test_log_writes_row_with_expected_shape(db_conn: sqlite3.Connection) -> None:
    row_id = audit_log.log(
        db_conn,
        event_category="settings",
        event_type="settings_changed_test_key",
        target_type="setting",
        target_id="test_key",
        details={"setting_key": "test_key", "old_value": 1, "new_value": 2},
    )
    assert row_id > 0
    row = db_conn.execute(
        "SELECT * FROM audit_logs WHERE id = ?", (row_id,)
    ).fetchone()
    assert row["event_category"] == "settings"
    assert row["event_type"] == "settings_changed_test_key"
    assert row["actor"] == "daniel"
    assert row["target_type"] == "setting"
    assert row["target_id"] == "test_key"
    assert row["success"] == 1
    assert row["error_message"] is None
    details = json.loads(row["details_json"])
    assert details["setting_key"] == "test_key"
    assert details["old_value"] == 1
    assert details["new_value"] == 2


def test_log_rejects_unknown_category(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="Unknown event_category"):
        audit_log.log(
            db_conn, event_category="nope", event_type="x"  # type: ignore[arg-type]
        )


def test_log_rejects_empty_event_type(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="event_type"):
        audit_log.log(db_conn, event_category="admin", event_type="")


def test_log_failure_row_carries_success_false_and_error(
    db_conn: sqlite3.Connection,
) -> None:
    audit_log.log(
        db_conn,
        event_category="publish",
        event_type="publish_failed_runtime",
        target_type="post",
        target_id=42,
        success=False,
        error_message="connection reset",
    )
    row = db_conn.execute(
        "SELECT success, error_message, target_id FROM audit_logs "
        "WHERE event_type = 'publish_failed_runtime'"
    ).fetchone()
    assert row["success"] == 0
    assert row["error_message"] == "connection reset"
    assert row["target_id"] == "42"  # coerced to string


def test_query_filters_compose(db_conn: sqlite3.Connection) -> None:
    audit_log.log(db_conn, event_category="settings", event_type="a")
    audit_log.log(db_conn, event_category="settings", event_type="b")
    audit_log.log(db_conn, event_category="publish", event_type="c")
    rows = audit_log.query(db_conn, category="settings")
    types = {r.event_type for r in rows}
    # Must include the two settings rows; must not include the publish row.
    assert {"a", "b"}.issubset(types)
    assert "c" not in types


def test_query_target_filter(db_conn: sqlite3.Connection) -> None:
    audit_log.log(
        db_conn, event_category="data", event_type="x", target_type="post", target_id=1
    )
    audit_log.log(
        db_conn, event_category="data", event_type="y", target_type="post", target_id=2
    )
    rows = audit_log.query(db_conn, target_type="post", target_id=1)
    assert len(rows) == 1
    assert rows[0].target_id == "1"


def test_query_limit_caps_results(db_conn: sqlite3.Connection) -> None:
    for i in range(5):
        audit_log.log(db_conn, event_category="admin", event_type=f"evt_{i}")
    rows = audit_log.query(db_conn, category="admin", limit=3)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# log_setting_change convenience.
# ---------------------------------------------------------------------------
def test_log_setting_change_payload_shape(db_conn: sqlite3.Connection) -> None:
    audit_log.log_setting_change(
        db_conn, key="niche_problem", old_value="", new_value="kitchen-scanner UX"
    )
    rows = audit_log.query(db_conn, category="settings", target_id="niche_problem")
    assert len(rows) == 1
    assert rows[0].event_type == "settings_changed_niche_problem"
    details = rows[0].details
    assert details is not None
    assert details["setting_key"] == "niche_problem"
    assert details["old_value"] == ""
    assert details["new_value"] == "kitchen-scanner UX"


# ---------------------------------------------------------------------------
# set_setting write-through.
# ---------------------------------------------------------------------------
def test_set_setting_emits_audit_row(db_conn: sqlite3.Connection) -> None:
    set_setting(db_conn, "daily_post_target", 2)
    rows = audit_log.query(
        db_conn, category="settings", target_id="daily_post_target"
    )
    assert len(rows) == 1
    assert rows[0].details is not None
    assert rows[0].details["old_value"] == 1   # seeded value
    assert rows[0].details["new_value"] == 2


def test_set_setting_skips_audit_on_no_op_write(db_conn: sqlite3.Connection) -> None:
    # Re-write the same value. No audit row should land.
    current = get_setting(db_conn, "daily_post_target")
    set_setting(db_conn, "daily_post_target", current)
    rows = audit_log.query(
        db_conn, category="settings", target_id="daily_post_target"
    )
    assert rows == []


def test_set_setting_audit_records_first_write_as_old_none(
    db_conn: sqlite3.Connection,
) -> None:
    set_setting(db_conn, "new_phase511_test_key", "v1")
    rows = audit_log.query(
        db_conn, category="settings", target_id="new_phase511_test_key"
    )
    assert len(rows) == 1
    assert rows[0].details is not None
    assert rows[0].details["old_value"] is None
    assert rows[0].details["new_value"] == "v1"


# ---------------------------------------------------------------------------
# Append-only invariant — no helper exposes UPDATE / DELETE on audit_logs.
# ---------------------------------------------------------------------------
def test_audit_log_module_has_no_update_or_delete_helpers() -> None:
    public = set(audit_log.__all__)
    forbidden = {"update", "delete", "remove", "edit", "modify"}
    assert public.isdisjoint(forbidden), (
        "audit_log must not expose any update/delete helpers — the table "
        "is append-only by discipline (§28.30)."
    )


# ---------------------------------------------------------------------------
# Retention pruning.
# ---------------------------------------------------------------------------
def test_prune_deletes_old_rows_and_self_audits(
    db_conn: sqlite3.Connection,
) -> None:
    # Insert one old row (well outside retention) and one fresh row.
    db_conn.execute(
        """
        INSERT INTO audit_logs
          (occurred_at_utc, event_category, event_type, actor)
        VALUES (?, 'admin', 'ancient_event', 'daniel')
        """,
        ("2020-01-01T00:00:00",),
    )
    audit_log.log(db_conn, event_category="admin", event_type="fresh_event")

    pruned = audit_log.prune(db_conn, retention_days=30)
    assert pruned >= 1

    # Old row gone.
    leftover = db_conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE event_type = 'ancient_event'"
    ).fetchone()[0]
    assert leftover == 0

    # Self-audit row landed with the count.
    self_audit_rows = audit_log.query(db_conn, category="admin")
    self_audit = [r for r in self_audit_rows if r.event_type == "audit_logs_pruned"]
    assert len(self_audit) >= 1
    assert self_audit[0].details is not None
    assert self_audit[0].details["pruned_count"] >= 1


def test_prune_zero_retention_is_noop(db_conn: sqlite3.Connection) -> None:
    audit_log.log(db_conn, event_category="admin", event_type="should_survive")
    pruned = audit_log.prune(db_conn, retention_days=0)
    assert pruned == 0
    surviving = db_conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE event_type = 'should_survive'"
    ).fetchone()[0]
    assert surviving == 1


def test_prune_never_self_deletes_its_own_marker(
    db_conn: sqlite3.Connection,
) -> None:
    # Two prunes in a row; the second must not delete the first's
    # self-audit row even if it falls outside the (artificially short)
    # retention window. The DELETE statement excludes admin/audit_logs_pruned.
    db_conn.execute(
        """
        INSERT INTO audit_logs
          (occurred_at_utc, event_category, event_type, actor)
        VALUES (?, 'admin', 'audit_logs_pruned', 'daniel')
        """,
        ("2020-01-01T00:00:00",),  # antique
    )
    audit_log.prune(db_conn, retention_days=30)
    remaining = db_conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE event_type = 'audit_logs_pruned'"
    ).fetchone()[0]
    # Antique self-audit survived (its category/event_type bypass) +
    # the fresh self-audit from this prune call landed.
    assert remaining >= 2


# ---------------------------------------------------------------------------
# Agent tool-registry assertion: no tool exposes audit_logs.
# ---------------------------------------------------------------------------
def test_audit_log_no_agent_tool_exposes_audit_logs() -> None:
    """§28.30 read-scope rule: the agent has NO read or write access to
    ``audit_logs``. If any AGENT_TOOLS entry's name references audit_log
    OR the implementing source code in app/agent/tools.py references the
    table, fail loudly so the rule isn't quietly violated by a future
    feature.
    """
    from app.agent.tools import AGENT_TOOLS

    forbidden_substrings = ("audit_log", "audit_logs")
    bad_tools = [
        t.name
        for t in AGENT_TOOLS
        if any(s in t.name.lower() for s in forbidden_substrings)
    ]
    assert not bad_tools, (
        f"Agent tools reference audit_log in their name: {bad_tools}. "
        "§28.30 forbids the agent from reading or writing the audit log."
    )

    # P511R-10: strengthen the source-scan to ALSO catch "audit_log"
    # (not just the plural "audit_logs"). The substring check now trips
    # on `from app.agent import audit_log`, `from app.agent.audit_log
    # import log`, or any handler closure that names the module — the
    # import statement itself is the smoke. Previous test only caught
    # the table name; a developer could have added the module import
    # without the test noticing.
    #
    # AST-level walk of the imports also runs below for belt-and-
    # suspenders — substring scan flags comments + docstrings, AST
    # walk flags actual import statements only. Together they cover
    # both "looks suspicious in source" and "actually imports the
    # forbidden module" failure modes.
    tools_source = Path("app/agent/tools.py").read_text()
    assert "audit_log" not in tools_source, (
        "app/agent/tools.py references audit_log (the module OR the "
        "table). The agent must NOT read or write the audit log table "
        "directly per §28.30 — state-changing tools should log via the "
        "underlying server-side module path, not from inside a tool "
        "handler."
    )

    import ast as _ast
    tree = _ast.parse(tools_source)
    forbidden_imports: list[str] = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                if alias.name == "app.agent.audit_log" or alias.name.endswith(
                    ".audit_log"
                ):
                    forbidden_imports.append(alias.name)
        elif isinstance(node, _ast.ImportFrom):
            module = node.module or ""
            if module == "app.agent.audit_log" or module.endswith(".audit_log"):
                forbidden_imports.append(module)
            if module == "app.agent":
                for alias in node.names:
                    if alias.name == "audit_log":
                        forbidden_imports.append(f"app.agent.{alias.name}")
    assert not forbidden_imports, (
        f"app/agent/tools.py imports audit_log: {forbidden_imports}. "
        "§28.30 forbids the agent layer from accessing the audit log."
    )


# ---------------------------------------------------------------------------
# Export + backup write-through.
# ---------------------------------------------------------------------------
def test_record_export_emits_audit_row(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    from app.exports._audit import EXPORT_KIND_CSV, record_export

    out = tmp_path / "posts.csv"
    record_export(
        db_conn,
        kind=EXPORT_KIND_CSV,
        output_path=out,
        table_name="posts",
        row_count=12,
        include_opt_in=None,
    )
    rows = audit_log.query(db_conn, category="export")
    matching = [r for r in rows if r.event_type == f"export_{EXPORT_KIND_CSV}"]
    assert len(matching) == 1
    assert matching[0].details is not None
    assert matching[0].details["row_count"] == 12
    assert matching[0].details["table_name"] == "posts"
    assert matching[0].target_id == str(out)
