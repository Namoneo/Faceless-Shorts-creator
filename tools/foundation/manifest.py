"""Episode manifest validation. The runner is offline and fails closed on generation providers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
REQUIRED_FPS = 30
REQUIRED_FORMATS = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
}
ALLOWED_PROVIDERS = frozenset({"import", "synthetic-ffmpeg", "remotion"})
FORBIDDEN_PROVIDERS = frozenset(
    {
        "elevenlabs",
        "fal",
        "fal.ai",
        "gemini",
        "google-gemini",
        "vertex",
        "voicestudio",
        "flow",
        "openai",
        "heygen",
        "krea",
    }
)
SUPPORTED_LANGUAGES = frozenset({"uz-Latn", "en", "nl"})
ID_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


class ManifestError(ValueError):
    pass


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not JSON: {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ManifestError("manifest root must be an object")
    return validate_manifest(doc, path.parent)


def validate_manifest(doc: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    if doc.get("schemaVersion") != SCHEMA_VERSION:
        raise ManifestError(f"schemaVersion must be {SCHEMA_VERSION}")
    _require_id(doc.get("id"), "episode id")
    language = doc.get("language")
    if language not in SUPPORTED_LANGUAGES:
        raise ManifestError(f"language must be one of {sorted(SUPPORTED_LANGUAGES)}")
    supported = doc.get("supportedLanguages")
    if not isinstance(supported, list) or not set(supported) >= {"uz-Latn", "en", "nl"}:
        raise ManifestError("supportedLanguages must include uz-Latn, en, and nl")
    if language not in supported:
        raise ManifestError("language must be listed in supportedLanguages")
    if doc.get("fps") != REQUIRED_FPS:
        raise ManifestError(f"fps must be {REQUIRED_FPS}")
    formats = doc.get("formats")
    if not isinstance(formats, list):
        raise ManifestError("formats must be a list")
    seen: set[str] = set()
    for fmt in formats:
        if not isinstance(fmt, dict):
            raise ManifestError("format entries must be objects")
        fid = fmt.get("id")
        if fid not in REQUIRED_FORMATS:
            raise ManifestError(f"unknown format {fid!r}; expected landscape and portrait")
        width, height = REQUIRED_FORMATS[fid]
        if fmt.get("width") != width or fmt.get("height") != height:
            raise ManifestError(f"{fid} must be {width}x{height}")
        if fmt.get("fps", REQUIRED_FPS) != REQUIRED_FPS:
            raise ManifestError(f"{fid} fps must be {REQUIRED_FPS}")
        if not isinstance(fmt.get("compositionId"), str) or not fmt["compositionId"]:
            raise ManifestError(f"{fid} needs compositionId")
        seen.add(fid)
    if not seen:
        raise ManifestError("formats must include landscape, portrait, or both")
    assets = doc.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ManifestError("assets must be a non-empty list")
    asset_ids: set[str] = set()
    for asset in assets:
        _validate_asset(asset, base_dir)
        if asset["id"] in asset_ids:
            raise ManifestError(f"duplicate asset id {asset['id']}")
        asset_ids.add(asset["id"])
    scenes = doc.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ManifestError("scenes must be a non-empty list")
    scene_ids: set[str] = set()
    for scene in scenes:
        _validate_scene(scene, asset_ids, supported, language)
        if scene["id"] in scene_ids:
            raise ManifestError(f"duplicate scene id {scene['id']}")
        scene_ids.add(scene["id"])
    _validate_timeline(doc, scene_ids)
    qa = doc.get("qa")
    if not isinstance(qa, dict):
        raise ManifestError("qa intent block is required")
    for key in ("allowBlack", "allowSilence", "allowClipping", "allowCaptionOverflow"):
        if not isinstance(qa.get(key), bool):
            raise ManifestError(f"qa.{key} must be an explicit boolean")
    return doc


def _validate_asset(asset: Any, base_dir: Path | None) -> None:
    if not isinstance(asset, dict):
        raise ManifestError("asset entries must be objects")
    _require_id(asset.get("id"), "asset id")
    if asset.get("kind") not in {"narration", "visual"}:
        raise ManifestError(f"asset {asset.get('id')} kind must be narration or visual")
    source = asset.get("source")
    if not isinstance(source, dict):
        raise ManifestError(f"asset {asset['id']} needs source")
    provider = str(source.get("provider") or "").strip().lower()
    if not provider:
        raise ManifestError(f"asset {asset['id']} source.provider is required")
    if provider in FORBIDDEN_PROVIDERS or provider not in ALLOWED_PROVIDERS:
        raise ManifestError(
            f"refusing provider {provider!r} on asset {asset['id']}; this runner is offline"
        )
    if not isinstance(source.get("role"), str) or not source["role"]:
        raise ManifestError(f"asset {asset['id']} source.role is required")
    rights = asset.get("rights")
    if not isinstance(rights, dict) or not rights.get("status") or not rights.get("owner"):
        raise ManifestError(f"asset {asset['id']} needs rights.status and rights.owner")
    provenance = asset.get("provenance")
    if not isinstance(provenance, dict) or not provenance.get("tool") or not provenance.get("origin"):
        raise ManifestError(f"asset {asset['id']} needs provenance.tool and provenance.origin")
    if provider == "import":
        packet = source.get("flowPacket")
        if asset["kind"] == "visual" and packet:
            _require_id(packet, "flow packet id")
        else:
            _validate_local_path(source.get("path"), f"asset {asset['id']} source.path", base_dir)
    elif provider == "synthetic-ffmpeg":
        synth = source.get("synth")
        if not isinstance(synth, dict):
            raise ManifestError(f"asset {asset['id']} needs source.synth")
        if asset["kind"] == "narration":
            if synth.get("kind") != "sine":
                raise ManifestError(f"asset {asset['id']} synth.kind must be sine")
            for key in ("frequencyHz", "durationSec", "gainDb"):
                if not isinstance(synth.get(key), (int, float)) or isinstance(synth.get(key), bool):
                    raise ManifestError(f"asset {asset['id']} synth.{key} must be a number")
        elif synth.get("kind") != "card" or not isinstance(synth.get("title"), str) or not synth["title"]:
            raise ManifestError(f"visual asset {asset['id']} synth.kind must be card with a title")
    elif provider == "remotion":
        if not isinstance(source.get("component"), str) or not source["component"]:
            raise ManifestError(f"asset {asset['id']} needs source.component")
    if asset["kind"] == "narration":
        timing = asset.get("timing")
        if not isinstance(timing, str) or not timing.endswith(".json"):
            raise ManifestError(f"narration asset {asset['id']} needs a timing JSON path")
        _validate_local_path(timing, f"narration asset {asset['id']} timing", base_dir)


def _validate_local_path(value: Any, label: str, base_dir: Path | None) -> None:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{label} is required")
    if "://" in value:
        raise ManifestError(f"{label} must be a local file, not a URL")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{label} must be a relative path inside the episode")
    if base_dir is not None:
        resolved = (base_dir / path).resolve()
        root = base_dir.resolve()
        if root != resolved and root not in resolved.parents:
            raise ManifestError(f"{label} escapes the episode directory")
        if not resolved.is_file():
            raise ManifestError(f"{label} does not exist: {value}")


def _validate_scene(
    scene: Any,
    asset_ids: set[str],
    languages: list[str],
    active: str,
) -> None:
    if not isinstance(scene, dict):
        raise ManifestError("scene entries must be objects")
    _require_id(scene.get("id"), "scene id")
    duration = scene.get("durationSec")
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise ManifestError(f"scene {scene.get('id')} durationSec must be positive")
    refs = scene.get("assetIds")
    if not isinstance(refs, list) or not refs:
        raise ManifestError(f"scene {scene['id']} needs assetIds")
    for ref in refs:
        if ref not in asset_ids:
            raise ManifestError(f"scene {scene['id']} references missing asset {ref}")
    narration = scene.get("narration")
    if narration is not None:
        if not isinstance(narration, dict):
            raise ManifestError(f"scene {scene['id']} narration must be an object")
        if narration.get("assetId") not in asset_ids:
            raise ManifestError(f"scene {scene['id']} narration asset is missing")
        start = narration.get("inSec")
        end = narration.get("outSec")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            raise ManifestError(f"scene {scene['id']} narration range is invalid")
        if abs((end - start) - float(duration)) > (1 / REQUIRED_FPS):
            raise ManifestError(
                f"scene {scene['id']} narration range must match durationSec within one frame"
            )
    focal = scene.get("focal")
    if not _unit_point(focal):
        raise ManifestError(f"scene {scene['id']} focal.x and focal.y must be between 0 and 1")
    safe = scene.get("safe")
    if not isinstance(safe, dict):
        raise ManifestError(f"scene {scene['id']} needs safe margins")
    for edge in ("top", "bottom", "left", "right"):
        value = safe.get(edge)
        if not isinstance(value, (int, float)) or not 0 <= float(value) < 0.45:
            raise ManifestError(f"scene {scene['id']} safe.{edge} must be a fraction under 0.45")
    if float(safe["top"]) + float(safe["bottom"]) >= 0.9 or float(safe["left"]) + float(safe["right"]) >= 0.9:
        raise ManifestError(f"scene {scene['id']} safe margins leave no content area")
    text = scene.get("text")
    if not isinstance(text, dict):
        raise ManifestError(f"scene {scene['id']} text must be an object of language strings")
    for lang in languages:
        value = text.get(lang)
        if not isinstance(value, str) or value == "":
            raise ManifestError(f"scene {scene['id']} is missing exact display text for {lang}")
    if active not in text:
        raise ManifestError(f"scene {scene['id']} is missing active language {active}")
    caption = scene.get("caption")
    if not isinstance(caption, dict):
        raise ManifestError(f"scene {scene['id']} needs caption settings")
    scale = caption.get("fontScale")
    if not isinstance(scale, (int, float)) or not 0.02 <= float(scale) <= 0.08:
        raise ManifestError(f"scene {scene['id']} caption.fontScale is out of range")
    if caption.get("maxLines") not in (1, 2, 3):
        raise ManifestError(f"scene {scene['id']} caption.maxLines must be 1, 2, or 3")
    picture = scene.get("picture")
    if picture is not None:
        if not isinstance(picture, dict) or picture.get("mode") not in {"remotion", "card", "flow-master"}:
            raise ManifestError(f"scene {scene['id']} picture.mode must be remotion, card, or flow-master")
        if picture["mode"] in {"card", "flow-master"} and picture.get("assetId") not in asset_ids:
            raise ManifestError(f"scene {scene['id']} picture asset is missing")


def _validate_timeline(doc: dict[str, Any], scene_ids: set[str]) -> None:
    scenes = doc["scenes"]
    transitions = doc.get("transitions")
    if not isinstance(transitions, list):
        raise ManifestError("transitions must be a list")
    by_after: dict[str, dict[str, Any]] = {}
    for item in transitions:
        if not isinstance(item, dict):
            raise ManifestError("transition entries must be objects")
        if item.get("type") != "cut":
            raise ManifestError("only cut transitions are implemented in this tranche")
        if item.get("durationSec") != 0:
            raise ManifestError("cut transitions must have durationSec 0")
        after = item.get("after")
        if after not in scene_ids:
            raise ManifestError(f"transition references missing scene {after}")
        if after in by_after:
            raise ManifestError(f"duplicate transition after {after}")
        by_after[after] = item
    total = 0.0
    for index, scene in enumerate(scenes):
        total += float(scene["durationSec"])
        if index < len(scenes) - 1 and scene["id"] not in by_after:
            raise ManifestError(f"missing cut transition after {scene['id']}")
        total += float(by_after.get(scene["id"], {}).get("durationSec", 0))
    expected = doc.get("episodeDurationSec")
    if not isinstance(expected, (int, float)):
        raise ManifestError("episodeDurationSec is required")
    if abs(float(expected) - total) > (1 / REQUIRED_FPS):
        raise ManifestError(f"episodeDurationSec {expected} does not match timeline {total:.4f}")


def timeline(doc: dict[str, Any]) -> list[dict[str, float | str]]:
    cursor = 0.0
    rows: list[dict[str, float | str]] = []
    transitions = {item["after"]: item for item in doc.get("transitions") or []}
    for scene in doc["scenes"]:
        start = cursor
        end = start + float(scene["durationSec"])
        rows.append(
            {
                "id": scene["id"],
                "startSec": start,
                "endSec": end,
                "durationSec": float(scene["durationSec"]),
            }
        )
        cursor = end + float(transitions.get(scene["id"], {}).get("durationSec", 0))
    return rows


def _unit_point(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("x", "y"):
        number = value.get(key)
        if not isinstance(number, (int, float)) or not 0 <= float(number) <= 1:
            return False
    return True


def _require_id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ManifestError(f"{label} must be a short string")
    if any(ch not in ID_OK for ch in value):
        raise ManifestError(f"{label} {value!r} must be lowercase ascii, digits, and hyphens")
    if value[0] == "-" or value[-1] == "-":
        raise ManifestError(f"{label} {value!r} cannot start or end with a hyphen")
