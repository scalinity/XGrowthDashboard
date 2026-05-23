"""Unified base class for spec / prompt / migration drift errors.

This module exists ONLY to host ``SpecDriftError`` and live below
every other ``app.agent`` module in the import graph so any of them
can inherit from the base without circular-import pain.

Phase 10 S10 originally landed ``SpecDriftError`` inside
``app.agent.prompt_builder``, but the chain
``app.agent.lint → app.agent.prompt_builder → app.agent.tools →
app.agent.lint`` made it impossible for ``lint``'s drift error to
inherit from it (the import at module load time triggered the
cycle, blowing up with ``ImportError: cannot import name
'is_thread_classifier_lint_enabled' from partially initialized module
'app.agent.lint'``). The unified-base benefit therefore did NOT
extend to ``ReplyQualityLintPromptMissingError``.

Pulling the base class into its own zero-dependency module breaks
the cycle: every drift-error consumer imports from
``app.agent.spec_drift`` directly. This module imports nothing from
``app.agent.*`` and never will — that's the load-bearing
invariant. Callers wanting to catch every drift error across the
codebase can now do::

    from app.agent.spec_drift import SpecDriftError
    try:
        ...
    except SpecDriftError:
        ...
"""

from __future__ import annotations


class SpecDriftError(RuntimeError):
    """Base class for every spec / prompt / migration drift error.

    Inherits from ``RuntimeError`` to preserve the prior public-API
    contract (all drift errors were ``RuntimeError`` subclasses).
    Subclasses live in ``app.agent.prompt_builder`` (rule extraction,
    voice-profile prescriptive layer, Section 4 anchors) and
    ``app.agent.lint`` (reply-quality lint prompt file). Other
    modules that grow their own drift checks should also subclass
    this so the unified-catch contract holds.
    """
