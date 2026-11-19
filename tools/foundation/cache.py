"""Cache decisions. A hit requires the parameter hash, dependency hashes, and output bytes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.foundation.hashutil import parameter_hash, sha256_file


def decide(job: dict[str, Any], sidecar: dict[str, Any] | None, output: Path) -> str:
    if sidecar is None or not output.is_file():
        return "render"
    if sidecar.get("parameterHash") != job["parameterHash"]:
        return "render"
    if sidecar.get("dependencies") != job["dependencies"]:
        return "render"
    if sidecar.get("outputContentHash") != sha256_file(output):
        return "render"
    return "skip"


def scene_job(
    manifest: dict[str, Any],
    scene: dict[str, Any],
    fmt: dict[str, Any],
    placed: dict[str, Any],
    line: dict[str, Any],
    narration_hash: str,
    visual_hash: str,
    out_path: Path,
) -> dict[str, Any]:
    language = manifest["language"]
    parameter = {
        "sceneId": scene["id"],
        "formatId": fmt["id"],
        "width": fmt["width"],
        "height": fmt["height"],
        "fps": manifest["fps"],
        "startSec": placed["startSec"],
        "durationSec": placed["durationSec"],
        "focal": scene["focal"],
        "safe": scene["safe"],
        "text": scene["text"][language],
        "caption": scene["caption"],
        "line": line,
        "compositionId": fmt["compositionId"],
    }
    dependencies = {
        "narration": narration_hash,
        "visual": visual_hash,
        "timing": parameter_hash(line),
    }
    fps = int(manifest["fps"])
    frame_start = int(round(float(placed["startSec"]) * fps))
    frame_count = int(round(float(placed["durationSec"]) * fps))
    return {
        "sceneId": scene["id"],
        "formatId": fmt["id"],
        "compositionId": fmt["compositionId"],
        "parameterHash": parameter_hash(parameter),
        "dependencies": dependencies,
        "output": str(out_path),
        "frameStart": frame_start,
        "frameEnd": frame_start + frame_count - 1,
        "frameCount": frame_count,
        "width": fmt["width"],
        "height": fmt["height"],
        "fps": fps,
        "inputProps": None,
        "parameter": parameter,
    }


def read_sidecar(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return None
    return loaded


def write_sidecar(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
