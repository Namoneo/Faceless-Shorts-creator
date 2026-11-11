"""Manifest, timing, and offline-provider checks."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.foundation.support import FIXTURE, HOLD, HOOK, REPO, copy_fixture, load_fixture
from tools.foundation.cache import scene_job
from tools.foundation.layout import caption_overflow
from tools.foundation.manifest import ManifestError, load_manifest, timeline, validate_manifest
from tools.foundation.qa import check_captions
from tools.foundation.render import _props
from tools.foundation.timing import TimingError, display_words, line_for_scene, load_timing, resolve_timing

HASH_A = "a" * 64
HASH_B = "b" * 64


class ManifestTests(unittest.TestCase):
    def test_fixture_accepts_both_ratios_and_languages(self) -> None:
        doc = validate_manifest(load_fixture(), FIXTURE)
        self.assertEqual(doc["fps"], 30)
        self.assertEqual({item["id"] for item in doc["formats"]}, {"landscape", "portrait"})
        self.assertEqual(doc["formats"][0]["width"], 1920)
        self.assertEqual(doc["formats"][1]["height"], 1920)
        self.assertIn("\u02bb", doc["scenes"][0]["text"]["uz-Latn"])
        self.assertIn("\u2018", doc["scenes"][1]["text"]["uz-Latn"])
        self.assertNotEqual(doc["scenes"][0]["text"]["uz-Latn"], doc["scenes"][0]["text"]["en"])

    def test_fixture_captions_fit_and_a_long_line_does_not(self) -> None:
        doc = load_fixture()
        for scene in doc["scenes"]:
            for fmt in doc["formats"]:
                box = caption_overflow(scene["text"]["uz-Latn"], fmt["width"], fmt["height"], scene["safe"], scene["caption"])
                self.assertFalse(box["overflow"], box)
        long = "O\u02bbzbekcha " * 12
        box = caption_overflow(long, 1080, 1920, doc["scenes"][0]["safe"], doc["scenes"][0]["caption"])
        self.assertTrue(box["overflow"])
        overflowing = json.loads(json.dumps(doc))
        overflowing["scenes"][0]["text"]["uz-Latn"] = long
        blocked = check_captions(overflowing)
        self.assertTrue(any(item["detail"].get("detected") and not item["pass"] for item in blocked))
        overflowing["qa"]["allowCaptionOverflow"] = True
        allowed = check_captions(overflowing)
        self.assertTrue(all(item["pass"] for item in allowed))
        self.assertTrue(any(item["detail"].get("detected") and item["detail"].get("allowed") for item in allowed))
        self.assertTrue(all(item["detail"].get("verified") for item in allowed))

    def test_wide_text_fails_geometry_when_the_estimate_fits(self) -> None:
        doc = load_fixture()
        doc["scenes"][0]["text"]["uz-Latn"] = "W" * 17
        doc["scenes"][0]["caption"] = {"fontScale": 0.045, "maxLines": 1}
        estimate = caption_overflow(
            "W" * 17,
            1080,
            1920,
            doc["scenes"][0]["safe"],
            doc["scenes"][0]["caption"],
        )
        self.assertFalse(estimate["overflow"], estimate)
        items = check_captions(doc)
        portrait = next(item for item in items if item["detail"]["formatId"] == "portrait" and item["detail"]["sceneId"] == "scene-hook")
        self.assertTrue(portrait["detail"]["verified"])
        self.assertTrue(portrait["detail"]["detected"])
        self.assertTrue(portrait["detail"]["estimateMissed"])
        self.assertFalse(portrait["pass"])
        doc["qa"]["allowCaptionOverflow"] = True
        allowed = check_captions(doc)
        portrait_ok = next(item for item in allowed if item["detail"]["formatId"] == "portrait" and item["detail"]["sceneId"] == "scene-hook")
        self.assertTrue(portrait_ok["pass"])
        self.assertTrue(portrait_ok["detail"]["detected"])

    def test_both_formats_keep_the_same_apostrophe_codepoints(self) -> None:
        manifest = load_manifest(FIXTURE / "manifest.json")
        timing = resolve_timing(
            load_timing(FIXTURE / "timing" / "narration-main.json"),
            HASH_A,
            {scene["id"] for scene in manifest["scenes"]},
        )
        props = _props(manifest, timeline(manifest), timing)
        hook = props["scenes"][0]["text"]
        hold = props["scenes"][1]["text"]
        self.assertEqual(hook, HOOK)
        self.assertEqual(hold, HOLD)
        self.assertEqual([ord(ch) for ch in hook if ord(ch) > 127], [0x02BB, 0x02BB])
        self.assertEqual([ord(ch) for ch in hold if ord(ch) > 127], [0x2018, 0x2018, 0x2018])
        self.assertNotIn("'", hook)
        self.assertNotIn("\u2019", hook)
        seen: dict[str, set[str]] = {}
        for fmt in manifest["formats"]:
            for scene, row in zip(manifest["scenes"], timeline(manifest)):
                line = line_for_scene(timing, scene["id"])
                self.assertIsNotNone(line)
                assert line is not None
                job = scene_job(manifest, scene, fmt, row, line, "n" * 64, "v" * 64, Path("scene.mp4"))
                seen.setdefault(scene["id"], set()).add(job["parameter"]["text"])
        self.assertEqual(seen["scene-hook"], {HOOK})
        self.assertEqual(seen["scene-hold"], {HOLD})

    def test_rejects_generation_providers_and_bad_shapes(self) -> None:
        for provider in ("elevenlabs", "fal", "fal.ai", "gemini", "voicestudio", "flow", "openai"):
            doc = load_fixture()
            doc["assets"][0]["source"]["provider"] = provider
            with self.assertRaises(ManifestError):
                validate_manifest(doc, None)
        cases = []
        fps = load_fixture()
        fps["fps"] = 24
        cases.append(fps)
        ratio = load_fixture()
        ratio["formats"][0]["width"] = 1280
        cases.append(ratio)
        duration = load_fixture()
        duration["episodeDurationSec"] = 9
        cases.append(duration)
        window = load_fixture()
        window["scenes"][0]["narration"]["outSec"] = 3
        cases.append(window)
        missing = load_fixture()
        missing["scenes"][0]["assetIds"] = ["missing-card"]
        cases.append(missing)
        for doc in cases:
            with self.assertRaises(ManifestError):
                validate_manifest(doc, None)

    def test_rejects_urls_and_paths_outside_the_episode(self) -> None:
        url = load_fixture()
        url["assets"][0]["source"] = {"provider": "import", "role": "narration", "path": "https://example.invalid/a.wav"}
        with self.assertRaises(ManifestError):
            validate_manifest(url, None)
        escape = load_fixture()
        escape["assets"][0]["source"] = {"provider": "import", "role": "narration", "path": "../secret.wav"}
        with self.assertRaises(ManifestError):
            validate_manifest(escape, FIXTURE)

    def test_timing_symlink_outside_the_episode_fails(self) -> None:
        with self._copied() as manifest:
            outside = manifest.parent.parent / "outside-timing.json"
            outside.write_text("{}", encoding="utf-8")
            timing = manifest.parent / "timing" / "narration-main.json"
            timing.unlink()
            timing.symlink_to(outside)
            with self.assertRaises(ManifestError):
                validate_manifest(json.loads(manifest.read_text(encoding="utf-8")), manifest.parent)

    def test_missing_import_file_fails(self) -> None:
        with self._copied() as manifest:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
            doc["assets"][0]["source"] = {"provider": "import", "role": "narration", "path": "audio/missing.wav"}
            manifest.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(ManifestError):
                validate_manifest(doc, manifest.parent)

    def test_studio_defaults_match_the_manifest_display_text(self) -> None:
        defaults = json.loads(
            (REPO / "remotion" / "src" / "shots" / "foundation" / "fixtureDefaults.json").read_text(encoding="utf-8")
        )
        doc = load_fixture()
        self.assertEqual(defaults["language"], doc["language"])
        for scene, view in zip(doc["scenes"], defaults["scenes"]):
            self.assertEqual(view["text"], scene["text"][doc["language"]])
            self.assertEqual(view["line"]["text"], scene["text"][doc["language"]])
            self.assertEqual(view["focal"], scene["focal"])

    def _copied(self) -> _TempCopy:
        return _TempCopy(tempfile.mkdtemp())


class _TempCopy:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def __enter__(self) -> Path:
        return copy_fixture(self.root)

    def __exit__(self, *_args: object) -> None:
        shutil.rmtree(self.root)


class TimingTests(unittest.TestCase):
    def test_uzbek_variants_stay_exact_and_the_correction_is_hash_bound(self) -> None:
        template = json.loads((FIXTURE / "timing" / "narration-main.json").read_text(encoding="utf-8"))
        self.assertEqual(template["lines"][0]["text"], HOOK)
        self.assertEqual(template["lines"][1]["text"], HOLD)
        self.assertEqual([ord(ch) for ch in HOOK if ord(ch) > 127], [0x02BB, 0x02BB])
        self.assertEqual([ord(ch) for ch in HOLD if ord(ch) > 127], [0x2018, 0x2018, 0x2018])
        resolved = resolve_timing(template, HASH_A, {"scene-hook", "scene-hold"})
        hook = resolved["lines"][0]
        self.assertEqual(hook["words"][2]["w"], "O\u02bbzbekcha")
        self.assertEqual(hook["words"][2]["state"], "manual")
        self.assertEqual(display_words(hook), HOOK)
        self.assertEqual(resolved["corrections"][0]["audioContentHash"], HASH_A)
        self.assertTrue(resolved["corrections"][0]["applied"])
        self.assertNotIn("words", resolved["lines"][1])
        self.assertEqual(resolved["lines"][1]["state"], "unaligned")

    def test_stale_hash_demotes_manual_and_skips_a_mismatched_correction(self) -> None:
        template = json.loads((FIXTURE / "timing" / "narration-main.json").read_text(encoding="utf-8"))
        template["audioContentHash"] = HASH_B
        template["lines"][0]["state"] = "manual"
        for word in template["lines"][0]["words"]:
            word["state"] = "manual"
        template["corrections"][0]["audioContentHash"] = HASH_B
        resolved = resolve_timing(template, HASH_A, {"scene-hook", "scene-hold"})
        self.assertTrue(resolved["stale"])
        self.assertEqual(resolved["lines"][0]["state"], "review")
        self.assertEqual(resolved["lines"][0]["words"][2]["w"], "Ozbekcha")
        self.assertFalse(resolved["corrections"][0]["applied"])
        self.assertEqual(resolved["corrections"][0]["skipReason"], "audio-hash-mismatch")

    def test_boolean_times_are_rejected(self) -> None:
        template = json.loads((FIXTURE / "timing" / "narration-main.json").read_text(encoding="utf-8"))
        template["lines"][0]["words"][0]["start"] = True
        template["lines"][0]["words"][0]["end"] = False
        with self.assertRaises(TimingError):
            resolve_timing(template, HASH_A, {"scene-hook", "scene-hold"})

    def test_unaligned_line_cannot_carry_invented_words(self) -> None:
        template = json.loads((FIXTURE / "timing" / "narration-main.json").read_text(encoding="utf-8"))
        template["lines"][1]["words"] = [{"w": "o\u2018g\u2018", "start": 2.1, "end": 2.4, "state": "manual"}]
        with self.assertRaises(TimingError):
            resolve_timing(template, HASH_A, {"scene-hook", "scene-hold"})


class OfflineSourceTests(unittest.TestCase):
    def test_runner_has_no_provider_client(self) -> None:
        banned = ("api.elevenlabs.io", "queue.fal.run", "generativelanguage.googleapis.com", "import gen_voice", "import gen_clip", "import gen_image")
        for path in (REPO / "tools" / "foundation").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text)
            self.assertNotIn("https://", text)
            self.assertNotIn("http://", text)

    def test_generate_command_fails_closed(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.foundation.cli", "generate"],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("offline", result.stdout)

    def test_short2math_source_is_unchanged(self) -> None:
        result = subprocess.run(
            ["git", "diff", "--exit-code", "773054bebbe460de0f31dcfda5315970b1c8b4f2", "--", "remotion/src/shots/short-2/Short2Math.tsx"],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
