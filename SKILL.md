---
name: youtube-clip
description: Download a YouTube video, or clip a precise time-section out of a long stream, into a local MP4. Use when the user shares a YouTube/youtu.be link and wants to save it, grab one game/match/segment out of a multi-hour livestream, get footage off YouTube for analysis or review, or says "download this video", "clip from X to Y", "extract the section", "get it off YouTube", or hits YouTube SSL / bot-blocking. Handles corporate-network gotchas (TLS-inspecting VPN/firewall breaking yt-dlp and ffmpeg) and explains YouTube datacenter-IP bot-blocking.
---

# YouTube Clip / Download

Pull a whole YouTube video, or just a time-slice of a long stream, into a local MP4. Built to also
work on managed Windows machines behind a TLS-inspecting VPN/firewall, where the normal tools fail.

## The two-tool stack

| Tool | Job | Get it |
|------|-----|--------|
| `yt-dlp.exe` | Talks to YouTube, picks formats, drives the download | `yt-dlp/yt-dlp` GitHub release |
| `ffmpeg.exe` + `ffprobe.exe` | Cuts the time-section, merges video+audio, writes the MP4 | `BtbN/FFmpeg-Builds` GitHub release (win64-gpl zip) |

yt-dlp without ffmpeg can only grab whole pre-muxed files. **Section cutting and 1080p merging need
ffmpeg.** Install both with `scripts/Install-Tools.ps1`.

## Fast path

Whole video:
```powershell
powershell -File scripts\Clip-YouTube.ps1 "https://www.youtube.com/watch?v=VIDEO_ID"
```

Just a section (a single game out of a multi-hour stream), `HH:MM:SS`:
```powershell
powershell -File scripts\Clip-YouTube.ps1 "https://www.youtube.com/watch?v=VIDEO_ID" -Start 5:21:10 -End 6:54:28
```

Output lands in `%USERPROFILE%\Videos\youtube_clips\` by default (override with `-OutDir`).

## The raw command (what the wrapper runs)

```powershell
yt-dlp --no-check-certificates --no-update `
  --download-sections "*5:21:10-6:54:28" `
  --downloader ffmpeg --downloader-args "ffmpeg_i:-tls_verify 0" `
  -S "res:1080,ext:mp4:m4a" --merge-output-format mp4 `
  --ffmpeg-location "C:\path\to\bin" `
  -o "%USERPROFILE%\Videos\youtube_clips\%(title)s_%(id)s.%(ext)s" `
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

Section syntax is `*START-END`. Times are `HH:MM:SS` or plain seconds. Omit `--download-sections`
for the whole video.

## CRITICAL: the corporate-network (TLS inspection) gotchas

Many corporate networks run a TLS-inspecting VPN/firewall (GlobalProtect, Zscaler, Netskope and
similar) that re-signs every HTTPS request with a company CA. Two separate components break on it,
and each needs its own bypass:

1. **yt-dlp** (Python, bundles certifi, ignores the Windows trust store) fails with
   `CERTIFICATE_VERIFY_FAILED: self signed certificate in certificate chain`.
   Fix: `--no-check-certificates`.

2. **ffmpeg** (does the byte-range fetch when cutting a section) fails with
   `Creating security context failed (0x80092012)` (Windows cannot check revocation on the
   re-signed cert). Fix: `--downloader ffmpeg --downloader-args "ffmpeg_i:-tls_verify 0"`.

Both bypasses are safe here: these are public videos, no credentials are sent. The connection is
still encrypted, just not certificate-verified. On a normal (non-inspected) network you can drop
both flags. The wrapper applies them by default; pass `-VerifyTls` to use strict checks.

## YouTube bot-blocking, and why this runs LOCALLY not on a server

YouTube's "Sign in to confirm you are not a bot" wall mainly targets **datacenter IP ranges**.
A cloud VM / server is exactly such an IP, so routing through one is the WORST option: it gets
blocked hardest. A normal laptop sits on a corporate/residential-class IP that YouTube trusts, so
downloads just work. **Always pull from the laptop, not a server.**

If you ever MUST download from a server/datacenter IP, the workarounds are:
- `--cookies-from-browser chrome` (or `--cookies cookies.txt`): sends a logged-in Google cookie so
  YouTube trusts the request. Most reliable datacenter fix.
- A residential proxy service (routes via residential IPs).
- Keep yt-dlp current (`yt-dlp -U`); they patch the extractor as YouTube changes.

## Quality / size guidance

`-Quality` picks the max vertical resolution (default `1080`). A section is roughly
`(section_seconds / total_seconds)` of the full-file size. A ~90-minute 1080p clip is about 2 GB.
For most review use 720p is plenty and roughly halves the size and time. Source-best is capped at
what the upload actually contains.

## Where to save

The default `Videos\youtube_clips` is a LOCAL (non-synced) folder on purpose: a multi-GB clip on a
cloud-synced Desktop (OneDrive/Dropbox) would trigger a large cloud sync. Move the finished file
deliberately if you want it shared via cloud storage.

## Troubleshooting

- `CERTIFICATE_VERIFY_FAILED` -> missing `--no-check-certificates` (yt-dlp).
- `Creating security context failed (0x80092012)` / `ffmpeg exited with code` -> missing the
  `ffmpeg_i:-tls_verify 0` downloader arg (ffmpeg section fetch).
- `ffmpeg not found` / cannot cut section -> ffmpeg is not installed or not found; run
  `Install-Tools.ps1`, or pass `--ffmpeg-location`.
- `Sign in to confirm you are not a bot` -> you are on a datacenter IP. Run from the laptop, or add
  `--cookies-from-browser chrome`.
- Slow download -> YouTube throttles some client APIs. It still completes; let it run in the
  background. Lower `-Quality` to speed it up.
- A few seconds extra at the start of the clip -> normal. The cut starts at the nearest keyframe.
  Add `--force-keyframes-at-cuts` for frame-accurate cuts (slower, re-encodes).

## Cut the dead time out (rally extractor)

`scripts/extract_rallies.py` (+ `Extract-Rallies.ps1` wrapper) takes a downloaded racket-sport
match and keeps only the live court-camera footage, dropping the filler between points: replays,
player close-ups, crowd shots, score graphics, ad bumpers. Built for padel/tennis/squash broadcasts.

No model, no training, no tagging. It samples ~2 frames/sec at low res, fingerprints each by a
coarse colour histogram, auto-detects the dominant recurring camera (the rally view is always the
single most-used shot), keeps the spans that match, pads them, drops sub-3s flashes, merges close
runs, and stitches the survivors with an ffmpeg concat **stream copy** (fast, full quality,
keyframe-snapped). Deps already on the laptop: ffmpeg/ffprobe in `~/bin`, numpy (no Pillow needed).

```powershell
# Review the cut first (writes a CSV of kept segments, renders nothing)
powershell -File scripts\Extract-Rallies.ps1 "C:\path\match.mp4" -DryRun
# Render the play-only file
powershell -File scripts\Extract-Rallies.ps1 "C:\path\match.mp4"
```
Or call the Python directly: `py -3.12 -u scripts\extract_rallies.py "match.mp4" [--dry-run]`.

Output: `<input>_rallies.mp4` plus `<input>_rallies.mp4.segments.csv` (index, start/end seconds +
HH:MM:SS, duration) for review. Proven run: a 93-min Arab Padel Tour match cut to 39:43 of rallies
(171 segments, 43% retained), 2.29 GB -> 992 MB, stitch in ~52s after a ~1-min analysis pass.

**Tunables (it is a colour heuristic, so a different broadcast may need a nudge):**
- `-Threshold 0..1` - match cutoff (default auto/Otsu). Raise if filler is kept, lower if rallies drop.
- `-RefTime <sec>` - force the court reference to a frame you know is a live rally (use if auto picks
  the wrong angle, e.g. it latches onto a replay camera).
- `-Pad` (default 1s), `-MinSeg` (default 3s), `-Gap` (default 1.5s) - run padding / flash filter / merge.

**How to verify a cut:** read the `.segments.csv`, or pull a thumbnail at a kept vs a dropped time
(`ffmpeg -ss T -i match.mp4 -frames:v 1 t.jpg`) - kept should be the wide court view, dropped should
be a replay/crowd/graphic. Always `-DryRun` and eyeball before trusting it on a new broadcast.

**Limits:** slow-mo replays of the rally share the court colour so they are kept (usually fine, it is
still on-court action). Productions that cut between two very different court angles, or splash a big
graphic over live play, can confuse the single-reference colour match - use `-RefTime` or `-Threshold`.
For frame-accurate cuts instead of keyframe-snapped, re-encode (not wired in; stream copy is the default).

## Other platforms

yt-dlp + ffmpeg are cross-platform. On macOS/Linux: `brew install yt-dlp ffmpeg` or
`pip install yt-dlp` plus the distro ffmpeg, then use the same flags (drop the corporate TLS flags
unless your network inspects TLS).
