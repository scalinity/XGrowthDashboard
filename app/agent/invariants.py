"""Startup safety invariants — single source of truth (§28.2 rule #10, §28.21, §28.23).

These three assertions guard the agent's tool registry. They were originally
private functions in ``app/main.py`` (the Streamlit entry point). Phase 11
(§31.3) adds a second presentation surface — the FastAPI service sidecar — that
MUST enforce the exact same guarantees at startup. Extracting them here keeps a
single source of truth so the native-desktop sidecar and ``streamlit run`` can
never drift.

Both entry points call ``run_all()`` once at startup:

- ``app/main.py``           → in ``_bootstrap_session_state`` (gated once per session).
- ``app/service/app.py``    → in ``create_app`` (once at sidecar boot).
"""

from __future__ import annotations

import json as _json


def assert_publish_tools_unreachable() -> None:
    """§28.2 rule #10, §28.4 internal-only tool surface.

    The publish tools must not leak into the agent's tool registry. If a
    future refactor accidentally adds 'publish_post_to_x' / 'publish_reply_to_x'
    to AGENT_TOOLS, this assertion stops the app before the model can be
    given a tool catalog that contains them.
    """
    from app.agent._internal_tools import INTERNAL_TOOLS
    from app.agent.tools import AGENT_TOOLS

    agent_names = {t.name for t in AGENT_TOOLS}
    internal_names = {t.name for t in INTERNAL_TOOLS}
    leaked = agent_names & internal_names
    assert not leaked, (
        "INVARIANT VIOLATION (§28.2 rule #10): publish tools leaked into "
        f"the agent's tool registry: {sorted(leaked)}. The publish path is "
        "click-handler-only by construction; see app/agent/_internal_tools.py."
    )


def assert_personality_lore_unreachable() -> None:
    """§28.21 Phase 5.9 access-control rule.

    No tool in AGENT_TOOLS may grant the agent write access to the
    ``personality_lore`` table. Lore is Daniel-curated; auto-extracted
    lore would warp drafts in unbounded ways. We scan each tool's name +
    description + JSON schema text for the bare table name. A read-only
    listing tool would still trip this assertion — the spec is explicit
    that NO tool entry references the table, even a hypothetical read-only one.
    """
    from app.agent.tools import AGENT_TOOLS

    needle = "personality_lore"
    offenders: list[str] = []
    for tool in AGENT_TOOLS:
        haystack = tool.name + " " + tool.description + " " + _json.dumps(tool.input_schema)
        if needle in haystack:
            offenders.append(tool.name)
    assert not offenders, (
        "INVARIANT VIOLATION (§28.21): personality_lore must NOT be "
        f"referenced by any AGENT_TOOLS entry. Offending tools: "
        f"{sorted(offenders)}. Daniel is the only writer; "
        "auto-extracted lore would warp drafts."
    )


def assert_coach_excludes_write_tools() -> None:
    """§28.23 Phase 5.10 access-control rule.

    The §14.10 Coach is advice-only: it must NEVER call save_draft_*,
    revise_draft, record_reply_target, score_replier_pool, process_
    brain_dump, analyze_account, or audit_profile. Delegates to the
    canonical check in ``app.agent.coach``.
    """
    from app.agent import coach as _coach
    from app.agent.tools import AGENT_TOOLS

    _coach.assert_coach_excludes_write_tools(AGENT_TOOLS)


def run_all() -> None:
    """Run every startup invariant. Raises AssertionError on any violation."""
    assert_publish_tools_unreachable()
    assert_personality_lore_unreachable()
    assert_coach_excludes_write_tools()
