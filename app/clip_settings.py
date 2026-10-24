"""Per-clip render settings.

The workflow is: analyse → pick favourites → set up EACH clip on its own →
render the selected ones → publish the selected ones. Until now the editor's
render panel was one set of knobs for the whole batch: tuning the subtitles for
one clip silently re-tuned every other clip rendered in the same run.

Now every clip plan carries its own settings (``clip_plans.render_settings_json``),
saved from the editor as you work. The batch render reads each plan's own
settings; a plan never opened in the editor falls back to the request's values.

The stored shape mirrors the editor panel one to one, so the editor can load a
plan's settings back verbatim.
"""

from __future__ import annotations

from app.aspect import settings_aspect
from app.audio_mute import normalize_gain, normalize_mute
from app.chroma_key import normalize_backdrop
from app.cuts import normalize_cuts
from app.montage_assets import normalize_inserts
from app.motion import normalize_camera, normalize_layers
from app.stream_layout import normalize_layout
from app.transitions import normalize_transition

SUBTITLE_FRAME_HEIGHT = 1920

COVER_MODES = ("none", "frame", "image")
# Uploaded covers are stored under a generated name — only such names are accepted,
# so a settings value can never point the renderer at an arbitrary file.
COVER_NAME_CHARS = set("0123456789abcdef")


def normalize_cover(raw: object) -> dict:
    """Cover of the clip: none, a frame of the clip itself, or an uploaded image.

    ``burn`` lays the cover over the first ``burn_sec`` of the video: platforms that
    take the preview from the first frame then show it — the one trick that works
    on every platform, including those with no cover upload in their API.
    """
    src = raw if isinstance(raw, dict) else {}
    mode = src.get("mode") if src.get("mode") in COVER_MODES else "none"
    try:
        piece = max(0, int(src.get("piece", 0)))
    except (TypeError, ValueError):
        piece = 0
    try:
        offset = max(0.0, float(src.get("offset", 0.0)))
    except (TypeError, ValueError):
        offset = 0.0
    image = src.get("image")
    if not (
        isinstance(image, str)
        and image.endswith(".jpg")
        and len(image) == 36
        and set(image[:-4]) <= COVER_NAME_CHARS
    ):
        image = ""
    try:
        burn_sec = float(src.get("burn_sec", 0.1))
    except (TypeError, ValueError):
        burn_sec = 0.1
    out = {
        "mode": mode,
        "piece": piece,
        "offset": round(offset, 3),
        "image": image,
        "burn": _bool(src.get("burn", True)),
        "burn_sec": min(0.5, max(0.04, burn_sec)),
    }
    if out["mode"] == "image" and not out["image"]:
        out["mode"] = "none"
    return out


def _bool(value: object) -> bool:
    return value is True or value == 1 or value == "1" or value == "true"


def _opt_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _pct(value: object, default: float, lo: float = 0.0, hi: float = 100.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, number))


# Стоп-кадр в конце ролика на клип (end_hold_sec): 0 — мем без «замирания» в конце.
END_HOLD_MAX_SEC = 1.5


def normalize_end_hold(value: object) -> float | None:
    """Стоп-кадр клипа, с: None (нет/мусор — как у сервиса, RENDER_END_HOLD_SEC) или 0..1.5."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return round(min(END_HOLD_MAX_SEC, max(0.0, number)), 2)


def normalize_render_settings(raw: object) -> dict:
    """Coerce whatever was stored/sent into the full settings shape."""
    src = raw if isinstance(raw, dict) else {}
    engine = src.get("sub_engine")
    return {
        "preset_id": _opt_int(src.get("preset_id")),
        "subs_on": _bool(src.get("subs_on")),
        "sub_id": _opt_int(src.get("sub_id")),
        "sub_engine": engine if isinstance(engine, str) and len(engine) <= 40 else "",
        # До 85 %: в раскладке meme субтитры идут по видео сверху (агент упирался в 40 %).
        "sub_pos_pct": _pct(src.get("sub_pos_pct"), 12.0, 2.0, 85.0),
        "banner_on": _bool(src.get("banner_on")),
        "banner_id": _opt_int(src.get("banner_id")),
        "banner_height_pct": _pct(src.get("banner_height_pct"), 14.0, 4.0, 40.0),
        "banner_pos_pct": _pct(src.get("banner_pos_pct"), 4.0, 0.0, 80.0),
        "music_on": _bool(src.get("music_on")),
        "track_id": _opt_int(src.get("track_id")),
        # С какой секунды трека играет музыка на 0 с клипа (рендер читает из настроек плана).
        "music_start_sec": round(_pct(src.get("music_start_sec"), 0.0, 0.0, 3600.0), 2),
        "mirror": _bool(src.get("mirror")),
        "transition": normalize_transition(src.get("transition")),
        "cover": normalize_cover(src.get("cover")),
        "inserts": normalize_inserts(src.get("inserts")),
        # Моушн-слои и наезды камеры (docs/motion/spec.md). Рендер читает их
        # прямо из настроек плана, как и вставки, — в kwargs рендера их нет.
        "layers": normalize_layers(src.get("layers")),
        "camera": normalize_camera(src.get("camera")),
        # Раскладка стрима: вебка сверху + геймплей и т.п. (app/stream_layout.py).
        # Как и слои, рендер читает её прямо из настроек плана; single — как раньше.
        "layout": normalize_layout(src.get("layout")),
        # Джамп-каты: вырезанные паузы и слова-паразиты, {start, end} во времени
        # клипа до вырезов (app/cuts.py). Пусто — рендер ровно прежний.
        "cuts": normalize_cuts(src.get("cuts")),
        # Фон за вырезанным персонажем, когда у источника есть хромакей (app/chroma_key.py).
        "backdrop": normalize_backdrop(src.get("backdrop")),
        # Тишина вместо звука исходника на отрезках, картинка остаётся (app/audio_mute.py).
        "mute": normalize_mute(src.get("mute")),
        # Громкость звука исходника на отрезках {start, end, gain_db} — поднять тихое (app/audio_mute.py).
        "gain": normalize_gain(src.get("gain")),
        # Формат кадра: 9:16 (как всегда) / 1:1 / 4:5 — пока только у холста (app/aspect.py).
        # Рендер читает его прямо из настроек плана, как раскладку.
        "aspect": settings_aspect(src.get("aspect")),
        # Стоп-кадр в конце ролика, с (0..1.5); None — как у сервиса (RENDER_END_HOLD_SEC).
        # Рендер читает прямо из настроек плана (ClipRenderService._end_hold_sec).
        "end_hold_sec": normalize_end_hold(src.get("end_hold_sec")),
    }


def settings_to_render_kwargs(settings: dict, default_subtitle_profile_id: int | None = None) -> dict:
    """Map stored settings onto ``ClipRenderService.render_clip_plan`` kwargs.

    Same rules as the editor panel always used when it sent a render request:
    a switched-off block sends nothing, so the preset's own value applies.
    """
    s = normalize_render_settings(settings)
    subs = s["subs_on"]
    # «Накладывать субтитры» with the style left on «по умолчанию» must still burn
    # subtitles: without a profile id the render skips the subtitle pass entirely.
    sub_id = s["sub_id"] or (default_subtitle_profile_id if subs else None)
    banner = s["banner_on"]
    return {
        "ffmpeg_preset_id": s["preset_id"],
        "subtitle_profile_id": sub_id if subs else None,
        "subtitle_provider": (s["sub_engine"] or None) if subs else None,
        "subtitle_margin_v": round(s["sub_pos_pct"] / 100 * SUBTITLE_FRAME_HEIGHT) if subs else None,
        "banner_id": s["banner_id"] if banner else None,
        "banner_height_frac": s["banner_height_pct"] / 100 if banner else None,
        "banner_y_frac": s["banner_pos_pct"] / 100 if banner else None,
        "music_track_id": s["track_id"] if s["music_on"] else None,
        "mirror": s["mirror"],
        "transition": s["transition"],
        "cover": s["cover"],
    }
