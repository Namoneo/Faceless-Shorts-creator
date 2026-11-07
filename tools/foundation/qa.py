"""QA checks. Allowances are explicit flags. Detection still runs when an allowance is set."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.foundation.ffmpeg import duration_seconds, frame_luma, probe, stream_of, volume_detect
from tools.foundation.layout import caption_overflow
from tools.foundation.manifest import timeline
from tools.foundation.timing import line_for_scene

BLACK_LUMA = 20.0
SILENCE_MAX_DB = -45.0
CLIP_MAX_DB = -0.5
DURATION_FRAMES = 2


def check_episode(
    manifest: dict[str, Any],
    assembled: dict[str, Path],
    timing_by_asset: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.extend(check_timeline(manifest))
    checks.extend(check_captions(manifest))
    checks.extend(check_timing_binding(manifest, timing_by_asset))
    for fmt in manifest["formats"]:
        path = assembled[fmt["id"]]
        checks.extend(check_media(path, fmt, manifest))
    passed = all(item["pass"] for item in checks)
    return {"pass": passed, "checks": checks}


def check_timeline(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = timeline(manifest)
    expected = float(manifest["episodeDurationSec"])
    actual = float(rows[-1]["endSec"]) if rows else 0.0
    problems = []
    if abs(actual - expected) > (1 / manifest["fps"]):
        problems.append(f"timeline ends at {actual:.4f}s, episodeDurationSec is {expected}")
    seen_end = 0.0
    for row in rows:
        if float(row["startSec"]) < seen_end - (1 / manifest["fps"]):
            problems.append(f"{row['id']} overlaps the previous scene")
        seen_end = float(row["endSec"])
    return [_item("timeline", not problems, {"problems": problems, "scenes": rows})]


def check_captions(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    language = manifest["language"]
    for scene in manifest["scenes"]:
        text = scene["text"][language]
        for fmt in manifest["formats"]:
            box = caption_overflow(text, fmt["width"], fmt["height"], scene["safe"], scene["caption"])
            allowed = bool(manifest["qa"]["allowCaptionOverflow"] or scene.get("qa", {}).get("allowCaptionOverflow"))
            items.append(
                _item(
                    "caption-overflow",
                    (not box["overflow"]) or allowed,
                    {
                        "sceneId": scene["id"],
                        "formatId": fmt["id"],
                        "detected": box["overflow"],
                        "allowed": allowed,
                        "estimate": box,
                    },
                )
            )
    return items


def check_timing_binding(manifest: dict[str, Any], timing_by_asset: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    placed = {row["id"]: row for row in timeline(manifest)}
    for scene in manifest["scenes"]:
        narration = scene.get("narration")
        if not narration:
            continue
        timing = timing_by_asset.get(narration["assetId"])
        if timing is None:
            items.append(_item("timing", False, {"sceneId": scene["id"], "problem": "missing timing"}))
            continue
        line = line_for_scene(timing, scene["id"])
        if line is None:
            items.append(_item("timing", False, {"sceneId": scene["id"], "problem": "scene has no timing line"}))
            continue
        if line["text"] != scene["text"][manifest["language"]]:
            items.append(
                _item(
                    "timing-display",
                    False,
                    {
                        "sceneId": scene["id"],
                        "problem": "timing text differs from manifest display text",
                        "timing": line["text"],
                        "manifest": scene["text"][manifest["language"]],
                    },
                )
            )
        else:
            items.append(_item("timing-display", True, {"sceneId": scene["id"], "text": line["text"]}))
        window = placed[scene["id"]]
        if line["state"] == "unaligned":
            if "words" in line:
                items.append(_item("timing-unaligned", False, {"sceneId": scene["id"], "problem": "words present"}))
            else:
                items.append(_item("timing-unaligned", True, {"sceneId": scene["id"], "state": "unaligned"}))
            continue
        for word in line.get("words") or []:
            if word["start"] < float(window["startSec"]) - 1e-6 or word["end"] > float(window["endSec"]) + 1e-6:
                items.append(
                    _item(
                        "timing-range",
                        False,
                        {"sceneId": scene["id"], "word": word["w"], "problem": "word time outside the scene"},
                    )
                )
    if not any(item["id"] == "timing-range" and not item["pass"] for item in items):
        items.append(_item("timing-range", True, {"problem": None}))
    return items


def check_media(path: Path, fmt: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    label = fmt["id"]
    if not path.is_file():
        return [_item("media-missing", False, {"formatId": label, "path": str(path)})]
    info = probe(path)
    video = stream_of(info, "video")
    audio = stream_of(info, "audio")
    if video is None:
        items.append(_item("video-stream", False, {"formatId": label, "problem": "no video stream"}))
        return items
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _fps(video.get("avg_frame_rate"))
    items.append(
        _item(
            "ratio",
            width == fmt["width"] and height == fmt["height"],
            {"formatId": label, "width": width, "height": height, "expected": [fmt["width"], fmt["height"]]},
        )
    )
    items.append(_item("fps", fps == manifest["fps"], {"formatId": label, "fps": fps, "expected": manifest["fps"]}))
    codec = video.get("codec_name")
    items.append(_item("video-codec", codec == "h264", {"formatId": label, "codec": codec}))
    audio_codec = None if audio is None else audio.get("codec_name")
    items.append(_item("audio-stream", audio is not None and audio_codec == "aac", {"formatId": label, "codec": audio_codec}))
    duration = duration_seconds(info)
    expected = float(manifest["episodeDurationSec"])
    tolerance = DURATION_FRAMES / float(manifest["fps"])
    duration_ok = duration is not None and abs(duration - expected) <= tolerance
    items.append(
        _item(
            "duration",
            duration_ok,
            {"formatId": label, "durationSec": duration, "expected": expected, "toleranceSec": tolerance},
        )
    )
    items.extend(_scene_picture_and_audio(path, manifest, label))
    return items


def check_levels(path: Path, allow_silence: bool, allow_clipping: bool, label: str) -> list[dict[str, Any]]:
    levels = volume_detect(path)
    silent = levels["max_db"] is None or levels["max_db"] <= SILENCE_MAX_DB
    clipping = levels["max_db"] is not None and levels["max_db"] > CLIP_MAX_DB
    return [
        _item(
            "silence",
            (not silent) or allow_silence,
            {"label": label, "detected": silent, "allowed": allow_silence, "levels": levels},
        ),
        _item(
            "clipping",
            (not clipping) or allow_clipping,
            {"label": label, "detected": clipping, "allowed": allow_clipping, "levels": levels},
        ),
    ]


def check_black(path: Path, time_sec: float, allow_black: bool, label: str) -> dict[str, Any]:
    luma = frame_luma(path, time_sec)
    detected = luma is None or luma < BLACK_LUMA
    return _item(
        "black-frame",
        (not detected) or allow_black,
        {"label": label, "timeSec": time_sec, "luma": luma, "detected": detected, "allowed": allow_black, "threshold": BLACK_LUMA},
    )


def _scene_picture_and_audio(path: Path, manifest: dict[str, Any], label: str) -> list[dict[str, Any]]:
    items = []
    allow_black = bool(manifest["qa"]["allowBlack"])
    allow_silence = bool(manifest["qa"]["allowSilence"])
    allow_clip = bool(manifest["qa"]["allowClipping"])
    for row in timeline(manifest):
        scene = next(item for item in manifest["scenes"] if item["id"] == row["id"])
        scene_black = allow_black or bool(scene.get("qa", {}).get("allowBlack"))
        scene_silence = allow_silence or bool(scene.get("qa", {}).get("allowSilence"))
        midpoint = (float(row["startSec"]) + float(row["endSec"])) / 2
        items.append(check_black(path, midpoint, scene_black, f"{label}:{row['id']}"))
        levels = volume_detect(path, start=float(row["startSec"]), duration=float(row["durationSec"]))
        silent = levels["max_db"] is None or levels["max_db"] <= SILENCE_MAX_DB
        items.append(
            _item(
                "scene-silence",
                (not silent) or scene_silence,
                {
                    "label": f"{label}:{row['id']}",
                    "detected": silent,
                    "allowed": scene_silence,
                    "levels": levels,
                },
            )
        )
    whole = volume_detect(path)
    clipping = whole["max_db"] is not None and whole["max_db"] > CLIP_MAX_DB
    items.append(
        _item(
            "clipping",
            (not clipping) or allow_clip,
            {"label": label, "detected": clipping, "allowed": allow_clip, "levels": whole},
        )
    )
    return items


def _fps(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    num, den = value.split("/", 1)
    try:
        denominator = float(den)
        if denominator == 0:
            return None
        return float(num) / denominator
    except ValueError:
        return None


def _item(check_id: str, passed: bool, detail: dict[str, Any]) -> dict[str, Any]:
    return {"id": check_id, "pass": passed, "detail": detail}
