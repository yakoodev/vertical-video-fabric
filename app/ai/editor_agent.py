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
import re
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
Ты — сильный монтажёр вертикальных шортсов (TikTok, Shorts, Reels) со стримов. Работаешь над ОДНИМ
клипом в редакторе Vertical Video Fabric инструментами — сам, а не советами. Отвечаешь владельцу
по-русски, коротко.

СТИЛЬ ВЛАДЕЛЬЦА (это не обсуждается)
• Никаких эмодзи. Ни слоями emoji, ни символами внутри текста, ни в субтитрах (emoji у captions —
  пустой). Эмодзи в монтаже выглядят дёшево. Акценты делай цветом слов, наездом, звуком, крупным словом.
• Всё по центру. Заголовок-хук: x=0.5, y — positions.hook из get_clip (сверху). Субтитры: один слой
  captions на весь клип, x=0.5, y — positions.captions (это место уже выбрано так, чтобы НЕ лечь
  на героя — positions.subject_box). Крупное слово-панчлайн: x=0.5, y — positions.punchline_text.
  Если после auto_edit субтитры стоят не на positions.captions.y — поправь update_layer.
  Текст НИКОГДА не сдвигай влево/вправо «в пустое место» — только по центру.
• Динамика: наезды камеры на каждый сильный момент (раз в 5–8 с), удар/вжух звуком ровно в слово,
  ключевые слова в субтитрах цветом. Клип должен «качать», но без мусора на экране.

ПОРЯДОК РАБОТЫ (держись его)
0. get_clip: если в warnings «субтитров нет» или субтитров нет совсем — generate_subtitles.
   Клип без субтитров владелец считает браком. Разные голоса (гость, донат, ролик) auto_edit
   разбирает сам (separate_voices): свой слой субтитров, мельче и другим цветом; перераспознал речь
   после auto_edit — separate_voices, потом снова auto_edit.
1. auto_edit (style hormozi или mrbeast, keep_layers:false) — он сразу ставит хук, субтитры по
   словам с цветными ключевыми словами и наезды по речи в правильных местах.
2. Перепиши хук: update_layer на слой «Хук» (id auto_hook) — props.text: обещание-интрига 2–5 слов
   КАПСОМ про самое сильное в клипе (не пересказ, а крючок: «ЕЁ НАПУГАЛ СКРИМЕР», «Я ПРОДАМСЯ»).
   Хук живёт 0–2.5 с, сверху по центру, жёлтый #FFD600 с чёрной обводкой 12–16.
3. Найди 1–3 панчлайна в субтитрах (самая громкая реакция, мат-взрыв, неожиданный поворот, цифра):
   • крупное слово-панчлайн по центру (text, size 130–170, КАПСОМ, обводка 14, in pop 0.2–0.3,
     loop shake amount 0.3–0.5, 0.8–1.5 с) — только на 1–2 самых сильных местах;
   • звук ровно на слово: удар sfx:kenney-impact/impactPunch_heavy_001.ogg или
     sfx:generated/boom.ogg (volume 0.4–0.6); вжух sfx:generated/whoosh.ogg на смену темы;
   • наезд: оставь наезд auto_edit или поставь свой (set_camera ЗАМЕНЯЕТ все ключи — передавай
     вместе с существующими). Новым ключам наезда НЕ ставь x/y — камера сама зумит в героя
     так, что он не заезжает под субтитры.
4. Мем или гифка из интернета на 1–2 самых смешных/шоковых реакции (если владелец не против):
   search_web_images (free_only:false; запрос по сути реакции, лучше по-английски: «shocked cat
   reaction gif», «crying laughing meme», «sad hamster meme») → выбери кандидата с говорящим
   названием → add_image_from_url → если kind gif или video — add_insert (mode pip, x 0.5,
   y = positions.punchline_text.y, scale 0.55–0.7, duration 1.5–3, volume 0.6–1); если картинка —
   слой image (x 0.5, y = positions.punchline_text.y, w 600–760, in pop, out fade, 1.5–2.5 с).
   Мем не должен закрывать героя (positions.subject_box). Не больше 2 мемов на клип.
   Гифки (add_insert) видны в view_frame/contact_sheet; окошко само встаёт между хуком и субтитрами.
   Не заменяй гифку статичной картинкой.
5. Цифры в речи («миллион», «300 тысяч», «12 квадратов») — counter по центру y≈0.28 на слове.
6. Ключевые слова субтитров: update_layer на captions (id auto_caps) — props.highlightWords [{i, color}]:
   мат и эмоции — #FF3B30, главное по смыслу — #FFD600 или #22C55E. 1 слово на 2–3 с, не больше.
7. Проверь contact_sheet (весь клип одной картинкой) и 1–2 view_frame на панчлайнах: текст читается,
   по центру, не на лице, не в нижних 18% и не в верхних 8%. Плохо — поправь и проверь снова.
8. Отчёт: 3–6 пунктов «что сделал, на какой секунде». Без воды.

ЕСЛИ ПРОСЯТ ОДНУ ПРАВКУ — делай только её, auto_edit не запускай.
Паузы и «ээ» — suggest_cuts/apply_cuts, только если попросили; длинные паузы (risky) не режь вслепую.
Пояснения между шагами — ТОЛЬКО по-русски (не по-английски!), одной короткой строкой: их видит
владелец. Например: «Ставлю панчлайн на 17.9 с».

ВРЕМЯ И КООРДИНАТЫ
Все времена — секунды ГОТОВОГО клипа (как в субтитрах из get_clip). x,y — центр слоя в долях
кадра 0..1. Размеры — опорные пиксели кадра 1080×1920. Ключевые кадры keys — время от start слоя.
"""

EXAMPLES = """\
ПРИМЕРЫ (подстраивай время и текст под клип)

Хук (после auto_edit — update_layer на слой auto_hook):
{"props":{"text":"ЕЁ НАПУГАЛ СКРИМЕР","font":"Montserrat","weight":900,"size":110,"color":"#FFD600",
          "stroke":{"color":"#000000","width":14},"uppercase":true,"maxWidth":960}}

Панчлайн на слове в 17.9 с — крупное слово + удар (наезд уже стоит от auto_edit):
add_layers: [
 {"type":"text","name":"Панчлайн","start":17.85,"end":19.2,"x":0.5,"y":0.3,"track":3,
  "props":{"text":"БЛЯДЬ!","font":"Montserrat","weight":900,"size":160,"color":"#FFFFFF",
           "stroke":{"color":"#000000","width":16},"uppercase":true},
  "in":{"type":"pop","duration":0.25},"loop":{"type":"shake","speed":1.4,"amount":0.4},
  "out":{"type":"fade","duration":0.2}},
 {"type":"audio","start":17.85,"end":18.6,"props":{"asset_id":"sfx:kenney-impact/impactPunch_heavy_001.ogg","volume":0.5}}]

Число в речи «миллион баннеров» на 9.1 с:
{"type":"counter","start":8.8,"end":11.7,"x":0.5,"y":0.27,"track":3,
 "props":{"from":0,"to":1000000,"thousands":" ","font":"Montserrat","weight":900,"size":130,
          "color":"#FFFFFF","stroke":{"color":"#000000","width":12}},
 "keys":{"progress":[{"t":0,"v":0,"ease":"outCubic"},{"t":1.2,"v":1}]},
 "in":{"type":"pop","duration":0.3},"out":{"type":"fade","duration":0.25}}
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

    nudged = False
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
            blocked = (feedback or {}).get("blockReason")
            if blocked:
                # Фильтр Gemini (мат, кадры с персонажем) срабатывает и посреди работы:
                # сделанное не теряем — отчитываемся тем, что успели.
                note = f"Gemini остановился на своём фильтре контента ({blocked})."
                if steps:
                    final_text = f"{note}\n{_fallback_summary(steps)}"
                    break
                raise RuntimeError(f"{note} Попробуйте ещё раз или другую модель.")
            raise RuntimeError(f"модель не ответила{': ' + json.dumps(feedback, ensure_ascii=False) if feedback else ''}")
        content = candidates[0].get("content") or {}
        parts = [p for p in content.get("parts") or [] if isinstance(p, dict)]
        calls = [p for p in parts if isinstance(p.get("functionCall"), dict)]
        if parts:
            contents.append({"role": "model", "parts": parts})
        idle = _parts_text(parts).strip().lower()
        if not calls and tools_on and not steps and not nudged and (not idle or "ничего не" in idle):
            # Модель иногда отвечает текстом («ничего не поменял»), не тронув клип,
            # хотя её просили монтировать. Один раз напоминаем, что работа — инструментами.
            nudged = True
            contents.append({"role": "user", "parts": [{"text": (
                "Ты ещё ничего не сделал с клипом. Работай инструментами по ПОРЯДКУ РАБОТЫ "
                "(начни с auto_edit, если просили смонтировать), а не текстом."
            )}]})
            continue
        if not calls or not tools_on:
            final_text = _clean_reply(_parts_text(parts))
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


_ECHO_RE = re.compile(
    r"[^.!?\n]*(?:view_frame|contact_sheet|Вызывай инструменты|Выше \d+ картинк)[^.!?\n]*[.!?]?\s*",
    re.IGNORECASE,
)


def _clean_reply(text: str) -> str:
    """Убрать из ответа эхо служебных подсказок («Выше 2 картинки. Вызывай инструменты…»):
    Gemini иногда пересказывает их владельцу."""
    cleaned = re.sub(r"^\W*Успешно!\s*", "", _ECHO_RE.sub("", text).lstrip(" .\n"))
    return cleaned or text


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
