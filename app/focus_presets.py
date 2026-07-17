"""Autofocus presets — tuning per content type.

One set of constants can't serve a talking-head interview and an anime fight: a
face detector trained on real faces barely fires on animation, and a slow, heavily
median-filtered track that looks calm on an interview lags badly on action.

Each preset tunes both halves of the pipeline:
- detection (`app/autofocus.py`): whether to trust faces, how small a face may be,
  how densely to sample, how hard to median-filter, how eager cut detection is;
- movement (`app/video_crop.py` / preview): SmoothDamp settle time, rubber band and
  deadzone.

``median_k`` is in SAMPLES, so its lag scales with ``samples_per_sec``: k=3 at
2 samples/sec delays a step by ~0.5s, k=5 at 1/sec by ~2s (which felt "slow").
"""

from __future__ import annotations

DEFAULT_FOCUS_PRESET = "balanced"

FOCUS_PRESETS: dict[str, dict] = {
    "balanced": {
        "label": "Сбалансированный",
        "hint": "Универсальный. Лица в приоритете, движение — запасной вариант.",
        "use_faces": True,
        "face_score": 0.7,
        "min_face_frac": 0.03,
        "samples_per_sec": 3.0,
        "median_k": 3,
        "cut_similarity": 0.5,
        "smooth_time": 0.85,
        "rubber": 2.5,
        "deadzone": 0.012,
    },
    "talking": {
        "label": "Разговорное (лица)",
        "hint": "Интервью, подкасты, влоги. Держит лицо, реагирует быстро.",
        "use_faces": True,
        "face_score": 0.6,  # ниже порог — ловит лица в профиль и на общем плане
        "min_face_frac": 0.02,
        "samples_per_sec": 4.0,
        "median_k": 3,
        "cut_similarity": 0.5,
        "smooth_time": 0.6,
        "rubber": 3.0,
        "deadzone": 0.01,
    },
    "animation": {
        "label": "Анимация / аниме",
        "hint": "Детектор реальных лиц на рисованных не работает — ведём по движению.",
        "use_faces": False,
        "face_score": 0.7,
        "min_face_frac": 0.03,
        "samples_per_sec": 3.0,
        "median_k": 3,
        "cut_similarity": 0.55,  # в анимации склейки частые и контрастные
        "smooth_time": 0.75,
        "rubber": 2.8,
        "deadzone": 0.015,
    },
    "action": {
        "label": "Экшн / спорт",
        "hint": "Быстрое движение. Кадр догоняет резко, лица не приоритет.",
        "use_faces": False,
        "face_score": 0.7,
        "min_face_frac": 0.04,
        "samples_per_sec": 4.0,
        "median_k": 3,
        "cut_similarity": 0.5,
        "smooth_time": 0.45,
        "rubber": 3.5,
        "deadzone": 0.02,
    },
    "static": {
        "label": "Статика / студия",
        "hint": "Почти неподвижная камера. Кадр стоит, двигается редко и мягко.",
        "use_faces": True,
        "face_score": 0.7,
        "min_face_frac": 0.03,
        "samples_per_sec": 2.0,
        "median_k": 5,
        "cut_similarity": 0.45,
        "smooth_time": 1.6,
        "rubber": 2.0,
        "deadzone": 0.035,
    },
}


def get_focus_preset(name: str | None) -> dict:
    """Preset params by name, falling back to the balanced default."""
    key = str(name or "").strip().lower()
    return FOCUS_PRESETS.get(key) or FOCUS_PRESETS[DEFAULT_FOCUS_PRESET]


def list_focus_presets() -> list[dict]:
    return [{"key": k, "label": v["label"], "hint": v["hint"]} for k, v in FOCUS_PRESETS.items()]
