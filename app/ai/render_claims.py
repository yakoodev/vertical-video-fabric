"""Проверка ответа агента: «отрендерено» и «готово» только при настоящем рендере.

Эксперимент 12.11: агенты чата писали владельцу «Клип уже отрендерен на 45 секунд» (B2) и «рендер в сервисе соберётся
автоматически» (B1), а рендера не было. Владелец, читающий только чат, верит. Теперь в конце хода ответ сверяется с
сервисом: если он говорит о рендере или готовом ролике, а у клипов, которые агент трогал в этом ходе, нет свежего
рендера (готового после последней правки), к ответу добавляется короткая пометка сервиса — что рендера нет и как его
запустить (render_clip → render_status), или что рендер ещё идёт.
"""

from __future__ import annotations

import re
from typing import Any

# «отрендерен», «рендер готов», «клип готов», «готов к публикации/заливу», «готовый ролик», «mp4 готов», «rendered».
CLAIM_RE = re.compile(
    r"(отрендер|срендер|рендер\w*\s+(?:готов|сделан|собран|прош[её]л|завершен|заверш[её]н)|"
    r"(?:клип|ролик|видео|mp4|файл)\w*\s+(?:уже\s+)?(?:полностью\s+)?(?:готов|собран\w*\s+и\s+отрендер)|"
    r"готов\w*\s+(?:к|для)\s+(?:публикац|залив|выкладк|загрузк|отправк)|готов(?:ый|ое)\s+(?:ролик|клип|видео|mp4)|"
    r"\brendered\b|render(?:ing)?\s+(?:is\s+)?(?:done|ready|complete))",
    re.IGNORECASE,
)
# Отрицания рядом — это честное «не отрендерил», а не утверждение.
NEGATION_RE = re.compile(r"(не\s+(?:от|с)?рендер|не\s+готов|рендера\s+нет|нет\s+рендера|без\s+рендера|"
                         r"не\s+запуска|render_clip|осталось\s+отрендер|нужно\s+отрендер|надо\s+отрендер|"
                         r"запусти\w*\s+рендер|not\s+rendered)", re.IGNORECASE)
MUTATING_HINT = ("create_clip", "create_canvas_clip")


def claims_render(text: str) -> bool:
    """Ответ утверждает, что ролик отрендерен или готов (и не оговаривается, что рендера нет)."""
    for sentence in re.split(r"(?<=[.!?\n])\s+", str(text or "")):
        if CLAIM_RE.search(sentence) and not NEGATION_RE.search(sentence):
            return True
    return False


def touched_clips(steps: list[dict]) -> tuple[list[int], list[int]]:
    """(клипы, которые агент менял или создал в ходе; клипы, которые он только смотрел)."""
    from app import chat_tools as CT

    tools = CT.all_tools()
    changed: list[int] = []
    seen: list[int] = []
    for step in steps or []:
        if not isinstance(step, dict) or step.get("kind") not in ("tool", "confirm"):
            continue
        clip = step.get("clip_id")
        preview = step.get("preview") or {}
        if clip is None and preview.get("kind") == "clip":
            clip = preview.get("clip_plan_id")
        try:
            clip = int(clip) if clip is not None else None
        except (TypeError, ValueError):
            clip = None
        if clip is None:
            continue
        tool = tools.get(str(step.get("tool") or ""))
        if step.get("ok") and (step.get("tool") in MUTATING_HINT or (tool is not None and tool.mutating)):
            changed.append(clip)
        seen.append(clip)
    return list(dict.fromkeys(changed)), list(dict.fromkeys(seen))


def render_state(store: Any, clip_plan_id: int, since: str | None = None) -> str:
    """``fresh`` — есть готовый рендер (при ``since`` — сделанный не раньше: клип правили в этом ходе, старый рендер
    уже не тот); ``running`` — рендер идёт; ``stale`` — готовый рендер старше ``since``; ``none`` — рендера нет.

    Почему не ``updated_at`` клипа: рендер сам потом пишет статус клипа и сдвигает его — свежий рендер выглядел бы
    старым."""
    from app import render_jobs as RJ

    if RJ.active_for(int(clip_plan_id)) is not None:
        return "running"
    stale = False
    for clip in store.list_clips_for_plan(int(clip_plan_id)):
        if clip.get("status") in ("queued", "rendering"):
            return "running"
        if clip.get("status") == "succeeded":
            made = str(clip.get("created_at") or "")
            if since is None or made >= since:
                return "fresh"
            stale = True
    return "stale" if stale else "none"


def render_note(store: Any, text: str, steps: list[dict], since: str | None = None) -> str:
    """Пометка сервиса к ответу («рендера нет — render_clip») или пусто: ответ не говорит о готовом ролике, клипов в
    ходе не было или рендер есть. ``since`` — начало хода (время базы): правленому в ходе клипу нужен рендер не раньше."""
    if not claims_render(text):
        return ""
    changed, seen = touched_clips(steps)
    clips = changed or seen
    if not clips:
        return ""
    states = {clip: render_state(store, clip, since if clip in changed else None) for clip in clips[:6]}
    missing = [c for c, s in states.items() if s in ("none", "stale")]
    running = [c for c, s in states.items() if s == "running"]
    parts = []
    if missing:
        names = ", ".join(f"#{c}" for c in missing)
        parts.append(f"рендера нет ({names}): готового mp4 после последних правок нет — render_clip(clip_id), потом "
                     "render_status")
    if running:
        parts.append("рендер ещё идёт (" + ", ".join(f"#{c}" for c in running) + ") — готово, когда render_status "
                     "скажет done")
    return ("⚠ Сервис: " + "; ".join(parts) + ".") if parts else ""


def db_now(store: Any) -> str | None:
    """Сейчас по часам базы (тот же формат, что created_at рендеров)."""
    try:
        return str(store.db.query_one("SELECT CURRENT_TIMESTAMP AS now")["now"])
    except Exception:  # noqa: BLE001
        return None


def with_render_note(store: Any, text: str, steps: list[dict], since: str | None = None) -> str:
    try:
        note = render_note(store, text, steps, since)
    except Exception:  # noqa: BLE001 - проверка не должна ронять ответ
        return text
    return f"{text.rstrip()}\n\n{note}" if note else text
