"""Рендер: звуковые проходы клипа. Заглушки, ручки J/L, слои звука, громкость, музыка
(``app/render.py``)."""

from __future__ import annotations

from pathlib import Path

from app.ingest import probe_media
from app.render_args import build_audio_handle_args, build_voice_fx_args
from app.render_exec import _has_audio_stream, _run_ffmpeg
from app.render_loudness import (
    _measure_loudness, audible, end_fade_filter, FINAL_AUDIO_BITRATE, finished_loudness_lift, fit_loudness,
    loudness_filters, LOUDNESS_PEAK_DB, loudnorm_needed, music_tail_sec, OUTPUT_SAMPLE_RATE, TRUE_PEAK_LIMIT_DB,
    true_peak_retry_ceiling,
)
from app.render_music import _resolve_music_settings, build_ffmpeg_music_args, music_volume_for
from app.render_presets import _source_gaps
from app.settings import settings


class SoundRender:
    def _apply_mute(self, clip: dict, plan: dict, cut_plan=None, audio_plan: dict | None = None) -> dict:
        """Тишина и громкость звука исходника на отрезках (``render_settings.mute`` и ``gain``,
        app/audio_mute.py) — одним проходом.

        До вставок и звуков слоёв: они ложатся поверх и играют. Сбой не теряет клип."""
        from app.audio_mute import build_source_volume_args, final_pass_mutes, normalize_gain, span_floor

        settings_ = plan.get("render_settings") or {}
        # Отрезки одной дорожки (track) и мат со стилем-эффектом (писк, шум, реверс…) уже
        # наложены при рендере кусков (app/audio_edit.py); не наложены (холст) — тишиной.
        fx_rendered = any(op.get("by") == "censor" for op in (audio_plan or {}).get("ops") or [])
        mutes = final_pass_mutes(settings_.get("mute"), fx_rendered)
        gains = [g for g in normalize_gain(settings_.get("gain")) if g.get("track") is None]
        path = Path(clip.get("output_path") or "")
        if not (mutes or gains) or not path.exists() or not _has_audio_stream(path):
            return clip
        spans = [(m["start"], m["end"], span_floor(m)) for m in mutes]
        boosts = [(g["start"], g["end"], g["gain_db"]) for g in gains]
        mapper = self._final_mapper(plan, cut_plan)
        if mapper is not None:
            spans = [(*r, k) for r, k in ((mapper.range(a, b), k) for a, b, k in spans) if r]
            boosts = [(*r, g) for r, g in ((mapper.range(a, b), g) for a, b, g in boosts) if r]
        try:
            tmp = path.with_name(path.stem + ".mute" + path.suffix)
            args = build_source_volume_args(path, tmp, spans, boosts)
            if args is None:
                return clip
            _run_ffmpeg(args, timeout=60 * 20)
            tmp.replace(path)
            return self.store.update_clip(clip["id"], size_bytes=path.stat().st_size)
        except Exception as exc:  # noqa: BLE001 - тишина и громкость — надстройка, клип важнее
            return self._note_clip_error(clip["id"], f"тишина/громкость не наложены: {str(exc)[:200]}")

    # ---- звуковой монтаж: J/L, дорожки, ключи, эффекты (app/audio_edit.py) ----

    def _audio_plan(self, plan: dict, cut_plan=None) -> dict | None:
        """План звукового монтажа клипа для рендера; None — рендер звука прежний."""
        from app.audio_edit import render_plan, wants_render_plan

        if not wants_render_plan(plan.get("render_settings") or {}):
            return None
        try:
            source = self.store.get_source(int(plan["source_id"]))
        except (KeyError, TypeError, ValueError):
            return None
        return render_plan(plan, source, self._final_mapper(plan, cut_plan), cut_plan)

    def _jl_handles(self, source: dict, preset: dict, parts: list[dict], durations: list[float], join: dict,
                    jumps: list[bool] | None, shifts: list[float], audio_edit: dict | None, temp_dir: Path) -> dict:
        """J/L: «ручки» звука из исходника (секунды до начала / после конца части) — тем же
        графом звука, что у частей (дорожки, операции). Не прочиталась — тишина нужной длины."""
        from app.audio_edit import handle_windows, part_edit, plan_jl
        from app.transitions import plan_joins

        overlaps = plan_joins(join, durations, _source_gaps(parts), jumps)
        plan = plan_jl(durations, overlaps, shifts, join["audio"], jumps)
        clip0 = list((audio_edit or {}).get("clip0") or [])
        handles = []
        for item in handle_windows(parts, plan):
            k, start, length = item["part"], item["start"], item["length"]
            path = temp_dir / f"handle-{k:03d}-{item['side']}.wav"
            edit = part_edit(audio_edit, k, max(0.0, start), max(0.0, start) + length)
            if edit and k < len(clip0):
                edit["clip0"] = round(clip0[k] + (max(0.0, start) - float(parts[k]["start_sec"])), 3)
            piece_preset = {**preset, "audio_edit": edit} if edit else preset
            try:
                _run_ffmpeg(build_audio_handle_args(Path(source["local_path"]), path, start, length, piece_preset),
                            timeout=60 * 5)
            except Exception:  # noqa: BLE001 - ручки нет (конец записи) — тишина, склейка цела
                _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                             "anullsrc=r=48000:cl=stereo", "-t", f"{length:.3f}", str(path)], timeout=60)
            handles.append((k, item["side"], path))
        return {"plan": plan, "handles": handles}

    def _apply_audio_layers(self, clip: dict, motion: dict, plan: dict | None = None) -> dict:
        """Звуки ``audio``-слоёв и звук ``video``-слоёв с ``props.audio`` в готовый клип —
        последним проходом, видео копией.

        Последним, а не в моушн-проходе: субтитры распознаются по звуку основы,
        и бум/свист поверх речи мешал бы распознаванию (как и музыка). Ошибка не
        теряет клип — пишется в его ошибку.
        """
        from app.motion import has_layer_sound
        from app.motion_render import build_audio_layers_args, resolve_layer_assets

        layers = [layer for layer in motion.get("layers") or [] if has_layer_sound(layer)]
        path = Path(clip.get("output_path") or "")
        if not layers or not path.exists():
            return clip
        try:
            layers, assets, notes = resolve_layer_assets(self.store, layers)
            meta = probe_media(path)
            tmp = path.with_name(path.stem + ".sfx" + path.suffix)
            from app.audio_mix import normalize_mix
            from app.motion_render import plan_is_speechless, speech_level_db

            has_audio = _has_audio_stream(path)
            # Без речи (игра) «голос» — громкие места клипа: эффект чуть ниже них, а не на 10 дБ.
            speechless = bool(plan) and has_audio and plan_is_speechless(self.store, plan)
            effects = any(layer["type"] == "audio" and not layer["props"].get("mode") == "track" for layer in layers)
            args = build_audio_layers_args(
                path, tmp, layers, assets, meta.duration_sec, clip_has_audio=has_audio,
                # Уровень голоса нужен только эффектам (дорожки без автоподгонки) — без них не меряем.
                speech_db=speech_level_db(path) if has_audio and effects else None, speechless=speechless,
                notes=notes, mix=normalize_mix(((plan or {}).get("render_settings") or {}).get("mix")),
            )
            if args is not None:
                _run_ffmpeg(args, timeout=60 * 20)
                tmp.replace(path)
                meta = probe_media(path)
                clip = self.store.update_clip(clip["id"], size_bytes=path.stat().st_size, duration_sec=meta.duration_sec)
                clip = self._attach_qc(clip, path, meta)
            if notes:
                clip = self._note_clip_error(clip["id"], "; ".join(notes))
            return clip
        except Exception as exc:  # noqa: BLE001 - звуки слоёв — надстройка, клип важнее
            return self._note_clip_error(clip["id"], f"звуки слоёв не наложены: {str(exc)[:200]}")

    def _normalize_loudness(self, clip: dict, hold: float | None = None, target: float | None = None) -> dict:
        """Громкость под площадки: −14 LUFS и пик не выше −1.5 dBFS.

        Тихий рэп выходил на −20 LUFS, а клип со звуками — с пиком выше нуля:
        TikTok/Shorts всё равно приводят громкость к своей, только тихий ролик
        тогда звучит хуже соседей, а громкий хрипит. Два прохода loudnorm:
        замер, потом линейная поправка по замеру — без «качелей» громкости.
        Заодно — финал: стоп-кадр ``hold`` (None — RENDER_END_HOLD_SEC; у клипа — его
        end_hold_sec, ``_end_hold_sec``) и затухание звука на нём.
        Пики — по true peak (лимитер на ×4 частоте), готовый файл проверяется замером
        и при перебое пересобирается с потолком ниже.
        Сбой не теряет клип.
        """
        if not settings.render_loudnorm:  # RENDER_LOUDNORM=0 выключает весь финальный проход звука
            return clip
        path = Path(clip.get("output_path") or "")
        if not path.exists() or not _has_audio_stream(path):
            return clip
        # Цель клипа (render_settings.mix.target_lufs, app/audio_mix.py) или сервиса (−14).
        target = float(target) if target is not None else settings.render_loudnorm_target
        try:
            measured = _measure_loudness(path, target)
            if not audible(measured):
                return clip  # немой клип: ни громкость, ни затухание не нужны
            meta = probe_media(path)
            # Музыка уже доиграна на стоп-кадр (проход музыки, _music_filter с hold_sec):
            # звук длиннее картинки — стоп-кадр ровно на этот хвост, тишину не добавляем.
            tail = music_tail_sec(meta)
            hold = tail if tail > 0 else (self._end_hold_sec() if hold is None else max(0.0, float(hold)))
            pad = [f"apad=pad_dur={hold:.3f}"] if hold > 0 and tail <= 0 else []
            import logging

            needed = loudnorm_needed(measured, target)
            # Пики за потолком (ударные без голоса): контрольный замер цепочки и, если
            # лимитер съел громкость, компрессор с подъёмом (fit_loudness).
            plan = fit_loudness(measured, target, lambda f: _measure_loudness(path, target, f)) if needed else None
            logging.getLogger("uvicorn.error").info(
                "loudness clip=%s input_i=%s input_tp=%s needed=%s plan=%s tail=%s",
                clip.get("id"), measured.get("input_i"), measured.get("input_tp"), needed, plan, tail,
            )
            # Затухание — на конце самого звука: без музыки это конец речи (стоп-кадр
            # после — тишина, иначе на речь приходилась 0.1 с и звук обрывался), с
            # музыкой — конец её хвоста на стоп-кадре, то есть конец файла.
            fade = end_fade_filter(float(meta.duration_sec or 0))
            # Стоп-кадр в конце: последний кадр держится, пока затухает звук —
            # владелец: «сейчас она просто обрывается жёстко». Тогда картинку надо
            # перекодировать (tpad), иначе — копия без потерь.
            video = (["-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f}", "-c:v", "libx264", "-crf", "18",
                      "-preset", "veryfast", "-pix_fmt", "yuv420p"] if hold > 0 else ["-c:v", "copy"])
            tmp = path.with_name(path.stem + ".loud" + path.suffix)
            ceiling = TRUE_PEAK_LIMIT_DB
            attempts = 3
            for attempt in range(attempts):
                filters = list(pad)
                if plan:
                    # Только линейная поправка (одно число на весь клип) + лимитер на пики.
                    # loudnorm второго прохода, когда линейно не влезает в пик, молча
                    # переходит в динамический режим и вытягивает тихие места на 10–14 дБ:
                    # фоновые звуки и эффекты становились громче речи («звуки пердежа
                    # во всю громкость»). Компрессор — только когда пики упираются в
                    # потолок сильнее, чем лимитер срежет без потерь (loudness_plan).
                    filters.extend(loudness_filters({**plan, "ceiling_db": ceiling}))
                if fade:
                    filters.append(fade)
                if not filters:
                    return clip
                af = ",".join(filters + [f"aresample={OUTPUT_SAMPLE_RATE}", "aformat=sample_fmts=fltp:channel_layouts=stereo"])
                _run_ffmpeg(
                    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
                     "-map", "0:a:0", *video, "-af", af, "-c:a", "aac", "-b:a", FINAL_AUDIO_BITRATE,
                     "-movflags", "+faststart",
                     str(tmp)],
                    timeout=60 * 10,
                )
                # Проверка готового файла — true peak после AAC: пробил потолок —
                # ещё один проход с лимитером ниже на перебор. Тише цели при запасе по
                # пикам — ещё один проход с подъёмом после лимитера (finished_loudness_lift).
                after = _measure_loudness(tmp, target)
                retry = true_peak_retry_ceiling(after.get("input_tp"), ceiling)
                if retry is None:
                    lift = finished_loudness_lift(after, plan, target)
                    if not lift or attempt == attempts - 1:
                        break
                    logging.getLogger("uvicorn.error").info(
                        "loudness clip=%s finished %s LUFS at %s dBTP: lift %.2f dB", clip.get("id"),
                        after.get("input_i"), after.get("input_tp"), lift,
                    )
                    plan = {**plan, "post_gain_db": round(float(plan.get("post_gain_db") or 0.0) + lift, 2)}
                    continue
                if attempt == attempts - 1:
                    break
                logging.getLogger("uvicorn.error").info(
                    "loudness clip=%s true peak over %.1f dBTP: limiter %.2f -> %.2f", clip.get("id"),
                    LOUDNESS_PEAK_DB, ceiling, retry,
                )
                plan = plan or {"gain_db": 0.0, "compressor": None, "makeup_db": 0.0}
                ceiling = retry
            tmp.replace(path)
            meta = probe_media(path)
            clip = self.store.update_clip(clip["id"], size_bytes=path.stat().st_size, duration_sec=meta.duration_sec)
            return self._attach_qc(clip, path, meta)
        except Exception as exc:  # noqa: BLE001 - громкость — надстройка, клип важнее
            return self._note_clip_error(clip["id"], f"громкость/затухание не применены: {str(exc)[:200]}")

    def _finalize_render(
        self,
        clip_id: int,
        render_id: str,
        base_output_path: Path,
        output_path: Path,
        preset: dict,
        subtitle_profile_id: int | None,
    ) -> tuple[Path, list[Path]]:
        """Apply optional subtitle and music passes after the base clip exists.

        Each pass reads the previous step's output and writes a new file; the
        final pass writes the canonical output_path.

        Subtitles run FIRST, so transcription always reads the clean speech-only
        audio of the base clip. If music were mixed in first, the recogniser
        would hear speech + music and place/time the words poorly. Music is mixed
        last; it ducks against the (still clean) speech track and the subtitle
        burn's video is stream-copied, so burned captions are preserved.
        Returns the final path and the list of intermediate temp files to clean.
        """

        music = _resolve_music_settings(self.store, preset)
        passes: list[str] = []
        if subtitle_profile_id:
            passes.append("subtitle")
        voice_fx = (preset.get("mix") or {}).get("voice_fx")
        if voice_fx:
            # Цепочка голоса клипа (mix.voice_fx, app/audio_fx.py) — по звуку основы после распознавания
            # субтитров (оно слышит чистую речь) и до музыки (эффект не ложится на трек).
            passes.append("voice_fx")
        if music:
            passes.append("music")
        temp_paths: list[Path] = []
        current = base_output_path
        for index, name in enumerate(passes):
            is_last = index == len(passes) - 1
            dest = output_path if is_last else self._clip_dir() / f"{render_id}.{name}.mp4"
            if name == "voice_fx":
                _run_ffmpeg(build_voice_fx_args(current, dest, voice_fx), timeout=60 * 20)
            elif name == "music":
                music_settings = dict(music)
                music_settings["duration_sec"] = probe_media(current).duration_sec
                # Стоп-кадр в конце: музыка доигрывает и затухает на нём, а не до него.
                music_settings["hold_sec"] = float(preset.get("end_hold_sec") or 0.0)
                music_settings["volume"] = music_volume_for(current, music_settings)
                _run_ffmpeg(build_ffmpeg_music_args(current, dest, music_settings), timeout=60 * 20)
            else:
                self._render_subtitles(
                    clip_id=clip_id,
                    input_path=current,
                    output_path=dest,
                    subtitle_profile_id=int(subtitle_profile_id),
                    preset=preset,
                )
            if current is not base_output_path:
                temp_paths.append(current)
            current = dest
        return current, temp_paths
