"""
Text post-processing utilities for OCR output.

Handles OCR fix patterns, noise removal, and text cleaning.
"""

import re

# ── Common OCR misreads ──────────────────────────────────────────────────────
_OCR_FIXES = [
    (r"\bl\.2b\b", "1.2b"),
    (r"\bICICIELombard\b", "ICICI Lombard"),
    (r"\b7EAL\b", "TEAL"),
    (r"\bNelwork\b", "Network"),
    (r"\bMymtra\b", "Myntra"),
    (r"\bBugature\b", "Bugasura"),
    (r"\bBlugatiuna\b", "Bugasura"),
    (r"\bNibhaye Vaade\b", ""),
    (r"(\w+)-\s*\n\s*(\w+)", r"\1\2"),
]

# ── Noise line patterns ──────────────────────────────────────────────────────
_NOISE_RE = [
    re.compile(r"^[O\s]{1,4}$"),
    re.compile(r"^\$\s*\$$"),
    re.compile(r"^[<>/]{1,3}$"),
    re.compile(r"^\d{1,2}$"),
    re.compile(r"^[A-Z]{1,2}$"),
    re.compile(r"^(gas|STP S|r\s+7|MO)$", re.I),
]


def post_process(text: str) -> str:
    """Apply OCR fix patterns and remove noise lines."""
    for pattern, fix in _OCR_FIXES:
        text = re.sub(pattern, fix, text)
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line or len(line) < 2:
            continue
        if any(rx.match(line) for rx in _NOISE_RE):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def clean_text(text: str) -> str:
    """Strip whitespace from each line and remove very short lines."""
    lines = text.split("\n")
    return "\n".join(l.strip() for l in lines if len(l.strip()) > 1)
