import chromadb
from typing import List, Dict, Any
import logging
import os
from pathlib import Path
import threading
from functools import lru_cache
import time
import re
import json

from app.core.config import settings
from app.ocr_pipeline.metadata import MetadataAugmentation


logger = logging.getLogger(__name__)


class EmbeddingDimensionMismatchError(RuntimeError):
    """Raised when a ChromaDB collection's embedding dimension does not match the current embedding model."""

    def __init__(
        self,
        collection_name: str,
        *,
        expected_dim: int | None = None,
        got_dim: int | None = None,
        original_error: Exception | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.expected_dim = expected_dim
        self.got_dim = got_dim
        self.original_error = original_error

        dim_part = ""
        if expected_dim and got_dim:
            dim_part = f" (expected {expected_dim}, got {got_dim})"

        msg = (
            f"Embedding dimension mismatch for ChromaDB collection '{collection_name}'{dim_part}. "
            "Dense retrieval is not usable until the vector DB is re-created/re-ingested.\n\n"
            "Fix (OpenAI embeddings everywhere):\n"
            "  1) Run: python scripts/drop_chroma_collections.py\n"
            "  2) Run: python scripts/reembed_all.py\n"
            "  3) Restart the FastAPI app to re-ingest all documents.\n\n"
            "Notes:\n"
            "  - The embedding model is configured as OpenAI 'text-embedding-3-small' (384 dims).\n"
            "  - If you recently migrated from a different dimension, "
            "    existing persisted collections must be rebuilt."
        )
        super().__init__(msg)


def _should_recreate_on_dim_mismatch() -> bool:
    return False


def _is_dim_mismatch_error(e: Exception) -> bool:
    msg = str(e).lower()
    return ("expecting embedding with dimension" in msg) or ("dimension" in msg and "got" in msg)


def _extract_dims_from_error(e: Exception) -> tuple[int | None, int | None]:
    msg = str(e)
    m = re.search(r"expected\s+(\d+)\s*,\s*got\s+(\d+)", msg, flags=re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"dimension\s+(\d+).+got\s+(\d+)", msg, flags=re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _recreate_collection(client: chromadb.ClientAPI, name: str, metadata: dict | None = None):
    try:
        client.delete_collection(name=name)
    except Exception:
        pass
    return client.create_collection(name=name, metadata=metadata or {})

# Thread-local storage for ChromaDB clients
_thread_local = threading.local()
_chroma_client_lock = threading.Lock()


def _get_persist_directory(persist_directory: str | None = None) -> str:
    if persist_directory is not None:
        return persist_directory
    
    if settings.chromadb_persist_directory:
        return settings.chromadb_persist_directory

    base_dir = Path(__file__).resolve().parents[1]
    return str(base_dir / "vector_db" / "chroma_db")


def _get_chroma_client(persist_directory: str | None = None) -> chromadb.ClientAPI:
    path = _get_persist_directory(persist_directory)
    
    if not hasattr(_thread_local, 'chroma_client') or _thread_local.persist_path != path:
        with _chroma_client_lock:
            _thread_local.chroma_client = chromadb.PersistentClient(
                path=path,
                settings=chromadb.Settings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                )
            )
            _thread_local.persist_path = path
    
    return _thread_local.chroma_client


@lru_cache(maxsize=1)
def get_chroma_client_cached(persist_directory: str | None = None) -> chromadb.ClientAPI:
    return _get_chroma_client(persist_directory)


def init_chromadb(persist_directory: str | None = None) -> bool:
    try:
        client = _get_chroma_client(persist_directory)
        _ = client.list_collections()
        print("[OK] ChromaDB connection initialized successfully")
        return True
    except Exception as e:
        print(f"[FAIL] ChromaDB connection failed: {e}")
        return False


_OPENAI_EMBED_MODEL = "text-embedding-3-small"
_OPENAI_EMBED_DIM = 384


def _get_openai_embed_client():
    from openai import OpenAI as _OpenAI
    from app.core.config import settings
    return _OpenAI(api_key=settings.openai_api_key)


def _ensure_embedding_dim(embedding: list, *, source: str) -> None:
    got = len(embedding or [])
    if got != _OPENAI_EMBED_DIM:
        raise RuntimeError(
            f"Unexpected embedding size from {source}: expected {_OPENAI_EMBED_DIM}, got {got}."
        )


def _get_collection_embedding_dim(collection: chromadb.Collection) -> int | None:
    try:
        if collection.count() == 0:
            return None
        sample = collection.get(limit=1, include=["embeddings"])
        embeddings = sample.get("embeddings") or []
        if not embeddings or embeddings[0] is None:
            return None
        return len(embeddings[0])
    except Exception:
        return None


def _assert_collection_dimension(collection: chromadb.Collection, collection_name: str) -> None:
    existing_dim = _get_collection_embedding_dim(collection)
    if existing_dim is not None and existing_dim != _OPENAI_EMBED_DIM:
        raise EmbeddingDimensionMismatchError(
            collection_name,
            expected_dim=existing_dim,
            got_dim=_OPENAI_EMBED_DIM,
        )


def _normalize_embedding_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    else:
        text = str(value)

    text = text.replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitize_chunks_for_storage(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sanitized: List[Dict[str, Any]] = []
    dropped = 0

    for chunk in chunks or []:
        if not isinstance(chunk, dict):
            dropped += 1
            continue

        cleaned = dict(chunk)
        cleaned_text = _normalize_embedding_text(cleaned.get("text"))
        if not cleaned_text:
            dropped += 1
            continue

        cleaned["text"] = cleaned_text
        sanitized.append(cleaned)

    if dropped:
        print(f"[EMBED SANITIZE] Dropped {dropped} invalid/blank chunk(s) before embedding.")

    return sanitized


def embed_worker(text: str) -> list:
    cleaned_text = _normalize_embedding_text(text)
    if not cleaned_text:
        cleaned_text = "[empty text]"
    client = _get_openai_embed_client()
    response = client.embeddings.create(
        input=cleaned_text,
        model=_OPENAI_EMBED_MODEL,
        dimensions=_OPENAI_EMBED_DIM,
    )
    embedding = response.data[0].embedding
    _ensure_embedding_dim(embedding, source=_OPENAI_EMBED_MODEL)
    return embedding


def _embed_batch(texts: List[str]) -> List[list]:
    normalized = [_normalize_embedding_text(t) for t in texts]
    invalid_indexes = [idx for idx, text in enumerate(normalized) if not text]
    if invalid_indexes:
        raise ValueError(f"Invalid embedding inputs at indexes {invalid_indexes[:10]}.")

    client = _get_openai_embed_client()
    all_embeddings: List[list] = []
    batch_size = 100
    for i in range(0, len(normalized), batch_size):
        batch = normalized[i : i + batch_size]
        response = client.embeddings.create(
            input=batch,
            model=_OPENAI_EMBED_MODEL,
            dimensions=_OPENAI_EMBED_DIM,
        )
        sorted_items = sorted(response.data, key=lambda x: x.index)
        batch_embeddings = [item.embedding for item in sorted_items]
        for emb in batch_embeddings:
            _ensure_embedding_dim(emb, source=f"{_OPENAI_EMBED_MODEL} batch")
        all_embeddings.extend(batch_embeddings)
    return all_embeddings


def _embed_query(text: str) -> list:
    cleaned_text = _normalize_embedding_text(text)
    if not cleaned_text:
        cleaned_text = "[empty query]"
    client = _get_openai_embed_client()
    response = client.embeddings.create(
        input=cleaned_text,
        model=_OPENAI_EMBED_MODEL,
        dimensions=_OPENAI_EMBED_DIM,
    )
    embedding = response.data[0].embedding
    _ensure_embedding_dim(embedding, source=_OPENAI_EMBED_MODEL)
    return embedding


_reranker_tokenizer = None
_reranker_model_instance = None
_RERANKER_MODEL = os.getenv("ASKMOJO_RERANKER_MODEL", "jinaai/jina-reranker-v1-tiny-en").strip() or "jinaai/jina-reranker-v1-tiny-en"
_RERANKER_LOCAL_ONLY = (os.getenv("ASKMOJO_RERANKER_LOCAL_ONLY", "1").strip().lower() not in {"0", "false", "no", "off"})
_RERANKER_MAX_LENGTH = int(os.getenv("ASKMOJO_RERANKER_MAX_LENGTH", "384"))
_RERANKER_BATCH_SIZE = int(os.getenv("ASKMOJO_RERANKER_BATCH_SIZE", "16"))


def _requires_trust_remote_code(model_name: str) -> bool:
    model_lower = (model_name or "").strip().lower()
    return model_lower.startswith("jinaai/")


def _get_transformers_modules_cache_dir() -> str:
    cache_dir = Path.home() / ".cache" / "askmojo_hfmods"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


def _prepare_transformers_dynamic_module_cache() -> None:
    cache_dir = _get_transformers_modules_cache_dir()
    os.environ["HF_MODULES_CACHE"] = cache_dir

    try:
        import transformers.dynamic_module_utils as dynamic_module_utils

        dynamic_module_utils.HF_MODULES_CACHE = cache_dir
    except Exception:
        pass


def _ensure_transformers_onnx_stub() -> None:
    try:
        import transformers.onnx  # type: ignore  # noqa: F401
        return
    except Exception:
        pass

    import sys
    import types

    shim = types.ModuleType("transformers.onnx")

    class OnnxConfig:  # pragma: no cover - compatibility shim
        def __init__(self, *args, **kwargs):
            self.task = kwargs.get("task")

    shim.OnnxConfig = OnnxConfig
    sys.modules["transformers.onnx"] = shim


def _ensure_transformers_pytorch_utils_compat() -> None:
    try:
        import torch
        import transformers.pytorch_utils as pytorch_utils
    except Exception:
        return

    if hasattr(pytorch_utils, "find_pruneable_heads_and_indices"):
        return

    def find_pruneable_heads_and_indices(heads, n_heads, head_size, already_pruned_heads):
        heads = set(heads) - set(already_pruned_heads)
        mask = torch.ones(n_heads, head_size, dtype=torch.bool)
        for head in heads:
            head = head - sum(1 if pruned_head < head else 0 for pruned_head in already_pruned_heads)
            if 0 <= head < n_heads:
                mask[head] = False
        index = torch.arange(mask.numel(), dtype=torch.long)[mask.view(-1)]
        return heads, index

    pytorch_utils.find_pruneable_heads_and_indices = find_pruneable_heads_and_indices


def _resolve_reranker_model_path() -> str:
    if os.path.isdir(_RERANKER_MODEL):
        return _RERANKER_MODEL

    if "/" in _RERANKER_MODEL:
        owner, repo = _RERANKER_MODEL.split("/", 1)
        snapshots_dir = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{owner}--{repo}" / "snapshots"
        if snapshots_dir.exists():
            snapshots = sorted(
                [p for p in snapshots_dir.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if snapshots:
                return str(snapshots[0])
    return _RERANKER_MODEL


def _get_reranker():
    global _reranker_tokenizer, _reranker_model_instance
    if _reranker_tokenizer is None or _reranker_model_instance is None:
        model_path = _resolve_reranker_model_path()
        needs_trust_remote_code = _requires_trust_remote_code(_RERANKER_MODEL) or _requires_trust_remote_code(model_path)
        if needs_trust_remote_code:
            _prepare_transformers_dynamic_module_cache()
            _ensure_transformers_onnx_stub()
            _ensure_transformers_pytorch_utils_compat()
        if _RERANKER_LOCAL_ONLY:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        model_kwargs: Dict[str, Any] = {
            "local_files_only": _RERANKER_LOCAL_ONLY,
            "trust_remote_code": needs_trust_remote_code,
            "torch_dtype": torch.float32,
        }
        if _requires_trust_remote_code(_RERANKER_MODEL) or _requires_trust_remote_code(model_path):
            model_kwargs["num_labels"] = 1

        _reranker_tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            local_files_only=_RERANKER_LOCAL_ONLY,
            trust_remote_code=needs_trust_remote_code,
        )
        _reranker_model_instance = AutoModelForSequenceClassification.from_pretrained(
            model_path,
            **model_kwargs,
        )
        _reranker_model_instance.eval()
    return _reranker_tokenizer, _reranker_model_instance


def warm_reranker() -> bool:
    try:
        _get_reranker()
        logger.info("[RERANKER] Warmed startup model: %s", _resolve_reranker_model_path())
        return True
    except Exception as e:
        logger.warning("[RERANKER] Startup warm-up failed: %s", e)
        return False


def rerank_chunks_scored(
    query: str,
    chunks: List[Dict],
    text_key: str = "chunk_text",
    top_k: int | None = None,
) -> List[Dict]:
    if not chunks:
        return []

    try:
        import torch

        tokenizer, reranker = _get_reranker()
        pairs = []
        materialized: List[Dict[str, Any]] = []
        for chunk in chunks:
            if isinstance(chunk, dict):
                item = dict(chunk)
                text = item.get(text_key, "") or ""
            else:
                item = {"value": chunk}
                text = getattr(chunk, text_key, "") or ""
            pairs.append((query, text))
            materialized.append(item)

        scores: List[float] = []
        batch_size = max(1, _RERANKER_BATCH_SIZE)
        with torch.inference_mode():
            for start in range(0, len(pairs), batch_size):
                batch = pairs[start : start + batch_size]
                batch_queries = [pair[0] for pair in batch]
                batch_docs = [pair[1] for pair in batch]
                encoded = tokenizer(
                    batch_queries,
                    batch_docs,
                    padding=True,
                    truncation=True,
                    max_length=_RERANKER_MAX_LENGTH,
                    return_tensors="pt",
                )
                logits = reranker(**encoded).logits
                batch_scores = logits.squeeze(-1).float().cpu().tolist()
                if isinstance(batch_scores, float):
                    batch_scores = [batch_scores]
                scores.extend(float(score) for score in batch_scores)

        ranked = sorted(
            zip(materialized, scores),
            key=lambda item: float(item[1]),
            reverse=True,
        )
        if isinstance(top_k, int) and top_k > 0:
            ranked = ranked[:top_k]

        out: List[Dict[str, Any]] = []
        for rank, (item, score) in enumerate(ranked, start=1):
            item["rerank_score"] = score
            item["rerank_rank"] = rank
            out.append(item)
        return out
    except Exception as e:
        print(f"[RERANKER] Warning: reranker failed ({e}), returning original order")
        fallback: List[Dict[str, Any]] = []
        materialized = chunks[:top_k] if isinstance(top_k, int) and top_k > 0 else chunks
        for rank, chunk in enumerate(materialized, start=1):
            item = dict(chunk) if isinstance(chunk, dict) else {"value": chunk}
            item["rerank_score"] = 0.0
            item["rerank_rank"] = rank
            fallback.append(item)
        return fallback


def rerank_chunks(query: str, chunks: List[Dict], text_key: str = "chunk_text", top_k: int = 5) -> List[Dict]:
    scored = rerank_chunks_scored(query=query, chunks=chunks, text_key=text_key, top_k=top_k)
    return [dict(chunk) for chunk in scored]


def store_chunks_in_chromadb(
    chunks: List[Dict],
    collection_name: str = "pdf_pages",
    persist_directory: str | None = None,
) -> chromadb.Collection:
    persist_directory = _get_persist_directory(persist_directory)
    chunks = sanitize_chunks_for_storage(chunks)
    if not chunks:
        raise ValueError("No valid non-empty chunks available for embedding/storage.")

    documents = [chunk["text"] for chunk in chunks]
    print(f"Generating embeddings via OpenAI {_OPENAI_EMBED_MODEL} ({len(documents)} chunks)...")
    embeddings = _embed_batch(documents)
    client = _get_chroma_client(persist_directory)

    try:
        collection = client.get_collection(name=collection_name)
        _assert_collection_dimension(collection, collection_name)
    except EmbeddingDimensionMismatchError:
        raise
    except Exception:
        collection = client.create_collection(
            name=collection_name,
            metadata={"description": f"Chunks for collection {collection_name}"},
        )

    ids: List[str] = []
    metadatas: List[Dict[str, Any]] = []

    for idx, chunk in enumerate(chunks, start=1):
        metadata = MetadataAugmentation.create_chromadb_metadata(chunk)
        d_id = metadata.get("doc_id") or str(chunk.get("document_id", ""))
        chunk_id = f"doc_{d_id}_chunk_{idx}" if d_id else f"chunk_{idx}"
        ids.append(chunk_id)

        if "version" not in metadata and chunk.get("version"):
            metadata["version"] = chunk["version"]

        metadatas.append(metadata)

    print("Storing chunks in ChromaDB...")
    try:
        collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
    except Exception as e:
        if _is_dim_mismatch_error(e):
            expected_dim, got_dim = _extract_dims_from_error(e)
            raise EmbeddingDimensionMismatchError(
                collection_name,
                expected_dim=expected_dim,
                got_dim=got_dim,
                original_error=e,
            ) from e
        raise

    return collection


def list_collections(persist_directory: str | None = None) -> List[Dict[str, Any]]:
    client = _get_chroma_client(persist_directory)
    collections = client.list_collections()
    result: List[Dict[str, Any]] = []

    for col in collections:
        result.append(
            {
                "name": col.name,
                "metadata": getattr(col, "metadata", {}) or {},
            }
        )
    return result


def query_collection(
    query_text: str,
    collection_name: str,
    n_results: int = 5,
    persist_directory: str | None = None,
) -> Dict[str, Any]:
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)

    try:
        collection = client.get_collection(name=collection_name)
        _assert_collection_dimension(collection, collection_name)
    except Exception:
        raise ValueError(f"Collection '{collection_name}' does not exist")

    query_embedding = _embed_query(query_text)

    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        if _is_dim_mismatch_error(e):
            expected_dim, got_dim = _extract_dims_from_error(e)
            raise EmbeddingDimensionMismatchError(
                collection_name,
                expected_dim=expected_dim,
                got_dim=got_dim,
                original_error=e,
            ) from e
        raise

    return results


def query_collection_with_filter(
    query_text: str,
    collection_name: str,
    n_results: int = 5,
    where: Dict[str, Any] | None = None,
    persist_directory: str | None = None,
) -> Dict[str, Any]:
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)

    try:
        collection = client.get_collection(name=collection_name)
        _assert_collection_dimension(collection, collection_name)
    except Exception:
        raise ValueError(f"Collection '{collection_name}' does not exist")

    query_embedding = _embed_query(query_text)

    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
            where=where
        )
    except Exception as e:
        if _is_dim_mismatch_error(e):
            expected_dim, got_dim = _extract_dims_from_error(e)
            raise EmbeddingDimensionMismatchError(
                collection_name,
                expected_dim=expected_dim,
                got_dim=got_dim,
                original_error=e,
            ) from e
        raise

    return results


def store_document_in_master_collection(
    document_id: int,
    title: str,
    description: str,
    category: str | None = None,
    source_type: str = "pdf",
    persist_directory: str | None = None,
    doc_type: str | None = None,
    domain: str | None = None,
    solution_area: str | None = None,
    industry: str | None = None,
    technology: str | None = None,
    use_case: str | None = None,
    entity: str | None = None,
    created_at: int | None = None,
) -> None:
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    
    try:
        master_collection = client.get_collection(name="master_docs")
        _assert_collection_dimension(master_collection, "master_docs")
    except EmbeddingDimensionMismatchError:
        raise
    except Exception:
        master_collection = client.create_collection(
            name="master_docs",
            metadata={"description": "Master collection for document-level search"}
        )
    
    doc_text_parts = [f"Title: {title}"]
    if description: doc_text_parts.append(f"Description: {description}")
    if category: doc_text_parts.append(f"Category: {category}")
    if doc_type: doc_text_parts.append(f"DocType: {doc_type}")
    doc_text_parts.append(f"Type: {source_type}")
    if domain: doc_text_parts.append(f"Domain: {domain}")
    if solution_area: doc_text_parts.append(f"Solution Area: {solution_area}")
    if industry: doc_text_parts.append(f"Industry: {industry}")
    if technology: doc_text_parts.append(f"Technology: {technology}")
    if use_case: doc_text_parts.append(f"UseCase: {use_case}")
    if entity: doc_text_parts.append(f"Entity: {entity}")

    doc_text = "\n".join(doc_text_parts)
    doc_embedding = _embed_query(doc_text)

    metadata = {
        "title": title,
        "category": category or "",
        "source_type": source_type,
        "doc_id": str(document_id),
        "doc_type": doc_type or "other",
        "domain": domain or "",
        "solution_area": solution_area or "",
        "industry": industry or "",
        "technology": technology or "",
        "use_case": use_case or "",
        "entity": entity or "internal",
        "created_at": created_at or int(time.time()),
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}

    try:
        master_collection.upsert(
            ids=[str(document_id)],
            embeddings=[doc_embedding],
            metadatas=[metadata],
            documents=[doc_text],
        )
    except Exception as e:
        if _is_dim_mismatch_error(e):
            expected_dim, got_dim = _extract_dims_from_error(e)
            raise EmbeddingDimensionMismatchError(
                "master_docs",
                expected_dim=expected_dim,
                got_dim=got_dim,
                original_error=e,
            ) from e
        raise


def delete_document_from_chromadb(
    document_id: int,
    collection_name: str | None = None,
    persist_directory: str | None = None,
) -> None:
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    deleted_from_collections = []

    def _delete_doc_chunks_from_collection(collection: chromadb.Collection, doc_id: int) -> int:
        total_deleted = 0
        doc_id_str = str(doc_id)
        
        try:
            res = collection.get(where={"doc_id": doc_id_str}, include=[])
            count = len(res.get("ids", []))
            if count > 0:
                collection.delete(where={"doc_id": doc_id_str})
                total_deleted += count
        except Exception: pass

        for val in [doc_id, doc_id_str]:
            try:
                res = collection.get(where={"document_id": val}, include=[])
                count = len(res.get("ids", []))
                if count > 0:
                    collection.delete(where={"document_id": val})
                    total_deleted += count
            except Exception: pass

        try:
            res = collection.get(limit=5000, include=[])
            ids_to_delete = [
                cid for cid in res.get("ids", []) 
                if isinstance(cid, str) and cid.startswith(f"doc_{doc_id}_chunk_")
            ]
            if ids_to_delete:
                collection.delete(ids=ids_to_delete)
                total_deleted += len(ids_to_delete)
        except Exception: pass

        return total_deleted
    
    try:
        master_collection = client.get_collection(name="master_docs")
        master_collection.delete(ids=[str(document_id)])
        master_collection.delete(where={"doc_id": str(document_id)})
        deleted_from_collections.append("master_docs")
    except Exception: pass
    
    if collection_name:
        try:
            collection = client.get_collection(name=collection_name)
            deleted_count = _delete_doc_chunks_from_collection(collection, document_id)
            if deleted_count > 0:
                deleted_from_collections.append(collection_name)
        except Exception: pass
    
    try:
        collections = client.list_collections()
        for col in collections:
            if col.name == "master_docs" or col.name == collection_name:
                continue
            try:
                collection = client.get_collection(name=col.name)
                deleted_count = _delete_doc_chunks_from_collection(collection, document_id)
                if deleted_count > 0:
                    deleted_from_collections.append(col.name)
            except Exception: pass
    except Exception: pass
    
    if deleted_from_collections:
        print(f"Successfully deleted document {document_id} from: {', '.join(set(deleted_from_collections))}")
    else:
        print(f"Warning: Document {document_id} was not found in any ChromaDB collections")


def query_master_collection(
    query_text: str,
    n_results: int = 5,
    where: Dict[str, Any] | None = None,
    persist_directory: str | None = None,
) -> Dict[str, Any]:
    return query_collection_with_filter(
        query_text=query_text,
        collection_name="master_docs",
        n_results=n_results,
        where=where,
        persist_directory=persist_directory
    )


def get_document_chunks_from_collection(
    *,
    document_id: int,
    collection_name: str,
    persist_directory: str | None = None,
    limit: int = 200,
) -> list[str]:
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)

    try:
        collection = client.get_collection(name=collection_name)
        res = collection.get(
            where={"doc_id": str(document_id)},
            include=["documents"],
        )
        docs = res.get("documents") or []
        out: list[str] = [d.strip() for d in docs if isinstance(d, str) and d.strip()]
        return out[:limit] if limit else out
    except Exception:
        return []


def rename_chromadb_collection(
    old_collection_name: str,
    new_collection_name: str,
    category_description: str | None = None,
    persist_directory: str | None = None,
) -> bool:
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    
    try:
        try:
            old_collection = client.get_collection(name=old_collection_name)
        except Exception:
            return True
        
        try:
            client.get_collection(name=new_collection_name)
            raise ValueError(f"Collection '{new_collection_name}' already exists")
        except Exception: pass
        
        # 1. MUST include embeddings to prevent data loss
        all_data = old_collection.get(include=["embeddings", "metadatas", "documents"])
        
        # 2. Prepare metadata (preserve structured fields)
        metadata = dict(old_collection.metadata or {})
        if category_description:
            metadata["description"] = category_description

        # 3. Create new and copy data
        if not all_data or not all_data.get("ids") or len(all_data["ids"]) == 0:
            client.delete_collection(name=old_collection_name)
            client.create_collection(name=new_collection_name, metadata=metadata)
            return True
        
        new_collection = client.create_collection(name=new_collection_name, metadata=metadata)
        new_collection.add(
            ids=all_data["ids"],
            embeddings=all_data.get("embeddings"),
            metadatas=all_data.get("metadatas"),
            documents=all_data.get("documents")
        )
        
        # 4. Delete old
        client.delete_collection(name=old_collection_name)
        return True
    except Exception as e:
        print(f"Error renaming collection: {e}")
        return False


def ensure_collection_exists(
    collection_name: str,
    category_description: str | None = None,
    persist_directory: str | None = None,
) -> bool:
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    
    try:
        client.get_collection(name=collection_name)
        return True
    except Exception:
        try:
            # Structured Metadata Schema
            metadata = {
                "description": category_description or f"Collection for: {collection_name}",
                "routing_hint": "",
                "routing_keywords": "",
                "doc_types": "",
                "domains": "",
                "industries": "",
                "technologies": "",
                "solution_areas": "",
                "key_entities": "",
                "example_questions": "",
                "doc_count": 0,
                "last_updated": int(time.time()),
            }
            client.create_collection(name=collection_name, metadata=metadata)
            return True
        except Exception as e:
            print(f"Error creating collection '{collection_name}': {e}")
            return False


def update_collection_descriptor(
    collection_name: str,
    new_doc_meta: dict,
    persist_directory: str | None = None,
) -> None:
    """Merge document metadata into structured collection descriptor."""
    client = _get_chroma_client(persist_directory)

    try:
        col = client.get_collection(name=collection_name)
        existing = dict(col.metadata or {})
    except Exception:
        # If collection doesn't exist, ignore (or could create, but usually handled by ingestion)
        return

    def _merge_csv(field: str, new_val: str | None) -> str:
        if not new_val: return str(existing.get(field, ""))
        old_val = str(existing.get(field, ""))
        unique = set(v.strip() for v in f"{old_val},{new_val}".split(",") if v.strip())
        return ",".join(sorted(unique)[:30])

    def _merge_space(field: str, new_val: str | None) -> str:
        if not new_val: return str(existing.get(field, ""))
        old_val = str(existing.get(field, "").lower())
        unique = set(old_val.split()) | set(new_val.lower().split())
        return " ".join(sorted(unique)[:60])

    # Extract metadata fields from the document
    d_type    = new_doc_meta.get("doc_type")
    domain    = new_doc_meta.get("domain")
    industry  = new_doc_meta.get("industry")
    tech      = new_doc_meta.get("technology")
    solution  = new_doc_meta.get("solution_area")
    entity    = new_doc_meta.get("entity")
    use_case  = new_doc_meta.get("use_case")

    new_routing_kw = " ".join(filter(None, [
        entity, tech, solution, use_case, domain, industry
    ]))

    updated = {
        "doc_types":      _merge_csv("doc_types", d_type),
        "domains":        _merge_csv("domains", domain),
        "industries":     _merge_csv("industries", industry),
        "technologies":   _merge_csv("technologies", tech),
        "solution_areas": _merge_csv("solution_areas", solution),
        "key_entities":   _merge_csv("key_entities", entity),
        "routing_keywords": _merge_space("routing_keywords", new_routing_kw),
        "doc_count":      int(existing.get("doc_count", 0)) + 1,
        "last_updated":   int(time.time()),
        # Preserve original descriptions/hints unless manually overwritten
        "description":    existing.get("description") or f"Collection: {collection_name}",
        "routing_hint":   existing.get("routing_hint") or "",
        "example_questions": existing.get("example_questions") or "",
    }

    try:
        col.modify(metadata=updated)
    except Exception as e:
        print(f"[METADATA] Error updating descriptor for {collection_name}: {e}")


def update_collection_metadata(
    collection_name: str,
    category_description: str | None = None,
    persist_directory: str | None = None,
) -> bool:
    """Non-destructively update collection metadata (description) while preserving other fields."""
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    try:
        collection = client.get_collection(name=collection_name)
        existing_metadata = dict(collection.metadata or {})
        
        # Merge the new description into existing metadata to preserve structured routing fields
        if category_description:
            existing_metadata["description"] = category_description
        
        # Use .modify() which is atomic and preserves embeddings
        collection.modify(metadata=existing_metadata)
        return True
    except Exception as e:
        print(f"Error updating collection metadata for '{collection_name}': {e}")
        return False

# =========================================================================
# BM25 SPARSE RETRIEVAL UTILITIES
# =========================================================================
import pickle
from rank_bm25 import BM25Okapi

def _get_bm25_dir() -> Path:
    base_dir = Path(__file__).resolve().parents[1]
    bm25_dir = base_dir / "vector_db" / "bm25_indices"
    bm25_dir.mkdir(parents=True, exist_ok=True)
    return bm25_dir

def build_bm25_index(collection_name: str, chunk_texts: List[str], chunk_ids: List[str]):
    if not chunk_texts: return
    tokenized_corpus = [text.lower().split() for text in chunk_texts]
    bm25 = BM25Okapi(tokenized_corpus)
    index_data = {"bm25": bm25, "chunk_ids": chunk_ids, "chunk_texts": chunk_texts}
    with open(_get_bm25_dir() / f"{collection_name}.pkl", "wb") as f:
        pickle.dump(index_data, f)
        
def query_bm25(query: str, collection_name: str, n_results: int = 5) -> List[Dict[str, Any]]:
    index_path = _get_bm25_dir() / f"{collection_name}.pkl"
    if not index_path.exists(): return []
    with open(index_path, "rb") as f:
        data = pickle.load(f)
    bm25, ids, texts = data["bm25"], data["chunk_ids"], data["chunk_texts"]
    scores = bm25.get_scores(query.lower().split())
    scored = sorted(zip(ids, texts, scores), key=lambda x: x[2], reverse=True)
    return [{"id": c, "chunk_text": t, "score": float(s)} for c, t, s in scored[:n_results] if s > 0]

def reciprocal_rank_fusion(dense_results: List[Dict], sparse_results: List[Dict], id_key="id", k=60) -> List[Dict]:
    fused_scores = {}
    def add_to_fusion(results, weight=1.0):
        for rank, item in enumerate(results, start=1):
            iid = item[id_key]
            if iid not in fused_scores: fused_scores[iid] = {"score": 0.0, "item": item}
            fused_scores[iid]["score"] += weight * (1.0 / (k + rank))
    add_to_fusion(dense_results); add_to_fusion(sparse_results)
    fused = sorted(fused_scores.values(), key=lambda x: x["score"], reverse=True)
    return [{**f["item"], "rrf_score": f["score"]} for f in fused]
