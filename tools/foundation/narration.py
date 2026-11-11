"""Import narration from a local file or a deterministic ffmpeg sine. Original bytes are preserved."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from tools.foundation.ffmpeg import duration_seconds, probe, run, stream_of, volume_detect
from tools.foundation.hashutil import parameter_hash, sha256_file
from tools.foundation.manifest import ManifestError

LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
CANONICAL_RATE = 48000
CANONICAL_CHANNELS = 2


def narration_parameters() -> dict[str, Any]:
    return {
        "filter": LOUDNORM,
        "sampleRate": CANONICAL_RATE,
        "channels": CANONICAL_CHANNELS,
        "codec": "pcm_s16le",
    }


def prepare_narration(manifest: dict[str, Any], manifest_dir: Path, out_dir: Path) -> list[dict[str, Any]]:
    records = []
    for asset in manifest["assets"]:
        if asset["kind"] != "narration":
            continue
        records.append(prepare_one(asset, manifest_dir, out_dir))
    if not records:
        raise ManifestError("episode has no narration asset")
    check_scene_ranges(manifest, records)
    return records


def prepare_one(asset: dict[str, Any], manifest_dir: Path, out_dir: Path) -> dict[str, Any]:
    source_path = materialize_source(asset, manifest_dir, out_dir)
    source_hash = sha256_file(source_path)
    preserved = out_dir / "originals" / asset["id"] / f"{source_hash}.wav"
    if not preserved.exists():
        preserved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, preserved)
    if sha256_file(preserved) != source_hash:
        raise ManifestError(f"preserved original for {asset['id']} does not match the source bytes")
    param_hash = parameter_hash(narration_parameters())
    canonical = out_dir / "canonical" / f"{asset['id']}.wav"
    sidecar_path = out_dir / "cache" / f"{asset['id']}.json"
    previous = _read_json(sidecar_path)
    if (
        previous
        and previous.get("originalContentHash") == source_hash
        and previous.get("parameterHash") == param_hash
        and canonical.is_file()
        and previous.get("canonicalContentHash") == sha256_file(canonical)
    ):
        previous["action"] = "skipped"
        return previous
    canonical.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(source_path),
            "-af",
            LOUDNORM,
            "-ar",
            str(CANONICAL_RATE),
            "-ac",
            str(CANONICAL_CHANNELS),
            "-c:a",
            "pcm_s16le",
            str(canonical),
        ]
    )
    info = probe(canonical)
    audio = stream_of(info, "audio")
    if audio is None:
        raise ManifestError(f"canonical narration for {asset['id']} has no audio stream")
    duration = duration_seconds(info)
    if duration is None:
        raise ManifestError(f"canonical narration for {asset['id']} has no duration")
    levels = volume_detect(canonical)
    record = {
        "id": asset["id"],
        "kind": "narration",
        "action": "built",
        "originalPath": str(source_path),
        "preservedPath": str(preserved),
        "canonicalPath": str(canonical),
        "originalContentHash": source_hash,
        "parameterHash": param_hash,
        "canonicalContentHash": sha256_file(canonical),
        "probe": {
            "durationSec": duration,
            "sampleRate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
            "codec": audio.get("codec_name"),
            "meanDb": levels["mean_db"],
            "maxDb": levels["max_db"],
        },
        "rights": asset["rights"],
        "provenance": asset["provenance"],
        "source": {"provider": asset["source"]["provider"], "role": asset["source"]["role"]},
    }
    _write_json(sidecar_path, record)
    return record


def materialize_source(asset: dict[str, Any], manifest_dir: Path, out_dir: Path) -> Path:
    provider = str(asset["source"]["provider"]).strip().lower()
    if provider == "import":
        path = manifest_dir / asset["source"]["path"]
        if not path.is_file():
            raise ManifestError(f"imported narration is missing: {path}")
        reject_undecodable(path, asset["id"])
        return path
    if provider == "synthetic-ffmpeg":
        synth = asset["source"]["synth"]
        target = out_dir / "sources" / asset["id"] / "synthetic-sine.wav"
        spec_path = target.with_suffix(".spec.json")
        spec_hash = parameter_hash(
            {
                "synth": synth,
                "sampleRate": CANONICAL_RATE,
                "channels": 2,
                "codec": "pcm_s16le",
            }
        )
        stored = _read_json(spec_path)
        if (
            target.is_file()
            and stored
            and stored.get("parameterHash") == spec_hash
            and stored.get("contentHash") == sha256_file(target)
        ):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        duration = float(synth["durationSec"])
        frequency = float(synth["frequencyHz"])
        gain = float(synth["gainDb"])
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
                f"sine=frequency={frequency}:sample_rate={CANONICAL_RATE}:duration={duration}",
                "-af",
                f"volume={gain}dB",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(target),
            ]
        )
        target.with_suffix(".txt").write_text(
            "Synthetic ffmpeg lavfi sine. Not TTS. Not a narrated pilot. Not VoiceStudio.\n",
            encoding="utf-8",
        )
        _write_json(
            spec_path,
            {
                "parameterHash": spec_hash,
                "contentHash": sha256_file(target),
                "synth": synth,
                "label": "synthetic-ffmpeg-sine",
            },
        )
        return target
    raise ManifestError(f"asset {asset['id']} is not a narration source this runner can import")


def reject_undecodable(path: Path, asset_id: str) -> None:
    try:
        info = probe(path)
    except Exception as exc:
        raise ManifestError(f"imported narration {asset_id} is not decodable audio: {exc}") from exc
    if stream_of(info, "audio") is None:
        raise ManifestError(f"imported narration {asset_id} has no audio stream")


def check_scene_ranges(manifest: dict[str, Any], records: list[dict[str, Any]]) -> None:
    by_id = {record["id"]: record for record in records}
    for scene in manifest["scenes"]:
        narration = scene.get("narration")
        if not narration:
            continue
        record = by_id.get(narration["assetId"])
        if record is None:
            raise ManifestError(f"scene {scene['id']} narration asset was not imported")
        duration = float(record["probe"]["durationSec"])
        if float(narration["outSec"]) > duration + (1 / 30):
            raise ManifestError(
                f"scene {scene['id']} narration outSec {narration['outSec']} exceeds audio {duration:.3f}s"
            )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return None
    return loaded


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
