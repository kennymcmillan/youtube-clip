# Rally detection: improvement plan

How to make `extract_rallies.py` better with modern computer-vision (and audio)
techniques, while staying CPU-only, no-GPU, no-training and no-tagging.

Based on a four-way agent investigation (June 2026). The headline: the live-play
shot is a fixed, locked-off wide camera with a constant court/wall structure and
always four players. That makes colour the *weakest* signal we could have picked,
and there are cheaper, sturdier ones sitting right there.

---

## Where v1 stands, and why it wobbles

Current method: sample ~2 fps at 160x90, take a 64-bin RGB colour histogram per
frame, auto-pick the dominant camera by medoid, keep frames whose histogram
intersection beats an Otsu threshold, then pad/merge/min-duration and stream-copy
stitch. On the 93-min Hassan Waly match it kept 43% and looked right.

It is a pure colour heuristic, so it struggles exactly here:
- **Court colours vary by tournament**, so the "court look" it learns does not carry across feeds and the threshold needs babysitting.
- **Slow-mo replays share the court colour**, so they get kept.
- **A full-frame graphic over live play** shifts the histogram, so live play gets dropped.
- It cannot tell a **second court angle** from the main one by colour alone.

## The core idea

Stop asking "what colour is on screen" and start asking three colour-blind questions
the broadcast answers for us:

1. **Is this the same fixed structure the match shows most often?** (court + glass walls, by shape not colour)
2. **Is the camera locked off?** (live) or panning/zooming/slow (replay, follow, transition)
3. **Is the ball actually in play?** (a regular train of racket impacts in the audio)

Plus a confirmation cue when needed: **are there ~4 player-sized people spread across a wide frame?**

None of these need labels, training or a GPU. Two of them (1 and 2) replace and
extend what `pick_reference`/`otsu_threshold` already do; the rest bolt on as extra
votes. `build_segments` and `render` stay exactly as they are.

---

## Recommended changes, cheapest-and-highest-leverage first

### Phase 1 — Structural camera lock (replaces the colour fingerprint)

Swap the colour cue for a **grayscale perceptual-hash dominant-cluster**, and add a
**static-camera gate**. This is the single best drop-in and slots into the existing
code shape almost line-for-line.

- Decode the sample frames as `-pix_fmt gray` instead of `rgb24` (a third of the bytes, colour-free by construction; the 2 fps decode is the tool's dominant cost, every CV op below is cheap next to it).
- **Perceptual hash:** compute a 64-bit dHash/pHash per frame (`imagehash`, or `cv2.img_hash` from opencv-contrib). The fixed wide shot forms the biggest near-duplicate cluster: four small moving players barely shift the low-frequency structure, while close-ups, crowd, graphics and replays sit 20-30+ Hamming bits away. Reuse `pick_reference` (medoid) and `otsu_threshold`, just swap histogram-intersection for Hamming distance. A second court angle falls out for free as a minority cluster.
- **Static-camera gate:** `cv2.phaseCorrelate` between consecutive grayscale frames returns a sub-pixel global shift plus a confidence. The locked-off live camera reads ~0 motion (the rigid court fills the frame and dominates; four small players are noise the peak ignores); pans/zooms/slow-mo follows read sustained motion. ~0.3 ms/frame. Its confidence drop also marks hard cuts, handy for snapping segment edges.
- **Self-calibrate the motion threshold** from the dominant cluster's own jitter rather than hard-coding zero, in case a given feed is operator-tracked rather than truly locked.

Fixes: colour variance across tournaments, panning/zooming replays, follow shots, second-angle confusion.
Deps: `opencv-python` (+`opencv-contrib-python` or `imagehash`), all fine via the Kakao mirror. No GPU, no tagging.
Cost: seconds of CPU on top of the existing decode.

### Phase 2 — Audio rally gate (the biggest single win, and nearly free)

Colour and structure still cannot tell a **same-angle slow-mo replay** from live play, and
a **graphic over live play** still trips the visual cues. Audio is independent of the
pixels and answers "is the ball in play", so it fixes both at once.

- Demux mono 22.05 kHz with ffmpeg (`-ac 1 -ar 22050 -vn`).
- Split percussive from harmonic (`librosa.effects.hpss`) to strip commentary/music, then an onset-strength envelope + peak-pick on the percussive channel. Score a sliding 3-5 s window by **onset density + inter-onset-interval regularity**: a sparse, roughly periodic ~0.5-2 Hz impact train = rally; near-silence = dead time; dense cloud = applause (a point-end boundary).
- Padel's pock is sharper than tennis (depressurised ball, glass walls), so this should work at least as well; the enclosed-court reverb smears tails a little but onset detection fires on the attack.
- **Fusion overrides** (the part that fixes the hard cases):
  - *Replay veto:* structure says court but the audio shows no impacts for >~5 s (and/or music bed present) -> DROP. That is a replay.
  - *Live-play rescue:* a sustained impact train for >=3 s -> KEEP, even if a graphic pushed the visual score down.
- Optional bolt-on: **YAMNet** (MobileNet, 521 AudioSet classes incl. Applause/Cheering, via `onnxruntime`/`tflite-runtime`, well under a minute for 90 min) to detect point-end applause and snap segment OUT points cleanly.

Deps: `librosa` (pulls scipy/soundfile/numba; degrades to a pure numpy/scipy spectral-flux version if numba fights the AV). No GPU, no tagging.
Cost: seconds to low tens of seconds for a 90-min track.
Caveat: rests on the feed actually having court-mic audio with audible ball strikes. Validate on one match first.

### Phase 3 — Four-players confirmation (optional, only if 1+2 leave errors)

A colour- and structure-independent sanity check using your "always four players" fact.

- Pretrained **YOLOv8n / YOLO11n** COCO `person` class (no tagging: counting people is exactly what COCO weights already do well). Export once to **ONNX** (or OpenVINO on the Intel laptop) and run with `onnxruntime` at `imgsz=320`, ~12-25 ms/frame; run it only every 2nd-3rd sampled frame to stay well inside a few minutes.
- Gate rule (not "==4" but occlusion-robust): keep `person` boxes with height between ~6% and ~33% of frame height and an upright aspect (kills close-up faces and tiny crowd figures), then require `3 <= count <= 5` whose horizontal centres span >40% of the frame width. Smooth over a 3-sample window (hysteresis) so a serve walk-off does not flicker the gate.
- Use as an AND with the structural keep: rejects residual close-ups (1-2 big boxes), crowd shots (many tiny clustered boxes) and graphics/ads (0 boxes).

Install tip for the locked-down laptop: download a prebuilt `yolov8n.onnx` and ship only `onnxruntime` + `opencv-python-headless` + `numpy`, avoiding the heavy `torch`/`ultralytics` stack entirely.
Deps: `onnxruntime`, `opencv-python-headless`. No GPU, no tagging.
Cost: a few minutes of CPU at most.

### Phase 4 — Refinements, only if a specific feed misbehaves

- **Edge / Chamfer court-structure score:** Canny the self-calibrated background plate (median image, built only from frames already in the dominant cluster), distance-transform it once, and per frame read the mean DT value at the frame's edges. Directed template->frame matching means added banner/overlay edges do not hurt; only the court structure going missing does. The most *discriminative* colour-free cue, exposure-invariant. Reach for it if same-angle replays or heavy graphics remain a problem.
- **Replay-sting / score-bug template:** replays are usually bracketed by short branded wipes; a court-looking run sandwiched between two short structure-mismatch bursts is a replay. A small persistent score-bug template check is another tell.

---

## Architecture: from one cue to pluggable late fusion

Refactor `extract_rallies.py` into a **per-match self-calibration pass** that produces
several aligned per-time cue tracks on a common 1 Hz grid, then a small fusion step,
then the unchanged `build_segments`/`render`:

```
sample (gray, 2fps) ──► cues:  V_struct (phash-to-medoid)
                               V_motion (phaseCorrelate)         late fusion ──► keep[] ──► build_segments ──► render
                               A_rally  (onset train)        (weighted vote +     (unchanged)        (unchanged)
                               A_end    (applause, optional)  hard overrides)
                               P_players(YOLO count, optional)
```

Fusion = weighted base score (`w_v*V_struct + w_a*A_rally`, start ~0.5/0.5) plus the two
hard overrides (replay veto, live-play rescue), then a 3-state machine (live / replay /
dead) feeding the existing smoothing. Keep the **colour cue as one optional vote and a
fallback**, so nothing regresses if a feed has odd audio or an unusual court.

Each cue is a function returning a 0..1 track, switchable by flag (`--cue-audio`,
`--cue-players`, ...), so we ship Phase 1, measure, then layer Phase 2, etc.

---

## What to deliberately NOT do

- **Full TrackNet / TrackNetV2/V3 ball-tracking nets** — ~1 fps on CPU (hours per match) unless slimmed and re-exported. GPU-shaped. Skip.
- **SoccerNet action-spotting / camera-shot-segmentation, stroke-recognition nets** — right *idea* (frame-level camera-type classes, replay grounding) but soccer/other-sport pretrained only; adapting to padel means labelling + GPU training. Borrow the taxonomy idea, not the weights.
- **Training our own detector / Roboflow tagging** — unnecessary. COCO `person` already gives the four-players cue; padel-specific community models on Roboflow are small, unverified, and only earn their keep for tiny far-court players or ball tracking, neither of which we need to gate live play.
- **CLIP / SigLIP zero-shot frame tagging** — genuinely no-tagging and attacks every failure mode by prompt ("wide live rally" vs "slow-motion replay" vs "crowd"...), but the image tower is the heaviest CPU option here (needs INT8/OpenVINO to hit ~15-40 ms/frame). Keep it in the back pocket as a Phase 5 if the cheap cues leave residual errors; do not lead with it.

---

## The one honestly-hard case

A **replay shot from the identical static wide angle** looks the same to every visual
cue. The defences, in order of strength: (1) **audio** — a replay has no live ball-impact
train, so the replay veto drops it; (2) the **replay-sting sandwich** heuristic; (3)
slow-motion shows abnormally low inter-frame change at our fixed 2 fps sampling. Audio is
the real fix, which is the main reason Phase 2 matters as much as Phase 1.

---

## Suggested build order and validation

1. **Phase 1 + Phase 2 together** as the next slice. Cheap, no scary deps, and between them they fix every named failure mode. Refactor to the cue-track + fusion shape while doing it.
2. **Validate before trusting:** for each test feed, plot the pHash Hamming-to-medoid histogram and the audio onset-density track and confirm they are cleanly bimodal; dry-run the segments CSV; spot-check thumbnails at kept vs dropped times; run across 2-3 tournaments with different court colours.
3. **Phase 3** only if 1+2 leave visible close-up/crowd/graphic leakage.
4. **Phase 4 / 5** per-feed, as needed.

Measure CPU on this actual laptop before committing the heavier cues (time a YOLOv8n
ONNX pass and, if used, a quantised CLIP tower over ~500 frames); the minute estimates
above assume ONNX/INT8 and the corporate AV can slow first model load.

---

## Dependency summary

| Phase | New deps | GPU | Tagging | CPU cost (90-min match) |
|------|----------|-----|---------|--------------------------|
| 1 Structural | opencv-python (+contrib or imagehash) | No | No | seconds |
| 2 Audio | librosa (numpy/scipy fallback); optional onnxruntime for YAMNet | No | No | seconds-tens of seconds |
| 3 Players | onnxruntime, opencv-python-headless, prebuilt yolov8n.onnx | No | No | a few minutes |
| 4 Edge/template | (none beyond opencv) | No | No | tens of seconds |
| 5 CLIP (optional) | onnxruntime/openvino + model | No | No | a few minutes (INT8) |

---

## Build log

### 2026-06-26 — Phase 1 built and verified; Phase 2 audio parked

Implemented in `scripts/extract_rallies_v2.py` (separate from the working v1; deps OpenCV +
scipy + numpy only, no new installs, no librosa/numba). pHash is self-implemented via scipy
DCT; HPSS-lite via scipy median filters. Reuses v1's `build_segments`/`render`/`otsu_threshold`.

Tested on the 93-min Hassan Waly Arab Padel Tour match:

- **Structural cue works well.** The pHash distance-to-medoid is cleanly **bimodal**: a court
  cluster at ~0.06-0.20, an empty valley ~0.25, everything else above ~0.40. Otsu (clamped)
  landed at 0.25, right in the valley. struct_keep = 35% of frames. This is colour-blind, so
  it should hold across tournaments where the v1 colour cue would not.
- **Motion gate** (phaseCorrelate, self-calibrated threshold 1.07) trimmed struct_keep from
  35% to 33%, removing the high-motion (pan/zoom/cut) frames as intended.
- **Phase 1 result:** 167 segments, 36:50 of rallies (39%), vs v1 colour's 171 segs / 39:42 /
  43%. Comparable on this match (colour happened to work here); the structural win is
  colour-invariance + replay-motion rejection across other feeds.
- **Audio cue (Phase 2) does NOT yet discriminate.** Percussive spectral-flux onset density
  measured median **0.80/s on live frames AND 0.80/s on non-live frames** - zero separation.
  The detector fires on commentary plosives and crowd, not just the ball "pock", so the
  live-play rescue over-fired and ballooned retention to 75%. Audio is therefore **off by
  default**, behind `--audio` (experimental).

**Next on audio:** band-limit the onset detector to the high-frequency band where the padel
pock dominates and speech does not; and score impact-train **periodicity/regularity** (short
autocorrelation of the onset envelope) rather than raw density, since a rally is a regular
~0.5-2 Hz train while commentary/crowd onsets are irregular. Re-verify with the same debug-CSV
split (live vs non-live density must actually separate) before wiring the overrides back on.

Usage (Phase 1, the reliable path):
```
py -3.12 -u scripts\extract_rallies_v2.py "match.mp4" [--dry-run]
```
`--audio` re-enables the experimental audio overrides; `--no-motion` / `--no-veto` /
`--no-rescue` isolate cues for A/B testing. A `<out>.debug.csv` of every per-frame cue track is
always written for inspection.
