<#
.SYNOPSIS
  One-time installer for the youtube-clip skill. Downloads yt-dlp.exe and ffmpeg (ffmpeg.exe +
  ffprobe.exe) into a bin folder on your PATH.

.DESCRIPTION
  Safe to re-run. Skips any tool already found on your PATH. Works on corporate networks
  (falls back to a certificate-validation bypass if TLS inspection blocks the download, and prefers
  the GitHub CLI when available).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File Install-Tools.ps1
  powershell -ExecutionPolicy Bypass -File Install-Tools.ps1 -BinDir "C:\tools"
#>
[CmdletBinding()]
param(
    [string]$BinDir = (Join-Path $env:USERPROFILE "bin")
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Get-File {
    param([string]$Url, [string]$OutFile)
    # 1. Prefer GitHub CLI for github.com URLs (it handles the inspecting proxy/cert cleanly).
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh -and $Url -match "github.com/([^/]+/[^/]+)/releases/.*/download/(.+)$") {
        $repo = $Matches[1]; $asset = Split-Path $Url -Leaf
        try {
            & gh release download latest -R $repo -p $asset -O $OutFile --clobber
            if ($LASTEXITCODE -eq 0 -and (Test-Path $OutFile)) { return }
        } catch { }
    }
    # 2. Plain download.
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
        return
    } catch {
        Write-Host "    strict download failed, retrying without certificate verification (corporate network)..." -ForegroundColor Yellow
    }
    # 3. Corporate-network fallback: bypass cert validation (these are public binaries).
    $cb = [Net.ServicePointManager]::ServerCertificateValidationCallback
    try {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
    } finally {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = $cb
    }
}

# --- Ensure bin dir ---
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Write-Step "Installing into $BinDir"

# --- yt-dlp ---
if (Get-Command yt-dlp -ErrorAction SilentlyContinue) {
    Write-Host "    yt-dlp already on PATH, skipping."
} elseif (Test-Path (Join-Path $BinDir "yt-dlp.exe")) {
    Write-Host "    yt-dlp.exe already in $BinDir, skipping."
} else {
    Write-Step "Downloading yt-dlp.exe"
    Get-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" (Join-Path $BinDir "yt-dlp.exe")
    Write-Host "    done."
}

# --- ffmpeg + ffprobe ---
$haveFfmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or (Test-Path (Join-Path $BinDir "ffmpeg.exe"))
if ($haveFfmpeg) {
    Write-Host "    ffmpeg already present, skipping."
} else {
    Write-Step "Downloading ffmpeg (about 170 MB)"
    $zip = Join-Path $env:TEMP "ffmpeg-ytclip.zip"
    Get-File "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" $zip
    Write-Step "Extracting ffmpeg.exe and ffprobe.exe"
    $tmp = Join-Path $env:TEMP "ffmpeg-ytclip-extract"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | ForEach-Object {
        Copy-Item $_.FullName $BinDir -Force
        Write-Host "    installed $($_.Name)"
    }
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# --- PATH ---
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$BinDir*") {
    Write-Step "Adding $BinDir to your user PATH"
    [Environment]::SetEnvironmentVariable("PATH", "$userPath;$BinDir", "User")
    $env:PATH = "$env:PATH;$BinDir"
    Write-Host "    added (open a new terminal for it to take effect everywhere)."
}

Write-Host ""
Write-Step "All set. Verify:"
& (Join-Path $BinDir "yt-dlp.exe") --version
& (Join-Path $BinDir "ffmpeg.exe") -version | Select-Object -First 1
Write-Host ""
Write-Host "Next: powershell -File Clip-YouTube.ps1 ""<url>"" -Start 5:21:10 -End 6:54:28" -ForegroundColor Green
