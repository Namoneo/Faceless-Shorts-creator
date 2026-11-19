"""Full-resolution offline fixture. Synthetic sine plus Remotion. Not a narrated pilot."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.foundation.support import FIXTURE
from tools.foundation.ffmpeg import duration_seconds, probe, run, stream_of
from tools.foundation.render import run_episode

OUT = FIXTURE / "out"


class RemotionFixtureTests(unittest.TestCase):
    def test_dual_format_render_skip_invalidation_and_resume(self) -> None:
        if OUT.exists():
            shutil.rmtree(OUT)
        manifest = FIXTURE / "manifest.json"
        first = run_episode(manifest, OUT)
        self.assertTrue(first["qaPass"])
        self.assertTrue(all(row["action"] == "rendered" for row in first["scenes"]))
        self._shared_assets(first)

        second = run_episode(manifest, OUT)
        self.assertTrue(all(row["action"] == "skipped" for row in second["scenes"]))
        self.assertEqual(second["assets"][0]["action"], "skipped")
        self.assertTrue(all(item["action"] == "skipped" for item in second["assembled"]))

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        copied = root / "foundation"
        shutil.copytree(FIXTURE, copied)
        copied_manifest = copied / "manifest.json"
        doc = json.loads(copied_manifest.read_text(encoding="utf-8"))
        doc["scenes"][1]["focal"]["y"] = 0.62
        copied_manifest.write_text(json.dumps(doc), encoding="utf-8")
        changed = run_episode(copied_manifest, copied / "out")
        actions = {(row["formatId"], row["sceneId"]): row["action"] for row in changed["scenes"]}
        self.assertEqual(actions[("landscape", "scene-hook")], "skipped")
        self.assertEqual(actions[("portrait", "scene-hook")], "skipped")
        self.assertEqual(actions[("landscape", "scene-hold")], "rendered")
        self.assertEqual(actions[("portrait", "scene-hold")], "rendered")
        self.assertEqual(changed["assets"][0]["canonicalContentHash"], first["assets"][0]["canonicalContentHash"])
        self.assertEqual(changed["assets"][1]["contentHash"], first["assets"][1]["contentHash"])

        portrait_hook = copied / "out" / "scenes" / "portrait" / "scene-hook.mp4"
        portrait_hook.unlink()
        resumed = run_episode(copied_manifest, copied / "out")
        resumed_actions = {(row["formatId"], row["sceneId"]): row["action"] for row in resumed["scenes"]}
        self.assertEqual(resumed_actions[("portrait", "scene-hook")], "rendered")
        self.assertEqual(resumed_actions[("landscape", "scene-hook")], "skipped")
        self.assertEqual(resumed_actions[("landscape", "scene-hold")], "skipped")
        self.assertEqual(resumed_actions[("portrait", "scene-hold")], "skipped")

        probes = {}
        for fmt, size in (("landscape", (1920, 1080)), ("portrait", (1080, 1920))):
            path = OUT / "assemble" / f"{fmt}.mp4"
            info = probe(path)
            video = stream_of(info, "video")
            audio = stream_of(info, "audio")
            self.assertIsNotNone(video)
            self.assertIsNotNone(audio)
            assert video is not None and audio is not None
            self.assertEqual((video.get("width"), video.get("height")), size)
            self.assertEqual(video.get("codec_name"), "h264")
            self.assertEqual(audio.get("codec_name"), "aac")
            rate = video.get("avg_frame_rate")
            self.assertEqual(rate, "30/1")
            duration = duration_seconds(info)
            self.assertIsNotNone(duration)
            assert duration is not None
            self.assertLessEqual(abs(duration - 4.0), 2 / 30)
            self.assertLess(path.stat().st_size, 15 * 1024 * 1024)
            probes[fmt] = {
                "path": str(path),
                "width": video.get("width"),
                "height": video.get("height"),
                "fps": rate,
                "videoCodec": video.get("codec_name"),
                "audioCodec": audio.get("codec_name"),
                "durationSec": duration,
                "bytes": path.stat().st_size,
            }
            self._frame(path, 1.0, OUT / "frames" / f"{fmt}-scene-hook.png")
            self._frame(path, 3.0, OUT / "frames" / f"{fmt}-scene-hold.png")
        (OUT / "probe.json").write_text(json.dumps(probes, indent=2) + "\n", encoding="utf-8")
        (OUT / "cache-evidence.json").write_text(
            json.dumps(
                {
                    "firstScenes": first["scenes"],
                    "secondScenes": second["scenes"],
                    "focalChange": changed["scenes"],
                    "resume": resumed["scenes"],
                    "narrationHash": first["assets"][0]["canonicalContentHash"],
                    "visualHash": first["assets"][1]["contentHash"],
                    "narrationHashAfterFocal": changed["assets"][0]["canonicalContentHash"],
                    "visualHashAfterFocal": changed["assets"][1]["contentHash"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _shared_assets(self, report: dict) -> None:
        narration = report["assets"][0]["canonicalContentHash"]
        visual = report["assets"][1]["contentHash"]
        for row in report["scenes"]:
            self.assertEqual(row["dependencies"]["narration"], narration)
            self.assertEqual(row["dependencies"]["visual"], visual)

    def _frame(self, source: Path, time_sec: float, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-v",
                "error",
                "-ss",
                f"{time_sec:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                str(dest),
            ]
        )
