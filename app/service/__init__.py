"""FastAPI loopback sidecar for the native macOS app (spec §31.3).

A thin HTTP/SSE adapter over the *existing* Python backend (db, agent, forms,
exports, jobs, backup). It contains **no business logic** — every endpoint
delegates to the same modules the Streamlit pages use. The Tauri shell spawns
this as a sidecar bound to 127.0.0.1 on a random port, gated by a per-launch
bearer token (see ``app/service/__main__.py``).

Phase 11.0 builds this surface incrementally; ``streamlit run app/main.py``
remains fully functional throughout (§31.8).
"""

from __future__ import annotations
