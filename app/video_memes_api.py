"""Ручки «Видеомемов» (app/video_memes.py): список и поиск, загрузка файла и по ссылке, правка, одобрение,
повторная расшифровка, удаление. Файл мема отдаёт ``/media/montage-assets/{asset_id}`` (это «Файл для монтажа»).

Роутер собирается функцией, как ``app/montage_assets_api.py``."""

# Без from __future__ import annotations: FastAPI читает аннотации вида _auth: auth_dep при объявлении.
from pathlib import Path
from typing import Annotated, Any, Callable
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from app import video_memes as VM
from app.api_common import _payload_data

MAX_MEME_UPLOAD_BYTES = 200 * 1024 * 1024


class MemeFromUrlRequest(BaseModel):
    url: str = Field(max_length=2000)
    start: float | None = Field(default=None, ge=0, description="секунда ролика — начало (нет — 0)")
    end: float | None = Field(default=None, gt=0, description="конец (нет — start + 8 с)")
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    when_to_use: str = Field(default="", max_length=1000)
    tags: list[str] | str = Field(default_factory=list)
    has_real_people: bool = True
    license_note: str = Field(default="", max_length=300)
    auto_trim: bool = True


class MemePatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    when_to_use: str | None = Field(default=None, max_length=1000)
    tags: list[str] | str | None = None
    transcript: str | None = Field(default=None, max_length=4000)
    transcript_lang: str | None = Field(default=None, max_length=16)
    has_real_people: bool | None = None
    source_url: str | None = Field(default=None, max_length=2000)
    license_note: str | None = Field(default=None, max_length=300)
    approved: bool | None = None


class MemeEditRequest(BaseModel):
    """Мем-эдит записи (app/meme_edit.py): два клипа 9:16 и 16:9 из одного монтажа."""
    music_track_id: int | None = Field(default=None, description="трек фона (list_music); нет — ближе к 128 BPM")
    music_asset_id: int | None = Field(default=None, description="или звук из «Файлов для монтажа»")
    target_sec: float = Field(default=40.0, ge=15, le=90)
    aspects: list[str] = Field(default_factory=lambda: ["9:16", "16:9"])
    events: list[dict] | None = Field(default=None, description="свои события [{t, kind, score?}] (секунды исходника)")
    seed: int = 0
    words: int = Field(default=3, ge=0, le=4)
    subtitles: bool = True
    render: bool = True
    dry_run: bool = Field(default=False, description="только план (файл клипа), без клипов")


def _public(meme: dict) -> dict:
    return {k: v for k, v in meme.items() if k != "file_path"}


def build_video_memes_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/video-memes", tags=["Video memes"], summary="Видеомемы: список и поиск по смыслу")
    def api_list_video_memes(
        _auth: auth_dep,
        q: Annotated[str, Query(max_length=200, description="слова: что значит, когда, что сказано, теги")] = "",
        tag: Annotated[str | None, Query(max_length=32)] = None,
        approved: Annotated[bool | None, Query(description="true — только одобренные, false — ждут одобрения")] = None,
        max_sec: Annotated[float | None, Query(gt=0)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> dict:
        memes = VM.search_memes(get_store(), q, tag=tag, approved=approved, max_sec=max_sec, limit=min(limit, 50)) \
            if (q.strip() or tag or max_sec) else VM.list_memes(get_store(), approved=approved)[:limit]
        return {"memes": [_public(m) for m in memes], "suggested_tags": list(VM.SUGGESTED_TAGS)}

    @router.post("/api/video-memes", tags=["Video memes"], summary="Загрузить видеомем (подрезка, расшифровка в фоне)")
    async def api_upload_video_meme(
        _auth: auth_dep,
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
        when_to_use: Annotated[str, Form()] = "",
        tags: Annotated[str, Form(description="через запятую")] = "",
        has_real_people: Annotated[bool, Form()] = True,
        source_url: Annotated[str, Form()] = "",
        license_note: Annotated[str, Form()] = "",
        trim_start: Annotated[float | None, Form()] = None,
        trim_end: Annotated[float | None, Form()] = None,
        auto_trim: Annotated[bool, Form(description="срезать тишину и чёрное по краям")] = True,
    ) -> dict:
        from app.settings import settings
        from app.video_meme_ingest import create_from_file

        safe_name = Path(file.filename or "meme.mp4").name
        work = settings.tmp_dir / f"meme-upload-{uuid4().hex}{Path(safe_name).suffix[:8]}"
        work.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with work.open("wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_MEME_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="файл больше 200 МБ — для мема многовато")
                    out.write(chunk)
            await file.close()
            meme = create_from_file(
                get_store(), work, original_name=safe_name, title=title, trim_start=trim_start, trim_end=trim_end,
                auto_trim=auto_trim, description=description, when_to_use=when_to_use, tags=tags,
                has_real_people=has_real_people, source_url=source_url, license_note=license_note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            work.unlink(missing_ok=True)
        return _public(meme)

    @router.post("/api/video-memes/from-url", tags=["Video memes"],
                 summary="Видеомем по ссылке (yt-dlp сервиса и его прокси; отрезок start..end)")
    def api_video_meme_from_url(payload: MemeFromUrlRequest, _auth: auth_dep) -> dict:
        from app.video_meme_ingest import create_from_url

        try:
            meme = create_from_url(
                get_store(), payload.url, start=payload.start, end=payload.end, title=payload.title,
                auto_trim=payload.auto_trim, description=payload.description, when_to_use=payload.when_to_use,
                tags=payload.tags, has_real_people=payload.has_real_people, license_note=payload.license_note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - сеть
            raise HTTPException(status_code=502, detail=f"не удалось скачать: {str(exc)[:200]}") from exc
        return _public(meme)

    @router.get("/api/video-memes/{meme_id}", tags=["Video memes"], summary="Один видеомем (со словами расшифровки)")
    def api_get_video_meme(meme_id: int, _auth: auth_dep) -> dict:
        try:
            return _public(VM.get_meme(get_store(), meme_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/api/video-memes/{meme_id}", tags=["Video memes"],
                  summary="Описание, когда вставлять, теги, расшифровка, люди, лицензия, одобрение")
    def api_patch_video_meme(meme_id: int, payload: MemePatch, _auth: auth_dep) -> dict:
        try:
            return _public(VM.update_meme(get_store(), meme_id, **_payload_data(payload, exclude_none=True)))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/video-memes/{meme_id}/transcribe", tags=["Video memes"],
                 summary="Расшифровать заново (Whisper в фоне)")
    def api_retranscribe_video_meme(meme_id: int, _auth: auth_dep) -> dict:
        from app.video_meme_ingest import start_transcript

        try:
            VM.get_meme(get_store(), meme_id)
            start_transcript(get_store(), meme_id)
            return _public(VM.get_meme(get_store(), meme_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/api/video-memes/{meme_id}", tags=["Video memes"], summary="Удалить видеомем вместе с файлом")
    def api_delete_video_meme(meme_id: int, _auth: auth_dep) -> dict:
        try:
            VM.delete_meme(get_store(), meme_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @router.post("/api/sources/{source_id}/meme-edit", tags=["Video memes"],
                 summary="Мем-эдит игры: геймплей по долям музыки + врезки видеомемов, два клипа 9:16 и 16:9")
    def api_meme_edit(source_id: int, payload: MemeEditRequest, request: Request, _auth: auth_dep) -> dict:
        from app.meme_edit import build_meme_edit, plan_meme_edit
        from app.stream_questions_api import refuse_agent_montage

        refuse_agent_montage(request, get_store(), source_id)
        options = dict(music_track_id=payload.music_track_id, music_asset_id=payload.music_asset_id,
                       target_sec=payload.target_sec, events=payload.events, seed=payload.seed,
                       words=payload.words, subtitles=payload.subtitles)
        try:
            if payload.dry_run:
                return {"dry_run": True,
                        "spec": plan_meme_edit(get_store(), source_id, aspect=(payload.aspects or ["9:16"])[0],
                                               **options)}
            return build_meme_edit(get_store(), source_id, aspects=payload.aspects, render=payload.render, **options)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
