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
    if "voiceConsent" not in doc:
        raise VoiceStudioBlocked("VoiceStudio config requires voiceConsent")
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
    engine = row.get("engine")
    profile = row.get("voice_profile")
    if not isinstance(engine, str) or not engine or not isinstance(profile, str) or not profile:
        raise VoiceStudioBlocked(f"VoiceStudio {language} needs engine and voice_profile")
    if engine == "OmniVoice" or row.get("model") == "OmniVoice":
        raise VoiceStudioBlocked("OmniVoice is not the default. Configure another engine explicitly.")
    if row.get("require_navoiy") is not True:
        raise VoiceStudioBlocked(
            f"VoiceStudio {language} fails closed: require_navoiy must be true so a run without the Navoiy checkpoint is refused."
        )
    return {
        "language": language,
        "engine": engine,
        "voice_profile": profile,
        "require_navoiy": True,
        "voiceConsent": doc.get("voiceConsent"),
        "navoiyActive": doc.get("navoiyActive") is True,
    }


def _refuse(row: dict[str, Any]) -> None:
    consent = row.get("voiceConsent")
    consent_note = (
        "voiceConsent is unset, so commercial use is blocked. "
        "Commercial production needs your own recording or a reference with explicit commercial voice-synthesis permission."
        if not consent
        else "voiceConsent is set. Commercial use still waits on an approved runner."
    )
    active = (
        "The Navoiy checkpoint is marked active in this config."
        if row.get("navoiyActive")
        else "require_navoiy is true and the Navoiy checkpoint is not active here. This stub does not query the Mac."
    )
    raise VoiceStudioBlocked(
        "VoiceStudio is gated until the Mac runner is approved. "
        f"The {row['language']} request would be engine {row['engine']}, voice_profile {row['voice_profile']}. "
        f"{active} {consent_note} "
        "This stub does not call VoiceStudio, download a model, or synthesize audio. Import a user-owned file."
    )


def narration_door(door: str, language: str = "uz-Latn") -> str:
    cleaned = door.strip().lower()
    if cleaned == "import":
        return "import"
    if cleaned == "voicestudio":
        _refuse(voice_for(language))
    raise VoiceStudioBlocked(f"unknown narration door {door!r}; the open door is import")


def synthesize(language: str, text: str, config: dict[str, Any] | None = None) -> None:
    if not isinstance(text, str) or not text.strip():
        raise VoiceStudioBlocked("refusing an empty VoiceStudio request")
    _refuse(voice_for(language, config))
