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
from tools.foundation.ffmpeg import frame_luma, volume_detect
from tools.foundation.hashutil import sha256_file
from tools.foundation.manifest import ManifestError, load_manifest
from tools.foundation.narration import prepare_narration, prepare_one
from tools.foundation.pilot import countdown_beats, digit_at, _pack_short
from tools.foundation.qa import (
    check_black_span,
    check_caption_straight_apostrophe,
    check_distinct_plates,
    check_levels,
    check_media,
    check_timeline,
)
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
        blocked = check_black_span(black, 0.0, 0.3, allow_black=False, label="black")
        allowed = check_black_span(black, 0.0, 0.3, allow_black=True, label="black")
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

    def test_black_away_from_the_midpoint_fails_until_allowed(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        path = root / "transient.mp4"
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
                "color=c=0x1e2a3a:s=320x180:r=30:d=2",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=30:d=2",
                "-filter_complex",
                "[0][1]overlay=enable='between(n,3,6)'",
                "-frames:v",
                "60",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )
        midpoint = frame_luma(path, 1.0)
        self.assertIsNotNone(midpoint)
        assert midpoint is not None
        self.assertGreaterEqual(midpoint, 20.0)
        blocked = check_black_span(path, 0.0, 2.0, allow_black=False, label="scene")
        self.assertFalse(blocked["pass"])
        self.assertTrue(blocked["detail"]["detected"])
        self.assertLess(blocked["detail"]["intervals"][0]["end"], 0.5)
        allowed = check_black_span(path, 0.0, 2.0, allow_black=True, label="scene")
        self.assertTrue(allowed["pass"])
        self.assertTrue(allowed["detail"]["detected"])
        self.assertTrue(allowed["detail"]["allowed"])

    def test_silence_inside_a_scene_allows_a_breath_and_fails_a_longer_hole(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        breath = root / "breath.mp4"
        self._silence_clip(breath, 2, 0.40, 0.70)
        manifest = {
            "fps": 30,
            "episodeDurationSec": 2,
            "qa": {"allowBlack": False, "allowSilence": False, "allowClipping": False, "allowCaptionOverflow": False},
            "scenes": [{"id": "scene-hook", "durationSec": 2}],
            "transitions": [],
        }
        fmt = {"id": "landscape", "width": 320, "height": 180}
        short_gap = [item for item in check_media(breath, fmt, manifest) if item["id"] == "scene-silence"]
        self.assertTrue(short_gap)
        self.assertTrue(short_gap[0]["pass"])
        self.assertFalse(short_gap[0]["detail"]["detected"])
        self.assertEqual(short_gap[0]["detail"]["toleranceSec"], 1.0)
        self.assertTrue(short_gap[0]["detail"]["gaps"])
        self.assertGreater(volume_detect(breath)["max_db"], -45)

        hole = root / "hole.mp4"
        self._silence_clip(hole, 2, 0.20, 1.60)
        long_gap = [item for item in check_media(hole, fmt, manifest) if item["id"] == "scene-silence"]
        self.assertFalse(long_gap[0]["pass"])
        self.assertTrue(long_gap[0]["detail"]["detected"])
        self.assertGreater(long_gap[0]["detail"]["intervals"][0]["overlapSec"], 1.0)

        manifest["scenes"][0]["qa"] = {"silenceGapSec": 2.5}
        held = [item for item in check_media(hole, fmt, manifest) if item["id"] == "scene-silence"]
        self.assertTrue(held[0]["pass"])
        self.assertFalse(held[0]["detail"]["detected"])
        self.assertEqual(held[0]["detail"]["toleranceSec"], 2.5)

        manifest["scenes"][0]["qa"] = {}
        manifest["qa"]["allowSilence"] = True
        allowed = [item for item in check_media(hole, fmt, manifest) if item["id"] == "scene-silence"]
        self.assertTrue(allowed[0]["pass"])
        self.assertTrue(allowed[0]["detail"]["detected"])
        self.assertTrue(allowed[0]["detail"]["allowed"])

    def _silence_clip(self, path: Path, seconds: float, gap_start: float, gap_end: float) -> None:
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
                f"color=c=0x1e2a3a:s=320x180:r=30:d={seconds}",
                "-f",
                "lavfi",
                "-i",
                (
                    f"sine=frequency=440:sample_rate=48000:duration={seconds},"
                    f"volume=volume='if(between(t,{gap_start},{gap_end}),0,1)':eval=frame"
                ),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(path),
            ],
            check=True,
        )

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


class EpisodeReviewTests(unittest.TestCase):
    def test_countdown_shows_one_through_the_hold(self) -> None:
        beats = countdown_beats(0.0, 3.32, 5.32)
        self.assertEqual([item["digit"] for item in beats], ["5", "4", "3", "2", "1"])
        self.assertEqual(digit_at(beats, 3.1), "1")
        self.assertEqual(digit_at(beats, 4.8), "1")
        self.assertIsNone(digit_at(beats, 5.32))
        self.assertNotIn("YOKI", [item["digit"] for item in beats])

    def test_caption_straight_apostrophe_fails_and_okina_passes(self) -> None:
        straight = check_caption_straight_apostrophe(
            {"scenes": [{"id": "bad", "text": {"uz-Latn": "o'zi"}, "captionCues": [{"text": "o'zi"}]}]}
        )
        self.assertFalse(straight[0]["pass"])
        kept = check_caption_straight_apostrophe(
            {
                "scenes": [
                    {
                        "id": "good",
                        "text": {"uz-Latn": "o\u02bbzi An\u02bcanaviy"},
                        "captionCues": [{"text": "o\u02bbzi"}, {"text": "An\u02bcanaviy"}],
                    }
                ]
            }
        )
        self.assertTrue(kept[0]["pass"])

    def test_choice_pair_must_use_two_hashes(self) -> None:
        same = check_distinct_plates(
            {"scenes": [{"id": "r3-choice", "plates": [{"id": "a", "sha256": "abc"}, {"id": "b", "sha256": "abc"}]}]}
        )
        self.assertFalse(same[0]["pass"])
        different = check_distinct_plates(
            {"scenes": [{"id": "r3-choice", "plates": [{"id": "a", "sha256": "abc"}, {"id": "b", "sha256": "def"}]}]}
        )
        self.assertTrue(different[0]["pass"])

    def test_short_trims_pauses_and_keeps_a_lead_in(self) -> None:
        scenes = [
            {
                "id": "r1-choice",
                "role": "choice",
                "start": 10.0,
                "end": 40.0,
                "duration": 30.0,
                "text": "Savol.",
                "cues": [{"text": "Savol.", "start": 0.0, "end": 30.0}],
                "countdown": None,
                "plates": [],
                "round": 1,
            },
            {
                "id": "r1-count",
                "role": "countdown",
                "start": 40.0,
                "end": 47.0,
                "duration": 7.0,
                "text": "Besh. Toʻrt. Uch. Ikki. Bir. Tanlovingiz qabul qilindi.",
                "cues": [{"text": "Tanlovingiz qabul qilindi.", "start": 5.0, "end": 6.2}],
                "countdown": {"start": 0.0, "end": 3.0},
                "plates": [],
                "round": 1,
                "allowSilence": True,
            },
            {
                "id": "r1-fact",
                "role": "reveal",
                "start": 47.0,
                "end": 80.0,
                "duration": 33.0,
                "text": "Fakt.",
                "cues": [{"text": "Fakt.", "start": 0.35, "end": 18.0}],
                "countdown": None,
                "plates": [],
                "round": 1,
            },
        ]
        local, pieces, duration = _pack_short(scenes, 10.0, 65.0)
        self.assertLessEqual(duration, 55.0)
        self.assertGreater(duration, 30.0)
        self.assertAlmostEqual(pieces[0][1] - pieces[0][0], 0.3, places=2)
        self.assertEqual(local[0]["cues"][0]["text"], "Savol.")
        self.assertEqual(local[-1]["cues"][-1]["text"], "Fakt.")
        count = next(item for item in local if item["role"] == "countdown")
        self.assertEqual(digit_at(count["countdownBeats"], count["countdown"]["end"] - 0.05), "1")
        self.assertEqual(digit_at(count["countdownBeats"], count["countdown"]["end"] + 0.05), "1")
