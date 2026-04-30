"""
Deep Agent — Single File (Testing Mode)

Architecture:
  Tool 1  →  get_all_collections()   : Fetch all collections from SQLite
  Agent   →  decide_collection()     : LLM picks the best collection for the query

Entry points:
  run_collection_tool()              : Test Tool 1 in isolation
  run_agent(query)                   : Run autonomous agentic pipeline (Tool-Calling)

Usage (from project root):
  python test_deep_agent_tool1.py
  python test_deep_agent.py
"""

import json
import copy
import hashlib
import logging
import os
import sys
import time
import contextlib
import contextvars
import chromadb
from openai import OpenAI
from sqlalchemy.orm import Session
import re
import concurrent.futures
from typing import Any
# Optional Jina Toon formatting helpers; provide local fallbacks if unavailable
try:
    from toon_format import estimate_savings, compare_formats, count_tokens  # type: ignore
except Exception:
    def _approx_token_count(text: str) -> int:
        try:
            import tiktoken as _tiktoken
            enc = _tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text or ""))
        except Exception:
            # Rough heuristic: ~4 chars per token
            s = text or ""
            return max(1, (len(s) + 3) // 4)

    def count_tokens(text: str) -> int:  # fallback
        return _approx_token_count(text)

    def estimate_savings(toon_str: str, json_str: str) -> dict:  # fallback
        jt = _approx_token_count(json_str)
        tt = _approx_token_count(toon_str)
        saved = max(jt - tt, 0)
        pct = round((saved / jt) * 100, 2) if jt else 0.0
        return {
            "json_tokens": jt,
            "toon_tokens": tt,
            "saved_tokens": saved,
            "saved_percent": pct,
        }

    def compare_formats(obj) -> str:  # fallback pretty summary
        json_str = None
        toon_str = None
        if isinstance(obj, dict):
            json_str = obj.get("json") or obj.get("json_str") or obj.get("original_json") or ""
            toon_str = obj.get("toon") or obj.get("toon_str") or obj.get("compact") or ""
        elif isinstance(obj, (list, tuple)) and len(obj) >= 2:
            json_str, toon_str = obj[0], obj[1]
        else:
            json_str = str(obj) if obj is not None else ""
            toon_str = ""
        stats = estimate_savings(toon_str or "", json_str or "")
        return (
            f"Original JSON tokens: {stats['json_tokens']}\n"
            f"Toon tokens:         {stats['toon_tokens']}\n"
            f"Saved tokens:        {stats['saved_tokens']} ({stats['saved_percent']}%)"
        )

# Optional third-party dependencies (graceful fallbacks if missing)
try:
    from toon import encode as toon_encode
except ImportError:
    toon_encode = None
try:
    import tiktoken as tiktoken_lib
except ImportError:
    tiktoken_lib = None
try:
    import orjson as orjson_lib
except ImportError:
    orjson_lib = None
from app.vector_logic.vector_store import (
    _get_chroma_client,
    _embed_query,
    EmbeddingDimensionMismatchError,
    query_collection,
    query_collection_with_filter,
    query_master_collection,
    query_bm25,
    reciprocal_rank_fusion,
    rerank_chunks_scored,
)
from app.vector_logic.intent_router import (
    QuestionIntent,
    classify_intent,
    classify_intent_light,
    extract_metadata_hints,
    handle_classification,
    handle_conversational,
    handle_count,
    handle_domain_query,
    handle_existence,
    handle_listing,
)
from app.sqlite.models import Document

# ---------------------------------------------------------------------------
# FINAL ANSWER PROMPT
# ---------------------------------------------------------------------------

FINAL_ANSWER_SYSTEM_PROMPT = """
You are ASK MOJO, a grounded enterprise knowledge assistant for internal business documents.

Your task is to answer the user's question using only the provided document chunks.

Question understanding rules:
1. First identify what the user is actually asking for before writing the answer.
2. Determine the exact requested focus, such as timeline, comparison, responsibilities, process, pricing, metrics, or summary.
3. Answer the user's actual question, not a nearby or loosely related topic.
4. If the question asks for a structured artifact such as a table, comparison, matrix, phases, or list, shape the answer accordingly when supported by the chunks.

Grounding rules:
1. Use only information that is explicitly supported by the provided chunks.
2. Do not use outside knowledge, assumptions, speculation, or filler.
3. If the chunks fully answer the question, give a complete direct answer.
4. If the chunks answer only part of the question, answer only the supported part and clearly state what is missing.
5. If the chunks do not answer the question, reply exactly: "I could not find relevant information in the available documents."
6. Do not shift to adjacent topics unless the user explicitly asked for them.
7. Do not include facts just because they are related. Include only facts that answer the asked question.

Pricing and confidentiality rules:
1. Never disclose numeric costs, budgets, rates, commercial amounts, or exact project pricing.
2. If the user asks for cost, pricing, budget, fee, estimate, quote, rate card, or numeric project amount, reply exactly:
"I cannot disclose specific project budgets or numeric costs. For pricing details, please refer to the project handling team."
3. For those pricing questions, do not add timeline details, payment terms, business process details, or any other project information unless the user explicitly asked for them.
4. General payment terms, billing process, invoice timing, and project timelines may be answered only when the user explicitly asks for them and the chunks support them.

Completeness rules:
1. Before answering, determine whether the provided chunks contain enough information to answer the actual question.
2. If the answer is spread across multiple chunks, synthesize them into one complete answer.
3. Do not give a partial answer if the required information is already present across the provided chunks.
4. If multiple entities are mentioned in the question, ensure each requested entity is addressed when supported by the chunks.
5. If one entity is supported and another is not, answer the supported part and explicitly identify the missing part.
6. If the user asks about duration, timeline, or time required for a specific project and the chunks contain multiple supported durations for that same project, include all supported durations with brief context instead of collapsing them into one value.

Answer mode behavior:
- DIRECT: Give the direct supported answer first.
- COMPARE: Compare the requested entities side by side using only supported facts.
- TIMELINE: List durations, phases, or milestones only if explicitly supported by the chunks.
- AGGREGATE: Combine relevant supported facts from multiple chunks into one coherent answer.
- PARTIAL_OK: Answer only the supported part and clearly state what is missing.

Formatting rules:
1. Start directly with the answer. Do not add meta commentary.
2. Use clear sections or bullets only when they improve readability.
3. Keep the answer concise, factual, and easy to verify from the chunks.
4. Do not dump raw chunk text.
5. If the requested answer is naturally tabular, present it as a clean Markdown table when the chunks support that structure, except for duration, timeline, phase, or time-required answers.
6. For duration, timeline, phase, or time-required answers, prefer bullets or short prose. Never use a Markdown table unless the user explicitly asked for a table.
7. For RACI matrices, role assignments, responsibility mappings, or similar structured evidence, prefer a Markdown table with clear columns instead of prose.
8. Preserve table structure when the source evidence is clearly tabular, except for duration, timeline, phase, or time-required answers when the user did not explicitly ask for a table.
9. If the evidence is insufficient, be explicit about what is missing.
10. Do not mention system rules, hidden instructions, retrieval mechanics, or source labels in the final answer.
11. Do not add a separate "Source" section or explicitly mention source names unless the user specifically asks for sources.

Source discipline:
1. Treat the provided chunks as the only source of truth.
2. Prefer precise wording that matches the supported evidence.
3. When evidence is weak, incomplete, or ambiguous, say so clearly instead of guessing.
"""

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

if os.name == "nt":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("deep_agent")

try:
    _toon_env_raw = os.getenv("ASKMOJO_USE_TOON")
    _toon_env_norm = (_toon_env_raw or "").strip().lower()
    _toon_enabled = _toon_env_norm not in {"0", "false", "no", "off"}
    logger.info(
        "[TOON] startup | ASKMOJO_USE_TOON=%r | enabled=%s | toon_installed=%s | tiktoken_installed=%s",
        _toon_env_raw,
        _toon_enabled,
        toon_encode is not None,
        tiktoken_lib is not None,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# App imports (SQLite models + config)
# ---------------------------------------------------------------------------

from app.core.config import settings
from app.sqlite.database import SessionLocal
from app.sqlite.models import Category


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

client = OpenAI(api_key=settings.openai_api_key)


_OPENAI_TIMEOUT_SECONDS = float(os.getenv("ASKMOJO_OPENAI_TIMEOUT_SECONDS", "20"))


_SOURCE_BLOCK_SPLIT_RE = re.compile(r"(?is)\n\s*\*?\*?source\*?\*?\s*:?\s*")


def _strip_source_block(text: str) -> str:
    return _SOURCE_BLOCK_SPLIT_RE.split(text or "", maxsplit=1)[0].strip()


def _strip_markdown_code_fence(text: str) -> str:
    s = (text or "").strip()
    if "```json" in s:
        return s.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in s:
        return s.split("```", 1)[1].split("```", 1)[0].strip()
    return s


def _append_unique_str(out: list[str], seen: set[str], value: str, *, key: str | None = None) -> None:
    v = (value or "").strip()
    if not v:
        return
    k = (key if key is not None else v).strip().lower()
    if not k or k in seen:
        return
    seen.add(k)
    out.append(v)


def _count_tokens(text: str) -> int:
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return 0
    if tiktoken_lib is not None:
        try:
            enc = tiktoken_lib.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    # Approximation fallback: 1 token ~ 4 chars
    return max(1, len(text) // 4) if text else 0


def _looks_like_table(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    if "|" in s and "\n" in s:
        return True
    lines = [line.strip() for line in s.splitlines() if line.strip()]
    if len(lines) >= 2 and all(len(re.split(r"\s{2,}|\t", line)) >= 2 for line in lines[:4]):
        return True
    return False


def _looks_like_list(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    return bool(re.search(r"(?m)^\s*(?:[-*•]|\d+\.)\s+", s))


def _format_chunk_for_prompt(text: str, *, preserve_structure: bool = True) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if preserve_structure and (_looks_like_table(raw) or _looks_like_list(raw)):
        return raw
    return re.sub(r"\s+", " ", raw).strip()


def _format_fallback_answer(text: str, source: str | None = None) -> str:
    body = _format_chunk_for_prompt(text, preserve_structure=True)
    if not body:
        return "I could not find relevant information in the available documents."
    if _looks_like_table(body):
        answer = f"**Answer:**\n\n{body}"
    elif _looks_like_list(body):
        answer = f"**Answer:**\n\n{body}"
    else:
        answer = f"**Answer:**\n{body[:900]}"
    if source:
        answer += f"\n\n**Source:** {source}"
    return answer


def _normalize_rag_query(user_query: str) -> str:
    q = (user_query or "").strip()
    if not q:
        return q

    q = re.sub(r"^\s*@+", "", q).strip()
    q = re.sub(r"^\s*(please|pls)\s+", "", q, flags=re.IGNORECASE)
    q = re.sub(r"^\s*(can you|could you|would you|will you)\s+", "", q, flags=re.IGNORECASE)

    replacements = [
        (r"^\s*(tell me about|tell me|explain to me|explain|describe|give me details about|give details about|give an overview of|overview of)\s+", ""),
        (r"^\s*(what is|what are)\s+", ""),
        (r"^\s*(summary of|summarize)\s+", ""),
    ]
    for pattern, repl in replacements:
        q2 = re.sub(pattern, repl, q, flags=re.IGNORECASE).strip()
        if q2 and q2 != q:
            q = q2
            break

    q = re.sub(r"\s+", " ", q).strip(" ?!.")
    if not q:
        return (user_query or "").strip()

    q_lower = q.lower()
    if any(t in q_lower for t in ["timeline", "timelines", "duration", "durations", "month", "months", "phase", "phases"]):
        return f"project timeline {q}".strip()
    if any(t in q_lower for t in ["compare", "comparison", "versus", "vs", "difference", "differences", "both"]):
        return f"compare {q}".strip()
    return q


def _is_specific_cost_question(user_query: str) -> bool:
    q = (user_query or "").strip().lower()
    if not q:
        return False

    cost_terms = [
        "cost", "costs", "price", "pricing", "budget", "budgets", "fee", "fees",
        "rate", "rates", "quote", "quotation", "estimate", "estimated", "amount",
        "amounts", "rate card",
    ]
    payment_terms_only = [
        "payment terms", "billing terms", "invoice", "invoices", "invoice generation",
        "payment timeline", "billing cycle", "when are invoices", "when is invoice",
        "within 30 days", "net 30",
    ]
    numeric_intent_terms = [
        "how much", "total", "monthly", "per month", "for the first", "first 6 months",
        "first six months", "specific price", "specific cost", "project budget",
        "project cost", "project price",
    ]

    has_cost_term = any(term in q for term in cost_terms)
    if not has_cost_term:
        return False

    has_payment_only_context = any(term in q for term in payment_terms_only)
    has_numeric_intent = any(term in q for term in numeric_intent_terms) or bool(re.search(r"\b\d+\b", q))

    if has_numeric_intent:
        return True

    if has_payment_only_context and not has_numeric_intent:
        return False

    return True


def _builtin_toon_encode(data: Any) -> str:
    if orjson_lib is not None:
        try:
            b = orjson_lib.dumps(data)
            return b.decode("utf-8") if isinstance(b, (bytes, bytearray)) else str(b)
        except Exception:
            pass
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(data)


def _maybe_toon_payload(
    data: Any,
    *,
    call_name: str,
    data_name: str,
    indent: int | None = None,
) -> tuple[str, int, int, int]:
    """Return (payload_str, json_tokens, toon_tokens, savings).

    Controlled by env ASKMOJO_USE_TOON (default 0). If disabled or toon not installed,
    this falls back to JSON. This keeps behavior unchanged unless explicitly enabled.
    """
    env_raw = os.getenv("ASKMOJO_USE_TOON")
    env_norm = (env_raw or "").strip().lower()
    use_toon = env_norm not in {"0", "false", "no", "off"}

    # Baseline JSON for comparison (default separators include spaces; do not minify here).
    json_str = json.dumps(data, ensure_ascii=False, indent=indent)
    json_tokens = _count_tokens(json_str)

    if not use_toon:
        _record_toon_usage(call_name, data_name, json_tokens, json_tokens, 0)
        return json_str, json_tokens, json_tokens, 0

    _toon_encode = toon_encode
    if _toon_encode is None:
        try:
            from toon import encode as _toon_encode  # type: ignore
        except ImportError:
            _toon_encode = None

    if _toon_encode is None:
        try:
            toon_str = _builtin_toon_encode(data)
        except Exception:
            toon_str = json_str
    else:
        try:
            toon_str = _toon_encode(data)
        except Exception as e:
            logger.warning("[TOON] encode failed for %s/%s: %s", call_name, data_name, e)
            _record_toon_usage(call_name, data_name, json_tokens, json_tokens, 0)
            return json_str, json_tokens, json_tokens, 0

    try:
        if isinstance(toon_str, bytes):
            toon_str = toon_str.decode("utf-8")
        elif not isinstance(toon_str, str):
            toon_str = str(toon_str)
    except Exception:
        toon_str = str(toon_str)

    toon_tokens = _count_tokens(toon_str)
    savings = json_tokens - toon_tokens
    _record_toon_usage(call_name, data_name, json_tokens, toon_tokens, savings)
    try:
        logger.info(
            "[TOON] %s | %s | json_tokens=%d | toon_tokens=%d | savings=%d",
            call_name,
            data_name,
            json_tokens,
            toon_tokens,
            savings,
        )
    except Exception:
        pass
    return toon_str, json_tokens, toon_tokens, savings


def _openai_chat_create_with_timeout(**kwargs):
    """Windows-safe timeout wrapper for OpenAI calls."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(lambda: client.chat.completions.create(**kwargs))
        result = fut.result(timeout=_OPENAI_TIMEOUT_SECONDS)
        try:
            usage = getattr(result, "usage", None)
            total = int(getattr(usage, "total_tokens", 0) or 0) if usage else 0
        except Exception:
            total = 0
        if total:
            label = _OPENAI_TOKEN_LABEL.get()
            store = _get_token_store()
            store["total"] = int(store.get("total", 0) or 0) + total
            if label:
                by_label = store.setdefault("by_label", {})
                by_label[label] = int(by_label.get(label, 0) or 0) + total
            _OPENAI_TOKEN_STORE.set(store)
        return result


_OPENAI_TOKEN_LABEL: contextvars.ContextVar[str | None] = contextvars.ContextVar("_OPENAI_TOKEN_LABEL", default=None)
_OPENAI_TOKEN_STORE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_OPENAI_TOKEN_STORE", default=None
)

_TOON_USAGE_STORE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("_TOON_USAGE_STORE", default=None)


def _get_toon_store() -> dict[str, Any]:
    store = _TOON_USAGE_STORE.get()
    if not isinstance(store, dict):
        store = {"calls": []}
        _TOON_USAGE_STORE.set(store)
    if "calls" not in store or not isinstance(store.get("calls"), list):
        store["calls"] = []
    return store


def _record_toon_usage(call_name: str, data_name: str, json_tokens: int, toon_tokens: int, savings: int) -> None:
    try:
        store = _get_toon_store()
        jt = int(json_tokens or 0)
        tt = int(toon_tokens or 0)
        sv = int(savings or 0)
        pct = (sv / jt * 100.0) if jt > 0 else 0.0
        store["calls"].append(
            {
                "call_name": f"{call_name}:{data_name}",
                "json_tokens": jt,
                "toon_tokens": tt,
                "savings": sv,
                "savings_percent": pct,
            }
        )
        _TOON_USAGE_STORE.set(store)
    except Exception:
        pass


def _get_token_store() -> dict[str, Any]:
    store = _OPENAI_TOKEN_STORE.get()
    if not isinstance(store, dict):
        store = {"total": 0, "by_label": {}}
        _OPENAI_TOKEN_STORE.set(store)
    if "total" not in store:
        store["total"] = 0
    if "by_label" not in store or not isinstance(store.get("by_label"), dict):
        store["by_label"] = {}
    return store


@contextlib.contextmanager
def _track_openai_tokens(label: str):
    prev_label = _OPENAI_TOKEN_LABEL.set(label)
    prev_store = _get_token_store()
    before_total = int(prev_store.get("total", 0) or 0)
    before_label = int((prev_store.get("by_label") or {}).get(label, 0) or 0)
    try:
        yield
    finally:
        store = _get_token_store()
        after_total = int(store.get("total", 0) or 0)
        after_label = int((store.get("by_label") or {}).get(label, 0) or 0)
        store["__last_delta_total__"] = after_total - before_total
        store["__last_delta_label__"] = after_label - before_label
        _OPENAI_TOKEN_STORE.set(store)
        _OPENAI_TOKEN_LABEL.reset(prev_label)


def _last_tracked_token_delta() -> int:
    store = _get_token_store()
    return int(store.get("__last_delta_label__", 0) or 0)


def _aggregate_toon_usage_by_call(calls: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        raw_name = str(call.get("call_name") or "")
        base_name = raw_name.split(":", 1)[0] if raw_name else "unknown"
        entry = grouped.setdefault(
            base_name,
            {
                "call": base_name,
                "json_tokens": 0,
                "toon_tokens": 0,
                "savings": 0,
            },
        )
        entry["json_tokens"] += int(call.get("json_tokens", 0) or 0)
        entry["toon_tokens"] += int(call.get("toon_tokens", 0) or 0)
        entry["savings"] += int(call.get("savings", 0) or 0)
    return grouped


def _build_tool_timing_summary(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool") or "unknown")
        entry = summary.setdefault(
            tool_name,
            {
                "tool": tool_name,
                "calls": 0,
                "total_time_seconds": 0.0,
                "total_tokens_used": 0,
            },
        )
        entry["calls"] += 1
        entry["total_time_seconds"] += float(step.get("time_taken_seconds", 0.0) or 0.0)
        entry["total_tokens_used"] += int(step.get("tokens_used", 0) or 0)
    return sorted(
        (
            {
                "tool": item["tool"],
                "calls": item["calls"],
                "total_time_seconds": round(float(item["total_time_seconds"]), 4),
                "total_tokens_used": int(item["total_tokens_used"]),
            }
            for item in summary.values()
        ),
        key=lambda item: item["total_time_seconds"],
        reverse=True,
    )


def _run_timed_call(fn, /, *args, **kwargs):
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, round(time.perf_counter() - started, 4)


# No local embedding model needed (using OpenAI API)
chroma_client = chromadb.PersistentClient(path="app/vector_db/chroma_db")


class _TTLCache:
    def __init__(self, ttl_seconds: float, max_items: int = 2048):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._data: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        now = time.time()
        v = self._data.get(key)
        if not v:
            return None
        exp, val = v
        if exp < now:
            self._data.pop(key, None)
            return None
        return val

    def set(self, key: str, val: object):
        if len(self._data) >= self.max_items:
            self._data.pop(next(iter(self._data)), None)
        self._data[key] = (time.time() + self.ttl_seconds, val)


_rewrite_cache = _TTLCache(ttl_seconds=300)


_EMBEDDING_DIM_MISMATCH_HELP = (
    "CRITICAL: Embedding dimension mismatch detected.\n"
    "Your persisted ChromaDB collections were built with a different embedding dimension.\n\n"
    "Fix (OpenAI embeddings everywhere):\n"
    "  1) Run: python scripts/drop_chroma_collections.py\n"
    "  2) Run: python scripts/reembed_all.py\n"
    "  3) Restart the app to re-ingest all documents\n\n"
    "Until you do this, dense retrieval is not usable and results will be incomplete."
)


def _is_dim_mismatch_error(e: Exception) -> bool:
    msg = str(e).lower()
    return (
        ("expecting embedding with dimension" in msg)
        or ("dimension" in msg and "got" in msg)
        or ("expected" in msg and "got" in msg)
    )


def _embedding_dim_mismatch_payload(collection_name: str, err: Exception) -> dict:
    return {
        "error_type": "embedding_dim_mismatch",
        "collection_name": collection_name,
        "message": _EMBEDDING_DIM_MISMATCH_HELP,
        "original_error": str(err),
    }


def _extract_dims_from_dim_error_message(e: Exception) -> tuple[int | None, int | None]:
    msg = str(e)
    m = re.search(r"expected\s+(\d+)\s*,\s*got\s+(\d+)", msg, flags=re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"dimension\s+(\d+).+got\s+(\d+)", msg, flags=re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# Canonical stopword set — used by all tokenisers in this file.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "with", "about",
    "which", "what", "where", "when", "who", "does", "do", "is", "are", "at", "by",
    "from", "be", "this", "that", "it", "we", "you", "i", "was", "were",
    "contains", "contain", "include", "includes",
    "information", "details", "documents", "document",
    "collection", "collections", "category", "categories",
})

_PROBLEM_QUERY_TERMS = {
    "problem", "problems", "challenge", "challenges", "issue", "issues", "limitation",
    "limitations", "struggle", "struggled", "gap", "gaps", "pain", "pains",
    "bottleneck", "bottlenecks", "obstacle", "obstacles", "difficulty", "difficulties",
}

_PROBLEM_EXPANSION_TERMS = [
    "customer",
    "challenges",
    "issues",
    "problems",
    "limitations",
    "struggled",
    "scalability",
    "traceability",
    "manual",
    "effort",
    "execution",
    "reporting",
    "mapping",
    "test cases",
    "scripts",
    "vbscript",
    "testcomplete",
]

_SOLUTION_QUERY_TERMS = {
    "solution", "approach", "architecture", "framework", "implementation", "implement", "how",
    "resolved", "fix", "designed", "built",
}

_TECHNOLOGY_QUERY_TERMS = {
    "technology", "technologies", "tool", "tools", "stack", "used", "using", "platform",
    "python", "testcomplete", "framework",
}

_METRICS_QUERY_TERMS = {
    "roi", "metric", "metrics", "impact", "results", "improvement", "improvements",
    "benefit", "benefits", "outcome", "outcomes", "coverage", "cycle", "time", "value",
}

_SUMMARY_QUERY_TERMS = {
    "summary", "summarize", "overview", "case", "study", "high", "level", "key", "points",
    "brief",
}

_QUERY_TYPE_EXPANSIONS: dict[str, list[str]] = {
    "problem": _PROBLEM_EXPANSION_TERMS,
    "solution": ["solution", "approach", "framework", "architecture", "implementation", "method", "design"],
    "technology": ["technology", "tools", "stack", "platform", "python", "testcomplete", "framework"],
    "metrics": ["roi", "metrics", "results", "impact", "improvement", "coverage", "qa cycle", "business value"],
    "summary": ["overview", "summary", "case study", "high level", "key points", "customer context", "solution", "results"],
}

_QUERY_TYPE_TERMS: dict[str, set[str]] = {
    "problem": _PROBLEM_QUERY_TERMS,
    "solution": _SOLUTION_QUERY_TERMS,
    "technology": _TECHNOLOGY_QUERY_TERMS,
    "metrics": _METRICS_QUERY_TERMS,
    "summary": _SUMMARY_QUERY_TERMS,
}

_QUERY_TYPE_METADATA_HINTS: dict[str, tuple[str, ...]] = {
    "problem": ("BUSINESS_PROBLEMS", "CUSTOMER_CHALLENGES", "KEY_CHALLENGES", "CONTEXT_SUMMARY", "DOCUMENT_PURPOSE", "KEY_ENTITIES"),
    "solution": ("SOLUTIONS_OR_METHODS", "SOLUTION", "SOLUTION_APPROACH", "APPROACH", "ARCHITECTURE", "FRAMEWORK", "IMPLEMENTATION", "CONTEXT_SUMMARY"),
    "technology": ("TOOLS_AND_TECHNOLOGIES", "TECHNOLOGY", "TECHNOLOGIES", "TOOLS", "STACK", "FRAMEWORK", "CONTEXT_SUMMARY", "MAIN_TOPICS"),
    "metrics": ("ROI", "METRICS", "RESULTS", "IMPACT", "OUTCOMES", "BUSINESS_VALUE", "BENEFITS", "CONTEXT_SUMMARY", "TIMELINE_OR_PHASES"),
    "summary": ("CONTEXT_SUMMARY", "DOCUMENT_PURPOSE", "PRIMARY_ENTITY", "MAIN_TOPICS", "KEY_ENTITIES", "KEYWORDS", "ROUTING_KEYWORDS", "ENUMERATED_CONTENT"),
}


def _tokenize(text: str) -> list[str]:
    """Tokenise text, removing stopwords and single-char tokens."""
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in toks if t and t not in _STOPWORDS and len(t) > 1]


# =====================================================================
# SHARED DATA UTILITIES
# =====================================================================

def _parse_doc_description(doc: Any) -> Any:
    """Parse a document's description field from JSON, falling back to raw string."""
    if not getattr(doc, "description", None):
        return None
    try:
        return json.loads(doc.description)
    except Exception:
        return doc.description


def _build_chroma_where(doc_id: int, hard_filters: dict | None) -> dict:
    """Build a ChromaDB `where` clause merging doc_id with optional hard filters."""
    if not hard_filters:
        return {"document_id": doc_id}
    parts = [{"document_id": doc_id}] + [
        {k: v} for k, v in hard_filters.items() if v
    ]
    return {"$and": parts} if len(parts) > 1 else {"document_id": doc_id}


def detect_query_type(query: str) -> str:
    q_tokens = set(_tokenize(query))
    if q_tokens & _PROBLEM_QUERY_TERMS:
        return "problem"
    if q_tokens & _SOLUTION_QUERY_TERMS:
        return "solution"
    if q_tokens & _TECHNOLOGY_QUERY_TERMS:
        return "technology"
    if q_tokens & _METRICS_QUERY_TERMS:
        return "metrics"
    if q_tokens & _SUMMARY_QUERY_TERMS:
        return "summary"
    return "general"


def _is_problem_query(query: str) -> bool:
    return detect_query_type(query) == "problem"


def _minimum_chunks_per_doc(query: str, requested: int) -> int:
    base = max(1, int(requested or 0))
    query_type = detect_query_type(query)
    if query_type in {"problem", "technology", "metrics"}:
        return max(base, 5)
    if query_type in {"solution", "summary"}:
        return max(base, 8)
    return max(base, 6)


def _tool3_local_per_doc_limit(
    answer_mode: str | None,
    *,
    query_type: str | None = None,
    total_docs: int,
    requested_limit: int,
) -> int:
    """Dynamic chunk limit calculation for Tool 3.
    
    Rules:
    - Summary queries: Ensure high-coverage (min 8 chunks).
    - Cross-doc (Multi-doc): Moderate depth (5-6 chunks).
    - Normal (Single-doc): Focused context (3-4 chunks).
    """
    base = max(1, int(requested_limit or 0))
    q_type = (query_type or "").strip().lower()
    
    # Summary priority: If the query is a summary, we need broad coverage.
    if q_type == "summary":
        return max(base, 8)
        
    # Cross-document retrieval: moderate depth per document.
    if total_docs > 1:
        return min(base, 6)
        
    # Normal / Single-document retrieval: focused context.
    return min(base, 4)


def _extract_metadata_support_text(description: Any, query_type: str) -> str:
    data = description
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return ""

    if not isinstance(data, dict):
        return ""

    values: list[str] = []
    preferred_keys = _QUERY_TYPE_METADATA_HINTS.get(query_type, ())

    def _append_value(value: Any):
        if isinstance(value, list):
            values.extend([str(item).strip() for item in value if str(item).strip()])
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())

    for key in preferred_keys:
        _append_value(data.get(key))

    if not values:
        for key, value in data.items():
            key_upper = str(key).upper()
            if any(token in key_upper for token in preferred_keys):
                _append_value(value)

    return " ".join(values)


def _boost_chunks_by_query_type(query: str, chunks: list[dict]) -> list[dict]:
    query_type = detect_query_type(query)
    if query_type == "general" or not chunks:
        return chunks

    query_terms = set(_tokenize(query)) | _QUERY_TYPE_TERMS.get(query_type, set())
    query_type_phrases = _QUERY_TYPE_EXPANSIONS.get(query_type, [])
    ranked: list[tuple[float, dict]] = []
    
    for idx, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        chunk_text = str(chunk.get("chunk_text", "") or chunk.get("text", "")).strip()
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        
        # Metadata enrichment
        section_path = str(metadata.get("section_path", "")).lower()
        heading_1 = str(metadata.get("heading_level_1", "")).lower()
        keywords = str(metadata.get("keywords", "")).lower()
        
        section_text = " ".join([section_path, heading_1, keywords])
        combined = f"{chunk_text} {section_text}".strip().lower()
        tokens = set(_tokenize(combined))
        
        overlap = len(tokens & query_terms)
        type_overlap = len(tokens & _QUERY_TYPE_TERMS.get(query_type, set()))
        
        exact_phrase_bonus = 0.0
        for phrase in query_type_phrases:
            if phrase in combined:
                exact_phrase_bonus += 0.75
        
        # Metadata specific boosts
        metadata_boost = 0.0
        if any(t in heading_1 for t in query_terms):
            metadata_boost += 2.0
        if any(t in keywords for t in query_terms):
            metadata_boost += 1.5
            
        # Low-information penalty
        penalty = 0.0
        if len(chunk_text.split()) < 10 and overlap < 1:
            penalty = 5.0

        base_score = float(chunk.get("rrf_score", 0.0))
        boost_score = (overlap * 1.25) + (type_overlap * 1.75) + exact_phrase_bonus + metadata_boost - penalty
        ranked.append((base_score + boost_score - (idx * 1e-6), chunk))

    ranked.sort(key=lambda item: item[0], reverse=True)
    boosted = []
    for rank, (_score, chunk) in enumerate(ranked, start=1):
        chunk = dict(chunk)
        chunk["rrf_score"] = float(chunk.get("rrf_score", 0.0))
        chunk["query_type"] = query_type
        chunk["query_type_boost_score"] = round(_score, 4)
        chunk["rank"] = rank
        boosted.append(chunk)
    return boosted


def _is_collection_structure_question(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False
    if re.search(r"\b(which|what)\s+(collection|collections|category|categories)\b", q):
        return True
    if re.search(r"\b(collection|collections)\b.*\b(contains|contain|include|includes)\b", q):
        return True
    return False


# _format_collection_structure_answer removed — callers use the value directly.


def _is_document_inventory_question(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False
    patterns = [
        r"\bwhich\s+all\s+documents?\b",
        r"\bwhat\s+documents?\s+do\s+you\s+have\b",
        r"\bwhich\s+documents?\s+do\s+you\s+have\b",
        r"\blist\s+(all\s+)?documents?\b",
        r"\bshow\s+(all\s+)?documents?\b",
        r"\bname(s)?\s+of\s+(the\s+)?documents?\b",
    ]
    return any(re.search(p, q) for p in patterns)


def _build_document_inventory_answer(db: Session, limit: int = 200) -> str:
    docs = (
        db.query(Document)
        .filter(Document.processed == True)
        .order_by(Document.title.asc())
        .all()
    )
    if not docs:
        return "I don't have any documents in the registry yet."

    shown = docs[: max(1, int(limit or 200))]
    lines = [f'• "{d.title}"' for d in shown if (d.title or "").strip()]
    if not lines:
        return f"I currently have {len(docs)} document(s), but titles are not available."

    suffix = ""
    if len(docs) > len(shown):
        suffix = f"\n…and {len(docs) - len(shown)} more."
    return (
        f"I currently have {len(docs)} document(s):\n"
        + "\n".join(lines)
        + suffix
    )


def _score_collections_from_metadata(question: str, collections_payload: dict) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Score collections using weighted structured metadata overlap."""
    cols = collections_payload.get("collections") if isinstance(collections_payload, dict) else None
    if not isinstance(cols, list) or not cols:
        return [], {"reason": "no_collections"}

    q = question.lower()
    q_tokens = set(_tokenize(question))
    if not q_tokens:
        return [], {"reason": "no_query_tokens"}

    # Field weights matching deep_agent_clean.py
    WEIGHTS = {
        "key_entities":     4.0,
        "routing_keywords": 3.0,
        "example_questions": 2.5,
        "solution_areas":   2.5,
        "technologies":     2.0,
        "domains":          2.0,
        "industries":       1.5,
        "doc_types":        1.5,
        "routing_hint":     1.0,
        "description":      0.5,
    }

    scored = []

    for c in cols:
        name = (c or {}).get("collection_name") or ""
        meta_raw = (c or {}).get("metadata") or ""
        
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
        except Exception:
            meta = {"description": str(meta_raw)}

        current_score = 0.0
        # Name match
        current_score += len(q_tokens & set(_tokenize(name))) * 1.5

        for field, weight in WEIGHTS.items():
            val = meta.get(field, "")
            if not val: continue
            
            field_tokens = set(_tokenize(str(val)))
            overlap = len(q_tokens & field_tokens)
            current_score += overlap * weight

            if field == "key_entities":
                for entity in str(val).split(","):
                    e = entity.strip().lower()
                    if e and len(e) > 2 and e in q:
                        current_score += 5.0

            if field == "example_questions":
                for eq in str(val).split("|"):
                    eq_tokens = set(_tokenize(eq))
                    if len(q_tokens & eq_tokens) >= 2:
                        current_score += len(q_tokens & eq_tokens) * 2.0

        scored.append({"collection": name, "score": round(current_score, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    debug = {"top_scores": scored[:5], "query_tokens": sorted(list(q_tokens))}
    return scored, debug


def _pick_best_collection_from_metadata(question: str, collections_payload: dict) -> tuple[str | None, dict]:
    """Pick the most relevant collection using weighted structured metadata scoring."""
    scored, debug = _score_collections_from_metadata(question, collections_payload)
    if not scored:
        return None, debug
    best = scored[0]
    if float(best.get("score", 0.0) or 0.0) <= 0.0:
        return None, {**debug, "reason": "no_overlap"}
    return str(best.get("collection") or ""), debug


def _sanitize_filters(filters: dict | None) -> dict | None:
    if not filters or not isinstance(filters, dict):
        return None
    allowed = {"doc_type", "domain", "technology", "entity", "use_case", "industry"}
    out = {}
    for k, v in filters.items():
        if k in allowed and v not in (None, ""):
            out[k] = v
    return out or None


def _normalize_collection_candidate(name: str | None) -> str:
    return (name or "").strip().lower().replace(" ", "_")


def _normalize_collection_identity(name: str | None) -> str:
    return re.sub(r"[_\-\s]+", "", (name or "").strip().lower())


def _normalize_text_identity(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _query_explicitly_mentions_collections(user_query: str, collection_names: list[str] | None) -> bool:
    """Return True when the query explicitly references any provided collection name."""
    q_norm = _normalize_text_identity(user_query)
    if not q_norm:
        return False
    for name in collection_names or []:
        n_norm = _normalize_text_identity(name)
        if n_norm and n_norm in q_norm:
            return True
    return False


def _extract_exact_query_phrases(user_query: str) -> list[str]:
    """Extract quoted phrases from user query (single or double quotes)."""
    phrases: list[str] = []
    if not user_query:
        return phrases
    for m in re.finditer(r"'([^']{3,})'|\"([^\"]{3,})\"", user_query):
        phrase = (m.group(1) or m.group(2) or "").strip().lower()
        if phrase:
            phrases.append(phrase)
    return phrases


def _should_aggregate_multi_doc(user_query: str) -> bool:
    """Heuristic: queries that benefit from synthesis across multiple documents."""
    q = _normalize_rag_query(user_query).lower()
    if not q:
        return False
    return (
        ("summarize" in q)
        or ("summary" in q)
        or ("explain" in q)
        or ("tell me about" in q)
        or ("what is" in q)
        or ("describe" in q)
        or ("overview" in q)
        or ("details" in q)
        or ("value proposition" in q)
        or ("key aspects" in q)
        or ("key points" in q)
        or (detect_query_type(user_query) == "summary")
    )


def _query_prefers_collection(user_query: str, collection_name: str | None) -> bool:
    """Heuristic preference map to stabilize routing for recurring query styles."""
    q = (user_query or "").lower()
    c = _normalize_collection_identity(collection_name or "")
    if not q or not c:
        return False

    cross_team_terms = [
        "raci",
        "jira stor",
        "test design session",
        "e2e test automation",
        "pair testing",
        "jagged frontier",
        "human-ai task classification",
    ]
    customer_pitch_terms = [
        "observability",
        "performance tracking",
        "prometheus",
        "grafana",
        "datadog",
        "elk",
        "monitoring",
    ]

    if c == _normalize_collection_identity("cross_team_decks"):
        return any(t in q for t in cross_team_terms)
    if c == _normalize_collection_identity("customer_pitch_decks"):
        return any(t in q for t in customer_pitch_terms)
    return False


def _extract_named_entities_from_query(user_query: str, hints: dict | None = None) -> list[str]:
    entities: list[str] = []
    seen: set[str] = set()
    h = hints or {}

    for value in [h.get("entity"), h.get("use_case"), h.get("technology"), h.get("industry")]:
        if isinstance(value, str):
            _append_unique_str(entities, seen, value)

    quoted = _extract_exact_query_phrases(user_query)
    for phrase in quoted:
        _append_unique_str(entities, seen, phrase)

    known_terms = [
        "Jagged Frontier",
        "Test Design Session",
        "E2E Test Automation",
        "RACI matrix",
        "Skin in the Game",
        "Prachar AI",
        "TestPert",
    ]
    q = user_query or ""
    for term in known_terms:
        if term.lower() in q.lower():
            _append_unique_str(entities, seen, term)

    title_case_spans = re.findall(r"\b(?:[A-Z][a-zA-Z0-9.&/-]*\s+){1,3}[A-Z][a-zA-Z0-9.&/-]*\b", user_query or "")
    for span in title_case_spans:
        cleaned = span.strip(" ?!.,:;()[]{}\"'")
        if len(cleaned) >= 4:
            _append_unique_str(entities, seen, cleaned)

    return entities[:8]


def _extract_anchor_terms_from_query(user_query: str, hints: dict | None = None) -> list[str]:
    anchors: list[str] = []
    seen: set[str] = set()
    q = (user_query or "").strip()
    if not q:
        return anchors

    for phrase in _extract_exact_query_phrases(q):
        _append_unique_str(anchors, seen, phrase)

    query_patterns = [
        r"\b(?:jagged frontier|skin in the game|test design session|e2e test automation|raci matrix)\b",
        r"\b(?:timeline|timelines|duration|durations|month|months|phase|phases)\b",
        r"\b(?:observability|performance tracking|prometheus|grafana|datadog|elk)\b",
    ]
    for pattern in query_patterns:
        for match in re.finditer(pattern, q, flags=re.IGNORECASE):
            term = match.group(0).strip()
            _append_unique_str(anchors, seen, term)

    for entity in _extract_named_entities_from_query(q, hints):
        _append_unique_str(anchors, seen, entity)

    return anchors[:10]


def _extract_collection_name(value: Any) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    if isinstance(value, dict):
        for key in ("collection_name", "name", "collection", "id"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


def _normalize_collection_list(names: list[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names or []:
        extracted = _extract_collection_name(name)
        if not extracted:
            continue
        resolved = _resolve_existing_collection_name(extracted)
        if not resolved:
            continue
        norm = _normalize_collection_identity(resolved)
        _append_unique_str(out, seen, resolved, key=norm)
    return out


def _text_matches_any_anchor(text: str, anchors: list[str] | None = None, entities: list[str] | None = None) -> int:
    searchable = (text or "").lower()
    score = 0
    for term in (anchors or []) + (entities or []):
        t = (term or "").strip().lower()
        if not t:
            continue
        if t in searchable:
            score += 1
    return score


def _doc_supports_entity(doc_payload: dict, entity: str) -> bool:
    if not isinstance(doc_payload, dict) or not entity:
        return False
    searchable = " ".join(
        [
            str(doc_payload.get("title") or ""),
            str(doc_payload.get("file_name") or ""),
            json.dumps(doc_payload.get("description"), ensure_ascii=False) if isinstance(doc_payload.get("description"), dict) else str(doc_payload.get("description") or ""),
        ]
    )
    return _text_matches_any_anchor(searchable, anchors=[entity]) > 0


def _score_chunk_with_anchors(
    chunk: dict,
    query: str,
    anchors: list[str] | None = None,
    entities: list[str] | None = None,
    answer_mode: str | None = None,
) -> float:
    text = str(chunk.get("chunk_text") or chunk.get("text") or "")
    base = float(chunk.get("rrf_score", chunk.get("score", 0.0)) or 0.0)
    text_tokens = set(_tokenize(text))
    query_tokens = set(_tokenize(query))
    overlap = len(text_tokens & query_tokens)
    anchor_hits = _text_matches_any_anchor(text, anchors=anchors, entities=entities)
    
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    heading_1 = str(metadata.get("heading_level_1", "")).lower()
    keywords = str(metadata.get("keywords", "")).lower()
    
    metadata_hits = 0
    for term in (anchors or []) + (entities or []):
        t = (term or "").strip().lower()
        if not t: continue
        if t in heading_1: metadata_hits += 2
        if t in keywords: metadata_hits += 1

    answer_terms = 0
    q = (query or "").lower()
    text_lower = text.lower()
    is_timeline = (answer_mode == "timeline") or any(t in q for t in ["timeline", "timelines", "duration", "month", "months", "phase"])
    is_compare = answer_mode == "compare"
    if is_timeline:
        answer_terms += sum(1 for t in ["timeline", "duration", "month", "months", "phase"] if t in text_lower)
    if "raci" in q or "accountable" in q:
        answer_terms += sum(1 for t in ["raci", "accountable", "responsible", "e2e test automation"] if t in text_lower)
    
    numeric_duration_bonus = 0.0
    if is_timeline and re.search(r"\b\d+(?:\.\d+)?\s*(?:-|to)?\s*\d*(?:\.\d+)?\s*(?:month|months|week|weeks)\b", text_lower):
        numeric_duration_bonus = 25.0
    
    timeline_keyword_bonus = 0.0
    if is_timeline:
        timeline_keyword_bonus = sum(1.0 for t in ["timeline", "duration", "month", "months", "phase"] if t in text_lower)
    
    compare_bonus = 0.0
    if is_compare:
        compare_bonus = sum(1.0 for t in ["versus", "compared", "difference", "whereas", "both"] if t in text_lower)
        
    low_info_penalty = 0.0
    if len(text.split()) < 12 and anchor_hits == 0 and overlap < 1:
        low_info_penalty = 10.0

    return (
        base
        + (overlap * 0.25)
        + (anchor_hits * 2.5)
        + (metadata_hits * 1.5)
        + (answer_terms * 1.0)
        + numeric_duration_bonus
        + (timeline_keyword_bonus * 4.0)
        + (compare_bonus * 4.0)
        - low_info_penalty
    )


def _get_chunk_text(chunk: dict | str | None) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("chunk_text") or chunk.get("text") or "").strip()
    return str(chunk or "").strip()


def _get_chunk_metadata(chunk: dict | None) -> dict:
    if isinstance(chunk, dict) and isinstance(chunk.get("metadata"), dict):
        return dict(chunk.get("metadata") or {})
    return {}


def _chunk_exact_phrase_hits(text: str, exact_phrases: list[str] | None = None) -> list[str]:
    searchable = (text or "").lower()
    hits: list[str] = []
    for phrase in exact_phrases or []:
        p = (phrase or "").strip().lower()
        if p and p in searchable:
            hits.append(p)
    return hits


def _resolve_chunk_identity(
    *,
    collection_name: str,
    document_id: int,
    metadata: dict | None,
    chunk_text: str,
) -> str:
    meta = metadata or {}
    for key in ("chunk_id", "ocr_chunk_id", "source_chunk_id"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value

    chunk_index = meta.get("chunk_index")
    if chunk_index not in (None, ""):
        return f"{collection_name}:{document_id}:{chunk_index}"

    digest = hashlib.sha1(
        f"{collection_name}|{document_id}|{(chunk_text or '').strip()}".encode("utf-8", errors="ignore")
    ).hexdigest()
    return f"hash:{digest}"


def _build_canonical_chunk(
    *,
    chunk: dict,
    collection_name: str,
    document_id: int,
    document_title: str,
    user_query: str,
    anchors: list[str] | None,
    entities: list[str] | None,
    answer_mode: str | None,
    retrieval_stage: str,
    dense_rank: int | None = None,
    sparse_rank: int | None = None,
    exact_phrases: list[str] | None = None,
    base_reason: str | None = None,
) -> dict:
    metadata = _get_chunk_metadata(chunk)
    chunk_text = _get_chunk_text(chunk)
    chunk_id = _resolve_chunk_identity(
        collection_name=collection_name,
        document_id=document_id,
        metadata=metadata,
        chunk_text=chunk_text,
    )

    anchor_score = float(_text_matches_any_anchor(chunk_text, anchors=anchors, entities=None))
    entity_score = float(_text_matches_any_anchor(chunk_text, anchors=None, entities=entities))
    base_rrf = float(chunk.get("rrf_score", 0.0) or 0.0)
    query_type_score = float(
        _score_chunk_with_anchors(
            {
                "chunk_text": chunk_text,
                "metadata": metadata,
                "rrf_score": base_rrf,
            },
            user_query,
            anchors=[],
            entities=[],
            answer_mode=answer_mode,
        )
        - base_rrf
    )
    exact_hits = _chunk_exact_phrase_hits(chunk_text, exact_phrases)

    selection_reason: list[str] = []
    if base_reason:
        selection_reason.append(base_reason)
    if dense_rank is not None:
        selection_reason.append("dense_hit")
    if sparse_rank is not None:
        selection_reason.append("bm25_hit")
    if anchor_score > 0:
        selection_reason.append("anchor_match")
    if entity_score > 0:
        selection_reason.append("entity_match")
    if exact_hits:
        selection_reason.append("quoted_phrase_match")

    final_score = float(
        base_rrf
        + (anchor_score * 3.0)
        + (entity_score * 4.0)
        + query_type_score
        + (2.5 * len(exact_hits))
    )

    return {
        "chunk_id": chunk_id,
        "document_id": int(document_id),
        "collection_name": collection_name,
        "document_title": document_title,
        "chunk_text": chunk_text,
        "text": chunk_text,
        "metadata": metadata,
        "dense_rank": dense_rank,
        "sparse_rank": sparse_rank,
        "rrf_score": base_rrf,
        "anchor_score": anchor_score,
        "entity_score": entity_score,
        "query_type_score": query_type_score,
        "final_score": final_score,
        "selection_reason": selection_reason,
        "retrieval_stage": retrieval_stage,
        "exact_phrase_hits": exact_hits,
    }


def _merge_canonical_chunks(existing: dict | None, incoming: dict) -> dict:
    if not existing:
        return dict(incoming)

    merged = dict(existing)
    merged["chunk_text"] = merged.get("chunk_text") or incoming.get("chunk_text") or ""
    merged["text"] = merged["chunk_text"]
    merged["metadata"] = merged.get("metadata") or incoming.get("metadata") or {}
    merged["dense_rank"] = min(
        [v for v in [merged.get("dense_rank"), incoming.get("dense_rank")] if isinstance(v, int)],
        default=None,
    )
    merged["sparse_rank"] = min(
        [v for v in [merged.get("sparse_rank"), incoming.get("sparse_rank")] if isinstance(v, int)],
        default=None,
    )
    for field in ("rrf_score", "anchor_score", "entity_score", "query_type_score", "final_score"):
        merged[field] = max(float(merged.get(field, 0.0) or 0.0), float(incoming.get(field, 0.0) or 0.0))

    reasons = []
    seen_reasons: set[str] = set()
    for value in list(merged.get("selection_reason") or []) + list(incoming.get("selection_reason") or []):
        if not isinstance(value, str):
            continue
        if value not in seen_reasons:
            seen_reasons.add(value)
            reasons.append(value)
    merged["selection_reason"] = reasons
    merged["exact_phrase_hits"] = sorted(
        {str(v) for v in (merged.get("exact_phrase_hits") or []) + (incoming.get("exact_phrase_hits") or []) if str(v).strip()}
    )
    return merged


def _score_canonical_chunk(
    chunk: dict,
    *,
    user_query: str,
    anchors: list[str] | None,
    entities: list[str] | None,
    answer_mode: str | None,
) -> dict:
    scored = dict(chunk)
    chunk_text = _get_chunk_text(scored)
    metadata = _get_chunk_metadata(scored)
    anchor_score = float(_text_matches_any_anchor(chunk_text, anchors=anchors, entities=None))
    entity_score = float(_text_matches_any_anchor(chunk_text, anchors=None, entities=entities))
    base_rrf = float(scored.get("rrf_score", 0.0) or 0.0)
    query_type_score = float(
        _score_chunk_with_anchors(
            {"chunk_text": chunk_text, "metadata": metadata, "rrf_score": base_rrf},
            user_query,
            anchors=[],
            entities=[],
            answer_mode=answer_mode,
        )
        - base_rrf
    )
    exact_hits = _chunk_exact_phrase_hits(chunk_text, scored.get("exact_phrase_hits"))
    final_score = float(
        float(scored.get("rrf_score", 0.0) or 0.0)
        + (anchor_score * 3.0)
        + (entity_score * 4.0)
        + query_type_score
        + (2.5 * len(exact_hits))
    )

    reasons = list(scored.get("selection_reason") or [])
    if anchor_score > 0 and "anchor_match" not in reasons:
        reasons.append("anchor_match")
    if entity_score > 0 and "entity_match" not in reasons:
        reasons.append("entity_match")
    if exact_hits and "quoted_phrase_match" not in reasons:
        reasons.append("quoted_phrase_match")

    scored.update(
        {
            "chunk_text": chunk_text,
            "text": chunk_text,
            "metadata": metadata,
            "anchor_score": anchor_score,
            "entity_score": entity_score,
            "query_type_score": query_type_score,
            "final_score": final_score,
            "selection_reason": reasons,
            "exact_phrase_hits": exact_hits,
        }
    )
    return scored


def _chunk_debug_summary(chunk: dict | None, *, include_text: bool = True) -> dict[str, Any]:
    if not isinstance(chunk, dict):
        return {}
    text = _format_chunk_for_prompt(_get_chunk_text(chunk), preserve_structure=False)
    summary: dict[str, Any] = {
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "document_id": chunk.get("document_id"),
        "document_title": str(chunk.get("document_title") or ""),
        "collection_name": str(chunk.get("collection_name") or ""),
        "chunk_index": _get_chunk_metadata(chunk).get("chunk_index"),
        "final_score": round(float(chunk.get("final_score", chunk.get("rrf_score", 0.0)) or 0.0), 4),
        "selection_reason": list(chunk.get("selection_reason") or []),
    }
    if include_text:
        summary["text_preview"] = (text or "")[:240]
    return summary


def _select_lossless_chunks_for_doc(
    *,
    candidates: list[dict],
    user_query: str,
    anchors: list[str] | None,
    entities: list[str] | None,
    answer_mode: str | None,
    per_doc_limit: int,
) -> tuple[list[dict], dict]:
    exact_phrases = _extract_exact_query_phrases(user_query)
    merged_by_id: dict[str, dict] = {}
    duplicates_merged = 0
    for raw in candidates:
        canonical = _score_canonical_chunk(
            raw,
            user_query=user_query,
            anchors=anchors,
            entities=entities,
            answer_mode=answer_mode,
        )
        chunk_id = str(canonical.get("chunk_id") or "")
        if chunk_id in merged_by_id:
            duplicates_merged += 1
        merged_by_id[chunk_id] = _merge_canonical_chunks(merged_by_id.get(chunk_id), canonical)

    merged_chunks = [
        _score_canonical_chunk(
            chunk,
            user_query=user_query,
            anchors=anchors,
            entities=entities,
            answer_mode=answer_mode,
        )
        for chunk in merged_by_id.values()
    ]
    merged_chunks.sort(key=lambda item: float(item.get("final_score", 0.0) or 0.0), reverse=True)

    must_keep_ids: set[str] = set()
    chunk_by_id = {str(chunk.get("chunk_id") or ""): chunk for chunk in merged_chunks}

    for chunk in merged_chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        if chunk.get("entity_score", 0.0) > 0:
            must_keep_ids.add(chunk_id)
        if _chunk_exact_phrase_hits(_get_chunk_text(chunk), exact_phrases):
            must_keep_ids.add(chunk_id)

    if merged_chunks:
        strongest_doc = merged_chunks[0]
        must_keep_ids.add(str(strongest_doc.get("chunk_id") or ""))
        strongest_doc.setdefault("selection_reason", []).append("strongest_for_doc")

    for entity in entities or []:
        entity_lower = (entity or "").strip().lower()
        if not entity_lower:
            continue
        best = None
        best_score = None
        for chunk in merged_chunks:
            if entity_lower not in _get_chunk_text(chunk).lower():
                continue
            score = float(chunk.get("final_score", 0.0) or 0.0)
            if best_score is None or score > best_score:
                best_score = score
                best = chunk
        if best:
            best.setdefault("selection_reason", []).append("strongest_for_entity")
            must_keep_ids.add(str(best.get("chunk_id") or ""))

    if answer_mode == "timeline":
        best_timeline = None
        best_timeline_score = None
        for chunk in merged_chunks:
            text = _get_chunk_text(chunk).lower()
            if not re.search(r"\b\d+(?:\.\d+)?\s*(?:-|to)?\s*\d*(?:\.\d+)?\s*(?:month|months|week|weeks|year|years)\b", text):
                continue
            score = float(chunk.get("final_score", 0.0) or 0.0)
            if best_timeline_score is None or score > best_timeline_score:
                best_timeline_score = score
                best_timeline = chunk
        if best_timeline:
            best_timeline.setdefault("selection_reason", []).append("timeline_numeric_match")
            must_keep_ids.add(str(best_timeline.get("chunk_id") or ""))

    if len(merged_chunks) > 1:
        rerank_candidates: list[dict[str, Any]] = []
        for chunk in merged_chunks:
            item = dict(chunk)
            item["rerank_text"] = _build_rerank_passage(item)
            rerank_candidates.append(item)
        merged_chunks = [
            {
                k: v
                for k, v in item.items()
                if k != "rerank_text"
            }
            for item in rerank_chunks_scored(
                query=user_query,
                chunks=rerank_candidates,
                text_key="rerank_text",
                top_k=None,
            )
        ]
        merged_chunks.sort(
            key=lambda item: float(item.get("rerank_score", item.get("final_score", 0.0)) or 0.0),
            reverse=True,
        )
        chunk_by_id = {str(chunk.get("chunk_id") or ""): chunk for chunk in merged_chunks}

    must_keep = [chunk_by_id[cid] for cid in must_keep_ids if cid in chunk_by_id]
    must_keep.sort(
        key=lambda item: float(item.get("rerank_score", item.get("final_score", 0.0)) or 0.0),
        reverse=True,
    )

    remaining = [chunk for chunk in merged_chunks if str(chunk.get("chunk_id") or "") not in must_keep_ids]
    temp_limit = max(int(per_doc_limit or 0), len(must_keep))
    selected = list(must_keep)
    for chunk in remaining:
        if len(selected) >= temp_limit:
            break
        selected.append(chunk)

    dropped = [chunk for chunk in remaining if str(chunk.get("chunk_id") or "") not in {str(s.get("chunk_id") or "") for s in selected}]

    for rank, chunk in enumerate(selected, start=1):
        chunk["rank"] = rank

    debug = {
        "candidates_collected": len(candidates),
        "duplicates_merged": duplicates_merged,
        "deduped_candidates": len(merged_chunks),
        "must_keep_count": len(must_keep),
        "scored_count": len(remaining),
        "chunks_removed_by_budget": max(0, len(merged_chunks) - len(selected)),
        "final_chunks_sent": len(selected),
        "temporary_cap": temp_limit,
        "local_rerank_applied": len(merged_chunks) > 1,
        "local_top_scores": [
            round(float(chunk.get("rerank_score", chunk.get("final_score", 0.0)) or 0.0), 4)
            for chunk in merged_chunks[:5]
        ],
        "top_dropped_chunk_ids": [str(chunk.get("chunk_id") or "") for chunk in dropped[:5]],
        "top_dropped_chunks": [_chunk_debug_summary(chunk) for chunk in dropped[:5]],
        "selected_chunk_ids": [str(chunk.get("chunk_id") or "") for chunk in selected],
        "selected_chunks": [_chunk_debug_summary(chunk) for chunk in selected[:8]],
        "exact_phrases": exact_phrases,
    }
    return selected, debug


def _merge_unique_str_lists(*lists: list[str] | None, limit: int | None = None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for values in lists:
        for value in values or []:
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if not stripped:
                continue
            _append_unique_str(merged, seen, stripped)
            if limit is not None and len(merged) >= limit:
                return merged[:limit]
    return merged[:limit] if limit is not None else merged


def _coerce_planner_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, (list, tuple, set)):
        cleaned: list[str] = []
        for item in value:
            if item is None:
                continue
            stripped = str(item).strip()
            if stripped:
                cleaned.append(stripped)
        return cleaned
    stripped = str(value).strip()
    return [stripped] if stripped else []


def _extract_question_focus_from_query(user_query: str) -> list[str]:
    q = (user_query or "").lower()
    focus: list[str] = []
    seen: set[str] = set()
    focus_rules = [
        ("timeline", ["timeline", "timelines", "schedule", "scheduling", "phase", "phases", "milestone", "milestones"]),
        ("duration", ["duration", "durations", "month", "months", "week", "weeks", "year", "years", "time required"]),
        ("solution", ["solution", "solutions", "proposal", "proposed", "deliverable", "deliverables", "mvp", "build"]),
        ("summary", ["summary", "summarize", "overview", "describe", "tell me about", "what is", "details"]),
        ("process", ["process", "workflow", "steps", "procedure", "how it works", "how does"]),
        ("responsibilities", ["responsibility", "responsibilities", "owner", "ownership", "accountable", "responsible", "raci", "role", "roles"]),
        ("technology", ["technology", "tech stack", "stack", "architecture", "framework", "frameworks", "system", "platform", "api", "apis"]),
        ("metrics", ["metric", "metrics", "kpi", "kpis", "result", "results", "outcome", "outcomes", "roi"]),
        ("problem", ["problem", "problems", "challenge", "challenges", "issue", "issues", "gap", "gaps", "pain point", "pain points"]),
        ("comparison", ["compare", "comparison", "vs", "versus", "difference", "both", "higher", "lower", "more", "less"]),
        ("pricing", ["pricing", "price", "prices", "cost", "costs", "budget", "rate", "rates", "quote", "estimate"]),
    ]
    for label, markers in focus_rules:
        if any(marker in q for marker in markers):
            _append_unique_str(focus, seen, label)
    return focus[:8]


def _build_retrieval_queries_from_focus(
    base_query: str,
    *,
    named_entities: list[str] | None,
    anchor_terms: list[str] | None,
    question_focus: list[str] | None,
    hints: dict | None,
) -> tuple[list[str], list[str], list[str]]:
    doc_queries: list[str] = []
    dense_queries: list[str] = []
    sparse_queries: list[str] = []
    seen_doc: set[str] = set()
    seen_dense: set[str] = set()
    seen_sparse: set[str] = set()

    def _add_query(target: list[str], seen: set[str], value: str) -> None:
        if not value or not value.strip():
            return
        _append_unique_str(target, seen, value.strip())

    focus_terms_map = {
        "timeline": ["timeline", "schedule", "phases"],
        "duration": ["duration", "months", "time required"],
        "solution": ["solution", "proposal", "deliverables"],
        "summary": ["summary", "overview"],
        "process": ["process", "workflow", "steps"],
        "responsibilities": ["responsibilities", "owner", "accountable"],
        "technology": ["technology", "architecture", "stack"],
        "metrics": ["metrics", "outcomes", "results"],
        "problem": ["problem", "challenge", "gap"],
        "comparison": ["compare", "difference", "both"],
        "pricing": ["pricing", "cost", "budget"],
    }

    _add_query(doc_queries, seen_doc, base_query)
    _add_query(dense_queries, seen_dense, base_query)
    _add_query(sparse_queries, seen_sparse, _keyword_expand(base_query, hints))

    focus_terms = _merge_unique_str_lists(*[focus_terms_map.get(focus, []) for focus in (question_focus or [])], limit=6)
    focus_tail = " ".join(focus_terms).strip()

    for entity in (named_entities or [])[:4]:
        focused = f"{entity} {focus_tail}".strip() if focus_tail else str(entity).strip()
        _add_query(doc_queries, seen_doc, focused)
        _add_query(dense_queries, seen_dense, focused)
        _add_query(sparse_queries, seen_sparse, _keyword_expand(focused, hints))

    for anchor in (anchor_terms or [])[:4]:
        focused_anchor = f"{anchor} {focus_tail}".strip() if focus_tail else str(anchor).strip()
        _add_query(doc_queries, seen_doc, focused_anchor)
        _add_query(dense_queries, seen_dense, focused_anchor)
        _add_query(sparse_queries, seen_sparse, _keyword_expand(focused_anchor, hints))

    distinct_entities = [e for e in dict.fromkeys(named_entities or []) if isinstance(e, str) and e.strip()]
    if len(distinct_entities) >= 2 and focus_tail:
        combo = f"{' '.join(distinct_entities[:3])} {focus_tail}".strip()
        _add_query(doc_queries, seen_doc, combo)
        _add_query(dense_queries, seen_dense, combo)
        _add_query(sparse_queries, seen_sparse, _keyword_expand(combo, hints))

    return doc_queries[:6], dense_queries[:6], sparse_queries[:6]


def _normalize_planned_answer_mode(
    candidate_mode: str | None,
    *,
    user_query: str,
    named_entities: list[str] | None,
    question_focus: list[str] | None,
    fallback_mode: str,
) -> str:
    allowed = {"direct", "aggregate", "compare", "timeline", "partial_ok"}
    candidate = str(candidate_mode or "").strip().lower()
    fallback = str(fallback_mode or "aggregate").strip().lower()
    focus = {str(item).strip().lower() for item in (question_focus or []) if str(item).strip()}
    entity_count = len([e for e in (named_entities or []) if isinstance(e, str) and e.strip()])

    if candidate == "partial_ok":
        return "partial_ok"
    if {"timeline", "duration"} & focus:
        return "timeline"
    if "comparison" in focus and entity_count >= 2:
        return "compare"
    if candidate in allowed:
        return candidate
    if fallback in allowed:
        return fallback
    if _should_aggregate_multi_doc(user_query):
        return "aggregate"
    return "direct"


def _summarize_chunk_coverage(
    chunks_data: dict | None,
    anchors: list[str] | None = None,
    entities: list[str] | None = None,
) -> dict[str, Any]:
    coverage = {
        "anchor_hit_chunks": 0,
        "entity_hits": {},
        "top_chunk_anchor_hits": 0,
    }
    top_checked = 0
    for _col, docs in (chunks_data or {}).items():
        if not isinstance(docs, list):
            continue
        for d in docs:
            if not isinstance(d, dict):
                continue
            for ch in d.get("chunks") or []:
                text = _get_chunk_text(ch if isinstance(ch, dict) else str(ch))
                if not isinstance(text, str):
                    continue
                anchor_hits = _text_matches_any_anchor(text, anchors=anchors, entities=entities)
                if anchor_hits:
                    coverage["anchor_hit_chunks"] += 1
                if top_checked < 3:
                    coverage["top_chunk_anchor_hits"] += anchor_hits
                    top_checked += 1
                for entity in entities or []:
                    if entity not in coverage["entity_hits"]:
                        coverage["entity_hits"][entity] = 0
                    if entity.lower() in text.lower():
                        coverage["entity_hits"][entity] += 1
    return coverage


def _should_retry_retrieval(
    query: str,
    chunks_data: dict | None,
    anchors: list[str] | None = None,
    entities: list[str] | None = None,
) -> bool:
    confidence = _compute_retrieval_confidence(chunks_data or {})
    coverage = _summarize_chunk_coverage(chunks_data, anchors=anchors, entities=entities)
    if coverage["anchor_hit_chunks"] == 0:
        return True
    if len([e for e in (entities or []) if e.strip()]) >= 2:
        entity_hits = coverage["entity_hits"]
        supported = sum(1 for e in entities or [] if entity_hits.get(e, 0) > 0)
        if supported < len(set(entities or [])):
            return True
    if confidence.get("top3_avg_rrf", 0.0) < 0.015 and coverage["top_chunk_anchor_hits"] == 0:
        return True
    return False


def select_retrieval_scope(
    user_query: str,
    available_collections: list[str],
    hints: dict | None = None,
    refined_query: str | None = None,
    collection_descriptors: dict[str, dict] | None = None,
) -> dict[str, Any]:
    semantic_query = _normalize_rag_query(refined_query or user_query)
    anchors = _extract_anchor_terms_from_query(semantic_query, hints)
    named_entities = _extract_named_entities_from_query(semantic_query, hints)
    question_focus = _extract_question_focus_from_query(semantic_query)
    query_type = detect_query_type(semantic_query)
    preferred_max = 3 if _should_aggregate_multi_doc(semantic_query) or len(named_entities) >= 2 else 1

    payload = {
        "collections": [],
        "reason": "default",
        "is_multi_collection": preferred_max > 1,
        "named_entities": named_entities,
        "anchor_terms": anchors,
        "question_focus": question_focus,
    }

    if not available_collections:
        return payload

    q_lower = semantic_query.lower()
    heuristic_collections: list[str] = []
    if any(term in q_lower for term in ["jagged frontier", "test design session", "e2e test automation", "raci"]):
        heuristic_collections = _normalize_collection_list(["cross_team_decks"])
    elif len(named_entities) >= 2 and any(t in q_lower for t in ["timeline", "timelines", "duration", "month", "months", "phase"]):
        heuristic_collections = _normalize_collection_list(["customer_pitch_decks"])
    elif any(term in q_lower for term in ["observability", "performance tracking", "prometheus", "grafana", "datadog", "elk"]):
        heuristic_collections = _normalize_collection_list(["customer_pitch_decks"])

    if heuristic_collections:
        payload["collections"] = heuristic_collections[:preferred_max]
        payload["reason"] = "heuristic_anchor"
        payload["is_multi_collection"] = len(payload["collections"]) > 1
        return payload

    collection_metadata_payload = {
        "collections": [
            {
                "collection_name": c,
                "metadata": json.dumps(collection_descriptors[c], ensure_ascii=False)
                            if (collection_descriptors and c in collection_descriptors)
                            else "",
            }
            for c in available_collections
        ]
    }

    if not payload["collections"]:
        ranked_collections, metadata_debug = _score_collections_from_metadata(
            semantic_query,
            collection_metadata_payload,
        )
        payload["collection_ranking"] = metadata_debug.get("top_scores", [])
        positive = [item for item in ranked_collections if float(item.get("score", 0.0) or 0.0) > 0.0]
        if positive:
            if preferred_max > 1:
                top_score = float(positive[0].get("score", 0.0) or 0.0)
                threshold = max(1.0, top_score * 0.45)
                payload["collections"] = [
                    str(item.get("collection") or "")
                    for item in positive
                    if str(item.get("collection") or "").strip() and float(item.get("score", 0.0) or 0.0) >= threshold
                ][:preferred_max]
            else:
                payload["collections"] = [str(positive[0].get("collection") or "")]
            payload["reason"] = "metadata_ranked"

    if not payload["collections"] and available_collections:
        payload["collections"] = [available_collections[0]]
        payload["reason"] = "first_available_fallback"

    payload["collections"] = _normalize_collection_list(payload["collections"])[:preferred_max] or available_collections[:1]
    payload["anchor_terms"] = _extract_anchor_terms_from_query(" ".join(payload["anchor_terms"]) or semantic_query, hints) or anchors
    payload["named_entities"] = payload["named_entities"] or named_entities
    payload["is_multi_collection"] = bool(payload["is_multi_collection"]) or len(payload["collections"]) > 1
    return payload


def _default_retrieval_plan(
    user_query: str,
    available_collections: list[str],
    hints: dict | None = None,
    router_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    semantic_query = _normalize_rag_query(user_query)
    router_scope = router_scope or {}
    collections = _normalize_collection_list(router_scope.get("collections") or [])
    if not collections:
        if (hints or {}).get("doc_type") == "case_study" and "case_studies" in available_collections:
            collections = ["case_studies"]
        elif available_collections:
            collections = [available_collections[0]]

    named_entities = router_scope.get("named_entities") or _extract_named_entities_from_query(semantic_query, hints)
    anchor_terms = router_scope.get("anchor_terms") or _extract_anchor_terms_from_query(semantic_query, hints)
    question_focus = router_scope.get("question_focus") or _extract_question_focus_from_query(semantic_query)
    answer_mode = "aggregate"
    q_lower = semantic_query.lower()
    if any(t in q_lower for t in ["partial", "partially", "if available", "if mentioned"]):
        answer_mode = "partial_ok"
    elif any(t in q_lower for t in ["pricing", "price", "cost", "budget", "estimate", "estimated"]):
        answer_mode = "partial_ok"
    answer_mode = _normalize_planned_answer_mode(
        None,
        user_query=semantic_query,
        named_entities=named_entities,
        question_focus=question_focus,
        fallback_mode=answer_mode if answer_mode != "aggregate" else ("aggregate" if _should_aggregate_multi_doc(semantic_query) else "direct"),
    )

    max_collections = 3
    if answer_mode == "direct":
        max_collections = 1
    elif answer_mode in {"compare", "timeline", "aggregate"}:
        max_collections = 3

    doc_queries, chunk_dense_queries, chunk_sparse_queries = _build_retrieval_queries_from_focus(
        semantic_query,
        named_entities=named_entities,
        anchor_terms=anchor_terms,
        question_focus=question_focus,
        hints=hints,
    )

    return {
        "collections": collections[:max_collections],
        "doc_queries": doc_queries,
        "chunk_dense_queries": chunk_dense_queries,
        "chunk_sparse_queries": chunk_sparse_queries,
        "named_entities": named_entities[:8],
        "anchor_terms": anchor_terms[:10],
        "question_focus": question_focus[:8],
        "answer_mode": answer_mode,
        "retry_policy": {
            "retry_on_missing_entity": False,
            "retry_on_no_anchor": False,
            "retry_on_low_confidence": False,
        },
        "_planner_model": "fallback",
        "_planner_tokens": 0,
    }


PLANNER_COLLECTION_SELECTION_PROMPT_INJECTION = """You are a high-precision retrieval planner for a RAG system.

Your task is to select the most relevant collection(s) for a user query using ONLY the provided collection metadata.

---

CORE TASK

1. Identify the exact user intent (what information is being asked).
2. Compare this intent against each collection.
3. Select the collection(s) whose purpose BEST matches the query.

---

PRIORITY SIGNALS (use in this order)

1. example_questions -> strongest indicator of intent match
2. routing_hint -> when the collection should be used
3. summary + primary_topics -> overall purpose
4. key_entities -> only if explicitly mentioned or clearly implied
5. routing_keywords -> weakest signal (do not rely on this alone)

---

DECISION RULES

- Match intent, not just words
- Prefer collections that could directly answer the query
- Do NOT select based on loose keyword overlap
- Use key_entities only when clearly relevant to the query
- Select up to 2 collections only if both are strongly relevant

---

STRICT RULES

- Use ONLY exact collection names provided
- Do NOT invent or modify names
- Avoid weak or broad matches

---

OUTPUT (STRICT JSON)

{
  "selected_collections": ["collection_name"],
  "confidence": 0.0,
  "query_intent": "precise intent",
  "reason": "why this collection best matches the intent",
  "alternative_collections": []
}

Return ONLY valid JSON."""



def unified_planning_exec(
    query: str,
    available_collections: list[str],
    collection_descriptors: dict[str, dict],
    hints: dict | None = None,
) -> dict:
    """Unified Planning: Merges query rewriting, anchor extraction, and retrieval strategy into one LLM trip."""
    prompt = f"""
You are the Strategic Retrieval Planner for ASK MOJO. 
Your goal is to analyze the user's query and provide a comprehensive execution plan for the RAG pipeline.

USER QUERY: {query}
AVAILABLE COLLECTIONS: {available_collections}

CONTEXTUAL HINTS:
{json.dumps(hints, indent=2) if hints else "None"}

COLLECTION SUMMARIES:
{json.dumps(collection_descriptors, indent=2)}

TASK:
1. REWRITE: Create a standalone, search-optimized version of the user's query (include key context, resolve coreferences).
2. ANCHORS: Extract exact phrase anchors (e.g., "Project Delta") or technical terms to be used for exact matching.
3. STRATEGY: 
   - Identify the primary collections to search.
   - Determine the ANSWER_MODE: "direct" (single doc/fact), "aggregate" (multi-doc summary), "compare", or "timeline".
   - Suggest 2-3 specific sub-queries (doc_queries) to find the best documents.

OUTPUT FORMAT:
Provide a JSON object with:
{{
  "rewritten_query": "Rephrased query",
  "anchors": ["List", "of", "anchors"],
  "collections": ["List", "of", "collections"],
  "answer_mode": "direct|aggregate|compare|timeline",
  "doc_queries": ["Query 1", "Query 2"],
  "named_entities": ["Entity 1", "Entity 2"],
  "question_focus": ["Focus Area 1"],
  "retry_policy": {{
      "retry_on_no_anchor": true,
      "retry_on_missing_entity": false,
      "retry_on_low_confidence": true
  }}
}}
"""
    try:
        completion = client.chat.completions.create(
            model=os.getenv("ASKMOJO_PLANNER_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        result = json.loads(completion.choices[0].message.content)
        result["_planner_model"] = completion.model
        result["_planner_tokens"] = completion.usage.total_tokens
        return result
    except Exception as e:
        logger.error("[Unified Planner] Failed: %s", e)
        return {
            "rewritten_query": query,
            "anchors": [],
            "collections": available_collections[:1],
            "answer_mode": "direct",
            "doc_queries": [query],
            "named_entities": [],
            "question_focus": ["General"],
            "retry_policy": {"retry_on_no_anchor": True, "retry_on_low_confidence": True}
        }


def plan_retrieval_strategy(
    user_query: str,
    hints: dict | None,
    available_collections: list[str],
    router_scope: dict[str, Any] | None = None,
    collection_descriptors: dict[str, dict] | None = None,
) -> dict[str, Any]:
    default_plan = _default_retrieval_plan(user_query, available_collections, hints, router_scope)
    preferred_max = 3 if default_plan["answer_mode"] in {"aggregate", "compare", "timeline"} else 1

    hints_payload, _h_json, _h_toon, _h_save = _maybe_toon_payload(
        _sanitize_filters(hints) or {}, call_name="planner", data_name="hints"
    )
    _cols_for_planner = [
        collection_descriptors[c] if (collection_descriptors and c in collection_descriptors)
        else {"collection_name": c}
        for c in available_collections
    ]
    cols_payload, _c_json, _c_toon, _c_save = _maybe_toon_payload(
        _cols_for_planner, call_name="planner", data_name="available_collections"
    )
    scope_payload, _s_json, _s_toon, _s_save = _maybe_toon_payload(
        router_scope or {}, call_name="planner", data_name="router_scope"
    )
    ent_payload, _e_json, _e_toon, _e_save = _maybe_toon_payload(
        default_plan["named_entities"], call_name="planner", data_name="default_named_entities"
    )
    anch_payload, _a_json, _a_toon, _a_save = _maybe_toon_payload(
        default_plan["anchor_terms"], call_name="planner", data_name="default_anchor_terms"
    )
    focus_payload, _f_json, _f_toon, _f_save = _maybe_toon_payload(
        default_plan.get("question_focus") or [], call_name="planner", data_name="default_question_focus"
    )
    planner_injection_payload, _pi_json, _pi_toon, _pi_save = _maybe_toon_payload(
        {"system_prompt": PLANNER_COLLECTION_SELECTION_PROMPT_INJECTION},
        call_name="planner",
        data_name="collection_selection_prompt_injection",
    )
    prompt = f"""
User query: {user_query}
Query type: {detect_query_type(user_query)}
Hints: {hints_payload}
Available collections (with routing metadata): {cols_payload}
Heuristic router scope: {scope_payload}
Default named entities: {ent_payload}
Default anchor terms: {anch_payload}
Default question focus: {focus_payload}

Collection-selection prompt injection for choosing `collections`:
{planner_injection_payload}

Plan retrieval for this query.

Rules:
- Return JSON only.
- Apply the collection-selection prompt injection above when deciding `collections`, but still return this pipeline's full planner JSON schema.
- Map the injected prompt's `selected_collections` concept to the planner field `collections`.
- `collections` must be a JSON array of collection name strings only, for example: ["customer_pitch_decks"].
- Do not return collection objects, descriptors, or nested structures inside `collections`.
- Use `routing_hint`, `routing_keywords`, `key_entities`, and `example_questions` from each collection descriptor to match the query intent.
- Collection choice must be driven primarily by descriptor support, not by guesswork.
- If a named entity in the query matches a collection's `key_entities`, strongly prefer that collection.
- If the query semantically matches an `example_questions` entry, use that collection.
- Prefer 1 collection for narrow factual questions.
- Use up to {preferred_max} collections ONLY for genuine multi-entity compare, timeline, or aggregate queries.
- Preserve quoted phrases, named entities, and unusual domain phrases.
- Never drop an explicit named entity from the default named entity list unless it is clearly not part of the user query.
- For multi-entity questions, keep all requested entities in `named_entities`.
- Add `question_focus` as a JSON array chosen from: timeline, duration, solution, summary, process, responsibilities, technology, metrics, problem, comparison, pricing.
- If the user asks about timeline, duration, schedule, phases, milestones, or time required, `question_focus` must include timeline or duration, and `answer_mode` should usually be `timeline`.
- `doc_queries` and `chunk_dense_queries` should be short retrieval-oriented search strings, not one long repetitive sentence.
- `answer_mode` must be one of: direct, aggregate, compare, timeline, partial_ok.
- `retry_policy` keys must be: retry_on_missing_entity, retry_on_no_anchor, retry_on_low_confidence.
"""
    try:
        response = _openai_chat_create_with_timeout(
            model="gpt-4o-mini",
            temperature=0.0,
            messages=[
                {"role": "system", "content": "You are a precise retrieval planner. Respond only with valid JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        json_str = _extract_json_object(raw) or raw
        data = json.loads(json_str)

        plan = dict(default_plan)
        coerced_collections = _normalize_collection_list(data.get("collections") or plan["collections"])
        if data.get("collections") and not coerced_collections:
            logger.warning("[Planner] Ignoring invalid collections payload: %r", data.get("collections"))
        plan["collections"] = coerced_collections[:preferred_max] or plan["collections"]
        if (router_scope or {}).get("reason") == "heuristic_anchor" and (router_scope or {}).get("collections"):
            plan["collections"] = _normalize_collection_list((router_scope or {}).get("collections"))[:preferred_max] or plan["collections"]
        for key, limit in (
            ("doc_queries", 6),
            ("chunk_dense_queries", 6),
            ("chunk_sparse_queries", 6),
            ("named_entities", 8),
            ("anchor_terms", 12),
        ):
            values = _coerce_planner_str_list(data.get(key))
            plan[key] = _merge_unique_str_lists(values, plan.get(key) or [], limit=limit)
        planner_focus = [v.lower() for v in _coerce_planner_str_list(data.get("question_focus"))]
        plan["question_focus"] = _merge_unique_str_lists(
            planner_focus,
            default_plan.get("question_focus") or [],
            limit=8,
        )
        plan["answer_mode"] = _normalize_planned_answer_mode(
            data.get("answer_mode"),
            user_query=user_query,
            named_entities=plan.get("named_entities") or [],
            question_focus=plan.get("question_focus") or [],
            fallback_mode=plan.get("answer_mode") or default_plan.get("answer_mode") or "aggregate",
        )
        retry_policy = data.get("retry_policy") or {}
        if isinstance(retry_policy, dict):
            plan["retry_policy"] = {
                "retry_on_missing_entity": bool(retry_policy.get("retry_on_missing_entity")),
                "retry_on_no_anchor": bool(retry_policy.get("retry_on_no_anchor")),
                "retry_on_low_confidence": bool(retry_policy.get("retry_on_low_confidence")),
            }
        plan["_planner_model"] = response.model
        plan["_planner_tokens"] = response.usage.total_tokens if response.usage else 0
        return plan
    except Exception as e:
        logger.warning("[Planner] Failed, using default retrieval plan: %s", e)
        return default_plan


def _resolve_existing_collection_name(name: str | None) -> str | None:
    candidate = (name or "").strip()
    if not candidate:
        return None

    try:
        live_names = [col.name for col in chroma_client.list_collections()]
    except Exception:
        return candidate

    if candidate in live_names:
        return candidate

    target = _normalize_collection_identity(candidate)
    for live_name in live_names:
        if _normalize_collection_identity(live_name) == target:
            return live_name

    return candidate


def _resolve_document_collection_name(doc: Any, db: Session) -> str | None:
    if getattr(doc, "category_id", None):
        cat = db.query(Category).filter(Category.id == doc.category_id).first()
        if cat and cat.collection_name:
            return _resolve_existing_collection_name(cat.collection_name)

    category_name = getattr(doc, "category", None)
    if category_name:
        normalized = _normalize_collection_candidate(category_name)
        categories = db.query(Category).all()
        for cat in categories:
            if not cat or not cat.collection_name:
                continue
            if _normalize_collection_candidate(cat.name) == normalized:
                return _resolve_existing_collection_name(cat.collection_name)
            if _normalize_collection_candidate(cat.collection_name) == normalized:
                return _resolve_existing_collection_name(cat.collection_name)

        return _resolve_existing_collection_name(category_name.lower().replace(" ", "_"))

    return None


def _split_filters(filters: dict | None) -> tuple[dict | None, dict | None]:
    """Return (hard_filters, soft_filters).

    Hard filters are used in vector store `where` clauses (can cause false negatives if too strict).
    Soft filters are only used for query rewriting/expansion.
    """
    f = _sanitize_filters(filters)
    if not f:
        return None, None

    hard_keys = {"doc_type"}
    hard = {k: v for k, v in f.items() if k in hard_keys}
    soft = {k: v for k, v in f.items() if k not in hard_keys}
    return (hard or None), (soft or None)


def _rewrite_query_for_retrieval(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return q
    try:
        resp = _openai_chat_create_with_timeout(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "Expand the query for better retrieval. Add synonyms and closely related technical terms. Output ONLY the rewritten query text.",
                },
                {"role": "user", "content": f"Query: {q}"},
            ],
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or q
    except Exception as e:
        logger.warning("[Rewrite] retrieval rewrite failed: %s", e)
        return q


def _extract_anchors_llm(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        resp = _openai_chat_create_with_timeout(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "Extract important keywords/anchor terms from the query. Respond with a comma-separated list only.",
                },
                {"role": "user", "content": f"Query: {q}"},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        seen: set[str] = set()
        out: list[str] = []
        for p in parts:
            _append_unique_str(out, seen, p)
        return out[:12]
    except Exception as e:
        logger.warning("[Anchors] LLM anchor extraction failed: %s", e)
        return []


def _rewrite_and_extract_anchors(query: str) -> tuple[str, list[str]]:
    """Merged LLM call: rewrite query for retrieval AND extract anchor terms.

    Saves one round-trip + ~40% tokens compared to calling
    _rewrite_query_for_retrieval and _extract_anchors_llm separately.
    """
    q = (query or "").strip()
    if not q:
        return q, []
    try:
        resp = _openai_chat_create_with_timeout(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You perform two tasks on the user query:\n"
                        "1) Expand the query for better retrieval — add synonyms and closely related technical terms.\n"
                        "2) Extract the most important keywords/anchor terms from the query.\n"
                        "Return JSON only: {\"rewritten_query\": \"...\", \"anchors\": [\"term1\", \"term2\", ...]}"
                    ),
                },
                {"role": "user", "content": f"Query: {q}"},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        json_str = _extract_json_object(raw) or raw
        data = json.loads(json_str)
        rewritten = (data.get("rewritten_query") or "").strip() or q
        anchors_raw = data.get("anchors") or []
        seen: set[str] = set()
        anchors: list[str] = []
        for a in anchors_raw:
            _append_unique_str(anchors, seen, str(a).strip())
        return rewritten, anchors[:12]
    except Exception as e:
        logger.warning("[RewriteAnchors] Merged call failed, falling back: %s", e)
        return q, []


def _to_chroma_where(hard_filters: dict | None) -> dict | None:
    if not hard_filters:
        return None

    def _norm_doc_type(v: str) -> str:
        s = (v or "").strip().lower().replace("-", "_").replace(" ", "_")
        if s in {"case", "case-study", "case_studies", "case_study"}:
            return "case_study"
        if s in {"proposal", "proposals"}:
            return "proposal"
        if s in {"solution", "solutions", "service", "services"}:
            return "solution"
        return s or v

    def _norm_domain(v: str) -> str:
        return (v or "").strip()

    normed: dict[str, object] = {}
    for k, v in hard_filters.items():
        if v in (None, ""):
            continue
        if k == "doc_type" and isinstance(v, str):
            normed[k] = _norm_doc_type(v)
        elif k == "domain" and isinstance(v, str):
            normed[k] = _norm_domain(v)
        else:
            normed[k] = v

    items = [(k, v) for k, v in normed.items() if v not in (None, "")]
    if not items:
        return None
    if len(items) == 1:
        k, v = items[0]
        return {k: v}
    return {"$and": [{k: v} for (k, v) in items]}


def _word_count(s: str) -> int:
    return len([t for t in (s or "").strip().split() if t])


def _select_query_variants(query_pack: dict[str, str], query: str, stage: str) -> list[str]:
    """Gating to avoid over-querying.

    - Short queries: prefer fewer variants.
    - Long/complex queries: allow clarified variant.
    """
    wc = _word_count(query)
    query_type = detect_query_type(query)
    if stage == "doc_dense":
        if query_type in {"solution", "technology", "metrics", "summary"}:
            return [query_pack["q0"], query_pack["q1"], query_pack["q2"]]
        if wc < 5:
            return [query_pack["q0"]]
        return [query_pack["q0"], query_pack["q1"]]
    if stage == "chunk_dense":
        if query_type in {"solution", "technology", "metrics", "summary"}:
            return [query_pack["q0"], query_pack["q1"], query_pack["q2"]]
        if wc < 5:
            return [query_pack["q0"]]
        return [query_pack["q0"], query_pack["q1"]]
    if stage == "chunk_sparse":
        # BM25 should stay lexical-heavy.
        if query_type in {"problem", "solution", "technology", "metrics", "summary"}:
            return [query_pack["q2"], query_pack["q0"]]
        return [query_pack["q2"], query_pack["q0"]] if wc < 5 else [query_pack["q2"]]
    return [query_pack["q0"]]


def _rewrite_clarified(query: str, hints: dict | None) -> str:
    cache_key = f"clarify::{query}::{json.dumps(_sanitize_filters(hints), sort_keys=True)}"
    cached = _rewrite_cache.get(cache_key)
    if isinstance(cached, str):
        return cached

    hint_text = json.dumps(_sanitize_filters(hints) or {}, ensure_ascii=False)
    try:
        resp = _openai_chat_create_with_timeout(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "Rewrite the query for retrieval. Preserve all entities. Add missing clarifications only if strongly implied. Output only the rewritten query text.",
                },
                {"role": "user", "content": f"Query: {query}\nHints: {hint_text}"},
            ],
        )
        out = (resp.choices[0].message.content or "").strip() or query
    except Exception as e:
        logger.warning("[Rewrite] clarify failed, using q0: %s", e)
        out = query
    _rewrite_cache.set(cache_key, out)
    return out


def _keyword_expand(query: str, hints: dict | None) -> str:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-./]+", query.lower())
    keep = [t for t in tokens if t not in _STOPWORDS and len(t) >= 3]

    # Deterministic synonym expansion for common short queries.
    # This boosts BM25 recall without extra LLM calls.
    synonyms: dict[str, list[str]] = {
        "steps": ["phases", "workflow", "process", "stage", "stages"],
        "phase": ["phases", "steps", "timeline"],
        "phases": ["steps", "workflow", "process"],
        "workflow": ["steps", "phases", "process"],
        "sprint": ["iteration", "iterations", "agile"],
        "model": ["framework", "approach"],
        "observability": ["monitoring", "logging", "tracing", "apm", "metrics"],
        "performance": ["benchmark", "latency", "throughput"],
        "tracking": ["monitoring", "measurement", "metrics"],
        "frameworks": ["framework", "libraries", "library", "ecosystem"],
        "framework": ["frameworks", "libraries", "library"],
        "typescript": ["ts", "javascript", "js"],
        "budget": ["cost", "pricing", "price"],
        "pricing": ["cost", "budget", "rates"],
        "cost": ["pricing", "budget", "price"],
    }
    base_tokens = list(keep)
    for t in base_tokens:
        for s in synonyms.get(t, []):
            if s and s not in keep:
                keep.append(s)
    f = _sanitize_filters(hints) or {}
    for v in f.values():
        if isinstance(v, str) and v.strip():
            keep.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-./]+", v.lower()))

    query_type = detect_query_type(query)
    for term in _QUERY_TYPE_EXPANSIONS.get(query_type, []):
            keep.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-./]+", term.lower()))

    seen = set()
    uniq = []
    for t in keep:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return " ".join(uniq[:32]) if uniq else query


def _hyde(query: str, hints: dict | None) -> str:
    cache_key = f"hyde::{query}::{json.dumps(_sanitize_filters(hints), sort_keys=True)}"
    cached = _rewrite_cache.get(cache_key)
    if isinstance(cached, str):
        return cached

    hint_text = json.dumps(_sanitize_filters(hints) or {}, ensure_ascii=False)
    try:
        resp = _openai_chat_create_with_timeout(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "Write a short pseudo-answer (5-8 bullet lines) that would appear in an internal presales document. Do not invent vendor-specific facts. Keep it general and retrieval-friendly.",
                },
                {"role": "user", "content": f"Query: {query}\nHints: {hint_text}"},
            ],
        )
        out = (resp.choices[0].message.content or "").strip() or query
    except Exception as e:
        logger.warning("[Rewrite] hyde failed, using q0: %s", e)
        out = query
    _rewrite_cache.set(cache_key, out)
    return out


def generate_queries(query: str, hints: dict | None = None) -> dict[str, str]:
    return {
        "q0": query,
        "q1": _rewrite_clarified(query, hints),
        "q2": _keyword_expand(query, hints),
        "q3": "",
    }


def _ensure_hyde_query(query_pack: dict[str, str] | None, query: str, hints: dict | None = None) -> str:
    if not isinstance(query_pack, dict):
        return _hyde(query, hints)
    q3 = str(query_pack.get("q3") or "").strip()
    if q3:
        return q3
    q3 = _hyde(query, hints)
    query_pack["q3"] = q3
    return q3


def refine_query(user_query: str, hints: dict | None = None) -> dict:
    """Mandatory first step for the agent: refine query and produce a single query pack.

    This is the only place where query rewriting/expansion/HyDE is allowed.
    Tools must be dumb executors and must not call LLMs or rewrite queries.
    """
    soft = _sanitize_filters(hints) or {}
    query_pack = generate_queries(user_query, soft)

    # The agent can choose to use only some variants, but we provide a sane default
    # set of queries to run at each stage.
    doc_queries = _select_query_variants(query_pack, user_query, stage="doc_dense")
    chunk_dense_queries = _select_query_variants(query_pack, user_query, stage="chunk_dense")
    chunk_sparse_queries = _select_query_variants(query_pack, user_query, stage="chunk_sparse")

    return {
        "refined_query": query_pack.get("q1") or user_query,
        "query_pack": query_pack,
        "doc_queries": doc_queries,
        "chunk_dense_queries": chunk_dense_queries,
        "chunk_sparse_queries": chunk_sparse_queries,
    }


def _extract_json_object(text: str) -> str | None:
    if not text:
        return None
    s = _strip_markdown_code_fence(text)

    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return s[start : end + 1]


def decompose_query(query: str) -> dict:
    """Break complex queries into 2-4 focused subqueries, otherwise keep original."""
    q = (query or "").strip()
    if not q:
        return {"is_complex": False, "subqueries": [query]}

    # Cheap heuristic to avoid an extra LLM call on obviously simple queries.
    # (Keeps pipeline linear + controlled, and reduces latency/cost.)
    wc = len([t for t in q.split() if t])
    if wc <= 8 and not re.search(r"\b(and|compare|vs\.?|versus|difference|differences|what\s+and\s+how|how\s+and\s+what)\b", q, re.IGNORECASE):
        return {"is_complex": False, "subqueries": [q]}

    try:
        response = _openai_chat_create_with_timeout(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": """
You are a query planner for an enterprise RAG system.

Break the query into subqueries that maximize retrieval quality.

Rules:
- Only decompose if the query is complex (multi-intent, comparison, multi-hop).
- If simple: return one subquery equal to the original query.
- If complex: produce 2-4 subqueries.
- Each subquery must target a DIFFERENT aspect.
- Each subquery must be more specific than the original (add retrieval terms like architecture, implementation, challenges, limitations, traceability, scalability, outcomes, metrics, ROI).
- Avoid repeating the same wording.
- Write subqueries as natural questions.

Return JSON only in this exact format:
{
  "is_complex": true/false,
  "subqueries": [
    {"type": "solution", "query": "..."},
    {"type": "scalability", "query": "..."}
  ]
}
""".strip(),
                },
                {"role": "user", "content": query},
            ],
        )

        content = (response.choices[0].message.content or "").strip()
        json_str = _extract_json_object(content) or content
        out = json.loads(json_str)

        is_complex = bool(out.get("is_complex"))
        subqueries = out.get("subqueries")

        cleaned: list[dict] = []
        if isinstance(subqueries, list):
            for item in subqueries:
                if isinstance(item, dict):
                    t = (item.get("type") or "").strip() or "general"
                    qq = (item.get("query") or "").strip()
                    if qq:
                        cleaned.append({"type": t, "query": qq})
                elif isinstance(item, str):
                    qq = item.strip()
                    if qq:
                        cleaned.append({"type": "general", "query": qq})

        cleaned = cleaned[:4] if is_complex else cleaned[:1]
        if not cleaned:
            cleaned = [{"type": "general", "query": q}]

        return {"is_complex": is_complex and len(cleaned) > 1, "subqueries": cleaned}
    except Exception as e:
        logger.warning("[DecomposeQuery] failed, using original query: %s", e)
        return {"is_complex": False, "subqueries": [{"type": "general", "query": q}]}


def _merge_docs(doc_dicts: list[dict]) -> dict:
    merged: dict[str, list[dict]] = {}
    best_by_key: dict[tuple[str, int], dict] = {}

    for d in doc_dicts:
        if not isinstance(d, dict):
            continue
        for col, docs in d.items():
            if not isinstance(docs, list):
                continue
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                doc_id = doc.get("document_id")
                if not isinstance(doc_id, int):
                    continue
                key = (str(col), doc_id)
                prev = best_by_key.get(key)
                prev_score = float(prev.get("rerank_score", prev.get("semantic_score", 0.0))) if prev else -1.0
                new_score = float(doc.get("rerank_score", doc.get("semantic_score", 0.0)))
                if prev is None or new_score > prev_score:
                    best_by_key[key] = doc

    for (col, _doc_id), doc in best_by_key.items():
        merged.setdefault(col, []).append(doc)

    for col_name in list(merged.keys()):
        merged[col_name] = sorted(
            merged[col_name],
            key=lambda x: float(x.get("rerank_score", x.get("semantic_score", 0.0))),
            reverse=True,
        )

    return merged


def _merge_chunks(chunks_dicts: list[dict]) -> dict:
    merged: dict[str, list[dict]] = {}
    by_doc: dict[tuple[str, int], dict] = {}

    for d in chunks_dicts:
        if not isinstance(d, dict):
            continue
        for col, docs in d.items():
            if not isinstance(docs, list):
                continue
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                doc_id = doc.get("document_id")
                if not isinstance(doc_id, int):
                    continue

                key = (str(col), doc_id)
                if key not in by_doc:
                    by_doc[key] = {
                        "document_id": doc_id,
                        "file_name": doc.get("file_name"),
                        "title": doc.get("title"),
                        "chunks": [],
                    }

                chunks = doc.get("chunks")
                if isinstance(chunks, list):
                    by_doc[key]["chunks"].extend([c for c in chunks if isinstance(c, dict)])

    for (col, _doc_id), doc in by_doc.items():
        merged.setdefault(col, []).append(doc)

    return merged


def _rrf_fuse_ranked_ids(ranked_id_lists: list[list[str]], k: int = 60, weights: list[float] | None = None) -> list[str]:
    fused: dict[str, float] = {}
    if weights is None:
        weights = [1.0] * len(ranked_id_lists)
    for lst, w in zip(ranked_id_lists, weights):
        for r, item_id in enumerate(lst, start=1):
            fused[item_id] = fused.get(item_id, 0.0) + w * (1.0 / (k + r))
    return [item_id for item_id, _ in sorted(fused.items(), key=lambda x: x[1], reverse=True)]


def _compute_retrieval_confidence(chunks_data: dict) -> dict:
    """Compute lightweight confidence metrics from retrieval output.

    Uses `rrf_score` proxy if present on chunk objects.
    """
    total_chunks = 0
    scores: list[float] = []
    for _col, docs in (chunks_data or {}).items():
        if not isinstance(docs, list):
            continue
        for d in docs:
            if not isinstance(d, dict):
                continue
            for ch in (d.get("chunks") or []):
                if not isinstance(ch, dict):
                    continue
                total_chunks += 1
                s = ch.get("rrf_score")
                if isinstance(s, (int, float)):
                    scores.append(float(s))

    if not scores:
        return {"total_chunks": total_chunks, "avg_rrf": 0.0, "top3_avg_rrf": 0.0}

    scores_sorted = sorted(scores, reverse=True)
    top3 = scores_sorted[:3]
    return {
        "total_chunks": total_chunks,
        "avg_rrf": sum(scores) / len(scores),
        "top3_avg_rrf": sum(top3) / len(top3),
    }


def _query_needs_multi_doc(answer_mode: str | None, named_entities: list[str] | None = None) -> bool:
    distinct_entities = [e for e in dict.fromkeys(named_entities or []) if isinstance(e, str) and e.strip()]
    return (answer_mode or "") in {"compare", "timeline", "aggregate"} or len(distinct_entities) >= 2


def _build_rerank_passage(chunk: dict) -> str:
    metadata = _get_chunk_metadata(chunk)
    document_title = str(chunk.get("document_title") or chunk.get("title") or "").strip()
    file_name = str(chunk.get("file_name") or "").strip()
    section_path = str(metadata.get("section_path") or metadata.get("heading_level_1") or "").strip()
    keywords = metadata.get("keywords")
    if isinstance(keywords, list):
        keywords_text = ", ".join(str(k).strip() for k in keywords if str(k).strip())
    else:
        keywords_text = str(keywords or "").strip()

    parts = []
    if document_title:
        parts.append(f"Document: {document_title}")
    if file_name and file_name != document_title:
        parts.append(f"File: {file_name}")
    if section_path:
        parts.append(f"Section: {section_path}")
    if keywords_text:
        parts.append(f"Keywords: {keywords_text[:220]}")
    parts.append(f"Content: {_get_chunk_text(chunk)}")
    return "\n".join(part for part in parts if part)


def _flatten_chunk_candidates(chunks_data: dict | None) -> list[dict[str, Any]]:
    best_by_id: dict[str, dict[str, Any]] = {}
    for col_name, docs in (chunks_data or {}).items():
        if col_name == "_metadata" or not isinstance(docs, list):
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            document_id = doc.get("document_id")
            if not isinstance(document_id, int):
                continue
            document_title = str(doc.get("title") or doc.get("file_name") or "").strip()
            file_name = str(doc.get("file_name") or doc.get("title") or "").strip()
            for ch in doc.get("chunks") or []:
                if not isinstance(ch, dict):
                    continue
                item = dict(ch)
                item["document_id"] = int(document_id)
                item["collection_name"] = str(item.get("collection_name") or col_name)
                item["document_title"] = str(item.get("document_title") or document_title)
                item["file_name"] = file_name
                item["rerank_text"] = _build_rerank_passage(item)
                chunk_id = str(item.get("chunk_id") or f"{item['collection_name']}:{document_id}:{len(best_by_id)}")
                prev = best_by_id.get(chunk_id)
                prev_score = float((prev or {}).get("final_score", (prev or {}).get("rrf_score", 0.0)) or 0.0)
                new_score = float(item.get("final_score", item.get("rrf_score", 0.0)) or 0.0)
                if prev is None or new_score > prev_score:
                    best_by_id[chunk_id] = item
    return list(best_by_id.values())


def _should_run_global_rerank(
    chunks_data: dict | None,
    *,
    answer_mode: str | None,
    named_entities: list[str] | None = None,
) -> tuple[bool, dict[str, Any]]:
    candidates = _flatten_chunk_candidates(chunks_data)
    total_chunks = len(candidates)
    distinct_docs = len({
        (str(item.get("collection_name") or ""), int(item.get("document_id") or 0))
        for item in candidates
        if isinstance(item.get("document_id"), int)
    })
    requires_multi_doc = _query_needs_multi_doc(answer_mode, named_entities)

    if total_chunks <= 8:
        return False, {
            "total_chunks": total_chunks,
            "distinct_docs": distinct_docs,
            "requires_multi_doc": requires_multi_doc,
            "reason": "chunk_count_le_8",
        }
    if total_chunks > 12:
        return True, {
            "total_chunks": total_chunks,
            "distinct_docs": distinct_docs,
            "requires_multi_doc": requires_multi_doc,
            "reason": "chunk_count_gt_15",
        }
    if requires_multi_doc:
        return True, {
            "total_chunks": total_chunks,
            "distinct_docs": distinct_docs,
            "requires_multi_doc": requires_multi_doc,
            "reason": "mid_band_multi_doc_query",
        }
    return False, {
        "total_chunks": total_chunks,
        "distinct_docs": distinct_docs,
        "requires_multi_doc": requires_multi_doc,
        "reason": "mid_band_simple_query",
    }


def _build_chunk_payload_from_ranked(selected_chunks: list[dict[str, Any]], *, stage: str) -> dict:
    payload: dict[str, list[dict[str, Any]] | dict[str, Any]] = {}
    doc_entries: dict[tuple[str, int], dict[str, Any]] = {}
    for chunk in sorted(selected_chunks, key=lambda item: int(item.get("rerank_rank", 10**9) or 10**9)):
        collection_name = str(chunk.get("collection_name") or "__unknown__")
        document_id = chunk.get("document_id")
        if not isinstance(document_id, int):
            continue
        key = (collection_name, document_id)
        if key not in doc_entries:
            entry = {
                "document_id": int(document_id),
                "file_name": str(chunk.get("file_name") or chunk.get("document_title") or ""),
                "title": str(chunk.get("document_title") or chunk.get("file_name") or ""),
                "chunks": [],
                "chunk_debug": {
                    "selected_chunk_ids": [],
                    "rerank_stage": stage,
                },
            }
            payload.setdefault(collection_name, []).append(entry)
            doc_entries[key] = entry
        clean_chunk = dict(chunk)
        clean_chunk.pop("rerank_text", None)
        doc_entries[key]["chunks"].append(clean_chunk)
        doc_entries[key]["chunk_debug"]["selected_chunk_ids"].append(str(clean_chunk.get("chunk_id") or ""))

    payload["_metadata"] = {
        "tool": "rerank_chunks_exec",
        "retrieval_stage": stage,
        "final_chunks_sent": len(selected_chunks),
    }
    return payload


def _select_reranked_chunk_payload(
    chunks_data: dict | None,
    *,
    query: str,
    top_k: int,
    answer_mode: str | None,
    named_entities: list[str] | None = None,
) -> tuple[dict, dict[str, Any]]:
    candidates = _flatten_chunk_candidates(chunks_data)
    if not candidates:
        return {"_metadata": {"tool": "rerank_chunks_exec", "retrieval_stage": "empty"}}, {
            "candidate_count": 0,
            "selected_count": 0,
            "top_k": top_k,
            "selected_chunk_ids": [],
            "top_scores": [],
        }

    ranked = rerank_chunks_scored(query=query, chunks=candidates, text_key="rerank_text", top_k=None)
    distinct_entities = [e for e in dict.fromkeys(named_entities or []) if isinstance(e, str) and e.strip()]
    distinct_docs_available = list(dict.fromkeys(
        (str(item.get("collection_name") or ""), int(item.get("document_id")))
        for item in ranked
        if isinstance(item.get("document_id"), int)
    ))
    require_multi_doc = _query_needs_multi_doc(answer_mode, distinct_entities) and len(distinct_docs_available) > 1
    max_per_doc = _tool3_local_per_doc_limit(
        answer_mode,
        query_type=detect_query_type(query),
        total_docs=len(distinct_docs_available),
        requested_limit=top_k,
    )

    must_keep_ids: set[str] = set()
    coverage_doc_target = min(5, len(distinct_docs_available))
    if require_multi_doc and coverage_doc_target > 0:
        covered_docs: set[tuple[str, int]] = set()
        for item in ranked:
            key = (str(item.get("collection_name") or ""), int(item.get("document_id") or 0))
            if key in covered_docs:
                continue
            covered_docs.add(key)
            must_keep_ids.add(str(item.get("chunk_id") or ""))
            if len(covered_docs) >= coverage_doc_target:
                break

    # Score-based priority: prioritize any chunk with high rerank certainty
    for item in ranked:
        if float(item.get("rerank_score", 0.0) or 0.0) >= 0.05:
            must_keep_ids.add(str(item.get("chunk_id") or ""))

    for entity in distinct_entities:
        entity_lower = entity.lower()
        for item in ranked:
            if entity_lower in _get_chunk_text(item).lower():
                must_keep_ids.add(str(item.get("chunk_id") or ""))

    if (answer_mode or "") == "timeline":
        for item in ranked:
            text = _get_chunk_text(item).lower()
            if re.search(r"\b\d+(?:\.\d+)?\s*(?:-|to)?\s*\d*(?:\.\d+)?\s*(?:month|months|week|weeks|year|years|phase|duration)\b", text):
                must_keep_ids.add(str(item.get("chunk_id") or ""))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    per_doc_counts: dict[tuple[str, int], int] = {}

    def _try_add(item: dict[str, Any], *, enforce_doc_cap: bool = True) -> bool:
        chunk_id = str(item.get("chunk_id") or "")
        if not chunk_id or chunk_id in selected_ids:
            return False
        key = (str(item.get("collection_name") or ""), int(item.get("document_id") or 0))
        if enforce_doc_cap and require_multi_doc and per_doc_counts.get(key, 0) >= max_per_doc:
            return False
        selected.append(item)
        selected_ids.add(chunk_id)
        per_doc_counts[key] = per_doc_counts.get(key, 0) + 1
        return True

    for item in ranked:
        if len(selected) >= top_k:
            break
        if str(item.get("chunk_id") or "") in must_keep_ids:
            _try_add(item, enforce_doc_cap=False)

    for item in ranked:
        if len(selected) >= top_k:
            break
        _try_add(item, enforce_doc_cap=True)

    for item in ranked:
        if len(selected) >= top_k:
            break
        _try_add(item, enforce_doc_cap=False)

    top_scores = [round(float(item.get("rerank_score", 0.0) or 0.0), 4) for item in ranked[:5]]
    payload = _build_chunk_payload_from_ranked(selected, stage=f"rerank_top_{top_k}")
    debug = {
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "top_k": top_k,
        "requires_multi_doc": require_multi_doc,
        "max_per_doc": max_per_doc,
        "distinct_docs_available": len(distinct_docs_available),
        "selected_docs": len({
            (str(item.get("collection_name") or ""), int(item.get("document_id") or 0))
            for item in selected
            if isinstance(item.get("document_id"), int)
        }),
        "selected_chunk_ids": [str(item.get("chunk_id") or "") for item in selected],
        "top_scores": top_scores,
    }
    return payload, debug


def _lightweight_rerank_coverage_check(
    selected_chunks: dict | None,
    *,
    full_chunks_data: dict | None,
    named_entities: list[str] | None,
    answer_mode: str | None,
) -> dict[str, Any]:
    selected = sorted(
        _flatten_chunk_candidates(selected_chunks),
        key=lambda item: float(item.get("rerank_score", item.get("final_score", 0.0)) or 0.0),
        reverse=True,
    )
    full_candidates = _flatten_chunk_candidates(full_chunks_data)
    distinct_entities = [e for e in dict.fromkeys(named_entities or []) if isinstance(e, str) and e.strip()]
    selected_doc_count = len({
        (str(item.get("collection_name") or ""), int(item.get("document_id") or 0))
        for item in selected
        if isinstance(item.get("document_id"), int)
    })
    available_doc_count = len({
        (str(item.get("collection_name") or ""), int(item.get("document_id") or 0))
        for item in full_candidates
        if isinstance(item.get("document_id"), int)
    })
    strong_chunks = 0
    entity_hits: dict[str, int] = {entity: 0 for entity in distinct_entities}
    has_duration = False
    for item in selected:
        text = _get_chunk_text(item).lower()
        reasons = set(str(reason) for reason in (item.get("selection_reason") or []))
        rerank_score = float(item.get("rerank_score", item.get("final_score", 0.0)) or 0.0)
        if rerank_score > 0.0 or reasons.intersection({"anchor_match", "entity_match", "quoted_phrase_match", "timeline_numeric_match", "strongest_for_doc", "strongest_for_entity"}):
            strong_chunks += 1
        if re.search(r"\b\d+(?:\.\d+)?\s*(?:-|to)?\s*\d*(?:\.\d+)?\s*(?:month|months|week|weeks|year|years|phase|duration)\b", text):
            has_duration = True
        for entity in distinct_entities:
            if entity.lower() in text:
                entity_hits[entity] = entity_hits.get(entity, 0) + 1

    top3_scores = [float(item.get("rerank_score", item.get("final_score", 0.0)) or 0.0) for item in selected[:3]]
    weak_scores = bool(top3_scores) and max(top3_scores) <= 0.0
    missing_entities = [entity for entity in distinct_entities if entity_hits.get(entity, 0) <= 0]

    passes = True
    reasons: list[str] = []
    if not selected:
        passes = False
        reasons.append("no_chunks_selected")
    if (answer_mode or "direct") == "direct" and strong_chunks < min(2, len(selected)):
        passes = False
        reasons.append("insufficient_strong_chunks")
    if missing_entities:
        passes = False
        reasons.append("missing_entity_coverage")
    if (answer_mode or "") == "compare" and available_doc_count > 1 and selected_doc_count < 2:
        passes = False
        reasons.append("compare_needs_multiple_docs")
    if (answer_mode or "") == "timeline" and not has_duration:
        passes = False
        reasons.append("timeline_needs_duration_evidence")
    if _query_needs_multi_doc(answer_mode, distinct_entities) and available_doc_count > 1 and selected_doc_count < 2:
        passes = False
        reasons.append("multi_doc_coverage_missing")
    if weak_scores and strong_chunks < 2:
        passes = False
        reasons.append("weak_rerank_scores")

    return {
        "passes": passes,
        "reasons": reasons,
        "selected_count": len(selected),
        "selected_doc_count": selected_doc_count,
        "available_doc_count": available_doc_count,
        "strong_chunks": strong_chunks,
        "missing_entities": missing_entities,
        "entity_hits": entity_hits,
        "has_duration": has_duration,
        "top3_rerank_scores": [round(score, 4) for score in top3_scores],
    }


def _should_expand_after_answer(
    answer_text: str,
    *,
    named_entities: list[str] | None,
    answer_mode: str | None,
) -> bool:
    answer = (answer_text or "").strip()
    if not answer:
        return True
    lower = answer.lower()
    if any(
        marker in lower
        for marker in (
            "i could not find relevant information",
            "could not find",
            "not mentioned in the",
            "not clearly mentioned",
            "unable to find",
            "information is missing",
            "supported part",
        )
    ):
        return True

    distinct_entities = [e for e in dict.fromkeys(named_entities or []) if isinstance(e, str) and e.strip()]
    if (answer_mode or "") in {"compare", "timeline"} and len(distinct_entities) >= 2:
        mentioned = sum(1 for entity in distinct_entities if entity.lower() in lower)
        if mentioned < min(2, len(distinct_entities)):
            return True
    return False


def _expand_pass2_candidate_pool(
    base_chunks: dict | None,
    *,
    query: str,
    db: Session,
    chunks_per_doc: int,
    anchors: list[str] | None,
    entities: list[str] | None,
    answer_mode: str | None,
) -> tuple[dict, dict[str, Any]]:
    recovered_docs = _recover_top_documents_globally(query, db)
    if not recovered_docs:
        return base_chunks or {}, {
            "expanded": False,
            "recovered_docs": 0,
            "reason": "no_global_recovery",
        }

    p2_chunks = _get_chunks_for_recovered_docs(
        query,
        recovered_docs,
        db,
        chunks_per_doc=max(chunks_per_doc + 1, 6),
        anchors=anchors,
        entities=entities,
        answer_mode=answer_mode,
    )
    stitched = _stitch_chunks(
        p2_chunks,
        query=query,
        anchors=anchors,
        entities=entities,
        answer_mode=answer_mode,
        chunks_per_doc=max(chunks_per_doc + 1, 6),
    )
    merged = _merge_chunks([base_chunks or {}, stitched])
    merged["_metadata"] = {
        "tool": "pass2_chunk_pool_merge",
        "retrieval_stage": "pass2_recovery",
        "recovered_docs": len(recovered_docs),
    }
    return merged, {
        "expanded": True,
        "recovered_docs": len(recovered_docs),
        "merged_doc_count": sum(len(v) for k, v in merged.items() if k != "_metadata" and isinstance(v, list)),
    }


def _compile_snippets(chunks_data: dict, query: str, max_sentences_per_chunk: int = 2, max_chars_per_snippet: int = 600) -> dict:
    q_terms = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-./]+", (query or "").lower()))
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "for", "with", "on", "at", "by", "from", "is", "are"}
    q_terms = {t for t in q_terms if t not in stop and len(t) >= 3}

    out = {}
    for col_name, docs in (chunks_data or {}).items():
        if not isinstance(docs, list):
            out[col_name] = docs
            continue

        new_docs = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            new_doc = dict(doc)
            chunks = doc.get("chunks") or []
            new_chunks = []
            for idx, ch in enumerate(chunks):
                chunk_text = None
                chunk_rank = None
                if isinstance(ch, dict):
                    chunk_text = _get_chunk_text(ch)
                    chunk_rank = ch.get("rank")
                elif isinstance(ch, str):
                    chunk_text = ch

                if not isinstance(chunk_text, str) or not chunk_text.strip():
                    continue

                raw = chunk_text.strip()
                is_table_like = ("|" in raw and "\n" in raw) or bool(re.search(r"\n\s*\|?\s*-{3,}\s*\|", raw))
                is_bullets_like = "\n-" in raw or "\n*" in raw or "\n•" in raw

                # Preserve structure for tables/bullets (critical for presales + case studies)
                if is_table_like or is_bullets_like:
                    snippet = raw
                    if len(snippet) > max_chars_per_snippet:
                        snippet = snippet[:max_chars_per_snippet]
                    new_chunks.append(snippet)
                    continue

                text = re.sub(r"\s+", " ", raw).strip()
                sentences = re.split(r"(?<=[.!?])\s+", text)
                scored = []
                for s in sentences:
                    s2 = s.strip()
                    if not s2:
                        continue
                    s_terms = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-./]+", s2.lower()))
                    overlap = len(s_terms & q_terms)

                    # Retrieval-rank proxy: earlier chunks are usually better; allow explicit `rank` if present.
                    rank_proxy = chunk_rank if isinstance(chunk_rank, (int, float)) else float(idx + 1)
                    rank_score = 1.0 / (1.0 + rank_proxy)

                    score = (overlap * 0.6) + (rank_score * 0.4)
                    scored.append((score, s2))

                scored.sort(key=lambda x: x[0], reverse=True)
                picked = [s for _, s in scored[:max_sentences_per_chunk] if s]
                snippet = " ".join(picked) if picked else text
                snippet = snippet[:max_chars_per_snippet]
                new_chunks.append(snippet)
            new_doc["chunks"] = new_chunks
            new_docs.append(new_doc)
        out[col_name] = new_docs
    return out



# ============================================================================
# TOOL 1 — get_all_collections
# ============================================================================

def get_all_collections() -> dict:
    """
    Fetch all collection metadata from SQLite.

    Returns:
        {
            "tool_name": "get_all_collections",
            "total_collections": <int>,
            "collections": [
                {
                    "collection_id": <int>,
                    "collection_name": "<str>",
                    "metadata": "<str>"
                },
                ...
            ]
        }
    """
    logger.info("[Tool 1] Fetching all collections from SQLite")

    db: Session = SessionLocal()

    try:
        categories = db.query(Category).all()

        collections = []

        for category in categories:
            collections.append({
                "collection_id": category.id,
                "collection_name": category.collection_name,
                "metadata": category.description,
            })


        result = {
            "tool_name": "get_all_collections",
            "total_collections": len(collections),
            "collections": collections,
        }

        logger.info("[Tool 1] Found %d collections", len(collections))

        return result

    finally:
        db.close()



# ============================================================================
# TOOL 2 — Hybrid Search & Reranking
# ============================================================================

def rerank_documents(user_query: str, candidates: list, top_k: int) -> tuple[list, str, int]:
    """
    Use LLM to score and rerank candidate documents.
    Returns: (ranked_candidates, model_used, tokens_used)
    """
    if not candidates:
        return [], None, 0

    # Fast path: too few candidates to justify an LLM rerank call.
    if len(candidates) <= 5:
        return candidates[:top_k], None, 0

    logger.info("[Reranker] Scoring %d candidates...", len(candidates))

    cand_info = [
        {"id": c["document_id"], "title": c.get("title", ""), "description": c.get("description", "")}
        for c in candidates
    ]
    cand_payload, _cj, _ct, _cs = _maybe_toon_payload(
        cand_info, call_name="rerank_documents", data_name="candidate_documents"
    )
    prompt = (
        f"Query: {user_query}\n\nCandidate Documents:\n{cand_payload}\n\n"
        "Score each document 0.0-10.0 on likelihood it contains the answer.\n"
        'Return ONLY a JSON list: [{"id": 1, "score": 9.5}, ...]'
    )

    try:
        response = _openai_chat_create_with_timeout(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "You are a document reranking expert. Respond only with JSON."},
                {"role": "user", "content": prompt},
            ],
        )

        scores_raw = _strip_markdown_code_fence(response.choices[0].message.content or "")
        scores_list = json.loads(scores_raw)
        score_map = {item["id"]: item["score"] for item in scores_list}

        # Apply scores
        for c in candidates:
            c["rerank_score"] = score_map.get(c["document_id"], 0.0)

        # Sort and limit
        ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
        return ranked[:top_k], response.model, response.usage.total_tokens if response.usage else 0

    except Exception as e:
        logger.error("[Reranker] Failed: %s", e)
        # Fallback to original order (semantic score)
        return candidates[:top_k], None, 0



def _parallel_worker_count(item_count: int, *, env_var: str, default_max: int) -> int:
    if item_count <= 1:
        return 1
    try:
        configured = int(os.getenv(env_var, str(default_max)))
    except Exception:
        configured = default_max
    configured = max(1, configured)
    return min(item_count, configured)


def _run_tool3_doc_chunk_job(
    *,
    job: dict[str, Any],
    user_query: str,
    query_type: str,
    hard_filters: dict | None,
    effective_chunks_per_doc: int,
    local_chunks_per_doc: int,
    dense_primary_queries: list[str],
    chunk_sparse_queries: list[str],
    anchor_terms: list[str],
    named_entities: list[str],
    answer_mode: str | None,
    retrieval_stage: str,
    exact_phrases: list[str],
) -> dict[str, Any] | None:
    doc_id = int(job["document_id"])
    file_name = str(job["file_name"])
    doc_title = str(job["title"])
    doc_collection_name = str(job["collection_name"])
    metadata_support_text = str(job.get("metadata_support_text") or "")

    doc_dense_queries = list(dense_primary_queries)
    doc_sparse_queries = list(chunk_sparse_queries)
    if query_type != "general" and metadata_support_text:
        doc_dense_queries.append(metadata_support_text)
        doc_sparse_queries.append(metadata_support_text)

    if anchor_terms or named_entities:
        doc_searchable = f"{doc_title} {file_name} {metadata_support_text}"
        matching_entities = [
            entity for entity in named_entities
            if entity and entity.lower() in doc_searchable.lower()
        ]
        answer_terms: list[str] = []
        q_lower = user_query.lower()
        if any(t in q_lower for t in ["timeline", "timelines", "duration", "month", "months", "phase"]):
            answer_terms = ["timeline", "duration", "months", "phase"]
        elif "raci" in q_lower or "accountable" in q_lower:
            answer_terms = ["raci", "accountable", "responsible"]

        for entity in matching_entities:
            focused = f"{entity} {' '.join(answer_terms)}".strip()
            if focused and focused not in doc_dense_queries:
                doc_dense_queries.append(focused)
            if focused and focused not in doc_sparse_queries:
                doc_sparse_queries.append(focused)
        for anchor in anchor_terms:
            if anchor and anchor.lower() in doc_searchable.lower():
                focused_anchor = f"{anchor} {' '.join(answer_terms)}".strip()
                if focused_anchor and focused_anchor not in doc_dense_queries:
                    doc_dense_queries.append(focused_anchor)
                if focused_anchor and focused_anchor not in doc_sparse_queries:
                    doc_sparse_queries.append(focused_anchor)

    dense_chunks: list[dict[str, Any]] = []
    try:
        local_chroma_client = _get_chroma_client()
        collection = local_chroma_client.get_collection(doc_collection_name)
        col_count = collection.count()
        safe_n = min(max(effective_chunks_per_doc * 2, effective_chunks_per_doc), col_count) if col_count > 0 else 1

        where_clause: dict[str, Any] = {"document_id": doc_id}
        if hard_filters:
            filter_parts: list[dict[str, Any]] = [{"document_id": doc_id}]
            for k, v in hard_filters.items():
                if v:
                    filter_parts.append({k: v})
            if len(filter_parts) > 1:
                where_clause = {"$and": filter_parts}

        for dq in doc_dense_queries:
            query_embedding = _embed_query(dq)
            dense_results = collection.query(
                query_embeddings=[query_embedding],
                where=where_clause,
                n_results=safe_n,
                include=["documents", "metadatas", "distances"],
            )
            if dense_results.get("ids") and dense_results["ids"][0]:
                for i, cid in enumerate(dense_results["ids"][0]):
                    dense_chunks.append({
                        "id": cid,
                        "chunk_text": dense_results["documents"][0][i],
                        "metadata": dense_results["metadatas"][0][i] if dense_results.get("metadatas") else {},
                        "score": float(dense_results["distances"][0][i]) if dense_results.get("distances") else 0.0,
                    })
    except Exception as dense_err:
        if _is_dim_mismatch_error(dense_err):
            expected_dim, got_dim = _extract_dims_from_dim_error_message(dense_err)
            raise EmbeddingDimensionMismatchError(
                doc_collection_name,
                expected_dim=expected_dim,
                got_dim=got_dim,
                original_error=dense_err,
            ) from dense_err
        logger.warning("[Tool 3 Exec] Dense retrieval failed for doc_id %s: %s", doc_id, dense_err)

    sparse_chunks: list[dict[str, Any]] = []
    for sq in doc_sparse_queries:
        sparse_chunks.extend(query_bm25(sq, doc_collection_name, n_results=effective_chunks_per_doc * 2))
    sparse_chunks = [s for s in sparse_chunks if s.get("id") and str(doc_id) in s["id"]]

    fused_chunks = reciprocal_rank_fusion(dense_chunks, sparse_chunks)
    fused_chunks = _boost_chunks_by_query_type(user_query, fused_chunks)
    dense_rank_map: dict[str, int] = {}
    sparse_rank_map: dict[str, int] = {}
    for rank, dense_chunk in enumerate(dense_chunks, start=1):
        dense_rank_map[str(dense_chunk.get("id") or "")] = rank
    for rank, sparse_chunk in enumerate(sparse_chunks, start=1):
        sparse_rank_map[str(sparse_chunk.get("id") or "")] = rank

    canonical_candidates: list[dict[str, Any]] = []
    for fused in fused_chunks:
        raw_chunk_id = str(fused.get("id") or "")
        canonical_candidates.append(
            _build_canonical_chunk(
                chunk=fused,
                collection_name=doc_collection_name,
                document_id=doc_id,
                document_title=doc_title,
                user_query=user_query,
                anchors=anchor_terms,
                entities=named_entities,
                answer_mode=answer_mode,
                retrieval_stage=retrieval_stage,
                dense_rank=dense_rank_map.get(raw_chunk_id),
                sparse_rank=sparse_rank_map.get(raw_chunk_id),
                exact_phrases=exact_phrases,
            )
        )

    final_chunks, selection_debug = _select_lossless_chunks_for_doc(
        candidates=canonical_candidates,
        user_query=user_query,
        anchors=anchor_terms,
        entities=named_entities,
        answer_mode=answer_mode,
        per_doc_limit=local_chunks_per_doc,
    )
    return {
        "order_index": int(job.get("order_index", 0)),
        "document_id": doc_id,
        "document_title": doc_title,
        "collection_name": doc_collection_name,
        "doc_entry": {
            "document_id": doc_id,
            "file_name": file_name,
            "title": doc_title,
            "chunks": final_chunks,
            "chunk_debug": selection_debug,
        },
        "selection_debug": selection_debug,
    }



def _score_single_doc_metadata_job(
    doc: Any,
    doc_id: int,
    user_query: str,
    query_type: str,
    collections_to_query: list[str],
    enforce_collection_filter: bool,
    exact_phrases: list[str],
    anchor_terms: list[str],
    named_entities: list[str],
    router_collections: list[str],
    soft_filters: dict | None,
    db: Session,
) -> dict | None:
    col_name = _resolve_document_collection_name(doc, db)
    if enforce_collection_filter and collections_to_query and (not col_name or col_name not in collections_to_query):
        return None

    desc_content = doc.description
    if desc_content:
        try:
            desc_content = json.loads(desc_content)
        except Exception:
            pass

    metadata_support_text = _extract_metadata_support_text(desc_content, query_type)
    searchable_text = ""
    if isinstance(desc_content, dict):
        try:
            searchable_text = json.dumps(desc_content, ensure_ascii=False)
        except Exception:
            searchable_text = str(desc_content)
    else:
        searchable_text = str(desc_content or "")
    searchable = f"{doc.title or ''} {doc.file_name or ''} {searchable_text}"

    metadata_support_boost = len(set(_tokenize(searchable)) & set(_tokenize(user_query)))
    if query_type != "general" and metadata_support_text:
        metadata_support_boost += len(
            set(_tokenize(metadata_support_text)) & (set(_tokenize(user_query)) | _QUERY_TYPE_TERMS.get(query_type, set()))
        )

    soft_domain_hint = None
    if isinstance(soft_filters, dict):
        soft_domain_hint = soft_filters.get("domain") or soft_filters.get("domain_hint")
    if soft_domain_hint:
        try:
            dom_name = None
            if getattr(doc, "domain_ref", None) is not None:
                dom_name = getattr(doc.domain_ref, "name", None)
            if dom_name and str(dom_name).strip().lower() == str(soft_domain_hint).strip().lower():
                metadata_support_boost += 2
        except Exception:
            pass

    if exact_phrases:
        exact_hits = sum(1 for p in exact_phrases if p in searchable.lower())
        if exact_hits:
            metadata_support_boost += exact_hits * 10

    anchor_match_score = _text_matches_any_anchor(searchable, anchors=anchor_terms, entities=named_entities)
    if anchor_match_score:
        metadata_support_boost += anchor_match_score * 5
    if router_collections and col_name in router_collections:
        metadata_support_boost += 2

    return {
        "col_name": col_name,
        "payload": {
            "document_id": doc_id,
            "title": doc.title,
            "file_name": doc.file_name,
            "description": desc_content,
            "metadata_support_text": metadata_support_text,
            "metadata_support_boost": metadata_support_boost,
            "anchor_match_score": anchor_match_score,
        }
    }


def get_top_documents_from_collection_exec(
    user_query: str,
    selected_collection: str,
    top_k: int,
    filters: dict | None = None,
    doc_queries: list[str] | None = None,
    router_collections: list[str] | None = None,
    anchor_terms: list[str] | None = None,
    named_entities: list[str] | None = None,
    session: Session | None = None,
) -> dict:
    """Tool 2 (executor): no LLM calls, no rewriting, no HyDE gating.

    The agent must pass `doc_queries` produced by `refine_query`.
    """
    hard_filters, soft_filters = _split_filters(filters)
    hard_where = _to_chroma_where(hard_filters)
    logger.info(
        "[Tool 2 Exec] Hybrid Search on: %s (hard_filters: %s)",
        selected_collection,
        hard_filters,
    )

    if "," in (selected_collection or ""):
        return {
            "error": (
                "STRICT TOOL USAGE RULE VIOLATION: selected_collection must contain exactly one "
                "collection name. Retry with a single collection."
            )
        }

    collections_to_query = [_resolve_existing_collection_name(c.strip()) for c in (selected_collection or "").split(",") if c.strip()]
    collections_to_query = [c for c in collections_to_query if c]
    router_collections = _normalize_collection_list(router_collections)
    anchor_terms = [a for a in (anchor_terms or []) if isinstance(a, str) and a.strip()]
    named_entities = [e for e in (named_entities or []) if isinstance(e, str) and e.strip()]
    enforce_collection_filter = _query_explicitly_mentions_collections(user_query, collections_to_query)
    exact_phrases = _extract_exact_query_phrases(user_query)
    doc_queries = [q for q in (doc_queries or []) if isinstance(q, str) and q.strip()]
    if not doc_queries:
        doc_queries = [user_query]

    db: Session = session if session else SessionLocal()
    from app.sqlite.models import Document, Category

    try:
        master_lists: list[list[str]] = []
        t2_search_workers = _parallel_worker_count(len(doc_queries), env_var="ASKMOJO_TOOL2_SEARCH_WORKERS", default_max=4)
        if t2_search_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=t2_search_workers) as ex:
                futures = {ex.submit(query_master_collection, query_text=q, n_results=top_k * 5, where=hard_where): q for q in doc_queries}
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        mr = fut.result()
                        ids = mr.get("ids") and mr["ids"][0]
                        if ids:
                            master_lists.append([str(x) for x in ids])
                    except EmbeddingDimensionMismatchError as e:
                        return _embedding_dim_mismatch_payload(collection_name="master_docs", err=e)
                    except Exception as e:
                        logger.warning("[Tool 2 Exec] Search failed for query '%s': %s", futures[fut], e)
        else:
            for q in doc_queries:
                try:
                    mr = query_master_collection(query_text=q, n_results=top_k * 5, where=hard_where)
                    ids = mr.get("ids") and mr["ids"][0]
                    if ids:
                        master_lists.append([str(x) for x in ids])
                except EmbeddingDimensionMismatchError as e:
                    return _embedding_dim_mismatch_payload(collection_name="master_docs", err=e)

        if not master_lists and hard_where:
            logger.warning("[Tool 2 Exec] 0 docs with hard filters; retrying without hard filters")
            for q in doc_queries:
                try:
                    mr = query_master_collection(query_text=q, n_results=top_k * 5, where=None)
                except EmbeddingDimensionMismatchError as e:
                    return _embedding_dim_mismatch_payload(collection_name="master_docs", err=e)
                ids = mr.get("ids") and mr["ids"][0]
                if ids:
                    master_lists.append([str(x) for x in ids])

        if not master_lists:
            return {"error": "No documents found in master collection"}

        fused_doc_ids_str = _rrf_fuse_ranked_ids(master_lists, k=60)
        fused_doc_ids_str = fused_doc_ids_str[: top_k * 5]
        doc_ids = [int(doc_id) for doc_id in fused_doc_ids_str if str(doc_id).isdigit()]

        # Phrase-aware fallback: if user asked with quoted phrase(s), enrich candidate
        # docs using SQLite metadata search so we do not miss relevant documents that
        # master_docs semantic retrieval under-ranks.
        # Lexical fallback: add metadata matches by term overlap so we don't over-rely on
        # semantic-only master retrieval when the user's wording is specific.
        q_terms = set(_tokenize(user_query))
        if exact_phrases or q_terms:
            all_processed_docs = db.query(Document).filter(Document.processed == True).all()
            # Pre-compute searchable text for each doc once
            doc_search_index: list[tuple[int, str]] = []
            for d in all_processed_docs:
                desc_content = _parse_doc_description(d)
                if isinstance(desc_content, dict):
                    try:
                        desc_text = json.dumps(desc_content, ensure_ascii=False)
                    except Exception:
                        desc_text = str(desc_content)
                else:
                    desc_text = str(desc_content or "")
                searchable = f"{d.title or ''} {d.file_name or ''} {desc_text}"
                doc_search_index.append((int(d.id), searchable))

            if exact_phrases:
                phrase_match_ids: list[int] = []
                for did, searchable in doc_search_index:
                    if any(p in searchable.lower() for p in exact_phrases):
                        phrase_match_ids.append(did)
                if phrase_match_ids:
                    seen = set(phrase_match_ids)
                    doc_ids = phrase_match_ids + [i for i in doc_ids if i not in seen]
                    doc_ids = doc_ids[: max(top_k * 8, top_k * 5)]

            if q_terms:
                lexical_scored: list[tuple[int, int]] = []
                for did, searchable in doc_search_index:
                    overlap = len(q_terms & set(_tokenize(searchable)))
                    if overlap >= 2:
                        lexical_scored.append((did, overlap))
                if lexical_scored:
                    lexical_scored.sort(key=lambda x: x[1], reverse=True)
                    lexical_ids = [doc_id for doc_id, _score in lexical_scored[: max(top_k * 4, 8)]]
                    seen = set(lexical_ids)
                    doc_ids = lexical_ids + [i for i in doc_ids if i not in seen]
                    doc_ids = doc_ids[: max(top_k * 10, top_k * 5)]

        all_docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
        doc_dict = {doc.id: doc for doc in all_docs}

        collection_candidates: dict[str, list[dict]] = {}
        query_type = detect_query_type(user_query)
        t2_score_workers = _parallel_worker_count(len(doc_ids), env_var="ASKMOJO_TOOL2_SCORE_WORKERS", default_max=4)
        
        score_results = []
        if t2_score_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=t2_score_workers) as ex:
                fut_to_id = {
                    ex.submit(
                        _score_single_doc_metadata_job,
                        doc=doc_dict[did],
                        doc_id=did,
                        user_query=user_query,
                        query_type=query_type,
                        collections_to_query=collections_to_query,
                        enforce_collection_filter=enforce_collection_filter,
                        exact_phrases=exact_phrases,
                        anchor_terms=anchor_terms,
                        named_entities=named_entities,
                        router_collections=router_collections,
                        soft_filters=soft_filters,
                        db=db
                    ): did
                    for did in doc_ids if did in doc_dict
                }
                for fut in concurrent.futures.as_completed(fut_to_id):
                    res = fut.result()
                    if res: score_results.append(res)
        else:
            for did in doc_ids:
                if did not in doc_dict: continue
                res = _score_single_doc_metadata_job(
                    doc=doc_dict[did], doc_id=did, user_query=user_query, query_type=query_type,
                    collections_to_query=collections_to_query, enforce_collection_filter=enforce_collection_filter,
                    exact_phrases=exact_phrases, anchor_terms=anchor_terms, named_entities=named_entities,
                    router_collections=router_collections, soft_filters=soft_filters, db=db
                )
                if res: score_results.append(res)
        
        for r in score_results:
            cn = r["col_name"]
            if cn not in collection_candidates: collection_candidates[cn] = []
            collection_candidates[cn].append(r["payload"])

        # Deterministic ordering: keep fused order (doc_ids) per collection.
        out: dict[str, list[dict]] = {}
        for col_name, cands in collection_candidates.items():
            id_to_doc = {c["document_id"]: c for c in cands if isinstance(c, dict) and isinstance(c.get("document_id"), int)}
            ordered = [id_to_doc[i] for i in doc_ids if i in id_to_doc]
            if detect_query_type(user_query) != "general":
                ordered = sorted(
                    ordered,
                    key=lambda item: (-int(item.get("metadata_support_boost", 0)), doc_ids.index(item["document_id"])),
                )
            out[col_name] = ordered[:top_k]

        if not out:
            return {"error": "No documents found for the requested criteria"}

        # If the query did not explicitly mention a collection, avoid getting stuck on
        # a potentially wrong LLM-selected collection. Pick the best collection deterministically.
        requested_col = collections_to_query[0] if collections_to_query else None
        collection_debug = {
            "requested_collection": requested_col,
            "enforce_collection_filter": enforce_collection_filter,
            "router_collections": list(router_collections or []),
            "collections_considered": [
                {
                    "collection_name": col_name,
                    "documents_returned": len(docs),
                    "best_metadata_support_boost": max(
                        [int(doc.get("metadata_support_boost", 0) or 0) for doc in docs if isinstance(doc, dict)],
                        default=0,
                    ),
                }
                for col_name, docs in out.items()
                if isinstance(docs, list)
            ],
        }

        if not enforce_collection_filter and requested_col and requested_col not in out:
            requested_norm = _normalize_collection_identity(requested_col)
            cat_ids = [
                c.id
                for c in db.query(Category).all()
                if c and _normalize_collection_identity(c.collection_name or "") == requested_norm
            ]
            if cat_ids:
                req_docs = (
                    db.query(Document)
                    .filter(Document.processed == True, Document.category_id.in_(cat_ids))
                    .all()
                )
                if req_docs:
                    q_terms_local = set(_tokenize(user_query))
                    ranked_local: list[tuple[int, dict]] = []
                    for d in req_docs:
                        desc_content = _parse_doc_description(d)
                        if isinstance(desc_content, dict):
                            try:
                                desc_text = json.dumps(desc_content, ensure_ascii=False)
                            except Exception:
                                desc_text = str(desc_content)
                        else:
                            desc_text = str(desc_content or "")
                        searchable = f"{d.title or ''} {d.file_name or ''} {desc_text}"
                        overlap = len(q_terms_local & set(_tokenize(searchable)))
                        ranked_local.append(
                            (
                                overlap,
                                {
                                    "document_id": int(d.id),
                                    "title": d.title,
                                    "file_name": d.file_name,
                                    "description": d.description,
                                    "metadata_support_text": "",
                                    "metadata_support_boost": overlap,
                                },
                            )
                        )
                    ranked_local.sort(key=lambda x: x[0], reverse=True)
                    fallback_docs = [item for _score, item in ranked_local[:top_k]]
                    if fallback_docs:
                        logger.info(
                            "[Tool 2 Exec] Requested collection fallback activated for '%s' (%d docs)",
                            requested_col,
                            len(fallback_docs),
                        )
                        out[requested_col] = fallback_docs
                        collection_debug["requested_collection_recovered"] = True

        if not enforce_collection_filter and len(out) > 1:
            # No collection locking in production: return all candidate collections
            # and let downstream chunk retrieval + synthesis decide.
            out["_metadata"] = collection_debug
            print(compare_formats(out))
            return out
        out["_metadata"] = collection_debug
        print(compare_formats(out))
        return out
    finally:
        if not session:
            db.close()


def get_document_chunks_exec(
    user_query: str,
    collection_name: str,
    document_ids: list,
    chunks_per_doc: int,
    filters: dict | None = None,
    chunk_dense_queries: list[str] | None = None,
    chunk_sparse_queries: list[str] | None = None,
    anchor_terms: list[str] | None = None,
    named_entities: list[str] | None = None,
    answer_mode: str | None = None,
    retrieval_stage: str = "pass1",
    session: Session | None = None,
) -> dict:
    """Tool 3 (executor): no LLM calls, no rewriting, no HyDE gating.

    The agent must pass query lists produced by `refine_query`.
    """
    hard_filters, _soft_filters = _split_filters(filters)
    collection_name = _resolve_existing_collection_name(collection_name) or collection_name
    logger.info(
        "[Tool 3 Exec] Fetching Hybrid chunks for %d documents in %s (hard_filters: %s)",
        len(document_ids),
        collection_name,
        hard_filters,
    )

    query_type = detect_query_type(user_query)
    effective_chunks_per_doc = _minimum_chunks_per_doc(user_query, chunks_per_doc)
    if effective_chunks_per_doc != chunks_per_doc:
        logger.info(
            "[Tool 3 Exec] Increased chunks_per_doc from %d to %d for %s-focused query",
            chunks_per_doc,
            effective_chunks_per_doc,
            query_type,
        )
    local_chunks_per_doc = _tool3_local_per_doc_limit(
        answer_mode,
        query_type=query_type,
        total_docs=len(document_ids),
        requested_limit=effective_chunks_per_doc,
    )
    if local_chunks_per_doc != effective_chunks_per_doc:
        logger.info(
            "[Tool 3 Exec] Applying local Jina prune cap of %d chunks/doc (retrieval candidate target remains %d)",
            local_chunks_per_doc,
            effective_chunks_per_doc,
        )

    dense_primary_queries = [q for q in (chunk_dense_queries or []) if isinstance(q, str) and q.strip()] or [user_query]
    chunk_sparse_queries = [q for q in (chunk_sparse_queries or []) if isinstance(q, str) and q.strip()]
    if not chunk_sparse_queries:
        chunk_sparse_queries = [user_query]
    anchor_terms = [a for a in (anchor_terms or []) if isinstance(a, str) and a.strip()]
    named_entities = [e for e in (named_entities or []) if isinstance(e, str) and e.strip()]

    db: Session = session if session else SessionLocal()
    per_collection_results: dict[str, list[dict]] = {}
    result_metadata = {
        "tool": "get_document_chunks_exec",
        "retrieval_stage": retrieval_stage,
        "documents_processed": 0,
        "candidates_collected": 0,
        "duplicates_merged": 0,
        "chunks_removed_by_budget": 0,
        "final_chunks_sent": 0,
        "per_doc": [],
    }
    try:
        doc_rows = db.query(Document).filter(Document.id.in_(document_ids)).all() if document_ids else []
        doc_lookup = {int(doc.id): doc for doc in doc_rows}
        doc_jobs: list[dict[str, Any]] = []
        for order_index, raw_doc_id in enumerate(document_ids):
            doc_id = int(raw_doc_id)
            doc = doc_lookup.get(doc_id)
            file_name = doc.file_name if doc else f"Unknown_Doc_{doc_id}"
            doc_title = doc.title if doc else f"Unknown Title {doc_id}"
            doc_collection_name = collection_name
            if collection_name in {"__auto__", "auto", "*", "all", ""} and doc:
                resolved = _resolve_document_collection_name(doc, db)
                doc_collection_name = _resolve_existing_collection_name(resolved) or resolved or collection_name
            doc_collection_name = _resolve_existing_collection_name(doc_collection_name) or doc_collection_name
            desc_content = None
            metadata_support_text = ""
            if doc and doc.description:
                try:
                    desc_content = json.loads(doc.description)
                except Exception:
                    desc_content = doc.description
                metadata_support_text = _extract_metadata_support_text(desc_content, query_type)
            doc_jobs.append({
                "order_index": order_index,
                "document_id": doc_id,
                "file_name": file_name,
                "title": doc_title,
                "collection_name": doc_collection_name,
                "metadata_support_text": metadata_support_text,
            })

        exact_phrases = _extract_exact_query_phrases(user_query)
        tool3_workers = _parallel_worker_count(
            len(doc_jobs),
            env_var="ASKMOJO_TOOL3_MAX_WORKERS",
            default_max=4,
        )

        doc_results: list[dict[str, Any]] = []
        if tool3_workers > 1:
            logger.info("[Tool 3 Exec] Parallel per-doc retrieval enabled with %d workers", tool3_workers)
            with concurrent.futures.ThreadPoolExecutor(max_workers=tool3_workers) as ex:
                future_map = {
                    ex.submit(
                        _run_tool3_doc_chunk_job,
                        job=job,
                        user_query=user_query,
                        query_type=query_type,
                        hard_filters=hard_filters,
                        effective_chunks_per_doc=effective_chunks_per_doc,
                        local_chunks_per_doc=local_chunks_per_doc,
                        dense_primary_queries=dense_primary_queries,
                        chunk_sparse_queries=chunk_sparse_queries,
                        anchor_terms=anchor_terms,
                        named_entities=named_entities,
                        answer_mode=answer_mode,
                        retrieval_stage=retrieval_stage,
                        exact_phrases=exact_phrases,
                    ): job
                    for job in doc_jobs
                }
                for fut in concurrent.futures.as_completed(future_map):
                    job = future_map[fut]
                    try:
                        result = fut.result()
                    except EmbeddingDimensionMismatchError as e:
                        return _embedding_dim_mismatch_payload(collection_name=str(job.get("collection_name") or collection_name), err=e)
                    except Exception as e:
                        logger.warning("[Tool 3 Exec] Failed for doc_id %s: %s", job.get("document_id"), e)
                        continue
                    if result:
                        doc_results.append(result)
        else:
            for job in doc_jobs:
                try:
                    result = _run_tool3_doc_chunk_job(
                        job=job,
                        user_query=user_query,
                        query_type=query_type,
                        hard_filters=hard_filters,
                        effective_chunks_per_doc=effective_chunks_per_doc,
                        local_chunks_per_doc=local_chunks_per_doc,
                        dense_primary_queries=dense_primary_queries,
                        chunk_sparse_queries=chunk_sparse_queries,
                        anchor_terms=anchor_terms,
                        named_entities=named_entities,
                        answer_mode=answer_mode,
                        retrieval_stage=retrieval_stage,
                        exact_phrases=exact_phrases,
                    )
                except EmbeddingDimensionMismatchError as e:
                    return _embedding_dim_mismatch_payload(collection_name=str(job.get("collection_name") or collection_name), err=e)
                except Exception as e:
                    logger.warning("[Tool 3 Exec] Failed for doc_id %s: %s", job.get("document_id"), e)
                    continue
                if result:
                    doc_results.append(result)

        for item in sorted(doc_results, key=lambda entry: int(entry.get("order_index", 0))):
            selection_debug = item.get("selection_debug") or {}
            doc_entry = item.get("doc_entry") if isinstance(item.get("doc_entry"), dict) else None
            doc_collection_name = str(item.get("collection_name") or collection_name)
            doc_id = int(item.get("document_id") or 0)
            doc_title = str(item.get("document_title") or "")
            if not doc_entry:
                continue

            final_chunks = doc_entry.get("chunks") or []
            result_metadata["documents_processed"] += 1
            result_metadata["candidates_collected"] += int(selection_debug.get("candidates_collected", 0) or 0)
            result_metadata["duplicates_merged"] += int(selection_debug.get("duplicates_merged", 0) or 0)
            result_metadata["chunks_removed_by_budget"] += int(selection_debug.get("chunks_removed_by_budget", 0) or 0)
            result_metadata["final_chunks_sent"] += len(final_chunks)
            result_metadata["per_doc"].append(
                {
                    "document_id": doc_id,
                    "document_title": doc_title,
                    "collection_name": doc_collection_name,
                    **selection_debug,
                }
            )

            if doc_collection_name not in per_collection_results:
                per_collection_results[doc_collection_name] = []
            per_collection_results[doc_collection_name].append(doc_entry)
    finally:
        if not session:
            db.close()

    if not per_collection_results:
        per_collection_results = {collection_name: []}
    per_collection_results["_metadata"] = result_metadata
    return per_collection_results






# ============================================================================
# RUNNER ENTRY POINTS
# ============================================================================


# ============================================================================
# AGENT RUNNER (Linear Pipeline — 3 AI Calls)
# ============================================================================



# ---------------------------------------------------------------------------
# Pass-2 Recovery Support
# ---------------------------------------------------------------------------


def _recover_top_documents_globally(query: str, db: Session, top_k: int = 12) -> list[dict]:
    """Search master_docs globally to recover the best documents across all collections."""
    logger.info("[Pass-2] Recovering top documents globally for: %s", query[:60])
    try:
        master_mr = query_master_collection(query_text=query, n_results=top_k * 2)
        ids = master_mr.get("ids") and master_mr["ids"][0]
        doc_ids = [int(i) for i in ids if str(i).isdigit()] if ids else []

        exact_phrases = _extract_exact_query_phrases(query)
        q_terms = set(_tokenize(query))
        all_processed_docs = db.query(Document).filter(Document.processed == True).all()
        if exact_phrases or q_terms:
            phrase_match_ids: list[int] = []
            lexical_scored: list[tuple[int, int]] = []
            for d in all_processed_docs:
                desc_content = _parse_doc_description(d)
                if isinstance(desc_content, dict):
                    try:
                        desc_text = json.dumps(desc_content, ensure_ascii=False)
                    except Exception:
                        desc_text = str(desc_content)
                else:
                    desc_text = str(desc_content or "")
                searchable = f"{d.title or ''} {d.file_name or ''} {desc_text}"
                searchable_lower = searchable.lower()
                if exact_phrases and any(p in searchable_lower for p in exact_phrases):
                    phrase_match_ids.append(int(d.id))
                if q_terms:
                    overlap = len(q_terms & set(_tokenize(searchable)))
                    if overlap >= 2:
                        lexical_scored.append((int(d.id), overlap))
            if phrase_match_ids:
                seen_phrase = set(phrase_match_ids)
                doc_ids = phrase_match_ids + [doc_id for doc_id in doc_ids if doc_id not in seen_phrase]
            if lexical_scored:
                lexical_scored.sort(key=lambda item: item[1], reverse=True)
                lexical_ids = [doc_id for doc_id, _score in lexical_scored[: max(top_k * 3, 8)]]
                seen_lexical = set(lexical_ids)
                doc_ids = lexical_ids + [doc_id for doc_id in doc_ids if doc_id not in seen_lexical]
            doc_ids = doc_ids[: max(top_k * 3, top_k)]

        if not doc_ids:
            return []

        all_docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
        doc_dict = {doc.id: doc for doc in all_docs}
        
        recovered = []
        for did in doc_ids:
            if did in doc_dict:
                doc = doc_dict[did]
                desc = _parse_doc_description(doc)
                recovered.append({
                    "document_id": did,
                    "title": doc.title,
                    "file_name": doc.file_name,
                    "description": desc,
                    "collection": _resolve_document_collection_name(doc, db)
                })
        return recovered[:top_k]
    except Exception as e:
        logger.warning("[Pass-2] Global document recovery failed: %s", e)
        return []


def _get_chunks_for_recovered_docs(
    query: str,
    recovered_docs: list[dict],
    db: Session,
    chunks_per_doc: int = 5,
    anchors: list[str] | None = None,
    entities: list[str] | None = None,
    answer_mode: str | None = None,
) -> dict:
    """Fetch chunks for recovered documents, grouped by collection."""
    by_col: dict[str, list[int]] = {}
    for d in recovered_docs:
        col = d["collection"] or "__auto__"
        by_col.setdefault(col, []).append(d["document_id"])
        
    all_chunks = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(by_col), 4)) as ex:
        futs = {
            ex.submit(
                get_document_chunks_exec,
                user_query=query,
                collection_name=col,
                document_ids=dids,
                chunks_per_doc=chunks_per_doc,
                anchor_terms=anchors,
                named_entities=entities,
                answer_mode=answer_mode,
                retrieval_stage="pass2_recovery",
                session=db
            ): col
            for col, dids in by_col.items()
        }
        for fut in concurrent.futures.as_completed(futs):
            chunks = fut.result()
            if chunks:
                metadata = chunks.get("_metadata") if isinstance(chunks, dict) else None
                for key, value in chunks.items():
                    if key == "_metadata": continue
                    all_chunks[key] = value
                if metadata:
                    all_chunks.setdefault("_metadata", {"per_doc": []})
                    all_chunks["_metadata"]["retrieval_stage"] = "pass2_recovery"
                    all_chunks["_metadata"]["per_doc"].extend(metadata.get("per_doc") or [])
    return all_chunks


def _stitch_chunks(
    chunks_data: dict,
    *,
    query: str,
    anchors: list[str] | None = None,
    entities: list[str] | None = None,
    answer_mode: str | None = None,
    chunks_per_doc: int = 5,
) -> dict:
    """Enriches context by fetching adjacent chunks for retrieved chunks."""
    logger.info("[Pass-2] Stitching adjacent chunks for context enrichment")
    stitched_data = copy.deepcopy(chunks_data)
    stitched_meta = stitched_data.get("_metadata") if isinstance(stitched_data.get("_metadata"), dict) else {}
    stitched_meta.setdefault("retrieval_stage", "pass2_stitch")
    stitched_meta.setdefault("per_doc", [])
    
    for col_name, docs in stitched_data.items():
        if col_name == "_metadata":
            continue
        if not col_name or col_name in {"__auto__", "auto", "*"}:
            continue
        for doc_entry in docs:
            doc_id = doc_entry.get("document_id")
            chunks = doc_entry.get("chunks", [])
            if not doc_id or not chunks:
                continue
            
            try:
                new_chunks = list(chunks)
                for ch in chunks:
                    metadata = _get_chunk_metadata(ch if isinstance(ch, dict) else {})
                    chunk_index = metadata.get("chunk_index")
                    section_path = metadata.get("section_path")
                    text = _get_chunk_text(ch if isinstance(ch, dict) else str(ch))
                    
                    is_weak = len(text.split()) < 20 or text.endswith(":") or (section_path and section_path.lower() in text.lower())
                    
                    if is_weak and chunk_index is not None:
                        try:
                            # Fetch neighbors (index -1 and +1)
                            # Chroma filter for document_id and chunk_index
                            # Note: Chroma expects metadata values to match type (int or str)
                            col = chroma_client.get_collection(_resolve_existing_collection_name(col_name))
                            
                            # Fetch prev
                            p_idx = int(chunk_index) - 1
                            if p_idx >= 0:
                                prev = col.get(where={"$and": [{"document_id": int(doc_id)}, {"chunk_index": p_idx}]}, include=["documents", "metadatas"])
                                if prev.get("ids"):
                                    prev_chunk = _build_canonical_chunk(
                                        chunk={
                                            "chunk_text": f"[PREV CONTEXT]: {prev['documents'][0]}",
                                            "metadata": prev["metadatas"][0],
                                            "rrf_score": 0.0,
                                        },
                                        collection_name=col_name,
                                        document_id=int(doc_id),
                                        document_title=str(doc_entry.get("title") or doc_entry.get("file_name") or ""),
                                        user_query=query,
                                        anchors=anchors,
                                        entities=entities,
                                        answer_mode=answer_mode,
                                        retrieval_stage="pass2_stitch",
                                        exact_phrases=_extract_exact_query_phrases(query),
                                        base_reason="adjacent_context",
                                    )
                                    new_chunks.append(prev_chunk)
                            
                            # Fetch next
                            n_idx = int(chunk_index) + 1
                            nxt = col.get(where={"$and": [{"document_id": int(doc_id)}, {"chunk_index": n_idx}]}, include=["documents", "metadatas"])
                            if nxt.get("ids"):
                                next_chunk = _build_canonical_chunk(
                                    chunk={
                                        "chunk_text": f"[NEXT CONTEXT]: {nxt['documents'][0]}",
                                        "metadata": nxt["metadatas"][0],
                                        "rrf_score": 0.0,
                                    },
                                    collection_name=col_name,
                                    document_id=int(doc_id),
                                    document_title=str(doc_entry.get("title") or doc_entry.get("file_name") or ""),
                                    user_query=query,
                                    anchors=anchors,
                                    entities=entities,
                                    answer_mode=answer_mode,
                                    retrieval_stage="pass2_stitch",
                                    exact_phrases=_extract_exact_query_phrases(query),
                                    base_reason="adjacent_context",
                                )
                                new_chunks.append(next_chunk)
                        except Exception as e:
                            logger.debug("[Pass-2] Stitching fetch failed for doc %s idx %s: %s", doc_id, chunk_index, e)

                selected_chunks, selection_debug = _select_lossless_chunks_for_doc(
                    candidates=[c for c in new_chunks if isinstance(c, dict)],
                    user_query=query,
                    anchors=anchors,
                    entities=entities,
                    answer_mode=answer_mode,
                    per_doc_limit=max(chunks_per_doc, len(chunks)),
                )
                doc_entry["chunks"] = selected_chunks
                doc_entry["chunk_debug"] = selection_debug
                stitched_meta["per_doc"].append(
                    {
                        "document_id": int(doc_id),
                        "collection_name": col_name,
                        "stitched": True,
                        **selection_debug,
                    }
                )
            except Exception as e:
                logger.warning("[Pass-2] Stitching failed for doc %s: %s", doc_id, e)

    stitched_data["_metadata"] = stitched_meta
    return stitched_data


def run_agent(query: str):
    """Main entry point.

    Runs the tool-calling agent loop (agentic RAG).
    """
    return run_agent_agentic(query)


def _has_document_results(documents: dict | None) -> bool:
    return bool(
        isinstance(documents, dict)
        and any(isinstance(v, list) and v for k, v in documents.items() if k != "_metadata")
    )


def _merge_document_outputs(outputs: list[dict]) -> dict:
    valid = [o for o in outputs if isinstance(o, dict) and _has_document_results(o)]
    return _merge_docs(valid) if valid else {}


def _choose_document_targets(
    documents: dict,
    query: str,
    named_entities: list[str] | None = None,
    answer_mode: str | None = None,
) -> tuple[str, list[int]]:
    valid_cols = [k for k, v in (documents or {}).items() if k != "_metadata" and isinstance(v, list) and v]
    if not valid_cols:
        return "__auto__", []

    if len(valid_cols) == 1 and answer_mode not in {"aggregate", "compare", "timeline"}:
        chosen_col = valid_cols[0]
        chosen_docs = [
            d for d in (documents.get(chosen_col) or [])
            if isinstance(d, dict) and isinstance(d.get("document_id"), int)
        ]
        return chosen_col, [int(d["document_id"]) for d in chosen_docs]

    merged_docs: list[dict] = []
    for c in valid_cols:
        for d in (documents.get(c) or []):
            if isinstance(d, dict) and isinstance(d.get("document_id"), int):
                merged_docs.append({
                    "document_id": int(d["document_id"]),
                    "collection": c,
                    "boost": int(d.get("metadata_support_boost", 0)),
                    "doc": d,
                })
    merged_docs.sort(key=lambda x: x["boost"], reverse=True)

    chosen_doc_ids: list[int] = []
    seen_ids: set[int] = set()
    for c in valid_cols:
        col_docs = [m for m in merged_docs if m.get("collection") == c]
        if not col_docs:
            continue
        did = int(col_docs[0]["document_id"])
        if did not in seen_ids:
            seen_ids.add(did)
            chosen_doc_ids.append(did)

    for entity in named_entities or []:
        for item in merged_docs:
            did = int(item["document_id"])
            if did in seen_ids:
                continue
            if _doc_supports_entity(item.get("doc") or {}, entity):
                seen_ids.add(did)
                chosen_doc_ids.append(did)
                break

    target_docs = 6 if answer_mode in {"aggregate", "compare", "timeline"} else 4
    for item in merged_docs:
        did = int(item["document_id"])
        if did in seen_ids:
            continue
        seen_ids.add(did)
        chosen_doc_ids.append(did)
        if len(chosen_doc_ids) >= target_docs:
            break

    return "__auto__", chosen_doc_ids


def _planner_requests_retry(
    chunks_data: dict | None,
    retry_policy: dict[str, Any] | None,
    anchors: list[str] | None = None,
    entities: list[str] | None = None,
) -> bool:
    if not chunks_data or not isinstance(retry_policy, dict):
        return False
    coverage = _summarize_chunk_coverage(chunks_data, anchors=anchors, entities=entities)
    confidence = _compute_retrieval_confidence(chunks_data)
    if retry_policy.get("retry_on_no_anchor") and coverage["anchor_hit_chunks"] == 0:
        return True
    if retry_policy.get("retry_on_missing_entity"):
        distinct_entities = [e for e in dict.fromkeys(entities or []) if e and e.strip()]
        if len(distinct_entities) >= 2:
            supported = sum(1 for e in distinct_entities if coverage["entity_hits"].get(e, 0) > 0)
            if supported < len(distinct_entities):
                return True
    if retry_policy.get("retry_on_low_confidence") and confidence.get("top3_avg_rrf", 0.0) < 0.015:
        return True
    return False


def run_agent_agentic(query: str) -> str:
    """Deterministic retrieval runner with one planner call and one final answer call."""
    db = SessionLocal()
    try:
        return _run_agent_agentic_internal(query, db)
    finally:
        db.close()

def _prepare_context(chunks_data: dict | None, answer_mode: str) -> tuple[str, dict[str, Any]]:
    """Prepare and trim final-answer context, with debug details for selected and omitted chunks."""
    max_total_chars = int(os.getenv("ASKMOJO_MAX_CONTEXT_CHARS", "20000"))
    if answer_mode in {"aggregate", "compare", "timeline"}:
        max_total_chars = max(max_total_chars, 25000)

    doc_entries = []
    for col_name, docs in (chunks_data or {}).items():
        if col_name == "_metadata":
            continue
        if not isinstance(docs, list):
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            doc_label = str(doc.get("title") or doc.get("file_name") or "").strip()
            if not doc_label:
                continue
            doc_chunks = doc.get("chunks") or []
            if doc_chunks:
                ordered_chunks = sorted(
                    [ch for ch in doc_chunks if isinstance(ch, dict)],
                    key=lambda item: float(
                        item.get(
                            "rerank_score",
                            item.get("final_score", item.get("rrf_score", 0.0))
                        ) or 0.0
                    ),
                    reverse=True,
                )
                doc_entries.append({
                    "label": doc_label,
                    "collection_name": col_name,
                    "document_id": doc.get("document_id"),
                    "chunks": ordered_chunks,
                    "chunk_debug": doc.get("chunk_debug") if isinstance(doc.get("chunk_debug"), dict) else {},
                })

    if not doc_entries:
        return "", {
            "max_total_chars": max_total_chars,
            "total_chunks_available": 0,
            "selected_chunks_count": 0,
            "omitted_chunks_count": 0,
            "selected_chunks": [],
            "omitted_chunks": [],
            "documents": [],
        }

    selected_texts = []
    current_chars = 0
    total_chunks_initial = sum(len(doc.get("chunks") or []) for doc in doc_entries)
    selected_chunk_summaries: list[dict[str, Any]] = []
    omitted_chunk_summaries: list[dict[str, Any]] = []
    per_document_debug: list[dict[str, Any]] = []

    for doc in doc_entries:
        doc_chunks = doc.get("chunks") or []
        chunk_debug = doc.get("chunk_debug") or {}
        per_document_debug.append({
            "document_title": doc.get("label"),
            "document_id": doc.get("document_id"),
            "collection_name": doc.get("collection_name"),
            "chunks_available_for_final_answer": len(doc_chunks),
            "retrieval_chunks_removed_by_budget": int(chunk_debug.get("chunks_removed_by_budget", 0) or 0),
            "retrieval_selected_chunk_ids": list(chunk_debug.get("selected_chunk_ids") or []),
            "retrieval_top_dropped_chunks": list(chunk_debug.get("top_dropped_chunks") or []),
        })

    if answer_mode == "compare":
        while doc_entries and current_chars < max_total_chars:
            made_progress = False
            for doc in list(doc_entries):
                if not doc["chunks"]:
                    doc_entries.remove(doc)
                    continue
                ch = doc["chunks"].pop(0)
                text = _get_chunk_text(ch if isinstance(ch, dict) else str(ch))
                t = _format_chunk_for_prompt(text, preserve_structure=True)
                if not t:
                    continue
                formatted = f"\nDocument: {doc['label']}\nContent: {t}\n---\n"
                if current_chars + len(formatted) > max_total_chars and current_chars > 0:
                    omitted_chunk_summaries.append({
                        **_chunk_debug_summary(ch),
                        "omission_stage": "final_context_char_budget",
                        "omission_reason": "max_total_chars_exceeded",
                    })
                    for remaining in doc["chunks"]:
                        omitted_chunk_summaries.append({
                            **_chunk_debug_summary(remaining),
                            "omission_stage": "final_context_char_budget",
                            "omission_reason": "not_reached_after_budget_cutoff",
                        })
                    for leftover_doc in doc_entries:
                        if leftover_doc is doc:
                            continue
                        for leftover_chunk in leftover_doc.get("chunks") or []:
                            omitted_chunk_summaries.append({
                                **_chunk_debug_summary(leftover_chunk),
                                "omission_stage": "final_context_char_budget",
                                "omission_reason": "not_reached_after_budget_cutoff",
                            })
                    doc_entries.clear()
                    break
                selected_texts.append(formatted)
                selected_chunk_summaries.append({
                    **_chunk_debug_summary(ch),
                    "context_chars": len(formatted),
                })
                current_chars += len(formatted)
                made_progress = True
            if not made_progress:
                break
    else:
        for doc_idx, doc in enumerate(doc_entries):
            for chunk_idx, ch in enumerate(doc["chunks"]):
                text = _get_chunk_text(ch if isinstance(ch, dict) else str(ch))
                t = _format_chunk_for_prompt(text, preserve_structure=True)
                if not t:
                    continue
                formatted = f"\nDocument: {doc['label']}\nContent: {t}\n---\n"
                if current_chars + len(formatted) > max_total_chars and current_chars > 0:
                    omitted_chunk_summaries.append({
                        **_chunk_debug_summary(ch),
                        "omission_stage": "final_context_char_budget",
                        "omission_reason": "max_total_chars_exceeded",
                    })
                    remaining_chunks = doc["chunks"][chunk_idx + 1:]
                    for remaining in remaining_chunks:
                        omitted_chunk_summaries.append({
                            **_chunk_debug_summary(remaining),
                            "omission_stage": "final_context_char_budget",
                            "omission_reason": "not_reached_after_budget_cutoff",
                        })
                    break
                selected_texts.append(formatted)
                selected_chunk_summaries.append({
                    **_chunk_debug_summary(ch),
                    "context_chars": len(formatted),
                })
                current_chars += len(formatted)
            if current_chars >= max_total_chars:
                remaining_docs = doc_entries[doc_idx + 1:]
                for remaining_doc in remaining_docs:
                    for remaining_chunk in remaining_doc.get("chunks") or []:
                        omitted_chunk_summaries.append({
                            **_chunk_debug_summary(remaining_chunk),
                            "omission_stage": "final_context_char_budget",
                            "omission_reason": "not_reached_after_budget_cutoff",
                        })
                break

    context_debug = {
        "max_total_chars": max_total_chars,
        "context_chars_used": current_chars,
        "total_chunks_available": total_chunks_initial,
        "selected_chunks_count": len(selected_chunk_summaries),
        "omitted_chunks_count": len(omitted_chunk_summaries),
        "retrieval_chunks_removed_by_budget_total": sum(int((d.get("retrieval_chunks_removed_by_budget") or 0)) for d in per_document_debug),
        "selected_chunks": selected_chunk_summaries,
        "omitted_chunks": omitted_chunk_summaries,
        "documents": per_document_debug,
    }
    return "".join(selected_texts), context_debug

def _run_agent_agentic_internal(query: str, db: Session) -> str:
    logger.info("[Agentic] Starting deterministic retrieval pipeline for: %s", query[:80])
    start_time_total = time.time()
    query_type = detect_query_type(query)

    _TOON_USAGE_STORE.set({"calls": []})
    _OPENAI_TOKEN_STORE.set({"total": 0, "by_label": {}})

    agent_history: dict[str, Any] = {
        "steps": [],
        "answer": None,
        "total_tokens_used": 0,
        "total_time_seconds": 0.0,
    }

    def _add_step(step: dict[str, Any]) -> None:
        tokens = int(step.get("tokens_used", 0) or 0)
        agent_history["steps"].append(step)
        agent_history["total_tokens_used"] += tokens

    def _finalize_answer(answer: str) -> str:
        agent_history["answer"] = answer
        agent_history["total_time_seconds"] = round(time.time() - start_time_total, 2)
        try:
            store = _get_toon_store()
            calls = store.get("calls") or []
            total_json = sum(int(c.get("json_tokens", 0) or 0) for c in calls if isinstance(c, dict))
            total_toon = sum(int(c.get("toon_tokens", 0) or 0) for c in calls if isinstance(c, dict))
            total_savings = total_json - total_toon
            total_pct = (total_savings / total_json * 100.0) if total_json > 0 else 0.0
            toon_by_call = _aggregate_toon_usage_by_call(calls)
            openai_store = _get_token_store()
            openai_total = int(openai_store.get("total", 0) or 0)
            openai_by_label = openai_store.get("by_label") or {}

            agent_history["toon_token_usage"] = {
                "total_json_tokens": total_json,
                "total_toon_tokens": total_toon,
                "total_savings": total_savings,
                "total_savings_percent": round(total_pct, 2),
                "breakdown_by_call": [
                    {
                        "call": c.get("call_name"),
                        "tokens_used": c.get("toon_tokens"),
                        "tokens_without_toon": c.get("json_tokens"),
                        "savings": c.get("savings"),
                        "savings_percent": round(float(c.get("savings_percent", 0.0) or 0.0), 2),
                    }
                    for c in calls
                    if isinstance(c, dict)
                ],
            }
            agent_history["toon_savings"] = {
                "by_call": calls,
                "total_savings": total_savings,
                "total_savings_percent": round(total_pct, 2),
            }
            agent_history["openai_non_embedding_token_usage"] = {
                "excludes_embedding_tokens": True,
                "total_tokens_used": openai_total,
                "estimated_total_tokens_without_toon": openai_total + sum(
                    int((toon_by_call.get(label) or {}).get("savings", 0) or 0)
                    for label in openai_by_label.keys()
                ),
                "total_toon_savings_applied": sum(
                    int((toon_by_call.get(label) or {}).get("savings", 0) or 0)
                    for label in openai_by_label.keys()
                ),
                "breakdown_by_call": [
                    {
                        "call": label,
                        "tokens_used": int(actual_tokens or 0),
                        "estimated_tokens_without_toon": int(actual_tokens or 0) + int((toon_by_call.get(label) or {}).get("savings", 0) or 0),
                        "toon_savings": int((toon_by_call.get(label) or {}).get("savings", 0) or 0),
                    }
                    for label, actual_tokens in sorted(openai_by_label.items())
                ],
            }
        except Exception:
            pass
        try:
            agent_history["tool_timing_summary"] = _build_tool_timing_summary(agent_history.get("steps") or [])
        except Exception:
            pass
        try:
            logger.info(
                "[Agentic] Completed query in %.2fs | total_tokens=%s | steps=%s",
                agent_history.get("total_time_seconds", 0.0),
                agent_history.get("total_tokens_used", 0),
                len(agent_history.get("steps") or []),
            )
            openai_usage = agent_history.get("openai_non_embedding_token_usage") or {}
            logger.info(
                "[Agentic] OpenAI non-embedding tokens | with_toon=%s | without_toon=%s | toon_savings=%s",
                openai_usage.get("total_tokens_used", 0),
                openai_usage.get("estimated_total_tokens_without_toon", 0),
                openai_usage.get("total_toon_savings_applied", 0),
            )
            for i, step in enumerate(agent_history.get("steps") or []):
                logger.info(
                    "[Agentic] Step %d | %s | time=%.4fs | tokens=%s",
                    i + 1,
                    step.get("tool"),
                    float(step.get("time_taken_seconds", 0.0) or 0.0),
                    step.get("tokens_used", 0),
                )
        except Exception:
            pass
        return json.dumps(agent_history, indent=2)

    def _gate_fast_paths() -> str | None:
        q_lower = (query or "").lower()
        q_norm = re.sub(r"[^a-z0-9\s]", " ", q_lower)
        q_norm = re.sub(r"\s+", " ", q_norm).strip()

        def _detect_metadata_fallback_type() -> str | None:
            count_terms = re.search(r"\b(how many|count|number of|total)\b", q_norm)
            registry_terms = re.search(
                r"\b(documents?|docs?|files?|domains?|collections?|categories?|items?|entries?|records?|proposals?|cases?\s+stud(?:y|ies)|solutions?|services?|policies?)\b",
                q_norm,
            )
            time_units = re.search(r"\b(years?|months?|weeks?|days?|hours?|minutes?|seconds?)\b", q_norm)
            if count_terms and (registry_terms or re.search(r"\b(do you have|we have|in total|overall)\b", q_norm)):
                if time_units and not registry_terms:
                    return None
                return "COUNT"

            if re.search(r"\b(list|show)\b", q_norm) and re.search(
                r"\b(documents?|docs?|files?|domains?|collections?|categories?)\b", q_norm
            ):
                return "LIST"
            if re.search(r"\b(which|what)\s+(documents?|docs?|files?)\b", q_norm):
                return "LIST"
            if re.search(r"\b(is there|do we have|do you have|do u have|exists|are there)\b", q_norm):
                return "EXISTENCE"
            return None

        if _is_specific_cost_question(query):
            refusal = (
                "I cannot disclose specific project budgets or numeric costs. "
                "For pricing details, please refer to the project handling team."
            )
            _add_step({
                "tool": "Policy: Specific Pricing Non-Disclosure",
                "input": {"query": query},
                "output": {"answer": refusal},
                "time_taken_seconds": 0.0,
                "model_used": "policy_guard",
                "tokens_used": 0,
            })
            return _finalize_answer(refusal)

        route_info, route_elapsed = _run_timed_call(classify_intent_light, query)
        _add_step({
            "tool": "Agent Decision: Intent Route (Light)",
            "input": {"query": query},
            "output": route_info,
            "time_taken_seconds": route_elapsed,
            "model_used": "intent_router_light",
            "tokens_used": 0,
        })

        if route_info.get("route") != "METADATA":
            fallback_type = _detect_metadata_fallback_type()
            if fallback_type:
                route_info = {
                    "route": "METADATA",
                    "type": fallback_type,
                    "source": "fallback_sql_guard",
                }
                logger.info(
                    "[Agentic] Metadata fallback guard activated | type=%s | query=%s",
                    fallback_type,
                    query[:120],
                )
                _add_step({
                    "tool": "Agent Decision: Metadata Guard Override",
                    "input": {"query": query},
                    "output": route_info,
                    "time_taken_seconds": 0.0,
                    "model_used": "rule_guard",
                    "tokens_used": 0,
                })

        if route_info.get("route") == "METADATA":
            t = (route_info.get("type") or "").upper()
            logger.info("[Agentic] SQL-only metadata route selected | type=%s", t)
            if t == "CONVERSATIONAL":
                return _finalize_answer(handle_conversational(query))

            from app.sqlite.models import Category
            categories = db.query(Category).all()
            metadata_hints = extract_metadata_hints(query, t)
            answer = None
            if t == "COUNT":
                answer = handle_count(query, db, categories, metadata_hints)
            elif t == "EXISTENCE":
                answer = handle_existence(query, db, categories, metadata_hints.get("doc_hint"), metadata_hints)
            elif t == "LIST":
                answer = handle_listing(query, db, categories, metadata_hints)

            if answer:
                _add_step({
                    "tool": "Metadata Intent Dispatch",
                    "input": {**route_info, "hints": metadata_hints},
                    "output": {"answer": answer},
                    "time_taken_seconds": 0.0,
                    "model_used": "N/A",
                    "tokens_used": 0,
                })
                return _finalize_answer(answer)

        if _is_collection_structure_question(query):
            collections_payload = get_all_collections()
            best, debug = _pick_best_collection_from_metadata(query, collections_payload)
            _add_step({
                "tool": "Tool Execution: get_all_collections",
                "input": {},
                "output": collections_payload,
                "time_taken_seconds": 0.0,
                "model_used": "N/A",
                "tokens_used": 0,
            })
            agent_history["decision_debug"] = {"mode": "system_structure", "selection": debug}
            return _finalize_answer(best or "I could not find relevant information in the available documents.")

        if _is_document_inventory_question(query):
            answer = _build_document_inventory_answer(db)
            _add_step({
                "tool": "Tool Execution: list_documents_from_registry",
                "input": {"question": query},
                "output": {"mode": "metadata_only"},
                "time_taken_seconds": 0.0,
                "model_used": "N/A",
                "tokens_used": 0,
            })
            return _finalize_answer(answer)

        return None
    fast_path_result = _gate_fast_paths()
    if fast_path_result:
        return fast_path_result

    state: dict[str, Any] = {"user_query": query, "query_type": query_type}
    semantic_query = _normalize_rag_query(query)
    state["semantic_query"] = semantic_query
    hints: dict[str, Any] = {}

    # ── Merged LLM call: query rewrite + anchor extraction (1 call instead of 2) ──
        # ── Unified Planning: query rewrite + anchor extraction + strategy (1 call instead of 2-3) ──
    planner_started = time.perf_counter()
    with _track_openai_tokens("unified_planning"):
        cats = db.query(Category).all()
        available = sorted({(c.collection_name or "").strip() for c in cats if c and c.collection_name})
        cat_descriptors = {}
        for _c in cats:
            _col = (_c.collection_name or "").strip()
            if not _col: continue
            try:
                _desc = json.loads(_c.description) if isinstance(_c.description, str) else (_c.description or {})
                cat_descriptors[_col] = {
                    "summary": str(_desc.get("summary") or _desc.get("description") or "")[:300],
                    "routing_hint": str(_desc.get("routing_hint") or "")[:200],
                    "primary_topics": (_desc.get("primary_topics") or [])[:8],
                    "doc_types": (_desc.get("doc_types") or [])[:6]
                }
            except Exception: pass

        planner = unified_planning_exec(semantic_query, available, cat_descriptors, hints=hints)
    
    planner_elapsed = round(time.perf_counter() - planner_started, 4)
    rewritten_query = planner.get("rewritten_query") or semantic_query
    
    state["planner"] = planner
    state["router_collections"] = planner.get("collections") or []
    state["router_anchors"] = planner.get("anchors") or planner.get("anchor_terms") or []
    state["named_entities"] = planner.get("named_entities") or []
    state["answer_mode"] = planner.get("answer_mode") or "direct"

    _add_step({
        "tool": "Agent Decision: Unified Planning (Merged)",
        "input": {"query": query, "available_collections": available},
        "output": planner,
        "time_taken_seconds": planner_elapsed,
        "model_used": planner.get("_planner_model", "gpt-4o-mini"),
        "tokens_used": int(planner.get("_planner_tokens", 0) or _last_tracked_token_delta()),
    })

    filters = None
    doc_outputs: list[dict] = []
    collections_to_try = _normalize_collection_list(planner.get("collections") or [])
    if not collections_to_try and available:
        collections_to_try = list(available)

    top_k = 3 if state["answer_mode"] in {"aggregate", "compare", "timeline"} else 2
    tool2_parallel = state["answer_mode"] in {"aggregate", "compare", "timeline"} and len(collections_to_try) > 1
    tool2_workers = _parallel_worker_count(
        len(collections_to_try),
        env_var="ASKMOJO_TOOL2_MAX_WORKERS",
        default_max=3,
    ) if tool2_parallel else 1
    collection_outputs: list[tuple[str, dict, float]] = []
    if tool2_workers > 1:
        logger.info("[Tool 2 Exec] Parallel per-collection retrieval enabled with %d workers", tool2_workers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=tool2_workers) as ex:
            future_map = {
                collection_name: ex.submit(
                    _run_timed_call,
                    get_top_documents_from_collection_exec,
                    user_query=query,
                    selected_collection=collection_name,
                    top_k=top_k,
                    filters=filters,
                    doc_queries=planner.get("doc_queries") or [query],
                    router_collections=planner.get("collections") or [],
                    anchor_terms=state["router_anchors"],
                    named_entities=state["named_entities"],
                    session=None,
                )
                for collection_name in collections_to_try
            }
            ordered_outputs: dict[str, tuple[dict, float]] = {}
            for collection_name, fut in future_map.items():
                ordered_outputs[collection_name] = fut.result()
            collection_outputs = [
                (
                    collection_name,
                    (ordered_outputs.get(collection_name) or ({}, 0.0))[0],
                    float((ordered_outputs.get(collection_name) or ({}, 0.0))[1] or 0.0),
                )
                for collection_name in collections_to_try
            ]
    else:
        for collection_name in collections_to_try:
            doc_out, tool2_elapsed = _run_timed_call(
                get_top_documents_from_collection_exec,
                user_query=query,
                selected_collection=collection_name,
                top_k=top_k,
                filters=filters,
                doc_queries=planner.get("doc_queries") or [query],
                router_collections=planner.get("collections") or [],
                anchor_terms=state["router_anchors"],
                named_entities=state["named_entities"],
                session=db
            )
            collection_outputs.append((collection_name, doc_out, tool2_elapsed))
            if state["answer_mode"] == "direct" and _has_document_results(doc_out):
                break

    for collection_name, doc_out, tool2_elapsed in collection_outputs:
        tool_tokens = 0
        if isinstance(doc_out, dict):
            tool_tokens = int(((doc_out.get("_metadata") or {}).get("tokens_used")) or 0)
        _add_step({
            "tool": "Tool Execution: get_top_documents_from_collection_exec",
            "input": {
                "user_query": query,
                "selected_collection": collection_name,
                "top_k": top_k,
                "filters": filters,
                "doc_queries": planner.get("doc_queries") or [query],
                "router_collections": planner.get("collections") or [],
                "anchor_terms": state["router_anchors"],
                "named_entities": state["named_entities"],
            },
            "output": doc_out,
            "time_taken_seconds": tool2_elapsed,
            "model_used": "N/A",
            "tokens_used": tool_tokens,
            "output_size": len(str(doc_out)),
        })
        if isinstance(doc_out, dict) and doc_out.get("error_type") == "embedding_dim_mismatch":
            agent_history["answer"] = doc_out.get("message") or _EMBEDDING_DIM_MISMATCH_HELP
            agent_history["total_time_seconds"] = round(float(time.time() - start_time_total), 2)
            return json.dumps(agent_history, indent=2)
        if _has_document_results(doc_out):
            doc_outputs.append(doc_out)

    documents = _merge_document_outputs(doc_outputs)
    state["documents"] = documents
    if not _has_document_results(documents):
        agent_history["answer"] = "No relevant info found"
        agent_history["retrieved_chunks"] = {}
        agent_history["total_time_seconds"] = round(float(time.time() - start_time_total), 2)
        return json.dumps(agent_history, indent=2)

    chunk_collection_name, document_ids = _choose_document_targets(
        documents,
        query,
        named_entities=state["named_entities"],
        answer_mode=state["answer_mode"],
    )
    chunks_per_doc = 5 if (query_type in {"solution", "summary"} or state.get("answer_mode") == "aggregate") else 4
    chunk_dense_queries = planner.get("chunk_dense_queries") or [query]
    chunk_sparse_queries = planner.get("chunk_sparse_queries") or [query]
    chunk_args: dict[str, Any] = {
        "user_query": query,
        "collection_name": chunk_collection_name,
        "document_ids": document_ids,
        "chunks_per_doc": chunks_per_doc,
        "filters": filters,
        "chunk_dense_queries": chunk_dense_queries,
        "chunk_sparse_queries": chunk_sparse_queries,
        "anchor_terms": state["router_anchors"],
        "named_entities": state["named_entities"],
        "answer_mode": state["answer_mode"],
        "retrieval_stage": "pass1",
    }
    chunks, chunk_elapsed = _run_timed_call(
        get_document_chunks_exec,
        user_query=query,
        collection_name=chunk_collection_name,
        document_ids=document_ids,
        chunks_per_doc=chunks_per_doc,
        filters=filters,
        chunk_dense_queries=chunk_dense_queries,
        chunk_sparse_queries=chunk_sparse_queries,
        anchor_terms=state["router_anchors"],
        named_entities=state["named_entities"],
        answer_mode=state["answer_mode"],
        retrieval_stage="pass1",
        session=db
    )
    chunk_tool_tokens = 0
    if isinstance(chunks, dict):
        chunk_tool_tokens = int(((chunks.get("_metadata") or {}).get("tokens_used")) or 0)
    _add_step({
        "tool": "Tool Execution: get_document_chunks_exec",
        "input": chunk_args,
        "output": chunks,
        "time_taken_seconds": chunk_elapsed,
        "model_used": "N/A",
        "tokens_used": chunk_tool_tokens,
        "output_size": len(str(chunks)),
    })
    state["chunks"] = chunks

    if _planner_requests_retry(
        chunks,
        planner.get("retry_policy"),
        anchors=state["router_anchors"],
        entities=state["named_entities"],
    ):
        retry_dense_queries = list(planner.get("chunk_dense_queries") or [query])
        retry_sparse_queries = list(planner.get("chunk_sparse_queries") or [query])
        hyde_query = _hyde(query, hints)
        if hyde_query and hyde_query not in retry_dense_queries:
            retry_dense_queries.append(hyde_query)
        if hyde_query and hyde_query not in retry_sparse_queries:
            retry_sparse_queries.append(hyde_query)
        retry_args: dict[str, Any] = dict(chunk_args)
        retry_args["chunks_per_doc"] = min(max(int(str(chunk_args.get("chunks_per_doc", 6))), 6), 7)
        retry_args["chunk_dense_queries"] = retry_dense_queries
        retry_args["chunk_sparse_queries"] = retry_sparse_queries
        retry_chunks, retry_chunk_elapsed = _run_timed_call(
            get_document_chunks_exec,
            user_query=query,
            collection_name=chunk_collection_name,
            document_ids=document_ids,
            chunks_per_doc=retry_args["chunks_per_doc"],
            filters=filters,
            chunk_dense_queries=retry_dense_queries,
            chunk_sparse_queries=retry_sparse_queries,
            anchor_terms=state["router_anchors"],
            named_entities=state["named_entities"],
            answer_mode=state["answer_mode"],
            retrieval_stage="pass1",
            session=db
        )
        retry_chunk_tokens = 0
        if isinstance(retry_chunks, dict):
            retry_chunk_tokens = int(((retry_chunks.get("_metadata") or {}).get("tokens_used")) or 0)
        _add_step({
            "tool": "Tool Execution: get_document_chunks_exec (retry)",
            "input": retry_args,
            "output": retry_chunks,
            "time_taken_seconds": retry_chunk_elapsed,
            "model_used": "N/A",
            "tokens_used": retry_chunk_tokens,
            "output_size": len(str(retry_chunks)),
        })
        if isinstance(retry_chunks, dict) and retry_chunks:
            state["chunks"] = retry_chunks

    # --- Tool 3 local prune + conditional Tool 3.5 global rerank ---
    pass2_triggered = False
    answer_text = ""
    named_entities = [e for e in (state["named_entities"] or _extract_named_entities_from_query(query)) if e]
    anchor_terms = [a for a in (state["router_anchors"] or _extract_anchor_terms_from_query(query)) if a]
    answer_mode = state.get("answer_mode") or "direct"
    candidate_pool = state["chunks"]

    for attempt in ["pass1", "pass2"]:
        shortlist_k = 15 if attempt == "pass1" else 20
        (run_global_rerank, tool35_gate), tool35_gate_elapsed = _run_timed_call(
            _should_run_global_rerank,
            candidate_pool,
            answer_mode=answer_mode,
            named_entities=named_entities,
        )
        _add_step({
            "tool": f"Agent Decision: Tool 3.5 Gate ({attempt})",
            "input": {
                "answer_mode": answer_mode,
                "pass": attempt,
                "named_entities": named_entities,
            },
            "output": tool35_gate,
            "time_taken_seconds": tool35_gate_elapsed,
            "model_used": "tool35_gate",
            "tokens_used": 0,
        })

        rerank_elapsed = 0.0
        if run_global_rerank:
            (current_chunks, rerank_debug), rerank_elapsed = _run_timed_call(
                _select_reranked_chunk_payload,
                candidate_pool,
                query=query,
                top_k=shortlist_k,
                answer_mode=answer_mode,
                named_entities=named_entities,
            )
        else:
            current_chunks = candidate_pool
            rerank_debug = {
                "candidate_count": tool35_gate.get("total_chunks", 0),
                "selected_count": tool35_gate.get("total_chunks", 0),
                "top_k": shortlist_k,
                "selected_docs": tool35_gate.get("distinct_docs", 0),
                "top_scores": [],
                "skipped": True,
                "reason": tool35_gate.get("reason"),
            }

        if run_global_rerank:
            _add_step({
                "tool": f"Tool Execution: rerank_chunks_exec ({attempt})",
                "input": {
                    "query": query,
                    "answer_mode": answer_mode,
                    "top_k": shortlist_k,
                    "candidate_chunks": rerank_debug.get("candidate_count", 0),
                    "named_entities": named_entities,
                },
                "output": rerank_debug,
                "time_taken_seconds": rerank_elapsed,
                "model_used": os.getenv("ASKMOJO_RERANKER_MODEL", "jinaai/jina-reranker-v1-tiny-en"),
                "tokens_used": 0,
            })

        coverage_debug, coverage_elapsed = _run_timed_call(
            _lightweight_rerank_coverage_check,
            current_chunks,
            full_chunks_data=candidate_pool,
            named_entities=named_entities,
            answer_mode=answer_mode,
        )
        _add_step({
            "tool": f"Agent Decision: Rerank Coverage Check ({attempt})",
            "input": {
                "answer_mode": answer_mode,
                "selected_chunk_count": rerank_debug.get("selected_count", 0),
                "selected_doc_count": rerank_debug.get("selected_docs", 0),
                "top3_rerank_scores": coverage_debug.get("top3_rerank_scores", []),
            },
            "output": coverage_debug,
            "time_taken_seconds": coverage_elapsed,
            "model_used": "coverage_guard",
            "tokens_used": 0,
        })

        if not coverage_debug.get("passes"):
            if attempt == "pass1":
                pass2_triggered = True
                (expanded_pool, expand_debug), expand_elapsed = _run_timed_call(
                    _expand_pass2_candidate_pool,
                    candidate_pool,
                    query=query,
                    db=db,
                    chunks_per_doc=chunks_per_doc,
                    anchors=anchor_terms,
                    entities=named_entities,
                    answer_mode=answer_mode,
                )
                candidate_pool = expanded_pool
                state["chunks"] = expanded_pool
                _add_step({
                    "tool": "Pass-2 Fallback Triggered",
                    "input": {
                        "reason": "Rerank coverage check failed on Pass-1",
                        "coverage_reasons": coverage_debug.get("reasons", []),
                    },
                    "output": expand_debug,
                    "time_taken_seconds": expand_elapsed,
                    "model_used": "N/A",
                    "tokens_used": 0,
                })
                continue

            pass2_chunk_count = 0
            for docs in (current_chunks or {}).values():
                if not isinstance(docs, list):
                    continue
                for doc in docs:
                    if not isinstance(doc, dict):
                        continue
                    pass2_chunk_count += len(doc.get("chunks") or [])

            if pass2_chunk_count <= 0:
                logger.info("[Rerank Coverage] Pass-2 selection still insufficient and no chunks are available. Skipping GPT call.")
                break

            logger.info(
                "[Rerank Coverage] Pass-2 selection still insufficient, but %s chunks are available. Proceeding with final GPT answer generation.",
                pass2_chunk_count,
            )

        named_entities_payload, _, _, _ = _maybe_toon_payload(named_entities, call_name=f"final_answer_{attempt}", data_name="named_entities")
        anchor_terms_payload, _, _, _ = _maybe_toon_payload(anchor_terms, call_name=f"final_answer_{attempt}", data_name="anchor_terms")

        formatted_context, final_context_debug = _prepare_context(current_chunks, state["answer_mode"])
        
        # Multi-entity timeline logic (preserved)
        multi_entity_timeline = answer_mode == "timeline" and len(named_entities) >= 1
        def _build_multi_entity_timeline_answer() -> str | None:
            if not multi_entity_timeline:
                return None
            entity_lines: list[str] = []
            found_any = False
            all_entities_have_strong_duration = True
            for entity in named_entities:
                entity_best_text = ""
                entity_best_score = 0.0
                entity_has_duration = False
                for _col, docs in (current_chunks or {}).items():
                    if not isinstance(docs, list): continue
                    for d in docs:
                        if not isinstance(d, dict): continue
                        src = str(d.get("file_name") or d.get("title") or "").strip()
                        for ch in d.get("chunks", []) or []:
                            text = _get_chunk_text(ch if isinstance(ch, dict) else str(ch))
                            if not isinstance(text, str): continue
                            tl = text.lower()
                            if entity.lower() not in tl:
                                continue
                            duration_pattern_hit = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:-|to)?\s*\d*(?:\.\d+)?\s*(?:month|months|week|weeks)\b", tl))
                            answer_term_hits = sum(1 for t in ["timeline", "duration", "month", "months", "phase"] if t in tl)
                            if answer_term_hits == 0 and not duration_pattern_hit: continue
                            score = _score_chunk_with_anchors(
                                {"text": text, "rrf_score": ch.get("rrf_score", 0.0) if isinstance(ch, dict) else 0.0, "metadata": ch.get("metadata", {})},
                                query,
                                anchors=anchor_terms,
                                entities=[entity],
                                answer_mode="timeline",
                            ) + answer_term_hits + (25 if duration_pattern_hit else 0)
                            if score > entity_best_score:
                                entity_best_score = score
                                entity_best_text = _format_chunk_for_prompt(text, preserve_structure=True)
                                entity_has_duration = duration_pattern_hit
                if entity_best_text:
                    found_any = True
                    if _looks_like_table(entity_best_text) or _looks_like_list(entity_best_text):
                        entity_lines.append(f"**{entity}**\n{entity_best_text}")
                    else:
                        entity_lines.append(f"- **{entity}**: {entity_best_text}")
                    all_entities_have_strong_duration = all_entities_have_strong_duration and entity_has_duration
                else:
                    entity_lines.append(f"- **{entity}**: Not clearly mentioned in the retrieved chunks.")
                    all_entities_have_strong_duration = False
            if not found_any:
                return None
            if not all_entities_have_strong_duration:
                return None
            return "\n".join(entity_lines)

        if answer_mode == "timeline":
            timeline_ans = _build_multi_entity_timeline_answer()
            if timeline_ans:
                return _finalize_answer(timeline_ans)

        # Final Answer Generation
        partial_instruction = ""
        if answer_mode == "partial_ok":
            partial_instruction = (
                "\nPARTIAL ANSWER RULE:\n"
                "- If evidence is incomplete, answer with what is supported and explicitly state what is missing.\n"
                "- Do not return a full not-found response when partial grounded evidence exists.\n"
            )

        final_llm_elapsed = 0.0
        with _track_openai_tokens(f"final_answer_{attempt}"):
            final_response, final_llm_elapsed = _run_timed_call(
                _openai_chat_create_with_timeout,
                model="gpt-4o-mini",
                temperature=0.0,
                max_tokens=1800,
                messages=[
                    {"role": "system", "content": FINAL_ANSWER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"""ANSWER MODE: {answer_mode}
NAMED ENTITIES: {named_entities_payload}
ANCHOR TERMS: {anchor_terms_payload}

DOCUMENT CHUNKS:
{formatted_context.strip()}

USER QUESTION: {query}

{partial_instruction}
Respond using the architecture defined in the system prompt.
Include source file names exactly as requested.
"""}
                ],
            )
        
        _add_step({
            "tool": f"Agent Decision: Final Answer Generation ({attempt})",
            "input": {
                "answer_mode": answer_mode,
                "pass": attempt,
                "query": query,
                "named_entities": named_entities,
                "anchor_terms": anchor_terms,
                "final_context_debug": final_context_debug,
                "formatted_context_chunk_count": final_context_debug.get("selected_chunks_count", 0),
                "formatted_context_chars": len(formatted_context),
                "formatted_context": formatted_context,
            },
            "output": {
                "model": "gpt-4o-mini",
                "context_chars_used": final_context_debug.get("context_chars_used", 0),
                "selected_chunks_count": final_context_debug.get("selected_chunks_count", 0),
                "omitted_chunks_count": final_context_debug.get("omitted_chunks_count", 0),
                "retrieval_chunks_removed_by_budget_total": final_context_debug.get("retrieval_chunks_removed_by_budget_total", 0),
            },
            "time_taken_seconds": final_llm_elapsed,
            "model_used": "gpt-4o-mini",
            "tokens_used": _last_tracked_token_delta(),
        })
        
        answer_text = (final_response.choices[0].message.content or "").strip()

        if attempt == "pass1" and _should_expand_after_answer(
            answer_text,
            named_entities=named_entities,
            answer_mode=answer_mode,
        ):
            pass2_triggered = True
            (expanded_pool, expand_debug), expand_elapsed = _run_timed_call(
                _expand_pass2_candidate_pool,
                candidate_pool,
                query=query,
                db=db,
                chunks_per_doc=chunks_per_doc,
                anchors=anchor_terms,
                entities=named_entities,
                answer_mode=answer_mode,
            )
            candidate_pool = expanded_pool
            state["chunks"] = expanded_pool
            _add_step({
                "tool": "Pass-2 Fallback Triggered",
                "input": {
                    "reason": "Pass-1 answer signaled incomplete coverage",
                    "answer_preview": answer_text[:300],
                },
                "output": expand_debug,
                "time_taken_seconds": expand_elapsed,
                "model_used": "N/A",
                "tokens_used": 0,
            })
            continue

        return _finalize_answer(_strip_source_block(answer_text))

    # After Pass-2 (or if Pass-2 wasn't needed), handle conversational and final cleanup
    if answer_text:
        lower = answer_text.lower()
        if re.search(r"^\s*(hi|hello|hey|thanks|thank you|bye|goodbye|see you)\b", lower):
            return _finalize_answer(_strip_source_block(answer_text))
        
        # Fallback signals
        not_found_signals = ("i could not find relevant information", "not found in the", "not mentioned in the")
        if not any(signal in lower for signal in not_found_signals):
            return _finalize_answer(_strip_source_block(answer_text))

    # Catch-all: Snippet fallback if even Pass-2 didn't yield a definitive answer
    q_terms = set(_tokenize(query))
    best_text = ""
    best_source = ""
    best_score = 0
    best_anchor_hits = 0
    best_entity_hits = 0
    for _col, docs in (state.get("chunks") or {}).items():
        if _col == "_metadata":
            continue
        if not isinstance(docs, list): continue
        for d in docs:
            if not isinstance(d, dict): continue
            src = str(d.get("file_name") or d.get("title") or "").strip()
            for ch in d.get("chunks", []) or []:
                text = _get_chunk_text(ch if isinstance(ch, dict) else str(ch))
                if not isinstance(text, str): continue
                t = _format_chunk_for_prompt(text, preserve_structure=True)
                if not t: continue
                score = len(set(_tokenize(t)) & q_terms)
                anchor_hits = _text_matches_any_anchor(t, anchors=anchor_terms, entities=None)
                entity_hits = _text_matches_any_anchor(t, anchors=None, entities=named_entities)
                if score > best_score:
                    best_score = score
                    best_text = t
                    best_source = src
                    best_anchor_hits = anchor_hits
                    best_entity_hits = entity_hits

    if best_text and (best_anchor_hits > 0 or best_entity_hits > 0 or best_score >= max(5, len(q_terms) // 2)):
        return _finalize_answer(_format_fallback_answer(best_text, best_source))

    return _finalize_answer(answer_text or "I could not find relevant information in the available documents.")


def agent(query:str):
    
    # Standalone execution for testing
    test_query = query
    
    print("\n" + "="*80)
    print(f"RUNNING DEEP AGENT STANDALONE")
    print(f"Query: {test_query}")
    print("="*80 + "\n")
    
    try:
        json_report = run_agent(test_query)
        report = json.loads(json_report)
        
        # Added return for API/Slack compatibility
        # We process the prints first, then return the final result
        
        print("\n" + "-"*40)
        print("AGENT FINAL ANSWER:")
        print("-"*40)
        print(report.get("answer"))
        
        print("\n" + "-"*40)
        print("EXECUTION STEPS:")
        print("-"*40)
        for i, step in enumerate(report.get("steps", [])):
            print(f"Step {i+1}: {step['tool']} (Input: {step['input']})")
            
        return json_report
            
    except Exception as e:
        logger.error("Standalone execution failed: %s", e, exc_info=True)
        raise
