# Errors

Command failures and integration errors.

---

## [ERR-20260605-001] video_download_api_progress_url_timeout

**Logged**: 2026-06-05T18:03:00+08:00
**Priority**: high
**Status**: partially fixed
**Area**: media/download

### Summary
The Video Download API response can include an initial URL plus a `progress_url`. The initial URL may hang without returning bytes; the final usable `download_url` appears later from the progress endpoint.

### Error
```
curl: (28) Operation timed out after N milliseconds with 0 bytes received
```

### Context
- Command/operation: podcast YouTube link autopilot download.
- Related provider: video-download-api.
- Direct `yt-dlp` was blocked by YouTube bot-check and `clipto` returned IP-locked/403 URLs.

### Suggested Fix
Poll `progress_url` until a final `download_url` is returned, then download that URL with a hard max-time and configurable retry count.

### Metadata
- Reproducible: yes
- Related Files: content-automation/mvp.py

---

## [ERR-20260604-001] ffmpeg_encoder_detection

**Logged**: 2026-06-04T01:49:00+08:00
**Priority**: medium
**Status**: fixed
**Area**: media

### Summary
Subtitle render script initially assumed `libx264`, but this VPS ffmpeg build reports no `libx264` encoder.

### Error
```
Unknown encoder libx264
```

### Context
- Command/operation: render spoken subtitles to MP4 via ffmpeg.
- Related script: `content-automation/scripts/render-subtitled-clips.py`.

### Suggested Fix
Detect available encoders before render and fall back to `mpeg4 -q:v 5` when `libx264` is unavailable.

### Metadata
- Reproducible: yes
- Related Files: content-automation/scripts/render-subtitled-clips.py

---
