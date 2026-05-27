# Native Parity Checklist

Quick map of the 18 native desktop views (spec §31.7) to their Streamlit source, FastAPI endpoints, tests, and known gaps. `spec.md` remains authoritative; this document is a navigation aid only.

Legend for **Known gap**: `none known` | concrete gap description | `unknown` when not yet verified.

| View ID | Streamlit page | Native view | FastAPI endpoint(s) | Tests | Known gap | Spec |
| --- | --- | --- | --- | --- | --- | --- |
| `today` | `app/pages/1_Today.py` | `desktop/src/views/TodayView.tsx` | `GET /views/today`, `GET /api/user-metrics`, `POST /api/sync-today`, `POST /forms/snapshot` | `tests/read_models/test_parity.py`, `tests/service/test_view_smoke.py` | Streamlit page still uses inline SQL; native/read-model path shared via `app/read_models/today.py` | §31.7, §31.10 |
| `next-rep` | `app/pages/2_Next_Rep.py` | `desktop/src/views/NextRepView.tsx` | `GET /views/next-rep` | `tests/service/test_service_smoke.py` | Read-model logic duplicated in sidecar slice vs Streamlit page | §31.7 |
| `progress` | `app/pages/3_Progress.py` | `desktop/src/views/ProgressView.tsx` | `GET /views/progress`, `GET /charts/follower-trend` | `tests/service/test_service_smoke.py` | Read-model logic duplicated in sidecar slice vs Streamlit page | §31.7 |
| `content-performance` | `app/pages/4_Content_Performance.py` | `desktop/src/views/ContentPerformanceView.tsx` | `GET /views/content-performance`, `GET /charts/lane-scatter` | `tests/service/test_service_smoke.py` | Read-model logic duplicated in sidecar slice vs Streamlit page | §31.7 |
| `funnel` | `app/pages/5_Funnel.py` | `desktop/src/views/FunnelView.tsx` | `GET /views/validation`, `GET /charts/funnel`, `GET /charts/funnel-daily` | `tests/service/test_service_smoke.py` | Funnel uses validation slice + chart endpoints rather than a single `/views/funnel` | §31.7 |
| `weekly-review` | `app/pages/6_Weekly_Review.py` | `desktop/src/views/WeeklyReviewView.tsx` | `GET /views/weekly-review`, `POST /forms/weekly-review` | `tests/service/test_service_smoke.py`, `tests/test_monthly_review.py` | Read-model logic duplicated; agent review drafting stubs remain in tools | §28, §31.7 |
| `manual-entry` | `app/pages/8_Manual_Entry.py` | `desktop/src/views/ManualEntryView.tsx` | `GET /views/needs-tagging`, `GET /views/needs-post-id`, multiple `/forms/*` and `/agent/*` job endpoints | `tests/service/test_service_writes.py`, `tests/test_forms_persistence.py` | Aggregates many operational forms/jobs; no single read endpoint | §31.7 |
| `settings` | `app/pages/7_Settings.py` | `desktop/src/views/SettingsView.tsx` | `GET/PUT /settings`, `GET/PUT /settings/secrets` | `tests/service/test_service_smoke.py`, `tests/test_settings_xss_guards.py` | Native Settings lacks diagnostics export/copy action | §31.5, §31.7 |
| `agent-chat` | `app/pages/9_Agent_Chat.py` | `desktop/src/views/AgentChatView.tsx` | `GET/POST/DELETE /agent/conversations`, `GET/POST /agent/conversations/{id}/messages`, SSE stream | `tests/service/test_service_agent.py`, `tests/test_agent.py` | Tool transparency and mode/permission chips incomplete in native UI | §28, §31.7 |
| `reply-queue` | `app/pages/10_Reply_Target_Queue.py` | `desktop/src/views/ReplyQueueView.tsx` | `GET /views/reply-queue`, `/agent/score-candidates`, `/agent/find-reply-targets`, `/reply-targets/*` | `tests/service/test_service_smoke.py`, `tests/test_reply_target_queue.py` | Read-model logic duplicated in sidecar slice vs Streamlit page | §31.7 |
| `brain-dump` | `app/pages/11_Brain_Dump.py` | `desktop/src/views/BrainDumpView.tsx` | `GET /views/brain-dump`, `POST /brain-dumps` | `tests/service/test_service_smoke.py`, `tests/test_brain_dump.py` | none known | §31.7 |
| `coach` | `app/pages/12_Coach.py` | `desktop/src/views/CoachView.tsx` | `GET/POST/DELETE /agent/conversations`, stream endpoints (coach mode) | `tests/test_coach.py`, `tests/service/test_service_agent.py` | No dedicated `/views/coach`; reuses agent conversation API | §28, §31.7 |
| `account-researcher` | `app/pages/13_Account_Researcher.py` | `desktop/src/views/AccountResearcherView.tsx` | `GET /views/account-researcher`, agent conversation endpoints | `tests/service/test_service_smoke.py`, `tests/test_account_research.py` | Agent turn flow differs from Streamlit session wiring | §31.7 |
| `content-calendar` | `app/pages/14_Content_Calendar.py` | `desktop/src/views/ContentCalendarView.tsx` | `GET /views/content-calendar` | `tests/service/test_service_smoke.py`, `tests/test_calendar.py` | none known | §31.7 |
| `campaigns` | `app/pages/15_Campaigns.py` | `desktop/src/views/CampaignsView.tsx` | `GET /views/campaigns`, `POST /campaigns`, `PUT /campaigns/{id}/activate` | `tests/service/test_service_smoke.py`, `tests/test_campaigns.py` | none known | §31.7 |
| `inspiration` | `app/pages/16_Inspiration_Library.py` | `desktop/src/views/InspirationView.tsx` | `GET /views/inspiration`, `POST /inspirations`, `PUT /inspirations/{id}/archive` | `tests/service/test_service_smoke.py`, `tests/test_inspiration.py` | none known | §31.7 |
| `blogs` | `app/pages/17_Blogs.py` | `desktop/src/views/BlogsView.tsx` | `GET /views/blogs`, `POST /blogs` | `tests/service/test_service_smoke.py`, `tests/test_blogs.py` | none known | §31.7 |
| `blog-editor` | `app/pages/18_Blog_Editor.py` | `desktop/src/views/BlogEditorView.tsx` | `GET /views/blogs`, `GET /views/blog/{id}`, blog write/agent endpoints | `tests/service/test_service_smoke.py`, `tests/test_blog_drafting.py` | Native editor currently selects first blog from list rather than deep-link by id | §31.7 |

## Notes

- Smoke coverage for read endpoints lives in `tests/service/test_service_smoke.py`.
- Parity between Streamlit pages and FastAPI slice builders is not yet enforced by shared read models (`app/read_models/` planned).
- Do not claim full parity until parity tests exist for a given view.
