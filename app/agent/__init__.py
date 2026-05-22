"""app.agent — Growth Agent module (spec §28).

Phase 5.5 splits the agent into a tool registry the SDK sees
(``app.agent.tools``) and a parallel set of publish-only callables it never
sees (``app.agent._internal_tools``). The startup assertion in
``app/main.py`` proves the two sets are disjoint by name at runtime.
"""
