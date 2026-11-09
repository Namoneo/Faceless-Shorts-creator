# make-long — offline long episode, with manual stops

Use this when the user wants a long episode, a 16:9 export, or a candidate list of Shorts. It does not replace `/make-short`, `/make-ai-short`, or `/make-vox`.

This skill never calls a provider. It does not open Flow, fal, ElevenLabs, Gemini, or VoiceStudio. It does not download a model. It does not invent word timings. The pilot is not this skill's job.

Run from the repo root.

## What you write

From topic, language, duration, and audience, write:

1. an outline
2. a script a person can read
3. a scene list
4. a storyboard
5. a classification for every scene: `remotion` or `flow`
6. narration as an import, not as synthesis
7. one Flow packet per Flow scene
8. the later 16:9 assembly, QA, and a candidate Shorts list

`python3 -m tools.foundation.cli plan-long` writes a skeleton and stops. Treat that skeleton as a tray for the words you write. Do not tell the user the episode has been generated.

## Stops

Stop and wait for an explicit answer at each of these. Do not continue past one because the files exist.

1. Outline, script, and storyboard approval.
2. Flow. The packet status must go `planned` → `awaiting_manual_generation` → `awaiting_import` → `validated` → `approved`. The person exports the clip in Flow. You only import the file they give you. `python3 -m tools.foundation.cli flow-generate` exits 2.
3. Narration import. `python3 -m tools.foundation.cli narration-door --door import` is the open door. `--door voicestudio` exits 1. The Uzbek config requests engine Navoiy, model Navoiy, and voice Nodira, and then refuses. Do not call the Mac app. `en` and `nl` have no voice configured and do not fall back to OmniVoice.
4. Assembly approval, after the packet is `approved` and the narration file is imported.

A credit figure from `productions/flow-price-table.json` is an estimate. Say that, and tell the person to check the price in Flow before they approve. It is not a quote.

A derived portrait pad does not send the scene back to Flow. If the person needs a separate portrait or landscape generation, record the reason on a new `planned` packet and stop again. fal is not a substitute.

## After the approvals

```bash
python3 -m tools.foundation.cli run-long \
  --manifest <episode>/manifest.json \
  --packet <approved-packet>.json \
  --out <episode>/out
```

That assembles the 16:9 file, runs QA, and lists candidate Shorts. Hand the list to `/repurpose-short`. Do not render those Shorts inside this skill unless the person asks.

Remotion-classified scenes use the existing offline Remotion path when a composition is actually being rendered. The long fixture's cards are ffmpeg stand-ins, labelled as such, and are not a Remotion render.

## Done =

the plan stopped at every manual step · Flow packets are approved imports, not API calls · narration is an imported file · 16:9 QA is honest about its limits · candidate Shorts are a list, not a published video · VoiceStudio was not called.
