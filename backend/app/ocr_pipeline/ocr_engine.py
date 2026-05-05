"""
PaddleOCR engine wrapper.

Provides lazy-initialized OCR singleton and page-level processing
with automatic retry for low-confidence results.
"""

import gc
import logging
import warnings

import numpy as np
from paddleocr import PaddleOCR

from app.ocr_pipeline.config import CONF_THRESHOLD, MAX_SIDE_LIMIT
from app.ocr_pipeline.preprocessing import (
    classify_background,
    preprocess_dark,
    preprocess_dark_retry,
    preprocess_light,
    resize_if_needed,
)
from app.ocr_pipeline.text_utils import post_process

logger = logging.getLogger(__name__)

# ── Lazy OCR singleton ───────────────────────────────────────────────────────
_ocr_engine = None


def get_ocr():
    """Return a lazily-initialized PaddleOCR instance."""
    global _ocr_engine
    if _ocr_engine is None:
        logger.info("🚀 Initializing PaddleOCR...")
        _ocr_engine = PaddleOCR(
            lang="en",
            use_angle_cls=False,
            det_limit_side_len=MAX_SIDE_LIMIT,
            det_limit_type="max",
            use_gpu=False,
            enable_mkldnn=False,
            show_log=False,
        )
        logger.info("✅ OCR Model Loaded")
    return _ocr_engine


# ── Result parsing ───────────────────────────────────────────────────────────

def _parse_ocr_result(result, page_num: int):
    """Parse PaddleOCR output (handles both dict and list formats)."""
    texts, scores, boxes = [], [], []
    if not result or len(result) == 0:
        return texts, scores, boxes

    page_result = result[0]

    if isinstance(page_result, dict):
        raw_texts = page_result.get("rec_texts") or page_result.get("texts") or []
        raw_scores = page_result.get("rec_scores") or page_result.get("scores") or []
        boxes = list(page_result.get("rec_boxes") or [])
        for t, s in zip(raw_texts, raw_scores):
            texts.append(str(t).strip())
            scores.append(float(s))

    elif isinstance(page_result, list):
        for entry in page_result:
            try:
                if isinstance(entry, (list, tuple)) and len(entry) > 1:
                    content = entry[1]
                    if isinstance(content, (list, tuple)) and len(content) > 1:
                        texts.append(str(content[0]).strip())
                        scores.append(float(content[1]))
                        if len(entry[0]) >= 4:
                            boxes.append(entry[0])
            except Exception:
                continue

    return texts, scores, boxes


def _spatial_sort(texts, scores, boxes, img_w: int):
    """Sort OCR results by column then vertical position for reading order."""
    if not boxes or len(boxes) != len(texts):
        return texts, scores

    col_split = img_w * 0.48
    items = []
    for t, s, box in zip(texts, scores, boxes):
        try:
            box_arr = np.array(box)
            x1 = float(np.min(box_arr[:, 0]) if box_arr.ndim == 2 else box_arr[0])
            y1 = float(np.min(box_arr[:, 1]) if box_arr.ndim == 2 else box_arr[1])
        except Exception:
            x1, y1 = 0, 0
        items.append({"text": t, "score": s, "col": 0 if x1 < col_split else 1, "y": y1})

    items.sort(key=lambda x: (x["col"], x["y"]))
    return [i["text"] for i in items], [i["score"] for i in items]


# ── Page-level OCR ───────────────────────────────────────────────────────────

def process_page(i: int, page) -> dict:
    """
    Run OCR on a single page image.

    Args:
        i: Zero-based page index.
        page: PIL Image of the page.

    Returns:
        Dict with 'page_number' (1-based) and 'raw_text'.
    """
    img_rgb = np.asarray(page)
    img_rgb = resize_if_needed(img_rgb)
    orig_h, orig_w = img_rgb.shape[:2]
    bg_type = classify_background(img_rgb)
    logger.info(f"  Page {i + 1}: bg={bg_type} ({orig_w}x{orig_h})")
    processed = preprocess_dark(img_rgb) if bg_type == "dark" else preprocess_light(img_rgb)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = get_ocr().ocr(processed)
        texts, scores, boxes = _parse_ocr_result(result, i + 1)
    except Exception:
        texts, scores, boxes = [], [], []

    word_count = sum(len(t.split()) for t in texts)
    if word_count < 3:
        logger.info(f"  Page {i + 1}: low word count ({word_count}), retrying...")
        # Free previous processed image before retrying to reduce peak memory
        try:
            del processed
        except Exception:
            pass
        retry_img = preprocess_dark_retry(img_rgb) if bg_type == "dark" else preprocess_dark(img_rgb)
        retry_img = resize_if_needed(retry_img)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                r_result = get_ocr().ocr(retry_img)
            r_texts, r_scores, r_boxes = _parse_ocr_result(r_result, i + 1)
            if sum(len(t.split()) for t in r_texts) > word_count:
                texts, scores, boxes = r_texts, r_scores, r_boxes
        except Exception:
            pass
        try:
            del retry_img
        except Exception:
            pass

    # Free heavy numpy arrays before returning — caller (batch loop) will gc.collect()
    del img_rgb, processed
    gc.collect()
    texts, scores = _spatial_sort(texts, scores, boxes, orig_w)
    # Debug: Show extraction stats
    raw_extracted = "\n".join(texts) if texts else ""
    logger.info(f"  Page {i + 1}: Extracted {len(texts)} text blocks (confidence range: {min(scores) if scores else 'N/A'}~{max(scores) if scores else 'N/A'})")
    lines = [t for t, s in zip(texts, scores) if s > CONF_THRESHOLD]
    logger.info(f"  Page {i + 1}: After CONF_THRESHOLD ({CONF_THRESHOLD}): {len(lines)} text blocks remain")
    
    raw_text = post_process("\n".join(lines))
    logger.info(f"  Page {i + 1}: After post_process: {len(raw_text.split())} words in {len(raw_text.splitlines())} lines")
    
    final_words = len(raw_text.split())

    if final_words < 2:
        logger.warning(f"  Page {i + 1}: ⚠️ empty after processing (extracted: {len(texts)} blocks, filtered: {len(lines)}, final: {final_words} words)")
    else:
        logger.info(f"  Page {i + 1}: ✅ {len(raw_text.splitlines())} lines / ~{final_words} words")

    try:
        del processed
    except Exception:
        pass
    try:
        del img_rgb
    except Exception:
        pass
    gc.collect()

    return {"page_number": i + 1, "raw_text": raw_text}
