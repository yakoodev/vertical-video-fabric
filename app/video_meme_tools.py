"""Инструменты агента для «Видеомемов» (app/video_memes.py): найти реакцию по смыслу, поставить её в клип,
дописать описание/теги. Входят в общий реестр (``app/agent_tools.py``) — у встроенного монтажёра и в MCP.

Ставится мем обычной вставкой (``add_insert`` → ``render.inserts``): ``full`` — врезка во весь кадр со своим звуком
(звук клипа выключен — ``game_audio: cut``, по умолчанию, или приглушён — ``duck``), ``pip`` — окошко поверх клипа.
Неодобренный владельцем мем не ставится (отказ здесь и в ``add_insert``).
"""

from __future__ import annotations

from app import video_memes as VM
from app.layer_tools import _AT_WORD_SCHEMA
from app.montage_assets import DUCK_VOLUME, FULL_FILLS
from app.tool_core import _num, _obj, Tool, ToolContext, ToolError

GAME_AUDIO = {"cut": (True, 0.0), "duck": (True, DUCK_VOLUME), "keep": (False, None)}


def _meme(ctx: ToolContext, args: dict) -> dict:
    try:
        return VM.get_meme(ctx.store, int(args.get("meme_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("нет такого видеомема (search_video_memes)") from exc


def h_search_video_memes(ctx: ToolContext, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    include_unapproved = bool(args.get("include_unapproved"))
    found = VM.search_memes(ctx.store, query, tag=args.get("tag") or None,
                            approved=None if include_unapproved else True,
                            max_sec=_num(args.get("max_sec")), limit=int(_num(args.get("limit"), 8) or 8))
    out: dict = {"memes": [VM.brief(m) for m in found], "count": len(found)}
    if not found:
        waiting = len(VM.list_memes(ctx.store, approved=False))
        out["note"] = ("ничего не нашлось" + (f"; ещё {waiting} мем(ов) ждут одобрения владельца — их ставить нельзя"
                                              if waiting and not include_unapproved else "")
                       + ". Другие слова (шок, фейл, победа, кринж, смех, злость…) или tag")
    if include_unapproved:
        out["rule"] = "approved=false — владелец не одобрил: place_video_meme откажет"
    return out


def h_place_video_meme(ctx: ToolContext, args: dict) -> dict:
    from app.cut_tools_pieces import h_add_insert

    meme = _meme(ctx, args)
    if not meme["approved"]:
        raise ToolError(f"видеомем #{meme['id']} «{meme['title']}» владелец ещё не одобрил — ставить нельзя")
    mode = args.get("mode") or "full"
    if mode not in ("full", "pip"):
        raise ToolError("mode: full (врезка во весь кадр) или pip (окошко)")
    game_audio = args.get("game_audio") or ("cut" if mode == "full" else "duck")
    if game_audio not in GAME_AUDIO:
        raise ToolError("game_audio: cut (звук клипа выключен), duck (приглушён) или keep")
    full_sec = meme["duration_sec"] or 1.5
    duration = _num(args.get("duration"), full_sec) or full_sec
    cap = VM.MEME_FULL_MAX_SEC if mode == "full" else VM.MAX_MEME_SEC
    duration = round(min(duration, full_sec, cap), 2)
    duck, duck_volume = GAME_AUDIO[game_audio]
    insert = {k: args[k] for k in ("at", "at_word", "word_offset", "x", "y", "scale", "volume", "reason")
              if args.get(k) is not None}
    if args.get("fill") in FULL_FILLS:
        insert["fill"] = args["fill"]
    insert.update(asset_id=meme["asset_id"], mode=mode, duration=duration, duck=duck,
                  reason=insert.get("reason") or f"видеомем «{meme['title']}»")
    if meme["has_audio"]:
        insert["level"] = "speech"  # реакция говорит вместо клипа — вровень с голосом, не «эффект под ним»
    if duck_volume is not None:
        insert["duck_volume"] = duck_volume
    out = h_add_insert(ctx, insert)
    out["meme"] = {"meme_id": meme["id"], "title": meme["title"], "duration": duration, "mode": mode,
                   "game_audio": game_audio}
    if meme["duration_sec"] and duration < meme["duration_sec"] - 0.05:
        out.setdefault("warnings", []).append(
            f"мем {meme['duration_sec']:g} с обрезан до {duration:g} с — проверь, что реакция не оборвалась (listen)")
    return out


def h_describe_video_meme(ctx: ToolContext, args: dict) -> dict:
    meme = _meme(ctx, args)
    fields = {k: args[k] for k in ("title", "description", "when_to_use", "tags", "has_real_people", "transcript")
              if args.get(k) is not None}
    if not fields:
        raise ToolError("нечего менять: title, description, when_to_use, tags, has_real_people или transcript")
    fresh = VM.update_meme(ctx.store, int(meme["id"]), **fields)
    return {"ok": True, "meme": VM.brief(fresh)}


def tools() -> list[Tool]:
    return [
        Tool(
            "search_video_memes", "Ищу видеомем",
            "Видеомемы — короткие реакции (0.5–4 с: человек орёт «вау», молится, кричит, ржёт) для мем-монтажа игры "
            "и стрима. Поиск по смыслу: query — слова (что происходит в клипе: «фейл», «победа», «тиммейт подвёл», "
            "«шок»), ищутся в тегах, «когда вставлять», описании и расшифровке; tag — один тег; max_sec — не длиннее. "
            "По умолчанию только одобренные владельцем (только их можно ставить). Ответ: meme_id, длительность, "
            "что значит, когда вставлять, что сказано (transcript), теги, has_real_people. Ставить — place_video_meme. "
            "/ Search the owner's reaction video memes by meaning.",
            _obj({"query": {"type": "string"}, "tag": {"type": "string"},
                  "max_sec": {"type": "number"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                  "include_unapproved": {"type": "boolean",
                                         "description": "показать и неодобренные (ставить их нельзя)"}}),
            h_search_video_memes, scope="global",
            label=lambda a: f"Ищу видеомем «{str(a.get('query') or a.get('tag') or '')[:30]}»",
        ),
        Tool(
            "place_video_meme", "Вставляю видеомем",
            "Поставить видеомем в клип в момент at (шкала clip) или на слово at_word. mode full (по умолчанию) — "
            "врезка во весь кадр: картинка клипа заменена мемом на его длительность (≤ 4 с), звучит мем, звук клипа "
            "выключен (game_audio cut, по умолчанию) или приглушён (duck); другой пропорции — fill blur (размытая "
            "копия, по умолчанию) или cover (обрезать во весь кадр). mode pip — окошко поверх клипа (x, y, scale), звук "
            "клипа приглушён (duck). duration — короче мема (по умолчанию весь мем). Только одобренные мемы. "
            "Ставь на сам момент (выстрел, смерть, победа, фраза), не раньше; проверь view_frame/listen. "
            "/ Cut a reaction meme into the clip (full-frame cut-in or picture-in-picture).",
            _obj({"meme_id": {"type": "integer"}, "at": {"type": "number"}, "at_word": _AT_WORD_SCHEMA,
                  "word_offset": {"type": "number"},
                  "mode": {"type": "string", "enum": ["full", "pip"]},
                  "duration": {"type": "number"},
                  "game_audio": {"type": "string", "enum": list(GAME_AUDIO)},
                  "fill": {"type": "string", "enum": ["blur", "cover", "black"]},
                  "x": {"type": "number"}, "y": {"type": "number"}, "scale": {"type": "number"},
                  "volume": {"type": "number", "description": "громкость мема ×, по умолчанию 1 (вровень с голосом)"},
                  "reason": {"type": "string"}}, ["meme_id"]),
            h_place_video_meme, mutating=True,
            label=lambda a: f"Вставляю видеомем #{a.get('meme_id')}" + (
                f" на {(_num(a.get('at'), 0.0) or 0.0):.1f} с" if a.get("at") is not None else ""),
        ),
        Tool(
            "describe_video_meme", "Описываю видеомем",
            "Дописать видеомему смысл: title, description (что значит / что происходит), when_to_use (когда "
            "вставлять), tags (список: шок, фейл, победа, кринж, смех, злость, тиммейт…), has_real_people, "
            "transcript (если Whisper ошибся). Одобрить мем может только владелец. / Edit a video meme's meaning.",
            _obj({"meme_id": {"type": "integer"}, "title": {"type": "string"}, "description": {"type": "string"},
                  "when_to_use": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
                  "has_real_people": {"type": "boolean"}, "transcript": {"type": "string"}}, ["meme_id"]),
            h_describe_video_meme, scope="global",
            label=lambda a: f"Описываю видеомем #{a.get('meme_id')}",
        ),
    ]
