# Structured Chunking v2 — Deterministic, Heading‑Anchored Redesign

TL;DR: Replace the current Adobe-based chunker with a deterministic, rule-only, heading-anchored system operating on normalized JSON. Each H1 defines a primary section chunk; H2/H3 and all body blocks (paragraphs, lists, tables) attach to the active section. Content spanning pages is merged under its H1 until the next H1. Text is never modified or summarized. Splitting occurs only at subheading boundaries (H3 then H2). The new module `app/pdf_extraction/structured_chunker_v2.py` integrates into the existing pipeline, deprecates the old chunker, and computes safe chunk sizes from OpenAI TPM/RPM and model context with a safety buffer. Target model context is GPT‑4o (128k). Retrieval keeps dynamic top_k, while budgeting assumes a safe default.

---

## 1) Problem Statement
The current chunking implementation processes raw Adobe `elements[]` and uses character thresholds to flush buffers. This yields non-deterministic groupings, can split inside sections, and does not explicitly merge content across pages by heading hierarchy. We need a deterministic chunker that:
- Consumes normalized JSON with per-page semantic arrays and (optionally) ordered blocks.
- Anchors chunks at H1 and keeps H2/H3 and body content within the parent H1.
- Merges scattered content under the same H1 across pages.
- Never alters text; only groups it.
- Splits only at subheading boundaries, never mid-paragraph/list/table.
- Emits rich, consistent metadata for downstream components.

## 2) Why Old Chunking Is Insufficient
- Operates directly on raw Adobe elements, re-detecting types ad hoc and relying on buffer size thresholds.
- May split mid-section due to character-based MAX size flushes.
- No explicit cross-page continuity by heading; section context can be lost or fragmented.
- Weak guarantees on nearest-heading attachment for lists/tables.
- Not aligned with TPM/RPM-aware sizing or strict reproduction requirements.

Relevant files for reference:
- Old chunker: [app/pdf_extraction/structured_chunking.py](../app/pdf_extraction/structured_chunking.py)
- Normalizer: [app/pdf_extraction/normalizer.py](../app/pdf_extraction/normalizer.py)
- Processor: [app/vector_logic/processor.py](../app/vector_logic/processor.py)
- Metadata augmentation: [app/pdf_extraction/metadata_augmentation.py](../app/pdf_extraction/metadata_augmentation.py)

## 3) Design Architecture (Textual Diagram)
Extraction (Adobe) → Normalization → Structured Chunking v2 → Metadata Augmentation → Embedding/Store → Retrieval
- Adobe Extract (unchanged) produces `elements[]`.
- Normalizer enhances structure into pages with semantic arrays and optional ordered `blocks`.
- Structured Chunker v2 consumes normalized JSON and deterministically groups by H1/H2/H3.
- Metadata Augmentation enriches chunks with document/section metadata for ChromaDB.
- Vector store persists embeddings and metadata.

Key integration points:
- Normalize in processor, then chunk: see [app/vector_logic/processor.py](../app/vector_logic/processor.py#L200-L260).

## 4) Chunking Algorithm Breakdown
Input shape (normalized):
- `{ "pages": [ { "page_number": n, "h1": [], "h2": [], "h3": [], "p": [], "list": [ [..] ], "table": [], "blocks"?: [ {type, text, page_number} ... ] } ] }`

State machine and grouping:
- Maintain active headings: `H1`, `H2`, `H3` across page boundaries.
- Each new `H1` starts a new major chunk (primary section anchor).
- `H2/H3` always remain within the current `H1` chunk.
- Paragraphs, lists, and tables attach to the nearest active heading: prefer `H3`, else `H2`, else `H1`.
- If a page has no `H1` but contains content, attach it to the previous active `H1` (cross-page merge).
- Lists/tables are atomic blocks; represent lists as bullet-joined text; do not rewrite or summarize.

Ordering and determinism:
- If `page.blocks` exists (recommended), iterate in order, updating heading state and attaching each block deterministically.
- If `blocks` is absent, process per page by applying new headings first (in their recorded order), then attach all `p`, then `list`, then `table`. This is a documented, deterministic fallback.

Splitting rules:
- Calculate `word_count` while accumulating under `H1`. If exceeding the computed threshold, split only at subheading boundaries:
  - Prefer split at `H3` boundaries; if none, split at `H2` boundaries.
  - If no subheadings exist under an oversized `H1`, do not split mid-paragraph/list/table; retain one large chunk (risk noted; retrieval token budget and pruning mitigate).

Emitted per chunk (required):
- `chunk_index`, `text`, `char_count`, `word_count`, `page_number` (first page encountered), `start_page`, `end_page`,
- `heading_level_1`, `heading_level_2`, `heading_level_3`, `section` (e.g., `H1 > H2 > H3`), `hierarchy_depth` (1–3),
- `element_type` (dominant category) and `element_types` (all), `extraction_source` (e.g., `adobe_api`), `is_table`.

Zero-modification invariant:
- The chunker never edits, adds, removes, or summarizes text. It only groups existing blocks. Block text is concatenated with single newlines between blocks.

## 5) TPM/RPM Safety Calculation Model
Configurable inputs:
- `OPENAI_TPM_LIMIT`, `OPENAI_RPM_LIMIT`, `MODEL_CONTEXT_LIMIT`, `EXPECTED_REQUESTS_PER_MINUTE`, `TARGET_TOP_K_FOR_BUDGET`, `CHUNK_SAFETY_BUFFER`.

Let:
- $C = MODEL\_CONTEXT\_LIMIT$ (tokens), target GPT‑4o: $C = 128000$.
- $R_p =$ reserved prompt/system tokens.
- $R_a =$ reserved answer tokens.
- $K =$ top_k chunks included per query (retrieval is dynamic; budgeting uses a conservative default, e.g., 6).
- $B =$ safety buffer multiplier (default 0.8 = 20% headroom).
- $TPM =$ `OPENAI_TPM_LIMIT`, $RPM =$ `OPENAI_RPM_LIMIT` (informational).
- $\text{req\_per\_min} =$ `EXPECTED_REQUESTS_PER_MINUTE`.

Per-request TPM allowance:
- $T_{req} = \left\lfloor \dfrac{TPM}{\text{req\_per\_min}} \right\rfloor$.

Chunk budget from context:
- $T_{chunk\_ctx} = \left\lfloor \dfrac{C - R_p - R_a}{K} \right\rfloor$.

Chunk budget from TPM:
- $T_{chunk\_tpm} = \left\lfloor \dfrac{T_{req} - (R_p + R_a)}{K} \right\rfloor$.

Safe per-chunk tokens:
- $T_{chunk\_safe} = B \times \min(T_{chunk\_ctx}, T_{chunk\_tpm})$.

Token→word estimate (typical English ~1 token ≈ 1.3 words):
- $W_{chunk\_safe} = \left\lfloor \dfrac{T_{chunk\_safe}}{1.3} \right\rfloor$.

Example (illustrative defaults; configurable):
- GPT‑4o: $C=128000$, choose $R_p=1200$, $R_a=1500$, $K=6$, $B=0.8$.
- $TPM=90000$, `EXPECTED_REQUESTS_PER_MINUTE=10` ⇒ $T_{req}=9000$.
- $T_{chunk\_ctx}=\lfloor(128000-2700)/6\rfloor=20900$.
- $T_{chunk\_tpm}=\lfloor(9000-2700)/6\rfloor=1050$.
- $T_{chunk\_safe}=0.8\times\min(20900,1050)=840$ tokens ⇒ $W_{chunk\_safe}\approx 646$ words.

Implementation will compute effective `max_tokens_per_chunk` and `max_words_per_chunk` at runtime from settings, unless explicit overrides are provided.

## 6) Config Variables to Introduce
Add to [app/core/config.py](../app/core/config.py):
- `model_context_limit: int = 128000`
- `openai_tpm_limit: int = 90000`
- `openai_rpm_limit: int = 60` (informational)
- `expected_requests_per_minute: int = 10`
- `chunk_safety_buffer: float = 0.8`
- `chunk_max_tokens_hint: int | None = None`
- `chunk_max_words_hint: int | None = None`
- `target_top_k_for_budget: int = 6`

Defaults are environment-overridable. The v2 chunker will accept an optional config dict to override computed limits.

## 7) Refactoring Plan
- Create new module: [app/pdf_extraction/structured_chunker_v2.py](../app/pdf_extraction/structured_chunker_v2.py)
  - Public API: `StructuredChunkerV2.chunk_normalized(normalized: dict, config: dict | None = None) -> list[dict]`.
  - Internal helpers: deterministic iterator over `pages`/`blocks`, `SectionState`, `Accumulator`, `split_at_subheadings`, `finalize_chunk`.
  - Logging: `logging.getLogger(__name__)` with tags like `[CHUNKER_V2]` for consistency.
  - Errors: raise `ValueError` for malformed inputs; never attempt recovery by altering content.
- Extend normalizer: [app/pdf_extraction/normalizer.py](../app/pdf_extraction/normalizer.py)
  - Optional `page["blocks"]` preserving original sequence: entries as `{ "type": "h1|h2|h3|p|list|table", "text": str, "page_number": int }`.
  - Keep existing `h1/h2/h3/p/list/table` arrays for backward compatibility.
- Replace integration in processor: [app/vector_logic/processor.py](../app/vector_logic/processor.py)
  - After normalization, call `StructuredChunkerV2.chunk_normalized(normalized, computed_config)`.
  - Remove call to `StructuredChunker.chunk_adobe_json`.
- Deprecate v1: keep [app/pdf_extraction/structured_chunking.py](../app/pdf_extraction/structured_chunking.py) with a top-of-file deprecation note. Optionally route v1 API to normalize+v2 to avoid external breakage.
- No changes to embedding logic in [app/vector_logic/vector_store.py](../app/vector_logic/vector_store.py) or retrieval; downstream fields preserved.

## 8) Backward Compatibility Strategy
- Hard replace (per decision): switch processor to v2 with no feature flag, while leaving v1 file in place with a clear deprecation header.
- Field compatibility: continue providing `chunk_index`, `page_number`, `section`, `char_count`, `word_count`, `heading_level_1/2/3`, `element_type`, `extraction_source`, `is_table`, and `element_types` so analyzer/augmentation remain unchanged.

## 9) Testing Strategy
Unit tests (pure, fast):
- H1 anchor → new chunk; H2/H3 remain under current H1.
- Cross-page continuity: pages without H1 attach to previous H1.
- Attachment precedence: p/list/table attach to nearest heading (`H3 > H2 > H1`).
- Lists/tables are atomic; verify no mid-block splits.
- Splitting only at subheadings; verify behavior when only H2 or no subheadings.
- Zero-modification invariant: concatenation of added block texts equals input in sequence when `blocks` present.

Integration tests:
- Use sample normalized inputs from [app/extraction_analysis](../app/extraction_analysis) to produce chunks; validate `start_page/end_page`, `hierarchy_depth`, `title`, and metadata.
- Processor smoke: run the normalization+chunk path without embeddings to ensure end-to-end flow.

## 10) Performance Impact Analysis
- Time complexity O(N) over normalized blocks; memory proportional to current accumulating section.
- Fewer, more meaningful chunks; embeddings throughput remains the bottleneck (unchanged).
- Logging guarded at info/debug; no LLM calls; negligible overhead versus v1.

## 11) Risk Assessment
- Absent `blocks` ordering reduces precision in associating body content to exact preceding headings; fallback is deterministic but less granular.
- Very long H1 without subheadings may exceed computed thresholds since mid-block splits are forbidden; mitigated by retrieval token budgeting and scoring/pruning.
- Duplicate H1 titles across document may visually resemble one section; algorithm uses document order, not title identity, to separate sections.
- Complex table/list nesting flattened to text preserves content but may reduce structure expressiveness; flagged via `is_table` and `element_types`.

## 12) Rollback Strategy
- Keep v1 module intact with a prominent deprecation notice.
- Single import/call-site reversion in [app/vector_logic/processor.py](../app/vector_logic/processor.py) restores v1 behavior.
- No schema or data migrations required; ChromaDB metadata field set remains compatible.

---

### Decisions Incorporated
- Model context: GPT‑4o (128k).
- Top_k: dynamic (retrieval decides); budgeting default used for safety.
- Compatibility: hard replace (no runtime flag).
- Normalizer: extend with ordered `blocks` while retaining current fields.

### Verification Checklist (post-implementation)
- Normalized JSON saved to `app/extraction_analysis` includes `blocks`.
- Chunks contain required metadata and never modify text.
- Analyzer report renders expected counts and samples.
- Computed `T_chunk_safe` and `W_chunk_safe` logged once at startup/chunking.

### Out of Scope (for this change)
- Any LLM involvement in chunking.
- Embedding/ranking algorithm changes beyond chunk boundaries.
- Major retrieval pipeline changes (kept compatible by field parity).
