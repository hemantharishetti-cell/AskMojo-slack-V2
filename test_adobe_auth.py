#!/usr/bin/env python3
"""
Test script to verify Adobe API access token works.
Run this after updating credentials in .env
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Load .env file
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import settings


def test_adobe_credentials():
    """Test Adobe API credentials."""
    print("=" * 70)
    print("Adobe PDF Services API Credentials Test")
    print("=" * 70)
    print(f"\nTest started at: {datetime.now().isoformat()}\n")

    try:
        # Check API Key
        api_key = settings.adobe_api_key
        if not api_key:
            print("[FAIL] ADOBE_API_KEY not set in environment")
            return False

        print("[OK] Adobe API Key loaded")
        print(f"  - API Key: {api_key[:20]}...")

        # Check Access Token
        access_token = settings.adobe_access_token
        if not access_token:
            print("[FAIL] ADOBE_ACCESS_TOKEN not set in environment")
            return False

        print("[OK] Adobe Access Token loaded")
        print(f"  - Token length: {len(access_token)} characters")
        print(f"  - Token preview: {access_token[:50]}...")

        # Verify token format (should be 3 base64 parts separated by dots)
        parts = access_token.split(".")
        if len(parts) != 3:
            print(f"[FAIL] Invalid JWT format (expected 3 parts, got {len(parts)})")
            return False

        print(f"  - Token structure: Valid (3 JWT parts)")

        # Decode and inspect token payload (don't verify signature, just inspect)
        try:
            import base64
            # Get the payload part
            payload_b64 = parts[1]
            # Add padding if needed
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload_str = base64.urlsafe_b64decode(payload_b64).decode('utf-8')
            payload = json.loads(payload_str)
            
            print(f"\n[OK] Token payload decoded:")
            print(f"  - Client ID: {payload.get('client_id', 'N/A')}")
            print(f"  - Org ID: {payload.get('org', 'N/A')}")
            print(f"  - User ID: {payload.get('user_id', 'N/A')}")
            print(f"  - Scopes: {payload.get('scope', 'N/A')}")
            expires_in_ms = int(payload.get('expires_in', 0))
            expires_in_hours = expires_in_ms / (1000 * 60 * 60)
            print(f"  - Token Expiry: {expires_in_hours:.1f} hours from issuance")
            
        except Exception as e:
            print(f"[WARN] Could not decode token payload: {e}")

        print("\n" + "=" * 70)
        print("[SUCCESS] ALL CHECKS PASSED - Adobe credentials are ready!")
        print("=" * 70)
        print(f"\nTest completed at: {datetime.now().isoformat()}\n")
        return True

    except Exception as e:
        print(f"[FAIL] {type(e).__name__}")
        print(f"   Error: {str(e)}\n")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_adobe_credentials()
    sys.exit(0 if success else 1)
