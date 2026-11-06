"""Библиотека рендера: пресеты ffmpeg, баннеры, музыкальные дорожки, бренд-киты, профили субтитров.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from pathlib import Path
from typing import Annotated, Any, Callable, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api_common import _payload_data
from app.ingest import probe_media
from app.settings import settings


class FfmpegPresetPayload(BaseModel):
    label: str
    description: str = ""
    output_width: int = 1080
    output_height: int = 1920
    fps: float = 30
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    video_bitrate: str = ""
    audio_bitrate: str = ""
    audio_mix_mode: Literal["primary", "secondary", "mix"] = "primary"
    audio_primary_stream: int = 0
    audio_primary_volume: float = 1
    audio_secondary_stream: int | None = None
    audio_secondary_volume: float = 1
    scale_mode: Literal["cover", "contain", "blur_background"] = "cover"
    crop_anchor: Literal["center", "top", "bottom"] = "center"
    banner_id: int | None = None
    subtitle_profile_id: int | None = None
    music_track_id: int | None = None
    music_volume: float = 0.25
    music_loop: bool = True
    music_fade_in_sec: float = 0
    music_fade_out_sec: float = 0
    music_duck: bool = True
    music_duck_amount: float = 0.6
    color_style: Literal["none", "warm", "cold", "cinematic", "vibrant", "noir", "vintage"] = "none"
    color_strength: float = 1
    vignette: float = 0
    grain: float = 0
    extra: dict = Field(default_factory=dict)


class FfmpegPresetPatchPayload(BaseModel):
    label: str | None = None
    description: str | None = None
    output_width: int | None = None
    output_height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    video_bitrate: str | None = None
    audio_bitrate: str | None = None
    audio_mix_mode: Literal["primary", "secondary", "mix"] | None = None
    audio_primary_stream: int | None = None
    audio_primary_volume: float | None = None
    audio_secondary_stream: int | None = None
    audio_secondary_volume: float | None = None
    scale_mode: Literal["cover", "contain", "blur_background"] | None = None
    crop_anchor: Literal["center", "top", "bottom"] | None = None
    banner_id: int | None = None
    subtitle_profile_id: int | None = None
    music_track_id: int | None = None
    music_volume: float | None = None
    music_loop: bool | None = None
    music_fade_in_sec: float | None = None
    music_fade_out_sec: float | None = None
    music_duck: bool | None = None
    music_duck_amount: float | None = None
    color_style: Literal["none", "warm", "cold", "cinematic", "vibrant", "noir", "vintage"] | None = None
    color_strength: float | None = None
    vignette: float | None = None
    grain: float | None = None
    extra: dict | None = None


class AudioTrackPatchPayload(BaseModel):
    label: str | None = None
    volume: float | None = None


class BannerPatchPayload(BaseModel):
    label: str | None = None
    position: Literal["top", "center", "bottom", "custom"] | None = None
    x: int | None = None
    y: int | None = None
    opacity: float | None = None


class SubtitleProfilePayload(BaseModel):
    label: str
    provider: Literal["mock", "whisper", "polza", "gemini", "artemox"] = "mock"
    model: str = "openai/gpt-4o-transcribe"
    language: str = ""
    timing_offset_sec: float | None = None
    font_family: str = "Arial"
    font_size: int = 64
    primary_color: str = "#FFFFFF"
    active_word_color: str = "#FACC15"
    outline_color: str = "#111827"
    back_color: str = "#000000"
    alignment: int = 2
    margin_v: int = 160
    max_words_per_line: int = 5
    uppercase: bool = False
    outline_width: float = 5
    shadow: float = 1


class SubtitleProfilePatchPayload(BaseModel):
    label: str | None = None
    provider: Literal["mock", "whisper", "polza", "gemini", "artemox"] | None = None
    model: str | None = None
    language: str | None = None
    timing_offset_sec: float | None = None
    font_family: str | None = None
    font_size: int | None = None
    primary_color: str | None = None
    active_word_color: str | None = None
    outline_color: str | None = None
    back_color: str | None = None
    alignment: int | None = None
    margin_v: int | None = None
    max_words_per_line: int | None = None
    uppercase: bool | None = None
    outline_width: float | None = None
    shadow: float | None = None


class ApplyBrandRequest(BaseModel):
    brand_id: int = Field(gt=0)


def build_render_library_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/media/banners/{banner_id}", include_in_schema=False)
    def banner_media(banner_id: int, _auth: auth_dep) -> FileResponse:
        try:
            banner = get_store().get_banner(banner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(banner["file_path"])
        try:
            path.resolve(strict=False).relative_to(settings.banner_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="banner media not found") from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="banner media not found")
        return FileResponse(path)

    @router.get("/media/audio-tracks/{track_id}", include_in_schema=False)
    def audio_track_media(track_id: int, _auth: auth_dep) -> FileResponse:
        try:
            track = get_store().get_audio_track(track_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(track["file_path"])
        try:
            path.resolve(strict=False).relative_to(settings.audio_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="audio track media not found") from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="audio track media not found")
        return FileResponse(path)

    @router.get("/api/ffmpeg-presets", tags=["Render"], summary="List ffmpeg presets")
    def api_ffmpeg_presets(_auth: auth_dep) -> list[dict]:
        """``default: true`` — пресет, которым рендерится клип с ``preset_id: null``
        (render.effective_preset; агент-монтажёр не знал, что значит null)."""
        from app.render import effective_preset

        fallback = effective_preset(get_store(), None) or {}
        return [{**preset, "default": preset["id"] == fallback.get("id")} for preset in get_store().list_ffmpeg_presets()]

    @router.post("/api/ffmpeg-presets", tags=["Render"], summary="Create ffmpeg preset")
    def api_create_ffmpeg_preset(payload: FfmpegPresetPayload, _auth: auth_dep) -> dict:
        try:
            return get_store().create_ffmpeg_preset(payload.label, **_payload_data(payload, exclude={"label"}))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/ffmpeg-presets/{preset_id}", tags=["Render"], summary="Get ffmpeg preset")
    def api_ffmpeg_preset(preset_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().get_ffmpeg_preset(preset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/api/ffmpeg-presets/{preset_id}", tags=["Render"], summary="Update ffmpeg preset")
    def api_update_ffmpeg_preset(
        preset_id: int,
        payload: FfmpegPresetPatchPayload,
        _auth: auth_dep,
    ) -> dict:
        try:
            return get_store().update_ffmpeg_preset(preset_id, **_payload_data(payload, exclude_none=True))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/ffmpeg-presets/{preset_id}", tags=["Render"], summary="Delete ffmpeg preset")
    def api_delete_ffmpeg_preset(preset_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().delete_ffmpeg_preset(preset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @router.get("/api/banners", tags=["Render"], summary="List banners")
    def api_banners(_auth: auth_dep) -> list[dict]:
        return get_store().list_banners()

    @router.get("/api/banners/{banner_id}", tags=["Render"], summary="Get banner")
    def api_banner(banner_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().get_banner(banner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/banners", tags=["Render"], summary="Upload banner overlay")
    async def api_create_banner(
        _auth: auth_dep,
        file: Annotated[UploadFile, File()],
        label: Annotated[str, Form()],
        position: Annotated[str, Form()] = "bottom",
        opacity: Annotated[float, Form()] = 1,
    ) -> dict:
        try:
            return await _save_banner_upload(file, label, position=position, opacity=opacity)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()

    @router.patch("/api/banners/{banner_id}", tags=["Render"], summary="Update banner")
    def api_update_banner(
        banner_id: int,
        payload: BannerPatchPayload,
        _auth: auth_dep,
    ) -> dict:
        try:
            return get_store().update_banner(banner_id, **_payload_data(payload, exclude_none=True))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/banners/{banner_id}", tags=["Render"], summary="Delete banner")
    def api_delete_banner(banner_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().delete_banner(banner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @router.get("/api/audio-tracks", tags=["Render"], summary="List background music tracks")
    def api_audio_tracks(_auth: auth_dep) -> list[dict]:
        return get_store().list_audio_tracks()

    @router.get("/api/audio-tracks/{track_id}", tags=["Render"], summary="Get music track")
    def api_audio_track(track_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().get_audio_track(track_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/audio-tracks", tags=["Render"], summary="Upload background music track")
    async def api_create_audio_track(
        _auth: auth_dep,
        file: Annotated[UploadFile, File()],
        label: Annotated[str, Form()],
        volume: Annotated[float, Form()] = 0.25,
    ) -> dict:
        try:
            return await _save_audio_upload(file, label, volume=volume)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()

    @router.post("/api/audio-tracks/from-url", tags=["Render"], summary="Background music from a link (YouTube etc.)")
    def api_audio_track_from_url(payload: dict, _auth: auth_dep) -> dict:
        """``{"url", "label"?}`` — скачать звук ссылки (yt-dlp) и завести дорожку фона."""
        from app.music_import import import_music_from_url

        try:
            return import_music_from_url(get_store(), str(payload.get("url") or ""), str(payload.get("label") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/api/audio-tracks/{track_id}", tags=["Render"], summary="Update music track")
    def api_update_audio_track(track_id: int, payload: AudioTrackPatchPayload, _auth: auth_dep) -> dict:
        try:
            return get_store().update_audio_track(track_id, **_payload_data(payload, exclude_none=True))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/audio-tracks/{track_id}", tags=["Render"], summary="Delete music track")
    def api_delete_audio_track(track_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().delete_audio_track(track_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @router.get("/api/brands", tags=["Brands"], summary="List brand kits")
    def api_brands(_auth: auth_dep) -> list[dict]:
        return get_store().list_brands()

    @router.post("/api/brands", tags=["Brands"], summary="Create a brand kit (font, colors, logo, CTA, music)")
    def api_create_brand(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        """Поля — как в ответе: name, font, colors{text,accent,plate}, logo_asset_id, caption_style,
        hook_style, cta{text,url_text,duration_sec}, music_track_id, backdrop{kind,colors,speed,intensity} (анимированный фон, app/backgrounds.py), sfx_policy."""
        try:
            return get_store().create_brand(str(payload.get("name") or ""), **{k: v for k, v in payload.items() if k != "name"})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/brands/{brand_id}", tags=["Brands"], summary="Get a brand kit")
    def api_brand(brand_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().get_brand(brand_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/api/brands/{brand_id}", tags=["Brands"], summary="Update a brand kit (nested objects merge)")
    def api_update_brand(brand_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        try:
            return get_store().update_brand(brand_id, **payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/brands/{brand_id}", tags=["Brands"], summary="Delete a brand kit")
    def api_delete_brand(brand_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().delete_brand(brand_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @router.post(
        "/api/clip-plans/{clip_plan_id}/apply-brand",
        tags=["Brands"],
        summary="Оформить клип в стиле бренда: субтитры, хук, логотип, призыв в конце, музыка, фон",
    )
    def api_apply_brand(clip_plan_id: int, payload: ApplyBrandRequest, _auth: auth_dep) -> dict:
        from app.brand_kit import apply_brand

        try:
            return apply_brand(get_store(), clip_plan_id, payload.brand_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/subtitle-profiles", tags=["Render"], summary="List subtitle profiles")
    def api_subtitle_profiles(_auth: auth_dep) -> list[dict]:
        return get_store().list_subtitle_profiles()

    @router.post("/api/subtitle-profiles", tags=["Render"], summary="Create subtitle profile")
    def api_create_subtitle_profile(payload: SubtitleProfilePayload, _auth: auth_dep) -> dict:
        try:
            return get_store().create_subtitle_profile(payload.label, **_payload_data(payload, exclude={"label"}, exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/subtitle-profiles/{profile_id}", tags=["Render"], summary="Get subtitle profile")
    def api_subtitle_profile(profile_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().get_subtitle_profile(profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/api/subtitle-profiles/{profile_id}", tags=["Render"], summary="Update subtitle profile")
    def api_update_subtitle_profile(
        profile_id: int,
        payload: SubtitleProfilePatchPayload,
        _auth: auth_dep,
    ) -> dict:
        try:
            return get_store().update_subtitle_profile(profile_id, **_payload_data(payload, exclude_none=True))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/subtitle-profiles/{profile_id}", tags=["Render"], summary="Delete subtitle profile")
    def api_delete_subtitle_profile(profile_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().delete_subtitle_profile(profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    async def _save_banner_upload(
        file: UploadFile,
        label: str,
        position: str = "bottom",
        opacity: float = 1,
    ) -> dict:
        safe_name = Path(file.filename or "banner.webm").name
        dest = settings.banner_dir / f"{uuid4().hex}-{safe_name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise ValueError("banner exceeds MAX_UPLOAD_BYTES")
                out.write(chunk)
        content_type = file.content_type or ""
        if dest.suffix.lower() == ".svg":
            # SVG-баннер с анимацией → webm с альфой (app/svg_webm.py, перенос svg-to-webm владельца):
            # оверлей рендера читает его как обычный webm-баннер. Длительность — по анимациям SVG.
            import asyncio
            import shutil as _shutil

            from app.svg_webm import SvgWebmError, convert

            try:
                info = await asyncio.to_thread(convert, dest.read_text("utf-8", errors="replace"), {})
            except SvgWebmError as exc:
                raise ValueError(str(exc)) from exc
            finally:
                dest.unlink(missing_ok=True)
            dest = dest.with_suffix(".webm")
            _shutil.copyfile(info["path"], dest)
            safe_name = Path(safe_name).with_suffix(".webm").name
            content_type = "video/webm"
        width = height = 0
        duration_sec = 0.0
        try:
            metadata = probe_media(dest)
            width = metadata.width
            height = metadata.height
            duration_sec = metadata.duration_sec
        except ValueError:
            pass
        return get_store().create_banner(
            label,
            dest,
            original_filename=safe_name,
            mime_type=content_type,
            width=width,
            height=height,
            duration_sec=duration_sec,
            position=position,
            opacity=opacity,
        )

    async def _save_audio_upload(file: UploadFile, label: str, volume: float = 0.25) -> dict:
        safe_name = Path(file.filename or "track.mp3").name
        dest = settings.audio_dir / f"{uuid4().hex}-{safe_name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise ValueError("audio track exceeds MAX_UPLOAD_BYTES")
                out.write(chunk)
        duration_sec = 0.0
        try:
            duration_sec = probe_media(dest).duration_sec
        except ValueError:
            pass
        track = get_store().create_audio_track(
            label,
            dest,
            original_filename=safe_name,
            mime_type=file.content_type or "",
            duration_sec=duration_sec,
            volume=volume,
        )
        # Где у трека энергия (старт громкой части, дроп, темп) — сразу, в потоке, не держа цикл.
        import asyncio

        from app.music_energy import mark_track

        return await asyncio.to_thread(mark_track, get_store(), track)

    return router
