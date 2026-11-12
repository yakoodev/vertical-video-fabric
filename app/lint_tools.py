"""MCP-инструмент ``lint_clip`` — линт качества клипа перед сдачей (app/quality_lint.py).

Регистрируется в общем реестре (``agent_tools.registry``) и в базовом наборе чата
(``app/chat_tools.BASE``): проверка ничего не меняет и нужна перед каждой сдачей.
"""

from __future__ import annotations

from app import quality_lint as Q
from app.agent_tools import Tool, ToolContext, ToolError, _obj

MAX_FINDINGS = 40


def h_lint_clip(ctx: ToolContext, args: dict) -> dict:
    try:
        result = Q.lint_clip(ctx.store, ctx.plan_id, checks=args.get("checks"), platforms=args.get("platforms"),
                             media=args.get("media", "auto"))
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    findings = result["findings"]
    if len(findings) > MAX_FINDINGS:
        result["findings"] = findings[:MAX_FINDINGS]
        result["truncated"] = len(findings) - MAX_FINDINGS
    result["note"] = ("Только предупреждения — сдаче не мешают. t — шкала clip (view_frame(t), перемотка в редакторе), "
                      "final_t — секунды готового ролика. Поправь warn (fix — как), info — на твоё усмотрение; "
                      "вспышки и луп — по последнему рендеру: после правок перерендерь (render_clip → render_status) и проверь снова.")
    return result


def tools() -> list[Tool]:
    return [
        Tool(
            "lint_clip", "Проверяю клип перед сдачей",
            "Линт качества ПЕРЕД СДАЧЕЙ (submit_for_approval) — только предупреждения, ничего не меняет. "
            "checks (по умолчанию все): ui_zones — текст, субтитры, плашка канала, стикеры под колонкой кнопок "
            "справа и в подписи снизу TikTok/Reels/Shorts (у Reels подпись выше; только 9:16), сколько px, на какой "
            "секунде и как поправить; reading — титры и страницы субтитров успевают прочитать (≥ 0.3 с/слово + 0.5 с, "
            "знаков/с; с учётом вырезов, скорости кусков и J/L); flashes — вспышки готового рендера > 3 в секунду и "
            "красные (безопасность фоточувствительных), диапазоны времени; loop — стык конца с началом (оценка 0..1 по "
            "кадру и звуку) и подсказки (set_end_hold(0), подрезать хвост); audio_mix — сведение звука: музыка громче нормы "
            "под голосом, звуки поверх слов, громкость ролика и пик (по стемам; подробно — audio_mix_report, "
            "исправляет auto_mix); montage — ч/б (fx grayscale) до конца клипа или дольше 2.5 с (цвет не вернётся), одна "
            "и та же вставка дважды; fragments — обрывки после вырезов: кусок короче 2.5 с или без трёх слов "
            "(поправить — apply_cuts с merge_short_islands); pieces и short в ответе — сколько кусков и обрывков. "
            "sub_sync — страницы субтитров против распознанных слов того же звука (раньше своих слов больше 0.6 с, позже "
            "больше 0.7 с, конец уплыл, слов не слышно; первый запуск на клип распознаёт речь, дольше) и против VAD; "
            "by_text в ответе — сверялся ли текст. "
            "platforms — tiktok|reels|shorts (или "
            "youtube|instagram; пусто — площадки проекта). media — auto (вспышки и луп, если есть готовый рендер; рендер — render_clip) | "
            "true | false (только план, мгновенно). Ответ: findings [{check, severity warn|info, message, fix, t, "
            "final_t, layer, platforms}], counts, skipped. / Pre-publish quality lint (platform UI zones, reading speed, "
            "flashes, loop).",
            _obj({"checks": {"type": "array", "items": {"type": "string", "enum": list(Q.CHECKS)}},
                  "platforms": {"type": "array", "items": {"type": "string",
                                                           "enum": ["tiktok", "reels", "shorts", "youtube", "instagram"]}},
                  "media": {"type": ["boolean", "string"],
                            "description": "auto (по умолчанию) — по готовому рендеру, если он есть; false — только план"}}),
            h_lint_clip,
            label=lambda a: "Проверяю клип: " + ", ".join(a.get("checks") or ["всё"]) if a.get("checks") else
            "Проверяю клип перед сдачей",
        ),
    ]
