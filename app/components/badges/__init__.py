"""Confidence + sample-size badges used by the lane-performance grid (§14.4)."""

from app.components.badges.claim_confidence_chip import (
    CONFIDENCE_CHIP_PRESENTATION,
    claim_confidence_chip,
)
from app.components.badges.confidence_label import (
    DB_LABEL_TO_UI,
    UI_LABEL_PRESENTATION,
    SAMPLE_SIZE_TOOLTIP,
    confidence_badge,
    ui_label_for_db_label,
)
from app.components.badges.prepublish_label import (
    COMPOSITE_LABEL_PRESENTATION,
    prepublish_chip,
    render_score_panel,
)
from app.components.badges.repetition_warning import repetition_banner
from app.components.badges.sample_size import sample_size_badge

__all__ = [
    "COMPOSITE_LABEL_PRESENTATION",
    "CONFIDENCE_CHIP_PRESENTATION",
    "DB_LABEL_TO_UI",
    "UI_LABEL_PRESENTATION",
    "SAMPLE_SIZE_TOOLTIP",
    "claim_confidence_chip",
    "confidence_badge",
    "prepublish_chip",
    "render_score_panel",
    "repetition_banner",
    "sample_size_badge",
    "ui_label_for_db_label",
]
