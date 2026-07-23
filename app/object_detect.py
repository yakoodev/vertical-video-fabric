"""Person/object detection for autofocus (YOLOX-S, ONNX via cv2.dnn).

A real detector answers "where is the subject" far more reliably than face+motion:
it boxes a person even from behind / partial, and the graphic/object being shown.
Model: OpenCV Zoo YOLOX (Apache-2.0), same cv2.dnn/ONNX path as YuNet, no extra
dependency. Loaded lazily and cached; if the file is missing the caller falls back
to the heuristic CV signals.
"""

from __future__ import annotations

import os

import cv2
import numpy as np

_INPUT = 640
_STRIDES = (8, 16, 32)
_MODEL_PATH = os.getenv("YOLOX_MODEL", "/opt/models/yolox_nano.onnx")
_SCORE = 0.35
_NMS = 0.45
# For FRAMING the class label barely matters (YOLOX happily calls a big central
# object an "airplane") — what matters is a big, confident box. So accept any
# class above threshold and just give people/animals a bias so a person wins ties.
_CLASS_BONUS = {0: 2.5, 15: 1.6, 16: 1.6, 17: 1.6, 18: 1.6, 19: 1.6}

_net = None
_grids: np.ndarray | None = None
_expanded: np.ndarray | None = None
_loaded = False


def _load() -> bool:
    global _net, _grids, _expanded, _loaded
    if _loaded:
        return _net is not None
    _loaded = True
    if not os.path.exists(_MODEL_PATH):
        return False
    try:
        _net = cv2.dnn.readNetFromONNX(_MODEL_PATH)
        grids, expanded = [], []
        for stride in _STRIDES:
            g = _INPUT // stride
            xv, yv = np.meshgrid(np.arange(g), np.arange(g))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            expanded.append(np.full((g * g, 1), stride))
        _grids = np.concatenate(grids, 0).astype(np.float32)
        _expanded = np.concatenate(expanded, 0).astype(np.float32)
    except Exception:  # noqa: BLE001 - any load failure → heuristic fallback
        _net = None
    return _net is not None


def subject_center(img: np.ndarray, prev_x: float | None = None) -> tuple[float, float] | None:
    """Normalized (x, y) centre of the main subject in a BGR frame, or None.

    Prefers people/animals, biases toward larger and more central subjects, and —
    when we were already tracking something — toward the one nearest ``prev_x``.
    """
    if not _load():
        return None
    h, w = img.shape[:2]
    scale = min(_INPUT / w, _INPUT / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    canvas = np.full((_INPUT, _INPUT, 3), 114, np.uint8)  # letterbox pad
    canvas[:nh, :nw] = cv2.resize(img, (nw, nh))
    blob = cv2.dnn.blobFromImage(canvas, 1.0, (_INPUT, _INPUT), swapRB=False)
    _net.setInput(blob)
    pred = _net.forward()[0]  # (8400, 85), obj/cls already sigmoid

    # Decode grid-relative boxes to letterbox pixels.
    xy = (pred[:, 0:2] + _grids) * _expanded
    wh = np.exp(pred[:, 2:4]) * _expanded
    obj = pred[:, 4]
    cls = pred[:, 5:]
    cls_id = cls.argmax(1)
    scores = obj * cls[np.arange(cls.shape[0]), cls_id]

    keep = scores >= _SCORE
    if not keep.any():
        return None
    xy, wh, scores, cls_id = xy[keep], wh[keep], scores[keep], cls_id[keep]
    boxes = np.concatenate([xy - wh / 2, wh], 1)  # x,y,w,h (letterbox px)
    idxs = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), _SCORE, _NMS)
    if idxs is None or len(idxs) == 0:
        return None
    idxs = np.array(idxs).ravel()

    best = None
    best_rank = -1.0
    for i in idxs:
        weight = _CLASS_BONUS.get(int(cls_id[i]), 1.0)
        cx = float(xy[i][0]) / scale / w  # back to original, normalized
        cy = float(xy[i][1]) / scale / h
        area = float(wh[i][0]) * float(wh[i][1])
        rank = weight * float(scores[i]) * (area ** 0.5)  # big + confident + subject-class
        if prev_x is not None:
            rank /= 1.0 + 3.0 * abs(cx - prev_x)  # stay on the one we were tracking
        if rank > best_rank:
            best_rank = rank
            best = (min(1.0, max(0.0, cx)), min(1.0, max(0.0, cy)))
    return best
