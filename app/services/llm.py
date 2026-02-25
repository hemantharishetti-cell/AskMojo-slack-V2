"""
OpenAI client singleton, token counting, and TOON encoding.

Replaces:
  - Per-request `OpenAIClient(api_key=...)` (was line 1055 of routes.py)
  - Duplicate _DummyEnc classes (lines 981 and 1075)
  - Scattered convert_json_to_toon_and_show_savings() calls
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from app.core.config import settings

logger = logging.getLogger("askmojo.services.llm")

# ── Optional imports with graceful fallbacks ─────────────────────────
try:
    from openai import OpenAI as _OpenAIClient
except ImportError:
    _OpenAIClient = None  # type: ignore[assignment, misc]

try:
    from toon import encode as _toon_encode
except ImportError:
    _toon_encode = None

try:
    import tiktoken as _tiktoken_lib
except ImportError:
    _tiktoken_lib = None


# ── Singleton OpenAI client ─────────────────────────────────────────
_client_lock = threading.Lock()
_client_instance: Any | None = None


def get_openai_client() -> Any:
    """
    Return a module-level OpenAI client singleton.

    Raises RuntimeError when the library is not installed or the API
    key is missing.
    """
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    with _client_lock:
        if _client_instance is not None:
            return _client_instance

        if _OpenAIClient is None:
            raise RuntimeError(
                "OpenAI library is not installed. "
                "Please install 'openai' to use this service."
            )
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key not configured (OPENAI_API_KEY).")

        _client_instance = _OpenAIClient(api_key=settings.openai_api_key)
        logger.info("OpenAI client singleton initialized.")
        return _client_instance


# ── Token counting ──────────────────────────────────────────────────
_encoder_lock = threading.Lock()
_encoder: Any | None = None


def _get_encoder() -> Any:
    """Return a tiktoken encoder or a simple fallback."""
    global _encoder
    if _encoder is not None:
        return _encoder

    with _encoder_lock:
        if _encoder is not None:
            return _encoder
        if _tiktoken_lib is not None:
            _encoder = _tiktoken_lib.get_encoding("cl100k_base")
        else:

            class _DummyEnc:
                """Rough approximation when tiktoken is unavailable."""

                def encode(self, s: str) -> list:
                    try:
                        return s.split()
                    except Exception:
                        return [s]

            _encoder = _DummyEnc()
        return _encoder


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken or fallback (1 token ≈ 4 chars)."""
    enc = _get_encoder()
    if _tiktoken_lib is not None:
        return len(enc.encode(text))
    # Fallback: ≈ 1 token per 4 characters
    return max(1, len(text) // 4)


# ── TOON encoding ───────────────────────────────────────────────────
def convert_to_toon(
    data: Any,
    call_name: str,
    data_name: str = "Data",
) -> tuple[str, int, int]:
    """
    Convert JSON data to TOON format and report token savings.

    Returns:
        (toon_string, original_json_tokens, toon_tokens)
    """
    json_str = json.dumps(data, indent=2)
    original_tokens = count_tokens(json_str)

    global _toon_encode

    try:
        # If the optional dependency was installed after module import,
        # try to import it lazily.
        if _toon_encode is None:
            try:
                from toon import encode as _toon_encode  # type: ignore
            except ImportError:
                _toon_encode = None

        if _toon_encode is None:
            raise RuntimeError("toon library not installed")

        toon_str = _toon_encode(data)
        if isinstance(toon_str, bytes):
            toon_str = toon_str.decode("utf-8")
        elif not isinstance(toon_str, str):
            toon_str = str(toon_str)
    except Exception as exc:
        logger.warning(
            "Could not encode %s to TOON for %s: %s. Using JSON.",
            data_name,
            call_name,
            exc,
        )
        toon_str = json_str

    if not isinstance(toon_str, str):
        toon_str = str(toon_str)

    toon_tokens = count_tokens(toon_str)
    return toon_str, original_tokens, toon_tokens
