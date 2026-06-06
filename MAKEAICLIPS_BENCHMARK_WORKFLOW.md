# MakeAIClips Benchmark Workflow

Purpose: dissect MakeAIClips as a quality reference, compare it against the VPS/OpenClaw clip engine, and copy only the useful ideas into our local system.

Core rule: MakeAIClips is not the default production engine. VPS/OpenClaw remains the processing center. Hostinger remains preview/final publishing only.

## When To Use

Use this workflow when:

- The local engine output feels weaker than external AI clip tools.
- Podcast crop, hook selection, caption style, or pacing needs a reference.
- Admin wants to compare quality before changing the local engine.
- A direct MakeAIClips benchmark is possible with `MAKEAICLIPS_API_KEY`.

Do not use this workflow to bypass approval, upload final content automatically, or send private/unlicensed video to a third party.

## Safety

- Only test videos owned by Admin or clearly permitted for reuse.
- Do not paste API keys, cookies, tokens, or account secrets into chat/logs.
- If using MakeAIClips URL mode, the YouTube URL is sent to MakeAIClips.
- If using MakeAIClips upload mode, the uploaded video file is sent to MakeAIClips.
- Final YouTube upload still requires Admin approval through our approval flow.

## Inputs

Required:

- Source video URL or local source file.
- Content type: podcast, vlog, tutorial, gaming, movie review, etc.
- Target: Shorts/Reels/TikTok, 9:16, 30-60 seconds unless specified.

Optional:

- Speaker focus: left, center, right, multi-speaker, screen-share, facecam.
- Caption preference: karaoke, clean subtitle, bold viral, documentary.
- Title overlay preference: default is no top title overlay.

## Environment Check

Run from:

```bash
cd /root/.openclaw/workspace/content-automation
```

Check whether live MakeAIClips benchmark is possible:

```bash
python3 - <<'PY'
import os
print("MAKEAICLIPS_API_KEY=" + ("SET" if os.getenv("MAKEAICLIPS_API_KEY") else "NOT_SET"))
PY
```

If `NOT_SET`, do design/API review only and skip live benchmark.

## Phase 1 - Local Baseline

Generate one local baseline from the same source.

For link-based input:

```bash
.venv/bin/python mvp.py autopilot "YOUTUBE_URL" --clips 1 --duration 45
```

For an already downloaded/local source:

```bash
.venv/bin/python mvp.py create-upload-job /path/to/source.mp4 --metadata-url "YOUTUBE_URL"
.venv/bin/python mvp.py caption JOB_ID --model small
```

For podcast speaker-aware test:

```bash
.venv/bin/python scripts/smart_podcast_clip.py /path/to/source.mp4 podcast_test left
```

For 2-person/two-side podcast scenes where wrong speaker crop is risky, use the wide pair layout first:

```bash
.venv/bin/python scripts/smart_podcast_clip.py /path/to/source.mp4 podcast_pair_test wide
```

Accepted focus values:

- `wide`, `pair`, `fit`, `both`: show the full horizontal frame inside 9:16 with blurred background.
- `left`, `center`, `right`: tight vertical crop toward that side.
- numeric `0.0` to `1.0`: manual crop ratio from left to right.

Publish only the preview link to Hostinger/static preview host. Do not upload the video file directly to Discord.

## Phase 2 - MakeAIClips Live Benchmark

Only run this phase when `MAKEAICLIPS_API_KEY` is set.

For YouTube videos, use file upload as the primary method. MakeAIClips docs warn that direct YouTube URL jobs can be blocked by YouTube; the reliable route is: download on our machine, then upload the local file to MakeAIClips.

Submit one test job with a local video file:

```bash
curl -sS -X POST "https://makeaiclips.live/api/v1/clips/upload" \
  -H "Authorization: Bearer $MAKEAICLIPS_API_KEY" \
  -F "video=@/path/to/source.mp4" \
  -F "num_clips=1" \
  -F "caption_style=karaoke-yellow" \
  -F "title_style=none" \
  -F "clip_duration=medium" \
  -F "quality=medium"
```

Poll status:

```bash
curl -sS "https://makeaiclips.live/api/v1/clips/JOB_ID" \
  -H "Authorization: Bearer $MAKEAICLIPS_API_KEY"
```

Download result:

```bash
curl -L -o workspace/clips/makeaiclips_JOB_ID_clip1.mp4 \
  "https://makeaiclips.live/api/v1/clips/JOB_ID/download/1" \
  -H "Authorization: Bearer $MAKEAICLIPS_API_KEY"
```

Only use URL job as a fallback for non-YouTube sources or a quick experiment:

```bash
curl -sS -X POST "https://makeaiclips.live/api/v1/clips" \
  -H "Authorization: Bearer $MAKEAICLIPS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"youtube_url":"TWITCH_OR_TEST_URL","num_clips":1,"title_style":"none"}'
```

## Phase 3 - Quality Review

Review local output and MakeAIClips output side by side.

Score 1-5 for:

- Segment choice: is the moment interesting?
- Context: does the clip start/end cleanly?
- Speaker framing: is the active speaker visible and centered?
- Caption timing: does text appear when words are spoken?
- Caption accuracy: are Indonesian words understandable?
- Caption style: readable on phone, not blocking faces.
- Pacing: no dead air, no awkward cuts.
- Audio clarity: voice is loud and clean.
- Output quality: no broken render, stutter, black frame, or wrong aspect ratio.
- Workflow reliability: speed, API failure, timeout, file size.

Podcast-specific checks:

- If a left speaker talks, crop should not focus on right speaker.
- If two people are on one side, crop should prefer active mouth/face movement, not just largest face.
- If the source camera angle itself does not show the speaker, reject that segment or use a wider crop.

## Phase 4 - Extract Useful Ideas

Turn benchmark findings into local engine changes.

Possible upgrades:

- Better scene scoring from transcript hooks.
- Speaker-zone crop: left, center, right, numeric crop ratio.
- Wide pair layout for 2-person/two-side podcast scenes.
- Multi-face tracking across sampled ffmpeg frames.
- Lip/mouth-motion score for podcast active speaker.
- Caption presets copied conceptually, not copied as assets.
- Hook/title suggestions as metadata only, not forced top overlay.
- QC gate before publishing preview.

Do not import MakeAIClips as a hidden dependency in the default path.

## Phase 5 - Apply Fix Locally

Before editing:

- Backup important scripts.
- Do not hardcode API keys or secrets.
- Keep edits scoped.

Expected local files:

- `mvp.py` for queue/job/approval/upload logic.
- `scripts/smart_podcast_clip.py` for podcast scene selection and speaker crop.
- `scripts/render-subtitled-clips.py` for subtitle render style.
- `scripts/publish-clips.sh` for Hostinger preview publishing.

After edits, run:

```bash
.venv/bin/python -m py_compile mvp.py scripts/smart_podcast_clip.py scripts/render-subtitled-clips.py
bash -n scripts/publish-clips.sh scripts/run-clip-pipeline.sh
```

Render one new preview and publish it as a link.

## Phase 6 - Report

Report back in Discord with:

- Local preview link.
- MakeAIClips preview link if benchmark was run.
- Which output is better and why.
- Bugs found.
- Files changed.
- Tests run.
- What still cannot be tested because config/API is missing.
- Next recommended engine improvement.

## Decision Gate

Use local engine as final when:

- Speaker crop is correct.
- Captions are readable and timed.
- Preview link works.
- Admin approves.

Use MakeAIClips only as fallback/reference when:

- Local scene selection is clearly worse.
- Admin explicitly wants external benchmark.
- API key/plan is available.
- The source is allowed to be processed by a third party.

Never auto-upload to YouTube just because either engine succeeds. Approval remains mandatory.
