# Engine Decision

Date: 2026-06-03
Updated: 2026-06-05

Goal: Admin sends one YouTube link, then the system handles video intake, clipping, metadata, preview, upload handoff, and VPS cleanup with free-first tooling.

## Decision

Default engine is the local autopilot pipeline:

1. Try public metadata from YouTube oEmbed.
2. Try direct local download with `yt-dlp`, optional dedicated-browser cookies, Node/EJS helpers, and `bgutil`.
3. If download succeeds, render clips locally with `ffmpeg`.
4. Caption locally with `faster-whisper`.
5. If YouTube blocks download, mark the job as `blocked_free_download` or `needs_external_provider`.
6. Do not use exploit, credential scraping, or illegal bypass.
7. Obsolete external download providers are not part of the active default path.
8. Account-assisted fallback may use `YTDLP_COOKIES_FROM_BROWSER` from a dedicated automation browser profile, never a password stored in chat, code, git, or env files.
9. Do not keep retrying Google login from a VPS when Google blocks it; treat it as a non-viable foundation.

## Open-Source Audit

- OpenShorts: best reference candidate. MIT license, self-hosted, no watermark/limits by default. Some features still need APIs such as Gemini or posting providers.
- SupoClip: useful reference, but AGPL-3.0 and API-heavy. Use carefully because AGPL has distribution/network-service obligations.
- ClippedAI: avoid for this project because license is CC BY-NC 4.0, which is not suitable for profit-oriented/commercial use.
- MakeAIClips: removed from active architecture. It can remain as external research/benchmark only, not the default system path. Use `MAKEAICLIPS_BENCHMARK_WORKFLOW.md` when Admin wants to compare external output quality against the local engine.

## 2026-06-04 Research Verdict

- Installed and tested `yt-dlp` nightly, `yt-dlp-ejs`, `bgutil-ytdlp-pot-provider`, and `yt-dlp-getpot-wpc`.
- `bgutil` loads as a PO-token provider, but this VPS still receives YouTube `LOGIN_REQUIRED` for both Admin's test video and public control videos.
- `wpc` requires Chrome/Chromium; this VPS only had Firefox available during testing, so it remains unavailable without a browser install.
- Tested 21 `yt-dlp` client/format combinations (`mweb`, `web_safari`, `web_embedded`, `web_music`, `android_vr`, `ios`, `tv`) and all failed with login/bot-check style errors.
- Public Cobalt requires challenge/JWT and no longer listed YouTube in the public instance services tested.
- Invidious public instances tested returned 403 or unavailable APIs.
- Piped public instances tested returned 502, shutdown notices, DNS/network failures, redirects, or unavailable API responses.
- SaveFrom and Y2mate-style web frontends are not accepted as backend providers because they are not stable documented APIs for automation.

Conclusion: do not spend more time trying public downloader websites from this VPS. The reliable option is a trusted-device worker outside this VPS path or a safely managed dedicated browser session that `yt-dlp` can use.

## Practical Limit

Fully automated YouTube-link intake without cookies or an external processor cannot be guaranteed. YouTube may block direct download from VPS. The system should make the local `yt-dlp` attempt first, then fail safely and visibly instead of trying unsafe bypasses.

If a dedicated Google account is used, treat it as a temporary session fallback, not a permanent guarantee. Google can expire sessions or request verification at any time.
