"""Инструмент агента ``meme_edit`` (app/meme_edit.py): мем-эдит игры одним вызовом — два новых клипа (9:16 и 16:9)
из записи, музыки и одобренных видеомемов. Входит в общий реестр (``app/agent_tools.py``) — у встроенного монтажёра
(источник — его клипа) и в MCP."""

from __future__ import annotations

from app.tool_core import _num, _obj, _source_or_clip_source, SOURCE_ARGS, Tool, ToolContext, ToolError

_EVENT = _obj({"t": {"type": "number", "description": "секунда исходника"},
               "kind": {"type": "string", "description": "knock, kill, death, squad_wipe, win, level_up, max_level, "
                                                         "shot_burst, reaction, ult, teammate_fail"},
               "score": {"type": "number"}, "detail": {"type": "string"}}, ["t", "kind"])


def h_meme_edit(ctx: ToolContext, args: dict) -> dict:
    from app.meme_edit import ASPECTS, build_meme_edit, plan_meme_edit

    from app import stream_questions as Q

    source_id = _source_or_clip_source(ctx, args)
    refusal = Q.montage_refusal(ctx.store, source_id, None)  # «сначала вопросы, потом монтаж»
    if refusal:
        return refusal
    aspects = args.get("aspects") or list(ASPECTS)
    if not isinstance(aspects, list) or not all(a in ("9:16", "16:9", "1:1", "4:5") for a in aspects):
        raise ToolError("aspects — список форматов: 9:16, 16:9 (по умолчанию оба)")
    options = dict(music_track_id=args.get("music_track_id"), music_asset_id=args.get("music_asset_id"),
                   target_sec=_num(args.get("target_sec"), 40.0), events=args.get("events"),
                   seed=int(_num(args.get("seed"), 0) or 0), words=int(_num(args.get("words"), 3) or 0),
                   subtitles=args.get("subtitles") is not False)
    try:
        if args.get("dry_run"):
            spec = plan_meme_edit(ctx.store, source_id, aspect=aspects[0], **options)
            return {"ok": True, "dry_run": True, "spec": spec,
                    "note": "только план: клипы не созданы (без dry_run — два клипа и рендер)"}
        out = build_meme_edit(ctx.store, source_id, aspects=aspects, render=args.get("render") is not False,
                              **options)
    except (KeyError, ValueError) as exc:
        raise ToolError(f"мем-эдит не собран: {exc}") from exc
    out["next"] = ("render_status(render_id) у каждого клипа; потом contact_sheet / listen — проверь врезки мемов и "
                   "слова; правки — обычными инструментами клипа (place_video_meme, add_layers, apply_cuts)")
    return out


def tools() -> list[Tool]:
    return [
        Tool(
            "meme_edit", "Собираю мем-эдит",
            "Мем-эдит игры одним вызовом: геймплей рубится по долям музыки (0.5–1.5 с), на каждом моменте (нок, килл, "
            "смерть, реакция в микрофон) — врезка видеомема во весь кадр со своим звуком (только одобренные, без "
            "повторов, по смыслу: килл → победа/шок, смерть → фейл/грусть, реакция → смех), наезд с тряской и вспышка "
            "на попадании, синий негатив на главном моменте, 2–4 мультяшных жёлтых слова, музыка подложкой, шумодав "
            "и мат серединой слова на микрофоне, субтитры только на фразах. Создаёт НОВЫЕ клипы: 9:16 и 16:9 из "
            "одного монтажа (aspects), рендер заданиями (render: false — без рендера). source_id — запись (у "
            "встроенного монтажёра — источник клипа); music_track_id (list_music) или music_asset_id (звук из "
            "библиотеки), иначе трек ближе к 128 BPM; target_sec 15–90 (40); events — свои события [{t, kind}] "
            "(секунды исходника), иначе детектор игры / по звуку; seed — другой вариант; dry_run — только план. "
            "/ One-call gaming meme edit (beat-cut gameplay + reaction video memes), vertical and horizontal.",
            _obj({**SOURCE_ARGS,
                  "music_track_id": {"type": "integer"}, "music_asset_id": {"type": "integer"},
                  "target_sec": {"type": "number", "minimum": 15, "maximum": 90},
                  "aspects": {"type": "array", "items": {"type": "string", "enum": ["9:16", "16:9", "1:1", "4:5"]}},
                  "events": {"type": "array", "items": _EVENT}, "seed": {"type": "integer"},
                  "words": {"type": "integer", "minimum": 0, "maximum": 4},
                  "subtitles": {"type": "boolean"}, "render": {"type": "boolean"}, "dry_run": {"type": "boolean"}}),
            h_meme_edit, scope="global", mutating=True, cost=("cpu",),
            label=lambda a: "Собираю мем-эдит" + (f" из записи {a.get('source_id')}" if a.get("source_id") else ""),
        ),
    ]
