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
from pathlib import Path

import cv2
import numpy as np

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


def _detect_faces(img: np.ndarray, gray: np.ndarray) -> list[list[int]]:
    if _yunet is not None:
        h, w = img.shape[:2]
        _yunet.setInputSize((w, h))
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


def _pick_face(faces: list, prev_x: float | None, w: int):
    """Prefer the face closest to the previous focus (temporal stability), with a
    bias toward larger faces; fall back to the largest when there's no history."""
    faces = [f for f in faces if f[2] >= _MIN_FACE_W_FRAC * w]
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


def compute_segment_focus(source: dict, start_sec: float, end_sec: float, samples: int | None = None) -> list[dict]:
    local = str(source.get("local_path") or "")
    if not local or not Path(local).exists():
        raise ValueError("source has no playable file")
    dur = float(end_sec) - float(start_sec)
    if dur <= 0:
        raise ValueError("segment has no duration")
    # ~one sample per second for responsive, fine-grained tracking.
    n = samples if samples else max(12, min(60, round(dur)))
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
            is_cut = prev_hist is not None and _hist_similarity(prev_hist, hist) < _CUT_SIMILARITY
            if is_cut:
                # New shot — nothing from the old one should influence it.
                prev_x = None
                prev_gray = None

            face = _pick_face(_detect_faces(img, gray), prev_x, w)
            x: float | None = None
            y = 0.5
            if face is not None:
                fx, fy, fw, fh = face
                x = (fx + fw / 2) / w
                y = (fy + fh / 2) / h
            elif prev_gray is not None:
                diff = cv2.absdiff(gray, prev_gray)
                _, mask = cv2.threshold(diff, 22, 255, cv2.THRESH_BINARY)
                m = cv2.moments(mask)
                if m["m00"] > 255 * (w * h) * 0.01:  # only a clear, large motion blob
                    x = (m["m10"] / m["m00"]) / w
                    y = (m["m01"] / m["m00"]) / h
            # No reliable detection → hold the last known position (or centre at the
            # very start / right after a cut) so we never extrapolate a wrong
            # neighbour onto empty frames.
            if x is None:
                x = prev_x if prev_x is not None else 0.5
            x = min(1.0, max(0.0, x))
            point = {"t": t, "x": round(x, 4), "y": round(y, 4)}
            if is_cut:
                point["cut"] = True
            points.append(point)
            prev_x = x
            prev_gray = gray
            prev_hist = hist

    # Kill isolated spikes before the render-side smoothing sees them: one bad frame
    # otherwise becomes a visible camera lurch. Smooth each shot separately — a
    # median across a cut would blend two unrelated compositions.
    for shot in _split_on_cuts(points):
        if len(shot) < 3:
            continue
        for axis in ("x", "y"):
            smoothed = _median_filter([p[axis] for p in shot])
            for p, v in zip(shot, smoothed):
                p[axis] = round(v, 4)

    return points


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
