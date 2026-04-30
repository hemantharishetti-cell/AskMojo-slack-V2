"""
Slack Socket Mode handler for real-time event processing.
This module handles WebSocket connections to Slack when Socket Mode is enabled.
"""
import asyncio
import logging
import re
from typing import Optional, Dict, Any
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from sqlalchemy.orm import Session
from app.sqlite.database import SessionLocal
from app.sqlite.models import SlackIntegration, SlackUser
from app.schemas.response import AskRequest
from app.core.config import settings
from app.utils.request_locks import (
    USER_REQUEST_BUSY_MESSAGE,
    is_user_request_in_progress,
)
from app.api.ask import _ask_question_impl
from datetime import datetime, timedelta
import threading
import atexit

logger = logging.getLogger(__name__)

_MARKDOWN_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")

# In-memory cache to prevent processing the same message multiple times
# Format: {message_ts: processed_at}
# States: "processing" = currently being processed, datetime = already processed
_processed_messages = {}
_processed_messages_lock = threading.Lock()
CLEANUP_INTERVAL = timedelta(minutes=5)  # Clean up old entries after 5 minutes


class _SlackSdkNoiseFilter(logging.Filter):
    """Throttle repeated low-signal Slack SDK reconnect noise."""

    def __init__(self, cooldown_seconds: int = 20):
        super().__init__()
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self._last_seen_at: Dict[str, datetime] = {}
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        normalized = self._normalize_message(message)
        if normalized is None:
            return True

        now = datetime.utcnow()
        with self._lock:
            last_seen_at = self._last_seen_at.get(normalized)
            if last_seen_at and now - last_seen_at < self.cooldown:
                return False
            self._last_seen_at[normalized] = now
        return True

    @staticmethod
    def _normalize_message(message: str) -> Optional[str]:
        if "getaddrinfo failed" in message:
            return "dns_resolution_failed"
        if "Going to retry the same request: POST https://slack.com/api/apps.connections.open" in message:
            return "apps_connections_open_retry"
        if "The session seems to be already closed. Reconnecting..." in message:
            return "session_reconnecting"
        return None


def _cleanup_processed_messages():
    """Remove old entries from the processed messages cache."""
    now = datetime.utcnow()
    with _processed_messages_lock:
        keys_to_remove = [
            ts for ts, status in _processed_messages.items()
            if isinstance(status, datetime) and (now - status > CLEANUP_INTERVAL)
        ]
        for ts in keys_to_remove:
            del _processed_messages[ts]


def _is_message_processed(message_ts: str) -> bool:
    """Check if a message has already been processed or is currently being processed."""
    _cleanup_processed_messages()
    with _processed_messages_lock:
        return message_ts in _processed_messages


def _mark_message_processing(message_ts: str) -> bool:
    """
    Mark a message as being processed.
    Returns True if message can be processed (not already processing/processed), False otherwise.
    This prevents race conditions where the same message is processed multiple times concurrently.
    """
    with _processed_messages_lock:
        if message_ts in _processed_messages:
            # Already processing or processed
            return False
        # Mark as processing
        _processed_messages[message_ts] = "processing"
        return True


def _unmark_message_processing(message_ts: str):
    """Unmark a message from processing state (if processing failed, allow retry)."""
    with _processed_messages_lock:
        if message_ts in _processed_messages and _processed_messages[message_ts] == "processing":
            del _processed_messages[message_ts]


def _mark_message_processed(message_ts: str):
    """Mark a message as fully processed (after response sent)."""
    with _processed_messages_lock:
        _processed_messages[message_ts] = datetime.utcnow()

# Global Socket Mode client instance
_socket_mode_client: Optional[SocketModeClient] = None
_socket_mode_task: Optional[asyncio.Task] = None
_shutting_down = False
_lifecycle_lock = threading.Lock()
_socket_mode_status_lock = threading.Lock()
_slack_sdk_logger = logging.getLogger(f"{__name__}.sdk")

if not any(isinstance(existing_filter, _SlackSdkNoiseFilter) for existing_filter in _slack_sdk_logger.filters):
    _slack_sdk_logger.addFilter(_SlackSdkNoiseFilter())

_socket_mode_status: Dict[str, Any] = {
    "state": "stopped",
    "connected": False,
    "last_error": None,
    "last_connected_at": None,
    "last_disconnected_at": None,
    "last_status_change_at": datetime.utcnow(),
}


def _update_socket_mode_status(
    state: str,
    *,
    connected: bool,
    last_error: Optional[str] = None,
    connected_at: Optional[datetime] = None,
    disconnected_at: Optional[datetime] = None,
):
    with _socket_mode_status_lock:
        _socket_mode_status["state"] = state
        _socket_mode_status["connected"] = connected
        _socket_mode_status["last_error"] = last_error
        if connected_at is not None:
            _socket_mode_status["last_connected_at"] = connected_at
        if disconnected_at is not None:
            _socket_mode_status["last_disconnected_at"] = disconnected_at
        _socket_mode_status["last_status_change_at"] = datetime.utcnow()


def get_socket_mode_runtime_status() -> Dict[str, Any]:
    """Return the current runtime status for Slack Socket Mode."""
    with _socket_mode_status_lock:
        return dict(_socket_mode_status)


def _describe_socket_mode_error(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    if "getaddrinfo failed" in message.lower():
        return "DNS lookup to Slack failed. Check internet access, DNS, VPN, or proxy settings."
    return message


def _handle_socket_mode_error(error: Exception):
    described_error = _describe_socket_mode_error(error)
    _update_socket_mode_status("degraded", connected=False, last_error=described_error)
    logger.warning("Slack Socket Mode error: %s", described_error)


def _handle_socket_mode_close(code: int, reason: Optional[str] = None):
    if _shutting_down:
        return
    details = f"Slack Socket Mode connection closed (code={code}"
    if reason:
        details += f", reason={reason}"
    details += ")"
    _update_socket_mode_status(
        "reconnecting",
        connected=False,
        last_error=details,
        disconnected_at=datetime.utcnow(),
    )
    logger.warning("%s", details)


def format_slack_message(text: str) -> dict:
    """
    Format a text response into Slack Block Kit format with rich formatting.
    Supports markdown, lists, bold, italic, and emojis.
    
    Args:
        text: Plain text response from the AI
        
    Returns:
        Dictionary with 'blocks' for Slack Block Kit
    """
    import re
    
    blocks = []
    
    # Split by double newlines to get major sections
    sections = re.split(r'\n\n+', text)
    
    for section in sections:
        section = section.strip()
        if not section:
            continue
        
        lines = section.split('\n')
        
        # Check if this section is a list
        is_list = False
        list_items = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Check for list markers
            list_match = re.match(r'^([\-\*•]|\d+[\.\)])\s+(.+)$', line)
            if list_match:
                is_list = True
                item_text = list_match.group(2)
                # Format the item text (preserve any markdown)
                item_text = format_markdown(item_text)
                list_items.append(item_text)
            else:
                # If we've started collecting list items and hit a non-list line,
                # add the list block and continue with regular text
                if list_items:
                    list_text = '\n'.join([f"• {item}" for item in list_items])
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": list_text
                        }
                    })
                    list_items = []
                    is_list = False
                
                # Format regular line
                formatted_line = format_markdown(line)
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": formatted_line
                    }
                })
        
        # If we ended with a list, add it
        if list_items:
            list_text = '\n'.join([f"• {item}" for item in list_items])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": list_text
                }
            })
    
    # If no blocks were created, create a simple text block
    if not blocks:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": format_markdown(text)
            }
        })
    
    return {"blocks": blocks}


def format_markdown(text: str) -> str:
    """
    Convert markdown-like formatting to Slack mrkdwn format.
    
    Args:
        text: Text with markdown formatting
        
    Returns:
        Slack mrkdwn formatted text
    """
    import re
    
    # Convert markdown bold **text** to Slack bold *text*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    
    # Convert markdown italic _text_ to Slack italic _text_
    # But be careful not to convert underscores in code or URLs
    text = re.sub(r'(?<!`)_([^_`]+?)_(?!`)', r'_\1_', text)
    
    # Convert markdown code `text` to Slack code `text`
    text = re.sub(r'`(.+?)`', r'`\1`', text)
    
    # Convert markdown links [text](url) to Slack links <url|text>
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<\2|\1>', text)
    
    # Convert markdown headers (# Header) to bold
    text = re.sub(r'^#+\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    
    # Preserve emojis (they should already be in the text)
    # Slack supports emojis natively
    
    return text


def _split_markdown_table_row(row_text: str) -> list[str]:
    """Split a markdown table row into cells while honoring escaped pipes."""
    row = (row_text or "").strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]

    cells = []
    current = []
    escaped = False

    for ch in row:
        if escaped:
            if ch != "|":
                current.append("\\")
            current.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "|":
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(ch)

    if escaped:
        current.append("\\")

    cells.append("".join(current).strip())
    return cells


def _is_markdown_table_separator(line: str, expected_columns: Optional[int] = None) -> bool:
    cells = _split_markdown_table_row(line)
    if len(cells) < 2:
        return False
    if expected_columns is not None and len(cells) != expected_columns:
        return False
    return all(_MARKDOWN_TABLE_SEPARATOR_CELL_RE.match(cell.replace(" ", "")) for cell in cells)


def _try_parse_markdown_table(lines: list[str], start_index: int) -> tuple[Optional[dict], int]:
    """
    Parse a markdown table starting at start_index.
    Returns ({header: [...], rows: [[...], ...]}, consumed_line_count) or (None, 0).
    """
    if start_index + 1 >= len(lines):
        return None, 0

    header_line = (lines[start_index] or "").strip()
    separator_line = (lines[start_index + 1] or "").strip()

    if "|" not in header_line:
        return None, 0

    header_cells = _split_markdown_table_row(header_line)
    if len(header_cells) < 2:
        return None, 0

    if not _is_markdown_table_separator(separator_line, expected_columns=len(header_cells)):
        return None, 0

    rows = []
    line_index = start_index + 2
    max_rows = 99  # Slack limit is 100 rows total; keep 1 for header.

    while line_index < len(lines):
        raw_line = lines[line_index]
        stripped = (raw_line or "").strip()
        if not stripped:
            break
        if "|" not in stripped:
            break
        if _is_markdown_table_separator(stripped):
            break

        row_cells = _split_markdown_table_row(stripped)
        if len(row_cells) < 2:
            break

        if len(row_cells) < len(header_cells):
            row_cells.extend([""] * (len(header_cells) - len(row_cells)))
        elif len(row_cells) > len(header_cells):
            row_cells = row_cells[: len(header_cells)]

        rows.append(row_cells)
        line_index += 1
        if len(rows) >= max_rows:
            break

    if not rows:
        return None, 0

    return {"header": header_cells, "rows": rows}, line_index - start_index


def _segment_answer_text(text: str) -> list[dict]:
    """Split answer text into alternating text/table segments."""
    lines = (text or "").splitlines()
    segments = []
    text_buffer = []
    i = 0

    while i < len(lines):
        parsed_table, consumed = _try_parse_markdown_table(lines, i)
        if parsed_table and consumed > 0:
            buffered_text = "\n".join(text_buffer).strip()
            if buffered_text:
                segments.append({"type": "text", "text": buffered_text})
            segments.append({"type": "table", "table": parsed_table})
            text_buffer = []
            i += consumed
            continue

        text_buffer.append(lines[i])
        i += 1

    buffered_text = "\n".join(text_buffer).strip()
    if buffered_text:
        segments.append({"type": "text", "text": buffered_text})

    return segments


def _build_slack_table_block(table_data: dict) -> Optional[dict]:
    header = table_data.get("header", [])
    rows = table_data.get("rows", [])
    if not header or not rows:
        return None

    column_count = min(len(header), 20)
    if column_count < 2:
        return None

    def _raw_cell(cell_text: str) -> dict:
        compact = re.sub(r"\s+", " ", (cell_text or "").strip())
        if len(compact) > 500:
            compact = compact[:497] + "..."
        return {"type": "raw_text", "text": compact or " "}

    table_rows = []
    table_rows.append([_raw_cell(cell) for cell in header[:column_count]])
    for row in rows:
        row_cells = row[:column_count]
        if all(not (cell or "").strip() for cell in row_cells):
            continue
        table_rows.append([_raw_cell(cell) for cell in row_cells])

    if len(table_rows) < 2:
        return None

    return {
        "type": "table",
        "column_settings": [{"is_wrapped": True} for _ in range(column_count)],
        "rows": table_rows[:100],
    }


def _table_to_readable_text(table_data: dict) -> str:
    """Fallback representation for extra tables when Slack allows only one table."""
    header = table_data.get("header", [])
    rows = table_data.get("rows", [])
    if not header or not rows:
        return ""

    preview_rows = rows[:10]
    lines = []
    for row in preview_rows:
        pairs = []
        for col_name, col_value in zip(header, row):
            key = format_markdown(col_name) if col_name else "Column"
            value = format_markdown(col_value) if col_value else "-"
            pairs.append(f"*{key}*: {value}")
        lines.append("- " + "; ".join(pairs))

    if len(rows) > len(preview_rows):
        lines.append(f"- ... and {len(rows) - len(preview_rows)} more rows")

    return "\n".join(lines)


def _build_slack_fallback_text(answer_text: str) -> str:
    """Build plain text fallback for notifications/push previews."""
    cleaned_lines = []
    for raw_line in (answer_text or "").splitlines():
        stripped = raw_line.strip()
        if _is_markdown_table_separator(stripped):
            continue
        if "|" in stripped:
            cells = _split_markdown_table_row(stripped)
            if len(cells) >= 2:
                cleaned_lines.append(" | ".join(cell for cell in cells if cell))
                continue
        cleaned_lines.append(raw_line)

    fallback = "\n".join(cleaned_lines).strip()
    if not fallback:
        fallback = "Response from ASKMOJO"
    if len(fallback) > 3000:
        fallback = fallback[:2997] + "..."
    return fallback


def build_slack_response_payload(answer_text: str) -> dict:
    """
    Build a Slack message payload.
    - Converts the first markdown table to a native Slack table block.
    - Keeps surrounding prose as regular section blocks.
    - Falls back safely to existing formatting when parsing fails.
    """
    segments = _segment_answer_text(answer_text)
    if not segments:
        basic = format_slack_message(answer_text)
        return {"text": _build_slack_fallback_text(answer_text), "blocks": basic.get("blocks", [])}

    blocks = []
    table_attachment = None

    for segment in segments:
        if segment.get("type") == "text":
            text_part = (segment.get("text") or "").strip()
            if text_part:
                blocks.extend(format_slack_message(text_part).get("blocks", []))
            continue

        if segment.get("type") == "table":
            table_data = segment.get("table") or {}
            if table_attachment is None:
                table_block = _build_slack_table_block(table_data)
                if table_block:
                    table_attachment = {"blocks": [table_block]}
                    continue

            fallback_table_text = _table_to_readable_text(table_data)
            if fallback_table_text:
                blocks.extend(format_slack_message(fallback_table_text).get("blocks", []))

    if not blocks:
        if table_attachment is not None:
            blocks = format_slack_message("Structured comparison table:").get("blocks", [])
        else:
            blocks = format_slack_message(answer_text).get("blocks", [])

    payload = {
        "text": _build_slack_fallback_text(answer_text),
        "blocks": blocks,
    }
    if table_attachment is not None:
        payload["attachments"] = [table_attachment]
    return payload


def get_slack_config_from_db() -> Optional[SlackIntegration]:
    """Get active Slack configuration from database."""
    db = SessionLocal()
    global _shutting_down
    if _shutting_down:
        return

    try:
        config = db.query(SlackIntegration).filter(
            SlackIntegration.is_active == True,
            SlackIntegration.socket_mode_enabled == True
        ).first()
        return config
    finally:
        db.close()


async def handle_app_home_opened(event: dict, bot_token: str):
    """
    Handle when a user opens the app home.
    Updates the app home view with a welcome message.
    
    Args:
        event: Slack app_home_opened event
        bot_token: Bot token for updating views
    """
    try:
        user_id = event.get("user")
        if not user_id:
            return
        
        client = WebClient(token=bot_token)
        
        # Create a welcome view for the app home
        home_view = {
            "type": "home",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Welcome to ASKMOJO!* 🤖\n\nI'm here to help answer your questions based on your organization's knowledge base."
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*How to use:*\n• Send me a message directly in this app\n• Ask questions about your documents and knowledge base\n• I'll provide detailed answers based on the available information"
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Example questions:*\n• What is aftershoot?\n• Who are the members in people and culture?\n• How many holidays do we have in 2026?"
                    }
                }
            ]
        }
        
        # Update the app home view
        client.views_publish(
            user_id=user_id,
            view=home_view
        )
        
        logger.info(f"Updated app home for user {user_id}")
        
    except Exception as e:
        logger.error(f"Error handling app home opened: {e}")


async def process_slack_message(
    event: dict,
    bot_token: str,
    base_url: str = "http://168.144.110.180:8000"
) -> None:
    """
    Process a Slack message event following this flow:
    Step 1: Query received from the user
    Step 2: Check SQLite if user is registered
    Step 3: If not exists, return message that user does not exist
    Step 4: If user exists, use POST /api/v1/ask endpoint to get answer
    Step 5: Respond back to Slack
    
    Args:
        event: Slack event data
        bot_token: Bot token for sending responses
        base_url: Base URL of the API server
    """
    # ====================================================================
    # DEDUPLICATION: Prevent processing duplicate messages
    # ====================================================================
    message_ts = event.get("ts")
    message_marked_processing = False
    if message_ts:
        if not _mark_message_processing(message_ts):
            logger.debug(f"Message {message_ts} already processing/processed, skipping duplicate event")
            return
        message_marked_processing = True
    
    # ====================================================================
    # FILTER: Ignore bot messages and invalid subtypes
    # ====================================================================
    # Ignore messages from bots (check bot_id first)
    if event.get("bot_id"):
        logger.debug(f"Ignoring message from bot (bot_id: {event.get('bot_id')})")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        return
    
    # Ignore bot_message subtype
    if event.get("subtype") == "bot_message":
        logger.debug("Ignoring message with bot_message subtype")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        return
    
    # Ignore other invalid subtypes (but allow thread_broadcast)
    if event.get("subtype") and event.get("subtype") not in [None, "thread_broadcast"]:
        logger.debug(f"Ignoring message with subtype: {event.get('subtype')}")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        return
    
    # Get user_id and check if it's the bot itself
    user_id = event.get("user")
    if not user_id:
        logger.debug("No user_id in event, skipping")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        return
    
    # Get bot's own user ID and ignore messages from the bot itself
    try:
        client = WebClient(token=bot_token)
        bot_info = client.auth_test()
        if bot_info.get("ok"):
            bot_user_id = bot_info.get("user_id")
            if user_id == bot_user_id:
                logger.debug(f"Ignoring message from bot itself (user_id: {user_id})")
                if message_marked_processing and message_ts:
                    _unmark_message_processing(message_ts)
                return
    except Exception as e:
        logger.debug(f"Could not verify bot user ID: {e}")
        # Continue processing if we can't verify (don't block legitimate messages)
    
    # ====================================================================
    # STEP 1: Query received from the user
    # ====================================================================
    
    # Extract question text
    question_text = event.get("text", "").strip()
    import re
    question_text = re.sub(r'<@[A-Z0-9]+>\s*', '', question_text).strip()  # Remove bot mentions
    
    if not question_text:
        logger.debug("No question text found in message")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        return
    
    # Ignore messages that are exactly our bot's error messages (prevent echo loops)
    # This prevents the bot's own error messages from being processed as user queries
    bot_error_phrases = [
        "sorry, you are not registered",
        "access denied",
        "not registered to use this slack app"
    ]
    question_lower = question_text.lower()
    # Check if the message starts with or contains our error message phrases
    if any(phrase in question_lower for phrase in bot_error_phrases) and len(question_text) < 300:
        logger.debug(f"Ignoring message that appears to be bot error message echo: {question_text[:50]}...")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        return
    
    logger.info(f"Step 1: Query received from user {user_id}: {question_text[:100]}...")
    
    # Get channel info for responding
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    
    # Handle app home messages (no channel_id)
    if not channel_id and user_id:
        try:
            client = WebClient(token=bot_token)
            dm_response = client.conversations_open(users=[user_id])
            if dm_response.get("ok"):
                channel_id = dm_response["channel"]["id"]
                logger.info(f"Opened DM channel {channel_id} for user {user_id}")
        except Exception as e:
            logger.error(f"Error opening DM channel: {e}")
            if message_marked_processing and message_ts:
                _unmark_message_processing(message_ts)
            return
    
    if not channel_id:
        logger.error(f"No channel_id available for message from user {user_id}")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        return
    
    is_dm = (channel_id and channel_id.startswith('D')) if channel_id else False
    
    # ====================================================================
    # STEP 2: Check SQLite if user is registered
    # ====================================================================
    logger.info(f"Step 2: Checking if user {user_id} is registered in SQLite...")
    
    db = SessionLocal()
    slack_user = None
    slack_user_email = None
    
    try:
        slack_user = db.query(SlackUser).filter(
            SlackUser.slack_user_id == user_id,
            SlackUser.is_registered == True
        ).first()
        
        if slack_user:
            logger.info(f"[OK] User {user_id} is registered in database")
            slack_user_email = slack_user.email
        else:
            logger.warning(f"[FAIL] User {user_id} is NOT registered in database")
    except Exception as e:
        logger.error(f"Error checking user registration: {e}")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        db.close()
        return
    finally:
        db.close()
    
    # ====================================================================
    # STEP 3: If not exists, return message that user does not exist
    # ====================================================================
    if not slack_user:
        logger.info(f"Step 3: User does not exist - sending error message")
        
        error_message = "Sorry, you are not registered to use this Slack app. Please contact your administrator."
        client = WebClient(token=bot_token)
        
        try:
            message_kwargs = {
                "channel": channel_id,
                "text": error_message,
                "blocks": [{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"⚠️ *Access Denied*\n\n{error_message}"
                    }
                }]
            }
            if not is_dm and thread_ts and thread_ts != event.get("ts"):
                message_kwargs["thread_ts"] = thread_ts
            
            client.chat_postMessage(**message_kwargs)
            
            # Mark message as processed after sending error
            if message_ts:
                _mark_message_processed(message_ts)
            
            logger.info(f"[OK] Access denied message sent to unregistered user {user_id} in channel {channel_id}")
            return
            
        except Exception as e:
            logger.error(f"Error sending access denied message: {e}")
            if message_marked_processing and message_ts:
                _unmark_message_processing(message_ts)
            return
    
    # ====================================================================
    # STEP 4: If user exists, use POST /api/v1/ask endpoint to get answer
    # ====================================================================
    logger.info(f"Step 4: User exists - calling POST /api/v1/ask endpoint")
    
    # Try to fetch email from Slack API if not in database
    if not slack_user_email:
        try:
            client = WebClient(token=bot_token)
            user_info = client.users_info(user=user_id)
            if user_info.get("ok") and user_info.get("user"):
                slack_user_email = user_info["user"].get("profile", {}).get("email")
                # Update database if email found
                if slack_user_email:
                    db = SessionLocal()
                    try:
                        db_slack_user = db.query(SlackUser).filter(
                            SlackUser.slack_user_id == user_id
                        ).first()
                        if db_slack_user:
                            db_slack_user.email = slack_user_email
                            db.commit()
                            logger.info(f"Updated email for user {user_id}: {slack_user_email}")
                    except Exception as e:
                        logger.error(f"Error updating user email in database: {e}")
                    finally:
                        db.close()
        except Exception as e:
            logger.debug(f"Could not fetch user email from Slack API: {e}")
    
    # Prepare request data for /ask endpoint
    request_data = {
        "question": question_text,
        "request_user_key": f"slack:{user_id.lower()}",
    }
    if slack_user_email:
        request_data["slack_user_email"] = slack_user_email
        logger.info(f"Calling /api/v1/ask with question and slack_user_email: {slack_user_email}")
    else:
        logger.info(f"Calling /api/v1/ask with question only (no email available)")

    busy_check_db = SessionLocal()
    try:
        if is_user_request_in_progress(
            busy_check_db,
            request_user_key=request_data.get("request_user_key"),
            slack_user_email=slack_user_email,
            slack_user_id=user_id,
        ):
            logger.info("User %s already has an in-flight request; returning validation message", user_id)
            busy_payload = build_slack_response_payload(USER_REQUEST_BUSY_MESSAGE)
            try:
                client = WebClient(token=bot_token)
                message_kwargs = {
                    "channel": channel_id,
                    "text": busy_payload.get("text", USER_REQUEST_BUSY_MESSAGE),
                    "blocks": busy_payload.get("blocks", []),
                }
                if busy_payload.get("attachments"):
                    message_kwargs["attachments"] = busy_payload.get("attachments")
                if not is_dm and thread_ts and thread_ts != event.get("ts"):
                    message_kwargs["thread_ts"] = thread_ts
                client.chat_postMessage(**message_kwargs)
                if message_ts:
                    _mark_message_processed(message_ts)
            except Exception as e:
                logger.error("Error sending busy validation message: %s", e)
                if message_marked_processing and message_ts:
                    _unmark_message_processing(message_ts)
            return
    except Exception as e:
        logger.warning("Could not run user busy pre-check; continuing to /ask: %s", e)
    finally:
        busy_check_db.close()

    # Post "Analysing your question..." so the user sees immediate feedback (like a typing/loading state)
    loading_ts = None
    try:
        client = WebClient(token=bot_token)
        loading_msg = {
            "channel": channel_id,
            "text": "MOJO is connecting the dots...",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*MOJO is connecting the dots...*\nPutting everything together for you..."
                    }
                }
            ]
        }
        if not is_dm and thread_ts and thread_ts != event.get("ts"):
            loading_msg["thread_ts"] = thread_ts
        resp = client.chat_postMessage(**loading_msg)
        if resp.get("ok") and resp.get("ts"):
            loading_ts = resp["ts"]
            logger.info(f"Posted loading message (ts={loading_ts})")
    except Exception as e:
        logger.warning(f"Could not post loading message: {e}")

    # Call the ask_question function directly
    db = SessionLocal()
    try:
        request_obj = AskRequest(**request_data)
        result = await _ask_question_impl(request_obj, db)

        ask_response = {
            "answer": result.answer
        }

        logger.info("[OK] Successfully received answer from direct function call")
    except Exception as e:
        logger.error(f"Error calling _ask_question_impl: {e}")
        ask_response = {"answer": f"Sorry, I encountered an error processing your question: {str(e)}"}
    finally:
        db.close()

    # Extract answer from response
    answer_text = ask_response.get("answer", "I couldn't process your question. Please try again.")

    # ====================================================================
    # STEP 5: Respond back to Slack (update loading message if we have one, else post new)
    # ====================================================================
    logger.info(f"Step 5: Sending response back to Slack")
    slack_payload = build_slack_response_payload(answer_text)

    try:
        client = WebClient(token=bot_token)
        if loading_ts:
            # Replace the "Analysing..." message with the real answer (single message, better UX)
            update_kwargs = {
                "channel": channel_id,
                "ts": loading_ts,
                "text": slack_payload.get("text", answer_text),
                "blocks": slack_payload.get("blocks", []),
            }
            if slack_payload.get("attachments"):
                update_kwargs["attachments"] = slack_payload.get("attachments")
            client.chat_update(**update_kwargs)
            logger.info(f"[OK] Updated loading message with answer in {'DM' if is_dm else 'channel'} {channel_id}")
        else:
            message_kwargs = {
                "channel": channel_id,
                "text": slack_payload.get("text", answer_text),
                "blocks": slack_payload.get("blocks", []),
            }
            if slack_payload.get("attachments"):
                message_kwargs["attachments"] = slack_payload.get("attachments")
            if not is_dm and thread_ts and thread_ts != event.get("ts"):
                message_kwargs["thread_ts"] = thread_ts
            client.chat_postMessage(**message_kwargs)
            logger.info(f"[OK] Response sent successfully to {'DM' if is_dm else 'channel'} {channel_id}")

        if message_ts:
            _mark_message_processed(message_ts)

    except Exception as e:
        logger.error(f"Error sending formatted response to Slack: {e}")
        try:
            client = WebClient(token=bot_token)
            if loading_ts:
                client.chat_update(
                    channel=channel_id,
                    ts=loading_ts,
                    text=answer_text,
                )
            else:
                message_kwargs = {"channel": channel_id, "text": answer_text}
                if not is_dm and thread_ts and thread_ts != event.get("ts"):
                    message_kwargs["thread_ts"] = thread_ts
                client.chat_postMessage(**message_kwargs)
            if message_ts:
                _mark_message_processed(message_ts)
        except Exception as e2:
            logger.error(f"Error sending fallback message: {e2}")
            if message_marked_processing and message_ts:
                _unmark_message_processing(message_ts)


def process_socket_mode_request(client: SocketModeClient, req: SocketModeRequest):
    """
    Process incoming Socket Mode requests from Slack.
    Handles multiple event types: messages, app_mentions, and app_home_opened.
    """
    global _shutting_down
    if _shutting_down:
        return

    try:
        # Handle URL verification
        if req.type == "events_api":
            # Acknowledge the event
            response = SocketModeResponse(envelope_id=req.envelope_id)
            client.send_socket_mode_response(response)
            
            # Process the event
            event = req.payload.get("event", {})
            event_type = event.get("type")
            
            # Get configuration
            config = get_slack_config_from_db()
            if not config or not config.bot_token:
                logger.error("No active Slack configuration found")
                return
            
            # Get base URL (you may want to make this configurable)
            # Use 127.0.0.1 for local connection regardless of bind address
            base_url = f"http://127.0.0.1:{settings.port}"
            
            # Handle different event types
            if event_type == "message":
                # Process regular messages (DMs, channel messages)
                def run_async():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(
                            process_slack_message(event, config.bot_token, base_url)
                        )
                    finally:
                        loop.close()
                
                import threading
                if not _shutting_down:
                    thread = threading.Thread(target=run_async, daemon=True)
                    thread.start()
                
            elif event_type == "app_mention":
                # Handle app mentions (@botname)
                def run_async():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(
                            process_slack_message(event, config.bot_token, base_url)
                        )
                    finally:
                        loop.close()
                
                import threading
                if not _shutting_down:
                    thread = threading.Thread(target=run_async, daemon=True)
                    thread.start()
                
            elif event_type == "app_home_opened":
                # Handle app home opened (user opens the app)
                def run_async():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(
                            handle_app_home_opened(event, config.bot_token)
                        )
                    finally:
                        loop.close()
                
                import threading
                if not _shutting_down:
                    thread = threading.Thread(target=run_async, daemon=True)
                    thread.start()
            
    except Exception as e:
        logger.error(f"Error processing Socket Mode request: {e}")


def start_socket_mode_client(app_token: str, bot_token: str) -> bool:
    """
    Start the Socket Mode client.
    
    Args:
        app_token: App-Level Token (starts with xapp-)
        bot_token: Bot User OAuth Token (starts with xoxb-)
    """
    global _socket_mode_client, _socket_mode_task, _shutting_down

    client: Optional[SocketModeClient] = None
    try:
        # Stop existing client first (this sets _shutting_down = True)
        stop_socket_mode_client(log_if_inactive=False)

        # Clear shutdown flag for the new client lifecycle
        with _lifecycle_lock:
            _shutting_down = False
        _update_socket_mode_status("connecting", connected=False, last_error=None)

        # Create Socket Mode client
        web_client = WebClient(token=bot_token, logger=_slack_sdk_logger)
        client = SocketModeClient(
            app_token=app_token,
            logger=_slack_sdk_logger,
            web_client=web_client,
            on_error_listeners=[_handle_socket_mode_error],
            on_close_listeners=[_handle_socket_mode_close],
        )

        # Register event handler
        client.socket_mode_request_listeners.append(process_socket_mode_request)

        # Connect once up front so the caller only gets success when Slack is reachable.
        client.connect()

        _socket_mode_client = client
        connected_at = datetime.utcnow()
        _update_socket_mode_status(
            "connected",
            connected=True,
            last_error=None,
            connected_at=connected_at,
        )
        logger.info("[OK] Slack Socket Mode connected successfully")
        return True

    except Exception as e:
        described_error = _describe_socket_mode_error(e)
        _update_socket_mode_status("error", connected=False, last_error=described_error)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        with _lifecycle_lock:
            _socket_mode_client = None
            _socket_mode_task = None
        logger.error("Failed to connect Slack Socket Mode client: %s", described_error)
        return False


def stop_socket_mode_client(log_if_inactive: bool = True):
    """Stop the Socket Mode client."""
    global _socket_mode_client, _socket_mode_task, _shutting_down

    with _lifecycle_lock:
        _shutting_down = True
        client = _socket_mode_client

    if client is None:
        _update_socket_mode_status("stopped", connected=False, last_error=None)
        if log_if_inactive:
            logger.info("Socket Mode client already stopped")
        return

        try:
            if hasattr(client, "close"):
                try:
                    client.close()
                except Exception:
                    pass
            client.disconnect()
        except Exception as e:
            logger.error(f"Error stopping Socket Mode client: {e}")

    with _lifecycle_lock:
        _socket_mode_client = None
        _socket_mode_task = None
    _update_socket_mode_status(
        "stopped",
        connected=False,
        last_error=None,
        disconnected_at=datetime.utcnow(),
    )
    logger.info("Socket Mode client stopped")


def _atexit_stop_socket_mode_client():
    try:
        stop_socket_mode_client()
    except Exception:
        pass


atexit.register(_atexit_stop_socket_mode_client)


def restart_socket_mode_client():
    """Restart Socket Mode client with current configuration."""
    config = get_slack_config_from_db()
    if config and config.socket_mode_enabled and config.app_token and config.bot_token:
        logger.info("Restarting Socket Mode client...")
        return start_socket_mode_client(config.app_token, config.bot_token)
    else:
        logger.info("Socket Mode not configured or disabled")
        stop_socket_mode_client()
        return False

