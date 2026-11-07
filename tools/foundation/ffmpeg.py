"""Local ffmpeg/ffprobe helpers. These process fixture media; they do not generate speech or pictures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-2000:]
        raise MediaError(f"command failed: {' '.join(cmd)}\n{tail}")
    return result


def probe(path: Path) -> dict:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned non-json for {path}") from exc


def stream_of(info: dict, codec_type: str) -> dict | None:
    for stream in info.get("streams") or []:
        if stream.get("codec_type") == codec_type:
            return stream
    return None


def duration_seconds(info: dict) -> float | None:
    raw = (info.get("format") or {}).get("duration")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def volume_detect(path: Path, start: float | None = None, duration: float | None = None) -> dict[str, float | None]:
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", f"{start:.6f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-af", "volumedetect", "-f", "null", "-"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise MediaError(f"volumedetect failed for {path}\n{result.stderr[-1500:]}")
    mean = _db(result.stderr, "mean_volume:")
    peak = _db(result.stderr, "max_volume:")
    return {"mean_db": mean, "max_db": peak}


def _db(text: str, label: str) -> float | None:
    for line in text.splitlines():
        if label in line:
            token = line.split(label, 1)[1].strip().split()[0]
            try:
                return float(token)
            except ValueError:
                return None
    return None


def frame_luma(path: Path, time_sec: float) -> float | None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-ss",
        f"{max(0.0, time_sec):.6f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "signalstats,metadata=print",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise MediaError(f"signalstats failed for {path}\n{result.stderr[-1500:]}")
    blob = result.stderr + "\n" + result.stdout
    for line in blob.splitlines():
        if "YAVG" in line:
            token = line.split("YAVG", 1)[1].lstrip("=: ").split()[0]
            try:
                return float(token)
            except ValueError:
                return None
    return None
