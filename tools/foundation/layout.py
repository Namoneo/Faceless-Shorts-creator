"""Advisory caption width estimate. The pass/fail check is Chrome geometry in qa.py."""

from __future__ import annotations

import math
from typing import Any

# DejaVu Sans average advance is a bit under 0.6em for this Latin-plus-modifier set.
# 0.62 is intentionally conservative so a borderline string fails rather than clips.
CHAR_WIDTH_EM = 0.62


def caption_overflow(text: str, width: int, height: int, safe: dict[str, Any], caption: dict[str, Any]) -> dict[str, Any]:
    font_px = height * float(caption["fontScale"])
    max_width = width * (1 - float(safe["left"]) - float(safe["right"]))
    char_px = font_px * CHAR_WIDTH_EM
    estimated_width = len(text) * char_px
    lines = math.ceil(estimated_width / max_width) if max_width > 0 else 99
    max_lines = int(caption["maxLines"])
    return {
        "text": text,
        "fontPx": round(font_px, 2),
        "maxWidthPx": round(max_width, 2),
        "estimatedLines": lines,
        "maxLines": max_lines,
        "overflow": lines > max_lines,
        "estimator": "dejavu-average-0.62em",
    }
