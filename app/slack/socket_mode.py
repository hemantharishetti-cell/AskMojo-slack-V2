"""
Slack Socket Mode handler for real-time event processing.
This module handles WebSocket connections to Slack when Socket Mode is enabled.
"""
import asyncio
import logging
from typing import Optional
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from sqlalchemy.orm import Session
from app.sqlite.database import SessionLocal
from app.sqlite.models import SlackIntegration, SlackUser
from app.vector_logic.schemas import AskRequest
import httpx
from datetime import datetime, timedelta
import threading

logger = logging.getLogger(__name__)

# In-memory cache to prevent processing the same message multiple times
# Format: {message_ts: processed_at}
# States: "processing" = currently being processed, datetime = already processed
_processed_messages = {}
_processed_messages_lock = threading.Lock()
CLEANUP_INTERVAL = timedelta(minutes=5)  # Clean up old entries after 5 minutes


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


def get_slack_config_from_db() -> Optional[SlackIntegration]:
    """Get active Slack configuration from database."""
    db = SessionLocal()
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
    base_url: str = "http://localhost:8001"
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
            logger.info(f"✓ User {user_id} is registered in database")
            slack_user_email = slack_user.email
        else:
            logger.warning(f"✗ User {user_id} is NOT registered in database")
    except Exception as e:
        logger.error(f"Error checking user registration: {e}")
        if message_marked_processing and message_ts:
            _unmark_message_processing(message_ts)
        db.close()
        return
    
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
            
            logger.info(f"✓ Access denied message sent to unregistered user {user_id} in channel {channel_id}")
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
        "question": question_text
    }
    if slack_user_email:
        request_data["slack_user_email"] = slack_user_email
        logger.info(f"Calling /api/v1/ask with question and slack_user_email: {slack_user_email}")
    else:
        logger.info(f"Calling /api/v1/ask with question only (no email available)")
    
    # Call the /ask endpoint
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/api/v1/ask",
                json=request_data,
                timeout=100.0
            )
            response.raise_for_status()
            ask_response = response.json()
            logger.info(f"✓ Successfully received answer from /api/v1/ask endpoint")
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error calling /ask endpoint: {e.response.status_code} - {e.response.text}")
        ask_response = {"answer": f"Sorry, I encountered an error processing your question (HTTP {e.response.status_code}). Please try again."}
    except Exception as e:
        logger.error(f"Error calling /ask endpoint: {e}")
        ask_response = {"answer": f"Sorry, I encountered an error processing your question: {str(e)}"}
    
    # Extract answer from response
    answer_text = ask_response.get("answer", "I couldn't process your question. Please try again.")
    
    # ====================================================================
    # STEP 5: Respond back to Slack
    # ====================================================================
    logger.info(f"Step 5: Sending response back to Slack")
    
    # Format the response for Slack with rich text support
    formatted_response = format_slack_message(answer_text)
    
    try:
        client = WebClient(token=bot_token)
        
        message_kwargs = {
            "channel": channel_id,
            "text": answer_text,  # Fallback text for notifications
            "blocks": formatted_response.get("blocks", []),  # Rich formatted blocks
            "mrkdwn": True  # Enable markdown formatting
        }
        
        # Only add thread_ts for channel messages (not DMs)
        if not is_dm and thread_ts and thread_ts != event.get("ts"):
            message_kwargs["thread_ts"] = thread_ts
        
        client.chat_postMessage(**message_kwargs)
        
        # Mark message as processed after successful response
        if message_ts:
            _mark_message_processed(message_ts)
        
        logger.info(f"✓ Response sent successfully to {'DM' if is_dm else 'channel'} {channel_id}")
        
    except Exception as e:
        logger.error(f"Error sending formatted response to Slack: {e}")
        # Fallback to plain text if Block Kit fails
        try:
            client = WebClient(token=bot_token)
            message_kwargs = {
                "channel": channel_id,
                "text": answer_text
            }
            if not is_dm and thread_ts and thread_ts != event.get("ts"):
                message_kwargs["thread_ts"] = thread_ts
            
            client.chat_postMessage(**message_kwargs)
            
            # Mark message as processed after fallback response
            if message_ts:
                _mark_message_processed(message_ts)
            
            logger.info(f"✓ Fallback response sent successfully to {'DM' if is_dm else 'channel'} {channel_id}")
            
        except Exception as e2:
            logger.error(f"Error sending fallback message: {e2}")
            # If both attempts failed, unmark processing so it can be retried
            if message_marked_processing and message_ts:
                _unmark_message_processing(message_ts)


def process_socket_mode_request(client: SocketModeClient, req: SocketModeRequest):
    """
    Process incoming Socket Mode requests from Slack.
    Handles multiple event types: messages, app_mentions, and app_home_opened.
    """
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
            base_url = "http://localhost:8001"  # Default, should be from config
            
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
                thread = threading.Thread(target=run_async, daemon=True)
                thread.start()
            
    except Exception as e:
        logger.error(f"Error processing Socket Mode request: {e}")


def start_socket_mode_client(app_token: str, bot_token: str):
    """
    Start the Socket Mode client.
    
    Args:
        app_token: App-Level Token (starts with xapp-)
        bot_token: Bot User OAuth Token (starts with xoxb-)
    """
    global _socket_mode_client, _socket_mode_task
    
    try:
        # Stop existing client if any
        stop_socket_mode_client()
        
        # Create Socket Mode client
        client = SocketModeClient(
            app_token=app_token,
            web_client=WebClient(token=bot_token)
        )
        
        # Register event handler
        client.socket_mode_request_listeners.append(process_socket_mode_request)
        
        # Start the client (this is a blocking call, so run in background)
        _socket_mode_client = client
        
        def run_socket_mode():
            try:
                client.connect()
            except Exception as e:
                logger.error(f"Socket Mode client error: {e}")
        
        # Run in a separate thread
        import threading
        socket_thread = threading.Thread(target=run_socket_mode, daemon=True)
        socket_thread.start()
        
        logger.info("✓ Slack Socket Mode client started successfully")
        return True
        
    except Exception as e:
        logger.error(f"Failed to start Socket Mode client: {e}")
        return False


def stop_socket_mode_client():
    """Stop the Socket Mode client."""
    global _socket_mode_client, _socket_mode_task
    
    if _socket_mode_client:
        try:
            _socket_mode_client.disconnect()
            logger.info("Socket Mode client stopped")
        except Exception as e:
            logger.error(f"Error stopping Socket Mode client: {e}")
        finally:
            _socket_mode_client = None
            _socket_mode_task = None


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

