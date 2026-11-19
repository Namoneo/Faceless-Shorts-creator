"""Cut standalone portrait Shorts from an assembled episode. Assets are reused by hash."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.foundation.cache import decide, read_sidecar, scene_job, write_sidecar
from tools.foundation.ffmpeg import run
from tools.foundation.flow import FlowError, derive_reframe, read_packet
from tools.foundation.hashutil import parameter_hash, sha256_file
from tools.foundation.manifest import ManifestError, load_manifest, timeline, validate_manifest
from tools.foundation.narration import prepare_one
from tools.foundation.pictures import DESIGNED_LAYOUT, NATIVE_CARD, render_card
from tools.foundation.qa import check_episode
from tools.foundation.render import RenderFailure, _assemble
from tools.foundation.timing import line_for_scene, resolve_timing

PORTRAIT = {"id": "portrait", "width": 1080, "height": 1920, "fps": 30, "compositionId": "ffmpeg-card"}


def run_repurpose(
    manifest_path: Path,
    selection_path: Path,
    episode_out: Path,
    packet_path: Path,
) -> dict[str, Any]:
    episode = load_manifest(manifest_path)
    packet = read_packet(packet_path)
    if packet.get("status") != "approved":
        raise FlowError("repurpose needs the approved Flow master and does not generate a new one")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    shorts = selection.get("shorts")
    if not isinstance(shorts, list) or len(shorts) < 1:
        raise ManifestError("selection needs at least one short")
    placed = {row["id"]: row for row in timeline(episode)}
    episode_narration = next(item for item in episode["assets"] if item["kind"] == "narration")
    canonical = episode_out / "canonical" / f"{episode_narration['id']}.wav"
    if not canonical.is_file():
        raise ManifestError("episode narration has not been imported yet")
    canonical_hash = sha256_file(canonical)
    reports = []
    for spec in shorts:
        reports.append(
            _one_short(episode, spec, episode_out, packet, placed, canonical, canonical_hash, manifest_path.parent)
        )
    summary = {"episodeId": episode["id"], "shorts": reports, "qaPass": all(item["qaPass"] for item in reports)}
    target = episode_out / "shorts" / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not summary["qaPass"]:
        raise RenderFailure("one or more repurposed shorts failed QA", [])
    return summary


def _one_short(
    episode: dict[str, Any],
    spec: dict[str, Any],
    episode_out: Path,
    packet: dict[str, Any],
    placed: dict[str, dict[str, Any]],
    canonical: Path,
    canonical_hash: str,
    episode_dir: Path,
) -> dict[str, Any]:
    if spec.get("standsAlone") is not True:
        raise ManifestError(f"short {spec.get('id')} must stand alone")
    moment = next(scene for scene in episode["scenes"] if scene["id"] == spec["momentSceneId"])
    moment_row = placed[moment["id"]]
    hook = spec["hook"]
    ending = spec["ending"]
    pieces = [
        ("hook", float(hook["durationSec"]), hook["text"], "card"),
        ("moment", float(moment_row["durationSec"]), moment["text"], moment["picture"]["mode"]),
        ("ending", float(ending["durationSec"]), ending["text"], "card"),
    ]
    manifest = _short_manifest(episode, spec, pieces)
    validate_manifest(manifest, None)
    short_out = episode_out / "shorts" / spec["id"]
    short_out.mkdir(parents=True, exist_ok=True)
    hook_hash = None
    hook_audio = None
    if spec.get("hookAudio"):
        asset = {
            "id": f"{spec['id']}-hook",
            "kind": "narration",
            "source": {"provider": "import", "role": "hook-tone", "path": spec["hookAudio"]},
            "rights": {"status": "synthetic-fixture", "owner": "local-test"},
            "provenance": {"tool": "ffmpeg-lavfi", "origin": "synthetic stand-in, not TTS"},
        }
        hook_record = prepare_one(asset, episode_dir, short_out)
        hook_audio = Path(hook_record["canonicalPath"])
        hook_hash = hook_record["originalContentHash"]
    scene_rows = []
    cursor = 0.0
    audio_parts: list[Path] = []
    language = episode["language"]
    for name, duration, _text, mode in pieces:
        scene = next(item for item in manifest["scenes"] if item["id"] == f"{spec['id']}-{name}")
        output = short_out / "scenes" / f"{name}.mp4"
        source_hash, reported_hash, layout = _identity(name, mode, scene, language, duration, moment, packet)
        part = short_out / "audio" / f"{name}.wav"
        if name == "moment" or hook_audio is None:
            start = float(moment["narration"]["inSec"]) if name == "moment" else 0.0
            _slice_audio(canonical, part, start, duration)
            part_source = canonical_hash
        else:
            _slice_audio(hook_audio, part, 0.0, duration)
            part_source = hook_hash
        audio_parts.append(part)
        line = {"sceneId": scene["id"], "text": scene["text"][language], "state": "unaligned"}
        row = {"id": scene["id"], "startSec": cursor, "durationSec": duration}
        job = scene_job(manifest, scene, PORTRAIT, row, line, part_source or "", source_hash, output)
        sidecar = short_out / "cache" / f"{name}.json"
        if decide(job, read_sidecar(sidecar), output) == "skip":
            action = "skipped"
        else:
            _produce_piece(name, mode, scene, output, duration, language, moment, packet)
            action = "rendered"
            write_sidecar(
                sidecar,
                {
                    "sceneId": scene["id"],
                    "formatId": "portrait",
                    "parameterHash": job["parameterHash"],
                    "dependencies": job["dependencies"],
                    "output": str(output),
                    "outputContentHash": sha256_file(output),
                    "sourceHash": reported_hash,
                    "layout": layout,
                    "regenerated": False,
                },
            )
        scene_rows.append(
            {
                "sceneId": scene["id"],
                "formatId": "portrait",
                "action": action,
                "parameterHash": job["parameterHash"],
                "dependencies": job["dependencies"],
                "output": str(output),
                "outputContentHash": sha256_file(output),
                "sourceHash": reported_hash,
                "layout": layout,
                "regenerated": False,
            }
        )
        cursor += duration
    soundtrack = _concat_audio(audio_parts, short_out / "soundtrack.wav")
    narration = {"id": manifest["assets"][0]["id"], "canonicalPath": str(soundtrack), "canonicalContentHash": sha256_file(soundtrack)}
    assembled = _assemble(manifest, short_out, narration, scene_rows)
    timing = _timing(manifest, canonical_hash)
    qa = check_episode(manifest, assembled["paths"], {narration["id"]: timing})
    (short_out / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    moment_row = next(row for row in scene_rows if row["sceneId"].endswith("-moment"))
    if moment_row["regenerated"] is not False:
        raise RenderFailure(f"{spec['id']} regenerated a reused asset", [])
    if not qa["pass"]:
        failed = [item["id"] for item in qa["checks"] if not item["pass"]]
        raise RenderFailure(f"{spec['id']} QA failed: {', '.join(failed)}", [])
    _require_standalone(manifest, timing)
    return {
        "id": spec["id"],
        "qaPass": True,
        "standsAlone": True,
        "assembled": assembled["records"],
        "scenes": scene_rows,
        "momentSourceHash": moment_row["sourceHash"],
        "layout": moment_row["layout"],
        "regenerated": False,
        "hookAudioHash": hook_hash,
        "episodeNarrationHash": canonical_hash,
    }


def _identity(
    name: str,
    mode: str,
    scene: dict[str, Any],
    language: str,
    duration: float,
    moment: dict[str, Any],
    packet: dict[str, Any],
) -> tuple[str, str, str]:
    text = scene["text"][language]
    if name != "moment" or mode != "flow-master":
        reported = parameter_hash(
            {"layout": NATIVE_CARD, "role": name, "text": text, "durationSec": duration, "width": 1080, "height": 1920}
        )
        return reported, reported, NATIVE_CARD
    focal = moment.get("focal") or {"x": 0.5, "y": 0.5}
    cache_key = parameter_hash(
        {
            "layout": DESIGNED_LAYOUT,
            "master": packet["sourceHash"],
            "focal": focal,
            "caption": text,
            "width": 1080,
            "height": 1920,
        }
    )
    return cache_key, str(packet["sourceHash"]), DESIGNED_LAYOUT


def _produce_piece(
    name: str,
    mode: str,
    scene: dict[str, Any],
    output: Path,
    duration: float,
    language: str,
    moment: dict[str, Any],
    packet: dict[str, Any],
) -> None:
    text = scene["text"][language]
    if mode == "flow-master" and name == "moment":
        derive_reframe(packet, output, 1080, 1920, focal=moment.get("focal"), caption=text)
        return
    render_card(output, 1080, 1920, duration, text)


def _short_manifest(episode: dict[str, Any], spec: dict[str, Any], pieces: list[tuple[str, float, dict[str, str], str]]) -> dict[str, Any]:
    scenes = []
    cursor = 0.0
    total = 0.0
    for name, duration, text, _mode in pieces:
        scene_id = f"{spec['id']}-{name}"
        scenes.append(
            {
                "id": scene_id,
                "durationSec": duration,
                "assetIds": ["narration-short"],
                "narration": {"assetId": "narration-short", "inSec": cursor, "outSec": cursor + duration},
                "focal": {"x": 0.5, "y": 0.45},
                "safe": {"top": 0.08, "bottom": 0.22, "left": 0.06, "right": 0.06},
                "text": text,
                "caption": {"fontScale": 0.04, "maxLines": 2},
                "picture": {"mode": "card", "assetId": "narration-short"},
            }
        )
        cursor += duration
        total += duration
    transitions = [{"after": scenes[index]["id"], "type": "cut", "durationSec": 0} for index in range(len(scenes) - 1)]
    return {
        "schemaVersion": 1,
        "id": spec["id"],
        "language": episode["language"],
        "supportedLanguages": episode["supportedLanguages"],
        "fps": 30,
        "episodeDurationSec": total,
        "formats": [PORTRAIT],
        "assets": [
            {
                "id": "narration-short",
                "kind": "narration",
                "source": {"provider": "synthetic-ffmpeg", "role": "reused-slice", "synth": {"kind": "sine", "frequencyHz": 440, "durationSec": total, "gainDb": -18}},
                "timing": "timing/short.json",
                "rights": {"status": "synthetic-fixture", "owner": "local-test"},
                "provenance": {"tool": "ffmpeg-lavfi", "origin": "slice of the episode tone, not TTS"},
            }
        ],
        "scenes": scenes,
        "transitions": transitions,
        "qa": {"allowBlack": False, "allowSilence": False, "allowClipping": False, "allowCaptionOverflow": False},
    }


def _timing(manifest: dict[str, Any], audio_hash: str) -> dict[str, Any]:
    template = {
        "assetId": "narration-short",
        "audioContentHash": None,
        "lines": [
            {"sceneId": scene["id"], "text": scene["text"][manifest["language"]], "state": "unaligned"}
            for scene in manifest["scenes"]
        ],
        "corrections": [],
    }
    resolved = resolve_timing(template, audio_hash, {scene["id"] for scene in manifest["scenes"]})
    for scene in manifest["scenes"]:
        line = line_for_scene(resolved, scene["id"])
        if line is None or line["text"] != scene["text"][manifest["language"]]:
            raise ManifestError(f"short timing drifted for {scene['id']}")
    return resolved


def _require_standalone(manifest: dict[str, Any], timing: dict[str, Any]) -> None:
    first, last = manifest["scenes"][0], manifest["scenes"][-1]
    if not first["id"].endswith("-hook") or not last["id"].endswith("-ending"):
        raise ManifestError(f"{manifest['id']} needs its own hook and ending")
    if line_for_scene(timing, first["id"])["text"] == line_for_scene(timing, last["id"])["text"]:
        raise ManifestError(f"{manifest['id']} hook and ending say the same thing")


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
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def _concat_audio(parts: list[Path], dest: Path) -> Path:
    listing = dest.with_suffix(".concat.txt")
    lines = [f"file '{part.resolve()}'" for part in parts]
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
            str(listing),
            "-c",
            "copy",
            str(dest),
        ]
    )
    return dest
