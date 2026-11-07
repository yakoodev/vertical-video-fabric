"""Готовые ролики: список и карточка, отдача файла, загрузка готового ролика (app/clip_upload.py), обложки, метаданные публикации.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Any, Callable
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.ai.publish_meta import generate_publish_metadata
from app.api_common import _optional_form_float, _optional_form_int, THUMB_CACHE
from app.settings import settings
from app.settings_api import _settings_panel_data
from app.thumbs import clip_thumb


COVER_UPLOAD_MAX_BYTES = 20 * 1024 * 1024


def _clip_screen(clip: dict) -> str:
    """Адрес экрана клипа: /clips/:id берёт id плана. Файл без плана своего экрана не имеет — он в «Готовых роликах»."""
    plan_id = clip.get("clip_plan_id")
    return f"/clips/{int(plan_id)}" if plan_id else "/pubs?tab=ready"


class ClipRenamePayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CoversPayload(BaseModel):
    count: int = Field(default=3, ge=1, le=6, description="сколько вариантов (1–6)")
    texts: list[str] | None = Field(default=None, description="свои тексты (2–5 слов); нет — ИИ/заголовок клипа")
    styles: list[str] | None = Field(default=None, description="yellow | white | plate | accent по вариантам")


class CoverChoicePayload(BaseModel):
    apply_to_render: bool = Field(default=True, description="ещё и обложка плана: следующий рендер вошьёт её")


def build_clips_router(
    auth_dep: Any,
    get_store: Callable[[], Any],
    get_clip_render_service: Callable[[], Any],
    get_probe_media: Callable[[], Any],
) -> APIRouter:
    router = APIRouter()

    @router.post("/ui/clips/upload", include_in_schema=False)
    async def ui_upload_clip(
        _auth: auth_dep,
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
        project_id: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        clip = await _accept_upload(file, title, description, project_id)
        return RedirectResponse(url=_clip_screen(clip), status_code=303)

    @router.post("/api/clips/upload", tags=["Render"], summary="Загрузить готовый ролик: файл не меняется, появляется клип с экраном")
    async def api_upload_clip(
        _auth: auth_dep,
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
        project_id: Annotated[str, Form()] = "",
    ) -> dict:
        clip = await _accept_upload(file, title, description, project_id)
        return {"clip_id": int(clip["id"]), "clip_plan_id": int(clip["clip_plan_id"]), "to": _clip_screen(clip),
                "project_id": _optional_form_int(project_id)}

    @router.post("/ui/clips/{clip_id}/render", include_in_schema=False)
    def ui_render_uploaded_clip(
        clip_id: int,
        _auth: auth_dep,
        ffmpeg_preset_id: Annotated[str, Form()] = "",
        subtitle_profile_id: Annotated[str, Form()] = "",
        banner_id: Annotated[str, Form()] = "",
        music_track_id: Annotated[str, Form()] = "",
        music_volume: Annotated[str, Form()] = "",
        use_subtitles: Annotated[bool, Form()] = False,
        use_banner: Annotated[bool, Form()] = False,
        use_music: Annotated[bool, Form()] = False,
        subtitle_offset_sec: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        defaults = _settings_panel_data(get_store())
        selected_subtitle_profile_id = _optional_form_int(subtitle_profile_id) if use_subtitles else None
        if use_subtitles and not selected_subtitle_profile_id:
            selected_subtitle_profile_id = defaults["default_subtitle_profile_id"] or None
        selected_banner_id = _optional_form_int(banner_id) if use_banner else 0
        if use_banner and not selected_banner_id:
            selected_banner_id = defaults["default_banner_id"] or None
        volume_provided = str(music_volume).strip() != ""
        music_in_play = use_music or str(music_track_id).strip() != "" or volume_provided
        selected_music_track_id = _optional_form_int(music_track_id) if music_in_play else None
        selected_music_volume = float(music_volume) if volume_provided else None
        try:
            clip = get_clip_render_service().render_uploaded_clip(
                clip_id,
                ffmpeg_preset_id=_optional_form_int(ffmpeg_preset_id),
                subtitle_profile_id=selected_subtitle_profile_id,
                banner_id=selected_banner_id,
                music_track_id=selected_music_track_id,
                music_volume=selected_music_volume,
                subtitle_offset_sec=_optional_form_float(subtitle_offset_sec),
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=_clip_screen(clip), status_code=303)

    @router.get("/media/clips/{clip_id}", include_in_schema=False)
    def clip_media(clip_id: int, _auth: auth_dep) -> FileResponse:
        try:
            clip = get_store().get_clip(clip_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(clip["output_path"])
        try:
            path.resolve(strict=False).relative_to(settings.clip_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="clip media not found") from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="clip media not found")
        return FileResponse(path)

    @router.get("/media/clips/{clip_id}/thumb", include_in_schema=False)
    def clip_thumb_media(clip_id: int, _auth: auth_dep) -> FileResponse:
        try:
            clip = get_store().get_clip(clip_id)
            return FileResponse(clip_thumb(clip, get_store().get_clip_cover_path(clip_id)), headers=THUMB_CACHE)
        except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=404, detail="clip thumb not found") from exc

    @router.get("/media/clips/{clip_id}/cover", include_in_schema=False)
    def clip_cover_media(clip_id: int, _auth: auth_dep) -> FileResponse:
        """The rendered clip's cover still (see app/cover.py)."""
        try:
            raw = get_store().get_clip_cover_path(clip_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not raw:
            raise HTTPException(status_code=404, detail="clip has no cover")
        path = Path(raw)
        try:
            path.resolve(strict=False).relative_to(settings.clip_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="cover not found") from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="cover not found")
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/api/covers", tags=["Render"], summary="Upload a cover image (fitted to the clip format's frame JPEG)")
    async def api_upload_cover(
        _auth: auth_dep,
        file: UploadFile = File(...),
        format: Annotated[str, Form(description="формат клипа: 9:16 (1080×1920, по умолчанию), 1:1, 4:5, 16:9 (1920×1080)")] = "",
        clip_plan_id: Annotated[int | None, Form(description="или клип — формат из его настроек")] = None,
    ) -> dict:
        """Store an uploaded picture as a cover. Normalised by ffmpeg into a JPEG of the
        format's frame (app/formats.py: 9:16 — 1080×1920 as always, 16:9 — 1920×1080, the
        YouTube thumbnail) under a generated name — the only kind of name clip settings accept.
        The render fits the cover to the output frame again, so a cover of another format still works."""
        from app.formats import frame_size, normalize_format, settings_render_format

        try:
            if format:
                fmt = normalize_format(format)
            elif clip_plan_id is not None:
                fmt = settings_render_format(get_store().get_clip_plan(int(clip_plan_id)).get("render_settings"))
            else:
                fmt = normalize_format(None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        width, height = frame_size(fmt)
        covers = settings.data_dir / "covers"
        covers.mkdir(parents=True, exist_ok=True)
        data = await file.read(COVER_UPLOAD_MAX_BYTES + 1)
        if not data:
            raise HTTPException(status_code=400, detail="пустой файл")
        if len(data) > COVER_UPLOAD_MAX_BYTES:
            raise HTTPException(status_code=413, detail="картинка больше 20 МБ")
        name = f"{uuid4().hex}.jpg"
        raw = settings.tmp_dir / f"cover-upload-{name}.bin"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(data)
        out = covers / name
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
                 "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
                 "-frames:v", "1", "-q:v", "2", str(out)],
                capture_output=True, text=True, timeout=60, check=False,
            )
        finally:
            raw.unlink(missing_ok=True)
        if proc.returncode != 0 or not out.exists():
            raise HTTPException(status_code=400, detail="это не картинка (ffmpeg не смог её прочитать)")
        return {"image": name, "url": f"/media/covers/{name}"}

    @router.get("/media/covers/{name}", include_in_schema=False)
    def cover_image_media(name: str, _auth: auth_dep) -> FileResponse:
        if len(name) != 36 or not name.endswith(".jpg") or not set(name[:-4]) <= set("0123456789abcdef"):
            raise HTTPException(status_code=404, detail="cover not found")
        path = settings.data_dir / "covers" / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="cover not found")
        return FileResponse(path, media_type="image/jpeg")

    @router.get("/api/clips", tags=["Render"], summary="List clips")
    def api_clips(_auth: auth_dep, source_id: int | None = None) -> list[dict]:
        return get_store().list_clips(source_id)

    @router.get("/api/clips/{clip_id}", tags=["Render"], summary="Get clip details")
    def api_clip(clip_id: int, _auth: auth_dep) -> dict:
        try:
            clip = get_store().get_clip(clip_id)
            clip["subtitle_tracks"] = get_store().list_subtitle_tracks(clip_id)
            return clip
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/api/clips/{clip_id}", tags=["Render"], summary="Delete a rendered clip")
    def api_delete_clip(clip_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().delete_clip(clip_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @router.patch("/api/clips/{clip_id}", tags=["Render"], summary="Rename a clip")
    def api_rename_clip(clip_id: int, payload: ClipRenamePayload, _auth: auth_dep) -> dict:
        try:
            return get_store().update_clip(clip_id, title=payload.title.strip())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/api/clips/{clip_id}/publish-meta",
        tags=["Render"],
        summary="Generate title, description and hashtags for a clip",
    )
    def api_clip_publish_meta(clip_id: int, _auth: auth_dep) -> dict:
        try:
            return generate_publish_metadata(get_store(), clip_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"Не удалось сгенерировать: {exc}") from exc

    @router.get("/api/clips/{clip_id}/covers", tags=["Render"], summary="Cover variants of a rendered clip")
    def api_clip_covers(clip_id: int, _auth: auth_dep) -> dict:
        from app.cover_variants import list_covers

        try:
            get_store().get_clip(clip_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"clip_id": clip_id, "covers": list_covers(get_store(), clip_id)}

    @router.post(
        "/api/clips/{clip_id}/covers",
        tags=["Render"],
        summary="Generate cover variants: strong frames + big Russian hook text (app/cover_variants.py)",
    )
    def api_generate_clip_covers(clip_id: int, payload: CoversPayload, _auth: auth_dep) -> dict:
        from app.cover_variants import generate_covers

        try:
            return generate_covers(get_store(), clip_id, count=payload.count, texts=payload.texts, styles=payload.styles)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"Обложки не получились: {exc}") from exc

    @router.post(
        "/api/clips/{clip_id}/covers/{cover_id}/choose",
        tags=["Render"],
        summary="Make a cover variant the clip's cover (preview, approval card, YouTube thumbnail for 16:9)",
    )
    def api_choose_clip_cover(clip_id: int, cover_id: int, _auth: auth_dep, payload: CoverChoicePayload | None = None) -> dict:
        from app.cover_variants import choose_cover

        try:
            return choose_cover(get_store(), cover_id, apply_to_render=(payload or CoverChoicePayload()).apply_to_render,
                                clip_id=clip_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/media/clips/{clip_id}/covers/{cover_id}", include_in_schema=False)
    def clip_cover_variant_media(clip_id: int, cover_id: int, _auth: auth_dep) -> FileResponse:
        from app.cover_variants import cover_file

        try:
            path = cover_file(get_store(), clip_id, cover_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="cover not found") from exc
        return FileResponse(path, media_type="image/jpeg")

    async def _accept_upload(file: UploadFile, title: str, description: str, project_id: str) -> dict:
        from app.projects import get_project

        try:
            wanted = _optional_form_int(project_id)
            if wanted is not None:
                get_project(get_store(), wanted, include_sources=False)  # до записи файла: чужой номер не оставит мусора
            return await _register_uploaded_clip(file, title, description, wanted)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Такого проекта нет") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()

    async def _register_uploaded_clip(file: UploadFile, title: str, description: str, project_id: int | None = None) -> dict:
        """Ingest a finished video and register it as a real clip with its own screen.

        The pristine upload is stored in the source directory as a backing
        ``clip_upload`` source (hidden from Projects), and a byte-identical copy is
        placed in the clip directory as the clip's playable output, marked 'succeeded'.
        The clip gets a plan (app/clip_upload.py), so ``clips.clip_plan_id`` is set and
        ``/clips/<plan id>`` opens it. Keeping the untouched original lets the clip
        later be rendered with subtitles, music and a banner from a clean source as a
        new version — see ``ClipRenderService.render_uploaded_clip``.
        """
        from app import clip_upload

        safe_name = Path(file.filename or "clip.mp4").name
        source_path = settings.source_dir / f"{uuid4().hex}-{safe_name}"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with source_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    out.close()
                    source_path.unlink(missing_ok=True)
                    raise ValueError("clip exceeds MAX_UPLOAD_BYTES")
                out.write(chunk)
        if size == 0:
            source_path.unlink(missing_ok=True)
            raise ValueError("clip file is empty")
        try:
            metadata = get_probe_media()(source_path)
        except ValueError as exc:
            source_path.unlink(missing_ok=True)
            raise ValueError(f"uploaded clip is not a readable video: {exc}") from exc
        source = get_store().create_source(
            "clip_upload",
            source_path,
            original_filename=safe_name,
            size_bytes=size,
            duration_sec=metadata.duration_sec,
            width=metadata.width,
            height=metadata.height,
            fps=metadata.fps,
            metadata={"standalone_clip": True},
            status="ready",
        )
        clip_path = settings.clip_dir / f"{uuid4().hex}-{safe_name}"
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, clip_path)
        name = title.strip() or Path(safe_name).stem
        plan = clip_upload.create_plan(get_store(), source, name, description.strip(), project_id)
        clip = get_store().create_clip(source["id"], clip_plan_id=int(plan["id"]), title=name, description=description.strip())
        return get_store().finish_clip_render(
            clip["id"],
            status="succeeded",
            output_path=clip_path,
            preview_path=clip_path,
            duration_sec=metadata.duration_sec,
            width=metadata.width,
            height=metadata.height,
            size_bytes=size,
        )

    return router
