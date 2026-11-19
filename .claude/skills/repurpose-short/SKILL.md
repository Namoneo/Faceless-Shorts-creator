# repurpose-short — standalone 9:16 Shorts from an episode

Use this after `/make-long`, when an episode manifest already exists and a person has chosen moments. It does not replace `/make-short`. It does not generate pictures, narration, or a new Flow clip.

Each Short must stand on its own: its own hook card, the reused moment, and its own ending card. It must not depend on a line that only makes sense inside the episode.

For `uz-Latn`, write the hook and the ending in the polite `siz` form. Never the informal `sen` form. Say `olasizmi`, not `olasanmi`. Say `tanlang`, not `tanla`.

## Reuse

- Take narration and visual assets from the episode by sha256.
- A matching hash is reused. A mismatch fails. Nothing is regenerated to "fix" a hash.
- A portrait version of an approved Flow master is a local layout: blurred fill, the clip placed by the focal point, and a caption at portrait size. It is not a pad onto empty bars, and it does not create a Flow packet.
- A separate Flow variant needs a written reason and goes back to `/make-long`'s manual stop.
- Hook audio is optional and must be a separate imported file. It is not VoiceStudio and not ElevenLabs.

## Command

```bash
python3 -m tools.foundation.cli repurpose \
  --manifest <episode>/manifest.json \
  --selection <episode>/shorts.json \
  --packet <approved-packet>.json \
  --out <episode>/out
```

The selection names the moment, the hook text, the ending text, and the optional hook-audio path. The runner writes a 1080x1920 file per Short, burns the display text, and runs the same QA as the episode. A repeat skips unchanged pieces.

Mobile captions use the short's own display strings. The 0.62 em estimate does not pass a caption. Unverified geometry fails.

## Done =

every Short starts and ends by itself · reused hashes match the episode · hook audio, if any, is an import · QA ran · no provider was called.
