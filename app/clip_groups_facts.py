"""Группы клипов: имена, факты из базы, предложение групп (из ``app/clip_groups.py``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.formats import DEFAULT_FORMAT, normalize_format

# Близкое начало: момент тот же, если первые кадры рядом…
CLOSE_START_SEC = 60.0
# …или версии делят материал: новая версия могла получить холодное открытие из другого места стрима.
SHARED_MATERIAL = 0.3
# Разные названия, но почти один и тот же материал: возможно, переименованная версия. Только в отчёт.
ALMOST_SAME_MATERIAL = 0.6


_VERSION = re.compile(r"\bv\s?(\d+)\b", re.I)
_PARENS = re.compile(r"\([^)]*\)")
_NUMBERED = re.compile(r"№\s*\d+")
# Служебная приставка перед названием: «Мура v3:», «B31 v3:», «apexA 47:», «v3:», «QA Gemini 9:», «Стрим:».
_PREFIX = re.compile(
    r"^\s*(?:v\d+|qa gemini(?:\s*\d+)?|проверка gemini(?:\s*\d+)?|стрим|apex|мура(?:\s+v\d+)?|"
    r"apexa\s*\d+(?:\s+v\d+)?|b\d+(?:\s+v\d+)?)\s*(?::|—|-)\s*",
    re.I,
)


def version_mark(title: Any) -> int | None:
    """Номер версии из названия: «Мура v3: …» → 3."""
    match = _VERSION.search(str(title or ""))
    return int(match.group(1)) if match else None


def name_key(title: Any) -> str:
    """Название без служебного: номера версии, приставки до двоеточия, скобок, знаков препинания.
    Части стрима («Мура Minecraft 1/4») остаются разными: дробь сохраняется."""
    text = str(title or "").lower()
    text = _VERSION.sub(" ", text)
    text = _PARENS.sub(" ", text)
    text = _NUMBERED.sub(" ", text)
    if ":" in text:
        tail = text.split(":", 1)[1]
        if re.sub(r"[^\w]", "", tail):
            text = tail
    return " ".join(re.sub(r"[^\w/]+", " ", text).split())


def display_name(title: Any) -> str:
    """Имя клипа для человека: «Мура v3: мать года» → «Мать года». Само название плана не меняется."""
    text = str(title or "").strip()
    while True:
        cut = _PREFIX.sub("", text, count=1).strip()
        if cut == text:
            break
        text = cut
    text = _PARENS.sub(" ", text)
    text = _NUMBERED.sub(" ", text)
    text = " ".join(_VERSION.sub(" ", text).split()).strip(" —-:")
    if not text:
        text = str(title or "").strip()
    return (text[:1].upper() + text[1:]) if text else "Без названия"


def _size_format(width: Any, height: Any) -> str | None:
    from app.publish_format import video_format

    return video_format(width, height)


@dataclass
class PlanFact:
    """Всё, что нужно группировке и сетке об одном плане."""

    id: int
    source_id: int
    title: str
    key: str
    fmt: str
    # Куски в секундах исходника.
    pieces: list[tuple[float, float]] = field(default_factory=list)
    renders: int = 0
    group: int | None = None
    locked: bool = False
    hidden: bool = False
    # Источник-контейнер (холсты всех проектов лежат в одном скрытом источнике): сравниваем внутри проекта.
    scope: tuple = ()
    project_id: int | None = None
    created_at: str = ""

    @property
    def start(self) -> float:
        return min((a for a, _ in self.pieces), default=0.0)

    @property
    def end(self) -> float:
        return max((b for _, b in self.pieces), default=0.0)

    @property
    def total(self) -> float:
        return sum(max(0.0, b - a) for a, b in self.pieces)


def shared_fraction(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    """Какая доля более короткого плана есть и в другом."""
    total_a = sum(max(0.0, y - x) for x, y in a)
    total_b = sum(max(0.0, y - x) for x, y in b)
    shorter = min(total_a, total_b)
    if shorter <= 0:
        return 0.0
    common = 0.0
    for x0, y0 in a:
        for x1, y1 in b:
            common += max(0.0, min(y0, y1) - max(x0, x1))
    return min(1.0, common / shorter)


def same_moment(a: PlanFact, b: PlanFact) -> bool:
    return abs(a.start - b.start) <= CLOSE_START_SEC or shared_fraction(a.pieces, b.pieces) >= SHARED_MATERIAL


# ---- факты из базы -----------------------------------------------------------------------------


Query = Callable[[str, tuple], Iterable[Any]]


def _columns(query: Query, table: str) -> set[str]:
    return {str(row["name"]) for row in query(f"PRAGMA table_info({table})", ())}


# Формат из настроек плана; битый JSON не должен ронять запрос.
_FMT_SQL = (
    "CASE WHEN json_valid(cp.render_settings_json) THEN COALESCE(json_extract(cp.render_settings_json, '$.format'), "
    "json_extract(cp.render_settings_json, '$.aspect')) END"
)


def load_facts(query: Query, source_id: int | None = None) -> list[PlanFact]:
    """Планы всей базы или одного источника. Работает и на базе без колонок группы (копия с боя до миграции)."""
    cols = _columns(query, "clip_plans")
    only_plans = "" if source_id is None else f" WHERE x.clip_plan_id IN (SELECT id FROM clip_plans WHERE source_id = {int(source_id)})"
    only_clips = "" if source_id is None else f" AND source_id = {int(source_id)}"
    only_source = "" if source_id is None else f"WHERE cp.source_id = {int(source_id)}"
    group_cols = (
        "cp.clip_group_id AS grp, cp.group_locked AS locked"
        if "clip_group_id" in cols
        else "NULL AS grp, 0 AS locked"
    )
    pieces: dict[int, list[tuple[float, float]]] = {}
    for row in query(
        "SELECT x.clip_plan_id AS plan, a.start_sec AS s, a.end_sec AS e FROM clip_plan_segments x "
        f"JOIN ai_segments a ON a.id = x.segment_id{only_plans} ORDER BY x.clip_plan_id, x.sort_order",
        (),
    ):
        pieces.setdefault(int(row["plan"]), []).append((float(row["s"]), float(row["e"])))
    renders: dict[int, dict] = {}
    for row in query(
        "SELECT clip_plan_id AS plan, width, height FROM clips "
        f"WHERE status = 'succeeded' AND clip_plan_id IS NOT NULL{only_clips} ORDER BY id",
        (),
    ):
        item = renders.setdefault(int(row["plan"]), {"n": 0})
        item["n"] += 1
        item["w"], item["h"] = row["width"], row["height"]
    assigned: dict[int, int] = {
        int(row["clip_plan_id"]): int(row["project_id"])
        for row in query("SELECT clip_plan_id, project_id FROM plan_projects", ())
    }
    facts: list[PlanFact] = []
    for row in query(
        f"""
        SELECT cp.id, cp.source_id, cp.title, cp.hidden, cp.created_at, s.source_type, s.project_id AS source_project,
               {_FMT_SQL} AS fmt,
               {group_cols}
        FROM clip_plans cp JOIN sources s ON s.id = cp.source_id
        {only_source}
        ORDER BY cp.id
        """,
        (),
    ):
        plan_id = int(row["id"])
        done = renders.get(plan_id) or {}
        fmt = normalize_format(row["fmt"]) if row["fmt"] else None
        fmt = fmt or _size_format(done.get("w"), done.get("h")) or DEFAULT_FORMAT
        canvas = str(row["source_type"]) == "canvas"
        project = assigned.get(plan_id) if canvas else row["source_project"]
        facts.append(PlanFact(
            id=plan_id,
            source_id=int(row["source_id"]),
            title=str(row["title"] or ""),
            key=name_key(row["title"]),
            fmt=fmt,
            pieces=pieces.get(plan_id, []),
            renders=int(done.get("n") or 0),
            group=int(row["grp"]) if row["grp"] is not None else None,
            locked=bool(row["locked"]),
            hidden=bool(row["hidden"]),
            scope=("canvas", project) if canvas else ("source", int(row["source_id"])),
            project_id=int(project) if project is not None else None,
            created_at=str(row["created_at"] or ""),
        ))
    return facts


# ---- предложение групп (скрипт и автопривязка) --------------------------------------------------


@dataclass
class Proposal:
    """Что думаем о базе: группы с уверенностью и то, что осталось на усмотрение владельца."""

    groups: list[list[PlanFact]]
    # Одно название, один формат и видео, но кадры далеко друг от друга.
    far_apart: list[list[PlanFact]]
    # Почти один материал, другие названия: возможно, переименованная версия.
    renamed: list[tuple[PlanFact, PlanFact]]
    # Одно название и формат в разных видео проекта (Apex: перезалитая запись).
    other_video: list[list[PlanFact]]
    # Один момент в другом формате: разные клипы, связаны перекрёстной ссылкой.
    other_format: list[list[PlanFact]]
    locked: int = 0

    @property
    def unclear(self) -> int:
        return len(self.far_apart) + len(self.renamed) + len(self.other_video)


def _clusters(plans: list[PlanFact]) -> list[list[PlanFact]]:
    parent = {p.id: p.id for p in plans}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(plans):
        for b in plans[i + 1:]:
            if same_moment(a, b):
                parent[find(a.id)] = find(b.id)
    out: dict[int, list[PlanFact]] = {}
    for plan in plans:
        out.setdefault(find(plan.id), []).append(plan)
    return list(out.values())


def propose(facts: list[PlanFact]) -> Proposal:
    """Группируем только уверенное: один источник, один формат, одно название, близкое начало."""
    buckets: dict[tuple, list[PlanFact]] = {}
    locked = 0
    for fact in facts:
        if fact.locked:
            locked += 1
            continue
        if fact.key and fact.pieces:
            buckets.setdefault((fact.scope, fact.fmt, fact.key), []).append(fact)
    groups: list[list[PlanFact]] = []
    far_apart: list[list[PlanFact]] = []
    finals: list[list[PlanFact]] = []
    for plans in buckets.values():
        clusters = _clusters(plans)
        finals += clusters
        groups += [c for c in clusters if len(c) > 1]
        if len(clusters) > 1:
            far_apart.append(sorted((c[0] for c in clusters), key=lambda p: p.start))

    # Отчёт: всё, что остаётся отдельными клипами, но владельцу стоит взглянуть.
    renamed: list[tuple[PlanFact, PlanFact]] = []
    per_place: dict[tuple, list[list[PlanFact]]] = {}
    for cluster in finals:
        per_place.setdefault((cluster[0].scope, cluster[0].fmt), []).append(cluster)
    for clusters in per_place.values():
        for i, first in enumerate(clusters):
            for second in clusters[i + 1:]:
                if first[0].key == second[0].key:
                    continue
                a, b = max(first, key=lambda p: p.id), max(second, key=lambda p: p.id)
                if shared_fraction(a.pieces, b.pieces) >= ALMOST_SAME_MATERIAL:
                    renamed.append((a, b))
    by_project: dict[tuple, dict[int, PlanFact]] = {}
    moments: dict[tuple, dict[str, PlanFact]] = {}
    for fact in facts:
        if not fact.key:
            continue
        if fact.project_id is not None and fact.scope[0] == "source":
            by_project.setdefault((fact.project_id, fact.fmt, fact.key), {}).setdefault(fact.source_id, fact)
        moments.setdefault((fact.scope, fact.key), {}).setdefault(fact.fmt, fact)
    other_video = [list(v.values()) for v in by_project.values() if len(v) > 1]
    other_format = [list(v.values()) for v in moments.values() if len(v) > 1]
    return Proposal(groups=sorted(groups, key=lambda g: g[0].id), far_apart=far_apart, renamed=renamed,
                    other_video=other_video, other_format=other_format, locked=locked)


def plan_group_writes(proposal: Proposal) -> list[tuple[int, int]]:
    """Какие записи (план, номер группы) привести в соответствие. Идемпотентно: у группы, где уже
    есть номер, он сохраняется, и второй прогон ничего не пишет."""
    writes: list[tuple[int, int]] = []
    for group in proposal.groups:
        existing = sorted({p.group for p in group if p.group is not None})
        number = existing[0] if existing else min(p.id for p in group)
        writes += [(p.id, number) for p in group if p.group != number]
    return writes
