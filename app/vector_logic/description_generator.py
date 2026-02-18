"""
Generate document descriptions using OpenAI API based on document content.
"""
import pdfplumber
from pathlib import Path
from openai import OpenAI
from app.core.config import settings


def extract_text_from_pdf(file_path: str, max_chars: int = None) -> str:
    """
    Extract text from PDF file (full document for description generation).
    
    Args:
        file_path: Path to PDF file
        max_chars: Maximum characters to extract (None = extract all, for API token limits)
    
    Returns:
        Extracted text content (full PDF or truncated if max_chars specified)
    """
    text_content = []
    total_chars = 0
    
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    # If max_chars is None, extract all pages
                    if max_chars is None:
                        text_content.append(page_text)
                    else:
                        # If max_chars is specified, respect the limit
                        if total_chars + len(page_text) > max_chars:
                            # Add partial text to reach max_chars
                            remaining = max_chars - total_chars
                            text_content.append(page_text[:remaining])
                            break
                        text_content.append(page_text)
                        total_chars += len(page_text)
    except Exception as e:
        print(f"Error extracting text from PDF: {str(e)}")
        return ""
    
    return "\n\n".join(text_content)


def generate_description(
    title: str,
    category: str | None,
    file_path: str,
    openai_api_key: str | None = None,
    domain: str | None = None
) -> tuple[str, dict | None]:
    """
    Generate document description using OpenAI API.
    
    Args:
        title: Document title
        category: Document category
        file_path: Path to the document file
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
        # Extract text from document (full PDF)
        file_ext = Path(file_path).suffix.lower()
        
        if file_ext == '.pdf':
            # Extract full PDF content
            document_content = extract_text_from_pdf(file_path, max_chars=None)
        else:
            # For other file types, you can add extraction logic here
            document_content = f"Document title: {title}"
        
        # Check content length and handle token limits intelligently
        # OpenAI has token limits, so we may need to truncate if too long
        # GPT-4o-mini context window is 128k tokens, but we want to leave room for response
        # Roughly 1 token = 4 characters, so 100k tokens = ~400k characters
        # We'll use a safe limit of 300k characters to leave room for prompt and response
        max_content_chars = 300000  # ~75k tokens for content
        
        if len(document_content) > max_content_chars:
            print(f"Document content is very long ({len(document_content)} chars). Truncating to {max_content_chars} chars for description generation.")
            document_content = document_content[:max_content_chars] + "\n\n[Content truncated for description generation...]"
        
        # Prepare prompt for OpenAI
        domain_lower = (domain or '').lower()
        domain_instructions = ""
        if domain_lower:
            if any(k in domain_lower for k in ["proposal", "proposals"]):
                domain_instructions = "Prioritize client, scope, deliverables, approach, milestones, pricing cues, and acceptance criteria."
            elif any(k in domain_lower for k in ["policy", "policies", "strategy"]):
                domain_instructions = "Prioritize rules, enforcement, compliance frameworks (e.g., ISO/SOC2), scope/coverage, exceptions, and definitions."
            elif any(k in domain_lower for k in ["icp", "profiles"]):
                domain_instructions = "Prioritize personas, pain points, buying triggers, evaluation criteria, objections, and messaging hooks."
            elif any(k in domain_lower for k in ["devops", "platform", "cloud"]):
                domain_instructions = "Prioritize cloud platforms, CI/CD, IaC, reliability, security controls, observability, and deployment strategies."
            elif any(k in domain_lower for k in ["qa", "testing", "quality"]):
                domain_instructions = "Prioritize test strategy, automation tooling, coverage, flaky detection, environments, and reporting."
            elif any(k in domain_lower for k in ["security", "infosec"]):
                domain_instructions = "Prioritize security controls, threat models, cert management, secrets, compliance, and mitigations."
        if domain_instructions:
            domain_instructions = f"\n\nDOMAIN FOCUS ({domain}): {domain_instructions}"
        prompt = f"""You are extracting structured metadata to help an AI router select this document correctly.

=== DOCUMENT INFO ===
Title: {title}
Category: {category or 'General'}
    Domain: {domain or 'Unspecified'}{domain_instructions}

=== DOCUMENT CONTENT ===
{document_content}

=== EXTRACT IN THIS EXACT FORMAT ===

PRIMARY_ENTITY: [Main client/company name (e.g., "Keysight", "Dimagi", "Benow") - CRITICAL for routing. If multiple, list primary first.]

DOCUMENT_TYPE: [proposal | report | guide | policy | presentation | audit]

COVERAGE: [complete | partial | overview] - Is this comprehensive or just an overview?

SUMMARY: [2-3 sentences - What this document is specifically about. Include the client name and main purpose.]

USE_WHEN: [Question patterns this doc answers. E.g., "Questions about Keysight DevOps approach", "Automation framework for Dimagi"]

EXAMPLE_QUESTIONS: 
- [Example question 1 this doc can answer]
- [Example question 2]
- [Example question 3]

TOOLS_AND_TECHNOLOGIES:
- [Tool1]: [What it does in THIS document's context]
- [Tool2]: [Purpose/role]
- [Continue for ALL tools mentioned]

SCOPE: [High-level deliverables and capabilities - what this doc covers]

ENUMERATED_SCOPE:
[CRITICAL: If the document contains ANY bullet point lists, numbered lists, or itemized content, you MUST extract EVERY SINGLE ITEM here verbatim. This is essential for answering "What are the..." questions.]
- [Item 1 exactly as written in doc]
- [Item 2 exactly as written in doc]
- [Item 3 exactly as written in doc]
- [Continue for ALL items - do NOT summarize, list EACH item]

KEY_ENTITIES:
[Extract specific named concepts, flows, processes, features that users might search for]
- [Entity 1 - e.g., "EMI flows", "cashback transactions", "DR failover"]
- [Entity 2]
- [Continue for all key searchable terms]

CONTAINS_LISTS: [true/false - Does this document have explicit bullet point lists or numbered lists?]

LIST_TOPICS: [What topics have enumerable content? e.g., "transaction journeys, tools, testing phases, deliverables"]

TIMELINE: [If phases/months mentioned, list them. Otherwise "N/A"]
- [Month 1 or Phase 1]: [What happens]
- [Month 2 or Phase 2]: [What happens]

KEYWORDS: [Industry terms, methodologies, standards for semantic search - 15-20 terms]

=== CRITICAL RULES ===
1. PRIMARY_ENTITY must be the exact client/company name - this prevents wrong document routing
2. EXAMPLE_QUESTIONS should be realistic questions a user might ask
3. Extract EVERY tool/technology mentioned with its specific purpose
4. TIMELINE is critical for phase/month questions - extract if present
5. **ENUMERATED_SCOPE is CRITICAL** - If you see ANY bulleted list in the document, you MUST copy EVERY item. Never summarize lists.
6. For questions like "What are the critical transaction journeys?", the answer must be findable in ENUMERATED_SCOPE
7. KEY_ENTITIES should include specific flows, processes, features (e.g., "Full swipe", "Instant cashback", "No-cost EMI")
8. Keep each section structured for parsing"""  

        # Call OpenAI API
        client = OpenAI(api_key=openai_api_key)
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Using mini for cost efficiency, can be changed to gpt-4
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that generates clear, searchable document descriptions for a knowledge base system. Analyze the full document content provided and create comprehensive descriptions."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=2500,  # Increased for ENUMERATED_SCOPE extraction
            temperature=0.3  # Lower for consistent extraction
        )
        
        description = response.choices[0].message.content.strip()
        
        # Extract token usage information
        usage_info = None
        if hasattr(response, 'usage'):
            usage_info = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
        
        return description, usage_info
        
    except Exception as e:
        print(f"Error generating description with OpenAI: {str(e)}")
        # Fallback description
        fallback_description = f"Document: {title}" + (f" (Category: {category})" if category else "") + (f" [Domain: {domain}]" if domain else "")
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
        prompt = f"""Refine and improve the following document description to make it more searchable and informative.

Document Title: {title}
Category: {category or 'Uncategorized'}
    Domain: {domain or 'Unspecified'}
Current Description: {current_description}

Please provide an improved description that:
1. Is more concise and clear
2. Better captures key searchable terms
3. Highlights when this document would be most useful
4. Includes relevant metadata for better search results"""

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
    Generate a category description by analyzing all document summaries.
    Uses Few-Shot + Structured Output prompting for consistent, searchable results.
    
    Args:
        category_name: Name of the category
        document_summaries: Combined summaries of all documents in category
        openai_api_key: Optional API key (uses settings if not provided)
    
    Returns:
        Generated category description string
    """
    # Get API key from settings if not provided
    if not openai_api_key:
        openai_api_key = getattr(settings, 'openai_api_key', None)
    
    # Fallback if no API key available
    if not openai_api_key:
        print("[WARNING] No OpenAI API key available for category description generation")
        return f"Category: {category_name}"
    
    try:
        # Few-Shot + Structured Output Prompt (Optimized for search)
               # Optimized prompt for routing-aware category descriptions
              # Optimized prompt for routing-aware category descriptions
        domain_lower = (domain or '').lower()
        domain_intro = ""
        if domain_lower:
            if any(k in domain_lower for k in ["proposal", "proposals"]):
                domain_intro = "Focus on client, scope, deliverables, approach, and milestones."
            elif any(k in domain_lower for k in ["policy", "policies", "strategy"]):
                domain_intro = "Focus on rules, enforcement, compliance, scope/coverage, and exceptions."
            elif any(k in domain_lower for k in ["icp", "profiles"]):
                domain_intro = "Focus on personas, pain points, decision criteria, and messaging."
            elif any(k in domain_lower for k in ["devops", "platform", "cloud"]):
                domain_intro = "Focus on CI/CD, cloud/IaC, reliability, security, and observability."
            elif any(k in domain_lower for k in ["qa", "testing", "quality"]):
                domain_intro = "Focus on test tooling, coverage, flakiness, environments, and reporting."
            elif any(k in domain_lower for k in ["security", "infosec"]):
                domain_intro = "Focus on controls, certs/secrets, threat modeling, and compliance."
        domain_block = f"\n\nDOMAIN: {domain} — {domain_intro}" if domain_intro else (f"\n\nDOMAIN: {domain}" if domain else "")

        prompt = f"""You are extracting metadata to help an AI router select the correct document collection.{domain_block}

=== EXAMPLE ===
INPUT: 
- Doc1: Test automation framework audit, flaky test detection
- Doc2: Selenium/Cypress automation setup

OUTPUT:
SUMMARY: Test automation proposals covering framework audits and tooling.
TOOLS: Selenium, Cypress, pytest, Appium, BrowserStack, Jenkins
SCOPE: automation audit, framework design, test coverage analysis, flaky test detection
ENUMERATED_ITEMS:
- Selenium WebDriver setup
- Cypress E2E testing
- pytest fixtures
- Flaky test detection
- Test coverage reporting
CLIENTS: Dimagi
KEYWORDS: QA, regression testing, test automation, framework optimization
SYNONYMS: test automation, QA automation, framework audit, automation audit

=== NOW EXTRACT ===
Category: {category_name}

Documents:
{document_summaries}

=== OUTPUT (exact format) ===
SUMMARY: [What this category contains - 2 sentences max]
PRIMARY_ENTITIES: [Company names, project names, client names - used for hard routing]
DOCUMENT_TYPES: [Proposal, Report, Audit, SOW, Technical Spec - helps match intent]
TOOLS: [ALL tools/platforms across ALL documents - exhaustive comma-separated list]
SCOPE: [Main deliverables and capabilities - what problems these docs solve]

ENUMERATED_ITEMS:
[CRITICAL: Extract EVERY bullet point, list item, feature, flow, journey mentioned in any document]
- [Item 1 - exact term from documents]
- [Item 2 - exact term from documents]
- [Continue for ALL items - this is essential for "What are the..." questions]

KEY_ENTITIES:
[Specific searchable terms: flows, transactions, processes, features]
- [Entity 1 - e.g., "EMI flows", "DR failover", "cashback transactions"]
- [Entity 2]

KEYWORDS: [Industry terms for semantic search - 15-20 terms]
SYNONYMS: [Alternative phrases users might use - include abbreviations and variations]

CONTAINS_LISTS: [true/false - Does this category have documents with scope lists, feature lists, bullet points?]
LIST_TOPICS: [Topics that have enumerable content, e.g., "transaction journeys, tools, testing phases"]
HAS_SCOPE_SECTIONS: [true/false - Do documents have explicit Scope/Coverage/Includes sections?]

QUESTION_PATTERNS:
- Extract questions: "What are the...", "List the...", "Which ... are included"
- Yes/No questions: "Does ... include...", "Is ... part of..."
- Explain questions: "How does...", "What is the approach for..."

CRITICAL RULES:
1. Extract EVERY tool from ALL documents (missing = routing failure)
2. PRIMARY_ENTITIES must list ALL company/client names exactly as written
3. **ENUMERATED_ITEMS is CRITICAL** - Extract EVERY list item from documents verbatim
4. For "What are the transaction journeys?" the answer MUST be in ENUMERATED_ITEMS
5. KEY_ENTITIES should include specific flows, processes, features (e.g., "Full swipe", "Instant cashback")
6. CONTAINS_LISTS must be accurate - this affects answer extraction
7. If unsure, set CONTAINS_LISTS: true (better to over-search than miss)"""

        # Call OpenAI API
        client = OpenAI(api_key=openai_api_key)
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Fast and cost-effective for structured extraction
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise metadata extractor. Output ONLY the structured format. No explanations."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=800,  # Increased for ENUMERATED_ITEMS
            temperature=0.1  # Very low for consistent extraction
        )
        
        generated_description = response.choices[0].message.content.strip()
        print(f"[SUCCESS] Generated category description for '{category_name}'")
        return generated_description
        
    except Exception as e:
        print(f"[ERROR] generate_category_description failed: {str(e)}")
        return f"Category: {category_name}"