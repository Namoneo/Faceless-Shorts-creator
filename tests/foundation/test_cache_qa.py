"""Cache invalidation, resume, and QA fixtures that must fail closed."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.foundation.support import copy_fixture, write_color
from tools.foundation.ffmpeg import volume_detect
from tools.foundation.hashutil import sha256_file
from tools.foundation.manifest import ManifestError, load_manifest
from tools.foundation.narration import prepare_narration, prepare_one
from tools.foundation.qa import check_black, check_levels, check_media, check_timeline
from tools.foundation.render import RenderFailure, run_episode


class CacheTests(unittest.TestCase):
    def test_repeat_skips_focal_change_rerenders_one_scene_and_failure_resumes(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        manifest = copy_fixture(root)
        out = root / "out"
        state = {"fail_after": 1, "calls": 0}

        def flaky(jobs: list[dict[str, Any]]) -> None:
            state["calls"] += 1
            for index, job in enumerate(jobs):
                if index >= state["fail_after"]:
                    raise RuntimeError("renderer stopped")
                write_color(job)

        with self.assertRaises(RenderFailure) as caught:
            run_episode(manifest, out, renderer=flaky)
        self.assertTrue(caught.exception.completed)

        def finish(jobs: list[dict[str, Any]]) -> None:
            state["calls"] += 1
            for job in jobs:
                write_color(job)

        first = run_episode(manifest, out, renderer=finish)
        self.assertTrue(first["qaPass"])
        self.assertIn("rendered", {row["action"] for row in first["scenes"]})
        self.assertIn("skipped", {row["action"] for row in first["scenes"]})
        narration = next(item for item in first["assets"] if item["kind"] == "narration")
        visual = next(item for item in first["assets"] if item["kind"] == "visual")
        for row in first["scenes"]:
            self.assertEqual(row["dependencies"]["narration"], narration["canonicalContentHash"])
            self.assertEqual(row["dependencies"]["visual"], visual["contentHash"])
        landscape = {row["sceneId"]: row["dependencies"]["visual"] for row in first["scenes"] if row["formatId"] == "landscape"}
        portrait = {row["sceneId"]: row["dependencies"]["visual"] for row in first["scenes"] if row["formatId"] == "portrait"}
        self.assertEqual(landscape, portrait)

        second = run_episode(manifest, out, renderer=finish)
        self.assertTrue(all(row["action"] == "skipped" for row in second["scenes"]))
        self.assertEqual(second["assets"][0]["action"], "skipped")
        self.assertTrue(all(item["action"] == "skipped" for item in second["assembled"]))
        calls_after_repeat = state["calls"]

        doc = json.loads(manifest.read_text(encoding="utf-8"))
        doc["scenes"][1]["focal"]["y"] = 0.62
        manifest.write_text(json.dumps(doc), encoding="utf-8")
        changed = run_episode(manifest, out, renderer=finish)
        by_scene = {(row["formatId"], row["sceneId"]): row["action"] for row in changed["scenes"]}
        self.assertEqual(by_scene[("landscape", "scene-hook")], "skipped")
        self.assertEqual(by_scene[("portrait", "scene-hook")], "skipped")
        self.assertEqual(by_scene[("landscape", "scene-hold")], "rendered")
        self.assertEqual(by_scene[("portrait", "scene-hold")], "rendered")
        self.assertEqual(changed["assets"][0]["action"], "skipped")
        self.assertEqual(changed["assets"][0]["canonicalContentHash"], narration["canonicalContentHash"])
        self.assertEqual(changed["assets"][1]["contentHash"], visual["contentHash"])
        self.assertGreater(state["calls"], calls_after_repeat)

    def test_import_preserves_bytes_and_rejects_a_non_audio_file(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        manifest = copy_fixture(root)
        wav = root / "tone.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=4",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(wav),
            ],
            check=True,
        )
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        doc["assets"][0]["source"] = {"provider": "import", "role": "user-file", "path": "tone.wav"}
        source = manifest.parent / "tone.wav"
        shutil.copy2(wav, source)
        original = source.read_bytes()
        record = prepare_one(doc["assets"][0], manifest.parent, root / "import-out")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(Path(record["preservedPath"]).read_bytes(), original)
        bogus = manifest.parent / "notes.txt"
        bogus.write_text("not audio", encoding="utf-8")
        doc["assets"][0]["source"]["path"] = "notes.txt"
        with self.assertRaises(ManifestError):
            prepare_one(doc["assets"][0], manifest.parent, root / "import-bad")

    def test_replaced_sine_bytes_are_rebuilt(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        manifest = copy_fixture(root)
        out = root / "out"
        prepare_narration(load_manifest(manifest), manifest.parent, out)
        wav = out / "sources" / "narration-main" / "synthetic-sine.wav"
        mutated = bytearray(wav.read_bytes())
        mutated[80] ^= 0xFF
        wav.write_bytes(bytes(mutated))
        mutated_hash = sha256_file(wav)
        prepare_narration(load_manifest(manifest), manifest.parent, out)
        self.assertNotEqual(sha256_file(wav), mutated_hash)
        stored = json.loads(wav.with_suffix(".spec.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["contentHash"], sha256_file(wav))

    def test_failed_rerender_does_not_accept_the_old_picture(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        manifest = copy_fixture(root)
        out = root / "out"

        def finish(jobs: list[dict[str, Any]]) -> None:
            for job in jobs:
                write_color(job)

        run_episode(manifest, out, renderer=finish)
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        doc["scenes"][1]["focal"]["y"] = 0.7
        manifest.write_text(json.dumps(doc), encoding="utf-8")

        def explode(jobs: list[dict[str, Any]]) -> None:
            raise RuntimeError("renderer stopped")

        with self.assertRaises(RenderFailure):
            run_episode(manifest, out, renderer=explode)
        again = run_episode(manifest, out, renderer=finish)
        actions = {(row["formatId"], row["sceneId"]): row["action"] for row in again["scenes"]}
        self.assertEqual(actions[("landscape", "scene-hook")], "skipped")
        self.assertEqual(actions[("portrait", "scene-hook")], "skipped")
        self.assertEqual(actions[("landscape", "scene-hold")], "rendered")
        self.assertEqual(actions[("portrait", "scene-hold")], "rendered")


class QaNegativeTests(unittest.TestCase):
    def test_black_silence_and_clipping_fail_unless_the_allowance_is_explicit(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        black = root / "black.mp4"
        silent = root / "silent.wav"
        clipped = root / "clipped.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30", "-frames:v", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(black)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.3", str(silent)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.3,volume=20dB", "-c:a", "pcm_s16le", str(clipped)],
            check=True,
        )
        blocked = check_black(black, 0.05, allow_black=False, label="black")
        allowed = check_black(black, 0.05, allow_black=True, label="black")
        self.assertFalse(blocked["pass"])
        self.assertTrue(blocked["detail"]["detected"])
        self.assertTrue(allowed["pass"])
        self.assertTrue(allowed["detail"]["detected"])
        silent_checks = check_levels(silent, allow_silence=False, allow_clipping=False, label="silent")
        self.assertFalse(next(item for item in silent_checks if item["id"] == "silence")["pass"])
        allowed_silence = next(item for item in check_levels(silent, True, False, "silent") if item["id"] == "silence")
        self.assertTrue(allowed_silence["pass"])
        self.assertTrue(allowed_silence["detail"]["detected"])
        self.assertTrue(allowed_silence["detail"]["allowed"])
        clip_checks = check_levels(clipped, allow_silence=False, allow_clipping=False, label="clip")
        clip = next(item for item in clip_checks if item["id"] == "clipping")
        self.assertFalse(clip["pass"])
        self.assertTrue(clip["detail"]["detected"])
        allowed_clip = next(item for item in check_levels(clipped, False, True, "clip") if item["id"] == "clipping")
        self.assertTrue(allowed_clip["pass"])
        self.assertTrue(allowed_clip["detail"]["detected"])
        self.assertTrue(allowed_clip["detail"]["allowed"])
        self.assertGreater(volume_detect(clipped)["max_db"], -0.5)

    def test_wrong_ratio_fps_and_duration_fail(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        wrong = root / "wrong.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x1e2a3a:s=640x360:r=24:d=0.5",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=0.5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "24",
                "-c:a",
                "aac",
                "-shortest",
                str(wrong),
            ],
            check=True,
        )
        manifest = {
            "fps": 30,
            "episodeDurationSec": 4,
            "qa": {"allowBlack": False, "allowSilence": False, "allowClipping": False, "allowCaptionOverflow": False},
            "scenes": [{"id": "scene-hook", "durationSec": 4}],
            "transitions": [],
        }
        fmt = {"id": "landscape", "width": 1920, "height": 1080}
        checks = {item["id"]: item for item in check_media(wrong, fmt, manifest)}
        self.assertFalse(checks["ratio"]["pass"])
        self.assertFalse(checks["fps"]["pass"])
        self.assertFalse(checks["duration"]["pass"])
        timeline = check_timeline({"episodeDurationSec": 9, "fps": 30, "scenes": [{"id": "scene-hook", "durationSec": 2}, {"id": "scene-hold", "durationSec": 2}], "transitions": [{"after": "scene-hook", "durationSec": 0}]})
        self.assertFalse(timeline[0]["pass"])
