"""ИИ-монтажёр: схемы функций и содержимое запроса к модели (из ``app/ai/editor_agent.py``)."""

from __future__ import annotations

import json
from typing import Any

from app import agent_tools
from app.agent_tools import ImageResult

SAY = "_say"  # «шаг»-реплика модели между вызовами инструментов (не инструмент)
HISTORY_MESSAGES = 16  # сколько прошлых реплик чата показывать модели
KEEP_IMAGES = 2  # картинки кадров держим только в последних N ответах инструментов
FRAME_MAX_HEIGHT = 960  # кадр для модели: 540×960 хватает, чтобы читать титры
MAX_RESULT_CHARS = 60000  # огромный ответ инструмента режем, чтобы не утопить контекст


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
    """Кадр 1080×1920 PNG весит мегабайты — модели хватает JPEG 540×960 (длинная сторона
    FRAME_MAX_HEIGHT в любом формате: 16:9 — 960×540)."""
    try:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(image.data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return image.data, image.mime_type
        height, width = frame.shape[:2]
        if max(height, width) > FRAME_MAX_HEIGHT:
            scale = FRAME_MAX_HEIGHT / max(height, width)
            size = (max(2, int(width * scale)), max(2, int(height * scale)))
            frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
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


# Подпись-заглушка Gemini 3 для вызова функции без своей подписи (документирована Google для перенесённой
# истории). QA 9 (клип 902): ход упал на 4-м шаге — «Function call is missing a thought_signature».
SKIP_SIGNATURE = "skip_thought_signature_validator"


def patch_thought_signatures(contents: list[dict]) -> bool:
    """Первому вызову функции в каждом ходе модели без ``thoughtSignature`` — подпись-заглушка. → поставили ли."""
    patched = False
    for content in contents:
        if content.get("role") != "model":
            continue
        call = next((p for p in content.get("parts") or [] if isinstance(p, dict) and "functionCall" in p), None)
        if call is not None and not call.get("thoughtSignature"):
            call["thoughtSignature"] = SKIP_SIGNATURE
            patched = True
    return patched


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
        if ok and result.get("skipped"):
            # add_layers применил пачку частично: пропущенные слои видны в журнале, как ошибки.
            step["partial"] = True
            step["applied"] = str(result.get("applied") or "")
            step["problems"] = [str(p)[:300] for p in result["skipped"][:12]]
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
