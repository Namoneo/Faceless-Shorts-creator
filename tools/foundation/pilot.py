"""Assemble the approved pilot from imported Flow stills and a narration folder.

The command does not call Flow or VoiceStudio. A placeholder narration is labelled
on the picture and in the report, and is not a final episode.
"""

from __future__ import annotations

import array
import json
import math
import re
import subprocess
import wave
from pathlib import Path
from typing import Any

from tools.foundation.ffmpeg import duration_seconds, probe, run
from tools.foundation.flow import (
    FlowError,
    import_export,
    mark_ready_to_import,
    read_packet,
    release_for_manual,
    write_packet,
)
from tools.foundation.hashutil import sha256_file
from tools.foundation.layout import caption_overflow
from tools.foundation.manifest import ManifestError, validate_manifest
from tools.foundation.narration import prepare_one
from tools.foundation.pictures import FONT, wrap_text
from tools.foundation.qa import check_episode
from tools.foundation.timing import resolve_timing

FPS = 30
HOLD_SEC = 2.0
SHORT_LEAD_SEC = 0.3
SHORT_TARGET_SEC = 54.85
SHORT_MIN_GAP_SEC = 0.08
COUNT_DIGITS = ("5", "4", "3", "2", "1")
AUDIO_RATE = 48000
PLACEHOLDER_LABEL = "PLACEHOLDER NARRATION - not a VoiceStudio take"
LABELS = {
    "still-palov": "PALOV",
    "still-somsa": "SOMSA",
    "still-choy": "CHOY",
    "still-qatiq": "QATIQ",
    "still-samarqand": "SAMARQAND",
    "still-buxoro": "BUXORO",
    "still-doira": "DOIRA",
    "still-rubob": "RUBOB",
    "still-kurash": "KURASH",
    "still-futbol": "FUTBOL",
    "still-sumalak": "SUMALAK",
    "still-holva": "HOLVA",
    "still-bozor": "BOZOR",
    "still-hovli": "HOVLI",
}
FACTS = {
    1: {"still": "still-palov", "line": "2016 · YuNESKO", "sub": None},
    2: {"still": "still-choy", "line": "3 marta", "sub": None},
    3: {"still": "still-buxoro", "line": "1127-yil", "sub": "~46 m"},
    4: {"still": "still-doira", "line": "halqali ramka", "sub": None},
    5: {"still": "still-kurash", "line": "2018 · Jakarta", "sub": None},
    6: {"still": "still-sumalak", "line": "21-mart", "sub": None},
    7: {"still": "still-bozor", "line": "chekich", "sub": None},
}
TITLES = {
    "hook": ["7 ta tanlov", "Qani, tanlang!"],
    "rules": ["Qoida", "Ikki yo\u02bbl. Siz tanlaysiz."],
    "cta": ["Qani, tanlang!", "Obuna bo\u02bbling"],
    "ending": ["Qani, tanlang!", "Shu yerda xayrlashamiz"],
}
COUNTS = {"besh", "tort", "uch", "ikki", "bir"}
SAFE = {"top": 0.08, "bottom": 0.18, "left": 0.06, "right": 0.06}
STILL_PROVENANCE = {
    "tool": "Flow",
    "origin": "uzbekajon.com",
    "note": "Nano Banana 2.1 still, 16:9, imported locally. 0 credits.",
    "model": "Nano Banana 2.1",
    "credits": 0,
}


class PilotError(ValueError):
    pass


def make_pilot(episode_dir: Path, narration_dir: Path, stills_dir: Path, out_dir: Path) -> dict[str, Any]:
    episode_dir = episode_dir.resolve()
    narration_dir = narration_dir.resolve()
    stills_dir = stills_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    timings = _read_timings(narration_dir / "timings.json")
    placeholder = bool(timings.get("placeholder"))
    packets = import_stills(episode_dir / "packets", stills_dir, episode_dir / "cache" / "flow")
    _record_hashes(episode_dir, packets)
    design = _load_design(episode_dir)
    by_id = {item["id"]: item for item in packets}
    scenes = json.loads((episode_dir / "scenes.json").read_text(encoding="utf-8"))
    shorts_plan = json.loads((episode_dir / "shorts.json").read_text(encoding="utf-8"))
    source_wav = narration_dir / "narration.wav"
    source_duration = _probe_duration(source_wav) if source_wav.is_file() else None
    if source_duration is None and not placeholder:
        raise PilotError("narration.wav is missing")
    if source_duration is not None and timings.get("duration") is not None:
        if abs(source_duration - float(timings["duration"])) > 1.0:
            raise PilotError("narration.wav duration does not match timings.json")
    aligned = _align(scenes, timings, by_id, stills_dir, source_duration)
    episode_duration = sum(scene["duration"] for scene in aligned)
    if source_duration is not None and abs(episode_duration - source_duration) > 1.0:
        raise PilotError("assembly would cut or stretch the narration")
    narration_path = _narration_file(narration_dir, timings, out_dir, episode_duration)
    canonical = _canonicalize(narration_path, out_dir)
    mix_path, loudness = _mix_show(Path(canonical["canonicalPath"]), aligned, out_dir, episode_duration)
    episode_video = out_dir / "assemble" / "landscape.mp4"
    _render_show(
        aligned, stills_dir, out_dir, episode_video, 1920, 1080, placeholder, None, None, mix_path, episode_duration, design
    )
    short_reports = []
    for plan in shorts_plan:
        short_reports.append(
            _render_short(plan, aligned, mix_path, out_dir, placeholder, by_id, stills_dir, design)
        )
    manifest = _manifest(
        episode_dir, out_dir, aligned, canonical, episode_duration, landscape_only=True, episode_id=str(design["id"])
    )
    timing_doc = _foundation_timing(aligned, canonical["canonicalContentHash"])
    qa = check_episode(manifest, {"landscape": episode_video}, {"narration-main": timing_doc})
    short_qa = []
    for item in short_reports:
        checked = check_episode(item["manifest"], {"portrait": item["path"]}, {"narration-main": item["timing"]})
        item["qaPass"] = checked["pass"]
        (out_dir / f"{item['id']}-qa.json").write_text(json.dumps(checked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        short_qa.append(
            {
                "id": item["id"],
                "pass": checked["pass"],
                "durationSec": item["durationSec"],
                "path": str(item["path"]),
            }
        )
        item.pop("manifest", None)
        item.pop("timing", None)
    cost = _cost(packets, placeholder, timings)
    report = {
        "episodeId": design["id"],
        "placeholder": placeholder,
        "final": False if placeholder else True,
        "label": PLACEHOLDER_LABEL if placeholder else None,
        "keptFullTake": source_duration is not None,
        "qaPass": bool(qa["pass"]) and all(item["pass"] for item in short_qa),
        "episode": {
            "path": str(episode_video),
            "durationSec": episode_duration,
            "sourceDurationSec": source_duration,
            "qaPass": qa["pass"],
        },
        "shorts": short_qa,
        "stills": [{"id": item["id"], "sourceHash": item["sourceHash"], "status": item["status"]} for item in packets],
        "narration": {
            "path": str(narration_path),
            "canonicalHash": canonical["canonicalContentHash"],
            "door": "import",
            "engine": "placeholder-sine" if placeholder else str(timings.get("engine") or "imported-file"),
            "voice": None if placeholder else timings.get("voice"),
            "durationSec": source_duration if source_duration is not None else episode_duration,
        },
        "cost": cost,
        "loudness": loudness,
    }
    (out_dir / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "cost.json").write_text(json.dumps(cost, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["qaPass"]:
        raise PilotError(f"pilot QA failed; see {out_dir / 'qa.json'}")
    return report


def import_stills(packet_dir: Path, stills_dir: Path, cache_dir: Path) -> list[dict[str, Any]]:
    sums = _checksums(stills_dir / "SHA256SUMS")
    imported = []
    for path in sorted(packet_dir.glob("still-*.json")):
        packet = read_packet(path)
        source = stills_dir / f"{packet['id']}.jpg"
        if not source.is_file():
            raise PilotError(f"missing still {source.name}")
        digest = sha256_file(source)
        if sums and sums.get(source.name) not in (None, digest):
            raise PilotError(f"{source.name} does not match SHA256SUMS")
        if packet.get("status") == "validated" and packet.get("sourceHash") == digest:
            imported.append(packet)
            continue
        if packet.get("status") == "validated":
            raise PilotError(f"{packet['id']} is validated with a different hash")
        current = packet
        if current.get("status") == "planned":
            current = release_for_manual(current)
        if current.get("status") == "awaiting_manual_generation":
            current = mark_ready_to_import(current)
        validated = import_export(current, source, STILL_PROVENANCE, cache_dir)
        if validated.get("sourceHash") != digest:
            raise PilotError(f"imported hash for {packet['id']} does not match the still")
        validated["cachedPath"] = None
        write_packet(path, validated)
        imported.append(validated)
    if len(imported) != 14:
        raise PilotError(f"expected 14 still packets, found {len(imported)}")
    return imported


def _record_hashes(episode_dir: Path, packets: list[dict[str, Any]]) -> None:
    by_id = {item["id"]: item for item in packets}
    shorts_path = episode_dir / "shorts.json"
    shorts = json.loads(shorts_path.read_text(encoding="utf-8"))
    for plan in shorts:
        for asset in plan["assets"]:
            asset["sourceHash"] = by_id[asset["packetId"]]["sourceHash"]
            asset["regenerated"] = False
    shorts_path.write_text(json.dumps(shorts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_path = episode_dir / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    for item in plan.get("flowPackets", []):
        packet = by_id.get(item["id"])
        if packet is None:
            continue
        item["status"] = packet["status"]
        item["sourceHash"] = packet["sourceHash"]
        item["deliveredModel"] = "Nano Banana 2.1"
        item["credits"] = 0
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_placeholder_timings(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    sections = []
    chunks = []
    for scene in scenes:
        start = float(scene["startSec"])
        end = start + float(scene["durationSec"])
        text = scene["text"]
        sections.append({"id": scene["id"], "start": round(start, 3), "end": round(end, 3), "text": text})
        speech_end = end - HOLD_SEC if scene.get("role") == "countdown" else end
        pieces = _sentences(text)
        weights = [max(1, len(piece.split())) for piece in pieces]
        span = max(0.2, speech_end - start)
        cursor = start
        for index, (piece, weight) in enumerate(zip(pieces, weights)):
            if index == len(pieces) - 1:
                chunk_end = speech_end
            else:
                chunk_end = cursor + span * weight / sum(weights)
            chunks.append(
                {
                    "id": f"{scene['id']}-{index}",
                    "sectionId": scene["id"],
                    "start": round(cursor, 3),
                    "end": round(chunk_end, 3),
                    "text": piece,
                }
            )
            cursor = chunk_end
    return {
        "placeholder": True,
        "sampleRate": 24000,
        "channels": 1,
        "note": "Synthetic timings stub. Not a VoiceStudio recording. Each countdown section ends with 2.0 s of silence.",
        "sections": sections,
        "chunks": chunks,
    }


def _align(
    scenes: list[dict[str, Any]],
    timings: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    stills_dir: Path,
    source_duration: float | None,
) -> list[dict[str, Any]]:
    if timings["sections"] and "section" in timings["sections"][0]:
        if source_duration is None:
            raise PilotError("a recorded timings file needs narration.wav")
        return _align_recorded(scenes, timings, packets, stills_dir, source_duration)
    return _align_ids(scenes, timings, packets, stills_dir)


def _align_recorded(
    scenes: list[dict[str, Any]],
    timings: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    stills_dir: Path,
    source_duration: float,
) -> list[dict[str, Any]]:
    """Keep the whole take. Choice stills cover the question and the countdown. The fact is a card."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for chunk in timings["chunks"]:
        grouped.setdefault(str(chunk["section"]), []).append(chunk)
    cards = {
        "Hook": ("hook", "hook"),
        "Qoida": ("rules", "rules"),
        "Chaqiruv": ("cta", "cta"),
        "Yakun": ("ending", "ending"),
    }
    specs: list[dict[str, Any]] = []
    sections = timings["sections"]
    for index, section in enumerate(sections):
        name = str(section["section"])
        start = float(section["start"])
        end = float(sections[index + 1]["start"]) if index + 1 < len(sections) else source_duration
        group = grouped.get(name, [])
        if name in cards:
            scene_id, role = cards[name]
            specs.append({"id": scene_id, "role": role, "start": start, "end": end, "chunks": group, "round": None})
            continue
        match = re.fullmatch(r"Tanlov ([1-7])", name)
        if match is None:
            raise PilotError(f"unknown timings section {name}")
        number = int(match.group(1))
        counted = next((chunk for chunk in group if chunk.get("countdown")), None)
        if counted is None:
            raise PilotError(f"{name} has no countdown chunk")
        fact = next(
            (
                chunk
                for chunk in group
                if float(chunk["start"]) >= float(counted["end"]) and str(chunk["text"]).lstrip().startswith("Fakt")
            ),
            None,
        )
        if fact is None:
            raise PilotError(f"{name} has no fact chunk")
        specs.append(
            {
                "id": f"r{number}-choice",
                "role": "choice",
                "start": start,
                "end": float(counted["start"]),
                "chunks": [chunk for chunk in group if float(chunk["end"]) <= float(counted["start"]) + 0.001],
                "round": number,
            }
        )
        specs.append(
            {
                "id": f"r{number}-count",
                "role": "countdown",
                "start": float(counted["start"]),
                "end": float(fact["start"]),
                "chunks": [
                    chunk
                    for chunk in group
                    if float(chunk["start"]) >= float(counted["start"]) - 0.001 and float(chunk["end"]) <= float(fact["start"]) + 0.001
                ],
                "round": number,
            }
        )
        specs.append(
            {
                "id": f"r{number}-fact",
                "role": "reveal",
                "start": float(fact["start"]),
                "end": end,
                "chunks": [chunk for chunk in group if float(chunk["start"]) >= float(fact["start"]) - 0.001],
                "round": number,
            }
        )
    total_frames = max(1, _frames(source_duration))
    cuts = [int(round(spec["start"] * FPS)) for spec in specs] + [total_frames]
    cuts[0] = 0
    for index in range(1, len(cuts)):
        if cuts[index] <= cuts[index - 1]:
            cuts[index] = cuts[index - 1] + 1
    if cuts[-1] != total_frames:
        raise PilotError("aligned scenes do not reach the end of the narration")
    aligned = []
    for index, spec in enumerate(specs):
        start_frame = cuts[index]
        end_frame = cuts[index + 1]
        start = start_frame / FPS
        duration = (end_frame - start_frame) / FPS
        cues = []
        spoken = []
        countdown = None
        for chunk in spec["chunks"]:
            cue_start = max(0.0, float(chunk["start"]) - start)
            cue_end = min(duration, float(chunk["end"]) - start)
            if cue_end <= cue_start:
                continue
            spoken.append(str(chunk["text"]).strip())
            if chunk.get("countdown"):
                countdown = {"start": round(cue_start, 3), "end": round(cue_end, 3)}
                continue
            pieces = _fit_caption(str(chunk["text"]))
            weights = [max(1, len(piece)) for piece in pieces]
            cursor = cue_start
            span = cue_end - cue_start
            for piece_index, (piece, weight) in enumerate(zip(pieces, weights)):
                piece_end = cue_end if piece_index == len(pieces) - 1 else cursor + span * weight / sum(weights)
                cues.append({"text": piece, "start": round(cursor, 3), "end": round(piece_end, 3)})
                cursor = piece_end
        plates = []
        if spec["role"] in {"choice", "countdown"}:
            plates = _plates(scenes, f"r{spec['round']}-choice", packets, stills_dir)
        aligned.append(
            {
                "id": spec["id"],
                "role": spec["role"],
                "start": start,
                "end": start + duration,
                "duration": duration,
                "text": " ".join(spoken),
                "cues": cues,
                "plates": plates,
                "countdown": countdown,
                "countdownBeats": countdown_beats(
                    countdown["start"],
                    countdown["end"],
                    cues[0]["start"] if cues else countdown["end"],
                )
                if countdown
                else None,
                "round": spec["round"],
                "allowSilence": spec["role"] == "countdown",
            }
        )
    return aligned


def _align_ids(
    scenes: list[dict[str, Any]],
    timings: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    stills_dir: Path,
) -> list[dict[str, Any]]:
    sections = {item["id"]: item for item in timings["sections"]}
    origin = float(timings["sections"][0]["start"])
    aligned = []
    cursor = 0.0
    for scene in scenes:
        section = sections.get(scene["id"])
        if section is None:
            raise PilotError(f"timings.json has no section {scene['id']}")
        start = _frames(float(section["start"]) - origin) / FPS
        end = _frames(float(section["end"]) - origin) / FPS
        if abs(start - cursor) > (1 / FPS):
            raise PilotError(f"section {scene['id']} leaves a gap at {cursor:.3f}")
        duration = max(1 / FPS, end - start)
        cues = []
        for chunk in timings["chunks"]:
            if chunk.get("sectionId") != scene["id"]:
                continue
            cue_start = _frames(float(chunk["start"]) - origin) / FPS - start
            cue_end = _frames(float(chunk["end"]) - origin) / FPS - start
            if cue_end <= 0 or cue_start >= duration:
                continue
            cues.append(
                {
                    "text": str(chunk["text"]),
                    "start": round(max(0.0, cue_start), 3),
                    "end": round(min(duration, cue_end), 3),
                }
            )
        plates = []
        if scene.get("role") in {"choice", "countdown"}:
            for plate_id in scene.get("flowPlates") or []:
                packet = packets[plate_id]
                source = stills_dir / f"{plate_id}.jpg"
                if sha256_file(source) != packet["sourceHash"]:
                    raise PilotError(f"{plate_id} no longer matches the validated hash")
                plates.append({"id": plate_id, "path": source, "focal": packet.get("focal") or {"x": 0.5, "y": 0.5}})
        aligned.append(
            {
                "id": scene["id"],
                "role": scene.get("role"),
                "start": round(start, 3),
                "end": round(start + duration, 3),
                "duration": round(duration, 3),
                "text": str(section["text"]),
                "cues": cues,
                "plates": plates,
                "allowSilence": scene.get("role") == "countdown",
            }
        )
        cursor = start + duration
    return aligned


def _plates(
    scenes: list[dict[str, Any]],
    scene_id: str,
    packets: dict[str, dict[str, Any]],
    stills_dir: Path,
) -> list[dict[str, Any]]:
    scene = next((item for item in scenes if item["id"] == scene_id), None)
    if scene is None:
        raise PilotError(f"scenes.json has no {scene_id}")
    plates = []
    for plate_id in scene.get("flowPlates") or []:
        packet = packets[plate_id]
        source = stills_dir / f"{plate_id}.jpg"
        if sha256_file(source) != packet["sourceHash"]:
            raise PilotError(f"{plate_id} no longer matches the validated hash")
        plates.append({"id": plate_id, "path": source, "focal": packet.get("focal") or {"x": 0.5, "y": 0.5}})
    if len(plates) != 2:
        raise PilotError(f"{scene_id} needs two stills")
    return plates


def countdown_beats(start: float, end: float, hold_end: float) -> list[dict[str, Any]]:
    """Five spoken beats, then keep 1 on screen through the hold so YOKI cannot replace it."""
    span = max(0.2, float(end) - float(start))
    beats = []
    for step, digit in enumerate(COUNT_DIGITS):
        beats.append(
            {
                "digit": digit,
                "start": round(float(start) + span * step / 5, 3),
                "end": round(float(start) + span * (step + 1) / 5, 3),
            }
        )
    beats[-1]["end"] = round(max(beats[-1]["end"], float(hold_end)), 3)
    return beats


def digit_at(beats: list[dict[str, Any]], moment: float) -> str | None:
    for beat in beats:
        if float(beat["start"]) <= moment < float(beat["end"]):
            return str(beat["digit"])
    return None


def _countdown_words(text: str) -> list[str]:
    words = [word.strip(" .") for word in text.replace(".", " ").split() if word.strip(" .")]
    folded = [_fold(word) for word in words]
    if folded == ["besh", "tort", "uch", "ikki", "bir"]:
        return words
    return _fit_caption(text)


def _fit_caption(text: str) -> list[str]:
    """Caption pages: punctuation first, then at most seven words, and two lines on both frames."""
    pages: list[str] = []
    for sentence in _sentences(text):
        current: list[str] = []
        for word in sentence.split():
            trial = " ".join([*current, word])
            if current and (len(current) >= 7 or _caption_too_wide(trial)):
                pages.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            pages.append(" ".join(current))
    return pages or [text.strip()]


def _caption_too_wide(text: str) -> bool:
    land = caption_overflow(text, 1920, 1080, SAFE, {"fontScale": 0.056, "maxLines": 2})
    port = caption_overflow(text, 1080, 1920, SAFE, {"fontScale": 0.035, "maxLines": 2})
    return bool(land["overflow"] or port["overflow"])


def _load_design(episode_dir: Path) -> dict[str, Any]:
    path = episode_dir / "design.json"
    if not path.is_file():
        return {
            "id": "episode-001",
            "labels": LABELS,
            "facts": FACTS,
            "titles": TITLES,
            "kenBurns": "standard",
        }
    doc = json.loads(path.read_text(encoding="utf-8"))
    facts = {}
    for key, value in (doc.get("facts") or {}).items():
        facts[int(key)] = value
    return {
        "id": str(doc.get("id") or episode_dir.name),
        "labels": doc.get("labels") or LABELS,
        "facts": facts or FACTS,
        "titles": doc.get("titles") or TITLES,
        "kenBurns": str(doc.get("kenBurns") or "standard"),
    }


def _render_show(
    scenes: list[dict[str, Any]],
    stills_dir: Path,
    out_dir: Path,
    dest: Path,
    width: int,
    height: int,
    placeholder: bool,
    title: str | None,
    ending: str | None,
    audio: Path,
    duration: float,
    design: dict[str, Any] | None = None,
) -> None:
    design = design or _load_design(dest)
    frames = _prepare_stills(stills_dir, out_dir)
    _link_stills(frames)
    props_path = dest.with_suffix(".props.json")
    silent = dest.with_suffix(".silent.mp4")
    props_path.parent.mkdir(parents=True, exist_ok=True)
    props_path.write_text(
        json.dumps(
            _show_props(scenes, frames, width, height, duration, placeholder, title, ending, design),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repo = Path(__file__).resolve().parents[2]
    command = [
        str(repo / "remotion" / "node_modules" / ".bin" / "remotion"),
        "render",
        "src/pilot-index.ts",
        "PilotShow",
        str(silent),
        f"--props={props_path}",
        "--muted",
        "--concurrency=2",
        "--crf=20",
        "--x264-preset=veryfast",
        "--log=error",
    ]
    log_path = dest.with_suffix(".render.log")
    with log_path.open("w", encoding="utf-8") as handle:
        result = subprocess.run(command, cwd=repo / "remotion", stdout=handle, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        tail = log_path.read_text(encoding="utf-8")[-2000:]
        raise PilotError(f"remotion render failed\n{tail}")
    _mux([silent], audio, dest, duration)


def _prepare_stills(stills_dir: Path, out_dir: Path) -> Path:
    """Lanczos-scale stills that are below 1080p. Larger masters are used as they are."""
    dest = out_dir / "stills-1080"
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(stills_dir.glob("still-*.jpg")):
        info = probe(path)
        video = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), None)
        width = int((video or {}).get("width") or 0)
        height = int((video or {}).get("height") or 0)
        target = dest / path.name
        if width >= 1920 and height >= 1080:
            if target.is_symlink() or target.exists():
                target.unlink()
            target.symlink_to(path.resolve())
            continue
        if target.is_file() and not target.is_symlink() and target.stat().st_mtime >= path.stat().st_mtime:
            continue
        if target.is_symlink():
            target.unlink()
        run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-v",
                "error",
                "-i",
                str(path),
                "-vf",
                "scale=1920:1080:flags=lanczos",
                "-q:v",
                "2",
                str(target),
            ]
        )
    return dest


def _link_stills(stills_dir: Path) -> None:
    link = Path(__file__).resolve().parents[2] / "media" / "pilot-stills"
    target = stills_dir.resolve()
    if link.is_symlink():
        if link.resolve() == target:
            return
        link.unlink()
    elif link.exists():
        raise PilotError(f"{link} exists and is not the stills link")
    link.symlink_to(target, target_is_directory=True)


def _show_props(
    scenes: list[dict[str, Any]],
    stills_dir: Path,
    width: int,
    height: int,
    duration: float,
    placeholder: bool,
    title: str | None,
    ending: str | None,
    design: dict[str, Any] | None = None,
) -> dict[str, Any]:
    labels = (design or {}).get("labels") or LABELS
    facts = (design or {}).get("facts") or FACTS
    titles = (design or {}).get("titles") or TITLES
    focals: dict[str, dict[str, float]] = {}
    for scene in scenes:
        for plate in scene.get("plates") or []:
            focals[str(plate["id"])] = plate.get("focal") or {"x": 0.5, "y": 0.5}
    library = []
    for path in sorted(stills_dir.glob("still-*.jpg")):
        focal = focals.get(path.stem) or {"x": 0.5, "y": 0.5}
        library.append(_plate_prop(path.stem, focal))
    built = []
    for scene in scenes:
        number = scene.get("round")
        if number is None:
            match = re.fullmatch(r"r(\d+)-.+", str(scene["id"]))
            number = int(match.group(1)) if match else None
        fact = facts.get(int(number)) if scene.get("role") == "reveal" and number is not None else None
        built.append(
            {
                "id": scene["id"],
                "role": scene.get("role") or "card",
                "startSec": scene["start"],
                "durationSec": scene["duration"],
                "plates": [
                    _plate_prop(str(plate["id"]), plate.get("focal") or {"x": 0.5, "y": 0.5})
                    for plate in scene.get("plates") or []
                ],
                "labels": [labels.get(str(plate["id"]), str(plate["id"])) for plate in scene.get("plates") or []],
                "round": number,
                "cues": scene.get("cues") or [],
                "countdown": scene.get("countdown"),
                "countdownBeats": scene.get("countdownBeats"),
                "fact": fact,
                "titles": titles.get(str(scene.get("role")), []),
            }
        )
    return {
        "width": width,
        "height": height,
        "durationInFrames": max(1, int(round(duration * FPS))),
        "placeholder": placeholder,
        "title": title or None,
        "ending": ending or None,
        "library": library,
        "scenes": built,
        "kenBurns": (design or {}).get("kenBurns") or "standard",
    }


def _plate_prop(plate_id: str, focal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": plate_id,
        "file": f"pilot-stills/{plate_id}.jpg",
        "focal": {"x": float(focal.get("x", 0.5)), "y": float(focal.get("y", 0.5))},
        "evening": plate_id == "still-hovli",
    }


def _mix_show(
    voice: Path,
    scenes: list[dict[str, Any]],
    out_dir: Path,
    duration: float,
) -> tuple[Path, dict[str, Any]]:
    bed = out_dir / "audio" / "bed.wav"
    _write_bed(bed, duration)
    whooshes = [float(scene["start"]) for scene in scenes[1:]]
    ticks: list[float] = []
    for scene in scenes:
        window = scene.get("countdown")
        if not window:
            continue
        span = max(0.2, float(window["end"]) - float(window["start"]))
        origin = float(scene["start"]) + float(window["start"])
        ticks.extend(origin + span * step / 5 for step in range(5))
    mixed = out_dir / "audio" / "mix.wav"
    _mix_tracks(voice, bed, mixed, duration, whooshes, ticks)
    return mixed, _measure_lufs(mixed)


def _write_bed(dest: Path, duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = dest.with_suffix(".raw.wav")
    tone = (
        "0.08*sin(2*PI*220*t)+0.05*sin(2*PI*277*t)+0.04*sin(2*PI*330*t)"
        "+0.02*sin(2*PI*0.07*t)*sin(2*PI*196*t)"
    )
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
            f"aevalsrc={tone}:s=48000:d={duration:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=pink:sample_rate=48000:duration={duration:.3f}",
            "-filter_complex",
            "[1:a]lowpass=f=280,highpass=f=70,volume=0.15[n];"
            "[0:a][n]amix=inputs=2:duration=first:normalize=0,volume=2,aresample=48000[a]",
            "-map",
            "[a]",
            "-ac",
            "2",
            "-ar",
            str(AUDIO_RATE),
            "-c:a",
            "pcm_s16le",
            str(raw),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(raw),
            "-af",
            "loudnorm=I=-28:TP=-2:LRA=11,aresample=48000",
            "-ac",
            "2",
            "-ar",
            str(AUDIO_RATE),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def _mix_tracks(
    voice: Path,
    bed: Path,
    dest: Path,
    duration: float,
    whooshes: list[float],
    ticks: list[float],
) -> None:
    repo = Path(__file__).resolve().parents[2]
    whoosh = repo / "media" / "library" / "sfx" / "clips" / "whoosh-soft.mp3"
    tick = repo / "media" / "library" / "sfx" / "clips" / "clock-tick-soft.mp3"
    parts = [
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voice][sc]",
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[bedin]",
        "[bedin][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[bed]",
    ]
    labels = ["[voice]", "[bed]"]
    command = ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(voice), "-i", str(bed)]
    next_input = 2
    if whooshes:
        command.extend(["-i", str(whoosh)])
        _place_sfx(parts, labels, next_input, whooshes, "w", 0.12)
        next_input += 1
    if ticks:
        command.extend(["-i", str(tick)])
        _place_sfx(parts, labels, next_input, ticks, "t", 0.16)
    parts.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0,apad,alimiter=limit=0.95[pre]"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = dest.with_suffix(".staged.wav")
    command.extend(
        [
            "-filter_complex",
            ";".join(parts),
            "-map",
            "[pre]",
            "-t",
            f"{duration:.3f}",
            "-ac",
            "2",
            "-ar",
            str(AUDIO_RATE),
            "-c:a",
            "pcm_s16le",
            str(staged),
        ]
    )
    run(command)
    _loudnorm_file(staged, dest, -14.0)


def _place_sfx(
    parts: list[str],
    labels: list[str],
    input_index: int,
    times: list[float],
    tag: str,
    volume: float,
) -> None:
    if len(times) == 1:
        parts.append(f"[{input_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={volume}[{tag}0]")
    else:
        split = "".join(f"[{tag}{index}]" for index in range(len(times)))
        parts.append(
            f"[{input_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={volume},asplit={len(times)}{split}"
        )
    for index, moment in enumerate(times):
        delay = max(0, int(round(moment * 1000)))
        parts.append(f"[{tag}{index}]adelay={delay}|{delay}[{tag}d{index}]")
        labels.append(f"[{tag}d{index}]")


def _loudnorm_file(source: Path, dest: Path, integrated: float) -> None:
    probe = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(source),
            "-af",
            f"loudnorm=I={integrated}:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    match = re.search(r"\{[^{}]*\}", probe.stderr or "")
    if match is None:
        raise PilotError("loudnorm did not report measured levels")
    stats = json.loads(match.group(0))
    measured = (
        f"loudnorm=I={integrated}:TP=-1.5:LRA=11:measured_I={stats['input_i']}:"
        f"measured_TP={stats['input_tp']}:measured_LRA={stats['input_lra']}:"
        f"measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(source),
            "-af",
            measured,
            "-ac",
            "2",
            "-ar",
            str(AUDIO_RATE),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def _measure_lufs(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=framelog=verbose", "-f", "null", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    summary = (result.stderr or "").split("Summary:")[-1]
    found = re.search(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", summary)
    return {
        "integratedLufs": float(found.group(1)) if found else None,
        "targetLufs": -14.0,
        "bedTargetLufs": -28.0,
        "method": "ffmpeg-ebur128",
        "note": "Local sine-and-noise bed at about -28 LUFS, ducked under the voice, then the mix is loudnormed to -14.",
    }


def _render_short(
    plan: dict[str, Any],
    aligned: list[dict[str, Any]],
    narration: Path,
    out_dir: Path,
    placeholder: bool,
    packets: dict[str, dict[str, Any]],
    stills_dir: Path,
    design: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wanted = set(plan["scenes"])
    chosen = [scene for scene in aligned if scene["id"] in wanted]
    if [scene["id"] for scene in chosen] != plan["scenes"]:
        raise PilotError(f"{plan['id']} scenes do not match the aligned episode")
    for asset in plan["assets"]:
        packet = packets[asset["packetId"]]
        if packet.get("sourceHash") != _hash_of_scene_plate(chosen, asset["packetId"]):
            raise PilotError(f"{plan['id']} would regenerate {asset['packetId']}")
        asset["sourceHash"] = packet["sourceHash"]
        asset["regenerated"] = False
    final = out_dir / "assemble" / f"{plan['id']}.mp4"
    audio = out_dir / "audio" / f"{plan['id']}.wav"
    if plan.get("inSec") is not None and plan.get("outSec") is not None:
        local, pieces, cursor = _pack_short(chosen, float(plan["inSec"]), float(plan["outSec"]))
        _concat_audio(narration, audio, pieces, cursor)
    else:
        offset = chosen[0]["start"]
        local, cursor = _clip_scenes(chosen, offset, chosen[-1]["end"])
        _slice_audio(narration, audio, offset, cursor)
    _render_show(
        local,
        stills_dir,
        out_dir,
        final,
        1080,
        1920,
        placeholder,
        str(plan.get("hook") or ""),
        str(plan.get("ending") or ""),
        audio,
        cursor,
        design,
    )
    manifest = _short_manifest(out_dir, plan["id"], local, cursor)
    timing = _foundation_timing(local, sha256_file(audio))
    return {"id": plan["id"], "path": final, "durationSec": cursor, "manifest": manifest, "timing": timing}


def _speech_spans(scenes: list[dict[str, Any]], start: float, end: float) -> list[tuple[float, float]]:
    spans = []
    for scene in scenes:
        origin = float(scene["start"])
        for cue in scene.get("cues") or []:
            opening = origin + float(cue["start"])
            closing = origin + float(cue["end"])
            if closing > start and opening < end:
                spans.append((max(opening, start), min(closing, end)))
        window = scene.get("countdown")
        if window:
            opening = origin + float(window["start"])
            closing = origin + float(window["end"])
            if closing > start and opening < end:
                spans.append((max(opening, start), min(closing, end)))
    spans.sort()
    merged: list[tuple[float, float]] = []
    for opening, closing in spans:
        if merged and opening <= merged[-1][1] + 0.02:
            merged[-1] = (merged[-1][0], max(merged[-1][1], closing))
        else:
            merged.append((opening, closing))
    return merged


def _shrink_gaps(lengths: list[float], excess: float, floor: float) -> list[float]:
    """Shorten the longest pauses first. Speech is not in this list."""
    shrunk = [float(item) for item in lengths]
    for limit in (floor, 0.0):
        while excess > 0.0005:
            index = max(range(len(shrunk)), key=lambda item: shrunk[item] - limit)
            room = shrunk[index] - limit
            if room <= 0.0005:
                break
            cut = min(room, excess)
            shrunk[index] -= cut
            excess -= cut
        if excess <= 0.0005:
            break
    return shrunk


def _time_map(pieces: list[tuple[float, float]]) -> tuple[Any, float]:
    ranges = []
    dest = 0.0
    for start, end in pieces:
        ranges.append((start, end, dest))
        dest += end - start

    def map_time(moment: float) -> float:
        if not ranges or moment <= ranges[0][0]:
            return 0.0
        for start, end, origin in ranges:
            if moment < start:
                return origin
            if moment <= end:
                return origin + (moment - start)
        return dest

    return map_time, dest


def _pack_short(
    scenes: list[dict[str, Any]],
    in_sec: float,
    out_sec: float,
) -> tuple[list[dict[str, Any]], list[tuple[float, float]], float]:
    """Open 0.3 s before the question, then drop pauses until the short is at most 55 s."""
    lead_start = max(0.0, in_sec - SHORT_LEAD_SEC)
    speech = _speech_spans(scenes, in_sec, out_sec)
    if not speech:
        raise PilotError("short window has no recorded speech")
    gaps: list[tuple[float, float]] = []
    cursor = in_sec
    for opening, closing in speech:
        if opening > cursor + 0.001:
            gaps.append((cursor, opening))
        cursor = max(cursor, closing)
    if out_sec > cursor + 0.001:
        gaps.append((cursor, out_sec))
    lengths = [closing - opening for opening, closing in gaps]
    excess = (out_sec - lead_start) - SHORT_TARGET_SEC
    if excess > 0 and lengths:
        lengths = _shrink_gaps(lengths, excess, SHORT_MIN_GAP_SEC)
    pieces: list[tuple[float, float]] = []
    if in_sec - lead_start > 0.001:
        pieces.append((lead_start, in_sec))
    gap_index = 0
    for opening, closing in speech:
        if gap_index < len(gaps) and abs(gaps[gap_index][1] - opening) < 0.02:
            keep = lengths[gap_index]
            if keep > 0.001:
                pieces.append((gaps[gap_index][0], gaps[gap_index][0] + keep))
            gap_index += 1
        pieces.append((opening, closing))
    if gap_index < len(gaps):
        keep = lengths[gap_index]
        if keep > 0.001:
            pieces.append((gaps[gap_index][0], gaps[gap_index][0] + keep))
    map_time, packed = _time_map(pieces)
    frames = max(1, int(round(packed * FPS)))
    duration = frames / FPS
    local = []
    for index, scene in enumerate(scenes):
        src_start = float(scene["start"])
        src_end = min(float(scene["end"]), out_sec)
        if index == 0:
            src_start = min(src_start, lead_start)
        src_start = max(src_start, lead_start)
        if src_end - src_start < (1 / FPS):
            continue
        dest_start = map_time(src_start)
        dest_end = map_time(src_end)
        scene_duration = dest_end - dest_start
        if scene_duration < (1 / FPS):
            continue
        cues = []
        for cue in scene.get("cues") or []:
            abs_start = float(scene["start"]) + float(cue["start"])
            abs_end = float(scene["start"]) + float(cue["end"])
            if abs_end <= in_sec + 0.02 or abs_start >= out_sec - 0.02:
                continue
            cues.append(
                {
                    "text": cue["text"],
                    "start": round(max(0.0, map_time(abs_start) - dest_start), 3),
                    "end": round(min(scene_duration, map_time(abs_end) - dest_start), 3),
                }
            )
        countdown = None
        if scene.get("countdown"):
            abs_start = float(scene["start"]) + float(scene["countdown"]["start"])
            abs_end = float(scene["start"]) + float(scene["countdown"]["end"])
            if abs_end > in_sec and abs_start < out_sec:
                countdown = {
                    "start": round(max(0.0, map_time(abs_start) - dest_start), 3),
                    "end": round(min(scene_duration, map_time(abs_end) - dest_start), 3),
                }
        beats = None
        if countdown:
            hold_end = cues[0]["start"] if cues else countdown["end"]
            beats = countdown_beats(countdown["start"], countdown["end"], hold_end)
        whole = abs(src_start - float(scene["start"])) < 0.02 and abs(src_end - float(scene["end"])) < 0.02
        if whole:
            text = scene["text"]
        else:
            parts = [str(cue["text"]) for cue in cues]
            if countdown:
                parts.insert(0, "Besh. Toʻrt. Uch. Ikki. Bir.")
            text = " ".join(parts) or scene["text"]
        item = dict(scene)
        item["start"] = round(dest_start, 3)
        item["duration"] = round(scene_duration, 3)
        item["end"] = round(dest_end, 3)
        item["cues"] = cues
        item["countdown"] = countdown
        item["countdownBeats"] = beats
        item["text"] = text
        local.append(item)
    if not local:
        raise PilotError("short window does not overlap the aligned scenes")
    drift = duration - local[-1]["end"]
    local[-1]["duration"] = round(local[-1]["duration"] + drift, 3)
    local[-1]["end"] = round(local[-1]["start"] + local[-1]["duration"], 3)
    return local, pieces, duration


def _concat_audio(source: Path, dest: Path, pieces: list[tuple[float, float]], duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    filters = []
    labels = []
    for index, (start, end) in enumerate(pieces):
        filters.append(
            f"[0:a]atrim=start={start:.4f}:end={end:.4f},asetpts=PTS-STARTPTS[a{index}]"
        )
        labels.append(f"[a{index}]")
    filters.append(
        "".join(labels)
        + f"concat=n={len(labels)}:v=0:a=1,aresample={AUDIO_RATE},apad,atrim=0:{duration:.4f}[a]"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(source),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[a]",
            "-ac",
            "2",
            "-ar",
            str(AUDIO_RATE),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def _clip_scenes(
    scenes: list[dict[str, Any]],
    start: float,
    end: float,
) -> tuple[list[dict[str, Any]], float]:
    """Keep the recorded question-through-fact window. Scene text outside that window is dropped."""
    local = []
    cursor = 0.0
    for scene in scenes:
        opening = max(float(scene["start"]), start)
        closing = min(float(scene["end"]), end)
        if closing - opening < (1 / FPS):
            continue
        delta = opening - float(scene["start"])
        duration = round((closing - opening) * FPS) / FPS
        cues = []
        for cue in scene.get("cues") or []:
            cue_start = float(cue["start"]) - delta
            cue_end = float(cue["end"]) - delta
            if cue_end <= 0.02 or cue_start >= duration - 0.02:
                continue
            cues.append(
                {
                    "text": cue["text"],
                    "start": round(max(0.0, cue_start), 3),
                    "end": round(min(duration, cue_end), 3),
                }
            )
        countdown = None
        if scene.get("countdown"):
            cue_start = float(scene["countdown"]["start"]) - delta
            cue_end = float(scene["countdown"]["end"]) - delta
            if cue_end > 0.05 and cue_start < duration:
                countdown = {"start": round(max(0.0, cue_start), 3), "end": round(min(duration, cue_end), 3)}
        whole = abs(opening - float(scene["start"])) < 0.02 and abs(closing - float(scene["end"])) < 0.02
        if whole:
            text = scene["text"]
        else:
            parts = [str(cue["text"]) for cue in cues]
            if countdown:
                parts.insert(0, "Besh. Toʻrt. Uch. Ikki. Bir.")
            text = " ".join(parts) or scene["text"]
        item = dict(scene)
        item["start"] = round(cursor, 3)
        item["duration"] = duration
        item["end"] = round(cursor + duration, 3)
        item["cues"] = cues
        item["countdown"] = countdown
        item["text"] = text
        local.append(item)
        cursor += duration
    if not local:
        raise PilotError("short window does not overlap the aligned scenes")
    return local, cursor


def _hash_of_scene_plate(scenes: list[dict[str, Any]], plate_id: str) -> str | None:
    for scene in scenes:
        for plate in scene["plates"]:
            if plate["id"] == plate_id:
                return sha256_file(plate["path"])
    return None


def _render_scene(
    scene: dict[str, Any],
    dest: Path,
    width: int,
    height: int,
    placeholder: bool,
    title: str | None,
    ending: str | None = None,
    ending_at: float = 0.0,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, _frames(scene["duration"]))
    work = dest.parent / "captions" / dest.stem
    work.mkdir(parents=True, exist_ok=True)
    font = max(28, int(height * 0.028))
    draws = _caption_filters(scene["cues"], width, font, work)
    if scene["role"] == "countdown":
        draws.extend(_count_filters(scene["cues"], height, work))
    if placeholder:
        label = work / "placeholder.txt"
        label.write_text(PLACEHOLDER_LABEL, encoding="utf-8")
        label_size = max(22, min(42, int(width / 28)))
        draws.append(
            f"drawtext=fontfile={FONT}:textfile={label}:fontsize={label_size}:fontcolor=white:"
            f"box=1:boxcolor=0x000000@0.78:boxborderw=14:x=(w-text_w)/2:y=h*0.04"
        )
    if title:
        title_file = work / "hook.txt"
        title_file.write_text(wrap_text(title, width, font), encoding="utf-8")
        draws.append(
            f"drawtext=fontfile={FONT}:textfile={title_file}:fontsize={font}:fontcolor=white:"
            f"box=1:boxcolor=0x102033@0.72:boxborderw=12:x=(w-text_w)/2:y=h*0.12"
        )
    if ending:
        ending_file = work / "ending.txt"
        ending_file.write_text(wrap_text(ending, width, font), encoding="utf-8")
        draws.append(
            f"drawtext=fontfile={FONT}:textfile={ending_file}:fontsize={font}:fontcolor=white:"
            f"box=1:boxcolor=0x102033@0.72:boxborderw=12:x=(w-text_w)/2:y=h*0.62:"
            f"enable='gte(t\\,{ending_at:.3f})'"
        )
    draw = ",".join(draws)
    if scene["plates"]:
        _render_plates(scene["plates"], dest, width, height, frames, draw, portrait=height > width)
    else:
        _render_card(dest, width, height, frames, draw)


def _render_plates(
    plates: list[dict[str, Any]],
    dest: Path,
    width: int,
    height: int,
    frames: int,
    draw: str,
    portrait: bool,
) -> None:
    if len(plates) != 2:
        raise PilotError("a choice scene needs two stills")
    if portrait:
        tile_w, tile_h = width, height // 2
        stack = "vstack=inputs=2"
    else:
        tile_w, tile_h = width // 2, height
        stack = "hstack=inputs=2"
    filters = []
    for index, plate in enumerate(plates):
        focal = plate["focal"]
        filters.append(
            f"[{index}:v]scale={tile_w}:{tile_h}:force_original_aspect_ratio=increase,"
            f"crop={tile_w}:{tile_h}:(iw-{tile_w})*{float(focal.get('x', 0.5)):.4f}:"
            f"(ih-{tile_h})*{float(focal.get('y', 0.5)):.4f},setsar=1[p{index}]"
        )
    filters.append(f"[p0][p1]{stack}[base]")
    if draw:
        filters.append(f"[base]{draw}[vout]")
        last = "[vout]"
    else:
        last = "[base]"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-v", "error"]
    for plate in plates:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", str(plate["path"])]
    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        last,
        "-frames:v",
        str(frames),
        "-r",
        str(FPS),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    run(cmd)


def _render_card(dest: Path, width: int, height: int, frames: int, draw: str) -> None:
    graph = f"format=yuv420p,{draw}" if draw else "format=yuv420p"
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
            f"color=c=0x1e2a3a:s={width}x{height}:r={FPS}",
            "-vf",
            graph,
            "-frames:v",
            str(frames),
            "-r",
            str(FPS),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ]
    )


def _caption_filters(cues: list[dict[str, Any]], width: int, font: int, work: Path) -> list[str]:
    filters = []
    for index, cue in enumerate(cues):
        path = work / f"cue-{index}.txt"
        path.write_text(wrap_text(str(cue["text"]), width, font), encoding="utf-8")
        filters.append(
            f"drawtext=fontfile={FONT}:textfile={path}:fontsize={font}:fontcolor=white:"
            f"line_spacing=8:box=1:boxcolor=0x000000@0.55:boxborderw=12:"
            f"x=(w-text_w)/2:y=h*0.78:enable='between(t\\,{float(cue['start']):.3f}\\,{float(cue['end']):.3f})'"
        )
    return filters


def _count_filters(cues: list[dict[str, Any]], height: int, work: Path) -> list[str]:
    filters = []
    size = max(72, int(height * 0.16))
    for index, cue in enumerate(cues):
        token = _fold(str(cue["text"])).strip(" .")
        if token not in COUNTS:
            continue
        path = work / f"count-{index}.txt"
        path.write_text(str(cue["text"]), encoding="utf-8")
        filters.append(
            f"drawtext=fontfile={FONT}:textfile={path}:fontsize={size}:fontcolor=white:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t\\,{float(cue['start']):.3f}\\,{float(cue['end']):.3f})'"
        )
    return filters


def _mux(videos: list[Path], narration: Path, dest: Path, duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    concat = dest.with_suffix(".concat.txt")
    lines = [f"file '{path.resolve()}'" for path in videos]
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    video = dest.with_suffix(".video.mp4")
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c",
            "copy",
            str(video),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(video),
            "-i",
            str(narration),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-af",
            "apad",
            "-c:a",
            "aac",
            "-ar",
            str(AUDIO_RATE),
            "-b:a",
            "160k",
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )


def _canonicalize(source: Path, out_dir: Path) -> dict[str, Any]:
    asset = {
        "id": "narration-main",
        "kind": "narration",
        "source": {"provider": "import", "role": "voice", "path": source.name},
        "timing": "timing.json",
        "rights": {"status": "imported", "owner": "local"},
        "provenance": {"tool": "ffmpeg", "origin": "local-file"},
    }
    return prepare_one(asset, source.parent, out_dir)


def _narration_file(narration_dir: Path, timings: dict[str, Any], out_dir: Path, duration: float) -> Path:
    wav = narration_dir / "narration.wav"
    if wav.is_file():
        return wav
    if not timings.get("placeholder"):
        raise PilotError("narration.wav is missing")
    dest = out_dir / "placeholder-narration.wav"
    _synthesize(timings, dest, duration)
    return dest


def _synthesize(timings: dict[str, Any], dest: Path, duration: float) -> None:
    """24 kHz mono sine during speech chunks. Countdown holds stay silent. Not a voice."""
    rate = 24000
    total = max(1, int(round(duration * rate)))
    samples = array.array("h", [0]) * total
    amplitude = int(0.12 * 32767)
    for chunk in timings["chunks"]:
        start = max(0, int(float(chunk["start"]) * rate))
        end = min(total, int(float(chunk["end"]) * rate))
        for index in range(start, end):
            samples[index] = int(amplitude * math.sin(2 * math.pi * 440 * index / rate))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())


def _slice_audio(source: Path, dest: Path, start: float, duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def _manifest(
    episode_dir: Path,
    out_dir: Path,
    scenes: list[dict[str, Any]],
    canonical: dict[str, Any],
    duration: float,
    landscape_only: bool,
    episode_id: str = "episode-001",
) -> dict[str, Any]:
    del episode_dir
    doc = _base_manifest(episode_id, scenes, duration, portrait=not landscape_only, landscape=True)
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    timing_path = out_dir / "timing" / "narration-main.json"
    timing_path.parent.mkdir(parents=True, exist_ok=True)
    timing_path.write_text(json.dumps(_raw_timing(scenes), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wav_name = Path(canonical["canonicalPath"]).name
    # The canonical file lives under out/canonical. Point the manifest at it.
    doc["assets"][0]["source"]["path"] = str(Path("canonical") / wav_name)
    doc["assets"][0]["timing"] = "timing/narration-main.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return validate_manifest(doc, out_dir)


def _short_manifest(out_dir: Path, short_id: str, scenes: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    doc = _base_manifest(short_id, scenes, duration, portrait=True, landscape=False)
    root = out_dir / short_id
    root.mkdir(parents=True, exist_ok=True)
    # validate_manifest requires the narration file inside this directory.
    return doc


def _base_manifest(
    episode_id: str,
    scenes: list[dict[str, Any]],
    duration: float,
    portrait: bool,
    landscape: bool,
) -> dict[str, Any]:
    formats = []
    if landscape:
        formats.append({"id": "landscape", "width": 1920, "height": 1080, "fps": FPS, "compositionId": "PilotLandscape"})
    if portrait:
        formats.append({"id": "portrait", "width": 1080, "height": 1920, "fps": FPS, "compositionId": "PilotPortrait"})
    built = []
    for scene in scenes:
        built.append(
            {
                "id": scene["id"],
                "durationSec": scene["duration"],
                "assetIds": ["narration-main", "card-title"],
                "narration": {"assetId": "narration-main", "inSec": scene["start"], "outSec": scene["end"]},
                "focal": {"x": 0.5, "y": 0.5},
                "safe": SAFE,
                "text": {"uz-Latn": scene["text"], "en": "Pilot scene.", "nl": "Pilotscène."},
                "caption": {
                    "fontScale": 0.035 if portrait and not landscape else 0.056,
                    "maxLines": 2,
                },
                "captionCues": scene["cues"],
                "plates": [
                    {"id": str(plate["id"]), "sha256": sha256_file(Path(plate["path"]))}
                    for plate in scene.get("plates") or []
                    if plate.get("path")
                ],
                "qa": {
                    "allowSilence": False,
                    "silenceGapSec": 2.5 if scene.get("allowSilence") else 1.0,
                },
            }
        )
    transitions = [{"after": scene["id"], "type": "cut", "durationSec": 0} for scene in scenes[:-1]]
    total = sum(float(scene["durationSec"]) for scene in built)
    return {
        "schemaVersion": 1,
        "id": episode_id,
        "language": "uz-Latn",
        "supportedLanguages": ["uz-Latn", "en", "nl"],
        "fps": FPS,
        "episodeDurationSec": total,
        "formats": formats,
        "assets": [
            {
                "id": "narration-main",
                "kind": "narration",
                "source": {"provider": "import", "role": "voice", "path": "canonical/narration-main.wav"},
                "timing": "timing/narration-main.json",
                "rights": {"status": "imported", "owner": "local"},
                "provenance": {"tool": "ffmpeg", "origin": "local-file"},
            },
            {
                "id": "card-title",
                "kind": "visual",
                "source": {"provider": "import", "role": "still", "flowPacket": "still-palov"},
                "rights": {"status": "imported", "owner": "local"},
                "provenance": {"tool": "Flow", "origin": "manual-export"},
            },
        ],
        "scenes": built,
        "transitions": transitions,
        "qa": {
            "allowBlack": False,
            "allowSilence": False,
            "allowClipping": False,
            "allowCaptionOverflow": False,
        },
    }


def _foundation_timing(scenes: list[dict[str, Any]], audio_hash: str) -> dict[str, Any]:
    raw = _raw_timing(scenes)
    raw["audioContentHash"] = audio_hash
    return resolve_timing(raw, audio_hash, {scene["id"] for scene in scenes})


def _raw_timing(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "assetId": "narration-main",
        "language": "uz-Latn",
        "lines": [{"sceneId": scene["id"], "text": scene["text"], "state": "unaligned"} for scene in scenes],
    }


def _cost(packets: list[dict[str, Any]], placeholder: bool, timings: dict[str, Any]) -> dict[str, Any]:
    return {
        "flowCredits": 0,
        "model": "Nano Banana 2.1",
        "origin": "uzbekajon.com",
        "stills": len(packets),
        "tts": "local-placeholder-sine" if placeholder else "local-voicestudio",
        "ttsCredits": 0,
        "engine": None if placeholder else timings.get("engine"),
        "voice": None if placeholder else timings.get("voice"),
        "label": (
            "0 Flow credits. Placeholder sine, not a VoiceStudio charge."
            if placeholder
            else "0 Flow credits. Narration is the local VoiceStudio take. This command did not call a provider."
        ),
    }


def _read_timings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PilotError(f"missing timings {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc.get("sections"), list) or not doc["sections"]:
        raise PilotError("timings.json needs sections with start and end")
    if not isinstance(doc.get("chunks"), list) or not doc["chunks"]:
        raise PilotError("timings.json needs chunks with start, end, and text")
    for item in doc["sections"]:
        for key in ("start", "end"):
            if key not in item:
                raise PilotError("a timings section is missing start or end")
        if float(item["end"]) <= float(item["start"]):
            raise PilotError(f"timings section {item.get('section') or item.get('id')} has a bad range")
        if "text" not in item and "section" not in item:
            raise PilotError("a timings section needs text or a section name")
    for item in doc["chunks"]:
        for key in ("start", "end", "text"):
            if key not in item:
                raise PilotError(f"timings chunk {item.get('id')} is missing {key}")
        if float(item["end"]) <= float(item["start"]):
            raise PilotError(f"timings chunk {item.get('id')} has a bad range")
        if "sectionId" not in item and "section" not in item:
            raise PilotError(f"timings chunk {item.get('id')} has no section")
    return doc


def _checksums(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            found[parts[-1]] = parts[0]
    return found


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
    return parts or [text.strip()]


def _frames(seconds: float) -> int:
    return int(round(seconds * FPS))


def _fold(text: str) -> str:
    return text.lower().replace("\u02bb", "").replace("\u2018", "").replace("\u2019", "").replace("\u02bc", "")


def _probe_duration(path: Path) -> float:
    info = probe(path)
    duration = duration_seconds(info)
    if duration is None:
        raise PilotError(f"{path} has no duration")
    return duration
