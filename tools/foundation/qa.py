"""QA checks. Allowances are explicit flags. Detection still runs when an allowance is set."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from tools.foundation.caption_measure import CaptionMeasureError, measure_captions
from tools.foundation.ffmpeg import black_intervals, duration_seconds, probe, silence_intervals, stream_of, volume_detect
from tools.foundation.layout import caption_overflow
from tools.foundation.manifest import timeline
from tools.foundation.timing import line_for_scene

SILENCE_MAX_DB = -45.0
SILENCE_MIN_SEC = 0.05
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
    checks.extend(check_uzbek_apostrophes(manifest))
    checks.extend(check_uzbek_address(manifest))
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
    language = manifest["language"]
    cases = []
    specs = []
    for scene in manifest["scenes"]:
        texts = _caption_texts(scene, language)
        allowed = bool(manifest["qa"]["allowCaptionOverflow"] or scene.get("qa", {}).get("allowCaptionOverflow"))
        for index, text in enumerate(texts):
            for fmt in manifest["formats"]:
                estimate = caption_overflow(text, fmt["width"], fmt["height"], scene["safe"], scene["caption"])
                case_id = f"{scene['id']}:{fmt['id']}" if len(texts) == 1 else f"{scene['id']}:{fmt['id']}:{index}"
                cases.append(
                    {
                        "id": case_id,
                        "text": text,
                        "widthPx": estimate["maxWidthPx"],
                        "fontPx": estimate["fontPx"],
                        "maxLines": estimate["maxLines"],
                    }
                )
                specs.append((scene["id"], fmt["id"], allowed, estimate, case_id))
    try:
        measured = {item["id"]: item for item in measure_captions(cases)}
    except CaptionMeasureError as exc:
        return [
            _item(
                "caption-overflow",
                False,
                {
                    "sceneId": scene_id,
                    "formatId": format_id,
                    "detected": None,
                    "allowed": allowed,
                    "verified": False,
                    "reason": str(exc),
                    "estimate": estimate,
                    "estimateRole": "advisory",
                },
            )
            for scene_id, format_id, allowed, estimate, _case_id in specs
        ]
    items = []
    for scene_id, format_id, allowed, estimate, case_id in specs:
        box = measured[case_id]
        verified = bool(box.get("fontOk"))
        detected = bool(box.get("overflow"))
        items.append(
            _item(
                "caption-overflow",
                verified and ((not detected) or allowed),
                {
                    "sceneId": scene_id,
                    "formatId": format_id,
                    "detected": detected,
                    "allowed": allowed,
                    "verified": verified,
                    "geometry": box,
                    "estimate": estimate,
                    "estimateRole": "advisory",
                    "estimateMissed": (not estimate["overflow"]) and detected,
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
    if height > width:
        items.extend(check_portrait_fill(path, width, height, manifest))
    return items


MIN_PORTRAIT_CONTENT = 0.70
DENSE_PICTURE = 0.50
APOSTROPHE_MARKS = "\u0027\u02bb\u2018\u2019\u02bc"
ASCII_OG = re.compile(r"[OoGg]\u0027")


def _caption_texts(scene: dict[str, Any], language: str) -> list[str]:
    cues = scene.get("captionCues")
    if isinstance(cues, list) and cues:
        texts = []
        for cue in cues:
            if isinstance(cue, str) and cue.strip():
                texts.append(cue)
            elif isinstance(cue, dict) and isinstance(cue.get("text"), str) and cue["text"].strip():
                texts.append(cue["text"])
        if texts:
            return texts
    return [scene["text"][language]]


def check_uzbek_apostrophes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Fail when an Uzbek o' or g' is a straight ASCII apostrophe (U+0027).

    U+02BB and U+2018 stay legal. They are different letters and are not folded.
    """
    items = []
    for scene in manifest["scenes"]:
        text = scene.get("text", {}).get("uz-Latn", "")
        if not isinstance(text, str):
            continue
        pairs = []
        for match in re.finditer(r"[OoGg].", text):
            mark = match.group(0)[1]
            if mark in APOSTROPHE_MARKS:
                pairs.append({"letters": match.group(0)[0] + mark, "mark": f"U+{ord(mark):04X}"})
        hits = [match.group(0) for match in ASCII_OG.finditer(text)]
        items.append(
            _item(
                "uzbek-apostrophe",
                not hits,
                {"sceneId": scene["id"], "hits": hits, "marks": pairs},
            )
        )
    return items


def check_uzbek_address(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Warn on common informal sen-form endings. This check does not fail the episode.

    The documented list is -san, -sanmi, -ding, and -ngmi on a word, in that
    role as a second-person ending. It is a suffix scan, not a parse, so a
    word that merely ends the same way can be flagged.
    """
    endings = _sen_form_endings()
    items = []
    for scene in manifest["scenes"]:
        text = scene.get("text", {}).get("uz-Latn", "")
        if not isinstance(text, str):
            continue
        hits = _sen_form_hits(text, endings)
        items.append(
            _item(
                "uzbek-address",
                True,
                {
                    "sceneId": scene["id"],
                    "warning": bool(hits),
                    "hits": hits,
                    "list": [f"-{item}" for item in endings],
                    "role": "warning-only",
                },
            )
        )
    return items


def _sen_form_endings() -> tuple[str, ...]:
    from tools.foundation.voicestudio import load_voice_config

    raw = load_voice_config()["languages"]["uz-Latn"]["address"]["senFormWarnings"]
    cleaned = tuple(item.lstrip("-").lower() for item in raw)
    return tuple(sorted(cleaned, key=len, reverse=True))


def _sen_form_hits(text: str, endings: tuple[str, ...]) -> list[dict[str, str]]:
    hits = []
    for match in re.finditer(r"[0-9A-Za-z\u02bb\u2018\u2019\u02bc\u0027]+", text):
        token = match.group(0)
        folded = token.lower().replace("\u02bb", "").replace("\u2018", "").replace("\u2019", "").replace("\u02bc", "").replace("'", "")
        for ending in endings:
            if len(folded) > len(ending) and folded.endswith(ending):
                hits.append({"token": token, "ending": f"-{ending}"})
                break
    return hits


def check_portrait_fill(path: Path, width: int, height: int, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Fail a portrait frame whose picture is a small dense block on empty bars.

    A full-frame field, a text card, and a blurred fill all pass. A 16:9 picture
    padded into the portrait frame does not.
    """
    items = []
    for row in timeline(manifest):
        mid = (float(row["startSec"]) + float(row["endSec"])) / 2
        frame = _read_frame(path, mid, width, height)
        if frame is None:
            items.append(_item("portrait-fill", False, {"sceneId": row["id"], "problem": "could not read a frame"}))
            continue
        verdict = _portrait_content(frame, width, height)
        items.append(_item("portrait-fill", not verdict["padded"], {"sceneId": row["id"], **verdict}))
    return items


def _read_frame(path: Path, time_sec: float, width: int, height: int) -> bytes | None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ss",
            f"{time_sec:.3f}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    expected = width * height * 3
    if result.returncode != 0 or len(result.stdout) != expected:
        return None
    return result.stdout


def _portrait_content(frame: bytes, width: int, height: int) -> dict[str, Any]:
    def channel(x: int, y: int) -> tuple[int, int, int]:
        index = (y * width + x) * 3
        return frame[index], frame[index + 1], frame[index + 2]

    bg = channel(min(2, width - 1), 0)

    def is_bg(x: int, y: int) -> bool:
        red, green, blue = channel(x, y)
        return abs(red - bg[0]) + abs(green - bg[1]) + abs(blue - bg[2]) < 36

    min_x = width
    max_x = -1
    min_y = height
    max_y = -1
    ink = 0
    samples = 0
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            samples += 1
            if is_bg(x, y):
                continue
            ink += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
    if ink == 0 or max_x < min_x:
        return {"contentArea": 1.0, "padded": False, "kind": "full-frame", "density": 0.0, "threshold": MIN_PORTRAIT_CONTENT}
    box_w = max_x - min_x + 2
    box_h = max_y - min_y + 2
    area = (box_w * box_h) / float(width * height)
    inside = 0
    inside_ink = 0
    for y in range(min_y, max_y + 1, 2):
        for x in range(min_x, max_x + 1, 2):
            inside += 1
            inside_ink += not is_bg(x, y)
    density = inside_ink / inside if inside else 0.0
    # Text on a full-bleed field has a small, sparse glyph box. A padded 16:9
    # picture is a dense rectangle that does not fill the portrait frame.
    padded = density >= DENSE_PICTURE and area < MIN_PORTRAIT_CONTENT
    kind = "padded-source" if padded else ("text-on-field" if density < DENSE_PICTURE else "filled")
    reported = 1.0 if kind != "padded-source" else area
    return {
        "contentArea": reported,
        "bandArea": area,
        "padded": padded,
        "kind": kind,
        "density": density,
        "threshold": MIN_PORTRAIT_CONTENT,
        "samples": samples,
    }


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


def check_black_span(path: Path, start: float, end: float, allow_black: bool, label: str) -> dict[str, Any]:
    intervals = [item for item in black_intervals(path) if _overlap(item["start"], item["end"], start, end) > 0.001]
    detected = bool(intervals)
    return _item(
        "black-frame",
        (not detected) or allow_black,
        {
            "label": label,
            "detected": detected,
            "allowed": allow_black,
            "intervals": intervals,
            "method": "ffmpeg-blackdetect",
            "minDurationSec": 0.001,
            "pictureBlackRatio": 0.98,
            "pixelBlackThreshold": 0.10,
        },
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
        items.append(
            check_black_span(path, float(row["startSec"]), float(row["endSec"]), scene_black, f"{label}:{row['id']}")
        )
        gaps = [
            item
            for item in silence_intervals(path, noise_db=SILENCE_MAX_DB, min_duration=SILENCE_MIN_SEC)
            if _overlap(item["start"], item["end"], float(row["startSec"]), float(row["endSec"])) > 0.001
        ]
        items.append(
            _item(
                "scene-silence",
                (not gaps) or scene_silence,
                {
                    "label": f"{label}:{row['id']}",
                    "detected": bool(gaps),
                    "allowed": scene_silence,
                    "intervals": gaps,
                    "method": "ffmpeg-silencedetect",
                    "noiseDb": SILENCE_MAX_DB,
                    "minDurationSec": SILENCE_MIN_SEC,
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


def _overlap(start: float, end: float, window_start: float, window_end: float) -> float:
    return max(0.0, min(end, window_end) - max(start, window_start))


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
