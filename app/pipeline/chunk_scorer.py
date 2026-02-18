"""
Chunk scoring, pruning, and quality assessment.

All functions here are pure — no I/O, no DB, no LLM.
"""

from __future__ import annotations

from app.schemas.retrieval import ChunkResult, DocumentResult, DataQualityAssessment
from app.utils.logging import get_logger

logger = get_logger("askmojo.pipeline.chunk_scorer")


def score_and_prune_chunks(
    chunks: list[ChunkResult],
    answer_mode: str,
) -> list[ChunkResult]:
    """
    Apply global chunk cap based on answer mode.

    This replaces the scattered trimming logic from routes.py.
    """
    if not chunks:
        return chunks

    # Global caps by mode
    caps = {
        "extract": 15,
        "brief": 20,
        "summarize": 25,
        "explain": 30,
    }
    cap = caps.get(answer_mode, 30)

    if len(chunks) > cap:
        # Sort by score (lower distance = more relevant for ChromaDB)
        sorted_chunks = sorted(chunks, key=lambda c: c.score)
        pruned = sorted_chunks[:cap]
        logger.info(
            "Pruned chunks: %d -> %d (mode=%s)",
            len(chunks), len(pruned), answer_mode,
        )
        return pruned

    return chunks


def apply_token_budget(
    chunks: list[ChunkResult],
    answer_mode: str,
    tpm_limit: int = 30000,
    avg_tokens_per_chunk: int = 800,
) -> list[ChunkResult]:
    """
    Enforce global chunk token budget.

    Estimates reserved tokens for prompt structure, system messages,
    and desired response, then trims chunks to fit.
    """
    if not chunks:
        return chunks

    # Reserve tokens
    reserved_prompt = 3000
    reserved_response = {
        "extract": 500,
        "brief": 1000,
        "summarize": 2000,
        "explain": 4000,
    }.get(answer_mode, 3000)

    reserved = reserved_prompt + reserved_response
    chunk_budget = tpm_limit - reserved
    max_chunks = max(5, int(chunk_budget / avg_tokens_per_chunk))

    if len(chunks) > max_chunks:
        sorted_chunks = sorted(chunks, key=lambda c: c.score)
        trimmed = sorted_chunks[:max_chunks]
        logger.info(
            "Token budget: trimmed %d -> %d chunks (budget=%d tokens)",
            len(chunks), len(trimmed), chunk_budget,
        )
        return trimmed

    return chunks


def assess_data_quality(
    chunks: list[ChunkResult],
    documents: list[DocumentResult],
) -> DataQualityAssessment:
    """
    Evaluate data quality based on retrieved chunks and documents.

    Pure function — no I/O.
    """
    if not chunks or not documents:
        return DataQualityAssessment(
            quality="insufficient",
            confidence_score=30,
            relevance_quality="very_low",
            total_chunks=0,
            total_documents=0,
        )

    n_chunks = len(chunks)
    n_docs = len(documents)
    avg_per_doc = n_chunks / n_docs if n_docs > 0 else 0

    # Count-based quality
    if avg_per_doc >= 5 and n_chunks >= 10:
        quality = "excellent"
        confidence = 85
    elif avg_per_doc >= 3 and n_chunks >= 5:
        quality = "good"
        confidence = 75
    elif avg_per_doc >= 1 and n_chunks >= 3:
        quality = "sufficient"
        confidence = 65
    else:
        quality = "insufficient"
        confidence = 50

    # Relevance-based adjustment (ChromaDB distance: lower = better)
    avg_distance = sum(c.score for c in chunks) / n_chunks
    if avg_distance < 0.3:
        relevance = "high"
        confidence = min(95, confidence + 5)
    elif avg_distance < 0.5:
        relevance = "medium"
    elif avg_distance < 0.7:
        relevance = "low"
        confidence = max(50, confidence - 15)
        if quality == "excellent":
            quality = "good"
    else:
        relevance = "very_low"
        confidence = max(40, confidence - 30)
        if quality in ("excellent", "good"):
            quality = "sufficient"

    logger.info(
        "Data quality: %s (confidence=%d, relevance=%s, avg_dist=%.3f, chunks=%d, docs=%d)",
        quality, confidence, relevance, avg_distance, n_chunks, n_docs,
    )

    return DataQualityAssessment(
        quality=quality,
        confidence_score=confidence,
        relevance_quality=relevance,
        avg_chunk_distance=avg_distance,
        total_chunks=n_chunks,
        total_documents=n_docs,
    )
