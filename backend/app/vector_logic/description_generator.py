import json
import time
from pathlib import Path
from typing import Any
from openai import OpenAI
from app.core.config import settings





def _safe_join_text(parts: list[str], *, max_chars: int) -> str:
    out: list[str] = []
    total = 0
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(p) > remaining:
            out.append(p[:remaining])
            total = max_chars
            break
        out.append(p)
        total += len(p)
    return "\n\n".join(out)


def _build_document_content_from_ocr(
    *,
    ocr_metadata: dict | None,
    ocr_pages_text: list[str] | None,
    chunks: list[dict[str, Any]] | None,
    max_chars: int,
) -> str:
    meta = ocr_metadata or {}
    parts: list[str] = []

    meta_bits: list[str] = []
    for k in (
        "document_title",
        "total_pages",
        "doc_type",
        "domain",
        "solution_area",
        "industry",
        "technology",
        "use_case",
        "entity",
    ):
        v = meta.get(k)
        if v is None or v == "":
            continue
        meta_bits.append(f"{k}: {v}")
    if meta_bits:
        parts.append("OCR_METADATA\n" + "\n".join(meta_bits))

    if ocr_pages_text:
        page_samples = []
        if len(ocr_pages_text) <= 4:
            page_samples = ocr_pages_text
        else:
            page_samples = [ocr_pages_text[0], ocr_pages_text[1], ocr_pages_text[len(ocr_pages_text) // 2], ocr_pages_text[-1]]
        parts.append("OCR_PAGE_SAMPLES\n" + "\n\n".join((t or "").strip() for t in page_samples if (t or "").strip()))

    if chunks:
        texts = [(c.get("text") or "").strip() for c in chunks]
        texts = [t for t in texts if t]
        if texts:
            if len(texts) <= 8:
                chunk_samples = texts
            else:
                chunk_samples = texts[:4] + [texts[len(texts) // 2]] + texts[-3:]
            parts.append("STRUCTURED_CHUNK_SAMPLES\n" + "\n\n".join(chunk_samples))

    return _safe_join_text(parts, max_chars=max_chars)


def generate_description_from_ocr(
    *,
    title: str,
    category: str | None,
    ocr_metadata: dict | None,
    ocr_pages_text: list[str] | None,
    chunks: list[dict[str, Any]] | None,
    openai_api_key: str | None = None,
    domain: str | None = None,
) -> tuple[str, dict | None]:
    """
    Generate document description using OpenAI API.
    
    Args:
        title: Document title
        category: Document category
        ocr_metadata: OCR-derived global metadata
        ocr_pages_text: Optional OCR page text (raw)
        chunks: OCR-derived structured chunks (should align with stored corpus)
        openai_api_key: OpenAI API key (if None, uses settings)
    
    Returns:
        Generated description
    """
    if not openai_api_key:
        openai_api_key = getattr(settings, 'openai_api_key', None)
    
    if not openai_api_key:
        print("Warning: OpenAI API key not configured. Returning default description.")
        fallback_description = f"Document: {title}" + (f" (Category: {category})" if category else "")
        return fallback_description, None

    try:
        max_content_chars = 300000

        document_content = _build_document_content_from_ocr(
            ocr_metadata=ocr_metadata,
            ocr_pages_text=ocr_pages_text,
            chunks=chunks,
            max_chars=max_content_chars,
        )

        if not (document_content or "").strip():
            raise ValueError("No OCR-derived text available to generate description")

        prompt = f"""You are an expert document analysis system.

Analyze the provided document content and generate structured metadata as a JSON object.
This metadata helps an AI agent decide whether this document should be used to answer a user query.

DOCUMENT INFORMATION
Title: {title}
Category: {category or "General"}

DOCUMENT CONTENT
{document_content}

---

Return ONLY valid JSON with exactly these keys (no extra keys, no markdown fences):

{{
  "PRIMARY_ENTITY": "Main organization, product, client, or system discussed.",
  "DOCUMENT_DOMAIN": "Industry or field inferred from the document.",
  "DOCUMENT_TYPE": "proposal | technical document | framework | report | policy | presentation | guide | case study",
  "DOCUMENT_PURPOSE": "2–3 sentences describing what the document is about and why it exists.",
  "MAIN_TOPICS": ["Array of major topics or sections covered."],
  "BUSINESS_PROBLEMS": ["Array of problems, challenges, or gaps discussed."],
  "SOLUTIONS_OR_METHODS": ["Array of solutions, frameworks, strategies, or approaches proposed."],
  "TOOLS_AND_TECHNOLOGIES": ["Array of tools, technologies, platforms, or systems mentioned with their role."],
  "KEY_ENTITIES": ["Important concepts, processes, workflows, or named elements."],
  "ENUMERATED_CONTENT": ["Every bullet/numbered list item exactly as written in the document. Do NOT summarize."],
  "TIMELINE_OR_PHASES": ["Phases, milestones, timelines, or stages. Empty array if none."],
  "EXAMPLE_QUESTIONS": ["Example user questions this document can answer."],
  "KEYWORDS": ["15–25 important domain and semantic keywords."],
  "ROUTING_KEYWORDS": ["Keywords or phrases users might include when asking questions about this document."],
  "QUERY_MATCH_SIGNALS": ["Specific phrases or terminology that should trigger this document during routing."],
  "CONTEXT_SUMMARY": "5–7 sentence paragraph explaining the document for AI routing."
}}

CRITICAL RULES:
1. Output ONLY the JSON object — no text before or after.
2. Capture ALL important topics from the document.
3. Never summarize bullet lists — extract every item exactly.
4. The metadata must represent the entire document.
5. EXAMPLE_QUESTIONS should reflect realistic user queries.
6. All array fields must be actual JSON arrays."""

        client = OpenAI(api_key=openai_api_key)

        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a metadata extraction system. Output ONLY valid JSON, no markdown, no explanations.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=3200,
                    temperature=0.1,
                )
                break
            except Exception as e:
                if attempt == 2:
                    raise e
                time.sleep(2)

        raw_content = response.choices[0].message.content.strip()

        if raw_content.startswith("```"):
            raw_content = raw_content.split("```", 2)[-1]
            if raw_content.startswith("json"):
                raw_content = raw_content[4:]
            closing = raw_content.rfind("```")
            if closing != -1:
                raw_content = raw_content[:closing]
            raw_content = raw_content.strip()

        try:
            parsed = json.loads(raw_content)
            description = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            print("[WARNING] Description generator returned non-JSON content, storing as plain text.")
            description = raw_content

        usage_info = None
        if hasattr(response, "usage"):
            usage_info = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return description, usage_info

    except Exception as e:
        print(f"Error generating description with OpenAI: {str(e)}")
        fallback_description = json.dumps(
            {
                "PRIMARY_ENTITY": title,
                "DOCUMENT_DOMAIN": domain or "General",
                "DOCUMENT_TYPE": "Unknown",
                "DOCUMENT_PURPOSE": f"Document: {title}" + (f" (Category: {category})" if category else ""),
                "MAIN_TOPICS": [],
                "BUSINESS_PROBLEMS": [],
                "SOLUTIONS_OR_METHODS": [],
                "TOOLS_AND_TECHNOLOGIES": [],
                "KEY_ENTITIES": [],
                "ENUMERATED_CONTENT": [],
                "TIMELINE_OR_PHASES": [],
                "EXAMPLE_QUESTIONS": [],
                "KEYWORDS": [],
                "ROUTING_KEYWORDS": [],
                "QUERY_MATCH_SIGNALS": [],
                "CONTEXT_SUMMARY": f"Document: {title}",
            },
            indent=2,
        )
        return fallback_description, None





def refine_description(
    current_description: str,
    title: str,
    category: str | None,
    openai_api_key: str | None = None,
    domain: str | None = None
) -> str:
    """
    Refine an existing description based on additional context.
    This can be used in a feedback loop to improve descriptions.
    
    Args:
        current_description: Current description to refine
        title: Document title
        category: Document category
        openai_api_key: OpenAI API key
    
    Returns:
        Refined description
    """
    if not openai_api_key:
        openai_api_key = getattr(settings, 'openai_api_key', None)
    
    if not openai_api_key:
        return current_description
    
    try:
        prompt = f"""Improve the document metadata for AI routing.

The refined description must:
- preserve all extracted entities
- improve clarity of topics
- strengthen routing keywords
- improve query match signals
- avoid removing important enumerated lists

Document Title: {title}
Category: {category or 'Uncategorized'}
Domain: {domain or 'Unspecified'}
Current Description: {current_description}"""

        client = OpenAI(api_key=openai_api_key)
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that refines document descriptions to improve searchability."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=800,
            temperature=0.7
        )
        
        refined_description = response.choices[0].message.content.strip()
        return refined_description
        
    except Exception as e:
        print(f"Error refining description: {str(e)}")
        return current_description


    # ============================================================
# CATEGORY DESCRIPTION GENERATOR
# Uses Few-Shot + Structured Output for optimal search matching
# ============================================================

def generate_category_description(
    category_name: str,
    document_summaries: str,
    openai_api_key: str | None = None,
    domain: str | None = None
) -> str:
    """
    Generate agent-optimized category metadata.

    The output is a compact JSON structure designed specifically
    for deep-agent routing and category selection.

    This metadata helps the agent quickly determine which category
    should be used to answer a user query.
    """

    # Use API key from settings if not provided
    if not openai_api_key:
        openai_api_key = getattr(settings, "openai_api_key", None)

    if not openai_api_key:
        print("[WARNING] No OpenAI API key found")
        return json.dumps({
            "category_name": category_name,
            "summary": f"Documents related to {category_name}"
        })

    try:
        # Domain hint (helps LLM understand context)
        domain_hint = ""
        if domain:
            domain_hint = f"\nDOMAIN CONTEXT: {domain}"

        prompt = f"""
You are generating structured metadata to help an AI routing agent
select the correct document collection.

Your job is to analyze document summaries and extract concise signals
that help determine WHEN this category should be used.

Return ONLY valid JSON.

{domain_hint}

CATEGORY NAME:
{category_name}

DOCUMENT SUMMARIES:
{document_summaries}

Return JSON with this structure:

{{
  "category_name": "...",

  "summary": "1–2 sentences describing what this collection contains",

  "routing_hint": "When the AI should use this category",

  "document_types": ["proposal", "report", "case study"],

  "primary_topics": ["main themes across the documents"],

  "routing_keywords": ["important words users might use in queries"],

  "example_questions": [
    "Example user questions answerable by this category"
  ],

  "key_entities": ["client names", "project names"],

  "key_tools": ["tools", "platforms", "technologies"],

  "document_titles": ["titles extracted from documents"]
}}

RULES:

1. Keep JSON compact and informative
2. Do NOT include explanations
3. Extract only high-value routing signals
4. Avoid long bullet lists
5. Focus on helping the AI choose the correct category
"""

        client = OpenAI(api_key=openai_api_key)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a metadata extraction system. Output only JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.1,
            max_tokens=500
        )

        content = response.choices[0].message.content.strip()

        # Validate JSON
        try:
            parsed = json.loads(content)
        except Exception:
            print("[WARNING] LLM returned invalid JSON, fixing...")
            parsed = {
                "category_name": category_name,
                "summary": content
            }

        print(f"[SUCCESS] Generated routing metadata for '{category_name}'")

        return json.dumps(parsed, indent=2)

    except Exception as e:
        print(f"[ERROR] generate_category_description failed: {str(e)}")

        return json.dumps({
            "category_name": category_name,
            "summary": f"Documents related to {category_name}"
        })


def convert_plain_description_to_json(title: str, category: str, old_description: str, openai_api_key: str) -> str:
    """
    Fallback function: Converts an existing plain-text description into the new structured JSON format.
    Used when the original PDF file is missing but we want to upgrade the record to JSON.
    """
    import json as _json
    if not openai_api_key:
        return old_description

    prompt = f"""You are a metadata migration expert.
    
    Convert the following plain-text document description into a structured JSON object.
    Map the existing information to the relevant JSON keys. If information for a key is missing, 
    infer it logically from the title or description text.
    
    DOCUMENT INFORMATION
    Title: {title}
    Category: {category or "General"}
    
    EXISTING DESCRIPTION
    {old_description}
    
    ---
    
    Return ONLY valid JSON with exactly these keys:
    
    {{
      "PRIMARY_ENTITY": "organization/product",
      "DOCUMENT_DOMAIN": "Industry/Field",
      "DOCUMENT_TYPE": "proposal | technical document | framework | report | policy | presentation | guide | case study",
      "DOCUMENT_PURPOSE": "2–3 sentence summary",
      "MAIN_TOPICS": ["array of topics"],
      "BUSINESS_PROBLEMS": ["array of problems"],
      "SOLUTIONS_OR_METHODS": ["array of solutions"],
      "TOOLS_AND_TECHNOLOGIES": ["array of tools"],
      "KEY_ENTITIES": ["concepts/named elements"],
      "ENUMERATED_CONTENT": ["bullet items if any, or key points"],
      "TIMELINE_OR_PHASES": ["phases if any"],
      "EXAMPLE_QUESTIONS": ["sample questions"],
      "KEYWORDS": ["domain keywords"],
      "ROUTING_KEYWORDS": ["routing keywords"],
      "QUERY_MATCH_SIGNALS": ["exact trigger phrases"],
      "CONTEXT_SUMMARY": "5–7 sentence paragraph for AI routing"
    }}
    
    CRITICAL RULES:
    1. Output ONLY the JSON object.
    2. All array fields must be JSON arrays.
    """

    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a JSON metadata converter. Respond ONLY with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        
        result = response.choices[0].message.content.strip()
        # Clean markdown if model ignored the system prompt
        if "```json" in result:
            result = result.split("```json")[1].split("```")[0].strip()
        elif "```" in result:
            result = result.split("```")[1].split("```")[0].strip()
            
        # Validate JSON
        _json.loads(result)
        return result
    except Exception as e:
        print(f"Fallback JSON conversion failed: {e}")
        return old_description
