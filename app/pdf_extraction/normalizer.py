"""
Adobe extraction normalizer

Converts Adobe structuredData.json (elements[]) into a compact, page-wise
normalized schema ready for LLM-based or rule-based chunking.

Phase-1: focus on semantic content (H1/H2/H3/P/LIST/TABLE), drop styles.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional


def _infer_type_from_path(path: str) -> str:
    p = (path or "").lower()
    if "/h1" in p:
        return "h1"
    if "/h2" in p:
        return "h2"
    if "/h3" in p:
        return "h3"
    if "/table" in p:
        return "table"
    if "/list" in p or "/li" in p:
        return "list"
    if "/p" in p:
        return "p"
    return "other"


def _serialize_table(element: Dict[str, Any]) -> str:
    """Serialize a table element into a compact multiline string.

    If nested rows/cells exist under element['elements'], join cells with ' | '
    and rows with newlines. Fallback to element['Text'].
    """
    rows: List[str] = []
    for row in element.get("elements", []):
        cells: List[str] = []
        for cell in row.get("elements", []):
            # Adobe uses capital 'Text'
            txt = (cell.get("Text") or "").strip()
            if txt:
                cells.append(txt)
        if cells:
            rows.append(" | ".join(cells))
    if rows:
        return "\n".join(rows)
    # Fallback
    return (element.get("Text") or "").strip()


def _collect_list_items(element: Dict[str, Any]) -> List[str]:
    """Collect list items under a list element, if nested structure exists."""
    items: List[str] = []
    for item in element.get("elements", []):
        txt = (item.get("Text") or "").strip()
        if txt:
            items.append(txt)
    return items


def normalize_adobe_elements(
    extracted_json: Dict[str, Any],
    document_id: int,
    schema_version: str = "1.0"
) -> Dict[str, Any]:
    """Normalize Adobe elements into a page-structured schema.

    Returns a dict with keys: version, document_id, created_at, pages.
    Each page has: page_number, h1, h2, h3, p, list, table.
    """
    elements = extracted_json.get("elements", []) or []

    # Group elements by page (convert to 1-based for readability)
    pages: Dict[int, Dict[str, Any]] = {}

    # For aggregating consecutive LI into a list block when only flat elements are present
    current_list: List[str] = []
    current_list_page: Optional[int] = None

    def _flush_current_list():
        nonlocal current_list, current_list_page
        if current_list and current_list_page is not None:
            page = pages.setdefault(current_list_page, _new_page(current_list_page))
            page["list"].append(current_list[:])
            # Also append ordered block for the aggregated flat list
            page["blocks"].append({
                "type": "list",
                "text": "\n".join([f"• {i}" for i in current_list]),
                "page_number": current_list_page,
            })
        current_list = []
        current_list_page = None

    for elem in elements:
        # Page is 0-based in Adobe; normalize to 1-based
        page_zero = elem.get("Page")
        if page_zero is None:
            # Skip elements without a page
            continue
        page_num = int(page_zero) + 1
        page = pages.setdefault(page_num, _new_page(page_num))

        path = elem.get("Path", "")
        etype = _infer_type_from_path(path)
        text = (elem.get("Text") or "").strip()

        if etype == "h1" and text:
            _flush_current_list()
            if not page["h1"] or page["h1"][-1] != text:
                page["h1"].append(text)
            # Preserve block order
            page["blocks"].append({"type": "h1", "text": text, "page_number": page_num})
        elif etype == "h2" and text:
            _flush_current_list()
            if not page["h2"] or page["h2"][-1] != text:
                page["h2"].append(text)
            page["blocks"].append({"type": "h2", "text": text, "page_number": page_num})
        elif etype == "h3" and text:
            _flush_current_list()
            if not page["h3"] or page["h3"][-1] != text:
                page["h3"].append(text)
            page["blocks"].append({"type": "h3", "text": text, "page_number": page_num})
        elif etype == "p" and text:
            _flush_current_list()
            page["p"].append(text)
            page["blocks"].append({"type": "p", "text": text, "page_number": page_num})
        elif etype == "table":
            _flush_current_list()
            table_text = _serialize_table(elem)
            if table_text:
                page["table"].append(table_text)
                # Treat table as an atomic block in sequence
                page["blocks"].append({"type": "table", "text": table_text, "page_number": page_num})
        elif etype == "list":
            # Prefer nested extraction if available
            items = _collect_list_items(elem)
            if items:
                # Flush any ongoing flat LI aggregation from another context
                _flush_current_list()
                page["list"].append(items)
                # Nested list becomes a block
                page["blocks"].append({"type": "list", "text": "\n".join(items), "page_number": page_num})
            else:
                # Flat LI/ LIST without nested structure: aggregate consecutively
                item_text = text
                if item_text:
                    if current_list_page is None or current_list_page != page_num:
                        _flush_current_list()
                        current_list_page = page_num
                    current_list.append(item_text)
        else:
            # Other/unknown types: ignore for Phase-1 normalization
            _flush_current_list()

    # Flush any remaining aggregated list
    _flush_current_list()

    # Build final pages array ordered by page number
    ordered_pages = [pages[k] for k in sorted(pages.keys())]

    normalized: Dict[str, Any] = {
        "version": schema_version,
        "document_id": document_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "pages": ordered_pages,
    }
    return normalized


def _new_page(page_number: int) -> Dict[str, Any]:
    return {
        "page_number": page_number,
        "h1": [],
        "h2": [],
        "h3": [],
        "p": [],
        "list": [],
        "table": [],
        # Ordered blocks preserve the original element sequence per page (optional)
        "blocks": [],
    }


def save_normalized_result(
    normalized: Dict[str, Any],
    document_id: int,
    export_dir: Optional[str] = None
) -> Path:
    """Persist normalized JSON next to other extraction artifacts."""
    if export_dir is None:
        base_dir = Path(__file__).resolve().parents[1]  # app/
        export_path = base_dir / "extraction_analysis"
    else:
        export_path = Path(export_dir)
    export_path.mkdir(exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = export_path / f"doc_{document_id}_{timestamp}_normalized.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    return filename
