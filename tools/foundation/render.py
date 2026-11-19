"""Render scene ranges and assemble both formats. The default renderer is local Remotion plus ffmpeg."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from tools.foundation.cache import decide, read_sidecar, scene_job, write_sidecar
from tools.foundation.ffmpeg import duration_seconds, probe, run, stream_of
from tools.foundation.hashutil import parameter_hash, sha256_file
from tools.foundation.manifest import load_manifest, timeline
from tools.foundation.narration import prepare_narration
from tools.foundation.qa import check_episode
from tools.foundation.timing import display_words, line_for_scene, load_timing, resolve_timing

ROOT = Path(__file__).resolve().parents[2]
Renderer = Callable[[list[dict[str, Any]]], None]


class RenderFailure(RuntimeError):
    def __init__(self, message: str, completed: list[str]) -> None:
        super().__init__(message)
        self.completed = completed


def run_episode(manifest_path: Path, out_dir: Path, renderer: Renderer | None = None) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    narration_records = prepare_narration(manifest, manifest_path.parent, out_dir)
    narration = narration_records[0]
    timing = _bind_timing(manifest, manifest_path.parent, narration, out_dir)
    visual = _visual_record(manifest)
    placed = timeline(manifest)
    scene_rows: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for fmt in manifest["formats"]:
        props = _props(manifest, placed, timing)
        for scene, row in zip(manifest["scenes"], placed):
            line = line_for_scene(timing, scene["id"])
            if line is None:
                raise RenderFailure(f"scene {scene['id']} has no timing line", [])
            output = out_dir / "scenes" / fmt["id"] / f"{scene['id']}.mp4"
            job = scene_job(
                manifest,
                scene,
                fmt,
                row,
                line,
                narration["canonicalContentHash"],
                visual["contentHash"],
                output,
            )
            job["inputProps"] = props
            sidecar_path = out_dir / "cache" / "scenes" / fmt["id"] / f"{scene['id']}.json"
            action = decide(job, read_sidecar(sidecar_path), output)
            if action == "skip":
                scene_rows.append(_scene_row(job, "skipped", sha256_file(output)))
            else:
                pending.append(job)
    completed: list[str] = []
    if pending:
        render = renderer or node_renderer
        before = {job["output"]: _file_hash(Path(job["output"])) for job in pending}
        try:
            render(pending)
        except Exception as exc:
            completed = _finalize(pending, out_dir, before, only_new=True)
            raise RenderFailure(str(exc), completed) from exc
        completed = _finalize(pending, out_dir, before, only_new=False)
        missing = [job["output"] for job in pending if f"{job['formatId']}/{job['sceneId']}" not in completed]
        if missing:
            raise RenderFailure(f"renderer produced no usable file for {missing}", completed)
        for job in pending:
            scene_rows.append(_scene_row(job, "rendered", sha256_file(Path(job["output"]))))
    assembled = _assemble(manifest, out_dir, narration, scene_rows)
    qa = check_episode(manifest, assembled["paths"], {narration["id"]: timing})
    (out_dir / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "episodeId": manifest["id"],
        "language": manifest["language"],
        "offline": True,
        "assets": [narration, visual],
        "scenes": scene_rows,
        "assembled": assembled["records"],
        "qaPass": qa["pass"],
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not qa["pass"]:
        failed = [item["id"] for item in qa["checks"] if not item["pass"]]
        raise RenderFailure(f"QA failed: {', '.join(failed)}", completed)
    return report


def node_renderer(jobs: list[dict[str, Any]]) -> None:
    payload = {
        "jobs": [
            {
                "compositionId": job["compositionId"],
                "out": job["output"],
                "frameStart": job["frameStart"],
                "frameEnd": job["frameEnd"],
                "inputProps": job["inputProps"],
            }
            for job in jobs
        ]
    }
    jobs_path = Path(jobs[0]["output"]).parents[2] / "cache" / "render-jobs.json"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    script = ROOT / "remotion" / "scripts" / "render-foundation.mjs"
    result = subprocess.run(
        ["node", str(script), str(jobs_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RenderFailure(result.stdout[-4000:] or "remotion render failed", [])


def _bind_timing(manifest: dict[str, Any], manifest_dir: Path, narration: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    asset = next(item for item in manifest["assets"] if item["id"] == narration["id"])
    template = load_timing(manifest_dir / asset["timing"])
    if template.get("assetId") != asset["id"]:
        raise RenderFailure(f"timing assetId does not match {asset['id']}", [])
    if template.get("language") != manifest["language"]:
        raise RenderFailure("timing language does not match the episode language", [])
    resolved = resolve_timing(template, narration["canonicalContentHash"], {scene["id"] for scene in manifest["scenes"]})
    for scene in manifest["scenes"]:
        line = line_for_scene(resolved, scene["id"])
        if line is None or line["text"] != scene["text"][manifest["language"]]:
            raise RenderFailure(f"timing display text for {scene['id']} does not match the manifest", [])
        joined = display_words(line)
        if joined is not None and joined != line["text"]:
            raise RenderFailure(
                f"timed words for {scene['id']} do not join to the exact display text",
                [],
            )
    target = out_dir / "timing" / f"{asset['id']}.resolved.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def _visual_record(manifest: dict[str, Any]) -> dict[str, Any]:
    asset = next(item for item in manifest["assets"] if item["kind"] == "visual")
    component = ROOT / "remotion" / "src" / "shots" / "foundation" / f"{asset['source']['component']}.tsx"
    renderer_script = ROOT / "remotion" / "scripts" / "render-foundation.mjs"
    if not component.is_file() or not renderer_script.is_file():
        raise RenderFailure(f"visual source for {asset['id']} is not in the repo", [])
    content = parameter_hash(
        {
            "id": asset["id"],
            "component": asset["source"]["component"],
            "componentSourceHash": sha256_file(component),
            "rendererSourceHash": sha256_file(renderer_script),
        }
    )
    return {
        "id": asset["id"],
        "kind": "visual",
        "action": "referenced",
        "contentHash": content,
        "parameterHash": content,
        "sharedAcrossFormats": True,
        "rights": asset["rights"],
        "provenance": asset["provenance"],
        "source": {"provider": asset["source"]["provider"], "role": asset["source"]["role"]},
    }


def _props(manifest: dict[str, Any], placed: list[dict[str, Any]], timing: dict[str, Any]) -> dict[str, Any]:
    scenes = []
    for scene, row in zip(manifest["scenes"], placed):
        line = line_for_scene(timing, scene["id"])
        scenes.append(
            {
                "id": scene["id"],
                "startSec": row["startSec"],
                "durationSec": row["durationSec"],
                "focal": scene["focal"],
                "safe": scene["safe"],
                "text": scene["text"][manifest["language"]],
                "captionFontScale": scene["caption"]["fontScale"],
                "captionMaxLines": scene["caption"]["maxLines"],
                "line": line,
            }
        )
    return {"language": manifest["language"], "label": "OFFLINE FIXTURE", "scenes": scenes}


def _finalize(
    jobs: list[dict[str, Any]],
    out_dir: Path,
    before: dict[str, str | None],
    only_new: bool,
) -> list[str]:
    done = []
    for job in jobs:
        output = Path(job["output"])
        output_hash = _file_hash(output)
        if output_hash is None:
            continue
        if only_new and output_hash == before.get(job["output"]):
            continue
        if not _output_ok(job):
            continue
        write_sidecar(
            out_dir / "cache" / "scenes" / job["formatId"] / f"{job['sceneId']}.json",
            {
                "sceneId": job["sceneId"],
                "formatId": job["formatId"],
                "parameterHash": job["parameterHash"],
                "dependencies": job["dependencies"],
                "output": job["output"],
                "outputContentHash": output_hash,
            },
        )
        done.append(f"{job['formatId']}/{job['sceneId']}")
    return done


def _file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256_file(path)


def _output_ok(job: dict[str, Any]) -> bool:
    output = Path(job["output"])
    if not output.is_file():
        return False
    try:
        info = probe(output)
    except Exception:
        return False
    video = stream_of(info, "video")
    if video is None:
        return False
    if int(video.get("width") or 0) != job["width"] or int(video.get("height") or 0) != job["height"]:
        return False
    duration = duration_seconds(info)
    expected = job["frameCount"] / job["fps"]
    if duration is None or abs(duration - expected) > (2 / job["fps"]):
        return False
    return True


def _scene_row(job: dict[str, Any], action: str, output_hash: str) -> dict[str, Any]:
    return {
        "sceneId": job["sceneId"],
        "formatId": job["formatId"],
        "action": action,
        "parameterHash": job["parameterHash"],
        "dependencies": job["dependencies"],
        "output": job["output"],
        "outputContentHash": output_hash,
    }


def _assemble(
    manifest: dict[str, Any],
    out_dir: Path,
    narration: dict[str, Any],
    scene_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    paths: dict[str, Path] = {}
    records = []
    order = [scene["id"] for scene in manifest["scenes"]]
    for fmt in manifest["formats"]:
        selected = [row for row in scene_rows if row["formatId"] == fmt["id"]]
        selected.sort(key=lambda row: order.index(row["sceneId"]))
        final = out_dir / "assemble" / f"{fmt['id']}.mp4"
        parameter = {
            "scenes": [row["outputContentHash"] for row in selected],
            "narration": narration["canonicalContentHash"],
            "width": fmt["width"],
            "height": fmt["height"],
            "fps": manifest["fps"],
            "durationSec": manifest["episodeDurationSec"],
            "audioCodec": "aac",
            "audioBitrate": "160k",
        }
        param_hash = parameter_hash(parameter)
        sidecar_path = out_dir / "cache" / "assemble" / f"{fmt['id']}.json"
        previous = read_sidecar(sidecar_path)
        dependencies = {"scenes": parameter["scenes"], "narration": narration["canonicalContentHash"]}
        action = "skipped"
        if (
            previous is None
            or previous.get("parameterHash") != param_hash
            or previous.get("dependencies") != dependencies
            or not final.is_file()
            or previous.get("outputContentHash") != sha256_file(final)
        ):
            _mux(selected, narration["canonicalPath"], final, float(manifest["episodeDurationSec"]))
            action = "built"
        output_hash = sha256_file(final)
        write_sidecar(
            sidecar_path,
            {
                "formatId": fmt["id"],
                "parameterHash": param_hash,
                "dependencies": dependencies,
                "output": str(final),
                "outputContentHash": output_hash,
            },
        )
        paths[fmt["id"]] = final
        records.append({"formatId": fmt["id"], "action": action, "path": str(final), "contentHash": output_hash})
    return {"paths": paths, "records": records}


def _mux(scene_rows: list[dict[str, Any]], narration: str, final: Path, duration: float) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    concat = final.with_suffix(".concat.txt")
    lines = []
    for row in scene_rows:
        escaped = str(Path(row["output"]).resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    video = final.with_suffix(".video.mp4")
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
            narration,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            str(final),
        ]
    )
