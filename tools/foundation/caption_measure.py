"""Caption geometry from headless Chrome using DejaVu Sans.

The 0.62em estimate stays advisory. A caption check does not pass on that estimate.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class CaptionMeasureError(RuntimeError):
    pass


CHROME_TIMEOUT_SEC = 8


def measure_captions(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chrome = shutil.which("google-chrome") or shutil.which("chrome")
    if chrome is None:
        raise CaptionMeasureError("google-chrome is not on PATH; caption geometry is unverified")
    if not cases:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "captions.html"
        html.write_text(_page(cases), encoding="utf-8")
        result = subprocess.run(
            [
                "timeout",
                str(CHROME_TIMEOUT_SEC),
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--virtual-time-budget=2000",
                "--dump-dom",
                str(html),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    # Chrome often prints the DOM and then does not exit. timeout's 124 is expected.
    match = re.search(r'id="out">(.*?)</div>', result.stdout, re.DOTALL)
    if match is None:
        raise CaptionMeasureError(f"caption geometry produced no measurement\n{result.stderr[-800:]}")
    try:
        measured = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise CaptionMeasureError("caption geometry was not JSON") from exc
    if not isinstance(measured, list) or len(measured) != len(cases):
        raise CaptionMeasureError("caption geometry returned the wrong number of boxes")
    return measured


def _page(cases: list[dict[str, Any]]) -> str:
    payload = json.dumps(cases, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!doctype html>
<meta charset="utf-8">
<div id="out">pending</div>
<script type="application/json" id="cases">{payload}</script>
<script>
const cases = JSON.parse(document.getElementById("cases").textContent);
const fontOk = document.fonts.check('48px "DejaVu Sans"');
const results = cases.map((item) => {{
  const el = document.createElement("div");
  el.style.fontFamily = '"DejaVu Sans", sans-serif';
  el.style.display = "block";
  el.style.width = item.widthPx + "px";
  el.style.fontSize = item.fontPx + "px";
  el.style.lineHeight = "1.25";
  el.style.whiteSpace = "normal";
  el.style.textTransform = "none";
  el.textContent = item.text;
  document.body.appendChild(el);
  const linePx = item.fontPx * 1.25;
  const lines = Math.max(1, Math.round(el.scrollHeight / linePx));
  const horizontal = el.scrollWidth > el.clientWidth + 1;
  return {{
    id: item.id,
    lines: lines,
    scrollHeight: el.scrollHeight,
    clientWidth: el.clientWidth,
    scrollWidth: el.scrollWidth,
    horizontal: horizontal,
    fontOk: fontOk,
    overflow: !fontOk || lines > item.maxLines || horizontal
  }};
}});
document.getElementById("out").textContent = JSON.stringify(results);
</script>
"""
