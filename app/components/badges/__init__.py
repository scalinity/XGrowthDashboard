"""Confidence + sample-size badges used by the lane-performance grid (§14.4)."""

from app.components.badges.confidence_label import (
    DB_LABEL_TO_UI,
    UI_LABEL_PRESENTATION,
    SAMPLE_SIZE_TOOLTIP,
    confidence_badge,
    ui_label_for_db_label,
)
from app.components.badges.sample_size import sample_size_badge

__all__ = [
    "DB_LABEL_TO_UI",
    "UI_LABEL_PRESENTATION",
    "SAMPLE_SIZE_TOOLTIP",
    "confidence_badge",
    "sample_size_badge",
    "ui_label_for_db_label",
]
