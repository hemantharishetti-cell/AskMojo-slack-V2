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

MAX_JSON_LENGTH = 1_000_000  # 1MB per JSON field
MAX_ANSWER_LENGTH = 1_000_000  # 1MB for answer
MAX_CALL_TEXT_LENGTH = 40_000


SYSTEM_USER_EMAIL = "system@askmojo.com"


def _truncate_text(value: object, max_len: int = MAX_CALL_TEXT_LENGTH) -> object:
    if not isinstance(value, str):
        return value
    if len(value) <= max_len:
        return value
    return value[:max_len] + "...[truncated]"


def _make_json_string(payload: object, *, max_len: int = MAX_JSON_LENGTH) -> str | None:
    if payload in (None, "", [], {}):
        return None
    try:
        text = json.dumps(payload, default=str)
    except Exception:
        return None
    if len(text) <= max_len:
        return text
    return None


def _shrink_api_calls(raw_calls: object) -> list[dict]:
    calls = raw_calls if isinstance(raw_calls, list) else []
    shrunk: list[dict] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        shrunk.append({
            "call_name": call.get("call_name"),
            "model_used": call.get("model_used"),
            "tokens_used": call.get("tokens_used"),
            "time_taken_seconds": call.get("time_taken_seconds"),
            "request_prompt": _truncate_text(call.get("request_prompt")),
            "response_content": _truncate_text(call.get("response_content")),
        })
    return shrunk


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
            token_usage_for_log = dict(token_usage)
            token_usage_for_log.pop("api_calls", None)
            token_usage_for_log.pop("calls", None)
            token_usage_json_str = _make_json_string(token_usage_for_log)
            if token_usage_json_str is None:
                token_usage_json_str = _make_json_string({
                    "total_tokens_used": total_used,
                    "total_tokens_without_toon": total_without,
                    "total_savings": savings,
                    "total_savings_percent": savings_pct,
                    "note": "Detailed token usage omitted because payload exceeded storage limit.",
                })

        api_calls_json_str = None
        if "calls" in token_usage or "api_calls" in token_usage:
            raw = token_usage.get("api_calls") or token_usage.get("calls") or []
            api_calls_json_str = _make_json_string(raw)
            if api_calls_json_str is None:
                shrunk_calls = _shrink_api_calls(raw)
                api_calls_json_str = _make_json_string(shrunk_calls)
            if api_calls_json_str is None:
                api_calls_json_str = _make_json_string([{
                    "call_name": "log_storage_summary",
                    "response_content": "Detailed API call logs were truncated because payload exceeded storage limit.",
                    "tokens_used": total_used,
                }])

        toon_savings_json_str = None
        if toon_savings:
            toon_savings_json_str = _make_json_string(toon_savings)
            if toon_savings_json_str is None:
                toon_savings_json_str = _make_json_string({
                    "total_savings": toon_savings.get("total_savings"),
                    "total_savings_percent": toon_savings.get("total_savings_percent"),
                    "note": "Detailed TOON breakdown omitted because payload exceeded storage limit.",
                })

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
