"""Один цикл агента на все чаты: модель ↔ инструменты, пока модель не ответит текстом.

Раньше циклов было два: у ИИ-монтажёра (``app/ai/editor_agent.py``) и у чата с агентом
(``app/project_chat.py``), и они разъехались: повтор с ``thought_signature`` был только у монтажёра, поток
текста — только у чата. Теперь цикл здесь, а чем ходы отличаются, решает ``TurnPolicy``:

* какой системный промпт и какие объявления инструментов у этого хода;
* как выполнить вызов (карточка подтверждения, снимок истории, превью — дело политики);
* что делать, когда модель ответила текстом: сдать ответ или дожать напоминанием (проверки монтажёра).

Здесь только то, что общее у всех ходов: запрос к модели с повторами, поток текста в живой ход (``run``),
реплики между шагами, ответы инструментов и кадры для модели, лимит шагов, «Стоп».
"""

from __future__ import annotations

import base64
import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent_tools import ImageResult
# Общие кирпичи разговора с моделью: чаты берут их отсюда, а не из модуля монтажёра.
from app.ai.editor_agent_contents import (  # noqa: F401
    _history_contents as history_contents,
    _parts_text as parts_text,
    _prune_images,
    _response_object,
    _shrink_frame,
    _step_from_result as step_from_result,
    patch_thought_signatures,
    SAY,
)
from app.ai.editor_agent_review import (  # noqa: F401
    _brief_args as brief_args,
    _clean_reply as clean_reply,
    _strip_leaks,
    degenerate,
    report_like,
)

_log = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 8192
SAY_MAX_CHARS = 600
from app.ai.finish_guard import FINISH_BONUS_ROUNDS, MAX_FINISH_NUDGES  # noqa: E402


class TurnCancelled(Exception):
    """Владелец нажал «Стоп»: ход прерван, сделанное остаётся."""


class TurnBusy(RuntimeError):
    """Агент ещё отвечает на прошлое сообщение этой переписки."""


# --------------------------------------------------------------------------- #
# Модель: поток или целиком
# --------------------------------------------------------------------------- #


def _pure_text(part: dict) -> bool:
    return "text" in part and not part.get("thought") and set(part) <= {"text", "thoughtSignature"}


def _merge_part(parts: list[dict], part: dict) -> None:
    if _pure_text(part) and parts and _pure_text(parts[-1]) and "thoughtSignature" not in parts[-1]:
        parts[-1]["text"] += part.get("text") or ""
        if part.get("thoughtSignature"):
            parts[-1]["thoughtSignature"] = part["thoughtSignature"]
        return
    parts.append(dict(part))


def call_model(gemini: Any, model: str, payload: dict, run: Any = None) -> dict:
    """Ответ модели; если клиент умеет поток — текст по кусочкам в ``run``. Сбой потока до
    первого куска — повтор обычным запросом (у него ретраи и прокси)."""
    stream = getattr(gemini, "stream_generate_content", None)
    if stream is None:
        if run:
            run.set_phase("thinking")
        return gemini.generate_content(model, payload)
    parts: list[dict] = []
    feedback = None
    finish = None
    got = False
    try:
        for chunk in stream(model, payload):
            got = True
            if run and run.cancel.is_set():
                raise TurnCancelled()
            if not isinstance(chunk, dict):
                continue
            feedback = chunk.get("promptFeedback") or feedback
            candidates = chunk.get("candidates") or []
            if not candidates:
                continue
            finish = candidates[0].get("finishReason") or finish
            for part in (candidates[0].get("content") or {}).get("parts") or []:
                if not isinstance(part, dict):
                    continue
                _merge_part(parts, part)
                if run and _pure_text(part) and part.get("text"):
                    run.add_text(part["text"])
    except TurnCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        if got:
            raise
        _log.info("chat: stream failed before first chunk (%s) — plain request", exc)
        return gemini.generate_content(model, payload)
    if not parts:
        return {"candidates": [], "promptFeedback": feedback} if feedback else {"candidates": []}
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}]}


# --------------------------------------------------------------------------- #
# Чем отличаются ходы
# --------------------------------------------------------------------------- #


@dataclass
class TurnState:
    """Всё, что копится за ход: разговор с моделью и шаги для журнала."""

    contents: list[dict]
    emit: Callable[..., None]
    steps: list[dict] = field(default_factory=list)
    rounds: int = 0
    report_only: bool = False  # дальше только отчёт, без инструментов
    finish_nudges: int = 0  # сколько раз сервис не дал закончить ход (finish_guard)
    use_json_schema: bool = True

    def nudge(self, text: str) -> None:
        """Реплика сервиса модели: напоминание, что ход ещё не окончен."""
        self.contents.append({"role": "user", "parts": [{"text": text}]})


class TurnPolicy:
    """Чем один ход агента отличается от другого. Подкласс: чат с агентом и ИИ-монтажёр."""

    limit_text = "Лимит шагов исчерпан — коротко отчитайся, что сделал и что осталось."
    image_caption = "Кадр на {t} с:"
    tool_config: dict | None = None

    def prompt(self, state: TurnState) -> str:
        raise NotImplementedError

    def declarations(self, json_schema: bool) -> list[dict]:
        raise NotImplementedError

    def run_call(self, state: TurnState, name: str, args: dict) -> tuple[Any, bool]:
        """Выполнить вызов: шаг в журнал и результат для модели."""
        raise NotImplementedError

    def after_call(self, state: TurnState, name: str, result: Any, ok: bool, body: dict) -> None:
        """После вызова, когда ответ для модели (``body``) уже собран."""

    def after_batch(self, state: TurnState, images: list[dict]) -> None:
        """После всех вызовов раунда, до ответа модели."""

    def on_answer(self, state: TurnState, parts: list[dict], calls: list[dict], tools_on: bool,
                  candidate: dict) -> str | None:
        """Модель ответила текстом. Текст — конец хода. ``None`` — политика дожала модель, ход продолжается."""
        return clean_reply(parts_text(parts))

    def on_blocked(self, state: TurnState, reason: Any) -> str:
        """Фильтр контента Gemini: текст ответа или исключение."""
        raise NotImplementedError

    def finish_guard(self, state: TurnState, answer: str) -> str | None:
        """Модель хочет закончить ход ответом ``answer``. Реплика сервиса — ход ещё не окончен (не больше
        ``MAX_FINISH_NUDGES`` раз за ход, с добавкой раундов на вызовы), None — можно сдавать."""
        return None

    def finish(self, state: TurnState, text: str) -> str:
        """Ход окончен: последняя правка ответа."""
        return text


# --------------------------------------------------------------------------- #
# Цикл
# --------------------------------------------------------------------------- #


def _request(policy: TurnPolicy, state: TurnState, tools_on: bool) -> dict:
    payload: dict = {
        "systemInstruction": {"parts": [{"text": policy.prompt(state)}]},
        "contents": state.contents,
        # Температуру не трогаем: Gemini 3 рассчитана на 1.0, ниже — зацикливается.
        # Потолок ответа: иногда модель «плывёт» в бесконечный повтор («- - - …»).
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
    }
    if tools_on:
        payload["tools"] = [{"functionDeclarations": policy.declarations(state.use_json_schema)}]
        if policy.tool_config:
            payload["toolConfig"] = policy.tool_config
    return payload


def _ask(policy: TurnPolicy, gemini: Any, model: str, state: TurnState, tools_on: bool, run: Any) -> dict:
    """Запрос к модели. Две ошибки лечатся повтором: старый эндпоинт не знает parametersJsonSchema, а вызову функции
    без подписи (QA 9, клип 902: ход упал на 4-м шаге — «Function call is missing a thought_signature») ставим заглушку."""
    payload = _request(policy, state, tools_on)

    def once() -> dict:
        # Живого хода нет (монтаж из автопилота, тесты) — потоку некуда писать: обычный запрос с ретраями.
        return call_model(gemini, model, payload, run) if run is not None else gemini.generate_content(model, payload)

    for _attempt in range(2):
        try:
            return once()
        except RuntimeError as exc:
            if tools_on and state.use_json_schema and "parametersJsonSchema" in str(exc):
                state.use_json_schema = False
                payload["tools"] = [{"functionDeclarations": policy.declarations(False)}]
            elif "thought_signature" in str(exc) and patch_thought_signatures(state.contents):
                pass
            else:
                raise
    return once()


def _say_step(parts: list[dict]) -> dict | None:
    """Пояснение модели между вызовами («Ставлю хук и наезд») — тоже видно в чате. Мусор («.», не по-русски,
    служебное) владельцу не показываем."""
    said = _strip_leaks(parts_text(parts))
    if not said or degenerate(said) or not report_like(said):
        return None
    return {"tool": SAY, "label": said[:SAY_MAX_CHARS], "ok": True, "kind": "say"}


def _tool_response(policy: TurnPolicy, state: TurnState, part: dict, images: list[dict]) -> dict:
    call = part["functionCall"]
    name = str(call.get("name") or "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    result, ok = policy.run_call(state, name, args)
    body = _response_object(result)
    policy.after_call(state, name, result, ok, body)
    if isinstance(result, ImageResult):
        data, mime = _shrink_frame(result)
        body["note"] = "кадр приложен следующей картинкой"
        images.append({"text": policy.image_caption.format(t=result.info.get("t"))})
        images.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})
    response_part: dict = {"functionResponse": {"name": name, "response": body}}
    if call.get("id"):
        response_part["functionResponse"]["id"] = call["id"]
    return response_part


def run_loop(policy: TurnPolicy, gemini: Any, model: str, contents: list[dict], *, limit: int,
             should_cancel: Callable[[], bool] | None = None, on_step: Callable[[list[dict]], None] | None = None,
             run: Any = None) -> tuple[str, TurnState]:
    """Один ход. Возвращает текст ответа и состояние (шаги, сколько раз спросили модель).

    ``run`` — живой ход чата (``project_chat_threads._Run``): поток текста, фаза, шаги для SSE и «Стоп».
    ``on_step`` — сохранить шаги: ход виден и после перезагрузки страницы. Отмена — ``TurnCancelled``."""

    def cancelled() -> bool:
        return bool((should_cancel and should_cancel()) or (run is not None and run.cancel.is_set()))

    def emit(phase: str | None = None) -> None:
        if run is not None:
            run.set_steps(state.steps, phase)
        if on_step:
            on_step(copy.deepcopy(state.steps))

    state = TurnState(contents=contents, emit=emit)
    while True:
        if cancelled():
            raise TurnCancelled()
        tools_on = state.rounds < limit and not state.report_only
        if state.rounds >= limit and not state.report_only:
            state.nudge(policy.limit_text)
        response = _ask(policy, gemini, model, state, tools_on, run)
        state.rounds += 1
        candidates = response.get("candidates") if isinstance(response, dict) else None
        if not candidates:
            feedback = (response or {}).get("promptFeedback") if isinstance(response, dict) else None
            blocked = (feedback or {}).get("blockReason")
            if blocked:
                # Фильтр Gemini (мат, кадры с персонажем) срабатывает и посреди работы.
                return policy.finish(state, policy.on_blocked(state, blocked)), state
            raise RuntimeError(f"модель не ответила{': ' + json.dumps(feedback, ensure_ascii=False) if feedback else ''}")
        candidate = candidates[0]
        parts = [p for p in (candidate.get("content") or {}).get("parts") or [] if isinstance(p, dict)]
        calls = [p for p in parts if isinstance(p.get("functionCall"), dict)]
        if parts:
            contents.append({"role": "model", "parts": parts})
        if not calls or not tools_on:
            answer = policy.on_answer(state, parts, calls, tools_on, candidate)
            if answer is not None and tools_on and not state.report_only and state.finish_nudges < MAX_FINISH_NUDGES:
                # Проверка конца хода на уровне цикла (повтор эксперимента 13.11: «осталось: render_clip» вместо рендера).
                guard = policy.finish_guard(state, answer)
                if guard:
                    state.finish_nudges += 1
                    state.nudge(guard)
                    limit += FINISH_BONUS_ROUNDS
                    if run is not None:
                        run.take_text()
                    continue
            if answer is not None:
                return policy.finish(state, answer), state
            if run is not None:
                run.take_text()  # напечатанное в этом раунде — не ответ: ход продолжается
            continue
        if run is not None:
            run.take_text()  # текст раунда с вызовами — реплика между шагами, а не ответ
        say = _say_step(parts)
        if say:
            state.steps.append(say)
            emit()
        responses: list[dict] = []
        images: list[dict] = []
        for part in calls:
            if cancelled():
                raise TurnCancelled()
            responses.append(_tool_response(policy, state, part, images))
        policy.after_batch(state, images)
        contents.append({"role": "user", "parts": responses + images})
        _prune_images(contents)
