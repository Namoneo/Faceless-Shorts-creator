"""VoiceStudio door. It records the requested engine and voice, then fails closed.

Nothing here contacts a localhost service, downloads a weight, or writes audio.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "productions" / "voicestudio-voices.json"


class VoiceStudioBlocked(RuntimeError):
    pass


def load_voice_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG
    doc = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("adapter") != "voicestudio":
        raise VoiceStudioBlocked(f"{target} is not a VoiceStudio voice config")
    if doc.get("status") != "fail-closed" or doc.get("callsRuntime") is not False:
        raise VoiceStudioBlocked("VoiceStudio config must stay fail-closed and must not call the runtime")
    languages = doc.get("languages")
    if not isinstance(languages, dict) or "uz-Latn" not in languages:
        raise VoiceStudioBlocked("VoiceStudio config needs a uz-Latn entry")
    return doc


def voice_for(language: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = config or load_voice_config()
    row = doc["languages"].get(language)
    if not isinstance(row, dict) or row.get("configured") is not True:
        raise VoiceStudioBlocked(
            f"VoiceStudio has no configured voice for {language}. "
            "It does not fall back to OmniVoice. Set that language in the voice config, or import audio."
        )
    for key in ("engine", "model", "voice"):
        if not isinstance(row.get(key), str) or not row[key]:
            raise VoiceStudioBlocked(f"VoiceStudio {language} is missing {key}")
    if row["engine"] == "OmniVoice" or row["model"] == "OmniVoice":
        raise VoiceStudioBlocked("OmniVoice is not the default. Configure another engine explicitly.")
    return {"language": language, "engine": row["engine"], "model": row["model"], "voice": row["voice"]}


def narration_door(door: str, language: str = "uz-Latn") -> str:
    cleaned = door.strip().lower()
    if cleaned == "import":
        return "import"
    if cleaned == "voicestudio":
        row = voice_for(language)
        raise VoiceStudioBlocked(
            "VoiceStudio is gated until the Mac runner is approved. "
            f"The {language} request would be engine {row['engine']}, model {row['model']}, voice {row['voice']}. "
            "This stub does not call VoiceStudio, download a model, or synthesize audio. Import a user-owned file."
        )
    raise VoiceStudioBlocked(f"unknown narration door {door!r}; the open door is import")


def synthesize(language: str, text: str, config: dict[str, Any] | None = None) -> None:
    if not isinstance(text, str) or not text.strip():
        raise VoiceStudioBlocked("refusing an empty VoiceStudio request")
    row = voice_for(language, config)
    raise VoiceStudioBlocked(
        "VoiceStudio is gated until the Mac runner is approved. "
        f"Requested {row['language']}: engine {row['engine']}, model {row['model']}, voice {row['voice']}. "
        "This stub does not call VoiceStudio, download a model, or synthesize audio."
    )
