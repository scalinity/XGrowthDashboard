"""Funnel — stub (Phase 3 builds the view)."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

st.title("Funnel")
st.info(
    "Phase 3 builds this view (§14.5). App Store attribution gap surfaced "
    "explicitly per §13 hard rule 11."
)
