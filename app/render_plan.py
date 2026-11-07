"""Рендер: сборка клипа по плану, от кусков до финального прохода (``app/render.py``)."""

from __future__ import annotations

from typing import Any

from app.backgrounds import needs_zones
from app.heavy_queue import heavy_slot


class PlanRender:
    def render_clip_plan(self, clip_plan_id: int, *args: Any, **kwargs: Any) -> dict:
        """Рендер клипа — по одному на весь сервис.

        Рендер тяжёлый (ffmpeg + компоновщик моушна). Два одновременных — два агента
        или агент и кнопка в редакторе — уже трижды роняли Docker по памяти; очередь
        дешевле. Очередь общая с распознаванием и автофокусом (app/heavy_queue.py); рендер
        может позвать рендер в том же потоке (подборка) — вложенный вызов места не ждёт."""
        from app.clip_upload import NO_MONTAGE, is_upload_plan

        if is_upload_plan(self.store, clip_plan_id):
            raise ValueError(NO_MONTAGE)
        with heavy_slot("render", f"клип {clip_plan_id}"):
            from app.clip_groups import capture_quiet, snapshot_render

            stamp = self._plan_fingerprint(clip_plan_id)
            state = capture_quiet(self.store, clip_plan_id)
            clip = self._render_clip_plan_unlocked(clip_plan_id, *args, **kwargs)
            if isinstance(clip, dict) and clip.get("id"):
                # Вечный снимок монтажа этой версии: откат с экрана клипа (история правок — всего 20 шагов).
                snapshot_render(self.store, clip_plan_id, int(clip["id"]), state)
            if stamp and isinstance(clip, dict) and clip.get("id"):
                # С какого состояния клипа сделан файл: lint_clip сверяет, правили ли клип после.
                try:
                    self.store.set_clip_plan_fingerprint(int(clip["id"]), stamp)
                    clip = {**clip, "plan_fingerprint": stamp}
                except Exception:  # noqa: BLE001 - отпечаток — удобство, рендер важнее
                    pass
        return self._after_render(clip)

    def _plan_fingerprint(self, clip_plan_id: int) -> str | None:
        try:
            from app.quality_lint import plan_fingerprint

            return plan_fingerprint(self.store, clip_plan_id)
        except Exception:  # noqa: BLE001
            return None

    def _render_clip_plan_unlocked(
        self,
        clip_plan_id: int,
        ffmpeg_preset_id: int | None = None,
        subtitle_profile_id: int | None = None,
        banner_id: int | None = None,
        music_track_id: int | None = None,
        music_volume: float | None = None,
        subtitle_offset_sec: float | None = None,
        subtitle_provider: str | None = None,
        banner_height_frac: float | None = None,
        banner_y_frac: float | None = None,
        subtitle_margin_v: int | None = None,
        mirror: bool = False,
        transition: dict | None = None,
        cover: dict | None = None,
        layout: dict | None = None,
        music_start_sec: float | None = None,
        aspect: str | None = None,
        end_hold_sec: float | None = None,
    ) -> dict:
        """``end_hold_sec`` — стоп-кадр в конце для этого рендера (0..1.5); None — из настроек
        клипа (render_settings.end_hold_sec), там нет — RENDER_END_HOLD_SEC."""
        plan = self.store.get_clip_plan(clip_plan_id)
        segments = plan.get("segments") or []
        if not segments:
            raise ValueError("clip plan has no segments")
        # Формат вывода (app/formats.py: 9:16 / 1:1 / 4:5 / 16:9): из запроса или из настроек
        # клипа (format, у старых — aspect); проверка до статуса «rendering», как и у вырезов.
        from app.formats import normalize_format, settings_layers_format, settings_render_format

        if aspect is None:
            aspect = settings_render_format(plan.get("render_settings"))
        aspect = normalize_format(aspect)
        layers_format = settings_layers_format(plan.get("render_settings"))
        saved_format = settings_render_format(plan.get("render_settings"))
        if subtitle_margin_v is not None and saved_format != aspect:
            # Отступ субтитров из настроек — доля высоты кадра СОХРАНЁННОГО формата
            # (settings_to_render_kwargs); разовый рендер в другом формате — та же доля его кадра.
            from app.formats import frame_size

            subtitle_margin_v = round(subtitle_margin_v * frame_size(aspect)[1] / frame_size(saved_format)[1])
        end_hold = self._end_hold_sec(plan, end_hold_sec)
        # Джамп-каты (app/cuts.py): нет вырезов — None, и всё ниже как раньше.
        # До статуса «rendering»: вырезы на весь клип — ошибка запроса, а не зависший клип.
        cut_plan = self._cut_plan(plan, ffmpeg_preset_id)
        self.store.update_clip_plan_status(clip_plan_id, "rendering")
        segment_ids = [segment["id"] for segment in segments]
        # Моушн, как и вставки, живёт в настройках самого клипа. Нет слоёв и
        # камеры — рендер ровно прежний (те же команды ffmpeg).
        from app.motion import has_pngtuber, plan_motion, react_sources, visual_motion

        motion = plan_motion(plan.get("render_settings"))
        visual = visual_motion(motion)
        if visual:
            from app.motion_render import attach_host_data

            # Слоям «субтитры» и «чат» нужны данные клипа: слова речи и сообщения чата.
            # Берутся во времени клипа ДО вырезов (чат фильтруется по исходным
            # слоям), а сдвигаются ниже — вместе со слоями.
            visual = attach_host_data(self.store, plan, visual)
            if visual and visual.get("words") and subtitle_profile_id is not None:
                # Анимированные субтитры-слой заменяют прожигаемые: два текста речи
                # друг на друге — брак.
                subtitle_profile_id = None
        if cut_plan is not None:
            # Слои, камера и чат — во времени ГОТОВОГО клипа (после переходов),
            # вырезы — во времени кусков подряд: переводим туда и обратно.
            visual = self._cut_motion(plan, cut_plan, visual)
            from app.cuts import remap_motion

            motion = remap_motion(motion, self._finished_cut_mapper(plan, cut_plan))
        visual = self._with_chroma_key(plan, visual)
        if visual and has_pngtuber(visual.get("layers")):
            # PNGtuber: рот по голосу дорожки — состояния по кадрам во времени готового ролика
            # (слои уже после вырезов), секунды источника — через ClipClock (app/pngtuber.py).
            from app.pngtuber import attach_voice

            voice_fps = float(self._preset(ffmpeg_preset_id).get("fps") or 30)
            visual = attach_voice(self.store, plan, visual, voice_fps, "final")
        if visual and react_sources(visual.get("layers")):
            # Реакция слоёв на звук: огибающие громкости по кадрам готового ролика (app/audio_react.py);
            # музыка — та, что ляжет в этот рендер (выбор запроса или настройки клипа).
            from app.audio_react import attach_levels

            rs = plan.get("render_settings") or {}
            music = None
            if music_track_id is not None:
                music = {"track_id": music_track_id,
                         "start_sec": music_start_sec if music_start_sec is not None else rs.get("music_start_sec")}
            visual = attach_levels(self.store, plan, visual, float(self._preset(ffmpeg_preset_id).get("fps") or 30),
                                   "final", music)
        # Стоп-кадры посреди ролика (app/freeze.py): основа получит их в render_*; здесь —
        # слои «continue» сдвигаются за вставку, звуки слоёв — за любую вставку.
        freezes = self._freezes(plan)
        if freezes:
            from app.cuts import remap_motion
            from app.freeze import FreezeMap, holds_layers, shift_voice

            def running(f: dict) -> bool:
                return not holds_layers(f)

            if visual:
                moved = remap_motion(visual, FreezeMap(freezes, only=running))
                if visual.get("voice"):
                    moved = {**moved, "voice": shift_voice(visual["voice"], freezes, only=running)}
                if visual.get("levels"):
                    from app.audio_react import shift_levels

                    moved = {**moved, "levels": shift_levels(visual["levels"], freezes, only=running)}
                visual = moved
            motion = remap_motion(motion, FreezeMap(freezes))
        # Раскладка стрима — тоже из настроек клипа (single = прежний рендер).
        if layout is None:
            layout = (plan.get("render_settings") or {}).get("layout")
        if music_start_sec is None:
            # Смещение старта музыки — из настроек клипа, как раскладка.
            music_start_sec = (plan.get("render_settings") or {}).get("music_start_sec")
        if not (visual or {}).get("key") and needs_zones(layout, segments):
            # Анимированный фон meme / куска «шире окна» рисует компоновщик — он нужен
            # и клипу без слоёв (зоны фона считает рендер, app/backgrounds.py).
            visual = visual or {"layers": [], "camera": []}
        # Звуковой монтаж (app/audio_edit.py): дорожки, эффекты, J/L, ключи — None, если его нет.
        audio_plan = self._audio_plan(plan, cut_plan)
        # Сведение (app/audio_mix.py: auto_mix / set_mix) — в проходы музыки, звуков слоёв и громкости.
        from app.audio_mix import normalize_mix

        mix = normalize_mix((plan.get("render_settings") or {}).get("mix"))
        pass_mix = dict(mix)
        if pass_mix.get("voice_fx"):
            # Цепочка голоса: у холста — на слоях-голосе (проход звуков слоёв), у клипа с исходником —
            # своим проходом по звуку основы (_finalize_render, до музыки).
            from app.canvas import is_canvas

            try:
                canvas_clip = is_canvas(self.store.get_source(int(plan["source_id"])))
            except (KeyError, TypeError, ValueError):
                canvas_clip = True
            if canvas_clip:
                pass_mix.pop("voice_fx")
        if pass_mix:
            audio_plan = {**(audio_plan or {"notes": []}), "mix": pass_mix}
        from app.piece_color import has_fix
        from app.piece_speed import is_retimed

        # Кусок со скоростью/реверсом (app/piece_speed.py) или своей цветокоррекцией
        # (app/piece_color.py) — путь монтажа: части несут скорость и поправку цвета.
        retimed_parts = (list(segments) if cut_plan is None
                         and any(is_retimed(s) or has_fix(s) for s in segments) else None)
        if retimed_parts is not None:
            clip = self.render_montage(
                segment_ids,
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                title=plan["title"],
                description=plan["description"],
                music_track_id=music_track_id,
                music_volume=music_volume,
                music_start_sec=music_start_sec,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                transition=transition,
                cover=cover,
                motion=visual,
                layout=layout,
                cut_parts=retimed_parts,
                aspect=aspect,
                end_hold_sec=end_hold,
                layers_format=layers_format,
                freezes=freezes,
                **({"audio_edit": audio_plan} if audio_plan else {}),
            )
        elif cut_plan is not None:
            # Куски делятся на части в обход вырезов — это всегда путь монтажа.
            clip = self.render_montage(
                segment_ids,
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                title=plan["title"],
                description=plan["description"],
                music_track_id=music_track_id,
                music_volume=music_volume,
                music_start_sec=music_start_sec,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                transition=transition,
                cover=cut_plan.remap_cover(cover),
                motion=visual,
                layout=layout,
                cut_parts=cut_plan.parts,
                jump_joins=cut_plan.jump_joins,
                aspect=aspect,
                end_hold_sec=end_hold,
                layers_format=layers_format,
                freezes=freezes,
                **({"audio_edit": audio_plan} if audio_plan else {}),
            )
        elif len(segment_ids) == 1:
            clip = self.render_segment(
                segment_ids[0],
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                music_track_id=music_track_id,
                music_volume=music_volume,
                music_start_sec=music_start_sec,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                cover=cover,
                motion=visual,
                layout=layout,
                aspect=aspect,
                end_hold_sec=end_hold,
                layers_format=layers_format,
                freezes=freezes,
                **({"audio_edit": audio_plan} if audio_plan else {}),
            )
        else:
            clip = self.render_montage(
                segment_ids,
                ffmpeg_preset_id=ffmpeg_preset_id,
                subtitle_profile_id=subtitle_profile_id,
                banner_id=banner_id,
                clip_plan_id=clip_plan_id,
                title=plan["title"],
                description=plan["description"],
                music_track_id=music_track_id,
                music_volume=music_volume,
                music_start_sec=music_start_sec,
                subtitle_offset_sec=subtitle_offset_sec,
                subtitle_provider=subtitle_provider,
                banner_height_frac=banner_height_frac,
                banner_y_frac=banner_y_frac,
                subtitle_margin_v=subtitle_margin_v,
                mirror=mirror,
                transition=transition,
                cover=cover,
                motion=visual,
                layout=layout,
                aspect=aspect,
                end_hold_sec=end_hold,
                layers_format=layers_format,
                freezes=freezes,
                **({"audio_edit": audio_plan} if audio_plan else {}),
            )
        if clip["status"] == "succeeded" and (audio_plan or {}).get("notes"):
            clip = self._note_clip_error(clip["id"], "; ".join(audio_plan["notes"]))
        if clip["status"] == "succeeded":
            clip = self._apply_mute(clip, plan, cut_plan, audio_plan)
            clip = self._apply_inserts(clip, plan, cut_plan)
            if motion:
                clip = self._apply_audio_layers(clip, motion, plan)
            # Цель клипа (mix.target_lufs) — только если задана: без неё вызов ровно прежний.
            clip = self._normalize_loudness(clip, hold=end_hold, **({"target": mix["target_lufs"]}
                                                                   if mix.get("target_lufs") is not None else {}))
        if clip["title"] != plan["title"] or clip["description"] != plan["description"]:
            clip = self.store.update_clip(
                clip["id"],
                title=plan["title"],
                description=plan["description"],
            )
        self.store.update_clip_plan_status(
            clip_plan_id,
            "rendered" if clip["status"] == "succeeded" else "failed",
        )
        return clip

    def _with_chroma_key(self, plan: dict, visual: dict | None) -> dict | None:
        """Источник на зелёнке — ключ и фон клипа едут в компоновщик (app/chroma_key.py)."""
        from app.chroma_key import normalize_backdrop

        segments = plan.get("segments") or []
        try:
            key = self.store.get_source(int(segments[0]["source_id"])).get("chroma_key") if segments else None
        except (KeyError, ValueError, TypeError):
            key = None
        if not key:
            return visual
        backdrop = normalize_backdrop((plan.get("render_settings") or {}).get("backdrop"))
        return {**(visual or {"layers": [], "camera": []}), "key": key, "background": backdrop}

    def _cut_plan(self, plan: dict, ffmpeg_preset_id: int | None = None, fps: float | None = None):
        """План джамп-катов клипа (app/cuts.py) или None, если вырезов нет.

        Пресет читается только при вырезах: без них рендер не делает ни
        одного лишнего шага."""
        from app.cuts import normalize_cuts, plan_cuts

        cuts = normalize_cuts((plan.get("render_settings") or {}).get("cuts"))
        if not cuts:
            return None
        if fps is None:
            fps = float(self._preset(ffmpeg_preset_id).get("fps") or 30)
        return plan_cuts(plan.get("segments") or [], cuts, fps)

    @staticmethod
    def _freezes(plan: dict | None) -> list[dict]:
        """Стоп-кадры посреди ролика из настроек клипа (app/freeze.py)."""
        from app.freeze import normalize_freezes

        return normalize_freezes(((plan or {}).get("render_settings") or {}).get("freezes"))

    def _final_mapper(self, plan: dict, cut_plan=None):
        """Время готового ролика без вырезов и стоп-кадров → в готовом ролике (None — то же)."""
        from app.freeze import chain

        cut = self._finished_cut_mapper(plan, cut_plan) if cut_plan is not None else None
        return chain(cut, self._freezes(plan))

    @staticmethod
    def _finished_cut_mapper(plan: dict, cut_plan):
        """Время готового клипа без вырезов → с вырезами (app/cuts.FinishedCutMap)."""
        transition = (plan.get("render_settings") or {}).get("transition")
        return cut_plan.finished_mapper(plan.get("segments") or [], transition)

    def _cut_motion(self, plan: dict, cut_plan, visual: dict | None) -> dict | None:
        """Видимый моушн (слои, камера, слова, чат) — во время клипа после вырезов."""
        from app.cuts import cut_caption_words, remap_motion

        if not visual:
            return visual
        had_words = bool(visual.get("words"))
        mapper = self._finished_cut_mapper(plan, cut_plan)
        fresh = remap_motion(visual, mapper)
        if had_words:
            # Слова пересчитываются из субтитров: вырез — до сдвига на переходы.
            fresh = {**fresh, "words": cut_caption_words(self.store, plan, cut_plan)}
            # Подсветка слов — номера в словах ДО вырезов: после них слово с тем же номером
            # уже другое (app/caption_marks.py) — переставляем на то же слово.
            from app.caption_marks import rebind_layers

            old = [{"w": w["w"], "t": float(w["t0"])} for w in visual.get("words") or []]
            new = [{"w": w["w"], "t": float(w["t0"])} for w in fresh.get("words") or []]
            layers, _lost = rebind_layers(fresh.get("layers") or [], old, new, time_map=mapper)
            fresh = {**fresh, "layers": layers}
        # Слой целиком в вырезе пропал — может не остаться ничего видимого.
        return fresh if (fresh.get("layers") or fresh.get("camera") or fresh.get("end_screen")) else None
