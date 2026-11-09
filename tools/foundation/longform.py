"""Plan a long episode and assemble a landscape export. Planning stops before any manual step."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.foundation.cache import decide, read_sidecar, scene_job, write_sidecar
from tools.foundation.flow import FlowError, plan_packet, read_packet
from tools.foundation.hashutil import parameter_hash, sha256_file
from tools.foundation.manifest import ManifestError, load_manifest, timeline
from tools.foundation.narration import prepare_narration
from tools.foundation.pictures import place_video, render_card
from tools.foundation.qa import check_episode
from tools.foundation.render import RenderFailure, _assemble, _bind_timing
from tools.foundation.timing import line_for_scene
from tools.foundation.voicestudio import VoiceStudioBlocked, load_voice_config, voice_for

MANUAL_STEPS = [
    "Review the outline, script, and storyboard. This command does not render them.",
    "For each Flow scene, generate the clip yourself in Flow and keep the receipt. This runner does not open Flow.",
    "Move the packet to awaiting import only after that file exists, then import and approve it.",
    "Import the narration file. VoiceStudio stays fail-closed until the Mac runner is approved.",
    "Assemble the 16:9 export only after those approvals.",
]


def plan_long(
    topic: str,
    language: str,
    duration_sec: float,
    audience: str,
    out_dir: Path,
) -> dict[str, Any]:
    if language not in {"uz-Latn", "en", "nl"}:
        raise ManifestError(f"language must be uz-Latn, en, or nl, got {language}")
    if duration_sec < 12 or duration_sec > 60:
        raise ManifestError("this planner writes a 12 to 60 second skeleton, not a pilot episode")
    if not topic.strip() or not audience.strip():
        raise ManifestError("topic and audience are required")
    out_dir.mkdir(parents=True, exist_ok=True)
    body = round(float(duration_sec) - 8, 3)
    scenes = [
        {"id": "scene-open", "durationSec": 4, "classification": "remotion", "role": "hook"},
        {"id": "scene-body", "durationSec": body, "classification": "flow", "role": "picture"},
        {"id": "scene-close", "durationSec": 4, "classification": "remotion", "role": "ending"},
    ]
    voice = load_voice_config()
    uzbek = voice["languages"]["uz-Latn"]
    packet = plan_packet(
        "packet-body",
        "scene-body",
        f"Manual Flow still for {topic}. Audience: {audience}. No generation is requested here.",
        body,
        "flow-manual-placeholder",
        ["card-title"],
    )
    narration = {
        "door": "import",
        "voicestudio": "fail-closed",
        "uzLatnRequest": {
            "engine": uzbek["engine"],
            "model": uzbek["model"],
            "voice": uzbek["voice"],
        },
        "note": "Do not call VoiceStudio. Import a file the rights owner already has.",
    }
    try:
        voice_for(language)
        narration["languageConfigured"] = True
    except VoiceStudioBlocked:
        narration["languageConfigured"] = False
        narration["note"] = (
            f"{language} has no VoiceStudio voice configured, and OmniVoice is not a fallback. Import audio."
        )
    doc = {
        "topic": topic.strip(),
        "language": language,
        "audience": audience.strip(),
        "durationSec": float(duration_sec),
        "stopped": True,
        "manualSteps": MANUAL_STEPS,
        "narration": narration,
        "scenes": scenes,
        "flowPacket": packet,
        "candidateShorts": [],
    }
    (out_dir / "outline.json").write_text(
        json.dumps({"topic": doc["topic"], "audience": doc["audience"], "beats": [item["id"] for item in scenes]}, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "script.md").write_text(
        f"# {topic.strip()}\n\nLanguage: {language}. Audience: {audience.strip()}.\n\n"
        "This is a skeleton. A person writes the spoken words. It is not the pilot and not narration audio.\n",
        encoding="utf-8",
    )
    (out_dir / "scenes.json").write_text(json.dumps(scenes, indent=2) + "\n", encoding="utf-8")
    (out_dir / "storyboard.json").write_text(
        json.dumps(
            [{"sceneId": item["id"], "classification": item["classification"], "onScreen": "to be written"} for item in scenes],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "classification.json").write_text(
        json.dumps({item["id"]: item["classification"] for item in scenes}, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "packet-body.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "manual-steps.json").write_text(json.dumps(MANUAL_STEPS, indent=2) + "\n", encoding="utf-8")
    (out_dir / "plan.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc


def run_longform(manifest_path: Path, out_dir: Path, packet_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    packet = read_packet(packet_path)
    if packet.get("status") != "approved":
        raise FlowError(f"landscape assembly is waiting for an approved Flow packet, status is {packet.get('status')}")
    source = Path(packet["cachedPath"])
    if not source.is_file() or sha256_file(source) != packet["sourceHash"]:
        raise FlowError("approved Flow packet does not match the cached file")
    out_dir.mkdir(parents=True, exist_ok=True)
    narration_records = prepare_narration(manifest, manifest_path.parent, out_dir)
    narration = narration_records[0]
    timing = _bind_timing(manifest, manifest_path.parent, narration, out_dir)
    placed = timeline(manifest)
    scene_rows: list[dict[str, Any]] = []
    for fmt in manifest["formats"]:
        for scene, row in zip(manifest["scenes"], placed):
            line = line_for_scene(timing, scene["id"])
            if line is None:
                raise RenderFailure(f"scene {scene['id']} has no timing line", [])
            mode = (scene.get("picture") or {}).get("mode")
            if mode not in {"card", "flow-master"}:
                raise RenderFailure(f"scene {scene['id']} needs a card or flow-master picture", [])
            visual_hash = packet["sourceHash"] if mode == "flow-master" else parameter_hash(scene["picture"])
            output = out_dir / "scenes" / fmt["id"] / f"{scene['id']}.mp4"
            job = scene_job(manifest, scene, fmt, row, line, narration["canonicalContentHash"], visual_hash, output)
            sidecar_path = out_dir / "cache" / "scenes" / fmt["id"] / f"{scene['id']}.json"
            action = "skipped" if decide(job, read_sidecar(sidecar_path), output) == "skip" else "render"
            if action != "skipped":
                _render_picture(scene, fmt, source, output, float(row["durationSec"]), manifest["language"])
                write_sidecar(
                    sidecar_path,
                    {
                        "sceneId": scene["id"],
                        "formatId": fmt["id"],
                        "parameterHash": job["parameterHash"],
                        "dependencies": job["dependencies"],
                        "output": str(output),
                        "outputContentHash": sha256_file(output),
                        "pictureMode": mode,
                        "flowRegenerated": False,
                    },
                )
                action = "rendered"
            scene_rows.append(
                {
                    "sceneId": scene["id"],
                    "formatId": fmt["id"],
                    "action": action,
                    "parameterHash": job["parameterHash"],
                    "dependencies": job["dependencies"],
                    "output": str(output),
                    "outputContentHash": sha256_file(output),
                    "classification": scene.get("classification"),
                    "pictureMode": mode,
                    "flowSourceHash": packet["sourceHash"] if mode == "flow-master" else None,
                    "flowRegenerated": False,
                }
            )
    assembled = _assemble(manifest, out_dir, narration, scene_rows)
    qa = check_episode(manifest, assembled["paths"], {narration["id"]: timing})
    candidates = [
        {"sceneId": scene["id"], "startSec": row["startSec"], "durationSec": row["durationSec"]}
        for scene, row in zip(manifest["scenes"], placed)
        if scene.get("standaloneCandidate") is True
    ]
    (out_dir / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "episodeId": manifest["id"],
        "kind": "longform",
        "offline": True,
        "language": manifest["language"],
        "flow": {
            "packetId": packet["id"],
            "status": packet["status"],
            "sourceHash": packet["sourceHash"],
            "cacheAction": packet.get("cacheAction"),
            "creditEstimate": packet.get("creditEstimate"),
            "regenerated": False,
        },
        "narration": narration,
        "scenes": scene_rows,
        "assembled": assembled["records"],
        "candidateShorts": candidates,
        "qaPass": qa["pass"],
        "manual": ["Flow export", "Flow approval", "narration import"],
        "automated": ["packet validation", "scene cards", "ffmpeg assembly", "QA"],
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not qa["pass"]:
        failed = [item["id"] for item in qa["checks"] if not item["pass"]]
        raise RenderFailure(f"QA failed: {', '.join(failed)}", [])
    return report


def _render_picture(
    scene: dict[str, Any],
    fmt: dict[str, Any],
    source: Path,
    output: Path,
    duration: float,
    language: str,
) -> None:
    mode = scene["picture"]["mode"]
    if mode == "card":
        render_card(output, int(fmt["width"]), int(fmt["height"]), duration, scene["text"][language])
        return
    if mode == "flow-master":
        place_video(source, output, int(fmt["width"]), int(fmt["height"]), duration, int(fmt["fps"]))
        return
    raise RenderFailure(f"scene {scene['id']} picture mode {mode} is not rendered here", [])
