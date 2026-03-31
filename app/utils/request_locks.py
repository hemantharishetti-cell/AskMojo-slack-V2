from __future__ import annotations

from datetime import datetime, timedelta
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


USER_REQUEST_BUSY_MESSAGE = (
    "Please kindly wait until your previous query is being processed. "
    "You can ask once that is completed."
)

_LOCK_STALE_AFTER_SECONDS = 15 * 60


def normalize_user_request_key(
    *,
    request_user_key: str | None = None,
    slack_user_email: str | None = None,
    slack_user_id: str | None = None,
) -> str | None:
    if request_user_key and request_user_key.strip():
        return request_user_key.strip().lower()
    if slack_user_email and slack_user_email.strip():
        return f"email:{slack_user_email.strip().lower()}"
    if slack_user_id and slack_user_id.strip():
        return f"slack:{slack_user_id.strip().lower()}"
    return None


def generate_processing_request_id() -> str:
    return str(uuid.uuid4())


def _resolve_lookup(
    *,
    request_user_key: str | None = None,
    slack_user_email: str | None = None,
    slack_user_id: str | None = None,
) -> tuple[str, str] | None:
    normalized_key = normalize_user_request_key(
        request_user_key=request_user_key,
        slack_user_email=slack_user_email,
        slack_user_id=slack_user_id,
    )
    if not normalized_key:
        return None

    if normalized_key.startswith("slack:"):
        return ("slack_user_id", normalized_key.split(":", 1)[1])
    if normalized_key.startswith("email:"):
        return ("email", normalized_key.split(":", 1)[1])
    if "@" in normalized_key:
        return ("email", normalized_key)
    return ("slack_user_id", normalized_key)


def is_user_request_in_progress(
    db: Session,
    *,
    request_user_key: str | None = None,
    slack_user_email: str | None = None,
    slack_user_id: str | None = None,
) -> bool:
    lookup = _resolve_lookup(
        request_user_key=request_user_key,
        slack_user_email=slack_user_email,
        slack_user_id=slack_user_id,
    )
    if lookup is None:
        return False

    field_name, field_value = lookup
    stale_cutoff = datetime.utcnow() - timedelta(seconds=_LOCK_STALE_AFTER_SECONDS)
    query = text(
        f"""
        SELECT 1
        FROM slack_users
        WHERE LOWER({field_name}) = :field_value
          AND is_registered = 1
          AND is_processing = 1
          AND processing_started_at IS NOT NULL
          AND processing_started_at >= :stale_cutoff
        LIMIT 1
        """
    )
    result = db.execute(
        query,
        {
            "field_value": field_value,
            "stale_cutoff": stale_cutoff,
        },
    ).first()
    return result is not None


def try_acquire_user_request(
    db: Session,
    *,
    processing_request_id: str,
    request_user_key: str | None = None,
    slack_user_email: str | None = None,
    slack_user_id: str | None = None,
) -> bool:
    lookup = _resolve_lookup(
        request_user_key=request_user_key,
        slack_user_email=slack_user_email,
        slack_user_id=slack_user_id,
    )
    if lookup is None:
        return True

    field_name, field_value = lookup
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(seconds=_LOCK_STALE_AFTER_SECONDS)
    query = text(
        f"""
        UPDATE slack_users
        SET is_processing = 1,
            processing_started_at = :now,
            processing_request_id = :processing_request_id,
            updated_at = :now
        WHERE LOWER({field_name}) = :field_value
          AND is_registered = 1
          AND (
                is_processing = 0
             OR processing_started_at IS NULL
             OR processing_started_at < :stale_cutoff
             OR processing_request_id = :processing_request_id
          )
        """
    )
    try:
        result = db.execute(
            query,
            {
                "now": now,
                "field_value": field_value,
                "stale_cutoff": stale_cutoff,
                "processing_request_id": processing_request_id,
            },
        )
        db.commit()
        return (result.rowcount or 0) > 0
    except Exception:
        db.rollback()
        raise


def release_user_request(
    db: Session,
    *,
    processing_request_id: str,
    request_user_key: str | None = None,
    slack_user_email: str | None = None,
    slack_user_id: str | None = None,
) -> None:
    lookup = _resolve_lookup(
        request_user_key=request_user_key,
        slack_user_email=slack_user_email,
        slack_user_id=slack_user_id,
    )
    if lookup is None:
        return

    field_name, field_value = lookup
    now = datetime.utcnow()
    query = text(
        f"""
        UPDATE slack_users
        SET is_processing = 0,
            processing_started_at = NULL,
            processing_request_id = NULL,
            updated_at = :now
        WHERE LOWER({field_name}) = :field_value
          AND processing_request_id = :processing_request_id
        """
    )
    try:
        db.execute(
            query,
            {
                "now": now,
                "field_value": field_value,
                "processing_request_id": processing_request_id,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
