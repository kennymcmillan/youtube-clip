<#
.SYNOPSIS
  Download a YouTube video, or cut one time-section out of it, into a local MP4.
  Corporate-network ready (handles TLS-inspecting VPN/firewall gotchas automatically).

.PARAMETER Url
  The YouTube URL (https://www.youtube.com/watch?v=... or https://youtu.be/...).

.PARAMETER Start
  Section start, as HH:MM:SS, MM:SS, or seconds. Use together with -End.

.PARAMETER End
  Section end. Omit both -Start and -End to download the whole video.

.PARAMETER OutDir
  Output folder. Default: %USERPROFILE%\Videos\youtube_clips

.PARAMETER Name
  Custom output file name (no extension). Default: the video title.

.PARAMETER Quality
  Max video height: 2160, 1440, 1080 (default), 720, 480, 360.

.PARAMETER VerifyTls
  Use strict TLS certificate verification. Only on a normal (non-inspected) network. By default the
  script skips verification so it works through a TLS-inspecting VPN/firewall.

.EXAMPLE
  .\Clip-YouTube.ps1 "https://www.youtube.com/watch?v=VIDEO_ID" -Start 5:21:10 -End 6:54:28

.EXAMPLE
  .\Clip-YouTube.ps1 "https://youtu.be/VIDEO_ID" -Quality 720 -Name "my_clip"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Url,
    [string]$Start,
    [string]$End,
    [string]$OutDir = (Join-Path $env:USERPROFILE "Videos\youtube_clips"),
    [string]$Name,
    [ValidateSet("2160", "1440", "1080", "720", "480", "360")][string]$Quality = "1080",
    [switch]$VerifyTls
)

$ErrorActionPreference = "Stop"

# --- locate tools ---
function Resolve-Tool($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $inBin = Join-Path $env:USERPROFILE "bin\$name.exe"
    if (Test-Path $inBin) { return $inBin }
    return $null
}

$ytdlp = Resolve-Tool "yt-dlp"
if (-not $ytdlp) {
    throw "yt-dlp not found. Run Install-Tools.ps1 first (it downloads yt-dlp + ffmpeg)."
}
$ffmpeg = Resolve-Tool "ffmpeg"
if (-not $ffmpeg) {
    throw "ffmpeg not found (needed to cut sections / build the MP4). Run Install-Tools.ps1 first."
}
$ffmpegDir = Split-Path $ffmpeg -Parent

# --- validate section ---
if (($Start -and -not $End) -or ($End -and -not $Start)) {
    throw "Provide BOTH -Start and -End for a section, or neither to download the whole video."
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# --- output template ---
if ($Name) {
    $template = Join-Path $OutDir ("{0}.%(ext)s" -f $Name)
} else {
    $template = Join-Path $OutDir "%(title)s_%(id)s.%(ext)s"
}

# --- build args ---
$ytArgs = @("--no-update", "--restrict-filenames")

if (-not $VerifyTls) {
    # yt-dlp side: skip cert verification (the inspecting proxy re-signs every HTTPS request).
    $ytArgs += "--no-check-certificates"
}

if ($Start -and $End) {
    $ytArgs += @("--download-sections", "*$Start-$End")
    if (-not $VerifyTls) {
        # ffmpeg side: it does the byte-range fetch for sections and needs its own TLS bypass.
        $ytArgs += @("--downloader", "ffmpeg", "--downloader-args", "ffmpeg_i:-tls_verify 0")
    }
}

$ytArgs += @(
    "-S", "res:$Quality,ext:mp4:m4a",
    "--merge-output-format", "mp4",
    "--ffmpeg-location", $ffmpegDir,
    "-o", $template,
    $Url
)

# --- run ---
Write-Host "yt-dlp: $ytdlp" -ForegroundColor DarkGray
if ($Start) { Write-Host "Clipping $Start to $End at ${Quality}p" -ForegroundColor Cyan }
else { Write-Host "Downloading whole video at ${Quality}p" -ForegroundColor Cyan }
Write-Host "Output folder: $OutDir" -ForegroundColor DarkGray
Write-Host ""

& $ytdlp @ytArgs
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "Done. File(s) in: $OutDir" -ForegroundColor Green
    Get-ChildItem $OutDir -Filter *.mp4 | Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 Name, @{N = "SizeMB"; E = { [math]::Round($_.Length / 1MB, 1) } }, FullName |
        Format-List
} else {
    Write-Host "yt-dlp exited with code $code. See README section 7 (troubleshooting)." -ForegroundColor Red
    exit $code
}
