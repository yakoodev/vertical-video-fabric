"""Инструменты озвучки: ``add_voiceover`` (текст → голос на клипе, app/voiceover.py) и ``check_voiceover`` (анализатор,
app/voice_check.py). Регистрируются в общем реестре (``agent_tools.registry``): MCP, чат проекта и ИИ-монтажёр.
"""

from __future__ import annotations

from app.tool_core import Tool, ToolContext, ToolError, _obj


def h_add_voiceover(ctx: ToolContext, args: dict) -> dict:
    from app.dubbing import DubError, provider_paid
    from app.voiceover import VoiceoverError, add_voiceover

    if not provider_paid(args.get("provider") or "gemini") or args.get("confirm"):
        ctx.before_change()
    try:
        return add_voiceover(ctx, args)
    except (VoiceoverError, DubError) as exc:
        raise ToolError(str(exc)) from None
    except RuntimeError as exc:
        raise ToolError(f"озвучка не сделана: {str(exc)[:300]}") from None


def h_check_voiceover(ctx: ToolContext, args: dict) -> dict:
    from app.voice_check import check_voiceover

    report = check_voiceover(ctx.store, ctx.plan_id, asr=args.get("asr", True) is not False,
                             levels=args.get("levels", True) is not False)
    report["note"] = ("t — шкала clip (view_frame, слои). Чини warn по fix / fix_call ({tool, args}) и вызови "
                      "check_voiceover снова; перед сдачей — render_clip → render_status (там тот же вердикт).")
    return report


_LINE = {"type": "object", "properties": {
    "text": {"type": "string", "description": "фраза (до 400 знаков; лучше ≤ 200) — она же в субтитрах"},
    "say": {"type": "string", "description": "как прочитать, если не как написано: «лайв-два-дэ» для Live2D, «фри "
                                             "десять» для Free10 (синтез и сверка — по say, субтитры — text)"},
    "start": {"type": "number", "description": "секунда клипа (шкала clip), где начать файл фразы; нет — сразу за "
                                               "прошлой фразой"},
    "pause_after": {"type": "number", "description": "пауза после фразы, с (по умолчанию 0.35)"}},
    "required": ["text"]}


def tools() -> list[Tool]:
    return [
        Tool(
            "add_voiceover", "Озвучиваю клип",
            "Озвучить клип или холст своим текстом — ОДНИМ вызовом: lines [{text, say?, start?, pause_after?}] (5–8 фраз до "
            "~200 знаков, 25–45 с на рекламу) → синтез каждой фразы (без style: Gemini читает указания вслух) → "
            "Whisper слушает фразу (не тот текст — синтез ещё раз) → слои audio vo_N (role voice, mode track, end = "
            "длина файла) подряд: следующая через pause_after (0.35 с) после последнего слышного слова → холст "
            "короче голоса удлиняется → субтитры из текста на времени настоящего голоса (subtitles: auto — у холста "
            "всегда, у клипа со своими субтитрами — нет; true/false) → check_voiceover. Голос: voice, иначе "
            "рассказчик проекта (бриф narrator_voice), иначе голос сервиса; голоса и тембры — tts_preview. provider "
            "gemini (по умолчанию) — БЕСПЛАТНО; polza — ПЛАТНО: без confirm только оценка ₽, confirm — только по «да» "
            "владельца на сумму. replace: true — заменить прежние vo_* (пересобрать озвучку), без него — фразы "
            "дописываются после прежних. tail_sec — сколько клипа после последнего слова (1.5; финал с логотипом — "
            "3). Ответ: table и lines (начало/конец/длина/speech — где звучат слова), "
            "voice_end, verdict проверки. Долго: ~5–10 с на фразу. Дальше — визуал под lines[].speech, музыка, "
            "auto_mix, render_clip. / Voice-over a clip or canvas from text lines (free Gemini TTS), with subtitles "
            "from the heard words and a check.",
            _obj({"lines": {"type": "array", "items": _LINE, "minItems": 1, "maxItems": 20},
                  "voice": {"type": "string", "description": "голос Gemini (Aoede, Puck, Kore…); нет — рассказчик "
                                                             "проекта"},
                  "provider": {"type": "string", "enum": ["gemini", "polza", "none"],
                               "description": "gemini — бесплатно (по умолчанию), polza — платно (оценка/confirm), "
                                              "none — сухой прогон тоном"},
                  "model": {"type": "string"},
                  "replace": {"type": "boolean", "description": "заменить прежние слои vo_* этого клипа"},
                  "subtitles": {"type": ["boolean", "string"],
                                "description": "auto (по умолчанию) | true — субтитры = текст озвучки | false"},
                  "confirm": {"type": "boolean", "description": "платный provider: синтез по «да» владельца"},
                  "budget_rub": {"type": "number", "minimum": 0},
                  "tail_sec": {"type": "number", "minimum": 0.3, "maximum": 10,
                               "description": "хвост клипа после последнего слова, с (по умолчанию 1.5)"},
                  "style": {"type": "string", "description": "не используется: Gemini зачитывает указание вслух"}},
                 ["lines"]),
            h_add_voiceover, mutating=True, cost=("network", "cpu"),
            label=lambda a: f"Озвучиваю клип: {len(a.get('lines') or [])} фраз",
        ),
        Tool(
            "check_voiceover", "Проверяю озвучку",
            "Анализатор озвучки клипа (ничего не меняет): Whisper каждого слоя-голоса против текста озвучки — лишние "
            "слова в начале (зачитанный style), нет хвоста фразы (обрезан слоем или текстом), доля ошибок по словам "
            "на фразу; слои короче файла («голос vo_1 обрезан: 3.0 из 6.4 с»); тишина внутри голоса > 1.2 с и голос, "
            "не покрывающий клип (последние 30 % молчат); голос против музыки во время речи (нужно ≥ 10–12 дБ) и "
            "голос, учтённый как эффект (нет role voice / не mode track); субтитры, которых нет в голосе, и сдвиг "
            "страниц > 0.4 с. Ответ: clean (нет warn), verdict («озвучка в порядке» / «озвучка с ошибками: …»), lines (что слышно "
            "по каждому слою, wer), findings [{severity, code, message, fix, fix_call {tool, args}, t, layer}], "
            "coverage, levels, subtitles. Тот же вердикт — в lint_clip (check voice) и в отчёте рендера. asr: false — "
            "только по плану (быстро), levels: false — без замера громкости. / Voice-over analyzer: heard vs "
            "written, clipped layers, gaps, levels, subtitle sync.",
            _obj({"asr": {"type": "boolean", "description": "слушать слои Whisper-ом (по умолчанию да)"},
                  "levels": {"type": "boolean", "description": "мерить голос против музыки (по умолчанию да)"}}),
            h_check_voiceover, cost=("cpu",),
            label=lambda a: "Проверяю озвучку",
        ),
    ]
