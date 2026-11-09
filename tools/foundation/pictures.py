"""Local ffmpeg pictures. These are cards, trims, and pads, not provider generation."""

from __future__ import annotations

from pathlib import Path

from tools.foundation.ffmpeg import run

FONT = Path(__file__).resolve().parents[2] / "media" / "fonts" / "DejaVuSans.ttf"


def render_card(path: Path, width: int, height: int, duration: float, text: str, fps: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text_file = path.with_suffix(".txt")
    text_file.write_text(text, encoding="utf-8")
    font_size = max(28, int(height * 0.045))
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
            f"color=c=0x1e2a3a:s={width}x{height}:r={fps}:d={duration:.3f}",
            "-vf",
            f"drawtext=fontfile={FONT}:textfile={text_file}:fontsize={font_size}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-t",
            f"{duration:.3f}",
            str(path),
        ]
    )


def place_video(source: Path, path: Path, width: int, height: int, duration: float, fps: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x1e2a3a",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-t",
            f"{duration:.3f}",
            str(path),
        ]
    )
