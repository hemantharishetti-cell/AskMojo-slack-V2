"""
Slack Socket Mode handler — refactored.

Key change: calls `orchestrator.run_pipeline()` directly instead
of making an HTTP POST back to its own `/api/v1/ask` endpoint.
This eliminates the ~200-500ms HTTP loopback latency.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from app.adapters.slack_adapter import format_for_slack, format_as_blocks
from app.core.config import settings
from app.sqlite.database import SessionLocal
from app.sqlite.models import SlackIntegration, SlackUser

logger = logging.getLogger("askmojo.adapters.slack_socket")

# ── Deduplication cache ─────────────────────────────────────────────
_processed_messages: dict[str, str | datetime] = {}
_processed_messages_lock = threading.Lock()
_CLEANUP_INTERVAL = timedelta(minutes=5)


def _cleanup():
    now = datetime.utcnow()
    with _processed_messages_lock:
        expired = [
            ts for ts, st in _processed_messages.items()
            if isinstance(st, datetime) and (now - st > _CLEANUP_INTERVAL)
        ]
        for ts in expired:
            del _processed_messages[ts]


def _try_mark_processing(ts: str) -> bool:
    _cleanup()
    with _processed_messages_lock:
        if ts in _processed_messages:
            return False
        _processed_messages[ts] = "processing"
        return True


def _mark_done(ts: str):
    with _processed_messages_lock:
        _processed_messages[ts] = datetime.utcnow()


def _unmark(ts: str):
    with _processed_messages_lock:
        if _processed_messages.get(ts) == "processing":
            del _processed_messages[ts]


# ── Global client ───────────────────────────────────────────────────
_socket_client: Optional[SocketModeClient] = None


# ── Message processing ──────────────────────────────────────────────

async def process_slack_message(
    event: dict,
    bot_token: str,
) -> None:
    """
    Process a Slack message by calling the pipeline directly.

    Eliminates the HTTP loopback to /api/v1/ask.
    """
    message_ts = event.get("ts")
    marked = False
    if message_ts:
        if not _try_mark_processing(message_ts):
            return
        marked = True

    # Ignore bot messages
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        if marked and message_ts:
            _unmark(message_ts)
        return

    if event.get("subtype") and event.get("subtype") not in [None, "thread_broadcast"]:
        if marked and message_ts:
            _unmark(message_ts)
        return

    user_id = event.get("user")
    if not user_id:
        if marked and message_ts:
            _unmark(message_ts)
        return

    # Check if it's the bot itself
    try:
        client = WebClient(token=bot_token)
        bot_info = client.auth_test()
        if bot_info.get("ok") and user_id == bot_info.get("user_id"):
            if marked and message_ts:
                _unmark(message_ts)
            return
    except Exception:
        pass

    # Extract question
    question = event.get("text", "").strip()
    question = re.sub(r"<@[A-Z0-9]+>\s*", "", question).strip()
    if not question:
        if marked and message_ts:
            _unmark(message_ts)
        return

    # Ignore bot error echoes
    error_phrases = ["sorry, you are not registered", "access denied", "not registered to use this slack app"]
    if any(p in question.lower() for p in error_phrases) and len(question) < 300:
        if marked and message_ts:
            _unmark(message_ts)
        return

    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")

    # Open DM if needed
    if not channel_id and user_id:
        try:
            client = WebClient(token=bot_token)
            dm = client.conversations_open(users=[user_id])
            if dm.get("ok"):
                channel_id = dm["channel"]["id"]
        except Exception:
            if marked and message_ts:
                _unmark(message_ts)
            return

    if not channel_id:
        if marked and message_ts:
            _unmark(message_ts)
        return

    is_dm = channel_id.startswith("D") if channel_id else False

    # Check user registration
    db = SessionLocal()
    try:
        slack_user = db.query(SlackUser).filter(
            SlackUser.slack_user_id == user_id,
            SlackUser.is_registered == True,
        ).first()
    except Exception:
        if marked and message_ts:
            _unmark(message_ts)
        db.close()
        return

    if not slack_user:
        _send_error(bot_token, channel_id, is_dm, thread_ts, event)
        if message_ts:
            _mark_done(message_ts)
        db.close()
        return

    slack_email = slack_user.email

    # ── Call pipeline directly (no HTTP loopback) ────────────────────
    try:
        from app.pipeline.orchestrator import run_pipeline, pipeline_response_to_ask_response

        final = await run_pipeline(
            question=question,
            db=db,
            slack_user_email=slack_email,
        )
        answer_text = final.answer
    except Exception as e:
        logger.error("Pipeline error: %s", e, exc_info=True)
        answer_text = f"Sorry, I encountered an error processing your question: {e}"
    finally:
        db.close()

    # Send response
    formatted = format_for_slack(answer_text)
    blocks_payload = format_as_blocks(answer_text)

    try:
        client = WebClient(token=bot_token)
        kwargs: dict = {
            "channel": channel_id,
            "text": formatted,
            "blocks": blocks_payload.get("blocks", []),
            "mrkdwn": True,
        }
        if not is_dm and thread_ts and thread_ts != event.get("ts"):
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)
        if message_ts:
            _mark_done(message_ts)
    except Exception as e:
        logger.error("Error sending Slack response: %s", e)
        try:
            client = WebClient(token=bot_token)
            kwargs = {"channel": channel_id, "text": formatted}
            if not is_dm and thread_ts and thread_ts != event.get("ts"):
                kwargs["thread_ts"] = thread_ts
            client.chat_postMessage(**kwargs)
            if message_ts:
                _mark_done(message_ts)
        except Exception:
            if marked and message_ts:
                _unmark(message_ts)


def _send_error(bot_token: str, channel_id: str, is_dm: bool, thread_ts: str | None, event: dict):
    """Send access-denied message."""
    try:
        client = WebClient(token=bot_token)
        msg = "Sorry, you are not registered to use this Slack app. Please contact your administrator."
        kwargs: dict = {
            "channel": channel_id,
            "text": msg,
            "blocks": [{
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Access Denied*\n\n{msg}"},
            }],
        }
        if not is_dm and thread_ts and thread_ts != event.get("ts"):
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)
    except Exception as e:
        logger.error("Error sending access denied: %s", e)


# ── Socket Mode handlers (kept from original) ──────────────────────

def _get_config() -> Optional[SlackIntegration]:
    db = SessionLocal()
    try:
        return db.query(SlackIntegration).filter(
            SlackIntegration.is_active == True,
            SlackIntegration.socket_mode_enabled == True,
        ).first()
    finally:
        db.close()


def process_socket_mode_request(client: SocketModeClient, req: SocketModeRequest):
    """Process incoming Socket Mode requests."""
    try:
        if req.type == "events_api":
            response = SocketModeResponse(envelope_id=req.envelope_id)
            client.send_socket_mode_response(response)

            event = req.payload.get("event", {})
            event_type = event.get("type")
            config = _get_config()
            if not config or not config.bot_token:
                return

            if event_type in ("message", "app_mention"):

                def _run():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(
                            process_slack_message(event, config.bot_token)
                        )
                    finally:
                        loop.close()

                threading.Thread(target=_run, daemon=True).start()

    except Exception as e:
        logger.error("Socket Mode error: %s", e)


def start_socket_mode_client(app_token: str, bot_token: str) -> bool:
    """Start the Socket Mode client."""
    global _socket_client
    stop_socket_mode_client()
    try:
        sm = SocketModeClient(
            app_token=app_token,
            web_client=WebClient(token=bot_token),
        )
        sm.socket_mode_request_listeners.append(process_socket_mode_request)
        _socket_client = sm

        def _connect():
            try:
                sm.connect()
            except Exception as e:
                logger.error("Socket Mode connect error: %s", e)

        threading.Thread(target=_connect, daemon=True).start()
        logger.info("Slack Socket Mode client started")
        return True
    except Exception as e:
        logger.error("Failed to start Socket Mode: %s", e)
        return False


def stop_socket_mode_client():
    global _socket_client
    if _socket_client:
        try:
            _socket_client.disconnect()
        except Exception:
            pass
        _socket_client = None


def restart_socket_mode_client() -> bool:
    config = _get_config()
    if config and config.socket_mode_enabled and config.app_token and config.bot_token:
        return start_socket_mode_client(config.app_token, config.bot_token)
    stop_socket_mode_client()
    return False
