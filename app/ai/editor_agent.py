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
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import threading
import time
import uuid
from typing import Any, Callable

from app import agent_tools
from app.agent_tools import ImageResult, ToolContext
from app.settings import settings

_log = logging.getLogger(__name__)

SAY = "_say"  # «шаг»-реплика модели между вызовами инструментов (не инструмент)
HISTORY_MESSAGES = 16  # сколько прошлых реплик чата показывать модели
KEEP_IMAGES = 2  # картинки кадров держим только в последних N ответах инструментов
FRAME_MAX_HEIGHT = 960  # кадр для модели: 540×960 хватает, чтобы читать титры
MAX_RESULT_CHARS = 60000  # огромный ответ инструмента режем, чтобы не утопить контекст


# Откуда брать клиента Gemini, если его не передали явно (тесты подставляют фейк).
client_factory: Callable[[], Any] | None = None


class AgentBusy(RuntimeError):
    """У клипа уже идёт ход агента."""


class AgentCancelled(Exception):
    pass


# --------------------------------------------------------------------------- #
# Системный промпт: ремесло монтажёра коротких вертикальных видео
# --------------------------------------------------------------------------- #

CRAFT = """\
Ты — сильный монтажёр коротких вертикальных видео (TikTok, YouTube Shorts, Reels) и моушн-дизайнер.
Работаешь в редакторе Vertical Video Fabric над ОДНИМ клипом: кладёшь поверх видео моушн-слои
(титры, эмодзи, стикеры, картинки, стрелки, счётчики, графики, плашки, звуки), делаешь наезды
камеры, правишь субтитры, переходы и подрезку. Делаешь всё сам — инструментами, а не советами.
Отвечаешь владельцу по-русски, коротко и по делу.

КАК РАБОТАТЬ
1. Пойми задачу. Состояние клипа уже приложено к сообщению (как get_clip) — изучи субтитры со
   временем слов: это твоя партитура. Перечитывай get_clip после подрезки кусков.
2. Спланируй 3–8 акцентов, привязанных к словам: хук, панчлайны, цифры, реакции, смена темы.
3. Делай правки крупными вызовами (add_layers принимает сразу несколько слоёв).
4. ОБЯЗАТЕЛЬНО посмотри результат: view_frame в 1–3 ключевых моментах (середина титра, пик
   наезда). Проверь: текст читается, не закрывает лицо и действие, не вылез из кадра, не
   залез в нижние 20% и верхние 10%. Плохо — поправь update_layer и посмотри снова.
5. Если инструмент вернул ok:false — прочитай problems, исправь и повтори. Не сдавайся после
   первой ошибки, но и не повторяй одно и то же.
6. Закончи коротким отчётом: что сделал (по пунктам, со временем) и что владельцу стоит
   проверить. Без воды и без пересказа инструкций.

РЕМЕСЛО
• Хук в первые 2 секунды: крупный титр-обещание (3–6 слов) с 0.0–0.2 с, анимация pop/words,
  можно лёгкий наезд 1.0→1.1. Зритель решает листать или нет именно тут.
• Титры: 3–6 слов, КАПСОМ для акцента, size 72–110 для заголовков, 52–64 для подписей,
  обводка stroke {color:#000000, width 8–12} или плашка bg — текст обязан читаться на любом фоне.
  Держи на экране минимум 1.2 с (+0.3 с на каждое слово сверх трёх).
• Безопасные зоны 1080×1920: центр слоёв с текстом по y 0.15–0.72. Нижние 20% (y > 0.8) —
  подпись, кнопки и прогресс-бар площадки; верхние 10% — шапка. Справа (x > 0.85) — кнопки лайков.
• Субтитры владельца (если включены) стоят внизу (subtitle_zone в get_clip) — не клади туда
  титры, поднимай выше. Не дублируй субтитры титрами слово в слово: титр — это акцент.
• Лица и действие не закрывать: сначала view_frame без слоёв (with_layers:false), найди, где
  лицо, и ставь графику в свободную часть кадра.
• Панчлайн: наезд камеры 1.0→1.15–1.35 за 0.15–0.3 с с ease outCubic ровно на слове-панчлайне,
  держать 0.8–2 с, вернуть 1.0 за 0.3 с; плюс звук «бум/вжух» (audio-слой из пака sfx, найди
  через search_pack) и/или эмодзи/титр. Не больше одного сильного наезда на 5–8 с.
• Реакции и мемные моменты: эмодзи (🔥😂😱💀🤯👀) или стикер рядом с лицом, pop + loop pulse/wiggle,
  0.8–1.5 с. Подходящий мем из библиотеки (list_assets) — слой image с asset_id.
• Числа в речи («300 тысяч», «в 5 раз», «70%»): инфографика — counter (растущее число), progress
  или chart; появляется на слове с числом.
• Указать на объект: shape arrow с in draw, или ellipse обвести.
• Не перегружай: не больше 1 новой графики на 2–3 с и не больше 2–3 слоёв на экране одновременно.
  Лучше 5 точных акцентов, чем 20 случайных.
• Единый стиль: 1–2 шрифта на весь клип (list_fonts, для русского cyrillic=true), 2–3 цвета
  (например белый + акцентный жёлтый #FFD60A или красный #FF3B30), одинаковые анимации входа.
• Тайминг по словам: start слоя = начало слова из субтитров (минус 0.05–0.1 с), анимации входа
  0.2–0.35 с, выхода 0.15–0.25 с. Звук-акцент ставь ровно на удар.
• Звуки (audio) — тише речи (volume 0.4–0.8), короткие; duck:true для громких.
• Анимированные субтитры (слой captions): ставь по центру по x (0.5) и НИЖЕ лица — обычно y 0.70–0.76;
  в раскладке «видео сверху, вебка снизу» — на шов, чуть выше вебки. Никогда не на голову/лицо.
  Ключевые слова — highlightWords, эмодзи над словами — emoji (номер слова из get_clip).
• Быстрая основа: auto_edit ставит хук, субтитры, эмодзи и наезды по речи за секунду — можно начать
  с него и доводить руками. Паузы и «ээ» — suggest_cuts/apply_cuts (длинные паузы risky — не режь
  вслепую: в реакциях тишина часто и есть момент).
• Пояснения между шагами (то, что пишешь текстом) — по-русски и в одну короткую строку: их видит владелец.
• Не трогай то, о чём не просили: если владелец просит одну правку — делай одну.
• Если просят убрать/переделать — правь существующие слои (update_layer/remove_layers), а не
  добавляй новые поверх.

ВРЕМЯ И КООРДИНАТЫ
Все времена — секунды ГОТОВОГО клипа (как в субтитрах из get_clip). x,y — центр слоя в долях
кадра 0..1. Размеры — опорные пиксели кадра 1080×1920. Ключевые кадры keys — время от start слоя.
"""

EXAMPLES = """\
ПРИМЕРЫ ХОРОШИХ СЛОЁВ (подстраивай время и текст под клип)

1) Хук в начале — крупный титр, слова появляются по одному:
{"type":"text","name":"Хук","start":0.0,"end":2.4,"x":0.5,"y":0.22,"track":1,
 "props":{"text":"ОН ЭТО СДЕЛАЛ","font":"Montserrat","weight":900,"size":104,"color":"#FFFFFF",
          "stroke":{"color":"#000000","width":10},"uppercase":true,"maxWidth":900,
          "highlight":{"words":[2],"color":"#FFD60A"}},
 "in":{"type":"words","duration":0.35},"out":{"type":"fade","duration":0.2}}

2) Панчлайн на слове в 7.40 с — эмодзи + звук + наезд камеры:
add_layers: [
 {"type":"emoji","start":7.35,"end":8.8,"x":0.78,"y":0.3,"props":{"char":"💀","size":220},
  "in":{"type":"pop","duration":0.3},"loop":{"type":"wiggle","speed":1.5,"amount":0.6},
  "out":{"type":"fade","duration":0.2}},
 {"type":"audio","start":7.38,"end":8.4,"props":{"asset_id":"sfx:generated/boom.ogg","volume":0.7}}]  (ref звука — из search_pack)
set_camera: [{"t":7.3,"zoom":1.0,"x":0.5,"y":0.5},{"t":7.5,"zoom":1.3,"x":0.5,"y":0.42,"ease":"outCubic"},
 {"t":9.0,"zoom":1.3,"x":0.5,"y":0.42},{"t":9.3,"zoom":1.0,"x":0.5,"y":0.5}]
(set_camera ЗАМЕНЯЕТ все ключи — передавай вместе с уже существующими наездами.)

3) Число в речи «заработал 300 тысяч» на 12.1 с — растущий счётчик на плашке:
{"type":"counter","name":"300к","start":12.0,"end":14.5,"x":0.5,"y":0.3,
 "props":{"from":0,"to":300000,"thousands":" ","suffix":" ₽","font":"Montserrat","weight":900,"size":120,
          "color":"#FFD60A","stroke":{"color":"#000000","width":10}},
 "keys":{"progress":[{"t":0,"v":0,"ease":"outCubic"},{"t":1.2,"v":1}]},
 "in":{"type":"zoom","duration":0.3},"out":{"type":"fade","duration":0.2}}

4) Указать на объект и подписать:
[{"type":"shape","start":4.0,"end":6.0,"x":0.62,"y":0.46,"rotation":35,
  "props":{"kind":"arrow","w":260,"h":40,"stroke":{"color":"#FF3B30","width":14}},"in":{"type":"draw","duration":0.35}},
 {"type":"callout","start":4.1,"end":6.0,"x":0.5,"y":0.66,
  "props":{"title":"Тот самый кот","subtitle":"3 года в розыске","accent":"#FF3B30","style":"pill","w":760},
  "in":{"type":"slideUp","duration":0.3},"out":{"type":"fade","duration":0.2}}]
"""


def system_prompt() -> str:
    return CRAFT + "\nСПРАВКА ПО СЛОЯМ\n" + agent_tools.motion_reference() + "\n\n" + EXAMPLES


# --------------------------------------------------------------------------- #
# Gemini: объявления функций, ответы инструментов, картинки
# --------------------------------------------------------------------------- #

# То, чего нет в OpenAPI-подмножестве параметров Gemini (запасной путь без
# parametersJsonSchema).
_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "$schema", "$id", "pattern", "default", "examples"}


def function_declarations(json_schema: bool = True) -> list[dict]:
    out = []
    for tool in agent_tools.list_tools():
        decl: dict = {"name": tool.name, "description": tool.description}
        if json_schema:
            decl["parametersJsonSchema"] = tool.parameters
        else:
            decl["parameters"] = _openapi_schema(tool.parameters)
        out.append(decl)
    return out


def _openapi_schema(schema: Any) -> Any:
    """JSON Schema → OpenAPI-подмножество, которое понимает поле ``parameters``."""
    if isinstance(schema, dict):
        clean = {k: _openapi_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
        if clean.get("type") == "object" and not clean.get("properties"):
            clean.pop("properties", None)
        return clean
    if isinstance(schema, list):
        return [_openapi_schema(v) for v in schema]
    return schema


def _shrink_frame(image: ImageResult) -> tuple[bytes, str]:
    """Кадр 1080×1920 PNG весит мегабайты — модели хватает JPEG 540×960."""
    try:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(image.data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return image.data, image.mime_type
        height, width = frame.shape[:2]
        if height > FRAME_MAX_HEIGHT:
            scale = FRAME_MAX_HEIGHT / height
            frame = cv2.resize(frame, (max(2, int(width * scale)), FRAME_MAX_HEIGHT), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if ok:
            return buf.tobytes(), "image/jpeg"
    except Exception:  # noqa: BLE001 - без OpenCV отдаём как есть
        pass
    return image.data, image.mime_type


def _response_object(result: Any) -> dict:
    """functionResponse.response обязан быть объектом; большое — режем."""
    payload = agent_tools.result_for_json(result)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        return {"ok": payload.get("ok", True), "truncated": True, "json": text[:MAX_RESULT_CHARS]}
    return json.loads(text)


def _prune_images(contents: list[dict], keep: int = KEEP_IMAGES) -> None:
    """Старые кадры выкидываем: каждый стоит токенов, а смотреть важно на свежие."""
    seen = 0
    for turn in reversed(contents):
        parts = turn.get("parts") or []
        if not any("inlineData" in p for p in parts):
            continue
        seen += 1
        if seen > keep:
            turn["parts"] = [p for p in parts if "inlineData" not in p] + [
                {"text": "(старый кадр убран из контекста)"}
            ]


def _step_from_result(step: dict, result: Any, ok: bool) -> dict:
    step["ok"] = ok
    if isinstance(result, dict):
        if not ok:
            step["error"] = str(result.get("error") or "")[:300]
            if result.get("problems"):
                step["problems"] = [str(p)[:300] for p in result["problems"][:12]]
        if result.get("warnings"):
            step["warnings"] = [str(w)[:200] for w in result["warnings"][:8]]
    if isinstance(result, ImageResult):
        step["frame_t"] = result.info.get("t")
    return step


def _parts_text(parts: list[dict]) -> str:
    return "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()


def _short_request(message: str, limit: int = 60) -> str:
    text = " ".join(str(message or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _history_contents(history: list[dict]) -> list[dict]:
    """Прошлая переписка — текстом. Подряд идущие реплики одной роли склеиваем."""
    contents: list[dict] = []
    for message in history[-HISTORY_MESSAGES:]:
        role = "user" if message.get("role") == "user" else "model"
        text = str(message.get("text") or "").strip()
        if role == "model":
            done = [s.get("label") for s in message.get("steps") or [] if s.get("ok") and s.get("tool") != SAY]
            if done:
                text += "\n(сделанные шаги: " + "; ".join(str(d) for d in done[:20]) + ")"
        if not text:
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return contents


# --------------------------------------------------------------------------- #
# Цикл агента
# --------------------------------------------------------------------------- #


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
) -> dict:
    """Один ход агента: модель ↔ инструменты, пока модель не ответит текстом.

    Возвращает ``{text, steps, changed, model, rounds}``. Снимок истории делается
    один раз — перед первой правкой клипа.
    """
    from app.ai.gemini import GeminiClient
    from app.clip_history import snapshot

    gemini = client or (client_factory() if client_factory else None) or GeminiClient(timeout=600)
    used_model = model or settings.gemini_agent_model
    limit = max(1, int(max_steps or settings.agent_max_steps))
    ctx = ToolContext(
        store=store,
        clip_plan_id=clip_plan_id,
        on_first_change=lambda: snapshot(store, clip_plan_id, f"ИИ: {_short_request(message, 50)}"),
    )
    clip_state, _ok = agent_tools.run_tool(ctx, "get_clip", {})
    first = (
        f"{message.strip()}\n\n---\nТекущее состояние клипа (get_clip):\n"
        + json.dumps(agent_tools.result_for_json(clip_state), ensure_ascii=False)[:MAX_RESULT_CHARS]
    )
    contents = _history_contents(history or []) + [{"role": "user", "parts": [{"text": first}]}]
    if len(contents) > 1 and contents[-2]["role"] == "user":  # история кончилась вопросом без ответа
        contents[-2]["parts"][0]["text"] += "\n\n(ответа не было)"
        contents.insert(-1, {"role": "model", "parts": [{"text": "(ответ прерван)"}]})
    steps: list[dict] = []
    use_json_schema = True
    final_text = ""
    rounds = 0

    def emit() -> None:
        if on_step:
            on_step(copy.deepcopy(steps))

    def call_model(tools_on: bool) -> dict:
        nonlocal use_json_schema
        payload: dict = {
            "systemInstruction": {"parts": [{"text": system_prompt()}]},
            "contents": contents,
            # Температуру не трогаем: Gemini 3 рассчитана на 1.0, ниже — зацикливается.
        }
        if tools_on:
            payload["tools"] = [{"functionDeclarations": function_declarations(use_json_schema)}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        try:
            return gemini.generate_content(used_model, payload)
        except RuntimeError as exc:
            # Старый эндпоинт не знает parametersJsonSchema — повторяем с OpenAPI-схемой.
            if tools_on and use_json_schema and "parametersJsonSchema" in str(exc):
                use_json_schema = False
                payload["tools"] = [{"functionDeclarations": function_declarations(False)}]
                return gemini.generate_content(used_model, payload)
            raise

    while True:
        if should_cancel and should_cancel():
            raise AgentCancelled()
        tools_on = rounds < limit
        if not tools_on:
            contents.append({"role": "user", "parts": [{"text": (
                "Лимит шагов исчерпан. Больше инструментов не будет — коротко отчитайся владельцу, "
                "что успел сделать и что осталось."
            )}]})
        response = call_model(tools_on)
        rounds += 1
        candidates = response.get("candidates") if isinstance(response, dict) else None
        if not candidates:
            feedback = (response or {}).get("promptFeedback") if isinstance(response, dict) else None
            raise RuntimeError(f"модель не ответила{': ' + json.dumps(feedback, ensure_ascii=False) if feedback else ''}")
        content = candidates[0].get("content") or {}
        parts = [p for p in content.get("parts") or [] if isinstance(p, dict)]
        calls = [p for p in parts if isinstance(p.get("functionCall"), dict)]
        if parts:
            contents.append({"role": "model", "parts": parts})
        if not calls or not tools_on:
            final_text = _parts_text(parts)
            break
        said = _parts_text(parts)
        if said:
            # Пояснение модели между вызовами («Ставлю хук и наезд») — тоже видно в чате.
            steps.append({"tool": SAY, "label": said[:400], "ok": True})
            emit()
        responses: list[dict] = []
        images: list[dict] = []
        for part in calls:
            if should_cancel and should_cancel():
                raise AgentCancelled()
            call = part["functionCall"]
            name = str(call.get("name") or "")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            try:
                label = agent_tools.get_tool(name).step_label(args)
            except KeyError:
                label = f"Неизвестный инструмент {name}"
            step = {"tool": name, "label": label, "ok": None, "args": _brief_args(args)}
            steps.append(step)
            emit()
            result, ok = agent_tools.run_tool(ctx, name, args)
            _step_from_result(step, result, ok)
            emit()
            body = _response_object(result)
            if isinstance(result, ImageResult):
                data, mime = _shrink_frame(result)
                body["note"] = "кадр приложен следующей картинкой"
                images.append({"text": f"Кадр клипа на {result.info.get('t')} с:"})
                images.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})
            response_part: dict = {"functionResponse": {"name": name, "response": body}}
            if call.get("id"):
                response_part["functionResponse"]["id"] = call["id"]
            responses.append(response_part)
        contents.append({"role": "user", "parts": responses + images})
        _prune_images(contents)

    if not final_text:
        final_text = _fallback_summary(steps)
    return {"text": final_text, "steps": steps, "changed": ctx.changed, "model": used_model, "rounds": rounds}


def _brief_args(args: dict) -> dict:
    """Аргументы шага для журнала: без километровых списков."""
    text = json.dumps(args, ensure_ascii=False, default=str)
    if len(text) <= 1500:
        return args
    return {"_truncated": text[:1500]}


def _fallback_summary(steps: list[dict]) -> str:
    done = [s["label"] for s in steps if s.get("ok") and s.get("tool") != SAY]
    if not done:
        return "Ничего не поменял."
    return "Сделал:\n" + "\n".join(f"• {label}" for label in done[:30])


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
    store.get_clip_plan(clip_plan_id, include_segments=False)  # KeyError → 404
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

    def work() -> None:
        def on_step(steps: list[dict]) -> None:
            store.update_clip_agent_message(run.message_id, steps=steps)

        try:
            result = run_agent(
                store, clip_plan_id, text, client=client, model=used_model, history=history,
                on_step=on_step, should_cancel=run.cancel.is_set, max_steps=max_steps,
            )
            store.update_clip_agent_message(run.message_id, text=result["text"], steps=result["steps"], status="done")
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
