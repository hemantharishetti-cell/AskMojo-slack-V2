"""
Performance timing decorator and context manager.

Usage:
    @timed("stage_1_intent")
    async def classify(question):
        ...

    async with Timer("chromadb_query") as t:
        results = await query(...)
    print(t.elapsed_ms)
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from app.utils.logging import get_logger

logger = get_logger("askmojo.timing")


class Timer:
    """Simple context-manager timer (sync + async compatible)."""

    def __init__(self, label: str = ""):
        self.label = label
        self._start: float = 0.0
        self.elapsed_s: float = 0.0

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_s * 1000

    # Sync
    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self.elapsed_s = time.perf_counter() - self._start
        if self.label:
            logger.info(
                "%s completed in %.1fms", self.label, self.elapsed_ms
            )

    # Async
    async def __aenter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.elapsed_s = time.perf_counter() - self._start
        if self.label:
            logger.info(
                "%s completed in %.1fms", self.label, self.elapsed_ms
            )


def timed(label: str | None = None) -> Callable:
    """
    Decorator that logs the wall-clock time of a function call.
    Works for both sync and async functions.
    """

    def decorator(fn: Callable) -> Callable:
        _label = label or fn.__qualname__

        if _is_coroutine_function(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with Timer(_label):
                    return await fn(*args, **kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with Timer(_label):
                return fn(*args, **kwargs)

        return sync_wrapper

    return decorator


def _is_coroutine_function(fn: Callable) -> bool:
    """Check if a callable is an async function."""
    import asyncio

    return asyncio.iscoroutinefunction(fn)
