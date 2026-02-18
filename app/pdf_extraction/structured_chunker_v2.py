"""
StructuredChunkerV2

Deterministic, heading-anchored chunker operating on normalized JSON.

Rules implemented:
- H1 starts a primary section chunk; H2/H3 attach under H1.
- Paragraphs/lists/tables attach to nearest active heading (H3 > H2 > H1).
- Merge across pages: pages without H1 attach to previous H1.
- Never modify text; never summarize.
- Split only at subheading boundaries (H3 then H2). If no subheadings exist, keep as single chunk.

Public API: StructuredChunkerV2.chunk_normalized(normalized: dict, config: dict | None = None) -> list[dict]
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional
from math import floor

from app.core.config import settings

logger = logging.getLogger(__name__)


class StructuredChunkerV2:
    @staticmethod
    def _compute_max_words(config: Optional[Dict[str, Any]] = None) -> int:
        """Compute a safe max words-per-chunk based on settings and optional overrides.

        Uses the TPM/RPM safety model described in the implementation plan.
        """
        # Allow explicit override
        if config and config.get("chunk_max_words_hint"):
            return int(config["chunk_max_words_hint"])

        # Reserved tokens (prompt + expected answer). These are conservative defaults.
        reserved_prompt = config.get("reserved_prompt", 1200) if config else 1200
        reserved_answer = config.get("reserved_answer", 1500) if config else 1500

        model_ctx = config.get("model_context_limit", settings.model_context_limit) if config else settings.model_context_limit
        tpm = config.get("openai_tpm_limit", settings.openai_tpm_limit) if config else settings.openai_tpm_limit
        expected_rpm = config.get("expected_requests_per_minute", settings.expected_requests_per_minute) if config else settings.expected_requests_per_minute
        top_k = config.get("target_top_k_for_budget", settings.target_top_k_for_budget) if config else settings.target_top_k_for_budget
        buffer = config.get("chunk_safety_buffer", settings.chunk_safety_buffer) if config else settings.chunk_safety_buffer

        # Context-limited tokens per chunk
        tokens_per_chunk_ctx = floor((model_ctx - reserved_prompt - reserved_answer) / max(1, top_k))

        # TPM-limited tokens per request
        tokens_per_request_allowed = floor(tpm / max(1, expected_rpm))
        tokens_per_chunk_tpm = floor((tokens_per_request_allowed - (reserved_prompt + reserved_answer)) / max(1, top_k))

        chosen = min(tokens_per_chunk_ctx, max(256, tokens_per_chunk_tpm))
        safe_tokens = int(buffer * chosen)

        # Convert tokens to words (approx 1 token ≈ 1.3 words)
        words = max(64, int(safe_tokens / 1.3))
        return words

    @staticmethod
    def chunk_normalized(normalized: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Create deterministic chunks from normalized JSON.

        Args:
            normalized: Normalized JSON produced by `normalize_adobe_elements`.
            config: Optional overrides for sizing and reserved tokens.

        Returns:
            List of chunk dicts with required metadata.
        """
        pages = normalized.get("pages", []) if normalized else []
        if not pages:
            return []

        max_words = None
        # allow explicit override
        if config and config.get("chunk_max_words_hint"):
            max_words = int(config["chunk_max_words_hint"])
        else:
            max_words = StructuredChunkerV2._compute_max_words(config=config)

        logger.info(f"[CHUNKER_V2] max_words per chunk: {max_words}")

        # Build H1-aware structure from ordered blocks (preferred)
        h1_nodes: List[Dict[str, Any]] = []
        current_h1 = None
        current_sub = None

        def _start_new_h1(title: Optional[str], page_number: int):
            nonlocal current_h1, current_sub
            node = {
                "h1": title,
                "start_page": page_number,
                "end_page": page_number,
                "subsections": [],
            }
            # Create a root subsection to collect H1-level blocks
            root_sub = {
                "type": "root",
                "title": None,
                "blocks": [],
                "start_page": page_number,
                "end_page": page_number,
                "depth": 1,
            }
            node["subsections"].append(root_sub)
            current_h1 = node
            current_sub = root_sub
            h1_nodes.append(node)
            return node

        # iterate pages in order
        for page in pages:
            page_num = page.get("page_number")
            blocks = page.get("blocks")

            # If blocks exist, iterate in order; otherwise fall back to deterministic ordering
            if blocks:
                for blk in blocks:
                    btype = (blk.get("type") or "").lower()
                    btext = (blk.get("text") or "").strip()

                    if btype == "h1":
                        _start_new_h1(btext or None, page_num)
                    elif btype == "h2":
                        if current_h1 is None:
                            # attach to implicit h1
                            _start_new_h1(None, page_num)
                        sub = {
                            "type": "h2",
                            "title": btext or None,
                            "blocks": [],
                            "start_page": page_num,
                            "end_page": page_num,
                            "depth": 2,
                        }
                        current_h1["subsections"].append(sub)
                        current_sub = sub
                    elif btype == "h3":
                        if current_h1 is None:
                            _start_new_h1(None, page_num)
                        sub = {
                            "type": "h3",
                            "title": btext or None,
                            "blocks": [],
                            "start_page": page_num,
                            "end_page": page_num,
                            "depth": 3,
                        }
                        current_h1["subsections"].append(sub)
                        current_sub = sub
                    elif btype in ("p", "list", "table"):
                        if current_h1 is None:
                            # No H1 seen yet on document; create implicit h1 to collect
                            _start_new_h1(None, page_num)
                        # append block to current subsection
                        if current_sub is None:
                            # safety: ensure a root exists
                            if not current_h1["subsections"]:
                                _start_new_h1(current_h1.get("h1"), page_num)
                            current_sub = current_h1["subsections"][0]
                        current_sub["blocks"].append({"type": btype, "text": btext, "page_number": page_num})
                        # update page ranges
                        current_sub["end_page"] = page_num
                        current_h1["end_page"] = page_num
                    else:
                        # ignore unknowns
                        continue

            else:
                # Fallback deterministic order: headings then paragraphs then lists then tables
                # Process H1/H2/H3 in recorded arrays
                h1s = page.get("h1", [])
                h2s = page.get("h2", [])
                h3s = page.get("h3", [])
                for t in h1s:
                    _start_new_h1(t or None, page_num)
                for t in h2s:
                    if current_h1 is None:
                        _start_new_h1(None, page_num)
                    sub = {
                        "type": "h2",
                        "title": t or None,
                        "blocks": [],
                        "start_page": page_num,
                        "end_page": page_num,
                        "depth": 2,
                    }
                    current_h1["subsections"].append(sub)
                    current_sub = sub
                for t in h3s:
                    if current_h1 is None:
                        _start_new_h1(None, page_num)
                    sub = {
                        "type": "h3",
                        "title": t or None,
                        "blocks": [],
                        "start_page": page_num,
                        "end_page": page_num,
                        "depth": 3,
                    }
                    current_h1["subsections"].append(sub)
                    current_sub = sub
                for p in page.get("p", []):
                    if current_h1 is None:
                        _start_new_h1(None, page_num)
                    if current_sub is None:
                        current_sub = current_h1["subsections"][0]
                    current_sub["blocks"].append({"type": "p", "text": p, "page_number": page_num})
                    current_sub["end_page"] = page_num
                    current_h1["end_page"] = page_num
                for lst in page.get("list", []):
                    if current_h1 is None:
                        _start_new_h1(None, page_num)
                    if current_sub is None:
                        current_sub = current_h1["subsections"][0]
                    list_text = "\n".join(lst if isinstance(lst, list) else [lst])
                    current_sub["blocks"].append({"type": "list", "text": list_text, "page_number": page_num})
                    current_sub["end_page"] = page_num
                    current_h1["end_page"] = page_num
                for table in page.get("table", []):
                    if current_h1 is None:
                        _start_new_h1(None, page_num)
                    if current_sub is None:
                        current_sub = current_h1["subsections"][0]
                    current_sub["blocks"].append({"type": "table", "text": table, "page_number": page_num})
                    current_sub["end_page"] = page_num
                    current_h1["end_page"] = page_num

        # Now convert H1 nodes into final chunks, splitting only at subsection boundaries
        chunks: List[Dict[str, Any]] = []
        chunk_idx = 1

        def _subsection_word_count(sub: Dict[str, Any]) -> int:
            wc = 0
            if sub.get("title"):
                wc += len(str(sub["title"]).split())
            for b in sub.get("blocks", []):
                wc += len(str(b.get("text", "")).split())
            return wc

        for hnode in h1_nodes:
            h1_title = hnode.get("h1")
            subsections = hnode.get("subsections", [])

            # If no real subsections (only root) then produce single chunk (do not split mid-paragraph)
            if not subsections or (len(subsections) == 1 and subsections[0]["type"] == "root"):
                # Build single chunk
                text_parts: List[str] = []
                if h1_title:
                    text_parts.append(h1_title)
                for sub in subsections:
                    if sub.get("title"):
                        text_parts.append(sub["title"])
                    for b in sub.get("blocks", []):
                        text_parts.append(b.get("text", ""))
                full_text = "\n\n".join([p for p in text_parts if p])

                chunk = {
                    "chunk_index": chunk_idx,
                    "page_number": subsections[0].get("start_page") if subsections else hnode.get("start_page"),
                    "start_page": hnode.get("start_page"),
                    "end_page": hnode.get("end_page"),
                    "text": full_text,
                    "char_count": len(full_text),
                    "word_count": len(full_text.split()),
                    "heading_level_1": h1_title,
                    "heading_level_2": None,
                    "heading_level_3": None,
                    "element_types": [b.get("type") for sub in subsections for b in sub.get("blocks", [])],
                    "element_type": subsections[0].get("blocks", [])[0].get("type") if subsections and subsections[0].get("blocks") else "paragraph",
                    "section": h1_title or None,
                    "hierarchy_depth": 1,
                    "extraction_source": "adobe_api",
                    "is_table": any(b.get("type") == "table" for sub in subsections for b in sub.get("blocks", [])),
                }
                chunks.append(chunk)
                chunk_idx += 1
                continue

            # Otherwise group subsections into chunks, splitting at subsection boundaries
            current_parts: List[Dict[str, Any]] = []
            current_word_sum = 0
            current_start_page = None
            current_end_page = None

            def _finalize(parts: List[Dict[str, Any]]):
                nonlocal chunk_idx
                text_list: List[str] = []
                # Add H1 title as first line
                if h1_title:
                    text_list.append(h1_title)
                elem_types = []
                pages_start = None
                pages_end = None
                depths = set()
                h2_titles = set()
                h3_titles = set()

                for sub in parts:
                    if sub.get("title"):
                        text_list.append(sub.get("title"))
                    for b in sub.get("blocks", []):
                        text_list.append(b.get("text", ""))
                        elem_types.append(b.get("type"))
                        if not pages_start:
                            pages_start = b.get("page_number")
                        pages_end = b.get("page_number")
                    depths.add(sub.get("depth", 1))
                    if sub.get("type") == "h2" and sub.get("title"):
                        h2_titles.add(sub.get("title"))
                    if sub.get("type") == "h3" and sub.get("title"):
                        h3_titles.add(sub.get("title"))

                full_text = "\n\n".join([p for p in text_list if p])
                heading_l2 = None
                heading_l3 = None
                # Only set heading_level_2/3 if exactly one unique title in the chunk
                if len(h2_titles) == 1:
                    heading_l2 = list(h2_titles)[0]
                if len(h3_titles) == 1:
                    heading_l3 = list(h3_titles)[0]

                chunk = {
                    "chunk_index": chunk_idx,
                    "page_number": pages_start,
                    "start_page": pages_start,
                    "end_page": pages_end,
                    "text": full_text,
                    "char_count": len(full_text),
                    "word_count": len(full_text.split()),
                    "heading_level_1": h1_title,
                    "heading_level_2": heading_l2,
                    "heading_level_3": heading_l3,
                    "element_types": elem_types,
                    "element_type": elem_types[0] if elem_types else "paragraph",
                    "section": (h1_title + (" > " + heading_l2 if heading_l2 else "")) if h1_title else None,
                    "hierarchy_depth": max(depths) if depths else 1,
                    "extraction_source": "adobe_api",
                    "is_table": any(t == "table" for t in elem_types),
                }

                chunks.append(chunk)
                chunk_idx += 1

            for sub in subsections:
                sw = _subsection_word_count(sub)
                # If current empty, start new
                if not current_parts:
                    current_parts.append(sub)
                    current_word_sum = sw
                    current_start_page = sub.get("start_page")
                    current_end_page = sub.get("end_page")
                    continue

                # Would adding this sub exceed limit?
                if current_word_sum + sw > max_words and current_parts:
                    # finalize current chunk
                    _finalize(current_parts)
                    # start new accumulation
                    current_parts = [sub]
                    current_word_sum = sw
                    current_start_page = sub.get("start_page")
                    current_end_page = sub.get("end_page")
                else:
                    current_parts.append(sub)
                    current_word_sum += sw
                    current_end_page = sub.get("end_page")

            # flush remaining
            if current_parts:
                _finalize(current_parts)

        logger.info(f"[CHUNKER_V2] Created {len(chunks)} chunks")
        return chunks
