from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import Optional
import httpx
import json

from app.sqlite.database import get_db
from app.sqlite.models import SlackIntegration, SlackUser
from app.core.security import get_current_admin_user
from app.slack.schemas import (
    SlackConfigCreate,
    SlackConfigUpdate,
    SlackConfigResponse,
    SlackTestRequest,
    SlackTestResponse,
    SlackUserFetchRequest,
    SlackUserInfo,
    SlackUserCreate,
    SlackUserUpdate,
    SlackUserResponse,
)
from app.vector_logic.schemas import AskRequest, AskResponse
from app.slack.socket_mode import start_socket_mode_client, stop_socket_mode_client, restart_socket_mode_client

router = APIRouter(prefix="/slack", tags=["slack"])


@router.get("/config", response_model=Optional[SlackConfigResponse])
def get_slack_config(
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get current Slack integration configuration."""
    config = db.query(SlackIntegration).first()
    if not config:
        return None
    return config


@router.post("/config", response_model=SlackConfigResponse, status_code=status.HTTP_201_CREATED)
def create_slack_config(
    config_data: SlackConfigCreate,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Create or update Slack integration configuration."""
    existing = db.query(SlackIntegration).first()
    
    if existing:
        # Update existing config
        update_data = config_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(existing, field, value)
        db.commit()
        db.refresh(existing)
        
        # Restart Socket Mode if enabled
        if existing.socket_mode_enabled and existing.is_active:
            if existing.app_token and existing.bot_token:
                restart_socket_mode_client()
            else:
                stop_socket_mode_client()
        else:
            stop_socket_mode_client()
        
        return existing
    else:
        # Create new config
        new_config = SlackIntegration(**config_data.model_dump())
        db.add(new_config)
        db.commit()
        db.refresh(new_config)
        
        # Start Socket Mode if enabled
        if new_config.socket_mode_enabled and new_config.is_active:
            if new_config.app_token and new_config.bot_token:
                start_socket_mode_client(new_config.app_token, new_config.bot_token)
        
        return new_config


@router.put("/config", response_model=SlackConfigResponse)
def update_slack_config(
    config_data: SlackConfigUpdate,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Update Slack integration configuration."""
    config = db.query(SlackIntegration).first()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Slack configuration not found. Please create it first."
        )
    
    update_data = config_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(config, field, value)
    
    db.commit()
    db.refresh(config)
    
    # Restart Socket Mode if enabled
    if config.socket_mode_enabled and config.is_active:
        if config.app_token and config.bot_token:
            restart_socket_mode_client()
        else:
            stop_socket_mode_client()
    else:
        stop_socket_mode_client()
    
    return config


@router.delete("/config", status_code=status.HTTP_204_NO_CONTENT)
def delete_slack_config(
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Delete Slack integration configuration."""
    config = db.query(SlackIntegration).first()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Slack configuration not found"
        )
    
    db.delete(config)
    db.commit()
    return None


@router.post("/test", response_model=SlackTestResponse)
async def test_slack_connection(
    test_data: SlackTestRequest,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Test Slack integration by sending a test message."""
    config = db.query(SlackIntegration).filter(SlackIntegration.is_active == True).first()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active Slack configuration found"
        )
    
    try:
        test_message = test_data.message if hasattr(test_data, 'message') else "Test message from ASKMOJO admin panel"
        
        # Try webhook URL first (simpler)
        if config.webhook_url:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    config.webhook_url,
                    json={"text": test_message},
                    timeout=10.0
                )
                response.raise_for_status()
                return SlackTestResponse(
                    success=True,
                    message="Test message sent successfully via webhook"
                )
        
        # Try bot token (Slack API)
        elif config.bot_token and config.channel_id:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {config.bot_token}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "channel": config.channel_id,
                        "text": test_message
                    },
                    timeout=10.0
                )
                result = response.json()
                if result.get("ok"):
                    return SlackTestResponse(
                        success=True,
                        message="Test message sent successfully via bot token"
                    )
                else:
                    return SlackTestResponse(
                        success=False,
                        message="Failed to send test message",
                        error=result.get("error", "Unknown error")
                    )
        else:
            return SlackTestResponse(
                success=False,
                message="No valid Slack configuration found",
                error="Either webhook_url or (bot_token and channel_id) must be configured"
            )
    except httpx.HTTPError as e:
        return SlackTestResponse(
            success=False,
            message="Failed to send test message",
            error=str(e)
        )
    except Exception as e:
        return SlackTestResponse(
            success=False,
            message="Unexpected error",
            error=str(e)
        )


@router.post("/webhook")
async def slack_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Handle incoming webhooks from Slack.
    This endpoint receives messages from Slack and forwards them to the questioning system.
    """
    config = db.query(SlackIntegration).filter(SlackIntegration.is_active == True).first()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Slack integration not configured or inactive"
        )
    
    try:
        # Parse Slack event
        body = await request.json()
        
        # Handle Slack URL verification challenge
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge")}
        
        # Handle event callbacks
        if body.get("type") == "event_callback":
            event = body.get("event", {})
            
            # Only process message events
            if event.get("type") != "message":
                return {"status": "ignored"}
            
            # Ignore bot messages
            if event.get("bot_id"):
                return {"status": "ignored"}
            
            # Get the question text
            question_text = event.get("text", "").strip()
            if not question_text:
                return {"status": "no_text"}
            
            # Get channel and user info
            channel_id = event.get("channel")
            user_id = event.get("user")
            thread_ts = event.get("thread_ts") or event.get("ts")  # Reply in thread if exists
            
            # Check if user is registered and get their email
            slack_user_email = None
            if user_id:
                slack_user = db.query(SlackUser).filter(
                    SlackUser.slack_user_id == user_id,
                    SlackUser.is_registered == True
                ).first()
                if not slack_user:
                    # User not registered, send message and return
                    error_message = "Sorry, you are not registered to use this Slack app. Please contact your administrator."
                    if config.bot_token and channel_id:
                        async with httpx.AsyncClient() as client:
                            await client.post(
                                "https://slack.com/api/chat.postMessage",
                                headers={
                                    "Authorization": f"Bearer {config.bot_token}",
                                    "Content-Type": "application/json"
                                },
                                json={
                                    "channel": channel_id,
                                    "text": error_message,
                                    "thread_ts": thread_ts if thread_ts != event.get("ts") else None
                                },
                                timeout=10.0
                            )
                    return {"status": "user_not_registered"}
                
                # Get the Slack user's email
                slack_user_email = slack_user.email
                
                # If email is not in our database, try to fetch it from Slack API
                if not slack_user_email and config.bot_token:
                    try:
                        async with httpx.AsyncClient() as client:
                            user_info_response = await client.get(
                                f"https://slack.com/api/users.info",
                                headers={
                                    "Authorization": f"Bearer {config.bot_token}",
                                    "Content-Type": "application/json"
                                },
                                params={"user": user_id},
                                timeout=10.0
                            )
                            if user_info_response.status_code == 200:
                                user_info = user_info_response.json()
                                if user_info.get("ok") and user_info.get("user"):
                                    slack_user_email = user_info["user"].get("profile", {}).get("email")
                                    # Update the SlackUser record with the email if we found it
                                    if slack_user_email:
                                        slack_user.email = slack_user_email
                                        db.commit()
                    except Exception as e:
                        print(f"Error fetching user email from Slack API: {e}")
            
            # Forward to questioning endpoint via HTTP
            # Get base URL from request
            scheme = request.url.scheme
            host = request.headers.get("host", "localhost:8001")
            base_url = f"{scheme}://{host}"
            
            async with httpx.AsyncClient() as client:
                try:
                    # Include slack_user_email in the request
                    request_data = {"question": question_text}
                    if slack_user_email:
                        request_data["slack_user_email"] = slack_user_email
                    
                    response = await client.post(
                        f"{base_url}/api/v1/ask",
                        json=request_data,
                        timeout=60.0
                    )
                    response.raise_for_status()
                    ask_response = response.json()
                except httpx.HTTPError as e:
                    response_text = f"Sorry, I encountered an error processing your question: {str(e)}"
                    ask_response = {"answer": response_text}
            
            # Send response back to Slack
            response_text = ask_response.get("answer", "I couldn't process your question. Please try again.")
            
            # Use webhook or bot token to respond
            if config.webhook_url:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        config.webhook_url,
                        json={
                            "text": response_text,
                            "thread_ts": thread_ts if thread_ts != event.get("ts") else None
                        },
                        timeout=10.0
                    )
            elif config.bot_token and channel_id:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        "https://slack.com/api/chat.postMessage",
                        headers={
                            "Authorization": f"Bearer {config.bot_token}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "channel": channel_id,
                            "text": response_text,
                            "thread_ts": thread_ts if thread_ts != event.get("ts") else None
                        },
                        timeout=10.0
                    )
            
            return {"status": "processed"}
        
        return {"status": "unknown_event_type"}
    
    except Exception as e:
        print(f"Error processing Slack webhook: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing webhook: {str(e)}"
        )


# Slack User Management Routes
@router.post("/users/fetch", response_model=SlackUserInfo)
async def fetch_slack_user(
    request_data: SlackUserFetchRequest,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Fetch Slack user information by email or Slack User ID from Slack API."""
    # Validate that either email or slack_user_id is provided
    if not request_data.email and not request_data.slack_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'email' or 'slack_user_id' must be provided"
        )
    
    config = db.query(SlackIntegration).filter(SlackIntegration.is_active == True).first()
    if not config or not config.bot_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack integration not configured or bot token not available"
        )
    
    try:
        async with httpx.AsyncClient() as client:
            # Use different API endpoints based on what's provided
            if request_data.slack_user_id:
                # Use users.info to fetch by user ID
                response = await client.get(
                    "https://slack.com/api/users.info",
                    headers={
                        "Authorization": f"Bearer {config.bot_token}",
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                    params={"user": request_data.slack_user_id},
                    timeout=10.0
                )
            else:
                # Use users.lookupByEmail to find user by email
                response = await client.get(
                    "https://slack.com/api/users.lookupByEmail",
                    headers={
                        "Authorization": f"Bearer {config.bot_token}",
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                    params={"email": request_data.email},
                    timeout=10.0
                )
            
            result = response.json()
            
            if not result.get("ok"):
                error = result.get("error", "Unknown error")
                if error == "users_not_found" or error == "user_not_found":
                    identifier = request_data.slack_user_id or request_data.email
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"User {identifier} not found in Slack workspace"
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Slack API error: {error}"
                )
            
            # Both APIs return user data in the same format
            user_data = result.get("user", {})
            profile = user_data.get("profile", {})
            
            return SlackUserInfo(
                id=user_data.get("id"),
                email=profile.get("email"),
                name=user_data.get("name", ""),
                real_name=profile.get("real_name"),
                display_name=profile.get("display_name"),
                image_24=profile.get("image_24"),
                image_32=profile.get("image_32"),
                image_48=profile.get("image_48"),
                image_72=profile.get("image_72"),
                image_192=profile.get("image_192"),
                is_admin=user_data.get("is_admin", False),
                is_owner=user_data.get("is_owner", False),
                is_bot=user_data.get("is_bot", False),
                is_active=user_data.get("deleted", False) == False,
                tz=user_data.get("tz"),
                tz_label=user_data.get("tz_label"),
                tz_offset=user_data.get("tz_offset")
            )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch user from Slack: {str(e)}"
        )


@router.post("/users", response_model=SlackUserResponse, status_code=status.HTTP_201_CREATED)
def create_slack_user(
    user_data: SlackUserCreate,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Register a Slack user to allow them to use the app."""
    # Handle case where email might be None (for users fetched by ID without email)
    # Use a placeholder email if none is provided
    email = user_data.email
    if not email or email.strip() == "":
        # Generate a placeholder email using the Slack user ID
        email = f"{user_data.slack_user_id}@slack.local"
    
    # Check if user already exists (by slack_user_id or email)
    existing = db.query(SlackUser).filter(
        SlackUser.slack_user_id == user_data.slack_user_id
    ).first()
    
    # Also check by email if provided
    if not existing and email:
        existing = db.query(SlackUser).filter(
            SlackUser.email == email
        ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack user already registered"
        )
    
    # Create user data with email (either provided or placeholder)
    user_dict = user_data.model_dump()
    user_dict['email'] = email
    
    new_user = SlackUser(**user_dict)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.get("/users", response_model=list[SlackUserResponse])
def list_slack_users(
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """List all registered Slack users."""
    users = db.query(SlackUser).offset(skip).limit(limit).all()
    return users


@router.get("/users/{user_id}", response_model=SlackUserResponse)
def get_slack_user(
    user_id: int,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get a specific Slack user by ID."""
    user = db.query(SlackUser).filter(SlackUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Slack user not found"
        )
    return user


@router.put("/users/{user_id}", response_model=SlackUserResponse)
def update_slack_user(
    user_id: int,
    user_data: SlackUserUpdate,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Update a Slack user (mainly to enable/disable registration)."""
    user = db.query(SlackUser).filter(SlackUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Slack user not found"
        )
    
    update_data = user_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)
    
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_slack_user(
    user_id: int,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Delete a Slack user registration."""
    user = db.query(SlackUser).filter(SlackUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Slack user not found"
        )
    
    db.delete(user)
    db.commit()
    return None

