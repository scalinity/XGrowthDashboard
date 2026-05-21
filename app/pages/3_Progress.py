"""Progress — stub (Phase 3 builds the view)."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

st.title("Progress")
st.info("Phase 3 builds this view (§14.3) — dual ladders and long-arc footer.")
