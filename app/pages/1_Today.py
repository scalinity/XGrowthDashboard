"""Today / Weigh-In — stub (Phase 3 builds the view).

Phase 2 only exposes the manual entry hub; this page exists so the sidebar
order matches the §19 view layout that Phase 3 fills in.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

st.title("Today")
st.info(
    "Phase 3 builds this view (§14.1). Until then, use **Manual entry** to log "
    "today's snapshot and reps."
)
