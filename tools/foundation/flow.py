"""Manual Flow packets. The runner never opens Flow, fal, or a network client."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from tools.foundation.ffmpeg import MediaError, duration_seconds, probe, run, stream_of
from tools.foundation.hashutil import sha256_file
from tools.foundation.manifest import REQUIRED_FPS
from tools.foundation.pictures import DESIGNED_LAYOUT, STAND_IN_LABEL, portrait_layout, stand_in_filter

STATUSES = (
    "planned",
    "awaiting_manual_generation",
    "awaiting_import",
    "validated",
    "approved",
    "rejected",
)
NEXT_STATUS = {
    "planned": "awaiting_manual_generation",
    "awaiting_manual_generation": "awaiting_import",
}
ESTIMATE_LABEL = "Estimate only. Verify the current price in Flow before approval."
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_TABLE = ROOT / "productions" / "flow-price-table.json"


class FlowError(ValueError):
    pass


def load_price_table(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_PRICE_TABLE
    doc = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("models"), dict):
        raise FlowError(f"{target} is not a Flow price table")
    if ESTIMATE_LABEL not in str(doc.get("label") or ""):
        raise FlowError("price table label must say the figure is an estimate to verify in Flow")
    return doc


def estimate_credits(model: str, duration_sec: float, table: dict[str, Any] | None = None) -> dict[str, Any]:
    prices = table or load_price_table()
    row = prices["models"].get(model)
    if not isinstance(row, dict) or not isinstance(row.get("creditsPerSecond"), (int, float)) or isinstance(row.get("creditsPerSecond"), bool):
        return {
            "known": False,
            "amount": None,
            "currency": prices.get("currency", "credits"),
            "model": model,
            "tableId": prices.get("id"),
            "label": ESTIMATE_LABEL,
            "note": "No row in the local price table.",
        }
    amount = float(row["creditsPerSecond"]) * float(duration_sec)
    return {
        "known": True,
        "amount": amount,
        "currency": prices.get("currency", "credits"),
        "model": model,
        "tableId": prices.get("id"),
        "label": ESTIMATE_LABEL,
        "note": row.get("note") or "",
    }


def plan_packet(
    packet_id: str,
    scene_id: str,
    prompt: str,
    duration_sec: float,
    model: str,
    references: list[str] | None = None,
    reference_files: list[str] | None = None,
    ratio: str = "16:9",
    focal: dict[str, float] | None = None,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    width, height = _ratio_size(ratio)
    return {
        "schemaVersion": 1,
        "id": packet_id,
        "sceneId": scene_id,
        "status": "planned",
        "prompt": prompt,
        "referenceAssetIds": list(references or []),
        "referenceFiles": list(reference_files or []),
        "requestedModel": model,
        "ratio": ratio,
        "width": width,
        "height": height,
        "durationSec": float(duration_sec),
        "fps": REQUIRED_FPS,
        "audio": "required",
        "focal": focal or {"x": 0.5, "y": 0.5},
        "safe": {"top": 0.08, "bottom": 0.18, "left": 0.06, "right": 0.06},
        "dualFormat": {"reuseMasterFor": ["portrait"], "separateVariantReason": None},
        "creditEstimate": estimate_credits(model, duration_sec, table),
        "provenance": None,
        "sourceHash": None,
        "cachedPath": None,
        "cacheAction": None,
        "variantOf": None,
        "history": ["planned"],
    }


def release_for_manual(packet: dict[str, Any]) -> dict[str, Any]:
    return _advance(packet, "awaiting_manual_generation")


def mark_ready_to_import(packet: dict[str, Any]) -> dict[str, Any]:
    return _advance(packet, "awaiting_import")


def import_export(
    packet: dict[str, Any],
    file_path: Path,
    provenance: dict[str, Any] | None,
    cache_dir: Path,
) -> dict[str, Any]:
    if packet.get("status") != "awaiting_import":
        raise FlowError(f"packet {packet.get('id')} cannot be imported from status {packet.get('status')}")
    _require_provenance(provenance)
    if packet.get("mediaKind") == "still":
        return _import_still(packet, file_path, provenance, cache_dir)
    info = _decoded(file_path)
    video = stream_of(info, "video")
    audio = stream_of(info, "audio")
    if video is None:
        raise FlowError(f"{file_path} has no video stream")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width != packet["width"] or height != packet["height"]:
        raise FlowError(f"wrong ratio or dimensions: got {width}x{height}, packet wants {packet['width']}x{packet['height']}")
    fps = _fps(video.get("avg_frame_rate"))
    if abs(fps - REQUIRED_FPS) > 0.01:
        raise FlowError(f"wrong fps: got {fps}, packet wants {REQUIRED_FPS}")
    duration = duration_seconds(info)
    if duration is None or abs(duration - float(packet["durationSec"])) > (2 / REQUIRED_FPS):
        raise FlowError(f"duration {duration} is outside the packet duration {packet['durationSec']}")
    if packet.get("audio") == "required" and audio is None:
        raise FlowError(f"{file_path} has no audio stream")
    digest = sha256_file(file_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / "sources" / f"{digest}.mp4"
    if cached.is_file() and sha256_file(cached) == digest:
        action = "skipped"
    else:
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, cached)
        action = "stored"
    updated = dict(packet)
    updated["status"] = "validated"
    updated["provenance"] = provenance
    updated["sourceHash"] = digest
    updated["cachedPath"] = str(cached)
    updated["cacheAction"] = action
    updated["history"] = [*packet.get("history", []), "validated"]
    return updated


def approve(packet: dict[str, Any]) -> dict[str, Any]:
    if packet.get("status") != "validated":
        raise FlowError(f"packet {packet.get('id')} cannot be approved from status {packet.get('status')}")
    updated = dict(packet)
    updated["status"] = "approved"
    updated["history"] = [*packet.get("history", []), "approved"]
    return updated


def reject(packet: dict[str, Any], reason: str) -> dict[str, Any]:
    if packet.get("status") != "validated":
        raise FlowError(f"packet {packet.get('id')} cannot be rejected from status {packet.get('status')}")
    if not isinstance(reason, str) or not reason.strip():
        raise FlowError("rejection needs a reason")
    updated = dict(packet)
    updated["status"] = "rejected"
    updated["rejectionReason"] = reason.strip()
    updated["history"] = [*packet.get("history", []), "rejected"]
    return updated


def plan_separate_variant(packet: dict[str, Any], reason: str, ratio: str = "9:16") -> dict[str, Any]:
    if packet.get("status") != "approved":
        raise FlowError("a separate variant can only be planned from an approved master")
    if not isinstance(reason, str) or not reason.strip():
        raise FlowError("a separate portrait or landscape variant needs a documented reason")
    variant = plan_packet(
        f"{packet['id']}-{ratio.replace(':', 'x')}",
        packet["sceneId"],
        packet["prompt"],
        float(packet["durationSec"]),
        packet["requestedModel"],
        packet.get("referenceAssetIds"),
        packet.get("referenceFiles"),
        ratio,
        packet.get("focal"),
    )
    variant["variantOf"] = packet["id"]
    variant["dualFormat"] = {"reuseMasterFor": [], "separateVariantReason": reason.strip()}
    variant["sourceHash"] = None
    return variant


def derive_reframe(
    packet: dict[str, Any],
    out_path: Path,
    width: int,
    height: int,
    focal: dict[str, Any] | None = None,
    caption: str = "",
) -> dict[str, Any]:
    if packet.get("status") != "approved":
        raise FlowError("a derived reframe requires an approved master and does not generate a new clip")
    source = Path(packet["cachedPath"])
    if sha256_file(source) != packet["sourceHash"]:
        raise FlowError("cached Flow master does not match the approved hash")
    used_focal = focal or packet.get("focal") or {"x": 0.5, "y": 0.5}
    sidecar = out_path.with_suffix(".reframe.json")
    if out_path.is_file() and sidecar.is_file():
        previous = json.loads(sidecar.read_text(encoding="utf-8"))
        if (
            previous.get("sourceHash") == packet["sourceHash"]
            and previous.get("width") == width
            and previous.get("height") == height
            and previous.get("layout") == DESIGNED_LAYOUT
            and previous.get("caption") == caption
            and previous.get("focal") == used_focal
            and previous.get("derivativeHash") == sha256_file(out_path)
        ):
            return {**previous, "action": "skipped", "packetStatus": packet["status"], "regenerated": False}
    portrait_layout(source, out_path, width, height, float(packet["durationSec"]), int(packet["fps"]), used_focal, caption)
    record = {
        "action": "derived",
        "layout": DESIGNED_LAYOUT,
        "sourceHash": packet["sourceHash"],
        "derivativeHash": sha256_file(out_path),
        "width": width,
        "height": height,
        "focal": used_focal,
        "caption": caption,
        "packetStatus": packet["status"],
        "regenerated": False,
    }
    sidecar.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def write_synthetic_stand_in(packet: dict[str, Any], dest: Path) -> Path:
    if packet.get("requestedModel") != "synthetic-stand-in":
        raise FlowError("refusing to invent a Flow export; only the labelled synthetic stand-in can be built locally")
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = float(packet["durationSec"])
    label_file = dest.with_suffix(".label.txt")
    label_file.write_text(STAND_IN_LABEL, encoding="utf-8")
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={packet['width']}x{packet['height']}:rate={packet['fps']}:duration={duration:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration:.3f}",
            "-filter_complex",
            f"[0:v]{stand_in_filter(label_file, int(packet['width']), int(packet['height']))}[v]",
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(packet["fps"]),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(dest),
        ]
    )
    dest.with_suffix(".txt").write_text(
        STAND_IN_LABEL + "\nSynthetic ffmpeg test pattern. Not a Flow render. Not fal. Not TTS.\n",
        encoding="utf-8",
    )
    return dest


def write_packet(path: Path, packet: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_packet(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("status") not in STATUSES:
        raise FlowError(f"{path} is not a Flow packet")
    return doc


def _advance(packet: dict[str, Any], target: str) -> dict[str, Any]:
    current = packet.get("status")
    if NEXT_STATUS.get(str(current)) != target:
        raise FlowError(f"packet {packet.get('id')} cannot move from {current} to {target}")
    updated = dict(packet)
    updated["status"] = target
    updated["history"] = [*packet.get("history", []), target]
    return updated


def _import_still(
    packet: dict[str, Any],
    file_path: Path,
    provenance: dict[str, Any],
    cache_dir: Path,
) -> dict[str, Any]:
    """Validate a manual Flow still. Ratio is checked. Pixel size may be above 1920x1080."""
    info = _decoded(file_path)
    video = stream_of(info, "video")
    if video is None:
        raise FlowError(f"{file_path} has no picture stream")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width < 16 or height < 16:
        raise FlowError(f"{file_path} is too small: {width}x{height}")
    if not _ratio_close(width, height, str(packet.get("ratio") or "16:9")):
        raise FlowError(f"wrong ratio: got {width}x{height}, packet wants {packet.get('ratio')}")
    digest = sha256_file(file_path)
    suffix = file_path.suffix.lower() if file_path.suffix else ".jpg"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / "sources" / f"{digest}{suffix}"
    if cached.is_file() and sha256_file(cached) == digest:
        action = "skipped"
    else:
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file_path, cached)
        action = "stored"
    updated = dict(packet)
    updated["status"] = "validated"
    updated["provenance"] = provenance
    updated["sourceHash"] = digest
    updated["sourceFile"] = file_path.name
    updated["deliveredWidth"] = width
    updated["deliveredHeight"] = height
    updated["cachedPath"] = str(cached)
    updated["cacheAction"] = action
    updated["history"] = [*packet.get("history", []), "validated"]
    return updated


def _ratio_close(width: int, height: int, ratio: str) -> bool:
    parts = ratio.split(":")
    if len(parts) != 2:
        return False
    try:
        wide = int(parts[0])
        tall = int(parts[1])
    except ValueError:
        return False
    if wide <= 0 or tall <= 0 or height <= 0:
        return False
    return abs((width / height) - (wide / tall)) / (wide / tall) <= 0.02


def _require_provenance(provenance: dict[str, Any] | None) -> None:
    if not isinstance(provenance, dict):
        raise FlowError("missing provenance")
    for key in ("tool", "origin", "note"):
        if not isinstance(provenance.get(key), str) or not provenance[key].strip():
            raise FlowError(f"missing provenance.{key}")


def _decoded(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FlowError(f"missing export file {path}")
    try:
        run(["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path), "-f", "null", "-"])
        return probe(path)
    except (MediaError, OSError) as exc:
        raise FlowError(f"cannot decode {path}") from exc


def _ratio_size(ratio: str) -> tuple[int, int]:
    sizes = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
    if ratio not in sizes:
        raise FlowError(f"unsupported ratio {ratio}")
    return sizes[ratio]


def _fps(value: Any) -> float:
    if not isinstance(value, str) or "/" not in value:
        return 0.0
    num, den = value.split("/", 1)
    try:
        denominator = float(den)
        if denominator == 0:
            return 0.0
        return float(num) / denominator
    except ValueError:
        return 0.0
