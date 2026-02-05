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
    openai_api_key: str | None = None
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
        prompt = f"""Analyze the following document and generate a concise, informative description that will help with search and retrieval.

Document Title: {title}
Category: {category or 'Uncategorized'}

Full Document Content:
{document_content}

Please generate:
1. A comprehensive summary (5-6 sentences) of what this document contains
2. Key topics, themes, and main points covered
3. When this document would be useful to reference
4. Any important metadata, sections, or details that would help with search

Format the response as a clear, searchable description that captures the essence and utility of this document. Base your description on the complete document content provided above."""

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
            max_tokens=2000,  # Increased for more detailed descriptions
            temperature=0.7
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
        fallback_description = f"Document: {title}" + (f" (Category: {category})" if category else "")
        return fallback_description, None


def refine_description(
    current_description: str,
    title: str,
    category: str | None,
    openai_api_key: str | None = None
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

