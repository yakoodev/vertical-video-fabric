"""Рендер клипов: ``ClipRenderService`` и всё, что о нём знают снаружи.

Код лежит по этапам, ``ClipRenderService`` собирает методы наследованием: монтаж кусков ``app/render_montage.py``,
сборка клипа по плану ``app/render_plan.py``, проходы поверх основы ``app/render_finish.py``, звук
``app/render_sound.py``, субтитры ``app/render_subs.py``, пресеты ``app/render_presets.py``; без состояния — музыка
``app/render_music.py``, аргументы ffmpeg ``app/render_args.py``, граф фильтров ``app/render_filters.py``, громкость
``app/render_loudness.py``, запуск и мелочи ``app/render_exec.py``. Части не импортируют этот модуль, а он их.
Все прежние имена доступны отсюда, подмены в тестах ставятся сюда же (``_RenderModule`` внизу файла).
Здесь остались только служебные методы и ``_after_render``: его логгер назван по этому модулю."""

from __future__ import annotations

import contextvars
import json
import logging
import math
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.focus_presets import get_focus_preset
from app.heavy_queue import heavy, heavy_slot
from app.ingest import file_hash_and_size, probe_media
from app.settings import settings
from app.store import AppStore
from app.subtitles.ass import write_ass_subtitles
from app.subtitles.contracts import SubtitleResult
from app.subtitles.registry import get_subtitle_provider, subtitle_model_for_profile
from app.subtitles.timing import normalize_subtitle_timeline, shift_subtitle_timeline
from app.clip_settings import normalize_cover
from app.cover import build_burn_args, build_frame_args, build_image_args, cover_output_time
from app.render_qc import run_qc
from app.stream_layout import (
    even_px,
    ff_color,
    gameplay_height,
    layout_active,
    normalize_layout,
    scaled_px,
    split_geometry,
)
from app.transitions import build_join_args, is_plain_concat, normalize_transition, part_overlaps, plan_joins
from app.backgrounds import background_zones, fill_color, needs_zones
from app.video_crop import build_reframe_x_expr
from app.piece_speed import seg_length

# Рендер разложен по этапам (app/render_*.py); все имена остаются доступны и отсюда.
from app.render_exec import (  # noqa: F401
    _clamp, _concat_file_line, _ff_number, _filter_filename, _fps, _has_audio_stream, _json_dict, _run_compositor,
    _run_ffmpeg, _safe_error, _seconds, _stream_index, _video_duration, _volume, RENDER_RUNNER,
)
from app.render_loudness import (  # noqa: F401
    _compressor, _measure_loudness, audible, COMPRESSOR_ABOVE_TARGET_DB, correct_loudness_plan, END_AUDIO_FADE_SEC,
    end_fade_filter, FINAL_AUDIO_BITRATE, finished_loudness_lift, fit_loudness, LIMITER_IDLE_DB, LIMITER_LOSS_LU,
    LIMITER_ROOM_DB, linear_gain_db, loudness_filters, LOUDNESS_LIFT_MARGIN_DB, LOUDNESS_LIMITER, LOUDNESS_PEAK_DB,
    loudness_plan, LOUDNESS_TOLERANCE_LU, loudnorm_needed, LOUDNORM_SILENCE_LUFS, MAKEUP_EFFICIENCY, MAX_EXTRA_BOOST_DB,
    MAX_LOUDNESS_BOOST_DB, MAX_MAKEUP_DB, MUSIC_TAIL_MIN_SEC, music_tail_sec, OUTPUT_SAMPLE_RATE, SILENT_LUFS,
    stream_durations, TRUE_PEAK_FLOOR_DB, TRUE_PEAK_LIMIT_DB, true_peak_limiter, TRUE_PEAK_OVERSAMPLE,
    true_peak_retry_ceiling, TRUE_PEAK_RETRY_MARGIN_DB, TRUE_PEAK_TOLERANCE_DB,
)
from app.render_filters import (  # noqa: F401
    _audio_filter, _audio_map, _banner_overlay, _banner_position_expr, _color_style_filters, _crop_prefix,
    _filter_complex, _fit_box, _freeze_source, _layout_filter, _piece_fit_filter, _render_filter, _retimed,
    _source_edit, _video_style_filter, BANNER_MAX_WIDTH_SHARE,
)
from app.render_args import (  # noqa: F401
    _banner_input_args, build_audio_handle_args, build_ffmpeg_concat_args, build_ffmpeg_render_args, build_unband_args,
    build_voice_fx_args,
)
from app.render_music import (  # noqa: F401
    _music_chain, _music_filter, _music_hold, _music_mix_fields, _music_sidechain, _resolve_music_settings,
    build_ffmpeg_music_args, MAX_MUSIC_START_SEC, MUSIC_NOMINAL_VOLUME, music_stem_filter, MUSIC_UNDER_SPEECH_DB,
    music_volume_for, music_wants_sidechain,
)
from app.render_presets import (  # noqa: F401
    _base_crop, _is_canvas, _keyed_render, _needs_sar_fix, _piece_retime, _preset_for_segment,
    _preset_with_music_automation, _preset_with_output, _preset_with_source_audio, _segment_reframe_x, _source_gaps,
    _with_background_zones, banded_pinned, effective_preset, KEY_EDGE_INSET, layout_camera_regions, pin_band,
    PresetsRender, REFRAME_MAX_SMOOTH_SEC,
)
from app.render_subs import (  # noqa: F401
    _apply_subtitle_timing_offset, _source_looks_like_anime, _subtitle_prompt_for_clip, _subtitle_timing_offset,
    build_ffmpeg_burn_subtitle_args, build_ffmpeg_extract_audio_args, SubtitlesRender,
)
from app.render_sound import SoundRender  # noqa: F401
from app.render_finish import FinishRender  # noqa: F401
from app.render_plan import PlanRender  # noqa: F401
from app.render_montage import MontageRender  # noqa: F401


class ClipRenderService(PresetsRender, SubtitlesRender, SoundRender, FinishRender, PlanRender, MontageRender):
    def __init__(self, store: AppStore) -> None:
        self.store = store
        # Per-source render-cancel marks (monotonic timestamps). A batch loop
        # records when it started and stops before its next clip once a cancel
        # was requested after that — so a later batch on the same source is
        # unaffected. In-memory is enough: all renders run in this process.
        self._cancel_marks: dict[int, float] = {}
        self._cancel_lock = threading.Lock()

    @staticmethod
    def batch_clock() -> float:
        """Clock for batch start / cancel marks. perf_counter, not monotonic:
        on Windows monotonic ticks every ~16 ms, so a cancel and a batch start
        in the same tick compared equal and the cancel was silently missed."""
        return time.perf_counter()

    def request_render_cancel(self, source_id: int) -> None:
        with self._cancel_lock:
            self._cancel_marks[int(source_id)] = self.batch_clock()

    def render_batch_cancelled(self, source_id: int, started_at: float) -> bool:
        with self._cancel_lock:
            return self._cancel_marks.get(int(source_id), -1.0) > started_at

    def _after_render(self, clip: dict) -> dict:
        """После рендера (уже вне очереди тяжёлых): заголовок, описание и хэштеги клипа, если их
        ещё нет (app/ai/publish_meta.auto_meta_after_render). Никогда не ломает рендер."""
        if not isinstance(clip, dict) or clip.get("status") != "succeeded":
            return clip
        try:
            from app.ai.publish_meta import auto_meta_after_render

            result = auto_meta_after_render(self.store, clip)
            if result.get("status") in ("generated", "inherited"):
                return self.store.get_clip(int(clip["id"]))
        except Exception:  # noqa: BLE001 - метаданные — не повод сорвать рендер
            logging.getLogger(__name__).warning("auto publish meta failed for clip %s", clip.get("id"), exc_info=True)
        return clip


# Части рендера: подмена имени на этом модуле доходит и до них.
_PARTS = tuple(sys.modules[f"app.{name}"] for name in (
    "render_exec", "render_loudness", "render_filters", "render_args", "render_music", "render_presets", "render_subs",
    "render_sound", "render_finish", "render_plan", "render_montage",
))


class _RenderModule(types.ModuleType):
    """Подмена имени на ``app.render`` видна коду всех частей.

    До разбора рендер был одним модулем, и подмены (``monkeypatch.setattr("app.render._run_ffmpeg", ...)``, а ещё
    ``probe_media``, ``run_qc``, ``uuid4``, ``fit_loudness``, ...) ставятся сюда. Функции частей читают имена из
    глобальных своего модуля, поэтому значение дублируется в каждую часть, где имя есть. Снятие подмены идёт тем же
    путём."""

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        for part in _PARTS:
            if name in vars(part):
                setattr(part, name, value)


sys.modules[__name__].__class__ = _RenderModule
