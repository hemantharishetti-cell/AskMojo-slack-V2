"""
Pipeline Stage 2: Retrieval.

1. Search master_docs collection for relevant documents
2. Filter documents by selected collections, entity, doc_type
3. Retrieve chunks in parallel per collection (asyncio)
4. Score and prune chunks (token budget)

NO LLM calls — eliminates the old Step 2 LLM call that was
always hardcoded to proceed_to_step3=True anyway.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

# ChromaDB index errors (e.g. "Nothing found on disk" after corrupt/restart)
try:
    import chromadb.errors as chromadb_errors
except ImportError:
    chromadb_errors = None


class ChromaDBIndexUnavailableError(Exception):
    """Raised when ChromaDB index is missing or corrupted (e.g. needs re-upload)."""
    pass

from app.schemas.intent import IntentDecision
from app.schemas.retrieval import (
    RetrievalResult,
    DocumentResult,
    ChunkResult,
    DataQualityAssessment,
)
from app.services.vector_store import query_master_collection, query_collection_with_filter
from app.services.llm import convert_to_toon
from app.utils.text import (
    normalize_collection_name,
    infer_doc_type_from_question,
    humanize_title,
)
from app.utils.logging import get_logger
from app.pipeline.chunk_scorer import (
    score_and_prune_chunks,
    assess_data_quality,
    apply_token_budget,
)

logger = get_logger("askmojo.pipeline.retrieval")


async def retrieve_documents_and_chunks(
    intent_decision: IntentDecision,
    db: Session,
) -> RetrievalResult:
    """
    Full Stage 2 retrieval pipeline.

    1. Query master_docs
    2. Filter by collection + entity + doc_type
    3. Parallel chunk retrieval per collection
    4. Token budget enforcement
    5. Data quality assessment
    """
    from app.sqlite.models import Document, Category
    from app.vector_logic.intent_router import recommend_solution, SOLUTION_KEYWORDS
    from app.vector_logic.doc_types import infer_doc_type_for_document

    refined_q = intent_decision.refined_question
    selected_colls = intent_decision.selected_collections
    entity = intent_decision.entity
    answer_mode = intent_decision.answer_mode

    # ── 1. Master doc search ────────────────────────────────────────
    top_k_docs = max(1, min(50, len(selected_colls) * 5)) if selected_colls else 10
    search_n = min(top_k_docs * 3 if selected_colls else top_k_docs, 50)

    logger.info("[RETRIEVAL] Master search: requesting %d results", search_n)
    try:
        master_results = query_master_collection(
            query_text=refined_q,
            n_results=search_n,
        )
    except Exception as e:
        # ChromaDB index missing/corrupted (e.g. "Nothing found on disk", HNSW segment error)
        err_msg = str(e).lower()
        if chromadb_errors and isinstance(e, chromadb_errors.InternalError):
            logger.warning("[RETRIEVAL] ChromaDB index error (index may need re-upload): %s", e)
            raise ChromaDBIndexUnavailableError(str(e)) from e
        if "nothing found on disk" in err_msg or "hnsw" in err_msg or "segment" in err_msg:
            logger.warning("[RETRIEVAL] ChromaDB index unavailable: %s", e)
            raise ChromaDBIndexUnavailableError(str(e)) from e
        raise

    num_ids = len(master_results["ids"][0]) if master_results.get("ids") and master_results["ids"][0] else 0
    logger.info("[RETRIEVAL] Master returned %s doc id(s)", num_ids)

    if not master_results.get("ids") or not master_results["ids"][0]:
        logger.warning("[RETRIEVAL] No master docs found")
        return RetrievalResult()

    document_ids = [int(did) for did in master_results["ids"][0]]

    # ── 2. Filter documents ─────────────────────────────────────────
    documents = db.query(Document).filter(Document.id.in_(document_ids)).all()
    doc_dict = {doc.id: doc for doc in documents}

    # Infer doc types
    doc_types: dict[int, str] = {}
    for doc in documents:
        doc_types[doc.id] = infer_doc_type_for_document(doc, db)

    # Get category map
    categories = db.query(Category).all()
    category_map = {c.id: c for c in categories}

    # Filter by selected collections
    filtered_ids = _filter_by_collections(
        document_ids, doc_dict, category_map, selected_colls,
    )

    # Soft boost by preferred doc type
    preferred_type = infer_doc_type_from_question(refined_q)
    if preferred_type:
        filtered_ids = _boost_by_doc_type(filtered_ids, doc_types, preferred_type)

    # Soft boost by solution: use Option C selected_solution when set, else fallback to keyword heuristic
    preferred_solution = getattr(intent_decision, "selected_solution", None) or recommend_solution(refined_q)
    if preferred_solution:
        filtered_ids = _boost_by_solution(
            filtered_ids, doc_dict,
            SOLUTION_KEYWORDS.get(preferred_solution, []),
        )

    # Entity-aware filtering
    if entity:
        filtered_ids = _filter_by_entity(
            filtered_ids, doc_dict, entity, answer_mode,
        )

    if not filtered_ids:
        logger.warning("No documents after filtering")
        return RetrievalResult()

    # ── 3. Build per-doc config (heuristic, no LLM) ────────────────
    doc_configs = _build_heuristic_configs(
        filtered_ids, doc_dict, doc_types, answer_mode,
    )

    # ── 4. Group by collection and retrieve chunks in parallel ──────
    docs_by_collection = _group_by_collection(
        filtered_ids, doc_dict, category_map,
    )

    all_chunks: list[ChunkResult] = []
    doc_results: list[DocumentResult] = []

    # Build document results
    for doc_id in filtered_ids:
        if doc_id not in doc_dict:
            continue
        doc = doc_dict[doc_id]
        coll_name = _get_collection_name(doc, category_map)
        idx = document_ids.index(doc_id) if doc_id in document_ids else 0
        dist = (
            float(master_results["distances"][0][idx])
            if master_results.get("distances") and idx < len(master_results["distances"][0])
            else 0.0
        )
        cfg = doc_configs.get(doc_id, {})
        doc_results.append(DocumentResult(
            document_id=doc_id,
            title=doc.title,
            collection_name=coll_name,
            description=doc.description or "",
            doc_type=doc_types.get(doc_id, "other"),
            relevance_score=dist,
            top_k_chunks=cfg.get("top_k", 5),
        ))

    # Parallel chunk retrieval
    tasks = []
    for coll_name, doc_ids_in_coll in docs_by_collection.items():
        tasks.append(
            _retrieve_chunks_for_collection(
                coll_name, doc_ids_in_coll, doc_dict,
                doc_configs, refined_q,
            )
        )

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error("Chunk retrieval error: %s", result)
                continue
            all_chunks.extend(result)

    # ── 5. Token budget + quality assessment ────────────────────────
    all_chunks = apply_token_budget(all_chunks, answer_mode)
    all_chunks = score_and_prune_chunks(all_chunks, answer_mode)
    quality = assess_data_quality(all_chunks, doc_results)

    # ── 6. Build TOON-encoded summaries for Stage 3 ─────────────────
    summaries_data = [
        {
            "document_id": dr.document_id,
            "title": dr.title,
            "collection": dr.collection_name,
            "description": dr.description[:200] if dr.description else "",
            "doc_type": dr.doc_type,
        }
        for dr in doc_results
    ]
    chunks_data = [
        {
            "document_id": c.document_id,
            "title": c.document_title,
            "text": c.chunk_text[:500],
            "page": c.page_number,
            "score": round(c.score, 4),
        }
        for c in all_chunks
    ]

    summaries_toon, summaries_json_tokens, summaries_toon_tokens = convert_to_toon(summaries_data, "retrieval", "Summaries")
    chunks_toon, chunks_json_tokens, chunks_toon_tokens = convert_to_toon(chunks_data, "retrieval", "Chunks")

    # Calculate TOON savings
    toon_savings = {
        "summaries_json_tokens": summaries_json_tokens,
        "summaries_toon_tokens": summaries_toon_tokens,
        "summaries_savings": summaries_json_tokens - summaries_toon_tokens,
        "summaries_savings_percent": (100 * (summaries_json_tokens - summaries_toon_tokens) / summaries_json_tokens) if summaries_json_tokens else 0,
        "chunks_json_tokens": chunks_json_tokens,
        "chunks_toon_tokens": chunks_toon_tokens,
        "chunks_savings": chunks_json_tokens - chunks_toon_tokens,
        "chunks_savings_percent": (100 * (chunks_json_tokens - chunks_toon_tokens) / chunks_json_tokens) if chunks_json_tokens else 0,
    }

    logger.info(
        "Retrieval complete: %d docs, %d chunks, quality=%s",
        len(doc_results), len(all_chunks), quality.quality,
    )

    return RetrievalResult(
        documents=doc_results,
        chunks=all_chunks,
        data_quality=quality,
        summaries_toon=summaries_toon,
        chunks_toon=chunks_toon,
        toon_savings=toon_savings,
    )


# ── Internal helpers ────────────────────────────────────────────────

def _filter_by_collections(
    doc_ids: list[int],
    doc_dict: dict[int, Any],
    category_map: dict[int, Any],
    selected_collections: list[str],
) -> list[int]:
    """Keep only documents whose collection is in the selected set."""
    if not selected_collections:
        return [d for d in doc_ids if d in doc_dict]

    filtered = []
    for did in doc_ids:
        doc = doc_dict.get(did)
        if not doc:
            continue
        coll = _get_collection_name(doc, category_map)
        if coll in selected_collections:
            filtered.append(did)
    return filtered


def _boost_by_doc_type(
    doc_ids: list[int],
    doc_types: dict[int, str],
    preferred: str,
) -> list[int]:
    """Move documents of the preferred type to the front."""
    pref = [d for d in doc_ids if doc_types.get(d) == preferred]
    rest = [d for d in doc_ids if doc_types.get(d) != preferred]
    if pref:
        logger.info("Doc-type boost: %d preferred + %d other", len(pref), len(rest))
    return pref + rest


def _boost_by_solution(
    doc_ids: list[int],
    doc_dict: dict[int, Any],
    keywords: list[str],
) -> list[int]:
    """Move documents matching solution keywords to the front."""
    pref = []
    for did in doc_ids:
        doc = doc_dict.get(did)
        if not doc:
            continue
        text = ((doc.title or "") + " " + (doc.description or "")).lower()
        if any(kw in text for kw in keywords):
            pref.append(did)
    rest = [d for d in doc_ids if d not in pref]
    if pref:
        logger.info("Solution boost: %d preferred + %d other", len(pref), len(rest))
    return pref + rest


def _filter_by_entity(
    doc_ids: list[int],
    doc_dict: dict[int, Any],
    entity: str,
    answer_mode: str,
) -> list[int]:
    """Entity-aware document filtering."""
    el = entity.lower()
    matching = [
        d for d in doc_ids
        if d in doc_dict and el in (doc_dict[d].title or "").lower()
    ]
    non_matching = [d for d in doc_ids if d not in matching]

    if matching:
        logger.info("Entity '%s': %d match, %d other", entity, len(matching), len(non_matching))
        if answer_mode in ("extract", "brief"):
            return matching  # strict filter for factual
        return matching + non_matching  # boost for explain/summarize
    return doc_ids


def _build_heuristic_configs(
    doc_ids: list[int],
    doc_dict: dict[int, Any],
    doc_types: dict[int, str],
    answer_mode: str,
) -> dict[int, dict]:
    """
    Build per-document retrieval config using heuristics.
    Eliminates the old Step 2 LLM call.
    """
    configs: dict[int, dict] = {}
    for did in doc_ids:
        # Base top_k by answer mode
        if answer_mode == "extract":
            top_k = 3
        elif answer_mode == "brief":
            top_k = 4
        elif answer_mode == "summarize":
            top_k = 5
        else:  # explain
            top_k = 6

        # Bound
        top_k = max(2, min(8, top_k))

        configs[did] = {"top_k": top_k}
    return configs


def _group_by_collection(
    doc_ids: list[int],
    doc_dict: dict[int, Any],
    category_map: dict[int, Any],
) -> dict[str, list[int]]:
    """Group document IDs by their collection name."""
    groups: dict[str, list[int]] = defaultdict(list)
    for did in doc_ids:
        doc = doc_dict.get(did)
        if not doc:
            continue
        coll = _get_collection_name(doc, category_map)
        groups[coll].append(did)
    return dict(groups)


def _get_collection_name(doc: Any, category_map: dict[int, Any]) -> str:
    """Resolve the ChromaDB collection name for a document."""
    if doc.category_id and doc.category_id in category_map:
        return category_map[doc.category_id].collection_name
    if doc.category:
        return normalize_collection_name(doc.category)
    return "documents"


async def _retrieve_chunks_for_collection(
    collection_name: str,
    doc_ids: list[int],
    doc_dict: dict[int, Any],
    doc_configs: dict[int, dict],
    refined_question: str,
) -> list[ChunkResult]:
    """
    Retrieve chunks from a single ChromaDB collection.

    Runs in a thread executor since ChromaDB client is synchronous.
    """
    loop = asyncio.get_event_loop()

    # Calculate total requested chunks
    doc_limits = {}
    total_k = 0
    for did in doc_ids:
        k = doc_configs.get(did, {}).get("top_k", 5)
        doc_limits[did] = k
        total_k += k

    query_limit = min(int(total_k * 1.5) + 5, 200)

    def _sync_query() -> list[ChunkResult]:
        try:
            results = query_collection_with_filter(
                query_text=refined_question,
                collection_name=collection_name,
                n_results=query_limit,
                where={"document_id": {"$in": doc_ids}},
            )
        except Exception as e:
            logger.error("Error querying collection %s: %s", collection_name, e)
            return []

        chunks: list[ChunkResult] = []
        if not results.get("ids") or not results["ids"][0]:
            return chunks

        chunks_per_doc: dict[int, int] = defaultdict(int)

        for idx, chunk_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][idx] if results.get("metadatas") else {}
            raw_did = meta.get("document_id")

            try:
                cdid = int(raw_did) if isinstance(raw_did, str) else raw_did
            except (ValueError, TypeError):
                continue

            if cdid is None or cdid not in doc_limits:
                continue

            if chunks_per_doc[cdid] >= doc_limits[cdid]:
                continue

            chunks_per_doc[cdid] += 1
            doc = doc_dict.get(cdid)
            chunks.append(ChunkResult(
                document_id=cdid,
                document_title=doc.title if doc else str(cdid),
                category=doc.category if doc else None,
                chunk_text=(
                    results["documents"][0][idx]
                    if results.get("documents")
                    else ""
                ),
                page_number=meta.get("page_number"),
                chunk_index=meta.get("chunk_index"),
                score=(
                    float(results["distances"][0][idx])
                    if results.get("distances")
                    else 0.0
                ),
            ))

        return chunks

    return await loop.run_in_executor(None, _sync_query)
