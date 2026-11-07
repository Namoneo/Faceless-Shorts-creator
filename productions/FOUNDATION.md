# Offline episode foundation

This is a foundation for shared episode assets, imported narration, dual-format Remotion renders, and QA. It is not production narration, not an Uzbek aligner, and not the Qani, tanla! pilot.

The new runner never calls ElevenLabs, fal, Gemini, VoiceStudio, Flow, or any other generator. Those older tools are still in `tools/` for the existing shorts. `python3 -m tools.foundation.cli generate` exits 2.

## What a run does

`productions/fixtures/foundation/manifest.json` is the runnable example. Its narration is a 440 Hz ffmpeg sine at -18 dB, labeled synthetic. It is not TTS and not VoiceStudio. The picture is the `FoundationEpisode` component, drawn separately at 1920x1080 and 1080x1920, 30 fps, from the same scene list. It is not a crop of one finished movie.

Stable ids: `fixture-foundation`, `narration-main`, `card-title`, `scene-hook`, `scene-hold`.

Each narration asset keeps the original bytes under `out/originals/<id>/<sha256>.wav`. The canonical derivative is one loudnorm pass (`I=-16:TP=-1.5:LRA=11`), 48 kHz, stereo, `pcm_s16le`. A repeat skips that work when the source hash, the parameter hash, and the canonical file hash still match.

Timing lives beside the episode, not inside the audio file. States are `unaligned`, `review`, and `manual`. Unaligned lines have no word times. Review and manual lines need explicit start and end. A correction replaces the display token only and is stamped with the canonical audio hash. A different hash does not apply it, and a stale manual line drops back to review. Nothing in this runner invents word times. Display text is stored as exact code points. `Oʻzbekcha` uses U+02BB. `o‘g‘` uses U+2018. They are not folded together.

Scene renders skip when the scene parameter hash, the narration hash, the visual hash, the timing hash, and the output file hash all match. The visual hash covers the component source and the render script, so an edit to either invalidates the pictures. A focal or text change re-renders the affected scenes only. The narration file stays. Assembly is an ffmpeg concat of the scene videos plus the canonical WAV as AAC. A missing scene file is rendered again. A failed render does not treat the previous picture as the new one: a sidecar is written only when that attempt replaced the file.

## Commands

From the repo root, with `remotion/node_modules` installed (`npm ci --ignore-scripts` inside `remotion/`):

```bash
python3 -m tools.foundation.cli import \
  --manifest productions/fixtures/foundation/manifest.json \
  --out productions/fixtures/foundation/out

python3 -m tools.foundation.cli render \
  --manifest productions/fixtures/foundation/manifest.json \
  --out productions/fixtures/foundation/out

python3 -m tools.foundation.cli qa \
  --manifest productions/fixtures/foundation/manifest.json \
  --out productions/fixtures/foundation/out

python3 -m tools.foundation.cli run \
  --manifest productions/fixtures/foundation/manifest.json \
  --out productions/fixtures/foundation/out
```

`import` only prepares narration. `render`, `qa`, and `run` all prepare, render misses, assemble, and QA. There is no separate resume command: run the same command again. Finished scenes are skipped. `generate` is refused.

Outputs stay in `productions/fixtures/foundation/out/` and are gitignored. `report.json` and `qa.json` are the machine-readable results.

## Language and layout

`supportedLanguages` must include `uz-Latn`, `en`, and `nl`. The active `language` picks the caption string. Landscape and portrait use the same fractions for focal point and safe margins, applied to that format's own width and height. Captions use DejaVu Sans from `media/fonts/` so U+02BB and U+2018 are drawn without a Latin-only webfont. The existing 14 shorts are unchanged and still use their own fonts.

`npm run gen`, `npm run studio`, and `npm run render` call `scripts/gen-registry.mjs`, which sorts every shot file and rewrites `registry.gen.tsx`. That would reorder the current 14 compositions. The two foundation entries were appended by hand. Do not run those three scripts as part of this fixture. `remotion/scripts/render-foundation.mjs` does not regenerate the registry.

## QA limits

These checks ran on the fixture. They do not prove pronunciation or Uzbek alignment.

- Black: one `signalstats` YAVG sample at each scene midpoint. Below 20 fails. Limited-range black measures about 16, and the fixture background measures about 51. This is not every frame.
- Silence: `volumedetect` on each scene window and clipping on the whole file. Silence is max volume at or below -45 dB. A quiet gap shorter than the window can be missed. Clipping is max volume above -0.5 dB.
- `allowBlack`, `allowSilence`, `allowClipping`, and `allowCaptionOverflow` are required booleans. Detection still runs. The report records `detected` and `allowed`. An allowance does not turn the detector off.
- Caption overflow is `ceil(len(text) * fontPx * 0.62 / safeWidth)` against `maxLines`. It is not HarfBuzz and not Chrome line breaking.
- Duration may differ by up to two frames. AAC priming and encoder rounding are the reason.
- Loudnorm is single-pass and depends on the local ffmpeg. The sine hash is stable on one machine. It is not a portable golden file.
- Transitions in this tranche are cuts of zero duration. The timeline must equal `episodeDurationSec` within one frame, and each narration in/out must match its scene.
- Wrong dimensions, a frame rate other than 30, a missing stream, or a duration outside the tolerance fail the fixture.

## Not in this tranche

VoiceStudio stays gated. No model download, no localhost speech service, no voice change. Flow has no adapter here. A manifest that names `flow`, `elevenlabs`, `fal`, `gemini`, `voicestudio`, or another unknown provider fails closed. `/make-long` and `/repurpose-short` are not added. The pilot episode was not produced.
