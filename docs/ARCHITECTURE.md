# Architecture

`spec.md` at the repo root is the authoritative source for all architecture and product decisions. This file exists only as a pointer.

For architecture, read:

- **§7.1 Decision** — SQLite + Streamlit + manual entry is the MVP.
- **§7.2 Architecture comparison** — why not spreadsheet / Next.js / Tauri / Electron.
- **§8 System overview** — data flow diagram.
- **§10 Database schema** — every table, constraint, and index.
- **§28 Growth Agent** — Anthropic-powered draft/reply/publish flow with confirmation-gated posting.
- **§29 Reply Target Queue** — first-class reply distribution surface.

If anything in this file ever conflicts with `spec.md`, the spec wins.
