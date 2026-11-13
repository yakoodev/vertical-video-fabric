"""Рендер: проходы поверх основы. Обложка, проверка качества, вставки, стоп-кадры, моушн-слои
(``app/render.py``)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.clip_settings import normalize_cover
from app.cover import build_burn_args, build_frame_args, build_image_args, cover_output_time
from app.ingest import probe_media
from app.render_args import build_unband_args
from app.render_exec import _has_audio_stream, _json_dict, _run_compositor, _run_ffmpeg, _safe_error
from app.render_presets import banded_pinned, layout_camera_regions
from app.render_qc import run_qc
from app.settings import settings
from app.transitions import plan_joins


class FinishRender:
    def _apply_cover(  # noqa: PLR0913 - обложке нужен весь контекст рендера
        self,
        clip_id: int,
        render_id: str,
        video: Path,
        preset: dict,
        cover: dict | None,
        durations: list[float],
        join: dict | None,
        gaps: list[float | None] | None = None,
        jump_joins: list[bool] | None = None,
    ) -> Path | None:
        """Make the clip's cover and, if asked, burn it in as the first frame.

        A cover problem never fails the render: the clip is still good, it just
        comes without a cover (the error is logged on the clip for the UI).
        """
        c = normalize_cover(cover)
        if c["mode"] == "none":
            return None
        width = int(preset.get("output_width") or 1080)
        height = int(preset.get("output_height") or 1920)
        out = settings.clip_dir / f"{render_id}.cover.jpg"
        try:
            if c["mode"] == "frame":
                overlaps = plan_joins(join, durations, gaps, jump_joins) if join else 0.0
                t = cover_output_time(durations, overlaps, c["piece"], c["offset"])
                _run_ffmpeg(build_frame_args(video, t, out), timeout=120)
            else:
                image = settings.data_dir / "covers" / c["image"]
                if not image.exists():
                    raise ValueError(f"cover image not found: {c['image']}")
                _run_ffmpeg(build_image_args(image, width, height, out), timeout=120)
            if c["burn"]:
                burned = video.with_name(video.stem + ".cover.mp4")
                extra = _json_dict(preset.get("extra_json"))
                _run_ffmpeg(
                    build_burn_args(
                        video, out, burned,
                        width=width, height=height, burn_sec=c["burn_sec"],
                        video_codec=str(preset.get("video_codec") or "libx264"),
                        video_bitrate=str(preset.get("video_bitrate") or "").strip(),
                        crf=extra.get("crf"),
                    ),
                    timeout=60 * 20,
                )
                burned.replace(video)
        except Exception as exc:  # noqa: BLE001 - the clip itself is fine
            out.unlink(missing_ok=True)
            self.store.set_clip_cover(clip_id, None, error=_safe_error(exc))
            return None
        self.store.set_clip_cover(clip_id, out)
        return out

    def _attach_qc(self, clip: dict, path: Path, metadata) -> dict:
        """Quality report for a finished render (never fails the render itself)."""
        qc = run_qc(path, duration_sec=metadata.duration_sec, width=metadata.width, height=metadata.height)
        if qc is None:
            return clip
        qc = self._qc_subtitle_sync(clip, path, qc)
        qc = self._qc_voiceover(clip, qc)
        self.store.set_clip_qc(clip["id"], qc)
        return self.store.get_clip(clip["id"])

    def _qc_voiceover(self, clip: dict, qc: dict) -> dict:
        """Озвучка клипа (app/voice_check.py): слышно ли написанное, не обрезаны ли слои, нет ли дыр, голос над
        музыкой, субтитры по голосу. Ошибки — issue ``voice`` «озвучка с ошибками: …»: файл остаётся (рендер не
        падает), но render_status и отчёт говорят об этом прямо. Сбой проверки QC не ломает."""
        try:
            plan_id = clip.get("clip_plan_id")
            if not plan_id:
                return qc
            from app.voice_check import qc_report

            voice = qc_report(self.store, int(plan_id))
        except Exception:  # noqa: BLE001 - QC никогда не ломает рендер
            return qc
        if voice is None:
            return qc
        qc = {**qc, "voice": voice}
        if not voice["ok"]:
            qc["issues"] = [*qc.get("issues", []), {"level": "warn", "code": "voice", "text": voice["summary"]}]
            qc["ok"] = False
        return qc

    def _apply_inserts(self, clip: dict, plan: dict, cut_plan=None) -> dict:
        """Overlay the clip's «Файлы для монтажа» inserts on the finished render.

        One extra ffmpeg pass on the output file; a failure keeps the render
        without inserts and says so in the clip's error, never loses the clip.
        """
        from app.montage_assets import build_inserts_args

        inserts, assets = self._final_inserts(plan, cut_plan)
        path = Path(clip.get("output_path") or "")
        if not inserts or not path.exists():
            return clip
        try:
            meta = probe_media(path)
            has_audio = _has_audio_stream(path)
            tmp = path.with_name(path.stem + ".inserts" + path.suffix)
            args = build_inserts_args(
                path, tmp, inserts, assets, meta.width, meta.height, meta.duration_sec, clip_has_audio=has_audio
            )
            if args is None:
                return clip
            _run_ffmpeg(args, timeout=60 * 20)
            tmp.replace(path)
            meta = probe_media(path)
            clip = self.store.update_clip(clip["id"], size_bytes=path.stat().st_size, duration_sec=meta.duration_sec)
            return self._attach_qc(clip, path, meta)
        except Exception as exc:  # noqa: BLE001 - inserts are an extra, never lose the clip
            return self._note_clip_error(clip["id"], f"вставки не наложены: {str(exc)[:200]}")

    def _final_inserts(self, plan: dict, cut_plan=None) -> tuple[list[dict], dict]:
        """Вставки клипа в секундах готового ролика и их файлы."""
        from app.montage_assets import normalize_inserts

        inserts = normalize_inserts((plan.get("render_settings") or {}).get("inserts"))
        mapper = self._final_mapper(plan, cut_plan)
        if mapper is not None and inserts:
            from app.cuts import remap_inserts

            inserts = remap_inserts(inserts, mapper)
        assets = {}
        for ins in inserts:
            try:
                assets[ins["asset_id"]] = self.store.get_montage_asset(ins["asset_id"])
            except KeyError:
                pass
        return inserts, assets

    @staticmethod
    def _apply_freezes(base: Path, preset: dict) -> None:
        """Стоп-кадры посреди ролика (app/freeze.py) — в основу, до компоновщика, субтитров и
        музыки: музыка играет и на вставленном стоп-кадре, слои и субтитры ложатся уже на него."""
        from app.freeze import build_freeze_args

        freezes = preset.get("freezes") or []
        if not freezes:
            return
        meta = probe_media(base)
        tmp = base.with_name(base.stem + ".freeze" + base.suffix)
        args = build_freeze_args(base, tmp, freezes, float(preset.get("fps") or 30), _has_audio_stream(base),
                                 float(meta.duration_sec or 0))
        if args is None:
            return
        _run_ffmpeg(args, timeout=60 * 20)
        tmp.replace(base)

    def _note_clip_error(self, clip_id: int, text: str) -> dict:
        """Дописать пометку в ошибку удачного клипа (моушн, вставки), не затирая прежние."""
        current = str(self.store.get_clip(clip_id).get("error") or "").strip()
        merged = f"{current}; {text}" if current else text
        return self.store.update_clip(clip_id, error=merged[:1000])

    def _apply_motion(
        self,
        render_id: str,
        video: Path,
        preset: dict,
        banner: dict | None,
        motion: dict,
        source: dict | None = None,
    ) -> list[str]:
        """Камера и слои поверх основы (на месте ``video``), затем баннер.

        Основа отрендерена без баннера — он ложится тут, поверх слоёв, чтобы
        камера его не зумила. Падение компоновщика не теряет клип: остаётся
        основа (с баннером), а причина — в пометках для ошибки клипа.
        Возвращает пометки.
        """
        from app.end_screen import comp_end_screen
        from app.formats import comp_ref, motion_formats
        from app.freeze import comp_freezes, freeze_zones
        from app.motion_render import (
            build_compositor_args,
            build_motion_mux_args,
            prepare_video_frames,
            reframe_for_output,
            resolve_layer_assets,
            unit_width,
            write_comp,
        )

        notes: list[str] = []
        work = self._tmp_dir() / f"motion-{render_id}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            layers, assets, missing = resolve_layer_assets(self.store, motion.get("layers") or [])
            notes.extend(missing)
            notes.extend(motion.get("host_notes") or [])
            assets.update(motion.get("chat_assets") or {})
            camera = motion.get("camera") or []
            band = motion.get("pin_band")
            drawn = video
            if layers or camera or motion.get("key") or motion.get("backgrounds") or motion.get("end_screen"):
                try:
                    meta = probe_media(video)
                    comp_w = meta.width or int(preset.get("output_width") or 1080)
                    comp_h = meta.height or int(preset.get("output_height") or 1920)
                    if band:
                        comp_h = int(preset.get("output_height") or 1920)  # основа выше на полосу окошка
                    regions = layout_camera_regions(preset, source, comp_w, comp_h) if camera else None
                    # Слои размечены в другом формате (9:16 → квадрат / 16:9 и т.п.) —
                    # переносим в новый опорный кадр одним масштабом по содержимому
                    # (app/aspect.py), камеру — тем же переносом; тот же формат — как есть.
                    # Тот же путь, что у кадра агента (view_frame).
                    out_format = motion_formats(preset)[0]
                    layers, camera = reframe_for_output(layers, camera, preset, assets)
                    comp = write_comp(
                        work / "comp.json",
                        width=comp_w,
                        height=comp_h,
                        ref=comp_ref(out_format),
                        fps=float(preset.get("fps") or 30),
                        duration=meta.duration_sec,
                        layers=layers,
                        camera=camera,
                        words=motion.get("words"),
                        chat=motion.get("chat"),
                        voice=motion.get("voice"),
                        levels=motion.get("levels"),
                        key=motion.get("key"),
                        background=motion.get("background"),
                        backgrounds=freeze_zones(motion.get("backgrounds"), preset.get("freezes")),
                        camera_box=(regions or {}).get("box"),
                        pinned=banded_pinned((regions or {}).get("pinned"), band, comp_h),
                        base_height=comp_h + int(band["h"]) if band else None,
                        freezes=comp_freezes(preset.get("freezes")),
                        end_screen=comp_end_screen(motion.get("end_screen"), layers, meta.duration_sec),
                        motion_blur=motion.get("motion_blur"),
                    )
                    assets_path = work / "assets.json"
                    assets_path.write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")
                    # Видео-слои: кадры их отрезков заранее (удаляются вместе с work).
                    videos_path, video_notes = prepare_video_frames(
                        layers, assets, work, fps=float(preset.get("fps") or 30),
                        out_width=unit_width(comp_w, out_format), clip_duration=meta.duration_sec,
                        fmt=out_format,
                    )
                    notes.extend(video_notes)
                    # Анимации с альфой (PNGtuber, GIF в image): PNG-кадры (удаляются вместе с work).
                    from app.anim_frames import prepare_anim_frames

                    anims_path, anim_notes = prepare_anim_frames(layers, assets, work,
                                                                 out_width=unit_width(comp_w, out_format))
                    notes.extend(anim_notes)
                    out = work / "motion.mp4"
                    _run_compositor(
                        build_compositor_args(
                            comp, assets_path, input_path=video, output_path=out,
                            crf=_json_dict(preset.get("extra_json")).get("crf"), videos_path=videos_path,
                            anims_path=anims_path,
                        ),
                        timeout=settings.motion_timeout_seconds,
                    )
                    if not out.is_file() or out.stat().st_size == 0:
                        raise RuntimeError("компоновщик не записал видео")
                    drawn = out
                except Exception as exc:  # noqa: BLE001 - моушн — надстройка, клип важнее
                    notes.append(f"моушн не наложен: {_safe_error(exc)[:300]}")
            if drawn is video and band:
                # Компоновщик не справился — собрать прежний кадр: окошко из полосы на место.
                drawn = work / "unbanded.mp4"
                _run_ffmpeg(build_unband_args(video, drawn, band, int(preset.get("output_height") or 1920)),
                            timeout=60 * 20)
            if drawn is video and not banner:
                return notes
            muxed = work / "muxed.mp4"
            try:
                _run_ffmpeg(build_motion_mux_args(drawn, video, muxed, preset, banner), timeout=60 * 20)
            except Exception as exc:  # noqa: BLE001
                if drawn is video:
                    raise  # баннер на голую основу не лёг — это настоящая ошибка рендера
                notes.append(f"моушн не наложен: {_safe_error(exc)[:300]}")
                if not banner:
                    return notes
                _run_ffmpeg(build_motion_mux_args(video, video, muxed, preset, banner), timeout=60 * 20)
            shutil.move(str(muxed), str(video))
            return notes
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _end_hold_sec(plan: dict | None = None, override: float | None = None) -> float:
        """Стоп-кадр в конце ролика; его кладёт финальный проход звука — без него
        (RENDER_LOUDNORM=0) стоп-кадра нет. Сколько: ``override`` (параметр рендера), иначе
        настройка клипа ``end_hold_sec`` (мему — 0: концовка без «замирания»), иначе
        RENDER_END_HOLD_SEC. Свои значения клипа — в рамках 0..END_HOLD_MAX_SEC."""
        if not settings.render_loudnorm:
            return 0.0
        from app.clip_settings import normalize_end_hold

        value = normalize_end_hold(override)
        if value is None and plan is not None:
            value = normalize_end_hold((plan.get("render_settings") or {}).get("end_hold_sec"))
        if value is None:
            value = float(settings.render_end_hold_sec or 0)
        return max(0.0, value)
