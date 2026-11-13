"""💬 Чат с агентом: страница «Чат» (переписки по проектам и общая), вкладка «Чат» серии и Telegram.

Владелец: «было бы круто еще общаться с тем агентом как с тобой. типа на каждый проект
свои чаты» и «общаться как с тобой в плане текстом в сервисе».

Цикл «модель ↔ инструменты» общий с ИИ-монтажёром (``app/ai/agent_loop.py``), клиент Gemini тоже
(``editor_agent.client_factory`` — тесты подставляют фейк), реестр инструментов общий с MCP
(``app/agent_tools.py``). Устройство:

* **Переписки** (``chat_threads``): у переписки может быть проект-серия/канал или нет
  (общий чат). У проекта — своя «основная» переписка (``is_default``): её же показывает
  вкладка «Чат» серии и в неё пишет Telegram-тема проекта. Реплики — ``project_chat_messages``.
  Чат видео — переписка с ``source_id``: одна на видео, проект берётся у видео в момент чтения
  (перенесли видео — чат работает уже в новом проекте, лор старого к нему не идёт), удаляется вместе с видео.
  Чат клипа — переписка с ``clip_plan_id``: так же, а ход в ней делает ИИ-монтажёр (``app/clip_chat.py``).
* **Инструменты группами** (``app/chat_tools.py``): база (проект + посмотреть клип) в каждом
  ходе, остальное — ``load_tools`` по запросу модели или по словам владельца.
* **Ответ печатается по ходу**: ход идёт в фоновом потоке, текст Gemini приходит потоком
  (``stream_generate_content``) в живое состояние хода (``_Run``), а SSE-ручка
  (``app/agent_chat_api.py``) отдаёт его браузеру. Клиент без потока — индикатор «думает…» и шаги.
* **Шаги видны**: каждый вызов — шаг с аргументами, кратким результатом и превью (кадр,
  лист кадров, клип, карточка одобрения).
* **Откат**: перед первой правкой клипа в ходе — снимок истории клипа (Ctrl+Z в редакторе) и
  копия состояния в ``chat_checkpoints`` (кнопка «Откатить» в чате, не зависит от глубины истории).
* **Опасное** (публикация, удаление…) — не выполняется сразу: карточка ``chat_confirmations``,
  владелец жмёт «Подтвердить» — ``decide_confirmation`` выполняет вызов.
* **Память**: модели — последние реплики и сжатое резюме старых (у переписки; у основной
  переписки проекта резюме дублируется в ``project_autopilot.chat_summary``).

Разговор в проекте — источник обучения: правилоподобная фраза владельца («такое больше не бери»,
«вот так круто») сразу разбирается эвристикой автопилота (``autopilot.learn_from_text``).
Настройка проекта ``auto_rules``: ``confirm`` (по умолчанию) — карточка «Записать правило?»,
ответ «да» в чате её одобряет; ``auto`` — однозначное записывается сразу (с цитатой).
Провайдер — Gemini сервиса; нет ключа или он лёг — так и отвечаем, с подсказкой, что делать.
Платный Polza не используется.

Где что лежит. Здесь — ход агента: модель, шаг, отправка, подтверждения. Вынесено: нити и сообщения ``app/project_chat_threads.py``, контекст и сводка
``app/project_chat_context.py``, вид нити и подтверждения ``app/project_chat_view.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable

from app import agent_settings as S
from app import autopilot as A
from app import chat_tools as CT
from app import gemini_usage
from app import project_knowledge as K
from app import projects as P
from app.agent_tools import ToolContext
from app.ai.agent_loop import (  # noqa: F401
    _merge_part, _pure_text, brief_args, call_model, clean_reply, history_contents, MAX_OUTPUT_TOKENS, parts_text,
    run_loop, SAY, step_from_result, TurnPolicy, TurnState,
)
from app.settings import settings

from app.project_chat_threads import (  # noqa: F401
    VIDEO_THREAD_TITLE, CLIP_THREAD_TITLE, HIDDEN_SOURCE_TYPES, _MESSAGES_SQL, CHAT_SCHEMA_SQL, ensure_chat_schema,
    _migrate_messages, ChatBusy, ChatCancelled, _loads, _dumps, _thread, get_thread, video_thread, _video_title,
    clip_thread, find_clip_thread, legacy_clip_messages, active_clip_run, wait_clip_run, create_thread,
    default_thread, update_thread, _touch, delete_thread, list_threads, _msg, add_thread_message, add_message,
    update_message, get_message, thread_messages, list_messages, clear_thread, clear_chat, ai_status, models, _Run,
    _RUNS, _LOCK, _live, active_run, cancel, wait_thread, wait, _active,
)
from app.project_chat_context import (  # noqa: F401
    _log, HISTORY_KEEP, SUMMARIZE_AFTER, SUMMARY_CHARS, CONTEXT_CHARS, CONTEXT_SOURCES, error_hint, video_context,
    project_context, general_context, _heuristic_summary, summarize_thread, maybe_summarize, model_history,
)
from app.project_chat_view import (  # noqa: F401
    _confirmation, get_confirmation, _patch_step, revert_checkpoint, _decorate, thread_view, chat_view,
)

MAX_ROUNDS = 24
# Клиповые инструменты, полезные в разговоре о проекте (совместимость: прежний список чата проекта).
CLIP_TOOLS = ("get_clip", "get_transcript", "get_performance", "get_playbook", "list_clips", "list_projects")


def _gemini(client: Any = None, timeout: int = 300) -> Any:
    from app.ai import editor_agent

    if client is not None:
        return client
    if editor_agent.client_factory:
        return editor_agent.client_factory()
    if not settings.gemini_api_key:
        return None
    from app.ai.gemini import GeminiClient

    return GeminiClient(timeout=timeout)


# --------------------------------------------------------------------------- #
# Контекст и резюме
# --------------------------------------------------------------------------- #

SYSTEM = """\
Ты — агент сервиса Vertical Video Fabric (нарезка стримов, сериалов и роликов в вертикальные шортсы и \
публикация). Владелец общается с тобой в чате, как с ассистентом.

Как работать:
- Отвечай по-русски, коротко и по делу, как коллега. Можно Markdown: списки, **жирный**, `код`.
- Не только советуй — делай инструментами. Не выдумывай: факты — из контекста ниже и инструментов.
  Не знаешь или инструмент не сработал — так и скажи.
- Инструменты подгружаются группами (их объявления дорого слать в каждом ходе). Сейчас есть база:
  проект, найти и посмотреть клип (list_clips, get_clip, view_frame, contact_sheet), плейбуки (get_playbook),
  нарезка источника (analyze_source), источник по ссылке (add_source_from_url). Нужна правка клипа или
  другое — сначала load_tools с нужными группами, потом вызывай их инструменты. Группы:
{groups}
- Клиповым инструментам нужен clip_id (list_clips: без аргументов — клипы этого проекта, source_id — одного видео). Время клиповых инструментов — секунды клипа.
  Таймкоды в ответе пиши так: «клип #123, 12.5 с» или «источник #7, 1:05:30» — владелец кликает по ним.
- Правки клипа откатываются кнопкой «Откатить» в чате и Ctrl+Z в редакторе — правь смело, но только то,
  что просили. Посмотри результат (view_frame / contact_sheet), прежде чем отчитаться.
- Опасное (публикация, удаление клипа, снятие всех слоёв, отмена правила) и правка черновика, которого владелец не
  выбрал, сервис сразу не выполняет: владелец подтверждает карточкой в чате. Ответ pending_confirmation — скажи, что
  ждёшь подтверждения, и не повторяй вызов.
- Что можно в этом проекте без владельца, решает он сам: блок agent_settings.allows в срезе. Держись его. Отказ
  сервиса называет настройку и где её менять: перескажи владельцу, настройки сам не меняй и не обходи. Новый клип
  через create_clip только по прямому заданию, а не чтобы обойти выбор из списка.
- Готовый ролик (mp4) — только render_clip(clip_id) → render_status(render_id, wait_sec=30) до status done.
  Просят «отрендеренный клип», «готовый ролик», «результат» — запусти render_clip сам и дождись done. Не пиши
  «отрендерен», «готов», «соберётся автоматически», пока render_status не ответил done: сам сервис клип не
  рендерит, а ответ о готовом ролике без рендера он пометит.
- Задание просит готовый ролик («результат — отрендеренный клип») или плейбук велит рендер — ход не кончается, пока
  у клипа, который ты правил или создал, нет render_status done. Не заканчивай списком «осталось: render_clip →
  render_status» — сделай это сам; не вышло — скажи почему.
- Холст (create_canvas_clip) ложится в проект этой переписки. Клип с project_warning или project_unassigned в
  get_clip — чужой или ничей: не выдавай его за свою работу и не бери как готовый результат.
- Клип, созданный не в этой переписке, — не твоя работа; не выдавай его за результат. Кто его делал — поле made_in в
  get_clip и list_clips. Нашёл готовый клип по тому же заданию — скажи владельцу, чей он, и делай своё (или спроси).
- Без настройки «Публиковать самому» публикуется только одобренное владельцем. Перед отправкой — lint_clip (зоны
  кнопок площадок, темп чтения, вспышки, луп): warn поправь и перерендерь. Клип на публикацию — submit_for_approval.
  Заголовок, описание и хэштеги сервис пишет сам после рендера; обложку — generate_covers → choose_cover (группа montage).
- Правила проекта — только словами владельца, с цитатой. Если в сообщении владельца правило или отмена
  правила, сервис уже разобрал его (блок «Сервис уже сделал»): опирайся на это и не дублируй.
  Своё наблюдение — гипотеза (add_hypothesis) или вопрос владельцу (propose_rule), не правило.
- Что ты знаешь о проекте — в срезе ниже: лор (brief), rules, facts (что владелец просил запомнить), memes (какие мемы
  он выбрал). Держись этого и не выдумывай сверх. Владелец рассказал новое о проекте (кто это, что значит словечко,
  как тут принято) — запиши remember_fact одной фразой; «забудь» — forget_fact. Он видит и правит этот список сам.
  noticed в срезе — что агент заметил сам, владелец не подтвердил: не выдавай за факт, при расхождении верь владельцу.
- Типовой заказ — сначала плейбук: нарезка стрима — get_playbook("stream-shorts"), реклама сайта или товара —
  get_playbook("ad-voiceover"), промо приложения по записям экрана — "app-promo". Подходящий сервис кладёт ниже
  сам (блок «АКТИВНЫЙ ПЛЕЙБУК»); его шаги и чек-лист — условие сдачи.
- Озвучка своим текстом (реклама, холст, серия) — группа voice: add_voiceover(clip_id, lines [{{text}}]) — фразы без
  style (Gemini зачитывает указания вслух), слои голоса длиной файла, субтитры по голосу; потом check_voiceover
  («озвучка в порядке»). Голос — narrator_voice из брифа. tts_preview — только проба голоса. render_status с
  «озвучка с ошибками» — не готово: поправь и перерендерь.
- Ссылка в сообщении: видео-исходник (YouTube, Twitch, файл) — add_source_from_url; картинка, гифка, мем
  в клип — add_image_from_url / add_video_from_url (группа assets).
- Приложенные файлы: «файл для монтажа #N» — asset_id для add_insert и слоёв; «источник #N» — source_id
  для analyze_source, create_clip, get_source_transcript.
- Стиль монтажа владельца: без эмодзи, текст по центру, динамика наездами и звуком в слово.
- Стрим режем так: посмотри его, запиши, что заметил о проекте (note_observation), спроси, что непонятно
  (add_stream_questions; кто это и что за шутка — level: project), дождись ответов, потом режь. Пока ответов
  нет, нарезка и вырезы откажут и назовут открытые вопросы: скажи об этом владельцу, не обходи. Снять паузу может только он.
  Если настройка проекта не ждёт ответов, вопросы всё равно задавай, но работу они не держат.
- Новая часть ролика с маскотом — группа explainer: explainer_overview, потом explainer_create. Объяснялка делается
  в проекте любого вида: тип и настройки проекта ради неё не меняй. Голос у маскота один на все ролики: не меняй его
  сам, даже чтобы не платить. Платный голос и новые позы запускает «Разрешить» владельца (или настройка проекта «Тратить
  без подтверждения» в её пределах): скажи сумму и что выбор за ним: разрешить или сменить голос маскота. Про голос, цену, субтитры и что не сделано говори только то, что
  написано в полях summary, voice, spent_rub, skipped результата explainer_status: своими догадками не дополняй. Пока
  status не done — клипа нет, так и скажи.
"""

PROJECT_NOTE = ("\nЭто переписка проекта-серии/канала #{pid}. В инструментах проекта project_id — id этого "
                "проекта (он ниже); у клиповых — clip_id. Лор и правила других проектов отсюда недоступны.\n"
                "Задание на проект: новый стрим по ссылке — add_source_from_url (видео ляжет в этот проект, скачается "
                "в фоне), уже скачанное — analyze_source(source_id).\n")
VIDEO_NOTE = ("\nЭто чат одного видео: source_id {sid}, «{title}». «Это видео», «стрим», «здесь» — оно. Инструментам "
              "источника давай source_id {sid}, клипы этого видео — list_clips(source_id={sid}), ещё нарезать — "
              "analyze_source(source_id={sid}). Что про него известно — в блоке video среза.\n")
GENERAL_NOTE = ("\nЭто общий чат, без проекта. Проекты-серии/каналы и источники — в срезе ниже; инструментам "
                "проекта давай project_id (list_channel_projects) или source_id. Просят новую серию про героя, а он "
                "похож на маскота проекта (поле mascots): это объяснялка с этим маскотом, группа explainer, нового "
                "персонажа не заводи.\n")
MASCOT_NOTE = ("\nУ этого проекта уже есть маскот: {names} (блок mascots среза). Новая серия, часть или ролик, где не "
               "назван другой герой, — это объяснялка с ним: load_tools с группой explainer, "
               "explainer_overview(project_id={pid}), explainer_create(mascot_id, topic). Нового персонажа не заводи и "
               "внешность не придумывай (aiv_define_character), пока владелец прямо не попросит другого героя; "
               "не ясно, о ком речь, — спроси.\n")


# --------------------------------------------------------------------------- #
# Инструменты (совместимость с прежним чатом проекта)
# --------------------------------------------------------------------------- #


def chat_tools() -> list:
    known = CT.all_tools()
    return [known[n] for n in CT.base_names()]


def _declarations(json_schema: bool = True, groups: list[str] | None = None) -> list[dict]:
    return CT.declarations(list(groups or []), json_schema)


# --------------------------------------------------------------------------- #
# Ход агента
# --------------------------------------------------------------------------- #


def _service_note(learned: dict | None, decided: dict | None) -> str:
    parts = []
    if decided:
        parts.append(decided.get("note") or "")
    text = A.describe_learning(learned)
    if text:
        parts.append(text)
    return " ".join(p for p in parts if p).strip()


def _quick_decision(store: Any, project_id: int, text: str, actor: str) -> dict | None:
    """«да»/«нет» в чате — ответ на последнее «Записать правило?» из этого чата."""
    cls = A.classify_reply(text)
    if cls["intent"] not in ("approve", "reject") or cls.get("praise"):
        return None
    row = store.db.query_one(
        "SELECT id FROM approvals WHERE project_id = ? AND kind = 'rule_proposal' AND status = 'pending' "
        "AND submitted_by = 'chat' ORDER BY id DESC LIMIT 1", (int(project_id),))
    if not row:
        return None
    result = A.decide(store, int(row["id"]), cls["intent"], actor=actor)
    from app.notify_tg import decision_note

    return {"approval_id": int(row["id"]), "status": result["status"], "note": decision_note(result)}


RULE_JUDGE = """Владелец канала написал агенту реплику. Это ПРАВИЛО на будущее для всего проекта (что делать/не делать
всегда, оценка типа контента) или РАЗОВАЯ правка конкретного клипа? Ответь только JSON:
{{"rule": true|false, "kind": "do"|"dont", "text": "правило коротко, по-русски"}}
Реплика: «{text}»"""


def rule_judge(gemini: Any) -> Callable[[str], dict | None]:
    """Спорная реплика (без «всегда/больше не…» и без «убери этот…») → Gemini одной короткой
    подсказкой. Не ответил или ответ не разобрать — None, решает эвристика автопилота."""

    def judge(text: str) -> dict | None:
        from app.ai.gemini import _extract_response_text

        response = gemini.generate_content(settings.gemini_text_model, {
            "contents": [{"role": "user", "parts": [{"text": RULE_JUDGE.format(text=str(text)[:600])}]}],
            "generationConfig": {"maxOutputTokens": 200, "responseMimeType": "application/json"}})
        raw = _extract_response_text(response).strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else None
        if not isinstance(data, dict) or not isinstance(data.get("rule"), bool):
            return None
        return {"rule": data["rule"], "kind": data.get("kind"), "text": str(data.get("text") or "")[:300]}

    return judge


def _system(thread: dict, groups: list[str]) -> str:
    text = SYSTEM.format(groups=CT.groups_hint())
    if thread.get("project_id"):
        text += PROJECT_NOTE.format(pid=thread["project_id"])
        if thread.get("mascots"):
            text += MASCOT_NOTE.format(names=", ".join(m["name"] for m in thread["mascots"]), pid=thread["project_id"])
    elif not thread.get("source_id"):
        text += GENERAL_NOTE
    if thread.get("source_id"):
        text += VIDEO_NOTE.format(sid=thread["source_id"], title=thread.get("source_title") or "")
    if groups:
        text += "\nПодгружены группы: " + ", ".join(groups) + ".\n"
    if any(g in groups for g in ("montage", "subtitles", "screens")):
        from app.ai.editor_agent_prompts import CRAFT

        text += "\nРЕМЕСЛО МОНТАЖА (для правок клипа; справка по слоям — get_motion_reference)\n" + CRAFT
    if thread.get("summary"):
        text += f"\nРезюме прежней переписки:\n{thread['summary']}\n"
    return text


def _short(text: str, limit: int = 50) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _checkpoint(store: Any, thread_id: int, message_id: int, clip_plan_id: int, label: str) -> int | None:
    from app.clip_history import capture, snapshot

    state = snapshot(store, clip_plan_id, label)  # Ctrl+Z в редакторе — тот же шаг
    if state is None:
        try:
            state = capture(store, clip_plan_id)
        except Exception:  # noqa: BLE001
            return None
    cur = store.db.execute(
        "INSERT INTO chat_checkpoints (thread_id, message_id, clip_plan_id, label, state_json) VALUES (?, ?, ?, ?, ?)",
        (int(thread_id), int(message_id), int(clip_plan_id), label, _dumps(state)))
    return int(cur.lastrowid)


def _step_from_result(step: dict, result: Any, ok: bool) -> dict:
    step_from_result(step, result, ok)
    step["result"] = CT.result_preview(result)
    return step


class ThreadPolicy(TurnPolicy):
    """Ход чата с агентом: инструменты группами, карточки опасного, точки отката и превью в шагах."""

    def __init__(self, store: Any, thread: dict, groups: list[str], message: str, reply_id: int | None, note: str) -> None:
        self.store = store
        self.thread = thread
        self.groups = groups
        self.message = message
        self.reply_id = reply_id
        self.note = note
        self.project_id = thread.get("project_id")
        self.changes: list[dict] = []
        self.ctxs: dict[int, ToolContext] = {}
        from app.ai.render_claims import db_now

        self.started = db_now(store)  # начало хода: правленому в ходе клипу нужен рендер не раньше

    def prompt(self, state: TurnState) -> str:
        # Плейбук по смыслу запроса (как у ИИ-монтажёра клипа): нарезка стрима, реклама с озвучкой, серия.
        from app.project_chat_playbook import block, pick

        return _system(self.thread, self.groups) + block(pick(self.message, state.steps))

    def declarations(self, json_schema: bool) -> list[dict]:
        return CT.declarations(self.groups, json_schema)

    def clip_ctx(self, clip_id: int) -> ToolContext:
        if clip_id not in self.ctxs:
            def first_change(cid: int = clip_id) -> None:
                if self.reply_id is None:
                    return
                cp = _checkpoint(self.store, self.thread["id"], self.reply_id, cid, f"Чат: {_short(self.message)}")
                if cp:
                    self.changes.append({"checkpoint_id": cp, "clip_plan_id": cid})

            # project_hint — проект чата: get_clip пометит клип другого проекта или ничей холст.
            self.ctxs[clip_id] = ToolContext(store=self.store, clip_plan_id=clip_id, on_first_change=first_change,
                                             project_hint=int(self.project_id) if self.project_id else None)
        return self.ctxs[clip_id]

    def run_call(self, state: TurnState, name: str, args: dict) -> tuple[Any, bool]:
        store, project_id = self.store, self.project_id
        tool = CT.all_tools().get(name)
        label_text = tool.step_label(args) if tool else f"Неизвестный инструмент {name}"
        step: dict = {"tool": name, "label": label_text, "ok": None, "args": brief_args(args), "kind": "tool"}
        if tool and tool.scope == "clip" and args.get("clip_id") not in (None, ""):
            try:
                step["clip_id"] = int(args["clip_id"])
            except (TypeError, ValueError):
                pass
        state.steps.append(step)
        state.emit("tool")
        ok = False
        if tool and CT.is_dangerous(store, name, args, project_id):
            result: Any = hold_for_owner(store, self.thread["id"], self.reply_id, step, name, args, label_text)
        else:
            group = CT.group_of(name) if tool is not None and name != CT.LOAD_TOOLS else None
            if group and group not in self.groups:
                self.groups.append(group)  # позвала инструмент неподгруженной группы — оставим её
            result, ok, clip_id = CT.run(store, name, args, project_id=project_id, clip_ctx=self.clip_ctx)
            _step_from_result(step, result, ok)
            if ok and name == CT.LOAD_TOOLS:
                self.groups[:] = list(dict.fromkeys([*self.groups, *(result.get("loaded") or [])]))
            if clip_id and "clip_id" not in step:
                step["clip_id"] = clip_id
            preview = CT.preview_for(store, name, result, clip_id) if ok else None
            if preview:
                step["preview"] = preview
        state.emit()
        return result, ok

    def on_blocked(self, state: TurnState, reason: Any) -> str:
        return f"Gemini остановился на своём фильтре контента ({reason}). Переформулируй или выбери другую модель."

    def finish_guard(self, state: TurnState, answer: str) -> str | None:
        # Задание просит готовый ролик (или плейбук велит рендер), а у клипа этого хода рендера нет — ход не кончается.
        from app.ai.finish_guard import render_guard
        from app.ai.render_claims import touched_clips
        from app.project_chat_playbook import pick

        changed, _seen = touched_clips(state.steps)
        return render_guard(self.store, self.message, pick(self.message, state.steps), changed, self.started)

    def finish(self, state: TurnState, text: str) -> str:
        from app.ai.finish_guard import thread_clips, with_foreign_note
        from app.ai.render_claims import with_render_note

        if not text:
            done = [s["label"] for s in state.steps if s.get("ok") and s.get("kind") == "tool"]
            text = self.note or ("Сделал:\n" + "\n".join(f"- {d}" for d in done[:20]) if done else "Готово.")
        # «Отрендерено»/«готово» без рендера у клипов этого хода — пометка сервиса (эксперимент 12.11, B2).
        text = with_render_note(self.store, text, state.steps, self.started)
        # Клип, сделанный не в этой переписке, результатом не выдаём (повтор 13.11, RC3: чужой клип 1074).
        return with_foreign_note(self.store, text, thread_clips(self.store, self.thread.get("id"), state.steps))


def hold_for_owner(store: Any, thread_id: int, reply_id: int | None, step: dict, name: str, args: dict, label: str) -> dict:
    """Опасное не выполняем: карточка владельцу в переписке, модели — «ждёт подтверждения»."""
    conf_id = None
    if reply_id is not None:
        cur = store.db.execute(
            "INSERT INTO chat_confirmations (thread_id, message_id, tool, args_json, label) "
            "VALUES (?, ?, ?, ?, ?)", (thread_id, reply_id, name, _dumps(args), label))
        conf_id = int(cur.lastrowid)
    step.update(kind="confirm", ok=None, confirmation_id=conf_id, danger=CT.danger_label(name))
    return {"ok": False, "pending_confirmation": True,
            "note": "не выполнено: ждёт подтверждения владельца кнопкой в чате; не повторяй "
                    "вызов, скажи владельцу, что ждёшь подтверждения"}


def run_turn(store: Any, thread: dict | int, message: str, *, actor: str = "chat:ui", message_id: int | None = None,
             reply_id: int | None = None, history: list[dict] | None = None, client: Any = None,
             on_step: Callable[[list], None] | None = None, run: _Run | None = None,
             max_rounds: int = MAX_ROUNDS, model: str | None = None, max_steps: int | None = None) -> dict:
    """Один ход: обучение на словах владельца (в проекте), затем модель ↔ инструменты.
    Возвращает ``{text, steps, learned, decided, ai, model, changes}``."""
    if not isinstance(thread, dict):
        thread = get_thread(store, int(thread))
    with usage_scope(thread, reply_id):
        return _run_turn(store, thread, message, actor=actor, message_id=message_id, reply_id=reply_id,
                         history=history, client=client, on_step=on_step, run=run, max_rounds=max_rounds,
                         model=model, max_steps=max_steps)


def usage_scope(thread: dict, reply_id: int | None = None) -> Any:
    """Контекст учёта трат Gemini для хода переписки: сессия ``chat:<id>``, ответ, проект/видео/клип."""
    return gemini_usage.scope("clip_chat" if thread.get("clip_plan_id") else "agent_chat",
                              session=f"chat:{int(thread['id'])}", message_id=reply_id,
                              project_id=thread.get("project_id"), source_id=thread.get("source_id"),
                              clip_id=thread.get("clip_plan_id"))


def _run_turn(store: Any, thread: dict, message: str, *, actor: str, message_id: int | None, reply_id: int | None,
              history: list[dict] | None, client: Any, on_step: Callable[[list], None] | None, run: _Run | None,
              max_rounds: int, model: str | None, max_steps: int | None) -> dict:
    if thread.get("clip_plan_id"):
        from app.clip_chat import run_clip_turn

        return run_clip_turn(store, thread, message, reply_id=reply_id, history=history, client=client,
                             on_step=on_step, run=run, model=model, max_steps=max_steps)
    project_id = thread.get("project_id")
    source_id = thread.get("source_id")
    if source_id:
        thread = {**thread, "source_title": _video_title(store, int(source_id))}
    if project_id:
        thread = {**thread, "mascots": K.project_mascots(store, int(project_id))}
    learned = decided = remembered = None
    gemini = _gemini(client)
    if project_id:
        cfg = A.get_config(store, project_id)
        decided = _quick_decision(store, project_id, message, actor)
        if decided is None:
            learned = A.learn_from_text(store, project_id, message, origin="chat",
                                        origin_ref=f"chat#{message_id}" if message_id else "chat",
                                        auto_rules=cfg["auto_rules"],
                                        judge=rule_judge(gemini) if gemini is not None else None)
            if (learned or {}).get("action") == "none":
                # Не правило, а факт: «запомни: Глеб — модер». Модель для этого не нужна.
                remembered = K.remember_from_chat(store, project_id, message.split("\n[Прикреплён")[0],
                                                  origin_ref=f"chat#{message_id}" if message_id else "chat",
                                                  source_id=int(source_id) if source_id else None)
    note = " ".join(x for x in (_service_note(learned, decided), K.remembered_note(remembered)) if x)
    if gemini is None:
        text = ("ИИ-агент сейчас недоступен: в сервисе не настроен GEMINI_API_KEY. Сообщение сохранено. "
                "Что сделать: добавь ключ Gemini из AI Studio в .env сервиса и перезапусти его.") \
            + (f"\n{note}" if note else "")
        return {"text": text, "steps": [], "learned": learned, "decided": decided, "remembered": remembered,
                "ai": False, "changes": []}

    groups = list(dict.fromkeys([*thread.get("tool_groups", []), *CT.preload_for(message)]))
    if project_id:
        context = project_context(store, project_id, source_id=source_id)
    elif source_id:  # видео вне проектов: лора нет, только оно само
        context = json.dumps({"video": video_context(store, int(source_id))}, ensure_ascii=False, default=str)
    else:
        context = general_context(store)
    label = "срез проекта" if project_id else ("срез видео" if source_id else "срез сервиса")
    first = f"{message.strip()}\n\n---\nСвежий {label}:\n{context}"
    if note:
        first += f"\n\nСервис уже сделал: {note}"
    contents = history_contents(history or []) + [{"role": "user", "parts": [{"text": first}]}]
    if len(contents) > 1 and contents[-2]["role"] == "user":  # история кончилась вопросом без ответа
        contents.insert(-1, {"role": "model", "parts": [{"text": "(ответ прерван)"}]})
    used_model = model or thread.get("model") or settings.gemini_agent_model
    policy = ThreadPolicy(store, thread, groups, message, reply_id, note)
    final, state = run_loop(policy, gemini, used_model, contents, limit=max_steps or max_rounds, on_step=on_step, run=run)
    try:
        if sorted(groups) != sorted(thread.get("tool_groups", [])):
            update_thread(store, thread["id"], tool_groups=groups)
    except KeyError:
        pass
    return {"text": final, "steps": state.steps, "learned": learned, "decided": decided, "remembered": remembered,
            "ai": True, "model": used_model, "changes": policy.changes}


# --------------------------------------------------------------------------- #
# Фоновые ходы
# --------------------------------------------------------------------------- #


def _attachments_note(attachments: list[dict]) -> tuple[str, list[dict]]:
    clean, lines = [], []
    for item in attachments or []:
        kind = str(item.get("kind") or "")
        try:
            ident = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "")[:120]
        media = str(item.get("media") or "")[:20]
        if kind == "asset":
            lines.append(f"[Прикреплён файл для монтажа #{ident} «{name}» ({media or 'файл'}) — asset_id {ident}]")
        elif kind == "source":
            lines.append(f"[Прикреплён источник #{ident} «{name}» — source_id {ident}]")
        else:
            continue
        clean.append({"kind": kind, "id": ident, "name": name, "media": media})
    return "\n".join(lines), clean


def send_to_thread(store: Any, thread_id: int, text: str, *, origin: str = "ui", actor: str = "chat:ui",
                   client: Any = None, attachments: list[dict] | None = None,
                   on_done: Callable[[str], None] | None = None, model: str | None = None,
                   max_steps: int | None = None) -> dict:
    """Сообщение владельца → ход агента в фоне. Возвращает {message_id, reply_id, thread_id}."""
    text = str(text or "").strip()
    note, files = _attachments_note(attachments or [])
    if not text and not files:
        raise ValueError("пустое сообщение")
    thread = get_thread(store, thread_id)
    with _LOCK:
        if _live(_RUNS.get(int(thread_id))):
            raise ChatBusy("агент ещё отвечает на прошлое сообщение")
        history = model_history(store, thread)
        user_id = add_thread_message(store, thread_id, "user", text or "(файл)", origin=origin,
                                     meta={"attachments": files} if files else None)
        used_model = model or thread.get("model") or settings.gemini_agent_model
        reply_id = add_thread_message(store, thread_id, "assistant", "", status="running", origin=origin, model=used_model)
        if thread["title"] in ("Новый чат", "") and not thread["is_default"] and text:
            update_thread(store, thread_id, title=_short(text, 60))
        run = _Run(int(thread_id), thread.get("project_id"), reply_id, thread.get("clip_plan_id"))
        _RUNS[int(thread_id)] = run
        prompt = (text + ("\n" + note if note else "")).strip()

        def _work() -> None:
            def on_step(steps: list) -> None:
                update_message(store, reply_id, steps=steps)

            reply = ""
            try:
                result = run_turn(store, get_thread(store, thread_id), prompt, actor=actor, message_id=user_id,
                                  reply_id=reply_id, history=history, client=client, on_step=on_step, run=run,
                                  model=model, max_steps=max_steps)
                meta = {"changes": result.get("changes") or [],
                        **({"remembered": result["remembered"]} if result.get("remembered") else {})}
                update_message(store, reply_id, text=result["text"], steps=result["steps"],
                               learned=result.get("learned") or {}, status="done",
                               model=result.get("model") or "", meta=meta)
                reply = result["text"]
            except ChatCancelled:
                partial = run.snapshot()
                reply = (partial["text"].strip() + "\n\n" if partial["text"].strip() else "") + \
                    "Остановлено. Уже сделанные правки остались — «Откатить» в чате или Ctrl+Z в редакторе."
                update_message(store, reply_id, text=reply, steps=partial["steps"], status="cancelled",
                               meta={"changes": _changes_of(store, reply_id)})
            except Exception as exc:  # noqa: BLE001 - честно: ИИ недоступен
                _log.warning("chat thread %s failed: %s", thread_id, exc)
                partial = run.snapshot()
                reply = _failure_text(thread, exc)
                update_message(store, reply_id, text=reply, status="error", steps=partial["steps"],
                               meta={"changes": _changes_of(store, reply_id)})
            try:
                summarize_thread(store, thread_id, _gemini(client) if settings.gemini_api_key or client else None)
            except Exception:  # noqa: BLE001
                pass
            with run.lock:
                run.done = True
                run.phase = "done"
                run.steps_seq += 1
            _touch(store, thread_id)
            if on_done:
                try:
                    on_done(reply)
                except Exception as exc:  # noqa: BLE001
                    _log.info("chat on_done failed: %s", exc)

        def work() -> None:
            # Траты Gemini за ход (и резюме после него) — на эту переписку и этот ответ (app/gemini_usage.py).
            with usage_scope(thread, reply_id):
                _work()

        thread_obj = threading.Thread(target=work, name=f"chat-{thread_id}", daemon=True)
        run.thread = thread_obj
        thread_obj.start()
    return {"message_id": user_id, "reply_id": reply_id, "thread_id": int(thread_id), "run_id": run.id}


def _failure_text(thread: dict, exc: Exception) -> str:
    hint = error_hint(exc)
    if thread.get("clip_plan_id"):  # чат клипа всегда начинал сбой со слова «Ошибка»: на это смотрят скрипты
        return hint if hint.startswith("Ошибка") else f"Ошибка: {hint}"
    return f"ИИ-агент не ответил. {hint} Сообщение сохранено."


def _changes_of(store: Any, message_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT id, clip_plan_id FROM chat_checkpoints WHERE message_id = ? ORDER BY id",
                              (int(message_id),))
    return [{"checkpoint_id": int(r["id"]), "clip_plan_id": int(r["clip_plan_id"])} for r in rows]


def send(store: Any, project_id: int, text: str, *, origin: str = "ui", actor: str = "chat:ui",
         client: Any = None, on_done: Callable[[str], None] | None = None) -> dict:
    """Сообщение в основную переписку проекта (вкладка «Чат» серии, Telegram)."""
    P.get_project(store, project_id, include_sources=False)
    thread = default_thread(store, project_id)
    return send_to_thread(store, thread["id"], text, origin=origin, actor=actor, client=client, on_done=on_done)


def decide_confirmation(store: Any, confirmation_id: int, approve: bool, actor: str = "chat:ui") -> dict:
    """Кнопка владельца на карточке опасного действия: выполнить вызов или отказаться."""
    conf = get_confirmation(store, confirmation_id)
    if conf["status"] != "pending":
        raise ValueError(f"уже решено: {conf['status']}")
    thread = get_thread(store, conf["thread_id"])
    if not approve:
        store.db.execute("UPDATE chat_confirmations SET status = 'rejected', decided_at = CURRENT_TIMESTAMP "
                         "WHERE id = ?", (int(confirmation_id),))
        _patch_step(store, conf["message_id"], "confirmation_id", int(confirmation_id), ok=False,
                    error="владелец отказался", confirm_status="rejected")
        add_thread_message(store, thread["id"], "assistant", f"Отменено владельцем: {conf['label']}.",
                           meta={"service": True})
        return {"ok": True, "status": "rejected", "confirmation": get_confirmation(store, confirmation_id)}
    changes: list[dict] = []
    if conf["args"].get("clip_id") not in (None, ""):
        # Кнопка владельца — его выбор: черновик взят в работу (настройка «Останавливаться на списке клипов»).
        try:
            S.set_picked(store, int(conf["args"]["clip_id"]))
        except (TypeError, ValueError):
            pass

    def clip_ctx(clip_id: int) -> ToolContext:
        def first_change() -> None:
            cp = _checkpoint(store, thread["id"], conf["message_id"], clip_id, f"Чат: {conf['label']}")
            if cp:
                changes.append({"checkpoint_id": cp, "clip_plan_id": clip_id})

        return ToolContext(store=store, clip_plan_id=clip_id, on_first_change=first_change)

    with usage_scope(thread, conf["message_id"]):
        result, ok, _clip = CT.run(store, conf["tool"], conf["args"], project_id=thread.get("project_id"),
                                   clip_ctx=clip_ctx)
    status = "done" if ok else "failed"
    store.db.execute("UPDATE chat_confirmations SET status = ?, result_json = ?, decided_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (status, _dumps(CT.result_preview(result)), int(confirmation_id)))
    fields: dict = {"ok": ok, "confirm_status": status, "result": CT.result_preview(result)}
    if not ok and isinstance(result, dict):
        fields["error"] = str(result.get("error") or "")[:300]
    preview = CT.preview_for(store, conf["tool"], result, None) if ok else None
    if preview:
        fields["preview"] = preview
    _patch_step(store, conf["message_id"], "confirmation_id", int(confirmation_id), **fields)
    if changes:
        try:
            message = get_message(store, conf["message_id"])
            meta = message["meta"]
            meta["changes"] = [*meta.get("changes", []), *changes]
            update_message(store, conf["message_id"], meta=meta)
        except KeyError:
            pass
    text = (f"Владелец подтвердил: {conf['label']} — выполнено." if ok
            else f"Владелец подтвердил: {conf['label']} — не вышло: {fields.get('error') or 'ошибка'}.")
    add_thread_message(store, thread["id"], "assistant", text, meta={"service": True})
    return {"ok": ok, "status": status, "result": CT.result_preview(result),
            "confirmation": get_confirmation(store, confirmation_id)}


def handle_telegram(store: Any, project_id: int, text: str, actor: str, message_id: int) -> dict:
    """Сообщение в теме проекта (или reply на сообщение бота проекта) → ход агента, ответ — туда же."""
    from app import notify_tg

    def reply(answer: str) -> None:
        notify_tg.send_text(store, answer, project_id=project_id, purpose="chat", reply_to=message_id)

    try:
        return send(store, project_id, text, origin="tg", actor=actor, on_done=reply)
    except ChatBusy as exc:
        reply(f"⏳ {exc}")
        return {"busy": True}
