"""Субтитры клипа и всё, что от них зависит: распознавание, перевод, озвучка, цензура, эмфаза, голоса и их банк.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.clip_history import snapshot as snapshot_clip
from app.clip_subtitles import generate_for_plan as generate_plan_subtitles, save_lines as save_plan_subtitle_lines


class PlanSubtitlesRequest(BaseModel):
    provider: str | None = Field(default=None, description="whisper | gemini — иначе как в стиле клипа")
    model: str | None = None


class PlanSubtitleLine(BaseModel):
    start: float
    end: float
    text: str = Field(max_length=300)


class PlanSubtitleWord(BaseModel):
    word: str = Field(max_length=80)
    start: float
    end: float
    speaker: str | None = Field(default=None, max_length=1)
    emph: int | None = Field(default=None, ge=0, le=2, description="эмфаза слова: 1 pop, 2 shake")


class PlanSubtitleLines(BaseModel):
    lines: list[PlanSubtitleLine]
    words: list[PlanSubtitleWord] | None = Field(default=None, description="пословные тайминги, если распознавали снаружи")


class TranslateSubtitlesRequest(BaseModel):
    mode: Literal["ru", "both", "off"] = Field(default="ru", description="ru — только русский, both — русский + "
                                                                         "оригинал вторым слоем, off — вернуть оригинал")
    retranslate: bool = Field(default=False, description="перевести заново (иначе — только смена режима)")
    force: bool = Field(default=False, description="переводить, даже если whisper считает речь русской")
    translations: list[str] | None = Field(default=None, description="готовый перевод по фразам (без Gemini)")


class DubRequest(BaseModel):
    provider: Literal["polza", "gemini", "none"] = Field(
        default="polza", description="polza — платный TTS (оценка, потом confirm); gemini — Gemini TTS бесплатно "
                                     "(синтез сразу); none — сухой прогон тоном (бесплатно)")
    voice: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=80)
    style: str | None = Field(default=None, max_length=300, description="gemini: как говорить («говори весело, "
                                                                         "быстро»)")
    confirm: bool = Field(default=False, description="без него — только оценка стоимости")
    budget_rub: float | None = Field(default=None, ge=0, description="потолок этого запуска, ₽ (по умолчанию 200)")
    stretch: bool = Field(default=False, description="замедлять короткие фразы до ×0.8 вместо тишины")
    remove: bool = Field(default=False, description="снять слои озвучки")


class TtsPreviewRequest(BaseModel):
    provider: Literal["gemini", "polza", "none"] = Field(default="gemini", description="gemini — бесплатно")
    voice: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=80)
    style: str | None = Field(default=None, max_length=300, description="как говорить («говори весело, быстро»)")
    text: str | None = Field(default=None, max_length=300, description="фраза пробы (по умолчанию приветствие)")
    confirm: bool = Field(default=False, description="платный провайдер: без него — только оценка")


class CensorRequest(BaseModel):
    verify: bool = Field(default=True, description="рендер + Whisper по готовому файлу, шире там, где мат узнаётся")
    max_rounds: int | None = Field(default=None, ge=1, le=5, description="кругов рендер→Whisper (по умолчанию 3)")
    levels: int | dict[str, int] | None = Field(default=None, description="уровень 1–3 всем или {номер слова: уровень}")
    soft: bool = Field(default=False, description="ещё и мягкое: блин, хрен, жопа, говно")
    stars: bool = Field(default=True, description="мат в субтитрах — первая буква и звёздочки")
    skip: list[int] = Field(default_factory=list, description="номера слов субтитров, которые не трогать")
    clear: bool = Field(default=False, description="снять отрезки цензуры (ручные mute остаются)")
    track: int | str | None = Field(default=None, description='глушить только дорожку: номер или "voice" '
                                                              "(дорожка голоса записи, если известна)")
    style: Literal["silence", "beep", "noise", "reverse", "lowpass", "stretch", "duck"] | None = Field(
        default=None, description="чем заполнить заглушённую середину слова; не задан — прежний стиль клипа, "
                                  "у нового — reverse")


class WordEmphasisRequest(BaseModel):
    indices: list[int] | None = Field(default=None, description="номера слов в субтитрах клипа (порядок words)")
    words: list[str] | None = Field(default=None, description="тексты слов (первое вхождение)")
    style: Literal["pop", "shake", "off"] = "pop"
    auto: bool = Field(default=False, description="одно ключевое слово на строку само")
    pick: Literal["heuristic", "gemini"] | None = None
    clear: bool = False


class SeparateVoicesRequest(BaseModel):
    retranscribe: bool = Field(default=True, description="сначала распознать речь точной моделью (large-v3-turbo)")


class AssignVoiceRequest(BaseModel):
    start: float
    end: float
    speaker: str = Field(pattern="^[A-D]$")
    description: str | None = Field(default=None, max_length=60)


class IdentifyVoicesRequest(BaseModel):
    labels: bool = True
    reassign: bool = False


class RegisterVoiceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    speaker: str | None = Field(default=None, pattern="^[A-D]$")
    start: float | None = None
    end: float | None = None
    kind: str = Field(default="person", pattern="^(person|bot)$")


def build_subtitles_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/clip-plans/{clip_plan_id}/subtitles",
        tags=["Subtitles"],
        summary="Субтитры клипа (строки для правки до рендера)",
    )
    def api_get_plan_subtitles(clip_plan_id: int, _auth: auth_dep) -> dict:
        try:
            payload = get_store().get_clip_plan_subtitles(clip_plan_id) or {"lines": [], "words": []}
            if payload.get("words"):
                from app.cuts import plan_joins_setting

                from app.piece_speed import drop_reversed, is_reversed

                plan = get_store().get_clip_plan(clip_plan_id)
                joins = plan_joins_setting(plan)
                segments = plan.get("segments") or []
                if joins or any(is_reversed(s) for s in segments):
                    # Превью субтитров редактора («куски подряд»): слова так, как их слышно — с J/L,
                    # без развёрнутых кусков (как в рендере).
                    from app.audio_edit import heard_subtitles

                    heard = heard_subtitles(payload, segments, segments, joins) if joins else payload
                    payload = {**payload, "heard_words": drop_reversed(heard, segments)["words"]}
            return payload
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/subtitles",
        tags=["Subtitles"],
        summary="Распознать речь клипа и сохранить строки (ДО рендера)",
    )
    def api_generate_plan_subtitles(
        clip_plan_id: int, _auth: auth_dep, payload: PlanSubtitlesRequest | None = None
    ) -> dict:
        try:
            return generate_plan_subtitles(
                get_store(), clip_plan_id, provider=payload.provider if payload else None, model=payload.model if payload else None
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put(
        "/api/clip-plans/{clip_plan_id}/subtitles",
        tags=["Subtitles"],
        summary="Сохранить правку субтитров (текст и тайминги строк)",
    )
    def api_put_plan_subtitles(clip_plan_id: int, payload: PlanSubtitleLines, _auth: auth_dep) -> dict:
        try:
            snapshot_clip(get_store(), clip_plan_id, "правка субтитров")
            return save_plan_subtitle_lines(
                get_store(),
                clip_plan_id,
                [line.model_dump() for line in payload.lines],
                [w.model_dump() for w in payload.words] if payload.words else None,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/subtitles/translate",
        tags=["Subtitles"],
        summary="Перевести субтитры иноязычного клипа на русский (Gemini; оригинал — вторым слоем по желанию)",
    )
    def api_translate_plan_subtitles(clip_plan_id: int, _auth: auth_dep, payload: TranslateSubtitlesRequest | None = None) -> dict:
        from app.translation import translate_subtitles

        body = payload or TranslateSubtitlesRequest()
        try:
            snapshot_clip(get_store(), clip_plan_id, "перевод субтитров")
            result = translate_subtitles(get_store(), clip_plan_id, mode=body.mode, translations=body.translations,
                                         force=body.force, retranslate=body.retranslate)
            return {**result, "subtitles": get_store().get_clip_plan_subtitles(clip_plan_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/dub",
        tags=["Subtitles"],
        summary="🔊 Озвучка RU переведённого клипа: Gemini — бесплатно сразу; Polza — оценка, синтез с confirm + бюджет",
    )
    def api_dub_clip(clip_plan_id: int, _auth: auth_dep, payload: DubRequest | None = None) -> dict:
        from app.dubbing import DubError, dub_clip, provider_paid

        body = payload or DubRequest()
        try:
            if body.confirm or body.remove or not provider_paid(body.provider):
                snapshot_clip(get_store(), clip_plan_id, "озвучка RU")
            return dub_clip(get_store(), clip_plan_id, provider=body.provider, voice=body.voice, model=body.model,
                            confirm=body.confirm, budget_rub=body.budget_rub, stretch=body.stretch, remove=body.remove,
                            style=body.style)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DubError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/tts/voices", tags=["Subtitles"], summary="🔊 Провайдеры TTS, модели и голоса (gemini — бесплатно)")
    def api_tts_voices(_auth: auth_dep) -> dict:
        from app.dubbing import voice_catalog

        return voice_catalog()

    @router.post("/api/tts/preview", tags=["Subtitles"],
              summary="🔊 Проба голоса: короткая фраза → mp3 в библиотеке (url); gemini бесплатно, платное — confirm")
    def api_tts_preview(_auth: auth_dep, payload: TtsPreviewRequest | None = None) -> dict:
        from app.dubbing import DubError, tts_preview

        body = payload or TtsPreviewRequest()
        try:
            return tts_preview(get_store(), provider=body.provider, voice=body.voice, model=body.model, style=body.style,
                               text=body.text, confirm=body.confirm)
        except DubError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/censor",
        tags=["Subtitles"],
        summary="🤬 Заглушить мат серединой слова, звёздочки в субтитрах, проверка Whisper по рендеру",
    )
    def api_clip_plan_censor(
        clip_plan_id: int, _auth: auth_dep, response: Response, payload: CensorRequest | None = None
    ) -> dict:
        """Тот же инструмент, что у агентов (censor_profanity, app/censor.py). С ``verify`` —
        фоном: запрос ждёт не дольше ~45 с, иначе 202 и ``{status: running, retry_after_sec}`` —
        повтори тот же запрос. Без ``verify`` — сразу (только mute и звёздочки)."""
        from app import agent_tools

        body = payload or CensorRequest()
        try:
            get_store().get_clip_plan(clip_plan_id, include_segments=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ctx = agent_tools.ToolContext(
            store=get_store(), clip_plan_id=clip_plan_id,
            on_first_change=lambda: snapshot_clip(get_store(), clip_plan_id, "цензура мата"),
        )
        args = body.model_dump(exclude_none=True)
        if any(args.get(k) not in (None, "", [], False) for k in ("levels", "level", "style", "groups", "soft", "skip", "track")):
            # Владелец в редакторе явно меняет цензуру — это не повторный вызов агента («уже применено»).
            args["redo"] = True
        result, ok = agent_tools.run_tool(ctx, "censor_profanity", args)
        if not ok:
            problems = "; ".join(str(p) for p in result.get("problems") or [])
            raise HTTPException(status_code=400, detail=str(result.get("error") or "цензура не удалась")
                                + (f": {problems}" if problems else ""))
        if result.get("status") == "running":
            response.status_code = 202
            response.headers["Retry-After"] = str(result.get("retry_after_sec") or 30)
        return result

    @router.post(
        "/api/clip-plans/{clip_plan_id}/subtitles/emphasis",
        tags=["Subtitles"],
        summary="Эмфаза слов субтитров: крупнее + pop (shake — со встряской), off — снять",
    )
    def api_clip_plan_word_emphasis(clip_plan_id: int, payload: WordEmphasisRequest, _auth: auth_dep) -> dict:
        """Тот же инструмент, что у агентов (emphasize_words, app/word_emphasis.py); шаг отмены — один."""
        from app import agent_tools

        try:
            get_store().get_clip_plan(clip_plan_id, include_segments=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ctx = agent_tools.ToolContext(
            store=get_store(), clip_plan_id=clip_plan_id,
            on_first_change=lambda: snapshot_clip(get_store(), clip_plan_id, "эмфаза слов"),
        )
        result, ok = agent_tools.run_tool(ctx, "emphasize_words", payload.model_dump(exclude_none=True))
        if not ok:
            raise HTTPException(status_code=400, detail=str(result.get("error") or "эмфаза не принята"))
        return {**result, "subtitles": get_store().get_clip_plan_subtitles(clip_plan_id)}

    @router.post(
        "/api/clip-plans/{clip_plan_id}/voices",
        tags=["Subtitles"],
        summary="Голоса: Gemini расшифровывает каждый голос отдельно, слова субтитров получают метку говорящего",
    )
    def api_separate_voices(clip_plan_id: int, _auth: auth_dep, payload: SeparateVoicesRequest | None = None) -> dict:
        from app.ai.speakers import separate_voices

        try:
            snapshot_clip(get_store(), clip_plan_id, "голоса")
            result = separate_voices(
                get_store(), clip_plan_id, force=True, retranscribe=payload.retranscribe if payload else True
            )
            return {**result, "subtitles": get_store().get_clip_plan_subtitles(clip_plan_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/voices/assign",
        tags=["Subtitles"],
        summary="Голоса: отдать слова отрезка [start, end) другому голосу (поправить модель руками)",
    )
    def api_assign_voice(clip_plan_id: int, payload: AssignVoiceRequest, _auth: auth_dep) -> dict:
        from app.ai.speakers import assign_voice

        try:
            snapshot_clip(get_store(), clip_plan_id, "голос строки")
            return assign_voice(get_store(), clip_plan_id, payload.start, payload.end, payload.speaker, payload.description)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/voices/identify",
        tags=["Subtitles"],
        summary="Голоса: кто есть кто по банку голосов канала (имена только при уверенном совпадении)",
    )
    def api_identify_voices(clip_plan_id: int, _auth: auth_dep, payload: IdentifyVoicesRequest | None = None) -> dict:
        from app.voice_bank import identify_voices

        body = payload or IdentifyVoicesRequest()
        try:
            snapshot_clip(get_store(), clip_plan_id, "имена голосов")
            return identify_voices(get_store(), clip_plan_id, labels=body.labels, reassign=body.reassign)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/voices/register",
        tags=["Subtitles"],
        summary="Голоса: запомнить в банке канала голос этого клипа (буква или отрезок)",
    )
    def api_register_voice(clip_plan_id: int, payload: RegisterVoiceRequest, _auth: auth_dep) -> dict:
        from app.voice_bank import register_voice

        try:
            return register_voice(get_store(), payload.name, clip_plan_id=clip_plan_id, speaker=payload.speaker,
                                  start=payload.start, end=payload.end, kind=payload.kind)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/voice-bank", tags=["Subtitles"], summary="Банк голосов: канал (project_id) и общие")
    def api_voice_bank(_auth: auth_dep, project_id: int | None = None) -> dict:
        from app.voice_bank import list_voices

        return {"voices": list_voices(get_store(), project_id)}

    @router.delete("/api/voice-bank/{voice_id}", tags=["Subtitles"], summary="Банк голосов: убрать голос")
    def api_voice_bank_delete(voice_id: int, _auth: auth_dep) -> dict:
        from app.voice_bank import delete_voice

        try:
            delete_voice(get_store(), voice_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    return router
