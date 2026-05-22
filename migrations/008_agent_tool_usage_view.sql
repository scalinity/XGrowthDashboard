-- migrations/008_agent_tool_usage_view.sql
--
-- Phase 5.5 /review-2 S12: telemetry view for the agent's tool catalog.
-- Rolls up agent_tool_calls.tool_name into per-tool counts + last-used
-- timestamps, plus a separate rollup of "stub" partial-status rows so
-- the Settings panel can surface "tool not called in N days" and
-- "tool returning stubs only" candidates for pruning.
--
-- This is a read-only telemetry surface — the agent has no path to
-- write through it; no FK; no triggers. Safe to drop in V1.1+ if it
-- proves not useful in practice.

DROP VIEW IF EXISTS v_agent_tool_usage;
CREATE VIEW v_agent_tool_usage AS
SELECT
    tool_name,
    COUNT(*)                                                 AS total_calls,
    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)      AS success_count,
    SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END)      AS partial_count,
    SUM(CASE WHEN status = 'error'   THEN 1 ELSE 0 END)      AS error_count,
    MIN(created_at_utc)                                      AS first_called_at_utc,
    MAX(created_at_utc)                                      AS last_called_at_utc,
    SUM(COALESCE(cost_usd, 0.0))                             AS total_cost_usd,
    AVG(COALESCE(duration_ms, 0))                            AS avg_duration_ms
FROM agent_tool_calls
GROUP BY tool_name;
