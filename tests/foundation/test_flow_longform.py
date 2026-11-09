"""Flow packets, the VoiceStudio stub, and the offline long-form fixture."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.foundation.support import REPO, load_fixture
from tools.foundation.ffmpeg import run
from tools.foundation.hashutil import sha256_file
from tools.foundation.pictures import DESIGNED_LAYOUT, NATIVE_CARD, STAND_IN_LABEL, portrait_layout, render_card
from tools.foundation.qa import check_portrait_fill, check_uzbek_address, check_uzbek_apostrophes
from tools.foundation.flow import (
    ESTIMATE_LABEL,
    FlowError,
    approve,
    derive_reframe,
    estimate_credits,
    import_export,
    mark_ready_to_import,
    plan_packet,
    plan_separate_variant,
    release_for_manual,
    reject,
    write_synthetic_stand_in,
)
from tools.foundation.longform import plan_long, run_longform
from tools.foundation.manifest import ManifestError, validate_manifest
from tools.foundation.repurpose import run_repurpose
from tools.foundation.voicestudio import VoiceStudioBlocked, load_voice_config, narration_door, synthesize, voice_for

LONGFORM = REPO / "productions" / "fixtures" / "longform"
PROVENANCE = {"tool": "ffmpeg", "origin": "unit", "note": "synthetic stand-in"}


def _clip(path: Path, width: int, height: int, fps: int, duration: float, audio: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x1e2a3a:s={width}x{height}:r={fps}:d={duration}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}", "-shortest", "-c:a", "aac", "-b:a", "96k"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), "-t", str(duration), str(path)]
    run(cmd)


class FlowPacketTests(unittest.TestCase):
    def test_estimate_is_labelled_and_not_a_provider_call(self) -> None:
        estimate = estimate_credits("flow-manual-placeholder", 2)
        self.assertEqual(estimate["amount"], 24)
        self.assertEqual(estimate["label"], ESTIMATE_LABEL)
        self.assertIn("Verify", estimate["label"])
        unknown = estimate_credits("not-a-model", 2)
        self.assertFalse(unknown["known"])
        self.assertIsNone(unknown["amount"])

    def test_status_machine_does_not_skip_the_manual_steps(self) -> None:
        packet = plan_packet("packet-a", "scene-a", "a prompt", 1, "synthetic-stand-in")
        self.assertEqual(packet["status"], "planned")
        with self.assertRaises(FlowError):
            import_export(packet, Path("missing.mp4"), PROVENANCE, Path("unused"))
        released = release_for_manual(packet)
        self.assertEqual(released["status"], "awaiting_manual_generation")
        with self.assertRaises(FlowError):
            approve(released)
        ready = mark_ready_to_import(released)
        self.assertEqual(ready["status"], "awaiting_import")

    def test_importer_rejects_bad_exports_and_caches_a_good_one_once(self) -> None:
        packet = mark_ready_to_import(release_for_manual(plan_packet("packet-b", "scene-b", "prompt", 1, "synthetic-stand-in")))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            wrong_ratio = root / "tall.mp4"
            wrong_fps = root / "fps.mp4"
            silent = root / "silent.mp4"
            good = root / "good.mp4"
            corrupt = root / "corrupt.mp4"
            _clip(wrong_ratio, 1080, 1920, 30, 1, True)
            _clip(wrong_fps, 1920, 1080, 24, 1, True)
            _clip(silent, 1920, 1080, 30, 1, False)
            _clip(good, 1920, 1080, 30, 1, True)
            corrupt.write_bytes(b"this is not a media file")
            with self.assertRaises(FlowError):
                import_export(packet, wrong_ratio, PROVENANCE, cache)
            with self.assertRaises(FlowError):
                import_export(packet, wrong_fps, PROVENANCE, cache)
            with self.assertRaises(FlowError):
                import_export(packet, silent, PROVENANCE, cache)
            with self.assertRaises(FlowError):
                import_export(packet, corrupt, PROVENANCE, cache)
            with self.assertRaises(FlowError):
                import_export(packet, good, None, cache)
            self.assertEqual(packet["status"], "awaiting_import")
            validated = import_export(packet, good, PROVENANCE, cache)
            self.assertEqual(validated["status"], "validated")
            self.assertEqual(validated["cacheAction"], "stored")
            again = import_export(dict(validated, status="awaiting_import", history=["planned"]), good, PROVENANCE, cache)
            self.assertEqual(again["cacheAction"], "skipped")
            self.assertEqual(again["sourceHash"], validated["sourceHash"])
            approved = approve(validated)
            self.assertEqual(packet["status"], "awaiting_import")
            with self.assertRaises(FlowError):
                plan_separate_variant(approved, "  ")
            variant = plan_separate_variant(approved, "The portrait composition cannot be a pad of this master.")
            self.assertEqual(approved["status"], "approved")
            self.assertIsNone(variant["sourceHash"])
            self.assertEqual(variant["status"], "planned")
            self.assertIn("portrait composition", variant["dualFormat"]["separateVariantReason"])
            framed = derive_reframe(approved, root / "portrait.mp4", 1080, 1920)
            self.assertFalse(framed["regenerated"])
            self.assertEqual(framed["sourceHash"], approved["sourceHash"])
            self.assertEqual(approved["status"], "approved")
            again_frame = derive_reframe(approved, root / "portrait.mp4", 1080, 1920)
            self.assertEqual(again_frame["action"], "skipped")
            rejected = reject(validated, "wrong picture")
            self.assertEqual(rejected["status"], "rejected")


class VoiceStudioTests(unittest.TestCase):
    def test_uzbek_defaults_to_navoiy_and_nodira_and_still_fails_closed(self) -> None:
        config = load_voice_config()
        row = voice_for("uz-Latn", config)
        self.assertEqual(row["engine"], "cosyvoice")
        self.assertEqual(row["voice_profile"], "Nodira")
        self.assertTrue(row["require_navoiy"])
        self.assertNotEqual(row["engine"], "OmniVoice")
        self.assertIsNone(config["voiceConsent"])
        self.assertFalse(row["navoiyActive"])
        self.assertFalse(config["callsRuntime"])
        self.assertFalse(config["downloadsModels"])
        self.assertFalse(config["upstreamCatalogMatch"])
        with self.assertRaises(VoiceStudioBlocked) as blocked:
            synthesize("uz-Latn", "Salom")
        self.assertIn("Nodira", str(blocked.exception))
        self.assertIn("gated", str(blocked.exception))
        self.assertIn("commercial use is blocked", str(blocked.exception))
        self.assertIn("not active", str(blocked.exception))
        with self.assertRaises(VoiceStudioBlocked) as english:
            voice_for("en")
        self.assertIn("OmniVoice", str(english.exception))
        self.assertEqual(narration_door("import"), "import")
        with self.assertRaises(VoiceStudioBlocked):
            narration_door("voicestudio")

    def test_narration_door_command_fails_closed(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.foundation.cli", "narration-door", "--door", "voicestudio"],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Nodira", result.stdout)
        self.assertIn("gated", result.stdout)
        refused = subprocess.run(
            [sys.executable, "-m", "tools.foundation.cli", "flow-generate"],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(refused.returncode, 2)


class LongformTests(unittest.TestCase):
    def test_plan_stops_before_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = plan_long("Synthetic fixture", "uz-Latn", 24, "local test", root)
            self.assertFalse(list(root.rglob("*.mp4")))
            self.assertFalse(list(root.rglob("*.wav")))
            english = plan_long("Synthetic fixture", "en", 24, "local test", root / "en")
        self.assertTrue(doc["stopped"])
        self.assertEqual(doc["flowPacket"]["status"], "planned")
        self.assertEqual(doc["narration"]["door"], "import")
        self.assertEqual(doc["narration"]["uzLatnRequest"]["engine"], "cosyvoice")
        self.assertEqual(doc["narration"]["uzLatnRequest"]["voice_profile"], "Nodira")
        self.assertTrue(doc["narration"]["uzLatnRequest"]["require_navoiy"])
        self.assertIsNone(doc["narration"]["uzLatnRequest"]["voiceConsent"])
        self.assertEqual(doc["scenes"][1]["classification"], "flow")
        self.assertFalse(english["narration"]["languageConfigured"])
        self.assertIn("OmniVoice", english["narration"]["note"])

    def test_pilot_plan_stops_without_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ManifestError):
                plan_long("Pilot", "uz-Latn", 360, "oilaviy", root)
            doc = plan_long("Pilot", "uz-Latn", 360, "oilaviy", root, pilot=True)
            self.assertTrue(doc["stopped"])
            self.assertEqual(doc["durationSec"], 360)
            self.assertFalse(list(root.rglob("*.mp4")))
            self.assertFalse(list(root.rglob("*.wav")))
            self.assertIn("siz", (root / "script.md").read_text(encoding="utf-8"))

    def test_sen_form_warns_and_does_not_fail(self) -> None:
        warned = check_uzbek_address(
            {"scenes": [{"id": "a", "text": {"uz-Latn": "Palovni olasanmi? Kecha ko\u02bbrding."}}]}
        )
        self.assertTrue(warned[0]["pass"])
        self.assertTrue(warned[0]["detail"]["warning"])
        endings = {hit["ending"] for hit in warned[0]["detail"]["hits"]}
        self.assertEqual(endings, {"-sanmi", "-ding"})
        quiet = check_uzbek_address(
            {"scenes": [{"id": "b", "text": {"uz-Latn": "Oxirigacha yetib bora olasizmi? Tanlang."}}]}
        )
        self.assertTrue(quiet[0]["pass"])
        self.assertFalse(quiet[0]["detail"]["warning"])
        listed = load_voice_config()["languages"]["uz-Latn"]["address"]["senFormWarnings"]
        self.assertEqual(listed, ["-san", "-sanmi", "-ding", "-ngmi"])

    def test_flow_provider_name_is_still_refused_on_an_asset(self) -> None:
        doc = load_fixture()
        doc["assets"][0]["source"]["provider"] = "flow"
        with self.assertRaises(ManifestError):
            validate_manifest(doc, None)
        doc = load_fixture()
        doc["assets"][0]["source"]["provider"] = "fal"
        with self.assertRaises(ManifestError):
            validate_manifest(doc, None)

    def test_fixture_exports_landscape_and_two_portrait_shorts(self) -> None:
        out = LONGFORM / "out"
        packet_path = out / "packet-flow.json"
        out.mkdir(parents=True, exist_ok=True)
        packet = plan_packet(
            "packet-flow",
            "scene-flow",
            "Offline test pattern standing in for a manual Flow export.",
            8,
            "synthetic-stand-in",
            ["flow-pattern"],
        )
        stand_in = out / "stand-in.mp4"
        label = stand_in.with_suffix(".label.txt")
        labeled = label.is_file() and label.read_text(encoding="utf-8") == STAND_IN_LABEL
        if not labeled:
            write_synthetic_stand_in(packet, stand_in)
            if packet_path.is_file():
                packet_path.unlink()
        if not packet_path.is_file():
            approved = approve(
                import_export(
                    mark_ready_to_import(release_for_manual(packet)),
                    stand_in,
                    {
                        "tool": "ffmpeg-testsrc",
                        "origin": "synthetic stand-in for a manual Flow export",
                        "note": "Not a Flow render. Not fal. Not TTS.",
                    },
                    out / "flow-cache",
                )
            )
            packet_path.write_text(json.dumps(approved, indent=2) + "\n", encoding="utf-8")
        first = run_longform(LONGFORM / "manifest.json", out, packet_path)
        self.assertTrue(first["qaPass"])
        self.assertFalse(first["flow"]["regenerated"])
        second = run_longform(LONGFORM / "manifest.json", out, packet_path)
        self.assertTrue(all(row["action"] == "skipped" for row in second["scenes"]))
        self.assertTrue(all(row["action"] == "skipped" for row in second["assembled"]))
        hook = LONGFORM / "audio" / "hook-tone.wav"
        hook.parent.mkdir(parents=True, exist_ok=True)
        if not hook.is_file():
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
                    "sine=frequency=523:sample_rate=48000:duration=4",
                    "-af",
                    "volume=-18dB",
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(hook),
                ]
            )
        shorts = run_repurpose(LONGFORM / "manifest.json", LONGFORM / "shorts.json", out, packet_path)
        self.assertTrue(shorts["qaPass"])
        self.assertEqual([item["id"] for item in shorts["shorts"]], ["short-pattern", "short-middle"])
        pattern, middle = shorts["shorts"]
        self.assertEqual(pattern["momentSourceHash"], first["flow"]["sourceHash"])
        self.assertEqual(pattern["layout"], DESIGNED_LAYOUT)
        self.assertFalse(pattern["regenerated"])
        self.assertIsNotNone(pattern["hookAudioHash"])
        middle_source = out / "scenes" / "landscape" / "scene-mid.mp4"
        self.assertEqual(middle["layout"], NATIVE_CARD)
        self.assertNotEqual(middle["momentSourceHash"], sha256_file(middle_source))
        self.assertFalse(middle["regenerated"])
        self.assertGreater(_max_dark_row(out / "assemble" / "landscape.mp4", 8.0, 20, 120, 1920, 1080), 0.30)
        self.assertGreater(
            _max_dark_row(out / "shorts" / "short-pattern" / "assemble" / "portrait.mp4", 7.0, 80, 200, 1080, 1920),
            0.30,
        )
        hook_ink = _ink_box(out / "shorts" / "short-middle" / "scenes" / "hook.mp4", 1080, 1920)
        moment_ink = _ink_box(out / "shorts" / "short-middle" / "scenes" / "moment.mp4", 1080, 1920)
        self.assertGreaterEqual(moment_ink[3] - moment_ink[2], (hook_ink[3] - hook_ink[2]) * 0.7)
        for short_id in ("short-pattern", "short-middle"):
            for name in ("hook", "ending") if short_id == "short-pattern" else ("hook", "moment", "ending"):
                box = _ink_box(out / "shorts" / short_id / "scenes" / f"{name}.mp4", 1080, 1920)
                self.assertGreater(box[0], 8, f"{short_id} {name}")
                self.assertLess(box[1], 1080 - 8, f"{short_id} {name}")
        self.assertIsNone(middle["hookAudioHash"])
        again = run_repurpose(LONGFORM / "manifest.json", LONGFORM / "shorts.json", out, packet_path)
        for item in again["shorts"]:
            self.assertTrue(all(row["action"] == "skipped" for row in item["scenes"]))
            self.assertTrue(all(row["action"] == "skipped" for row in item["assembled"]))
        finals = [
            out / "assemble" / "landscape.mp4",
            out / "shorts" / "short-pattern" / "assemble" / "portrait.mp4",
            out / "shorts" / "short-middle" / "assemble" / "portrait.mp4",
        ]
        for path in finals:
            self.assertLess(path.stat().st_size, 15 * 1024 * 1024, path)
            info = json.loads(
                subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                    text=True,
                )
            )
            video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
            audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
            self.assertEqual(video["avg_frame_rate"], "30/1")
            self.assertIsNotNone(audio)
            if "landscape" in path.name:
                self.assertEqual(video["width"], 1920)
                self.assertEqual(video["height"], 1080)
            else:
                self.assertEqual(video["width"], 1080)
                self.assertEqual(video["height"], 1920)

    def test_portrait_pad_fails_and_fixture_apostrophes_are_u02bb(self) -> None:
        manifest = {"scenes": [{"id": "only", "durationSec": 0.4}], "transitions": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            padded = root / "pad.mp4"
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
                    "color=c=0x1e2a3a:s=270x480:r=30:d=0.4",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=270x152:rate=30:duration=0.4",
                    "-filter_complex",
                    "[0:v][1:v]overlay=(W-w)/2:(H-h)/2",
                    "-frames:v",
                    "12",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(padded),
                ]
            )
            card = root / "card.mp4"
            render_card(card, 270, 480, 0.4, "O\u02bbrta sahna. Alohida turadi.")
            wide = root / "wide.mp4"
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
                    "testsrc2=size=480x270:rate=30:duration=0.4",
                    "-frames:v",
                    "12",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(wide),
                ]
            )
            laid = root / "laid.mp4"
            portrait_layout(wide, laid, 270, 480, 0.4, 30, {"x": 0.5, "y": 0.5}, "Naqsh. Qo\u02bblda olingan tasvir.")
            solid = root / "solid.mp4"
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
                    "color=c=0x1e2a3a:s=270x480:r=30:d=0.4",
                    "-frames:v",
                    "12",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(solid),
                ]
            )
            pad_check = check_portrait_fill(padded, 270, 480, manifest)
            self.assertFalse(pad_check[0]["pass"], pad_check[0])
            self.assertLess(pad_check[0]["detail"]["contentArea"], 0.70)
            for path in (card, laid, solid):
                result = check_portrait_fill(path, 270, 480, manifest)
                self.assertTrue(result[0]["pass"], result[0])
        bad = check_uzbek_apostrophes({"scenes": [{"id": "bad", "text": {"uz-Latn": "O'zbekcha O'rta o'zi"}}]})
        self.assertFalse(bad[0]["pass"])
        self.assertEqual(bad[0]["detail"]["marks"], [
            {"letters": "O'", "mark": "U+0027"},
            {"letters": "O'", "mark": "U+0027"},
            {"letters": "o'", "mark": "U+0027"},
        ])
        good = check_uzbek_apostrophes(
            {"scenes": [{"id": "good", "text": {"uz-Latn": "O\u02bbzbekcha O\u02bbrta o\u02bbzi"}}]}
        )
        self.assertTrue(good[0]["pass"])
        self.assertEqual([item["mark"] for item in good[0]["detail"]["marks"]], ["U+02BB", "U+02BB", "U+02BB"])
        held = check_uzbek_apostrophes(load_fixture())
        self.assertTrue(all(item["pass"] for item in held))
        hold_marks = [mark["mark"] for item in held for mark in item["detail"]["marks"]]
        self.assertIn("U+02BB", hold_marks)
        self.assertIn("U+2018", hold_marks)
        self.assertNotIn("U+0027", hold_marks)
        fixture_marks = []
        for path in (
            LONGFORM / "manifest.json",
            LONGFORM / "timing" / "narration-main.json",
            LONGFORM / "shorts.json",
        ):
            fixture_marks.extend(_og_marks(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False)))
        self.assertTrue(fixture_marks)
        self.assertTrue(all(mark == "U+02BB" for mark in fixture_marks))


def _og_marks(text: str) -> list[str]:
    found = []
    for index, char in enumerate(text[:-1]):
        nxt = text[index + 1]
        if char in "OoGg" and nxt in "\u0027\u02bb\u2018\u2019\u02bc":
            found.append(f"U+{ord(nxt):04X}")
    return found


def _ink_box(path: Path, width: int, height: int) -> tuple[int, int, int, int]:
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
    )
    min_x, max_x, min_y, max_y = width, -1, height, -1
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            index = (y * width + x) * 3
            if raw[index] > 200 and raw[index + 1] > 200 and raw[index + 2] > 200:
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    return min_x, max_x, min_y, max_y


def _max_dark_row(path: Path, time_sec: float, y0: int, y1: int, width: int, height: int) -> float:
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ss",
            f"{time_sec:.3f}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
    )
    peak = 0.0
    for y in range(y0, y1):
        dark = 0
        total = 0
        for x in range(0, width, 4):
            index = (y * width + x) * 3
            luma = 0.2126 * raw[index] + 0.7152 * raw[index + 1] + 0.0722 * raw[index + 2]
            total += 1
            if luma < 40:
                dark += 1
        if total:
            peak = max(peak, dark / total)
    return peak
