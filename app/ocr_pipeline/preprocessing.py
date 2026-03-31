"""
Image preprocessing for OCR.

Handles light/dark background detection, deskewing, denoising,
and contrast enhancement to maximize OCR accuracy.
"""

import cv2
import numpy as np

from app.ocr_pipeline.config import DARK_BG_THRESH, MAX_SIDE_LIMIT


def classify_background(img_rgb: np.ndarray) -> str:
    """Classify image background as 'dark' or 'light'."""
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    return "dark" if float(gray.mean()) < DARK_BG_THRESH else "light"


def resize_if_needed(img: np.ndarray) -> np.ndarray:
    """Downscale image if any side exceeds MAX_SIDE_LIMIT."""
    h, w = img.shape[:2]
    max_side = max(h, w)
    if max_side > MAX_SIDE_LIMIT:
        scale = MAX_SIDE_LIMIT / max_side
        img = cv2.resize(
            img,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return img


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Correct small rotation angles in scanned pages."""
    coords = np.column_stack(np.where(gray < 200))
    if len(coords) < 50:
        return gray
    rect = cv2.minAreaRect(coords.astype(np.float32))
    angle = rect[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.3 or abs(angle) > 10:
        return gray
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def preprocess_light(img_rgb: np.ndarray) -> np.ndarray:
    """Preprocess a light-background slide for OCR."""
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    gray = _deskew(gray)
    denoised = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)
    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
    return cv2.cvtColor(cv2.filter2D(denoised, -1, kernel), cv2.COLOR_GRAY2RGB)


def preprocess_dark(img_rgb: np.ndarray) -> np.ndarray:
    """Preprocess a dark-background slide for OCR (invert + CLAHE + Sauvola)."""
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    inverted = cv2.bitwise_not(gray)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(inverted)
    try:
        from skimage.filters import threshold_sauvola
        thresh_map = threshold_sauvola(enhanced, window_size=51, k=0.2)
        binary = (enhanced > thresh_map).astype(np.uint8) * 255
    except ImportError:
        _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    return cv2.cvtColor(cleaned, cv2.COLOR_GRAY2RGB)


def preprocess_dark_retry(img_rgb: np.ndarray) -> np.ndarray:
    """Alternative dark-background preprocessing for retry attempts."""
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=5.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(gray)
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((2, 2), np.uint8)
    dilated = cv2.dilate(binary, kernel, iterations=1)
    return cv2.cvtColor(dilated, cv2.COLOR_GRAY2RGB)
