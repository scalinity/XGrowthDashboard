"""Reusable rendering primitives for the Phase 3 dashboard views.

Two subpackages:

- ``badges`` — confidence-label / sample-size pills with the §14.4 boundary
  rules baked into their tooltips.
- ``charts`` — Plotly-backed visualizations that bake in the §13 honesty
  rules (noise-floor bands, IQR error bars, visible App-Store-attribution
  gap).

Every component takes the data it needs as a Python object — never an open
DB connection — so the same primitives can be exercised from
``streamlit.testing.v1.AppTest`` fixtures without spinning up a real DB.
"""
