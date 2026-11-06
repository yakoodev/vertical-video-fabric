"""💬 ИИ-монтажёр в редакторе: чат, который сам правит клип инструментами.

Владелец пишет «сделай динамичнее», «подпиши цифры», «добавь мем на реакцию» —
агент (Gemini с function calling) смотрит клип, ставит титры/эмодзи/наезды через
инструменты ``app/agent_tools.py``, смотрит получившийся кадр (``view_frame``) и
правит, пока не станет хорошо. В конце — короткий отчёт по-русски.

Почему так устроено:

* **Инструменты общие с MCP** — внешние агенты и встроенный чат правят клип одними
  и теми же ручками с одной проверкой (``app.motion``), поэтому не разъезжаются.
* **Один снимок истории на ход агента** («ИИ: <запрос>»), сделанный перед первой
  правкой: Ctrl+Z в редакторе откатывает ответ агента целиком, а вопрос без правок
  историю не засоряет.
* **Фоновый поток + опрос.** Ход агента — это десятки секунд и до ~25 вызовов
  модели. Так же, как ИИ-монтаж (``app/ai/montage.py``), работа идёт в потоке, а
  редактор раз в секунду читает ``GET /api/clip-plans/{id}/agent``: шаги пишутся в
  базу по мере работы, так что их видно живьём и после перезагрузки страницы.
* **Разговор хранится** в ``clip_agent_messages``: следующий вопрос модель
  получает вместе с прошлой перепиской (текстом — без старых картинок и вызовов).

Где что лежит. Здесь — ход агента ``run_agent`` и его проверки перед сдачей (``ClipPolicy``), запуск и вид чата: они читают ``client_factory``, который подменяют тесты.
Сам цикл «модель ↔ инструменты» общий с чатом: ``app/ai/agent_loop.py``. Вынесено: промпт и плейбуки
``app/ai/editor_agent_prompts.py``, разговор с моделью ``app/ai/editor_agent_contents.py``, проверки перед сдачей и чистка ответа
``app/ai/editor_agent_review.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable

from app import agent_tools
from app.agent_tools import ToolContext
from app.ai.agent_loop import MAX_OUTPUT_TOKENS, TurnCancelled, TurnPolicy, TurnState, run_loop  # noqa: F401
from app.settings import settings

from app.ai.editor_agent_prompts import (  # noqa: F401
    CRAFT, EXAMPLES, owner_settings_note, system_prompt, PLAYBOOK_MAX_CHARS, playbook_block, active_playbook,
    FORMAT_LAYOUT, format_craft, clip_formats,
)
from app.ai.editor_agent_contents import (  # noqa: F401
    SAY, HISTORY_MESSAGES, KEEP_IMAGES, FRAME_MAX_HEIGHT, MAX_RESULT_CHARS, _UNSUPPORTED_SCHEMA_KEYS,
    function_declarations, _openapi_schema, _shrink_frame, _response_object, SKIP_SIGNATURE,
    patch_thought_signatures, _prune_images, _step_from_result, _parts_text, _short_request, _history_contents,
)
from app.ai.editor_agent_review import (  # noqa: F401
    GAP_MARKERS, VISUAL_TOOLS, LOOK_TOOLS, framing_stop_nudge, FRAMING_QUESTION, _FRAMING_ASK_RE, framing_question,
    _is_mutating, unchecked_visual, look_nudge, AVATAR_TOOLS, MAX_AUTO_AVATAR_FRAMES, unviewed_avatar_places,
    avatar_look_nudge, _auto_avatar_look, _auto_look, clip_check, _NOT_ACCENT_RE, MAX_ACCENT_FACTS, report_facts,
    _SENTENCE_SPLIT_RE, _SOUND_WORDS_RE, _MUTE_WORDS_RE, _REAL_WORDS_RE, _TOKEN_RE, _FACE_CUT_RE, _FACE_OK_RE,
    _mentions, report_fact_gaps, facts_nudge, _LINT_CLAIM_RE, _GIF_CLAIM_RE, honest_report, unresolved_failure,
    fix_nudge, _current_gaps, report_like, _REPEAT_RE, degenerate, _ECHO_RE, _MARKER_RE, _LEAK_PREFIX_RE,
    _ECHO_MARKS, _ECHO_END, strip_service_echo, _REPORT_START_RE, _PLANNING_RE, strip_ru_monologue, _strip_leaks,
    _CYR_RE, _SENTENCE_END_RE, MONOLOGUE_MIN_LETTERS, REPORT_MIN_CYRILLIC, strip_monologue, _clean_reply,
    _brief_args, _fallback_summary,
)

_log = logging.getLogger(__name__)


# Откуда брать клиента Gemini, если его не передали явно (тесты подставляют фейк).
client_factory: Callable[[], Any] | None = None


class AgentBusy(RuntimeError):
    """У клипа уже идёт ход агента."""


AgentCancelled = TurnCancelled  # прежнее имя: цикл теперь общий с чатом (app/ai/agent_loop.py)


# --------------------------------------------------------------------------- #
# Цикл агента
# --------------------------------------------------------------------------- #


class ClipPolicy(TurnPolicy):
    """Ход ИИ-монтажёра: один клип, все клиповые инструменты, проверки перед сдачей.

    ``hooks`` — чат переписки клипа (``app/project_chat.py``): карточки подтверждения и превью в шагах.
    Без них (ИИ-монтаж, автопилот, тесты) — голый монтажёр, как и был."""

    limit_text = ("Лимит шагов исчерпан. Больше инструментов не будет — коротко отчитайся владельцу, "
                  "что успел сделать и что осталось.")
    image_caption = "Кадр клипа на {t} с:"
    tool_config = {"functionCallingConfig": {"mode": "AUTO"}}

    def __init__(self, store: Any, clip_plan_id: int, message: str, ctx: ToolContext, hooks: Any = None) -> None:
        self.store = store
        self.clip_plan_id = clip_plan_id
        self.message = message
        self.ctx = ctx
        self.hooks = hooks
        self.nudged = False
        self.look_nudges = 0
        self.auto_looked = False
        self.avatar_nudges = 0
        self.auto_avatar_looked = False
        self.nudged_continue = False
        self.nudged_report = False
        self.nudged_facts = False
        self.nudged_work = False
        self.nudged_fix = False
        self.asked_report = False
        self.framing_told = False
        self.silent_nudges = 0
        self.gap_warnings: list[str] = []  # «пустой кадр»/«нет финала» из последнего ответа линта
        self.last_change_body: dict | None = None

    def prompt(self, state: TurnState) -> str:
        # Формат читаем на каждый вызов: агент мог сменить его set_format (по просьбе владельца).
        fmt, layers_fmt, has_layers = clip_formats(self.store, self.clip_plan_id)
        prompt = system_prompt(fmt, layers_fmt, has_layers=has_layers, playbook=active_playbook(self.message, state.steps))
        prompt += owner_settings_note(self.store, self.clip_plan_id)
        if self.hooks:
            prompt += self.hooks.prompt_note()
        return prompt

    def declarations(self, json_schema: bool) -> list[dict]:
        return function_declarations(json_schema)

    def run_call(self, state: TurnState, name: str, args: dict) -> tuple[Any, bool]:
        try:
            label = agent_tools.get_tool(name).step_label(args)
        except KeyError:
            label = f"Неизвестный инструмент {name}"
        step = {"tool": name, "label": label, "ok": None, "args": _brief_args(args), "kind": "tool"}
        self.ctx.playbook = active_playbook(self.message, state.steps)  # инструменты (auto_edit) сверяются с плейбуком
        state.steps.append(step)
        state.emit("tool")
        if name in {t.name for t in agent_tools._project_tools()}:
            # Лор и правила проекта монтажёру не объявлены: название из головы модели не должно их открыть.
            result: Any = {"ok": False, "error": f"в чате клипа нет инструмента {name}: это в чате проекта"}
            ok = False
            _step_from_result(step, result, ok)
        else:
            held = self.hooks.hold(step, name, args) if self.hooks else None
            if held is not None:
                result, ok = held, False  # ждёт кнопки владельца: шаг не ✗, а карточка
            else:
                result, ok = agent_tools.run_tool(self.ctx, name, args)
                _step_from_result(step, result, ok)
                if self.hooks:
                    self.hooks.decorate(step, name, result, ok)
        if ok and name in LINT_TOOLS and isinstance(result, dict):
            self.gap_warnings = [str(w) for w in result.get("warnings") or [] if str(w).startswith(GAP_MARKERS)]
        state.emit()
        return result, ok

    def after_call(self, state: TurnState, name: str, result: Any, ok: bool, body: dict) -> None:
        if ok and _is_mutating(name):
            self.last_change_body = body
            try:
                agent_tools.apply_playbook_defaults(self.ctx)  # vtuber-story: без стоп-кадра в конце (QA 5)
                agent_tools.nameplate_after_hook(self.ctx)  # плашка не из-под хука (QA 8)
            except Exception:  # noqa: BLE001 - умолчание плейбука не повод сорвать ход
                pass

    def after_batch(self, state: TurnState, images: list[dict]) -> None:
        if self.last_change_body is not None:
            # Свежий lint и предупреждения get_clip после правок этого шага — модель видит их до отчёта.
            check = clip_check(self.ctx)
            if check:
                self.last_change_body["clip_check"] = check
            self.last_change_body = None
        if self.ctx.framing_stop and not self.framing_told:
            # QA 6 (клип 898): 12 set_layout руками без прогресса. Предел попыток — указание остановиться и спросить.
            self.framing_told = True
            images.append({"text": framing_stop_nudge(self.ctx.framing_stop)})

    def on_blocked(self, state: TurnState, reason: Any) -> str:
        # Сделанное не теряем — отчитываемся тем, что успели.
        note = f"Gemini остановился на своём фильтре контента ({reason})."
        if state.steps:
            return f"{note}\n{_fallback_summary(state.steps)}"
        raise RuntimeError(f"{note} Попробуйте ещё раз или другую модель.")

    def on_answer(self, state: TurnState, parts: list[dict], calls: list[dict], tools_on: bool,
                  candidate: dict) -> str | None:
        ctx, steps = self.ctx, state.steps
        idle = _parts_text(parts).strip().lower()
        if not calls and tools_on and not steps and not self.nudged and (not idle or "ничего не" in idle):
            # Модель иногда отвечает текстом («ничего не поменял»), не тронув клип,
            # хотя её просили монтировать. Один раз напоминаем, что работа — инструментами.
            self.nudged = True
            state.nudge("Ты ещё ничего не сделал с клипом. Работай инструментами по ПОРЯДКУ РАБОТЫ "
                        "(начни с auto_edit, если просили смонтировать), а не текстом.")
            return None
        if not calls and tools_on and steps and not self.nudged_fix:
            # Хочет закончить, а последний вызов не прошёл или ролик с дырой (прогон 578: пачка
            # финала отклонена, агент не повторил — последние 4 с пустой фон). Один раз дожимаем.
            # Кадрирование остановлено по пределу попыток — его неудачи «исправь и повтори» не дожимаем.
            fixable = [s for s in steps if s.get("tool") not in AVATAR_TOOLS] if ctx.framing_stop else steps
            nudge = fix_nudge(fixable, _current_gaps(ctx, self.gap_warnings))
            if nudge:
                self.nudged_fix = True
                state.nudge(nudge)
                return None
        if not calls and tools_on and steps and not idle and self.silent_nudges < MAX_SILENT_NUDGES:
            # Пустой ответ посреди работы (проверочный прогон: после листа кадров исходника
            # Gemini замолчал на 6-м шаге из ~30, и ход «закончился» без отчёта).
            self.silent_nudges += 1
            _log.warning("agent: пустой ответ модели посреди хода (finishReason=%s), подталкиваю",
                         candidate.get("finishReason"))
            state.nudge("Ответ пустой. Продолжай работу по задаче владельца инструментами со следующего шага; "
                        "когда всё сделано — короткий отчёт по-русски.")
            return None
        done_tools = {st.get("tool") for st in steps if st.get("ok")}
        if (
            not calls and tools_on and not self.nudged_continue
            and "auto_edit" in done_tools and not done_tools & CRAFT_TOOLS
        ):
            # Остановился сразу после автомонтажа: хук не переписан, панчлайнов нет.
            self.nudged_continue = True
            state.nudge("Ты остановился после auto_edit — это только основа. Продолжай по ПОРЯДКУ РАБОТЫ: "
                        "перепиши хук (update_layer auto_hook), поставь 1–3 панчлайна со звуком и наездом, "
                        "ключевые слова цветом, проверь contact_sheet. Потом короткий отчёт по-русски.")
            return None
        if not calls and tools_on and steps and idle:
            # Кадрирование остановлено по пределу попыток — «посмотри и поправь рамку» уже не к месту.
            missing = [] if ctx.framing_stop else unviewed_avatar_places(ctx, steps)
            if missing and self.avatar_nudges < MAX_LOOK_NUDGES:
                # Аватар в нескольких местах (QA 02.11, клип 892): один лист кадров не заменяет взгляд на
                # каждое место — агент сдал ролик с 4 с волос без лица, ни разу не вызвав view_frame.
                self.avatar_nudges += 1
                state.nudge(avatar_look_nudge(missing))
                return None
            if missing and not self.auto_avatar_looked:
                self.auto_avatar_looked = True
                state.contents.append(_auto_avatar_look(ctx, steps, missing, state.emit))
                return None
            pending = unchecked_visual(steps)
            if pending and self.look_nudges < MAX_LOOK_NUDGES:
                # Правил кадр и не посмотрел (QA 01.11: 36 шагов без единого view_frame — весь ролик
                # оказался ютуб-клипом без аватара). Сначала просим посмотреть самого.
                self.look_nudges += 1
                state.nudge(look_nudge(pending))
                return None
            if pending and not self.auto_looked:
                # Не посмотрел и после напоминания — лист кадров снимает сервис и показывает модели.
                self.auto_looked = True
                state.contents.append(_auto_look(ctx, steps, state.emit))
                return None
        final_text = _clean_reply(_parts_text(parts))
        if degenerate(final_text):
            final_text = ""  # ниже — отчёт по сделанным шагам
        elif final_text and not report_like(final_text) and tools_on and steps and not self.nudged_work:
            # Рассуждения текстом посреди работы — это ещё не конец хода. Проверочный
            # прогон: Gemini на 9-м шаге из 40 «подумал вслух», ход переключался в отчёт
            # без инструментов, и он писал «не успел из-за лимита шагов».
            self.nudged_work = True
            state.nudge("Продолжай работу инструментами по задаче владельца. Если всё уже сделано — "
                        "напиши короткий отчёт по-русски, 3–6 пунктов.")
            return None
        elif final_text and not report_like(final_text) and not self.nudged_report:
            # Вместо отчёта — рассуждения по-английски или эхо служебного вывода:
            # один раз просим нормальный отчёт (без инструментов).
            self.nudged_report = True
            state.report_only = True
            state.nudge("Это не отчёт. Напиши владельцу по-русски 3–6 пунктов: что сделал и на какой секунде. "
                        "Без рассуждений, без английского, без служебных данных.")
            return None
        elif final_text and not report_like(final_text):
            final_text = ""
        if not final_text and steps and not self.asked_report and not self.nudged_report:
            # Только шаги, без отчёта (прогон 578: ход кончился списком шагов) — один раз
            # просим отчёт без инструментов; не напишет — отчёт соберётся из шагов.
            self.asked_report = True
            state.report_only = True
            state.nudge("Ты не написал отчёт. Напиши владельцу по-русски 3–6 пунктов: что сделал и на какой "
                        "секунде; что не получилось — так и скажи.")
            return None
        if final_text and ctx.changed and not self.nudged_facts:
            # QA 4 (клип 896): «удар на обосралась» — а звук на «это»; молчание о заглушённом панчлайне и
            # реальных людях на вставках. Один раз показываем факты и просим исправить/переписать.
            gaps = report_fact_gaps(final_text, clip_check(ctx))
            if gaps:
                self.nudged_facts = True
                state.report_only = state.report_only or not tools_on
                state.nudge(facts_nudge(gaps, tools_on))
                return None
        return final_text

    def finish(self, state: TurnState, text: str) -> str:
        if not text:
            text = _fallback_summary(state.steps)
        if self.ctx.changed:
            text = honest_report(text, clip_check(self.ctx), state.steps)
        if self.ctx.framing_stop:
            text = framing_question(text)
        return text


def run_agent(
    store: Any,
    clip_plan_id: int,
    message: str,
    *,
    client: Any = None,
    model: str | None = None,
    history: list[dict] | None = None,
    on_step: Callable[[list[dict]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    max_steps: int | None = None,
    run: Any = None,
    on_first_change: Callable[[], None] | None = None,
    hooks: Any = None,
) -> dict:
    """Один ход агента: модель ↔ инструменты, пока модель не ответит текстом.

    Возвращает ``{text, steps, changed, model, rounds}``. Снимок истории делается
    один раз — перед первой правкой клипа (``on_first_change`` его заменяет: чату нужна ещё точка отката).
    ``run``, ``hooks`` — ход идёт в переписке (``app/project_chat.py``): поток текста в браузер и карточки."""
    from app.ai.gemini import GeminiClient
    from app.clip_history import snapshot

    gemini = client or (client_factory() if client_factory else None) or GeminiClient(timeout=600)
    used_model = model or settings.gemini_agent_model
    limit = max(1, int(max_steps or settings.agent_max_steps))
    ctx = ToolContext(
        store=store,
        clip_plan_id=clip_plan_id,
        on_first_change=on_first_change
        or (lambda: snapshot(store, clip_plan_id, f"ИИ: {_short_request(message, 50)}")),
        request=str(message or ""),
    )
    clip_state, _ok = agent_tools.run_tool(ctx, "get_clip", {})
    from app.project_knowledge import block_for_plan, mascot_block_for_plan

    first = (
        f"{message.strip()}\n\n---\nТекущее состояние клипа (get_clip):\n"
        + json.dumps(agent_tools.result_for_json(clip_state), ensure_ascii=False)[:MAX_RESULT_CHARS]
    )
    lore = block_for_plan(store, clip_plan_id)
    if lore:
        first += f"\n\n---\n{lore}"
    mascots = mascot_block_for_plan(store, clip_plan_id)
    if mascots:
        first += f"\n\n---\n{mascots}"
    contents = _history_contents(history or []) + [{"role": "user", "parts": [{"text": first}]}]
    if len(contents) > 1 and contents[-2]["role"] == "user":  # история кончилась вопросом без ответа
        contents[-2]["parts"][0]["text"] += "\n\n(ответа не было)"
        contents.insert(-1, {"role": "model", "parts": [{"text": "(ответ прерван)"}]})
    policy = ClipPolicy(store, clip_plan_id, message, ctx, hooks)
    text, state = run_loop(policy, gemini, used_model, contents, limit=limit, should_cancel=should_cancel,
                           on_step=on_step, run=run)
    return {"text": text, "steps": state.steps, "changed": ctx.changed, "model": used_model, "rounds": state.rounds}


# Сколько раз подталкивать модель, замолчавшую посреди работы (пустой ответ без вызовов).
MAX_SILENT_NUDGES = 2
# Инструменты, которыми агент доводит основу auto_edit до монтажа.
CRAFT_TOOLS = {"update_layer", "add_layers", "set_camera", "add_insert", "remove_layers"}
# Чьи предупреждения говорят о дыре во всём ролике (их ответы содержат линт всего клипа).
LINT_TOOLS = {"get_clip", "add_layers", "update_layer", "remove_layers"}


MAX_LOOK_NUDGES = 1  # потом лист кадров снимает сервис


# --------------------------------------------------------------------------- #
# Фоновые ходы и чат
# --------------------------------------------------------------------------- #


class _Run:
    def __init__(self, clip_plan_id: int, message_id: int) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.clip_plan_id = clip_plan_id
        self.message_id = message_id
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.started_at = time.time()


_RUNS: dict[int, _Run] = {}
_RUNS_LOCK = threading.Lock()


def _active_run(clip_plan_id: int) -> _Run | None:
    with _RUNS_LOCK:
        run = _RUNS.get(int(clip_plan_id))
        if run is not None and run.thread is not None and not run.thread.is_alive():
            _RUNS.pop(int(clip_plan_id), None)
            return None
        return run


def remember_owner_fact(store: Any, clip_plan_id: int, message: str, source_id: Any = None) -> str:
    """«Запомни: …» в чате клипа → факт проекта этого клипа (app/project_knowledge.py). Строка для ответа или пусто."""
    from app import project_knowledge as K
    from app.asset_scope import clip_project

    try:
        remembered = K.remember_from_chat(store, clip_project(store, int(clip_plan_id)), message,
                                          origin_ref=f"clip#{int(clip_plan_id)}", clip_plan_id=int(clip_plan_id),
                                          source_id=int(source_id) if source_id else None)
    except Exception as exc:  # noqa: BLE001 - разговор важнее записи
        _log.info("remember fact from clip chat %s failed: %s", clip_plan_id, exc)
        return ""
    return K.remembered_note(remembered)


def start_run(
    store: Any,
    clip_plan_id: int,
    message: str,
    *,
    client: Any = None,
    model: str | None = None,
    max_steps: int | None = None,
) -> dict:
    """Запустить ход агента в фоне. Возвращает ``{run_id, message_id}``."""
    text = str(message or "").strip()
    if not text:
        raise ValueError("пустое сообщение")
    plan = store.get_clip_plan(clip_plan_id, include_segments=False)  # KeyError → 404
    with _RUNS_LOCK:
        current = _RUNS.get(int(clip_plan_id))
        if current is not None and current.thread is not None and current.thread.is_alive():
            raise AgentBusy("агент уже работает над этим клипом")
        history = [m for m in store.list_clip_agent_messages(clip_plan_id) if m.get("status") != "running"]
        used_model = model or settings.gemini_agent_model
        store.add_clip_agent_message(clip_plan_id, "user", text[:8000])
        reply = store.add_clip_agent_message(
            clip_plan_id, "assistant", "", steps=[], status="running", model=used_model
        )
        run = _Run(int(clip_plan_id), int(reply["id"]))
        store.db.execute("UPDATE clip_agent_messages SET run_id = ? WHERE id = ?", (run.id, run.message_id))
        _RUNS[int(clip_plan_id)] = run

    note = remember_owner_fact(store, clip_plan_id, text, source_id=plan.get("source_id"))
    prompt = f"{text}\n\nСервис уже сделал: {note}" if note else text

    def work() -> None:
        def on_step(steps: list[dict]) -> None:
            store.update_clip_agent_message(run.message_id, steps=steps)

        try:
            result = run_agent(
                store, clip_plan_id, prompt, client=client, model=used_model, history=history,
                on_step=on_step, should_cancel=run.cancel.is_set, max_steps=max_steps,
            )
            reply = f"{note}\n\n{result['text']}" if note else result["text"]
            store.update_clip_agent_message(run.message_id, text=reply, steps=result["steps"], status="done")
        except AgentCancelled:
            store.update_clip_agent_message(run.message_id, text="Остановлено. Уже сделанные правки остались — "
                                            "Ctrl+Z откатит их целиком.", status="cancelled")
        except Exception as exc:  # noqa: BLE001 - ошибка хода видна в чате, сервер живёт
            _log.warning("editor agent failed for clip plan %s: %s", clip_plan_id, exc)
            store.update_clip_agent_message(run.message_id, text=f"Ошибка: {str(exc)[:500]}", status="error")

    run.thread = threading.Thread(target=work, name=f"editor-agent-{run.id}", daemon=True)
    run.thread.start()
    return {"run_id": run.id, "message_id": run.message_id, "model": used_model}


def cancel_run(clip_plan_id: int) -> bool:
    run = _active_run(clip_plan_id)
    if run is None:
        return False
    run.cancel.set()
    return True


def wait_run(clip_plan_id: int, timeout: float | None = None) -> bool:
    """Дождаться конца хода (для тестов и CLI в процессе). True — закончился."""
    run = _active_run(clip_plan_id)
    if run is None or run.thread is None:
        return True
    run.thread.join(timeout)
    return not run.thread.is_alive()


def chat_view(store: Any, clip_plan_id: int) -> dict:
    """Переписка + флаг «агент работает» — то, что опрашивает редактор."""
    store.get_clip_plan(clip_plan_id, include_segments=False)
    run = _active_run(clip_plan_id)
    messages = []
    for message in store.list_clip_agent_messages(clip_plan_id):
        if message.get("status") == "running" and (run is None or run.message_id != message["id"]):
            # Сервер перезапускали посреди хода — поток умер, запись осталась.
            store.update_clip_agent_message(message["id"], status="interrupted",
                                            text=message.get("text") or "Прервано перезапуском сервера.")
            message["status"] = "interrupted"
            message["text"] = message.get("text") or "Прервано перезапуском сервера."
        messages.append(
            {
                "id": message["id"],
                "role": message["role"],
                "text": message.get("text") or "",
                "steps": [{k: v for k, v in s.items() if k != "args"} for s in message.get("steps") or []],
                "status": message.get("status") or "done",
                "model": message.get("model") or "",
                "created_at": message.get("created_at"),
            }
        )
    return {
        "messages": messages,
        "running": run is not None,
        "run_id": run.id if run else None,
        "model": settings.gemini_agent_model,
    }


def clear_chat(store: Any, clip_plan_id: int) -> int:
    if _active_run(clip_plan_id) is not None:
        raise AgentBusy("агент ещё работает — сначала остановите его")
    return store.clear_clip_agent_messages(clip_plan_id)
