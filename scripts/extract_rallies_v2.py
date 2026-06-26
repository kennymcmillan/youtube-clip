#!/usr/bin/env python3
"""
extract_rallies_v2.py - structural + audio rally detection (Phase 1 + 2).

Improves on extract_rallies.py (a pure colour heuristic) using colour-blind signals,
because the live-play shot is a fixed locked-off wide camera with a constant court/wall
structure and four players, while court COLOURS vary by tournament.

Cues (all CPU-only, no GPU, no training, no tagging; deps: opencv + scipy + numpy):
  - STRUCTURE  : grayscale perceptual hash (pHash). The fixed wide shot forms the dominant
                 near-duplicate cluster; close-ups/crowd/graphics/replays sit far away in
                 Hamming distance. Self-calibrated by medoid + Otsu (same shape as v1).
  - MOTION     : cv2.phaseCorrelate global shift between consecutive frames. The live camera
                 is locked off (~0 motion); pans/zooms/slow-mo follows read sustained motion.
  - AUDIO      : percussive spectral-flux onset density (HPSS-lite via median filters). A live
                 rally is a regular train of ball "pock" impacts; replays/graphics differ.
                 Used as two overrides: replay-veto (court-looking but no impacts -> drop) and
                 live-play-rescue (clear impact train -> keep even if a graphic dropped it).

Fusion: visual_keep = structure AND locked-off; then audio overrides; then the SAME
build_segments / render as v1 (imported, unchanged). Writes a debug CSV of every cue track
for review. Use --dry-run to stop after the segments CSV.

Toggles for A/B testing: --no-motion, --no-audio, --no-veto, --no-rescue.
"""
import argparse
import os
import sys
import csv
import subprocess
from pathlib import Path

import numpy as np
import cv2
import scipy.fft
import scipy.signal
import scipy.ndimage
import scipy.io.wavfile

# reuse the proven v1 helpers (segments, render, probe, tool lookup)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_rallies as v1  # noqa: E402

ANALYSIS_W, ANALYSIS_H = 160, 90
FRAME_BYTES = ANALYSIS_W * ANALYSIS_H  # gray, 1 byte/pixel
SCRATCH = os.environ.get("TEMP", os.path.expanduser("~"))


# ---------- Phase 1: structural + motion ----------

def phash_bits(gray):
    """64-bit pHash of a grayscale frame (scipy DCT, no imagehash dep)."""
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = scipy.fft.dctn(small, norm="ortho")
    low = d[:8, :8]
    med = np.median(low.flatten()[1:])  # exclude DC term
    return (low > med).flatten().astype(np.uint8)


def sample_visual(ffmpeg, path, fps):
    """One gray decode pass -> (times, phash bits [N,64], motion[N])."""
    cmd = [ffmpeg, "-v", "error", "-i", path,
           "-vf", f"fps={fps},scale={ANALYSIS_W}:{ANALYSIS_H}",
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    hann = cv2.createHanningWindow((ANALYSIS_W, ANALYSIS_H), cv2.CV_32F)
    hashes, motion = [], []
    prev = None
    try:
        while True:
            buf = proc.stdout.read(FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                break
            gray = np.frombuffer(buf, np.uint8).reshape(ANALYSIS_H, ANALYSIS_W)
            hashes.append(phash_bits(gray))
            cur = gray.astype(np.float32)
            if prev is None:
                motion.append(0.0)
            else:
                (dx, dy), _resp = cv2.phaseCorrelate(prev, cur, hann)
                motion.append(float(np.hypot(dx, dy)))
            prev = cur
    finally:
        proc.stdout.close()
        proc.wait()
    if not hashes:
        sys.exit("No frames sampled - is the input a valid video?")
    times = np.arange(len(hashes), dtype=np.float64) / fps
    return times, np.vstack(hashes), np.asarray(motion, dtype=np.float64)


def structural_distance(hashes):
    """Dominant-cluster medoid (min mean Hamming) -> normalised distance 0..1 per frame."""
    n = len(hashes)
    sel = np.linspace(0, n - 1, min(n, 600)).astype(int)
    sub = hashes[sel]
    best_i, best = sel[0], 1e9
    for i in sel:
        mean_h = (sub != hashes[i]).sum(axis=1).mean()
        if mean_h < best:
            best, best_i = mean_h, i
    dist = (hashes != hashes[best_i]).sum(axis=1) / 64.0
    return dist


# ---------- Phase 2: audio onset density ----------

def audio_onset_density(ffmpeg, path, duration, sr=16000, block_s=20.0):
    """Per-second ball-impact onset density via percussive spectral flux (HPSS-lite)."""
    wav = os.path.join(SCRATCH, "rallies_v2_" + Path(path).stem + ".wav")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", path,
                    "-ac", "1", "-ar", str(sr), "-vn",
                    "-c:a", "pcm_s16le", wav], check=True)
    try:
        _sr, y = scipy.io.wavfile.read(wav)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y.astype(np.float32)
    if np.max(np.abs(y)) > 0:
        y = y / np.max(np.abs(y))

    nper, hop = 1024, 512
    fps_env = sr / hop
    block = int(block_s * sr)
    flux_all = []
    # carry the last percussive column across blocks so flux is continuous
    prev_col = None
    for start in range(0, len(y), block):
        seg = y[start:start + block + nper]  # small overlap
        if len(seg) < nper:
            break
        f, t, Z = scipy.signal.stft(seg, fs=sr, nperseg=nper, noverlap=nper - hop)
        mag = np.abs(Z).astype(np.float32)
        # HPSS-lite: median along time = harmonic, along freq = percussive
        harm = scipy.ndimage.median_filter(mag, size=(1, 17))
        perc = scipy.ndimage.median_filter(mag, size=(17, 1))
        mask = (perc ** 2) / (perc ** 2 + harm ** 2 + 1e-9)
        pmag = mag * mask
        cols = pmag.T  # (frames, freq)
        if prev_col is not None:
            cols = np.vstack([prev_col[None, :], cols])
        diff = np.diff(cols, axis=0)
        flux = np.maximum(diff, 0).sum(axis=1)
        flux_all.append(flux)
        prev_col = pmag.T[-1]
    if not flux_all:
        return np.zeros(int(np.ceil(duration)) + 1)
    flux = np.concatenate(flux_all)
    # peak-pick onsets: local max above local mean + k*std
    if flux.max() > 0:
        flux = flux / flux.max()
    win = max(3, int(0.3 * fps_env))
    local_mean = scipy.ndimage.uniform_filter1d(flux, win)
    local_std = np.sqrt(scipy.ndimage.uniform_filter1d(flux ** 2, win) - local_mean ** 2 + 1e-9)
    thr = local_mean + 1.2 * local_std
    is_peak = (flux > thr) & (flux > 0.05)
    is_peak &= np.r_[True, flux[1:] >= flux[:-1]] & np.r_[flux[:-1] >= flux[1:], True]
    onset_times = np.where(is_peak)[0] / fps_env
    # density per 1s bin (counts within a centred 5s window)
    nbins = int(np.ceil(duration)) + 1
    counts = np.zeros(nbins)
    for ot in onset_times:
        b = int(ot)
        if 0 <= b < nbins:
            counts[b] += 1
    density = scipy.ndimage.uniform_filter1d(counts, 5)  # onsets/sec, smoothed over ~5s
    return density


# ---------- fusion ----------

def fuse(times, dist, motion, density, fps, args):
    n = len(times)
    # structural keep
    thr_s = args.threshold if args.threshold is not None else v1.otsu_threshold(dist)
    thr_s = float(np.clip(thr_s, 0.10, 0.45))
    struct_keep = dist <= thr_s
    # motion keep (self-calibrated from in-cluster jitter)
    if args.no_motion:
        motion_keep = np.ones(n, bool)
        thr_m = float("inf")
    else:
        incl = motion[struct_keep]
        base = np.percentile(incl, 90) if incl.size else np.percentile(motion, 50)
        thr_m = max(args.motion_threshold if args.motion_threshold is not None else base * 1.8, 0.4)
        motion_keep = motion <= thr_m
    visual_keep = struct_keep & motion_keep

    # audio density aligned to each sampled frame
    ad = np.zeros(n)
    if density is not None:
        idx = np.clip(times.astype(int), 0, len(density) - 1)
        ad = density[idx]
    keep = visual_keep.copy()

    rally_lvl = quiet_lvl = 0.0
    if density is not None and args.audio:
        live_ad = ad[visual_keep]
        med_live = np.median(live_ad) if live_ad.size else 0.0
        rally_lvl = max(0.6, 0.5 * med_live)
        quiet_lvl = 0.25 * med_live
        # live-play rescue: a sustained impact train keeps the frame even if visual dropped it
        if not args.no_rescue and med_live > 0:
            strong = scipy.ndimage.uniform_filter1d((ad >= rally_lvl).astype(float), 5) >= 0.6
            keep |= strong
        # replay-veto: a court-looking run with essentially no impacts is a replay -> drop
        if not args.no_veto and med_live > 0:
            keep = _veto_silent_runs(keep, ad, times, fps, quiet_lvl, min_run=5.0)

    return keep, dict(thr_struct=thr_s, thr_motion=thr_m, rally_lvl=rally_lvl,
                      quiet_lvl=quiet_lvl, struct_keep=struct_keep, motion_keep=motion_keep,
                      visual_keep=visual_keep, ad=ad)


def _veto_silent_runs(keep, ad, times, fps, quiet_lvl, min_run):
    out = keep.copy()
    n = len(keep)
    i = 0
    while i < n:
        if out[i]:
            j = i
            while j + 1 < n and out[j + 1]:
                j += 1
            dur = times[j] - times[i]
            if dur >= min_run and ad[i:j + 1].mean() < quiet_lvl:
                out[i:j + 1] = False
            i = j + 1
        else:
            i += 1
    return out


def write_debug(path, times, dist, motion, density, info, keep):
    ad = info["ad"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "struct_dist", "motion", "audio_density",
                    "struct_keep", "motion_keep", "visual_keep", "keep_final"])
        for i, t in enumerate(times):
            w.writerow([f"{t:.2f}", f"{dist[i]:.3f}", f"{motion[i]:.3f}", f"{ad[i]:.3f}",
                        int(info["struct_keep"][i]), int(info["motion_keep"][i]),
                        int(info["visual_keep"][i]), int(keep[i])])


def main():
    ap = argparse.ArgumentParser(description="Structural + audio rally detection (Phase 1+2).")
    ap.add_argument("input")
    ap.add_argument("--out")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--threshold", type=float, default=None, help="structural Hamming cutoff 0..1 (default auto)")
    ap.add_argument("--motion-threshold", type=float, default=None)
    ap.add_argument("--pad", type=float, default=1.0)
    ap.add_argument("--min-seg", type=float, default=3.0)
    ap.add_argument("--gap", type=float, default=1.5)
    ap.add_argument("--no-motion", action="store_true")
    ap.add_argument("--audio", action="store_true",
                    help="enable EXPERIMENTAL audio overrides (onset cue does not yet separate "
                         "rallies from commentary/crowd - off by default)")
    ap.add_argument("--no-veto", action="store_true")
    ap.add_argument("--no-rescue", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--ffprobe", default=None)
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"Input not found: {args.input}")
    ffmpeg = v1.find_bin("ffmpeg", args.ffmpeg)
    ffprobe = v1.find_bin("ffprobe", args.ffprobe)
    out_path = args.out or os.path.splitext(args.input)[0] + "_rallies_v2.mp4"
    csv_path = out_path + ".segments.csv"
    dbg_path = out_path + ".debug.csv"

    duration = v1.probe_duration(ffprobe, args.input)
    print(f"Source: {os.path.basename(args.input)}  ({v1.hms(duration)})")

    print(f"[1/3] Visual pass: gray {args.fps} fps, pHash + phaseCorrelate ...")
    times, hashes, motion = sample_visual(ffmpeg, args.input, args.fps)
    dist = structural_distance(hashes)
    print(f"      {len(times)} frames; structural distance median {np.median(dist):.3f}")

    density = None
    if args.audio:
        print("[2/3] Audio pass (EXPERIMENTAL): percussive onset density ...")
        try:
            density = audio_onset_density(ffmpeg, args.input, duration)
            print(f"      onset density: median {np.median(density):.2f}/s, peak {density.max():.2f}/s")
        except Exception as e:
            print(f"      audio cue FAILED ({type(e).__name__}: {e}) - continuing visual-only.")
            density = None

    print("[3/3] Fusing cues ...")
    keep, info = fuse(times, dist, motion, density, args.fps, args)
    print(f"      structural thr {info['thr_struct']:.3f}, motion thr {info['thr_motion']:.3f}, "
          f"rally {info['rally_lvl']:.2f}/s, quiet {info['quiet_lvl']:.2f}/s")
    print(f"      visual-keep frames {100*info['visual_keep'].mean():.0f}%, "
          f"final-keep frames {100*keep.mean():.0f}%")

    segments = v1.build_segments(times, keep, args.fps, duration, args.pad, args.gap, args.min_seg)
    kept = sum(b - a for a, b in segments)
    print(f"Kept {len(segments)} segments = {v1.hms(kept)} of {v1.hms(duration)} "
          f"({100*kept/duration:.0f}% of the match).")

    v1.write_csv(csv_path, segments)
    write_debug(dbg_path, times, dist, motion, density, info, keep)
    print(f"Segments: {csv_path}")
    print(f"Debug tracks: {dbg_path}")

    if not segments:
        sys.exit("No segments survived - adjust thresholds.")
    if args.dry_run:
        print("Dry run - skipped rendering.")
        return
    print(f"Rendering (stream copy) -> {out_path}")
    v1.render(ffmpeg, args.input, segments, out_path)
    print(f"Done: {out_path}")


if __name__ == "__main__":
    main()
