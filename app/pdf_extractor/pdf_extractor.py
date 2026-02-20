"""
Production-ready PDF extraction module for RAG ingestion.
Uses PyMuPDF for layout-aware parsing and PaddleOCR for image text extraction.
"""

import os
import io
import re
from typing import Any

os.environ.setdefault(
    "HUB_DATASET_ENDPOINT", "https://modelscope.cn/api/v1/datasets"
)

import fitz
import numpy as np
from PIL import Image, ImageEnhance
from paddleocr import PaddleOCR

_ocr_engine: PaddleOCR | None = None

SHORT_CHUNK_THRESHOLD = 60
MERGE_DISTANCE_PX = 35
TABLE_MIN_LINES = 2
TABLE_COLUMN_TOLERANCE = 15


def _get_ocr() -> PaddleOCR:
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = PaddleOCR(use_textline_orientation=True)
    return _ocr_engine


def _extract_text_from_spans(block: dict) -> str:
    parts: list[str] = []
    for line in block.get("lines", []):
        line_text: list[str] = []
        for span in line.get("spans", []):
            t = span.get("text", "").strip()
            if t:
                line_text.append(t)
        if line_text:
            parts.append(" ".join(line_text))
    return "\n".join(parts) if parts else ""


def _get_block_font_info(block: dict) -> tuple[float, bool]:
    sizes: list[float] = []
    bold = False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            s = span.get("size", 0)
            if s:
                sizes.append(s)
            flags = span.get("flags", 0)
            if flags & 2**4:
                bold = True
    avg_size = sum(sizes) / len(sizes) if sizes else 0.0
    return avg_size, bold


def _get_block_bbox(block: dict) -> tuple[float, float, float, float]:
    return block.get("bbox", (0, 0, 0, 0))


def _classify_headings(
    font_sizes: set[float], size: float, bold: bool
) -> tuple[str | None, str | None, str | None]:
    if not font_sizes or size <= 0:
        return None, None, None
    sorted_sizes = sorted(font_sizes, reverse=True)
    if len(sorted_sizes) >= 3:
        h1_thresh = (sorted_sizes[0] + sorted_sizes[1]) / 2
        h2_thresh = (sorted_sizes[1] + sorted_sizes[2]) / 2
        if size >= h1_thresh:
            return "h1", None, None
        if size >= h2_thresh:
            return None, "h2", None
        if bold and size >= sorted_sizes[-1] * 1.1:
            return None, None, "h3"
    elif len(sorted_sizes) == 2:
        mid = (sorted_sizes[0] + sorted_sizes[1]) / 2
        if size >= mid:
            return "h1", None, None
        if bold:
            return None, "h2", None
    elif len(sorted_sizes) == 1 and size >= sorted_sizes[0] * 0.99:
        return "h1", None, None
    return None, None, None


def _has_table_structure(block: dict) -> bool:
    lines = block.get("lines", [])
    if not lines:
        return False
    text = _extract_text_from_spans(block)
    header_patterns = [
        r"\b(s\.?no\.?|serial|#|no\.)\b",
        r"\b(task|activity|description|item)\b",
    ]
    pattern_match = any(
        re.search(p, text, re.IGNORECASE | re.MULTILINE) for p in header_patterns
    )
    col_anchors: list[list[float]] = []
    for line in lines:
        x_positions: list[float] = []
        for span in line.get("spans", []):
            bbox = span.get("bbox", (0, 0, 0, 0))
            x_positions.append(bbox[0])
        if x_positions:
            col_anchors.append(sorted(set(round(x / 5) * 5 for x in x_positions)))
    col_count = len(col_anchors[0]) if col_anchors else 0
    has_multiple_cols = col_count >= 2
    if pattern_match and (
        len(lines) >= TABLE_MIN_LINES or (has_multiple_cols and len(lines) >= 1)
    ):
        return True
    if "\t" in text and len(lines) >= 2 and has_multiple_cols:
        return True
    if not pattern_match and "\t" not in text and len(col_anchors) >= 2:
        first_cols = set(col_anchors[0])
        aligned_count = sum(
            1
            for cols in col_anchors[1:]
            if len(cols) >= 2
            and all(
                any(abs(c - fc) < TABLE_COLUMN_TOLERANCE for fc in first_cols)
                for c in cols[:3]
            )
        )
        if aligned_count >= len(col_anchors) - 1 and len(first_cols) >= 2:
            return True
    return False


def _match_image_xref(page: fitz.Page, block_bbox: tuple) -> int | None:
    img_list = page.get_images()
    if not img_list:
        return None
    block_rect = fitz.Rect(block_bbox)
    for item in img_list:
        xref = item[0]
        rects = page.get_image_rects(xref)
        for r in rects:
            if r.intersects(block_rect) or (
                abs(r.x0 - block_rect.x0) < 5 and abs(r.y0 - block_rect.y0) < 5
            ):
                return xref
    return None


def _preprocess_for_ocr(img: Image.Image) -> np.ndarray:
    img = img.convert("RGB")
    w, h = img.size
    if w < 1000:
        scale = 1000 / w
        new_w = 1000
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    elif w > 4000:
        scale = 3000 / w
        new_w = 3000
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.2)
    return np.array(img)


def _run_ocr(img_np: np.ndarray) -> str:
    def _parse_paddle_result(result) -> list[str]:
        lines: list[str] = []
        if not result:
            return lines
        items = result[0] if isinstance(result, list) else result
        if isinstance(items, list):
            for line in items:
                if isinstance(line, (list, tuple)) and len(line) >= 2:
                    txt = line[1][0] if isinstance(line[1], (list, tuple)) else str(line[1])
                    t = str(txt).strip()
                    if t:
                        lines.append(t)
                elif isinstance(line, dict) and "rec_text" in line:
                    t = str(line["rec_text"]).strip()
                    if t:
                        lines.append(t)
        return lines

    try:
        ocr = _get_ocr()
        result = ocr.ocr(img_np, cls=True)
        lines = _parse_paddle_result(result)
        if lines:
            text = "\n".join(lines)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = re.sub(r"[ \t]+", " ", text)
            return text.strip()
    except Exception:
        pass
    try:
        import pytesseract
        tesseract_paths = [
            os.environ.get("TESSERACT_CMD"),
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
        ]
        for p in tesseract_paths:
            if p and os.path.isfile(p):
                pytesseract.pytesseract.tesseract_cmd = p
                break
        img = Image.fromarray(img_np)
        text = pytesseract.image_to_string(img)
        if text and text.strip():
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = re.sub(r"[ \t]+", " ", text)
            return text.strip()
    except Exception:
        pass
    return ""


def _ocr_image(doc: fitz.Document, xref: int) -> str:
    try:
        base = doc.extract_image(xref)
        img_bytes = base["image"]
        img = Image.open(io.BytesIO(img_bytes))
        img_np = _preprocess_for_ocr(img)
        return _run_ocr(img_np)
    except Exception:
        return ""


def _ocr_page_as_image(page: fitz.Page, dpi: int = 200) -> str:
    try:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        img_np = _preprocess_for_ocr(img)
        return _run_ocr(img_np)
    except Exception:
        return ""


def _normalize_for_dedup(text: str) -> str:
    return " ".join(text.lower().split())


def extract_pdf(file_path: str) -> list[dict[str, Any]]:
    doc = fitz.open(file_path)
    chunks: list[dict[str, Any]] = []
    chunk_index = 0
    seen_texts: set[str] = set()

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])

        current_h1: str | None = None
        current_h2: str | None = None
        current_h3: str | None = None

        text_blocks = [b for b in blocks if b.get("type") == 0]
        image_blocks = [b for b in blocks if b.get("type") == 1]
        has_meaningful_text = any(
            _extract_text_from_spans(b).strip() for b in text_blocks
        )

        if not has_meaningful_text and (len(image_blocks) >= 1 or not blocks):
            full_page_text = _ocr_page_as_image(page)
            if not full_page_text and image_blocks:
                parts: list[str] = []
                for img_block in image_blocks:
                    xref = _match_image_xref(page, img_block.get("bbox", (0, 0, 0, 0)))
                    if xref is not None:
                        t = _ocr_image(doc, xref)
                        if t:
                            parts.append(t)
                full_page_text = "\n\n".join(parts) if parts else ""
            if full_page_text:
                norm = _normalize_for_dedup(full_page_text)
                if norm and norm not in seen_texts:
                    seen_texts.add(norm)
                    chunks.append({
                        "chunk_index": chunk_index,
                        "page_number": page_num + 1,
                        "text": full_page_text,
                        "heading_level_1": None,
                        "heading_level_2": None,
                        "heading_level_3": None,
                        "section": None,
                        "is_table": False,
                    })
                    chunk_index += 1
            continue

        font_sizes: set[float] = set()
        for block in text_blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    s = span.get("size", 0)
                    if s:
                        font_sizes.add(round(s, 1))

        text_buffer: list[tuple[str, float, float, dict]] = []
        last_y1 = -9999.0

        def _flush_text_buffer(
            h1: str | None,
            h2: str | None,
            h3: str | None,
            is_tbl: bool,
        ) -> None:
            nonlocal chunk_index, seen_texts
            if not text_buffer:
                return
            merged = " ".join(t[0] for t in text_buffer).strip()
            if not merged:
                text_buffer.clear()
                return
            norm = _normalize_for_dedup(merged)
            if norm in seen_texts:
                text_buffer.clear()
                return
            seen_texts.add(norm)
            chunks.append({
                "chunk_index": chunk_index,
                "page_number": page_num + 1,
                "text": merged,
                "heading_level_1": h1,
                "heading_level_2": h2,
                "heading_level_3": h3,
                "section": None,
                "is_table": is_tbl,
            })
            chunk_index += 1
            text_buffer.clear()

        block_iter = iter(blocks)
        for block in block_iter:
            block_type = block.get("type", 0)
            bbox = _get_block_bbox(block)

            if block_type == 0:
                text = _extract_text_from_spans(block)
                if not text:
                    continue
                size, bold = _get_block_font_info(block)
                h1_tag, h2_tag, h3_tag = _classify_headings(
                    font_sizes, size, bold
                )
                if h1_tag:
                    current_h1 = text
                    current_h2 = None
                    current_h3 = None
                elif h2_tag:
                    current_h2 = text
                    current_h3 = None
                elif h3_tag:
                    current_h3 = text

                is_table = _has_table_structure(block)
                y0, y1 = bbox[1], bbox[3]
                gap = y0 - last_y1 if last_y1 > -9999 else 0
                last_y1 = y1

                should_merge = (
                    len(text) < SHORT_CHUNK_THRESHOLD
                    and text_buffer
                    and gap < MERGE_DISTANCE_PX
                    and not is_table
                )
                if should_merge:
                    text_buffer.append((text, y0, y1, block))
                    last_y1 = y1
                else:
                    _flush_text_buffer(
                        current_h1, current_h2, current_h3, False
                    )
                    if len(text) < SHORT_CHUNK_THRESHOLD and not is_table:
                        text_buffer.append((text, y0, y1, block))
                        last_y1 = y1
                    else:
                        norm = _normalize_for_dedup(text)
                        if norm not in seen_texts:
                            seen_texts.add(norm)
                            chunks.append({
                                "chunk_index": chunk_index,
                                "page_number": page_num + 1,
                                "text": text,
                                "heading_level_1": current_h1,
                                "heading_level_2": current_h2,
                                "heading_level_3": current_h3,
                                "section": None,
                                "is_table": is_table,
                            })
                            chunk_index += 1
                        last_y1 = y1

            elif block_type == 1:
                _flush_text_buffer(
                    current_h1,
                    current_h2,
                    current_h3,
                    False,
                )
                xref = _match_image_xref(page, bbox)
                if xref is not None:
                    ocr_text = _ocr_image(doc, xref)
                    if ocr_text:
                        norm = _normalize_for_dedup(ocr_text)
                        if norm not in seen_texts:
                            seen_texts.add(norm)
                            chunks.append({
                                "chunk_index": chunk_index,
                                "page_number": page_num + 1,
                                "text": ocr_text,
                                "heading_level_1": current_h1,
                                "heading_level_2": current_h2,
                                "heading_level_3": current_h3,
                                "section": None,
                                "is_table": False,
                            })
                            chunk_index += 1

        _flush_text_buffer(current_h1, current_h2, current_h3, False)

    doc.close()
    return chunks


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pdf_extractor.py <path_to_pdf>")
        sys.exit(1)
    path = sys.argv[1]
    result = extract_pdf(path)
    for c in result:
        txt = c["text"]
        preview = (txt[:80] + "...") if len(txt) > 80 else txt
        print(f"[{c['chunk_index']}] p{c['page_number']}: {preview}")
    print(f"\nTotal chunks: {len(result)}")
