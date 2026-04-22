import logging
from typing import Any


logger = logging.getLogger("monitoring.error_handler")


def handle_error(
    *,
    stage: str,
    user: str | None = None,
    query: str | None = None,
    error: Exception | str | None = None,
    severity: str = "ERROR",
    **extra: Any,
) -> None:
    """Best-effort local error capture used by API and Slack flows."""
    err_text = str(error) if error is not None else "unknown_error"
    payload = {
        "stage": stage,
        "user": user or "unknown",
        "query": (query or "")[:500],
        "severity": severity,
        "error": err_text,
    }
    if extra:
        payload["extra"] = extra

    level = getattr(logging, str(severity).upper(), logging.ERROR)
    logger.log(level, "Captured application error | %s", payload)
