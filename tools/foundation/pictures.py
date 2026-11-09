"""Local ffmpeg pictures. Cards are drawn at the target size. A portrait Flow frame is a layout, not a pad."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.foundation.ffmpeg import run

FONT = Path(__file__).resolve().parents[2] / "media" / "fonts" / "DejaVuSans.ttf"
STAND_IN_LABEL = "TEST PATTERN - stand-in for manual Google Flow export"
DESIGNED_LAYOUT = "designed-portrait-v3"
NATIVE_CARD = "native-card-v3"


def render_card(path: Path, width: int, height: int, duration: float, text: str, fps: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text_file = path.with_suffix(".txt")
    font_size = max(28, int(height * 0.045))
    text_file.write_text(wrap_text(text, width, font_size), encoding="utf-8")
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
            f"drawtext=fontfile={FONT}:textfile={text_file}:fontsize={font_size}:fontcolor=white:line_spacing=12:x=(w-text_w)/2:y=(h-text_h)/2",
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
    """Scale to the exact target. This does not pad a different aspect into bars."""
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
            f"scale={width}:{height}",
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


def portrait_layout(
    source: Path,
    path: Path,
    width: int,
    height: int,
    duration: float,
    fps: int,
    focal: dict[str, Any],
    caption: str,
    label: str = STAND_IN_LABEL,
) -> None:
    """Fill the portrait frame from a landscape master.

    The background is the master scaled to cover the frame and blurred, cropped
    around the focal point. The sharp master sits in the remaining band, also
    placed from that focal point, with the caption and stand-in label drawn at
    portrait size. It is not a 16:9 picture padded onto empty bars.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fx = _unit(focal.get("x"))
    fy = _unit(focal.get("y"))
    font_size = max(36, int(height * 0.045))
    label_size = label_font_size(width, height)
    caption_text = wrap_text(caption, width, font_size)
    label_file = path.with_suffix(".label.txt")
    caption_file = path.with_suffix(".caption.txt")
    label_file.write_text(label, encoding="utf-8")
    caption_file.write_text(caption_text, encoding="utf-8")
    clip_h = max(2, int(width * 9 / 16) // 2 * 2)
    label_bottom = int(height * 0.18)
    caption_top = int(height * 0.72)
    min_y = label_bottom
    max_y = max(min_y, caption_top - clip_h)
    overlay_y = int(round(min_y + (max_y - min_y) * fy))
    drawn = (
        f"[lay]drawtext=fontfile={FONT}:textfile={label_file}:fontsize={label_size}:fontcolor=white:"
        f"box=1:boxcolor=0x000000@0.75:boxborderw=16:x=(w-text_w)/2:y=h*0.06"
    )
    if caption.strip():
        drawn += (
            f",drawtext=fontfile={FONT}:textfile={caption_file}:fontsize={font_size}:fontcolor=white:"
            f"line_spacing=12:box=1:boxcolor=0x000000@0.55:boxborderw=14:x=(w-text_w)/2:y=h*0.78"
        )
    graph = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-{width})*{fx:.4f}:(ih-{height})*{fy:.4f},boxblur=24:2[bgb];"
        f"[fg]scale={width}:-2[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:{overlay_y}[lay];"
        f"{drawn}"
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
            graph,
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


def stand_in_filter(label_file: Path, width: int, height: int) -> str:
    label_size = label_font_size(width, height)
    return (
        f"drawtext=fontfile={FONT}:textfile={label_file}:fontsize={label_size}:fontcolor=white:"
        f"box=1:boxcolor=0x000000@0.78:boxborderw=18:x=(w-text_w)/2:y=48"
    )


def label_font_size(width: int, height: int) -> int:
    """One line, large enough to read, small enough that the whole label stays in frame."""
    fitted = int((width * 0.90) / (len(STAND_IN_LABEL) * 0.52))
    return max(22, min(fitted, max(28, int(height * 0.04))))


def wrap_text(text: str, width: int, font_size: int) -> str:
    """Break on spaces so a card line stays inside the frame at the shared type size."""
    advance = font_size * 0.56
    limit = max(8, int((width * 0.90) / advance))
    words = text.split()
    if not words:
        return text
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if len(trial) <= limit:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return "\n".join(lines)


def _unit(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, number))
