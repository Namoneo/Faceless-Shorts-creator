"""Shared helpers for the offline foundation tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "productions" / "fixtures" / "foundation"
HOOK = "Qani, tanla! O\u02bbzbekcha g\u02bbalaba."
HOLD = "o\u2018g\u2018 varianti: g\u2018alaba."


def load_fixture() -> dict[str, Any]:
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))


def copy_fixture(dest: Path) -> Path:
    target = dest / "foundation"
    shutil.copytree(FIXTURE, target, ignore=shutil.ignore_patterns("out"))
    return target / "manifest.json"


def write_color(job: dict[str, Any]) -> None:
    output = Path(job["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x1e2a3a:s={job['width']}x{job['height']}:r={job['fps']}",
            "-frames:v",
            str(job["frameCount"]),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ],
        check=True,
    )
