# Offline episode foundation

This is a foundation for shared episode assets, imported narration, dual-format Remotion renders, and QA. It is not production narration and not an Uzbek aligner. The stopped review draft of «7 ta tanlov — oxirigacha yetib bora olasizmi?» is `productions/episode-001`. That folder does not render.

The new runner never calls ElevenLabs, fal, Gemini, VoiceStudio, Flow, or any other generator. Those older tools are still in `tools/` for the existing shorts. `python3 -m tools.foundation.cli generate` exits 2.

## What a run does

`productions/fixtures/foundation/manifest.json` is the runnable example. Its narration is a 440 Hz ffmpeg sine at -18 dB, labeled synthetic. It is not TTS and not VoiceStudio. The picture is the `FoundationEpisode` component, drawn separately at 1920x1080 and 1080x1920, 30 fps, from the same scene list. It is not a crop of one finished movie.

Stable ids: `fixture-foundation`, `narration-main`, `card-title`, `scene-hook`, `scene-hold`.

Each narration asset keeps the original bytes under `out/originals/<id>/<sha256>.wav`. The canonical derivative is one loudnorm pass (`I=-16:TP=-1.5:LRA=11`), 48 kHz, stereo, `pcm_s16le`. A repeat skips that work when the source hash, the parameter hash, and the canonical file hash still match.

Timing lives beside the episode, not inside the audio file. States are `unaligned`, `review`, and `manual`. Unaligned lines have no word times. Review and manual lines need explicit start and end. A correction replaces the display token only and is stamped with the canonical audio hash. A different hash does not apply it, and a stale manual line drops back to review. Nothing in this runner invents word times. Display text is stored as exact code points. `Oʻzbekcha` uses U+02BB. `o‘g‘` uses U+2018. They are not folded together. Uzbek scripts use the polite `siz` form, never informal `sen`. A warning, not a failure, flags the endings `-san`, `-sanmi`, `-ding`, and `-ngmi`. Examples: `olasizmi`, not `olasanmi`; `tanlang`, not `tanla`. The pilot title is «7 ta tanlov — oxirigacha yetib bora olasizmi?».

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

- Black: ffmpeg `blackdetect` over the whole stream (`d=0.001`, pixel threshold 0.10, 98% of the picture). A black frame that is not the scene midpoint still fails. Limited-range black measures about 16. A small dark object that does not cover the frame is not detected. `allowBlack` still reports the intervals.
- Silence: ffmpeg `silencedetect` at -45 dB with a minimum of 0.05 s. A silent stretch inside a scene fails even when the rest of the scene is loud. A dropout shorter than 0.05 s can pass. Clipping is still whole-file max volume above -0.5 dB. `allowSilence` still reports the intervals.
- `allowBlack`, `allowSilence`, `allowClipping`, and `allowCaptionOverflow` are required booleans. Detection still runs. The report records `detected` and `allowed`. An allowance does not turn the detector off.
- Caption overflow is measured in headless Chrome with DejaVu Sans, the caption width, and the real font size. The pass uses that geometry. The 0.62 em estimate is advisory and cannot pass a caption by itself. If Chrome or the font cannot be measured, the caption check fails as unverified. The status chip (`REVIEW`, `UNALIGNED`) is not part of the measured string. This is not a read-back of encoded pixels, and it does not prove pronunciation.
- Duration may differ by up to two frames. AAC priming and encoder rounding are the reason.
- Loudnorm is single-pass and depends on the local ffmpeg. The sine hash is stable on one machine. It is not a portable golden file.
- Transitions in this tranche are cuts of zero duration. The timeline must equal `episodeDurationSec` within one frame, and each narration in/out must match its scene.
- Wrong dimensions, a frame rate other than 30, a missing stream, or a duration outside the tolerance fail the fixture.

## Manual Flow and long-form

`/make-long` and `/repurpose-short` are skills. They stop for a person. They do not open Flow, fal, ElevenLabs, or VoiceStudio.

```bash
python3 -m tools.foundation.cli plan-long \
  --topic "Synthetic fixture" --language uz-Latn --duration 24 \
  --audience "local test" --out /tmp/long-plan

python3 -m tools.foundation.cli narration-door --door import
python3 -m tools.foundation.cli narration-door --door voicestudio
python3 -m tools.foundation.cli flow-generate
```

`plan-long` writes an outline, script skeleton, scene list, storyboard, Remotion-or-Flow classification, and one Flow packet in status `planned`. It does not render. `narration-door voicestudio` exits 1. `flow-generate` and `generate` exit 2. fal stays refused on an asset.

A Flow packet moves `planned` → `awaiting_manual_generation` → `awaiting_import` → `validated` → `approved` or `rejected`. Import checks decode, dimensions, 30 fps, duration, an audio stream when required, sha256, and provenance (`tool`, `origin`, `note`). The source file is copied once under `out/flow-cache/sources/<sha256>.mp4`. A second import of the same bytes skips the copy. A portrait version of an approved master is a derived layout: the frame is filled with a blurred cover crop, the clip is placed from the focal point, and the caption is drawn at portrait size. It is not a pad onto empty bars, and it does not ask Flow again. A separate portrait or landscape generation needs a written reason and starts a new `planned` packet with no media.

The credit field comes from `productions/flow-price-table.json`. Every estimate says to verify the price in Flow before approval. The table is not a quote and it does not contact Flow.

`productions/fixtures/longform/` is a 24 second landscape fixture. Its narration is a 440 Hz sine. One scene is an ffmpeg `testsrc2` file that stands in for a manual Flow export, with the label `TEST PATTERN - stand-in for manual Google Flow export` burned in. The other scenes are ffmpeg title cards standing in for Remotion scenes. The fixture does not render a new Remotion composition. Two portrait Shorts reuse that Flow file by hash. Text cards are drawn again at 1080×1920, and the Flow clip uses the portrait layout above. Each Short has its own hook and ending card. `short-pattern` also imports a separate sine as hook audio. Repeat runs skip unchanged scene files. Uzbek oʻ and gʻ in these cards are U+02BB. A straight apostrophe (U+0027) in those words fails QA. U+2018 in the 4 second fixture is still a different letter and is left as written.

```bash
python3 -m tools.foundation.cli run-long \
  --manifest productions/fixtures/longform/manifest.json \
  --packet productions/fixtures/longform/out/packet-flow.json \
  --out productions/fixtures/longform/out

python3 -m tools.foundation.cli repurpose \
  --manifest productions/fixtures/longform/manifest.json \
  --selection productions/fixtures/longform/shorts.json \
  --packet productions/fixtures/longform/out/packet-flow.json \
  --out productions/fixtures/longform/out
```

Naming `flow` as an asset provider still fails. The approved file enters as `import` plus `flowPacket`.

## VoiceStudio names

The adapter config is `productions/voicestudio-voices.json`. For `uz-Latn` it requests engine `cosyvoice`, voice profile `Nodira`, and `require_navoiy` true. `voiceConsent` is required and unset, so commercial use stays blocked. `navoiyActive` is false: this copy does not treat the checkpoint as active, and it does not ask the Mac. `en` and `nl` are unconfigured. There is no OmniVoice fallback. `synthesize` and `narration-door voicestudio` raise before any audio, download, or localhost call. The Mac runner is not approved. `upstreamCatalogMatch` is false.

Checked on 9 Nov 2026 against `debpalash/VoiceStudio` `main` (`backend/config/models.yaml`, `backend/config/model_licenses.json` at commit `06c6e077f0fc35149efefc3561e9be5ae835d916`, `docs/engines/omnivoice.md`, `docs/licensing/model-inventory.md`) and against `aisha-org/navoiy-tts` README, NOTICE, and `inference.py`. No weights were downloaded.

VoiceStudio's own engine doc says: "OmniVoice (k2-fsa/OmniVoice) is VoiceStudio's default TTS engine — the one a fresh install uses without any configuration." The licence record for `k2-fsa/OmniVoice` says `license` "CC-BY-NC (version unspecified); Higgs Audio 2; Meta Llama 3", `commercial_use` false, `review_status` "noncommercial", and notes: "The pinned card distinguishes Apache-2.0 code from non-commercial weights."

The inventory says: "`commercial_use: true` means the recorded distribution scope has been reviewed and cleared, with a reviewer, date, revision and scope. No record currently makes that claim." The JSON inspected here has 70 model records and zero `commercial_use: true`. The same file's CosyVoice row, `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`, has `license` "apache-2.0", `review_status` "unreviewed", and `commercial_use` false. `models.yaml` does not contain Navoiy or Nodira. A filename search of that tree and a commit search for those names were empty.

Aisha's card says: "Lison Navoiy TTS is Aisha's public Uzbek expressive text-to-speech model artifact built on CosyVoice2." The summary lists `License | Apache-2.0` and `Base model | FunAudioLLM/CosyVoice2-0.5B`. The NOTICE says the CosyVoice components are "licensed under the Apache License, Version 2.0." `inference.py` selects the speaker with `--reference`, described as "Consented reference-speaker WAV", and the checkpoint with `--checkpoint`. It has no `--voice Nodira` flag. The demo manifest's reference is `zero_shot_prompt.wav`.

A read-only check of Sherzod's Mac on 9 Nov 2026, after that upstream inspection: VoiceStudio 0.5.7 is running at 127.0.0.1:3900 and discovery responds. His local checkout has uncommitted additions that are not upstream: `backend/engines/cosyvoice_subprocess/navoiy.py`, `tests/test_navoiy_adapter.py`, and edits to `tts_backend.py` and `docs/engines/cosyvoice.md`. Navoiy is an opt-in checkpoint (`aisha-org/navoiy-tts`, Apache-2.0, CosyVoice2) loaded through engine `cosyvoice` when `OMNIVOICE_NAVOIY_DIR` is set. It advertises Uzbek only. It requires a permitted reference-speaker recording. The docs say commercial production needs your own recording or a reference with explicit commercial voice-synthesis permission. Nodira is a VoiceStudio voice profile. His worker config uses engine `cosyvoice`, voice profile `Nodira`, and `require_navoiy` true. This repo stores that selection and still does not call the Mac. `upstreamCatalogMatch` stays false because those Navoiy files are not on published upstream.

## Still out of scope

No Uzbek aligner. No rendered pilot. QA does not prove pronunciation. Black that covers less than about 98% of the frame is missed. Silence under 0.05 s is ignored. Caption geometry is browser layout of the display string, not a pixel read of the frame. The long fixture's Remotion-classified scenes are ffmpeg cards, not a Remotion render. VoiceStudio remains a stub.
