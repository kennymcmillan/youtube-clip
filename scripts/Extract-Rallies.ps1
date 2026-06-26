<#
.SYNOPSIS
  Strip the dead time out of a racket-sport broadcast: keep only the live court-camera
  footage (the rallies) and drop the filler between points (replays, close-ups, crowd
  shots, score graphics, ad bumpers).

  No model, no training, no tagging. It samples the video, fingerprints each frame by
  colour, works out the dominant court camera on its own, keeps the matching spans, and
  stitches them with a fast stream copy. A <output>.segments.csv is written alongside the
  result so you can review the cut before trusting it.

.PARAMETER Input
  Path to the source MP4 (e.g. a full match you downloaded with Clip-YouTube.ps1).

.PARAMETER Out
  Output MP4. Default: <input>_rallies.mp4 next to the source.

.PARAMETER DryRun
  Write only the segments CSV and stop. Review it, then re-run without -DryRun to render.

.PARAMETER Threshold
  Colour-match cutoff 0..1. Omit to auto-pick (Otsu). Raise it if too much filler is kept,
  lower it if rallies are being dropped.

.PARAMETER RefTime
  Seconds. Force the court reference to a frame you know is a live rally, instead of the
  auto pick. Use this if the auto detection latches onto the wrong camera.

.PARAMETER Pad
  Seconds padded onto each kept run so serves are not clipped. Default 1.

.PARAMETER MinSeg
  Drop kept runs shorter than this many seconds (kills one-frame flashes). Default 3.

.PARAMETER Gap
  Merge kept runs closer together than this many seconds. Default 1.5.

.EXAMPLE
  .\Extract-Rallies.ps1 "$env:USERPROFILE\Videos\youtube_clips\match.mp4"

.EXAMPLE
  # Check the cut first, then render
  .\Extract-Rallies.ps1 "match.mp4" -DryRun
  .\Extract-Rallies.ps1 "match.mp4"

.EXAMPLE
  # Auto pick latched onto a replay angle; point it at a known rally and keep more
  .\Extract-Rallies.ps1 "match.mp4" -RefTime 270 -Threshold 0.70
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Input,
    [string]$Out,
    [switch]$DryRun,
    [double]$Threshold,
    [double]$RefTime,
    [double]$Pad = 1.0,
    [double]$MinSeg = 3.0,
    [double]$Gap = 1.5
)

$ErrorActionPreference = "Stop"

$py = Get-Command "py" -ErrorAction SilentlyContinue
if (-not $py) { throw "The 'py' Python launcher was not found on PATH." }

$script = Join-Path $PSScriptRoot "extract_rallies.py"
if (-not (Test-Path $script)) { throw "extract_rallies.py not found next to this wrapper." }
if (-not (Test-Path $Input)) { throw "Input not found: $Input" }

$pyArgs = @("-3.12", "-u", $script, $Input,
    "--pad", $Pad, "--min-seg", $MinSeg, "--gap", $Gap)
if ($Out) { $pyArgs += @("--out", $Out) }
if ($DryRun) { $pyArgs += "--dry-run" }
if ($PSBoundParameters.ContainsKey("Threshold")) { $pyArgs += @("--threshold", $Threshold) }
if ($PSBoundParameters.ContainsKey("RefTime")) { $pyArgs += @("--ref-time", $RefTime) }

& py @pyArgs
exit $LASTEXITCODE
