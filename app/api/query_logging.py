"""
Background query logging for the /ask endpoint.

Writes to QueryLog so the admin panel can show query history.
Uses its own DB session (SessionLocal) so it is safe to run in a background task.
"""

from __future__ import annotations

import json
from app.sqlite.database import SessionLocal
from app.sqlite.models import User, QueryLog
from app.schemas.response import FinalResponse
from app.core.security import get_password_hash
from app.utils.logging import get_logger

logger = get_logger("askmojo.api.query_logging")

MAX_JSON_LENGTH = 500_000  # 500KB per JSON field
MAX_ANSWER_LENGTH = 1_000_000  # 1MB for answer


SYSTEM_USER_EMAIL = "system@askmojo.com"


def _get_or_create_system_user(db) -> User:
    """Get or create the system user for logging (by email, not id)."""
    user = db.query(User).filter(User.email == SYSTEM_USER_EMAIL).first()
    if user:
        return user
    user = User(
        name="System",
        email=SYSTEM_USER_EMAIL,
        password=get_password_hash("system"),
        role="system",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def log_query(
    question: str,
    slack_user_email: str | None,
    final: FinalResponse,
) -> None:
    """
    Write one row to query_logs. Safe to call from a background task.
    Uses a new DB session and does not depend on request scope.
    """
    db = SessionLocal()
    try:
        system_user = _get_or_create_system_user(db)
        meta = final.pipeline_metadata

        # Ensure string for DB (in case enum or other type slips through)
        intent = None
        if meta and meta.intent is not None:
            intent = getattr(meta.intent, "value", None) or getattr(meta.intent, "name", None) or str(meta.intent)
        response_type = (meta.answer_mode if meta else None) or "full_flow"
        if not isinstance(response_type, str):
            response_type = "full_flow"

        token_usage = final.token_usage or {}
        toon_savings = final.toon_savings or {}
        total_used = token_usage.get("total_tokens_used") or token_usage.get("total_json_tokens")
        total_without = token_usage.get("total_tokens_without_toon")
        savings = token_usage.get("total_savings") or toon_savings.get("total_savings")
        savings_pct = token_usage.get("total_savings_percent") or toon_savings.get("total_savings_percent")

        token_usage_json_str = None
        if token_usage:
            s = json.dumps(token_usage, default=str)
            token_usage_json_str = s[:MAX_JSON_LENGTH] + "..." if len(s) > MAX_JSON_LENGTH else s

        api_calls_json_str = None
        if "calls" in token_usage or "api_calls" in token_usage:
            raw = token_usage.get("api_calls") or token_usage.get("calls") or []
            s = json.dumps(raw if isinstance(raw, list) else token_usage, default=str)
            api_calls_json_str = s[:MAX_JSON_LENGTH] + "..." if len(s) > MAX_JSON_LENGTH else s

        toon_savings_json_str = None
        if toon_savings:
            s = json.dumps(toon_savings, default=str)
            toon_savings_json_str = s[:MAX_JSON_LENGTH] + "..." if len(s) > MAX_JSON_LENGTH else s

        answer = (final.answer or "")[:MAX_ANSWER_LENGTH]
        if len(final.answer or "") > MAX_ANSWER_LENGTH:
            answer = answer + "...[truncated]"

        log = QueryLog(
            user_id=system_user.id,
            query=question,
            intent=intent,
            response_type=response_type,
            used_internal_only=False,
            answer=answer,
            processing_time_seconds=final.processing_time_seconds,
            total_tokens_used=total_used,
            total_tokens_without_toon=total_without,
            token_savings=savings,
            token_savings_percent=savings_pct,
            token_usage_json=token_usage_json_str,
            api_calls_json=api_calls_json_str,
            toon_savings_json=toon_savings_json_str,
            slack_user_email=slack_user_email,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        # Console-friendly so you see each question logged in the terminal
        logger.info(
            "[LOG] Query logged | id=%s | time=%.2fs | intent=%s | slack=%s",
            log.id,
            final.processing_time_seconds or 0,
            intent or "—",
            slack_user_email or "—",
        )
    except Exception as e:
        logger.warning("Query logging failed: %s", e, exc_info=True)
    finally:
        db.close()
