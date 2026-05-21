"""Next Rep — stub (Phase 3 builds the view; Phase 5.6 adds reply-target panel)."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

st.title("Next rep")
st.info("Phase 3 builds this view (§14.2). Reply-target queue lands in Phase 5.6.")
