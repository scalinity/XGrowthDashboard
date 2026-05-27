# Feature matrix

Maps major product areas to code, tests, and spec sections. `spec.md` remains authoritative.

| Area | Primary files | Surface | Status | Key tests | Spec |
| --- | --- | --- | --- | --- | --- |
| Today dashboard | `app/pages/1_Today.py`, `app/read_models/today.py`, `desktop/src/views/TodayView.tsx` | Streamlit + native | shipped | `tests/read_models/test_parity.py`, `tests/service/test_view_smoke.py` | §14.1 |
| Progress | `app/pages/3_Progress.py`, `app/read_models/progress.py` | both | shipped | parity + smoke | §14.3 |
| Content performance | `app/pages/4_Content_Performance.py`, `app/read_models/content_performance.py` | both | shipped | parity + smoke | §14.4 |
| Weekly review | `app/pages/6_Weekly_Review.py`, `app/read_models/weekly_review.py` | both | shipped | parity + smoke | §14.6 |
| Reply queue | `app/pages/10_Reply_Target_Queue.py`, `app/read_models/reply_queue.py` | both | shipped | parity + smoke | §29.7 |
| Manual forms / snapshots | `app/forms/`, `app/service/routes/registry.py` | both | shipped | `tests/service/test_*` | §14 |
| Agent chat | `app/agent/`, `desktop/src/views/AgentChatView.tsx` | both | shipped | `tests/service/test_service_agent.py`, `tests/test_review_drafting.py` | §28, §14.8 |
| Coach / researcher | `app/agent/coach.py`, coach views | Streamlit + native coach routes | shipped | service agent tests | §14.10 |
| Settings + secrets | `app/pages/7_Settings.py`, Keychain via `app/secret_store.py` | both | shipped | `tests/service/test_settings_allowlist.py` | §14.7, §31.5 |
| Native sidecar | `app/service/`, `scripts/dev_sidecar.py` | FastAPI loopback | shipped | `tests/service/*` | §31.3 |
| Diagnostics / health | `app/service/diagnostics.py`, `SidecarBootstrap.tsx` | native | shipped | `tests/service/test_health_details.py`, `test_diagnostics.py` | §31 |
| Backup / export | `scripts/backup_db.py`, export paths in settings | Streamlit + jobs | shipped | scripts tests | §7 |
| Publish flow | `app/agent/publish.py`, `_internal_tools.py` | UI confirmation only | shipped | `tests/service/test_publish_endpoint.py` | §28.10 |
| Contracts | `app/service/models.py`, `desktop/src/lib/contracts.ts` | native API | shipped | `tests/service/test_contract_snapshots.py` | §31 |

## Docs map

- [`API_SURFACE.md`](API_SURFACE.md) — route inventory (generated)
- [`AGENT_TOOL_MATRIX.md`](AGENT_TOOL_MATRIX.md) — tool capabilities and stub audit
- [`NATIVE_PARITY_CHECKLIST.md`](NATIVE_PARITY_CHECKLIST.md) — 18-view parity tracker
