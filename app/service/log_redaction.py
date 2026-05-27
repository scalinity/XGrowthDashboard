"""Redact sensitive values from sidecar logs (§31.5)."""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.paths import application_support_dir

REDACTED = "[REDACTED]"

_BEARER_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_TOKEN_PREFIX_PATTERN = re.compile(
    r"(XGROWTH_TOKEN=|Authorization:\s*Bearer\s+)([^\s\"']+)",
    re.IGNORECASE,
)
_SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_XAI_PATTERN = re.compile(r"\bxai-[A-Za-z0-9_-]{8,}\b")
_PUBLISH_TOKEN_PATTERN = re.compile(r"\bpublish[_-]?token[=:\s]+[^\s\"']+", re.IGNORECASE)


def collect_configured_secrets() -> list[str]:
    """Return non-empty secret values currently configured in the process."""
    names = (
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "X_API_BEARER_TOKEN",
        "X_API_ACCESS_TOKEN",
        "X_API_REFRESH_TOKEN",
    )
    values = [os.environ.get(name, "") for name in names]
    return [value for value in values if value]


def redact_text(text: str, extra_secrets: list[str] | None = None) -> str:
    if not text:
        return text
    redacted = _BEARER_PATTERN.sub(f"Bearer {REDACTED}", text)
    redacted = _TOKEN_PREFIX_PATTERN.sub(rf"\1{REDACTED}", redacted)
    redacted = _SK_PATTERN.sub(REDACTED, redacted)
    redacted = _XAI_PATTERN.sub(REDACTED, redacted)
    redacted = _PUBLISH_TOKEN_PATTERN.sub(f"publish_token={REDACTED}", redacted)
    for secret in extra_secrets or collect_configured_secrets():
        if secret and secret in redacted:
            redacted = redacted.replace(secret, REDACTED)
    return redacted


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.getMessage()))
        record.args = ()
        return True


def sidecar_log_path() -> Path:
    return application_support_dir() / "logs" / "sidecar.log"


def configure_sidecar_logging() -> Path:
    """Configure bounded, redacted sidecar logging under Application Support."""
    log_dir = sidecar_log_path().parent
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        sidecar_log_path(),
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for existing in list(root.handlers):
        if getattr(existing, "_xgrowth_sidecar_handler", False):
            root.removeHandler(existing)
    handler._xgrowth_sidecar_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    return sidecar_log_path()
