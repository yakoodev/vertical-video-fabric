"""Deterministic point-of-interest detection (no LLM).

Samples frames across a segment with ffmpeg and finds where the subject is:
a detected face wins, otherwise the centre of motion (frame differencing). The
result is a focus track ``[{t,x,y}]`` (t from the segment start; x/y normalized in
the source frame) — the same shape the LLM produces, so it flows through the
existing reframe smoothing/render path.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from app.focus_presets import DEFAULT_FOCUS_STRATEGY, get_focus_preset

_SAMPLE_W = 320
_FRONTAL = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_PROFILE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")

# YuNet DNN face detector — far fewer false positives than Haar (Haar fires on
# bright/contrasty backgrounds like windows). Loaded if the model is present.
_YUNET_PATH = os.getenv("YUNET_MODEL", "/opt/models/yunet.onnx")
_yunet = None
if os.path.exists(_YUNET_PATH):
    try:
        _yunet = cv2.FaceDetectorYN.create(_YUNET_PATH, "", (_SAMPLE_W, _SAMPLE_W), 0.7, 0.3, 50)
    except Exception:  # noqa: BLE001 - any load failure → fall back to Haar
        _yunet = None


def _detect_faces(img: np.ndarray, gray: np.ndarray, face_score: float = 0.7) -> list[list[int]]:
    if _yunet is not None:
        h, w = img.shape[:2]
        _yunet.setInputSize((w, h))
        try:
            _yunet.setScoreThreshold(float(face_score))
        except Exception:  # noqa: BLE001 - older builds lack the setter
            pass
        ok, faces = _yunet.detect(img)
        out: list[list[int]] = []
        if ok and faces is not None:
            for f in faces:
                out.append([int(f[0]), int(f[1]), int(f[2]), int(f[3])])
        return out
    faces: list = []
    for cascade in (_FRONTAL, _PROFILE):
        det = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(22, 22))
        if len(det):
            faces.extend(det.tolist())
    flipped = cv2.flip(gray, 1)
    det = _PROFILE.detectMultiScale(flipped, scaleFactor=1.15, minNeighbors=6, minSize=(22, 22))
    w = gray.shape[1]
    for (x, y, fw, fh) in det.tolist() if len(det) else []:
        faces.append([w - x - fw, y, fw, fh])
    return faces


# Faces smaller than this fraction of the frame width are background extras /
# false positives, not the subject — following them yanks the crop around.
_MIN_FACE_W_FRAC = 0.05

# Histogram correlation below this = the picture changed wholesale → hard cut.
# Motion inside one shot keeps the histogram broadly similar, so this separates a
# real shot change from someone just walking across the frame.
_CUT_SIMILARITY = 0.5


def _frame_hist(gray: np.ndarray) -> np.ndarray:
    hist = cv2.calcHist([gray], [0], None, [64], [0, 256])
    cv2.normalize(hist, hist)
    return hist


def _hist_similarity(a: np.ndarray, b: np.ndarray) -> float:
    try:
        return float(cv2.compareHist(a, b, cv2.HISTCMP_CORREL))
    except cv2.error:  # pragma: no cover - defensive
        return 1.0


def _pick_face(faces: list, prev_x: float | None, w: int, min_face_frac: float = _MIN_FACE_W_FRAC):
    """Prefer the face closest to the previous focus (temporal stability), with a
    bias toward larger faces; fall back to the largest when there's no history."""
    faces = [f for f in faces if f[2] >= min_face_frac * w]
    if not faces:
        return None
    if prev_x is None:
        return max(faces, key=lambda f: f[2] * f[3])
    diag = float(w)

    def score(f):
        cx = (f[0] + f[2] / 2) / w
        size = (f[2] * f[3]) ** 0.5 / diag
        return abs(cx - prev_x) - 0.6 * size  # near previous + reasonably large

    return min(faces, key=score)


# Motion tuning. A blob must be a real object, not sensor noise; and if "everything"
# moves it's a camera pan (or a dissolve), where any centroid is meaningless.
_MIN_BLOB_FRAC = 0.004   # ≥0.4% of the frame to count as a subject
_GLOBAL_MOTION_FRAC = 0.5  # >50% moving = camera move, not a subject
_NEAR_PREV_WEIGHT = 3.0  # how strongly we stick to the previously tracked object

# Cap the VLM batch: one request per segment stays cheap, and the tail of very
# choppy segments keeps its CV framing.
_VLM_MAX_SHOTS = 12


def _motion_focus(
    gray: np.ndarray,
    prev_gray: np.ndarray,
    prev_x: float | None,
    w: int,
    h: int,
) -> tuple[float, float] | None:
    """Centre of the largest coherent MOVING OBJECT, or None when motion is
    unusable (noise, or the whole frame moving).

    Taking the centroid of *all* motion (the old behaviour) is the classic trap:
    with two people moving it lands in the empty space between them, and on a
    camera pan it drifts to the middle of the frame. Segmenting the motion mask
    into blobs and following the biggest one — preferring the blob we were already
    on — tracks the actual subject instead.
    """
    diff = cv2.absdiff(gray, prev_gray)
    _, mask = cv2.threshold(diff, 22, 255, cv2.THRESH_BINARY)
    # Open: drop single-pixel sensor/compression noise, keep real shapes.
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    frame_area = float(w * h)
    moving_frac = float(np.count_nonzero(mask)) / frame_area
    if moving_frac > _GLOBAL_MOTION_FRAC or moving_frac < _MIN_BLOB_FRAC:
        return None

    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    best: tuple[float, float] | None = None
    best_score = 0.0
    for i in range(1, count):  # 0 is the background label
        area = float(stats[i, cv2.CC_STAT_AREA])
        if area < _MIN_BLOB_FRAC * frame_area:
            continue
        cx = float(centroids[i][0]) / w
        cy = float(centroids[i][1]) / h
        # Big blobs win, but stay on the object we were already following.
        score = area if prev_x is None else area / (1.0 + _NEAR_PREV_WEIGHT * abs(cx - prev_x))
        if score > best_score:
            best_score = score
            best = (cx, cy)
    return best


def _column_profile(
    gray: np.ndarray,
    prev_gray: np.ndarray | None,
    edge_weight: float,
    motion_weight: float,
) -> np.ndarray:
    """Per-column "how interesting is this strip" profile.

    Two signals, because neither survives alone on B-roll (memes / movie cutouts
    over a flat background while someone narrates):
    - EDGES: real content is detailed — text, faces, drawings. Flat background,
      gradients and letterbox bars score ~0. This finds *where the picture is*
      even when nothing moves and no face is detectable.
    - MOTION: what changed since the previous sample.
    """
    prof = np.zeros(gray.shape[1], dtype=np.float32)
    if edge_weight > 0:
        edges = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        prof += edge_weight * _normalize(edges.sum(axis=0))
    if motion_weight > 0 and prev_gray is not None:
        diff = cv2.absdiff(gray, prev_gray).astype(np.float32)
        diff[diff < 22] = 0.0  # ignore sensor/compression noise
        prof += motion_weight * _normalize(diff.sum(axis=0))
    # Blur across ~8% of the width so single spiky columns don't win.
    k = max(3, (gray.shape[1] // 12) | 1)
    return cv2.GaussianBlur(prof.reshape(1, -1), (k, 1), 0).ravel()


def _normalize(arr: np.ndarray) -> np.ndarray:
    peak = float(arr.max())
    return arr / peak if peak > 1e-6 else np.zeros_like(arr, dtype=np.float32)


def _profile_focus(profile: np.ndarray, prev_x: float | None) -> float | None:
    """Centre of the strongest contiguous run in the profile.

    Same idea as the 2D blob pick, in 1D: a weighted mean over the whole profile
    would land between two separate things (the wall problem again), so threshold
    at half the peak, take the connected runs, and score them by mass — sticking to
    the run we were already on.
    """
    w = profile.size
    peak = float(profile.max())
    if peak <= 1e-6:
        return None
    mask = profile >= peak * 0.5
    best_score = 0.0
    best_x: float | None = None
    i = 0
    while i < w:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < w and mask[j]:
            j += 1
        seg = profile[i:j]
        mass = float(seg.sum())
        centre = (i + float((seg * np.arange(seg.size)).sum() / max(mass, 1e-6))) / w
        score = mass if prev_x is None else mass / (1.0 + _NEAR_PREV_WEIGHT * abs(centre - prev_x))
        if score > best_score:
            best_score = score
            best_x = centre
        i = j
    return best_x


def _median_filter(values: list[float], k: int = 5) -> list[float]:
    """Median-smooth a series to kill single-frame outliers (a stray detection or
    one bad motion blob) without lagging behind real movement the way a mean would."""
    if len(values) < 3:
        return values
    half = max(1, k // 2)
    out: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        window = sorted(values[lo:hi])
        out.append(window[len(window) // 2])
    return out


def compute_segment_focus(
    source: dict,
    start_sec: float,
    end_sec: float,
    samples: int | None = None,
    preset: str | None = None,
    strategy: str | None = None,
    vlm_resolver: Callable[[list[bytes]], list[float | None]] | None = None,
) -> list[dict]:
    """Focus track for a segment.

    ``strategy`` is the GLOBAL camera behaviour (center / shot / follow / auto);
    ``preset`` tunes detection for the content type. ``vlm_resolver`` (injected so
    this module stays pure CV) gets one frame per shot and returns the subject x.
    """
    cfg = get_focus_preset(preset if preset is not None else source.get("focus_preset"))
    strat = str(strategy if strategy is not None else source.get("focus_strategy") or "").strip().lower() or DEFAULT_FOCUS_STRATEGY
    local = str(source.get("local_path") or "")
    if not local or not Path(local).exists():
        raise ValueError("source has no playable file")
    dur = float(end_sec) - float(start_sec)
    if dur <= 0:
        raise ValueError("segment has no duration")
    # Sampling density comes from the preset: denser = faster reaction (and a
    # shorter median window in seconds), at the cost of more frames to decode.
    per_sec = float(cfg["samples_per_sec"])
    n = samples if samples else max(12, min(120, round(dur * per_sec)))
    fps = n / dur

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "f_%03d.png"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, start_sec):.3f}", "-t", f"{dur:.3f}", "-i", local,
                "-vf", f"fps={fps:.5f},scale={_SAMPLE_W}:-2", "-frames:v", str(n + 1), str(out),
            ],
            capture_output=True,
            timeout=180,
        )
        frames = sorted(Path(td).glob("f_*.png"))
        if not frames:
            raise ValueError("ffmpeg produced no frames")

        points: list[dict] = []
        point_frames: list[Path] = []  # frame behind each point, for the VLM pass
        prev_gray: np.ndarray | None = None
        prev_hist: np.ndarray | None = None
        prev_x: float | None = None
        for i, fp in enumerate(frames):
            img = cv2.imread(str(fp))
            if img is None:
                continue
            h, w = img.shape[:2]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            t = round(min(dur, i / fps), 2)

            # Hard cut detection: within one shot the histogram stays similar even
            # under heavy motion; a cut to another shot changes it wholesale.
            hist = _frame_hist(gray)
            is_cut = prev_hist is not None and _hist_similarity(prev_hist, hist) < float(cfg["cut_similarity"])
            if is_cut:
                # New shot — nothing from the old one should influence it.
                prev_x = None
                prev_gray = None

            face = (
                _pick_face(
                    _detect_faces(img, gray, float(cfg["face_score"])),
                    prev_x,
                    w,
                    float(cfg["min_face_frac"]),
                )
                if cfg["use_faces"]
                else None
            )
            x: float | None = None
            y = 0.5
            if face is not None:
                fx, fy, fw, fh = face
                x = (fx + fw / 2) / w
                y = (fy + fh / 2) / h
            elif cfg.get("use_saliency"):
                # B-roll / animation: follow where the actual picture content sits
                # (detail + change), not "the average of everything that moved".
                prof = _column_profile(
                    gray, prev_gray, float(cfg.get("edge_weight", 1.0)), float(cfg.get("motion_weight", 1.0))
                )
                px = _profile_focus(prof, prev_x)
                if px is not None:
                    x = px
            elif prev_gray is not None:
                moving = _motion_focus(gray, prev_gray, prev_x, w, h)
                if moving is not None:
                    x, y = moving
            # No reliable detection → hold the last known position (or centre at the
            # very start / right after a cut) so we never extrapolate a wrong
            # neighbour onto empty frames.
            if x is None:
                x = prev_x if prev_x is not None else 0.5
            # Centre bias: on chaotic content (memes/cutaways) the safest framing is
            # near the middle, so pull the result back instead of chasing every jump.
            bias = float(cfg.get("center_bias", 0.0))
            if bias > 0:
                x = x * (1.0 - bias) + 0.5 * bias
            x = min(1.0, max(0.0, x))
            point = {"t": t, "x": round(x, 4), "y": round(y, 4)}
            if is_cut:
                point["cut"] = True
            points.append(point)
            point_frames.append(fp)
            prev_x = x
            prev_gray = gray
            prev_hist = hist

        # Grab one representative frame per shot while the temp dir still exists;
        # the VLM pass itself runs after the CV post-processing below.
        vlm_batch: list[tuple[int, int, bytes]] = []
        if vlm_resolver and points:
            for lo, hi in _shot_ranges(points)[:_VLM_MAX_SHOTS]:
                try:
                    vlm_batch.append((lo, hi, point_frames[(lo + hi) // 2].read_bytes()))
                except OSError:
                    continue

    # Kill isolated spikes before the render-side smoothing sees them: one bad frame
    # otherwise becomes a visible camera lurch. Smooth each shot separately — a
    # median across a cut would blend two unrelated compositions.
    median_k = int(cfg["median_k"])
    if median_k > 1:
        for shot in _split_on_cuts(points):
            if len(shot) < 3:
                continue
            for axis in ("x", "y"):
                smoothed = _median_filter([p[axis] for p in shot], median_k)
                for p, v in zip(shot, smoothed):
                    p[axis] = round(v, 4)

    # Global strategy — the overall camera behaviour, chosen by the user:
    resolved = _resolve_strategy(strat, points)
    if resolved == "center":
        # No reframe: hand back an empty track so the render just centre-crops.
        # (VLM would be pointless here, so skip it.)
        return []
    if resolved == "shot":
        # One framing per shot: per-frame estimates on busy content are near-random
        # and any smoothing still swings edge to edge. A human editor picks one
        # framing per shot and cuts — hold the shot median, jump only at the cut.
        for shot in _split_on_cuts(points):
            for axis in ("x", "y"):
                values = sorted(p[axis] for p in shot)
                mid = round(values[len(values) // 2], 4)
                for p in shot:
                    p[axis] = mid
    # "follow" keeps the continuous (median-filtered) track as-is.

    # VLM has the final say on framing: it answers "where is the subject" per shot,
    # which is exactly what the CV heuristics can only approximate. Anything it
    # doesn't answer keeps the CV value.
    if vlm_batch:
        answers = vlm_resolver([img for (_lo, _hi, img) in vlm_batch]) or []
        for (lo, hi, _img), x in zip(vlm_batch, answers):
            if x is None:
                continue
            fixed = round(min(1.0, max(0.0, float(x))), 4)
            for p in points[lo:hi]:
                p["x"] = fixed

    return points


def _resolve_strategy(strategy: str, points: list[dict]) -> str:
    """Turn ``auto`` into a concrete strategy from the track's own dynamics."""
    if strategy != "auto" or not points:
        return strategy
    xs = [p["x"] for p in points]
    spread = max(xs) - min(xs)
    if spread < 0.08:
        # The subject barely moves horizontally — reframing would only add wobble.
        return "center"
    shots = _shot_ranges(points)
    # Jitter = mean frame-to-frame jump INSIDE shots. A smooth follow has a big
    # spread but tiny steps; jumpy B-roll has big steps. Cuts don't count.
    steps: list[float] = []
    for lo, hi in shots:
        seg = xs[lo:hi]
        steps.extend(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    jitter = sum(steps) / len(steps) if steps else 0.0
    # Many cuts OR jumpy within shots -> lock one framing per shot; else glide.
    if len(shots) >= 3 or jitter > 0.12:
        return "shot"
    return "follow"


def _split_on_cuts(points: list[dict]) -> list[list[dict]]:
    """Group points into shots, starting a new one at every hard cut."""
    shots: list[list[dict]] = []
    current: list[dict] = []
    for p in points:
        if p.get("cut") and current:
            shots.append(current)
            current = []
        current.append(p)
    if current:
        shots.append(current)
    return shots


def _shot_ranges(points: list[dict]) -> list[tuple[int, int]]:
    """Shot boundaries as [start, end) index ranges into ``points``."""
    ranges: list[tuple[int, int]] = []
    start = 0
    for i, p in enumerate(points):
        if p.get("cut") and i > start:
            ranges.append((start, i))
            start = i
    if start < len(points):
        ranges.append((start, len(points)))
    return ranges
