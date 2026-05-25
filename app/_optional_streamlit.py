"""Optional Streamlit import for modules shared by both presentation surfaces.

Several `app.components.*` and `app.forms.*` modules contain both pure logic
(submit/compute helpers the FastAPI sidecar calls) and Streamlit `render()`
functions (used only by the `streamlit run` surface). The frozen sidecar
(§31.6) deliberately excludes Streamlit from its PyInstaller bundle, so a
module-level ``import streamlit`` crashes the sidecar at import time — before it
can print its handshake — leaving the native app stuck "loading".

These modules import ``st`` from here instead. In the Streamlit surface ``st``
is the real module; in the frozen sidecar it is ``None``. By contract the
sidecar never calls the ``st.*`` render functions (it only imports the pure
logic), so ``st`` being ``None`` there is safe — any accidental call surfaces
loudly as an ``AttributeError`` rather than silently mis-rendering.
"""

from __future__ import annotations

try:
    import streamlit as st
except ImportError:  # pragma: no cover - exercised only in the frozen sidecar
    st = None  # type: ignore[assignment]

__all__ = ["st"]
