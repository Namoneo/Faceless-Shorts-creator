"""Timing documents. Word times are explicit or absent. Nothing here invents alignment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATES = frozenset({"unaligned", "review", "manual"})


class TimingError(ValueError):
    pass


def load_timing(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TimingError(f"cannot read timing {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise TimingError("timing root must be an object")
    return doc


def resolve_timing(doc: dict[str, Any], audio_content_hash: str, scene_ids: set[str]) -> dict[str, Any]:
    """Bind a timing document to the final audio hash.

    Manual corrections replace display text only. They do not create start or end times.
    A hash mismatch demotes manual precision to review instead of pretending it still applies.
    Unaligned lines stay without word times.
    """
    if not isinstance(audio_content_hash, str) or len(audio_content_hash) != 64:
        raise TimingError("audio content hash must be a sha256 hex digest")
    lines = doc.get("lines")
    if not isinstance(lines, list) or not lines:
        raise TimingError("timing.lines must be a non-empty list")
    previous = doc.get("audioContentHash")
    stale = previous not in (None, "") and previous != audio_content_hash
    resolved_lines = [_copy_line(line, scene_ids, stale) for line in lines]
    corrections_out = [
        _apply_correction(correction, resolved_lines, audio_content_hash, stale)
        for correction in (doc.get("corrections") or [])
    ]
    for line in resolved_lines:
        _rollup(line)
        _check_words(line)
    return {
        "assetId": doc.get("assetId"),
        "audioContentHash": audio_content_hash,
        "stale": stale,
        "language": doc.get("language"),
        "lines": resolved_lines,
        "corrections": corrections_out,
    }


def line_for_scene(timing: dict[str, Any], scene_id: str) -> dict[str, Any] | None:
    for line in timing.get("lines") or []:
        if line.get("sceneId") == scene_id:
            return line
    return None


def display_words(line: dict[str, Any]) -> str | None:
    words = line.get("words")
    if not words:
        return None
    return " ".join(word["w"] for word in words)


def _copy_line(line: Any, scene_ids: set[str], stale: bool) -> dict[str, Any]:
    if not isinstance(line, dict):
        raise TimingError("timing line must be an object")
    scene_id = line.get("sceneId")
    if scene_id not in scene_ids:
        raise TimingError(f"timing line references missing scene {scene_id}")
    text = line.get("text")
    if not isinstance(text, str) or text == "":
        raise TimingError(f"timing line {scene_id} needs exact display text")
    state = line.get("state")
    if state not in STATES:
        raise TimingError(f"timing line {scene_id} state must be unaligned, review, or manual")
    copied: dict[str, Any] = {"sceneId": scene_id, "text": text, "state": state}
    words = line.get("words")
    if state == "unaligned":
        if words:
            raise TimingError(f"unaligned line {scene_id} must not include word times")
        return copied
    if not isinstance(words, list) or not words:
        raise TimingError(f"{state} line {scene_id} needs explicit word times")
    copied_words = []
    for word in words:
        copied_words.append(_copy_word(word, scene_id, state, stale))
    if stale and state == "manual":
        state = "review"
    copied["state"] = state
    copied["words"] = copied_words
    if stale:
        copied["stale"] = True
    return copied


def _copy_word(word: Any, scene_id: str, line_state: str, stale: bool) -> dict[str, Any]:
    if not isinstance(word, dict):
        raise TimingError(f"word on {scene_id} must be an object")
    token = word.get("w")
    if not isinstance(token, str) or token == "":
        raise TimingError(f"word on {scene_id} needs exact w text")
    if "start" not in word or "end" not in word:
        raise TimingError(f"word {token!r} on {scene_id} is missing an explicit time")
    start = word["start"]
    end = word["end"]
    if isinstance(start, bool) or isinstance(end, bool):
        raise TimingError(f"word {token!r} on {scene_id} has a non-numeric time")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
        raise TimingError(f"word {token!r} on {scene_id} has a bad time range")
    word_state = word.get("state", line_state)
    if word_state not in {"review", "manual"}:
        raise TimingError(f"word {token!r} cannot carry times in state {word_state!r}")
    if stale and word_state == "manual":
        word_state = "review"
    return {"w": token, "start": float(start), "end": float(end), "state": word_state}


def _apply_correction(
    correction: Any,
    lines: list[dict[str, Any]],
    audio_hash: str,
    stale: bool,
) -> dict[str, Any]:
    if not isinstance(correction, dict):
        raise TimingError("correction must be an object")
    scene_id = correction.get("sceneId")
    index = correction.get("wordIndex")
    replacement = correction.get("replacement")
    reason = correction.get("reason")
    if not isinstance(scene_id, str) or not isinstance(index, int) or index < 0:
        raise TimingError("correction needs sceneId and wordIndex")
    if not isinstance(replacement, str) or replacement == "":
        raise TimingError("correction replacement must be the exact display text")
    if not isinstance(reason, str) or reason == "":
        raise TimingError("correction reason is required")
    bound = correction.get("audioContentHash")
    record: dict[str, Any] = {
        "id": correction.get("id") or f"{scene_id}-{index}",
        "sceneId": scene_id,
        "wordIndex": index,
        "replacement": replacement,
        "reason": reason,
        "audioContentHash": audio_hash,
        "applied": False,
    }
    if bound not in (None, "", audio_hash):
        record["applied"] = False
        record["skipReason"] = "audio-hash-mismatch"
        record["audioContentHash"] = bound
        return record
    line = next((item for item in lines if item["sceneId"] == scene_id), None)
    if line is None or "words" not in line:
        raise TimingError(f"correction {scene_id}#{index} has no timed word to edit")
    words = line["words"]
    if index >= len(words):
        raise TimingError(f"correction wordIndex {index} is outside {scene_id}")
    words[index]["w"] = replacement
    words[index]["state"] = "review" if stale else "manual"
    record["applied"] = True
    return record


def _rollup(line: dict[str, Any]) -> None:
    if line["state"] == "unaligned":
        return
    states = {word["state"] for word in line["words"]}
    line["state"] = "manual" if states == {"manual"} else "review"


def _check_words(line: dict[str, Any]) -> None:
    if line["state"] == "unaligned":
        if "words" in line:
            raise TimingError("unaligned output must not contain words")
        return
    previous = -1.0
    for word in line["words"]:
        if word["start"] < previous:
            raise TimingError(f"word times on {line['sceneId']} go backwards")
        previous = word["end"]
