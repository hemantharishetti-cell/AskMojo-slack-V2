"""
Structured logging setup.

Replaces the 100+ print() statements in the codebase with
a proper logging configuration.

Usage:
    from app.utils.logging import get_logger
    logger = get_logger("askmojo.pipeline.intent")
    logger.info("Classified intent", extra={"intent": "factual_content"})
"""

from __future__ import annotations

import logging
import os
import sys

# Set PYTHONIOENCODING for any child processes.
# Do NOT reconfigure sys.stderr here — tqdm (used by sentence_transformers)
# calls sys.stderr.flush() which raises OSError [Errno 22] on a
# reconfigured stream in Windows.
if os.name == "nt":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure structured logging for the entire application."""
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger("askmojo")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger under the ``askmojo`` namespace.

    Automatically calls ``setup_logging()`` on first use to ensure
    the root handler is attached.
    """
    setup_logging()
    return logging.getLogger(name)
