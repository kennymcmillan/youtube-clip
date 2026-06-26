# YouTube Clip / Download (corporate-network ready)

Save a whole YouTube video, or cut one segment (for example a single game out of a multi-hour
stream) into a local MP4 file you can keep, analyse, or share. Works even on managed Windows
machines behind a TLS-inspecting VPN/firewall, where the normal tools fail with certificate errors.

This folder is self-contained. You can zip it and send it to a colleague.

---

## 1. What you need to download (one time)

Two free, open-source command-line tools:

| Tool | What it is | Download link |
|------|-----------|---------------|
| **yt-dlp** | The YouTube downloader | https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe |
| **ffmpeg** (+ ffprobe) | Cuts the time-section and builds the MP4 | https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip |

You do not have to fetch them by hand. Run the installer in step 2 and it does it for you.

> Why both? yt-dlp on its own can only grab a whole video at whatever single quality is pre-bundled.
> To cut a precise section, and to get 1080p (which YouTube serves as separate video + audio that
> must be joined), you need ffmpeg.

---

## 2. Install (one time)

Open **PowerShell**, change into this folder, and run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Install-Tools.ps1
```

This downloads yt-dlp.exe and ffmpeg into `%USERPROFILE%\bin` (creating it and adding it to your
PATH if needed). If you already have them on your PATH, it leaves them alone.

---

## 3. Use it

### Cut one segment out of a long stream (most common)

Times are `HOURS:MINUTES:SECONDS`. Example: a game that ran from 5:21:10 to 6:54:28 inside a
multi-hour stream:

```powershell
powershell -File scripts\Clip-YouTube.ps1 "https://www.youtube.com/watch?v=VIDEO_ID" -Start 5:21:10 -End 6:54:28
```

### Download the whole video

```powershell
powershell -File scripts\Clip-YouTube.ps1 "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Options

| Option | Default | Meaning |
|--------|---------|---------|
| `-Start` / `-End` | (none = whole video) | Section start/end as `HH:MM:SS` or seconds. Use both together. |
| `-OutDir` | `%USERPROFILE%\Videos\youtube_clips` | Where to save the MP4 |
| `-Name` | the video title | Custom output file name (no extension) |
| `-Quality` | `1080` | Max height: `2160`, `1440`, `1080`, `720`, `480`, `360`. Lower = smaller + faster |
| `-VerifyTls` | off | Use strict certificate checks (only on a normal, non-inspected network) |

The finished file is saved into the output folder and the script prints its full path.

---

## 4. How long / how big

A section is roughly its share of the full video. A ~90-minute clip at 1080p is about **2 GB** and
takes around **45 minutes** (it downloads at roughly 2x real-time). Drop to `-Quality 720` to roughly
halve both. The download runs to completion on its own; you can leave it.

---

## 5. The corporate-network catch (already handled)

Many corporate networks run a TLS-inspecting VPN or firewall (GlobalProtect, Zscaler, Netskope and
similar). It re-signs every HTTPS request with a company certificate, which makes both tools think
the connection is fake and refuse:

- **yt-dlp** errors with `CERTIFICATE_VERIFY_FAILED ... self signed certificate in certificate chain`.
- **ffmpeg** errors with `Creating security context failed (0x80092012)`.

The script works around both automatically (it tells the tools to skip certificate verification for
these public-video downloads; the traffic is still encrypted). On a home or non-inspected network it
is not needed, and you can add `-VerifyTls` to use strict checks.

---

## 6. Important: run this on your LAPTOP, not on a server

YouTube blocks downloads from **data-centre IP addresses** (cloud servers) with a
"Sign in to confirm you are not a bot" wall. Your laptop's normal internet connection is trusted, so
it works. A cloud VM is the worst place to run this. Keep it local.

If you are ever forced to run from a server, add `--cookies-from-browser chrome` to the yt-dlp
command so it borrows your logged-in YouTube session.

---

## 7. Quick troubleshooting

| Symptom | Fix |
|---------|-----|
| `CERTIFICATE_VERIFY_FAILED` | You removed the TLS bypass. Do not pass `-VerifyTls` on an inspected network. |
| `Creating security context failed (0x80092012)` | Same as above, on ffmpeg's side. |
| `ffmpeg not found` | Run `Install-Tools.ps1`. |
| `Sign in to confirm you are not a bot` | You are on a server / VPN exit that looks like a data centre. Run from the laptop. |
| Clip starts a few seconds early | Normal (cut snaps to the nearest keyframe). Harmless for video review. |
| Very slow | YouTube throttles some requests. It still finishes. Lower `-Quality` to speed up. |

---

## 7b. Cut the dead time out of a match (rally extractor)

Got a full match and only want the rallies? `Extract-Rallies.ps1` keeps the live court-camera
footage and throws away the bits between points (replays, close-ups, crowd shots, score graphics,
ad breaks). It works it out from the picture itself, so there is nothing to set up, label, or train.

```powershell
# 1. (optional) preview the cut - writes a list of the bits it will keep, renders nothing
powershell -File scripts\Extract-Rallies.ps1 "C:\path\to\match.mp4" -DryRun

# 2. make the play-only file
powershell -File scripts\Extract-Rallies.ps1 "C:\path\to\match.mp4"
```

You get `match_rallies.mp4` next to the original, plus a `..._rallies.mp4.segments.csv` listing every
kept clip with timestamps so you can skip straight to any rally. On one 93-minute padel match it
produced 39 minutes of rallies (171 clips) and shrank the file from 2.3 GB to under 1 GB in about a
minute. It needs the same ffmpeg you already installed in step 2, nothing else.

If a particular broadcast keeps too much or too little, two dials help: `-Threshold 0.7` (higher keeps
less), or `-RefTime 270` to point it at a second in the video you know is a live rally. Always preview
with `-DryRun` on a new channel before trusting it.

## 8. Using it inside Claude Code (optional)

This repo is also a Claude Code skill. To let Claude run it for you, copy the whole folder to your
skills directory and restart Claude Code:

```powershell
# Windows
Copy-Item -Recurse . "$env:USERPROFILE\.claude\skills\youtube-clip"
```
```bash
# macOS / Linux
cp -r . ~/.claude/skills/youtube-clip
```

Then paste a YouTube link and say "clip from 5:21:10 to 6:54:28" and Claude handles the rest.

---

## 9. Mac / Linux

Same two tools, easier install:

```bash
# macOS
brew install yt-dlp ffmpeg
# Debian/Ubuntu
sudo apt install ffmpeg && pip install -U yt-dlp
```

Then the same yt-dlp command (see `SKILL.md` for the raw form). Drop the corporate TLS flags unless
your network inspects TLS.

---

## License

MIT. Use freely. yt-dlp and ffmpeg are separate projects with their own licenses.
