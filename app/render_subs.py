"""Рендер: субтитры клипа. Распознавание, стиль, вжигание, сверка со звуком после рендера
(``app/render.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from app.ingest import probe_media
from app.render_exec import _filter_filename, _json_dict, _run_ffmpeg, _safe_error, _seconds
from app.settings import settings
from app.store import AppStore
from app.subtitles.ass import write_ass_subtitles
from app.subtitles.contracts import SubtitleResult
from app.subtitles.registry import get_subtitle_provider, subtitle_model_for_profile
from app.subtitles.timing import normalize_subtitle_timeline, shift_subtitle_timeline


def build_ffmpeg_extract_audio_args(
    input_path: Path,
    output_path: Path,
    start_sec: float | None = None,
    duration_sec: float | None = None,
    tracks: Any = None,
) -> list[str]:
    """``tracks`` — звуковые дорожки исходника (app/source_audio.py, номера или
    ``[{track, gain_db}]``); None — первая."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    from app.source_audio import is_default_mix

    if tracks and not is_default_mix(tracks):
        from app.source_audio import extract_args

        return extract_args(input_path, output_path, list(tracks), start=start_sec, duration=duration_sec)
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if start_sec is not None:
        args.extend(["-ss", _seconds(float(start_sec))])
    args.extend(
        [
            "-i",
            str(input_path),
        ]
    )
    if duration_sec is not None:
        args.extend(["-t", _seconds(float(duration_sec))])
    args.extend(
        [
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    return args


def build_ffmpeg_burn_subtitle_args(
    input_path: Path,
    output_path: Path,
    ass_filename: str,
    preset: dict,
) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        f"ass={_filter_filename(ass_filename)}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        str(preset.get("video_codec") or "libx264"),
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
    ]
    video_bitrate = str(preset.get("video_bitrate") or "").strip()
    if video_bitrate:
        args.extend(["-b:v", video_bitrate])
    extra = _json_dict(preset.get("extra_json"))
    crf = extra.get("crf")
    if crf is not None:
        args.extend(["-crf", str(crf)])
    args.append(str(output_path))
    return args


def _apply_subtitle_timing_offset(result: SubtitleResult, profile: dict, input_duration: float) -> SubtitleResult:
    offset = _subtitle_timing_offset(profile)
    if abs(offset) < 0.001:
        return result
    return shift_subtitle_timeline(result, offset, input_duration)


def _subtitle_timing_offset(profile: dict) -> float:
    try:
        offset = float(profile.get("timing_offset_sec") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(-2.0, min(2.0, offset))


def _subtitle_prompt_for_clip(store: AppStore, clip_id: int) -> str:
    saved_prompt = str(store.get_app_setting_value("subtitle_prompt", "", include_secret=False) or "").strip()
    if saved_prompt:
        return saved_prompt
    clip = store.get_clip(clip_id)
    try:
        source = store.get_source(clip["source_id"])
    except KeyError:
        source = {}
    label = "Anime subtitles" if _source_looks_like_anime(source) else ""
    if label:
        for preset in store.list_prompt_presets("subtitle"):
            if str(preset.get("label") or "").strip().lower() == label.lower():
                return str(preset.get("prompt") or "").strip()
    preset = store.get_default_prompt_preset("subtitle")
    return str((preset or {}).get("prompt") or "").strip()


def _source_looks_like_anime(source: dict) -> bool:
    source_type = str(source.get("source_type") or "").lower()
    original_url = str(source.get("original_url") or "").lower()
    original_filename = str(source.get("original_filename") or "").lower()
    haystack = f"{source_type} {original_url} {original_filename}"
    return any(marker in haystack for marker in ("smotvibe", "yummyani", "anime"))


class SubtitlesRender:
    def _qc_subtitle_sync(self, clip: dict, path: Path, qc: dict) -> dict:
        """Совпадают ли сохранённые субтитры клипа с речью в готовом файле.

        Ловит класс ошибок «обрезали кусок, а слова не сдвинули»: субтитры
        уезжают на секунды, и на глаз это видно только на просмотре.
        """
        from app.render_qc import estimate_subtitle_offset, subtitle_sync_issue

        try:
            plan_id = clip.get("clip_plan_id")
            if not plan_id:
                return qc
            plan = self.store.get_clip_plan(int(plan_id))
            cut_plan = self._cut_plan(plan, clip.get("ffmpeg_preset_id"))
            voice = self._qc_sync_on_voice(plan, cut_plan)
            if voice is not None:
                # Своя дорожка речи (микрофон): сверяем по голосу на ней, а не по громкости
                # готового звука, где выстрелы игры похожи на речь.
                offset, basis = voice, "дорожке речи"
                qc = {**qc, "subtitle_offset_sec": offset, "subtitle_sync_basis": "speech_track"}
                issue = subtitle_sync_issue(offset, basis)
                if issue:
                    qc["issues"] = [*qc.get("issues", []), issue]
                    qc["ok"] = False
                return qc
            # Ровно те тайминги слов, что рендер прожёг/отдал слою «субтитры»:
            # вырезы, потом настоящие overlap склеек после вырезов.
            if cut_plan is not None:
                from app.cuts import cut_caption_words

                final = cut_caption_words(self.store, plan, cut_plan)
            else:
                from app.motion_render import plan_caption_words

                final = plan_caption_words(self.store, plan)
            words = [{"start": w["t0"], "end": w["t1"]} for w in final]
            offset = estimate_subtitle_offset(path, words)
            if offset is None:
                return qc
            qc = {**qc, "subtitle_offset_sec": offset}
            issue = subtitle_sync_issue(offset)
            if issue:
                qc["issues"] = [*qc.get("issues", []), issue]
                qc["ok"] = False
        except Exception:  # noqa: BLE001 - QC никогда не ломает рендер
            return qc
        return qc

    def _qc_sync_on_voice(self, plan: dict, cut_plan=None) -> float | None:
        """Сдвиг субтитров по дорожке речи источника (``speech_tracks``), или None — дорожки
        речи отдельно нет / сверить нельзя. Звук — части клипа подряд (после вырезов, без
        наплывов), слова — в том же времени."""
        from app.clip_subtitles import build_clip_audio_args
        from app.cuts import remap_subtitles
        from app.render_qc import estimate_offset_on_voice
        from app.source_audio import separate_speech, speech_mix
        from app.speech_onsets import wav_pcm

        source = self.store.get_source(int(plan["source_id"]))
        if not separate_speech(source):
            return None
        payload = self.store.get_clip_plan_subtitles(int(plan["id"])) or {}
        if not payload.get("words"):
            return None
        if cut_plan is not None:
            payload = remap_subtitles(payload, cut_plan.mapper)
            parts = cut_plan.parts
        else:
            parts = plan.get("segments") or []
        pieces = [(float(p["start_sec"]), float(p["end_sec"])) for p in parts]
        source_path = Path(str(source.get("local_path") or ""))
        if not pieces or not source_path.exists():
            return None
        wav = settings.tmp_dir / f"qc-voice-{uuid4().hex}.wav"
        try:
            _run_ffmpeg(build_clip_audio_args(source_path, pieces, wav, speech_mix(source)), timeout=60 * 5)
            pcm = wav_pcm(wav)
        finally:
            wav.unlink(missing_ok=True)
        return estimate_offset_on_voice(pcm, 16000, payload.get("words") or [])

    def _plan_subtitles_for_clip(self, clip_id: int, fps: float | None = None):
        """Субтитры, сделанные в редакторе до рендера (если есть)."""
        from app.clip_subtitles import result_from_payload
        from app.cuts import plan_joins_setting, subtitles_to_final

        try:
            clip = self.store.get_clip(clip_id)
            plan_id = clip.get("clip_plan_id")
            if not plan_id:
                return None
            payload = self.store.get_clip_plan_subtitles(int(plan_id))
            if not payload or not (payload.get("lines") or payload.get("words")):
                return None
            plan = self.store.get_clip_plan(int(plan_id))
            transition = (plan.get("render_settings") or {}).get("transition")
            cut_plan = self._cut_plan(plan, clip.get("ffmpeg_preset_id"), fps)
            # Слова лежат в склейке кусков: сначала вырезы, потом сдвиг на
            # настоящие overlap склеек (как их спланирует рендер).
            final = subtitles_to_final(payload, plan.get("segments") or [], transition, cut_plan,
                                       plan_joins_setting(plan))
            freezes = self._freezes(plan)
            remap = None
            if freezes:
                # Вставленный стоп-кадр: речь после него звучит позже (app/freeze.py).
                from app.cuts import remap_subtitles
                from app.freeze import FreezeMap

                fmap = FreezeMap(freezes)
                final = remap_subtitles(final, fmap)
                remap = lambda p: remap_subtitles(p, fmap)  # noqa: E731
            result = result_from_payload(final)
            # Перевод с оригиналом (app/translation.py): строки оригинала — вторым стилем ASS.
            from app.translation import secondary_lines

            extra = secondary_lines(payload, plan.get("segments") or [], transition, cut_plan, remap,
                                    joins=plan_joins_setting(plan))
            if extra:
                from dataclasses import replace

                result = replace(result, response={**result.response, "secondary_lines": extra})
            return result
        except (KeyError, ValueError, TypeError):
            return None

    def _render_subtitles(
        self,
        clip_id: int,
        input_path: Path,
        output_path: Path,
        subtitle_profile_id: int,
        preset: dict,
    ) -> None:
        profile = self._subtitle_profile_for_render(clip_id, subtitle_profile_id, preset)
        ready = self._plan_subtitles_for_clip(clip_id, float(preset.get("fps") or 30))
        provider = get_subtitle_provider(profile["provider"])
        model = subtitle_model_for_profile(profile)
        track = self.store.create_subtitle_track(
            clip_id,
            provider=provider.provider,
            subtitle_profile_id=subtitle_profile_id,
            model=model,
            status="running",
        )
        self.store.update_clip(clip_id, subtitle_track_id=track["id"])
        return self._burn_subtitles(clip_id, input_path, output_path, profile, ready, provider, model, track, preset)

    def _subtitle_profile_for_render(self, clip_id: int, subtitle_profile_id: int, preset: dict) -> dict:
        """Стиль субтитров этого рендера: поправки запроса и отступ под формат кадра."""
        profile = self.store.get_subtitle_profile(subtitle_profile_id)
        profile["prompt"] = _subtitle_prompt_for_clip(self.store, clip_id)
        # A per-render nudge (set on the preset) overrides the profile's saved
        # timing offset, so a single drifting clip can be pulled into sync without
        # changing the shared subtitle style.
        offset_override = preset.get("subtitle_offset_override")
        if offset_override is not None:
            profile = {**profile, "timing_offset_sec": offset_override}
        # Per-render engine swap: pick whisper/gemini independently of the style.
        provider_override = preset.get("subtitle_provider_override")
        if provider_override:
            profile = {**profile, "provider": provider_override}
        # Per-render vertical position so the burned subtitles match the preview band.
        margin_override = preset.get("subtitle_margin_v_override")
        if margin_override is not None:
            profile = {**profile, "margin_v": margin_override}
        elif preset.get("format"):
            # Формат не 9:16: отступ стиля задан в px кадра 1920 — та же доля высоты кадра формата.
            height = int(preset.get("output_height") or 1920)
            profile = {**profile, "margin_v": round(int(profile.get("margin_v") or 160) * height / 1920)}
        return profile

    def _burn_subtitles(self, clip_id: int, input_path: Path, output_path: Path, profile: dict, ready: Any,
                        provider: Any, model: Any, track: dict, preset: dict) -> None:
        audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
        ass_path = settings.subtitle_dir / f"{uuid4().hex}.ass"
        try:
            input_duration = probe_media(input_path).duration_sec
            # Always transcribe the exact audio of the clip we are about to burn
            # onto, in a single pass. Because the timestamps come from the same
            # (already cut, possibly stitched) audio they are rendered over, the
            # karaoke highlight stays locked to the speech. Per-part chunked
            # transcription used to re-seek into the stitched file and could land a
            # couple of seconds off on real concatenated media, drifting the words.
            if ready is not None:
                # Прошли правку в редакторе — жжём ровно их, без новой транскрипции.
                result = ready
                result = normalize_subtitle_timeline(result, input_duration)
            else:
                _run_ffmpeg(build_ffmpeg_extract_audio_args(input_path, audio_path), timeout=60 * 10)
                result = provider.transcribe(audio_path, profile, model)
                audio_duration = probe_media(audio_path).duration_sec
                result = normalize_subtitle_timeline(result, min(audio_duration, input_duration))
            result = _apply_subtitle_timing_offset(result, profile, input_duration)
            ass_path = write_ass_subtitles(
                result,
                profile,
                ass_path,
                width=int(preset.get("output_width") or 1080),
                height=int(preset.get("output_height") or 1920),
                # Формат вывода (только не 9:16): кегль, поля и отступ под кадр (app/subtitles/ass.py).
                fmt=preset.get("format"),
            )
            _run_ffmpeg(
                build_ffmpeg_burn_subtitle_args(input_path, output_path, ass_path.name, preset),
                timeout=60 * 20,
                cwd=ass_path.parent,
            )
            self.store.update_subtitle_track(
                track["id"],
                status="succeeded",
                transcript=result.transcript_json(),
                usage=result.usage,
                ass_path=ass_path,
                model=str(result.usage.get("model") or model),
            )
        except Exception as exc:
            self.store.update_subtitle_track(
                track["id"],
                status="failed",
                error=_safe_error(exc),
                model=model,
            )
            raise
        finally:
            audio_path.unlink(missing_ok=True)
