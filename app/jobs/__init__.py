"""Background jobs scoped to single-user / local-only operation.

These are idempotent, transactional, and safe to call on every Streamlit
boot. They are NOT scheduled processes — the dashboard re-enters them on
each app start.
"""
