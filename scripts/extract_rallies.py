#!/usr/bin/env python3
"""
extract_rallies.py - strip the dead time out of a racket-sport broadcast.

Keeps only the live court-camera footage (rallies) and drops the filler that sits
between points: replays, player close-ups, crowd shots, score graphics, ad bumpers.

How it works (no model, no training, no tagging):
  1. Sample a couple of frames a second at low resolution.
  2. Fingerprint each frame by a coarse colour histogram.
  3. Auto-pick the dominant recurring camera (the live court view) as the reference,
     because the rally angle is always the single most-used shot in a broadcast.
     Override with --ref-time if the auto pick is wrong.
  4. Keep frames whose colour matches the court reference, pad each kept run, drop
     flashes shorter than --min-seg, merge runs closer than --gap.
  5. Render the survivors with a stream copy (fast, full quality, keyframe-snapped).

Writes a <output>.segments.csv alongside the result so you can review the cut first
(use --dry-run to stop after the CSV and skip rendering).

Deps: ffmpeg + ffprobe (found in ~/bin or on PATH), numpy. Pillow not required.
"""
import argparse
import os
import shutil
import subprocess
import sys
import csv

import numpy as np

ANALYSIS_W, ANALYSIS_H = 160, 90          # forced 16:9 analysis frame
FRAME_BYTES = ANALYSIS_W * ANALYSIS_H * 3
BINS_PER_CH = 4                            # 4 levels/channel -> 64-bin RGB histogram


def find_bin(name, override):
    if override:
        return override
    home_bin = os.path.join(os.path.expanduser("~"), "bin", name + ".exe")
    if os.path.exists(home_bin):
        return home_bin
    found = shutil.which(name)
    if found:
        return found
    sys.exit(f"Could not find {name}. Pass --{name} or put it in ~/bin / on PATH.")


def hms(seconds):
    s = max(0.0, float(seconds))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:d}:{m:02d}:{sec:06.3f}"


def probe_duration(ffprobe, path):
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def sample_histograms(ffmpeg, path, fps):
    """Stream low-res frames from ffmpeg, return (times[], hist[N,64] normalised)."""
    cmd = [
        ffmpeg, "-v", "error", "-i", path,
        "-vf", f"fps={fps},scale={ANALYSIS_W}:{ANALYSIS_H}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    shift = 8 - 2  # keep top 2 bits -> 4 levels
    hists = []
    idx = 0
    try:
        while True:
            buf = proc.stdout.read(FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                break
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            q = arr >> shift                       # 0..3 per channel
            code = q[:, 0] * 16 + q[:, 1] * 4 + q[:, 2]
            h = np.bincount(code, minlength=64).astype(np.float32)
            h /= h.sum() + 1e-9
            hists.append(h)
            idx += 1
    finally:
        proc.stdout.close()
        proc.wait()
    if not hists:
        sys.exit("No frames sampled - is the input a valid video?")
    times = np.arange(len(hists), dtype=np.float64) / fps
    return times, np.vstack(hists)


def pick_reference(hists, ref_idx=None):
    """Return the court reference histogram = medoid (most typical look), or a given index."""
    if ref_idx is not None:
        return hists[ref_idx]
    n = len(hists)
    # subsample for the medoid search so it stays cheap on long matches
    cap = 600
    sel = np.linspace(0, n - 1, min(n, cap)).astype(int)
    sub = hists[sel]
    # histogram intersection similarity, vectorised over the subsample
    # sim(i,j) = sum(min(sub_i, sub_j)); medoid = max mean similarity
    best_i, best_score = sel[0], -1.0
    for k, i in enumerate(sel):
        sims = np.minimum(sub, hists[i]).sum(axis=1)
        score = sims.mean()
        if score > best_score:
            best_score, best_i = score, i
    return hists[best_i]


def otsu_threshold(values):
    """Split a 0..1 distribution into low/high; returns the valley point."""
    hist, edges = np.histogram(values, bins=50, range=(0.0, 1.0))
    total = hist.sum()
    if total == 0:
        return 0.5
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = total - w0
    mu0 = np.cumsum(hist * centres) / np.maximum(w0, 1)
    mu_total = (hist * centres).sum() / total
    mu1 = (mu_total * total - np.cumsum(hist * centres)) / np.maximum(w1, 1)
    between = w0 * w1 * (mu0 - mu1) ** 2
    return float(centres[np.argmax(between)])


def build_segments(times, keep, fps, duration, pad, gap, min_seg):
    step = 1.0 / fps
    # contiguous runs of kept frames -> core [start, end]
    cores = []
    i = 0
    n = len(keep)
    while i < n:
        if keep[i]:
            j = i
            while j + 1 < n and keep[j + 1]:
                j += 1
            cores.append([times[i], times[j] + step])
            i = j + 1
        else:
            i += 1
    # drop short cores (a flash that survived as a single sample)
    cores = [c for c in cores if (c[1] - c[0]) >= min_seg]
    # pad, clamp
    padded = [[max(0.0, a - pad), min(duration, b + pad)] for a, b in cores]
    # merge runs closer than `gap` (after padding)
    merged = []
    for seg in padded:
        if merged and seg[0] - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], seg[1])
        else:
            merged.append(list(seg))
    return merged


def write_csv(csv_path, segments):
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "start_s", "end_s", "start_hms", "end_hms", "duration_s"])
        for i, (a, b) in enumerate(segments, 1):
            w.writerow([i, f"{a:.3f}", f"{b:.3f}", hms(a), hms(b), f"{b - a:.3f}"])


def render(ffmpeg, src, segments, out_path):
    list_path = out_path + ".concat.txt"
    src_fwd = os.path.abspath(src).replace("\\", "/")
    with open(list_path, "w") as f:
        for a, b in segments:
            f.write(f"file '{src_fwd}'\n")
            f.write(f"inpoint {a:.3f}\n")
            f.write(f"outpoint {b:.3f}\n")
    cmd = [
        ffmpeg, "-v", "error", "-stats", "-y",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", "-movflags", "+faststart", out_path,
    ]
    subprocess.run(cmd, check=True)
    os.remove(list_path)


def main():
    ap = argparse.ArgumentParser(description="Keep only live court-camera footage; cut the dead time.")
    ap.add_argument("input")
    ap.add_argument("--out", help="output MP4 (default: <input>_rallies.mp4)")
    ap.add_argument("--fps", type=float, default=2.0, help="analysis sample rate (default 2)")
    ap.add_argument("--threshold", type=float, default=None, help="match 0..1 (default: auto/Otsu)")
    ap.add_argument("--ref-time", type=float, default=None, help="seconds; force the court frame to sample here")
    ap.add_argument("--pad", type=float, default=1.0, help="seconds padded onto each kept run (default 1)")
    ap.add_argument("--min-seg", type=float, default=3.0, help="drop kept runs shorter than this (default 3)")
    ap.add_argument("--gap", type=float, default=1.5, help="merge kept runs closer than this (default 1.5)")
    ap.add_argument("--dry-run", action="store_true", help="write the CSV only, do not render")
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--ffprobe", default=None)
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"Input not found: {args.input}")

    ffmpeg = find_bin("ffmpeg", args.ffmpeg)
    ffprobe = find_bin("ffprobe", args.ffprobe)
    out_path = args.out or os.path.splitext(args.input)[0] + "_rallies.mp4"
    csv_path = out_path + ".segments.csv"

    duration = probe_duration(ffprobe, args.input)
    print(f"Source: {os.path.basename(args.input)}  ({hms(duration)})")
    print(f"Sampling at {args.fps} fps and fingerprinting by colour ...")
    times, hists = sample_histograms(ffmpeg, args.input, args.fps)
    print(f"  {len(times)} frames analysed.")

    ref_idx = None
    if args.ref_time is not None:
        ref_idx = int(round(args.ref_time * args.fps))
        ref_idx = max(0, min(len(hists) - 1, ref_idx))
        print(f"Court reference: forced at {hms(args.ref_time)}.")
    else:
        print("Court reference: auto-detecting the dominant camera ...")
    ref = pick_reference(hists, ref_idx)

    sims = np.minimum(hists, ref).sum(axis=1)            # 0..1 histogram intersection
    thr = args.threshold if args.threshold is not None else otsu_threshold(sims)
    keep = sims >= thr
    pct_frames = 100.0 * keep.mean()
    print(f"Match threshold: {thr:.3f}  ({pct_frames:.0f}% of frames look like the court)")

    if pct_frames < 5:
        print("  WARNING: very little kept. The court look may be mis-detected - "
              "try --ref-time at a known rally, or lower --threshold.")
    elif pct_frames > 92:
        print("  WARNING: almost nothing dropped. Either it is already mostly rallies, "
              "or the colour fingerprint is not separating the dead time - try a higher --threshold.")

    segments = build_segments(times, keep, args.fps, duration,
                              args.pad, args.gap, args.min_seg)
    kept = sum(b - a for a, b in segments)
    print(f"Kept {len(segments)} segments = {hms(kept)} of {hms(duration)} "
          f"({100.0 * kept / duration:.0f}% of the match).")

    write_csv(csv_path, segments)
    print(f"Segment list: {csv_path}")

    if not segments:
        sys.exit("No segments survived - nothing to render. Adjust --threshold / --ref-time.")

    if args.dry_run:
        print("Dry run - skipped rendering. Review the CSV, then re-run without --dry-run.")
        return

    print(f"Rendering (stream copy) -> {out_path}")
    render(ffmpeg, args.input, segments, out_path)
    print(f"Done: {out_path}")


if __name__ == "__main__":
    main()
