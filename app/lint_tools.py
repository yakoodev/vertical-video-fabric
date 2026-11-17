"""MCP-инструмент ``lint_clip`` — линт качества клипа перед сдачей (app/quality_lint.py).

Регистрируется в общем реестре (``agent_tools.registry``) и в базовом наборе чата
(``app/chat_tools.BASE``): проверка ничего не меняет и нужна перед каждой сдачей.
"""

from __future__ import annotations

from app import quality_lint as Q
from app.agent_tools import Tool, ToolContext, ToolError, _obj

MAX_FINDINGS = 40


def h_lint_clip(ctx: ToolContext, args: dict) -> dict:
    assertions = args.get("assertions")
    if assertions is not None and not isinstance(assertions, list):
        raise ToolError("assertions — список строк («X появился к 2.0») или объектов ({appear, by})")
    saved = None
    if assertions is not None and args.get("save_assertions"):
        from app.quality_lint_assert import save_assertions

        saved = str(save_assertions(ctx.plan_id, assertions))
    try:
        result = Q.lint_clip(ctx.store, ctx.plan_id, checks=args.get("checks"), platforms=args.get("platforms"),
                             media=args.get("media", "auto"), assertions=assertions)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if saved:
        result["assertions_saved"] = saved
    findings = result["findings"]
    if len(findings) > MAX_FINDINGS:
        result["findings"] = findings[:MAX_FINDINGS]
        result["truncated"] = len(findings) - MAX_FINDINGS
    result["note"] = ("Только предупреждения — сдаче не мешают, но P0 — брак: сдавать нельзя; P1 — поправь до сдачи; "
                      "P2 — шлифовка. lines — те же находки строками «P · t=… · где · что не так → что поменять» "
                      "(t в строке — секунды готового ролика): владельцу — их. "
                      "t в находке — шкала clip (view_frame(t), перемотка в редакторе), "
                      "final_t — секунды готового ролика. Поправь warn (fix — как), info — на твоё усмотрение; "
                      "вспышки, застывшие куски и луп — по последнему рендеру: после правок перерендерь (render_clip → render_status) и проверь снова.")
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
            "красные (безопасность фоточувствительных), диапазоны времени; freezes — застывшие куски готового рендера от "
            "0.6 с («клип остановился»: почти-стоп-кадр под текст, слоу-мо до стопа, запись с неверным fps), титры, "
            "субтитры и зерно не мешают, нарочные freeze_frame и set_end_hold пропускаются; loop — стык конца с началом (оценка 0..1 по "
            "кадру и звуку) и подсказки (set_end_hold(0), подрезать хвост); audio_mix — сведение звука: музыка громче нормы "
            "под голосом, звуки поверх слов, громкость ролика и пик (по стемам; подробно — audio_mix_report, "
            "исправляет auto_mix); montage — ч/б (fx grayscale) до конца клипа или дольше 2.5 с (цвет не вернётся), одна "
            "и та же вставка дважды; fragments — обрывки после вырезов: кусок короче 2.5 с или без трёх слов "
            "(поправить — apply_cuts с merge_short_islands); pieces и short в ответе — сколько кусков и обрывков. "
            "sub_sync — страницы субтитров против распознанных слов того же звука (раньше своих слов больше 0.6 с, позже "
            "больше 0.7 с, конец уплыл, слов не слышно; первый запуск на клип распознаёт речь, дольше) и против VAD; "
            "by_text в ответе — сверялся ли текст. voice — озвучка, как check_voiceover: слышно ли написанное (лишнее "
            "в начале — зачитанный style, нет хвоста), голос короче файла («голос vo_1 обрезан: 3.0 из 6.4 с»), "
            "тишина > 1.2 с и молчащий конец, голос над музыкой ≥ 10–12 дБ, голос учтён как эффект, субтитры мимо "
            "голоса; media false — только обрезка и роль слоя. "
            "ПЕРЕД СДАЧЕЙ: frame0 — кадр 0 (обложка и крючок): пустой/чёрный (P0), только логотип, крючок ещё не вошёл "
            "(вход с нуля — на обложке его нет); dead — больше 3 с без перемены (по плану; у видео основы сверка с рендером); "
            "hold — после последнего входа кадр держится 1–1.5 с до склейки («встало и сразу ушло»); outro — концовка "
            "≤ 1.4 с и ≤ 25 % ролика; subject — главный объект графической сцены ≥ 1/3 высоты кадра (не геймплей); "
            "slide_chrome — «слайдовая обвязка»: счётчики «02/05», полосы прогресса, подписи в углах, печатная машинка "
            "как украшение; zero_scale — ничего не растёт из нуля (pop/spin/words, ключ scale с 0 → въезд от края или "
            "«оседание» 0.94→1); entrances — один и тот же вход слов (focus, colorize, slot, letters, scramble, glitch, "
            "rise) в одном ролике дважды; assertions — проверки клипа: assertions [строки «X появился к 2.0», "
            "«A раньше B», «X не выходит за кадр», «сцена не стоит 0–10» или {appear, by} / {before: [A, B]} / "
            "{inside} / {moving, from, to, max_still}] — X — id, name или кусок текста слоя; save_assertions=true — "
            "запомнить их за клипом (дальше каждый lint_clip сверяет сам). Склейки глазами — contact_sheet mode "
            "cuts, читаемость на телефоне — contact_sheet mode phone. "
            "platforms — tiktok|reels|shorts (или "
            "youtube|instagram; пусто — площадки проекта). media — auto (вспышки, застывшие куски и луп, если есть готовый "
            "рендер; рендер — render_clip) | "
            "true | false (только план, мгновенно). Ответ: findings [{check, severity warn|info, message, fix, t, "
            "final_t, layer, platforms, priority P0|P1|P2, line}], lines, counts, by_priority, skipped. / Pre-publish "
            "quality lint (platform UI zones, reading speed, flashes, loop, cover frame, dead air, holds, outro, "
            "motion craft, per-clip assertions).",
            _obj({"checks": {"type": "array", "items": {"type": "string", "enum": list(Q.CHECKS)}},
                  "platforms": {"type": "array", "items": {"type": "string",
                                                           "enum": ["tiktok", "reels", "shorts", "youtube", "instagram"]}},
                  "media": {"type": ["boolean", "string"],
                            "description": "auto (по умолчанию) — по готовому рендеру, если он есть; false — только план"},
                  "assertions": {"type": "array", "maxItems": 40,
                                 "items": {"type": ["string", "object"]},
                                 "description": "проверки-утверждения вместо сохранённых у клипа"},
                  "save_assertions": {"type": "boolean",
                                      "description": "запомнить assertions за клипом (файл проверок клипа)"}}),
            h_lint_clip,
            label=lambda a: "Проверяю клип: " + ", ".join(a.get("checks") or ["всё"]) if a.get("checks") else
            "Проверяю клип перед сдачей",
        ),
    ]
