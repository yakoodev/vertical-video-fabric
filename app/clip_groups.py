"""Клип как единица работы: группа планов одного момента и одного формата.

План (``clip_plans``) — монтаж, рендер (``clips``) — готовый файл. Агенты делают новую версию монтажа
новым планом («Мура v2: мать года», «Мура v3: мать года»), поэтому раньше один и тот же клип лежал
в списках тремя карточками. Теперь:

* ``clip_plans.clip_group_id`` — связь версий. NULL — план сам себе клип; иначе номер группы (это id
  плана, с которого группа началась; он не переиспользуется, даже если этот план удалят);
* **клип** адресуется id любого своего плана: ``/clips/931`` и ``/clips/878`` — один экран. Главный план
  (``head``) — самый новый в группе: его правят «Открыть монтаж», чат и откат;
* вертикаль и горизонталь одного момента — **разные** клипы (решение владельца): формат входит в ключ
  группы, а карточка показывает перекрёстную ссылку «этот момент в 9:16»;
* ``clip_plans.group_locked`` — владелец отделил план руками: скрипт и автопривязка его не трогают;
* ``clip_versions`` — вечные снимки монтажа: у каждого нового рендера и перед каждым откатом. История
  правок (``clip_plan_history``) хранит только 20 шагов на план и нужна для Ctrl+Z.

Старые API и MCP этой группы не замечают: у них по-прежнему планы и рендеры.

Разбор названий и предложение групп — чистые функции над строками базы: их зовёт и сервис, и
``tools/group_clip_versions.py``, который читает копию базы без миграции.
"""

from __future__ import annotations

import hashlib
import json
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
# Перекрёстная ссылка «этот момент в 9:16» по материалу: длины отличаются не больше чем вдвое.
MOMENT_LENGTH_RATIO = 2.0

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


# ---- снимок монтажа ------------------------------------------------------------------------------


_TEXT_KEYS = ("title", "description", "notes", "favorite")


def state_fingerprint(state: dict) -> str:
    """Тот же отпечаток, что у рендера (app/quality_lint.plan_fingerprint), для готового снимка."""
    spec = {k: v for k, v in (state.get("spec") or {}).items() if k not in _TEXT_KEYS}
    raw = json.dumps({"spec": spec, "subtitles": state.get("subtitles")}, ensure_ascii=False, sort_keys=True,
                     default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def add_snapshot(store: Any, clip_plan_id: int, state: dict, *, clip_id: int | None, kind: str, label: str = "") -> int:
    cur = store.db.execute(
        "INSERT INTO clip_versions (clip_plan_id, clip_id, kind, label, state_json) VALUES (?, ?, ?, ?, ?)",
        (int(clip_plan_id), clip_id, kind, str(label or "")[:200], json.dumps(state, ensure_ascii=False, sort_keys=True)),
    )
    return int(cur.lastrowid)


def capture_quiet(store: Any, clip_plan_id: int) -> dict | None:
    """Монтаж плана перед рендером. Не сняли — рендер всё равно идёт."""
    try:
        from app.clip_history import capture

        return capture(store, clip_plan_id)
    except Exception:  # noqa: BLE001 - снимок — страховка, рендер важнее
        return None


def snapshot_render(store: Any, clip_plan_id: int, clip_id: int, state: dict | None) -> None:
    """Сервис рендера: запомнить монтаж, с которого сделан файл. Никогда не ломает рендер."""
    if not state:
        return
    try:
        add_snapshot(store, clip_plan_id, state, clip_id=clip_id, kind="render")
    except Exception:  # noqa: BLE001 - снимок — страховка, рендер важнее
        pass


def _history_times(store: Any, plan_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for chunk in _chunks(plan_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"SELECT id, clip_plan_id, label, created_at FROM clip_plan_history WHERE clip_plan_id IN ({marks}) ORDER BY id",
            chunk,
        ):
            out.setdefault(int(row["clip_plan_id"]), []).append(dict(row))
    return out


def history_candidate(history: list[dict], render_created_at: str, depth: int) -> tuple[str, dict | None]:
    """Откуда взять монтаж рендера по истории правок: снимок ПЕРЕД первой правкой после рендера.

    Возвращает («entry», запись) — снимок нашёлся; («current», None) — правок после рендера не было, это
    текущее состояние; («lost», None) — история урезана глубиной и нужного снимка могло не остаться."""
    after = [h for h in history if str(h["created_at"]) > str(render_created_at)]
    if not after:
        return "current", None
    trimmed = len(history) >= depth and len(after) == len(history)
    if trimmed:
        return "lost", None
    return "entry", after[0]


def state_of_render(store: Any, clip: dict) -> dict | None:
    """Монтаж, с которого сделан этот рендер, или None, если снимок не сохранился."""
    from app.clip_history import HISTORY_DEPTH, capture

    plan_id = clip.get("clip_plan_id")
    if not plan_id:
        return None
    row = store.db.query_one(
        "SELECT state_json FROM clip_versions WHERE clip_id = ? ORDER BY id DESC LIMIT 1", (int(clip["id"]),)
    )
    if row:
        return json.loads(row["state_json"])
    rows = [dict(r) for r in store.db.query_all(
        "SELECT id, label, created_at, state_json FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id",
        (int(plan_id),),
    )]
    stamp = clip.get("plan_fingerprint")
    if stamp:
        # У новых рендеров есть отпечаток: снимок истории с тем же отпечатком — точно он.
        for entry in rows:
            try:
                state = json.loads(entry["state_json"])
            except ValueError:
                continue
            if state_fingerprint(state) == stamp:
                return state
    how, entry = history_candidate(rows, str(clip.get("created_at") or ""), HISTORY_DEPTH)
    if how == "current":
        return capture(store, int(plan_id))
    if how == "lost" or entry is None:
        return None
    try:
        return json.loads(entry["state_json"])
    except ValueError:
        return None


def backfill_snapshots(query: Query) -> list[tuple[int, int, str]]:
    """Снимки для старых рендеров из истории правок (скрипт группировки): история хранит 20 шагов и
    со временем их потеряет. Рендеры без правок после них не трогаем: их монтаж и есть текущий."""
    from app.clip_history import HISTORY_DEPTH

    if "clip_group_id" not in _columns(query, "clip_plans"):
        have: set[int] = set()
    else:
        have = {int(r["clip_id"]) for r in query("SELECT clip_id FROM clip_versions WHERE clip_id IS NOT NULL", ())}
    history: dict[int, list[dict]] = {}
    for row in query("SELECT id, clip_plan_id, label, created_at, state_json FROM clip_plan_history ORDER BY id", ()):
        history.setdefault(int(row["clip_plan_id"]), []).append(dict(row))
    out: list[tuple[int, int, str]] = []
    for row in query(
        "SELECT id, clip_plan_id, created_at, plan_fingerprint FROM clips "
        "WHERE status = 'succeeded' AND clip_plan_id IS NOT NULL ORDER BY id",
        (),
    ):
        if int(row["id"]) in have:
            continue
        entries = history.get(int(row["clip_plan_id"]), [])
        chosen: str | None = None
        if row["plan_fingerprint"]:
            for entry in entries:
                try:
                    if state_fingerprint(json.loads(entry["state_json"])) == row["plan_fingerprint"]:
                        chosen = entry["state_json"]
                        break
                except ValueError:
                    continue
        if chosen is None:
            how, entry = history_candidate(entries, str(row["created_at"] or ""), HISTORY_DEPTH)
            if how == "entry" and entry:
                chosen = entry["state_json"]
        if chosen:
            out.append((int(row["id"]), int(row["clip_plan_id"]), chosen))
    return out


# ---- сетка: один элемент на клип ------------------------------------------------------------------


def _chunks(ids: list[int], size: int = 400) -> Iterable[list[int]]:
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


def _fmt_of_plan(settings_fmt: Any, last: dict | None) -> str:
    if settings_fmt:
        return normalize_format(settings_fmt)
    if last:
        return _size_format(last.get("width"), last.get("height")) or DEFAULT_FORMAT
    return DEFAULT_FORMAT


_PLAN_SELECT = f"""
    SELECT cp.id, cp.source_id, cp.title, cp.status, cp.hidden, cp.favorite, cp.score, cp.created_at,
           cp.clip_group_id, cp.group_locked, {_FMT_SQL} AS fmt
    FROM clip_plans cp
"""


def _member_plans(store: Any, where: str, args: tuple) -> list[dict]:
    """Планы и их соседи по группе: группа могла получить план из другого видео (объединение руками)."""
    rows = [dict(r) for r in store.db.query_all(f"{_PLAN_SELECT} WHERE {where}", args)]
    known = {int(r["id"]) for r in rows}
    groups = sorted({int(r["clip_group_id"]) for r in rows if r["clip_group_id"] is not None})
    for chunk in _chunks(groups):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(f"{_PLAN_SELECT} WHERE cp.clip_group_id IN ({marks})", chunk):
            if int(row["id"]) not in known:
                known.add(int(row["id"]))
                rows.append(dict(row))
    return rows


def _pieces(store: Any, plan_ids: list[int]) -> dict[int, list[tuple[float, float]]]:
    out: dict[int, list[tuple[float, float]]] = {}
    for chunk in _chunks(plan_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"""
            SELECT x.clip_plan_id AS plan, a.start_sec AS s, a.end_sec AS e FROM clip_plan_segments x
            JOIN ai_segments a ON a.id = x.segment_id WHERE x.clip_plan_id IN ({marks}) ORDER BY x.clip_plan_id, x.sort_order
            """,
            chunk,
        ):
            out.setdefault(int(row["plan"]), []).append((float(row["s"]), float(row["e"])))
    return out


def _renders(store: Any, plan_ids: list[int]) -> dict[int, list[dict]]:
    """Рендеры планов, старые первыми, те же поля, что у GET /api/clips."""
    out: dict[int, list[dict]] = {}
    for chunk in _chunks(plan_ids):
        for clip in store.list_clips_for_plans(chunk):
            out.setdefault(int(clip["clip_plan_id"]), []).append(clip)
    for rows in out.values():
        rows.sort(key=lambda c: int(c["id"]))
    return out


_ACTIVE = ("queued", "rendering")
_FAILED = ("failed", "error")


def _card(plans: list[dict], renders: dict[int, list[dict]], pieces: dict[int, list[tuple[float, float]]]) -> dict:
    plans = sorted(plans, key=lambda p: int(p["id"]))
    head = plans[-1]
    members = [r for p in plans for r in renders.get(int(p["id"]), [])]
    members.sort(key=lambda c: int(c["id"]))
    done = [c for c in members if c.get("status") == "succeeded"]
    latest = done[-1] if done else None
    active = any(c.get("status") in _ACTIVE for c in members)
    status = "rendering" if active else ("ready" if latest else ("failed" if members and members[-1].get("status") in _FAILED else "no_render"))
    # Формат клипа — настройка главного плана; у плана без настройки — его последний файл.
    head_renders = [c for c in renders.get(int(head["id"]), []) if c.get("status") == "succeeded"]
    fmt = _fmt_of_plan(head.get("fmt"), head_renders[-1] if head_renders else latest)
    named = next((p for p in reversed(plans) if renders.get(int(p["id"]))), head)
    head_pieces = pieces.get(int(head["id"])) or []
    all_pieces = [x for p in plans for x in pieces.get(int(p["id"]), [])]
    drafts = sum(1 for p in plans[:-1] if not renders.get(int(p["id"])))
    return {
        "id": int(head["id"]),
        "plan_ids": [int(p["id"]) for p in plans],
        "group": head.get("clip_group_id"),
        "name": display_name(named["title"]),
        "title": head["title"],
        "source_id": int(head["source_id"]),
        "format": fmt,
        "versions": len(done),
        "plans": len(plans),
        "drafts": drafts,
        "status": status,
        "favorite": any(p.get("favorite") for p in plans),
        "hidden": all(p.get("hidden") for p in plans),
        "score": max(float(p.get("score") or 0) for p in plans),
        "start_sec": min((a for a, _ in head_pieces), default=0.0),
        "end_sec": max((b for _, b in head_pieces), default=0.0),
        "span_start": min((a for a, _ in all_pieces), default=0.0),
        "span_end": max((b for _, b in all_pieces), default=0.0),
        "duration_sec": float(latest["duration_sec"]) if latest else sum(max(0.0, b - a) for a, b in head_pieces),
        "published": sum(int(c.get("published_targets_count") or 0) for c in members),
        "error": members[-1].get("error", "") if status == "failed" else "",
        "created_at": str(head.get("created_at") or ""),
        "latest": latest,
    }


def _cards_from_plans(store: Any, rows: list[dict], scope_ids: set[int] | None = None) -> list[dict]:
    plan_ids = [int(r["id"]) for r in rows]
    renders = _renders(store, plan_ids)
    pieces = _pieces(store, plan_ids)
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        key = int(row["clip_group_id"]) if row["clip_group_id"] is not None else -int(row["id"])
        grouped.setdefault(key, []).append(row)
    cards = [_card(group, renders, pieces) for group in grouped.values()]
    if scope_ids is not None:
        # Клип принадлежит видео своего главного плана.
        cards = [c for c in cards if c["source_id"] in scope_ids]
    cards.sort(key=lambda c: (c["start_sec"], c["id"]))
    return cards


def _add_moment(card: dict, other: dict, *, exact: bool) -> None:
    """Одна ссылка на формат: то же название важнее общего материала."""
    for item in card["moments"]:
        if item["format"] == other["format"]:
            if exact and not item.get("exact"):
                item.update({"id": other["id"], "exact": True})
            return
    card["moments"].append({"id": other["id"], "format": other["format"], "exact": exact})


def _link_moments(cards: list[dict], pieces: dict[int, list[tuple[float, float]]]) -> None:
    """Перекрёстная ссылка «этот момент в 9:16»: другой формат, то же название или тот же материал."""
    for card in cards:
        card["moments"] = []
    by_source: dict[int, list[dict]] = {}
    for card in cards:
        by_source.setdefault(card["source_id"], []).append(card)
    for group in by_source.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["format"] == b["format"]:
                    continue
                same = name_key(a["name"]) == name_key(b["name"])
                total_a = sum(y - x for x, y in pieces.get(a["id"], []))
                total_b = sum(y - x for x, y in pieces.get(b["id"], []))
                # Часть стрима на 5 минут и шортс из неё — не «этот момент в 9:16».
                comparable = min(total_a, total_b) > 0 and max(total_a, total_b) / min(total_a, total_b) <= MOMENT_LENGTH_RATIO
                if not same and comparable:
                    same = shared_fraction(pieces.get(a["id"], []), pieces.get(b["id"], [])) >= ALMOST_SAME_MATERIAL
                if same:
                    _add_moment(a, b, exact=name_key(a["name"]) == name_key(b["name"]))
                    _add_moment(b, a, exact=name_key(a["name"]) == name_key(b["name"]))
    for card in cards:
        card["moments"] = [{"id": m["id"], "format": m["format"]} for m in card["moments"]]


def _orphan_renders(store: Any, sql_where: str, args: tuple) -> list[dict]:
    """Рендеры без плана (план удалили, файл остался): в сетке клипов их нет, но терять нельзя."""
    ids = [int(r["id"]) for r in store.db.query_all(
        f"SELECT c.id FROM clips c JOIN sources s ON s.id = c.source_id WHERE c.clip_plan_id IS NULL AND {sql_where} ORDER BY c.id DESC",
        args,
    )]
    return [store.get_clip(i) for i in ids]


def source_cards(store: Any, source_id: int) -> dict:
    rows = _member_plans(store, "cp.source_id = ?", (int(source_id),))
    cards = _cards_from_plans(store, rows, {int(source_id)})
    _link_moments(cards, _pieces(store, [c["id"] for c in cards]))
    return {"clips": cards, "orphans": _orphan_renders(store, "c.source_id = ?", (int(source_id),))}


def project_source_ids(store: Any, project_id: int) -> list[int]:
    return [int(r["id"]) for r in store.db.query_all("SELECT id FROM sources WHERE project_id = ?", (int(project_id),))]


def project_cards(store: Any, project_id: int) -> dict:
    """Клипы проекта: его видео плюс холсты, приписанные к нему (скрытый общий источник)."""
    sources = project_source_ids(store, project_id)
    rows: list[dict] = []
    for chunk in _chunks(sources):
        marks = ",".join("?" for _ in chunk)
        rows += _member_plans(store, f"cp.source_id IN ({marks})", tuple(chunk))
    rows += _member_plans(
        store,
        "cp.id IN (SELECT pp.clip_plan_id FROM plan_projects pp WHERE pp.project_id = ?)",
        (int(project_id),),
    )
    unique = {int(r["id"]): r for r in rows}
    cards = _cards_from_plans(store, list(unique.values()))
    _link_moments(cards, _pieces(store, [c["id"] for c in cards]))
    from app.nav import titles_for

    wanted = sorted({c["source_id"] for c in cards})
    infos: list[dict] = []
    for chunk in _chunks(wanted):
        marks = ",".join("?" for _ in chunk)
        infos += [dict(r) for r in store.db.query_all(
            f"SELECT id, source_type, original_filename, created_at, project_id FROM sources WHERE id IN ({marks})", chunk,
        )]
    names = titles_for(store, infos)
    for card in cards:
        card["video_name"] = names.get(card["source_id"], "")
    cards.sort(key=lambda c: -int(c["id"]))
    orphans = _orphan_renders(store, "s.project_id = ?", (int(project_id),))
    return {"clips": cards, "orphans": orphans}


def group_members(store: Any, clip_plan_id: int) -> list[dict]:
    """Все планы клипа, старые первыми. Незнакомый план — KeyError."""
    row = store.db.query_one("SELECT id, clip_group_id FROM clip_plans WHERE id = ?", (int(clip_plan_id),))
    if not row:
        raise KeyError(f"клипа нет: {clip_plan_id}")
    if row["clip_group_id"] is None:
        where, args = "cp.id = ?", (int(clip_plan_id),)
    else:
        where, args = "cp.clip_group_id = ?", (int(row["clip_group_id"]),)
    return [dict(r) for r in store.db.query_all(f"{_PLAN_SELECT} WHERE {where} ORDER BY cp.id", args)]


def head_plan_id(store: Any, clip_plan_id: int) -> int:
    return int(group_members(store, clip_plan_id)[-1]["id"])


# ---- версии ----------------------------------------------------------------------------------------


def _version_labels(plans: list[dict]) -> dict[int, int]:
    """Номер версии плана: из названия, если он там есть и растёт; иначе порядковый."""
    numbers: dict[int, int] = {}
    last = 0
    for plan in plans:
        mark = version_mark(plan["title"])
        last = mark if mark and mark >= last else last + 1
        numbers[int(plan["id"])] = last
    return numbers


def clip_versions(store: Any, plans: list[dict]) -> list[dict]:
    """Версии клипа новыми первыми: рендеры всех планов плюс снимки «до отката» без своего файла."""
    from app.clip_history import HISTORY_DEPTH

    plan_ids = [int(p["id"]) for p in plans]
    numbers = _version_labels(plans)
    renders = _renders(store, plan_ids)
    history = _history_times(store, plan_ids)
    saved = set()
    extra: list[dict] = []
    marks_by_plan: dict[int, list[dict]] = {}
    for chunk in _chunks(plan_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"SELECT id, clip_plan_id, clip_id, kind, label, created_at FROM clip_versions WHERE clip_plan_id IN ({marks}) ORDER BY id",
            chunk,
        ):
            if row["clip_id"] is not None:
                saved.add(int(row["clip_id"]))
            elif row["kind"] == "rollback_mark":
                marks_by_plan.setdefault(int(row["clip_plan_id"]), []).append(dict(row))
            elif row["kind"] != "render":
                extra.append(dict(row))
    versions: list[dict] = []
    for plan in plans:
        pid = int(plan["id"])
        rows = renders.get(pid, [])
        done = [c for c in rows if c.get("status") == "succeeded"]
        prev = ""
        for clip in rows:
            created = str(clip.get("created_at") or "")
            changes: list[str] = []
            for entry in history.get(pid, []) + marks_by_plan.get(pid, []):
                at = str(entry["created_at"])
                label = str(entry.get("label") or "").replace("агент: ", "")
                if prev < at <= created and label and label not in changes:
                    changes.append(label)
            prev = created
            ok = clip.get("status") == "succeeded"
            index = done.index(clip) + 1 if ok else 0
            label = f"v{numbers[pid]}" + (f".{index}" if ok and len(done) > 1 else "")
            how = "saved" if int(clip["id"]) in saved else history_candidate(history.get(pid, []), created, HISTORY_DEPTH)[0]
            qc = clip.get("qc") if isinstance(clip.get("qc"), dict) else None
            versions.append({
                "key": f"r{clip['id']}",
                "render_id": int(clip["id"]),
                "snapshot_id": None,
                "plan_id": pid,
                "label": label,
                "at": clip.get("finished_at") or created,
                "status": clip.get("status"),
                "error": clip.get("error") or "",
                "format": _size_format(clip.get("width"), clip.get("height")) or "",
                "width": int(clip.get("width") or 0),
                "height": int(clip.get("height") or 0),
                "duration_sec": float(clip.get("duration_sec") or 0),
                "qc_ok": qc.get("ok") if qc else None,
                "changes": changes[-5:],
                "can_rollback": ok and how != "lost",
                "why_not": "" if not ok or how != "lost" else "Снимок правок этой старой версии не сохранился: смотреть и скачать можно, откатить нельзя",
                "url": f"/media/clips/{clip['id']}" if ok else None,
                "thumb": f"/media/clips/{clip['id']}/thumb?v={clip.get('updated_at') or ''}" if ok else None,
            })
    for row in extra:
        versions.append({
            "key": f"s{row['id']}",
            "render_id": None,
            "snapshot_id": int(row["id"]),
            "plan_id": int(row["clip_plan_id"]),
            "label": row["label"] or "Правки до отката",
            "at": row["created_at"],
            "status": "snapshot",
            "error": "",
            "format": "",
            "width": 0,
            "height": 0,
            "duration_sec": 0.0,
            "qc_ok": None,
            "changes": [],
            "can_rollback": True,
            "why_not": "",
            "url": None,
            "thumb": None,
        })
    versions.sort(key=lambda v: (str(v["at"]), v["render_id"] or 0), reverse=True)
    return versions


def _hashtags(meta: dict | None, description: str) -> tuple[str, list[str]]:
    """Описание и хэштеги отдельно: в файле описания они дописаны в конец (full_description)."""
    tags = [str(t) for t in (meta or {}).get("hashtags") or []]
    text = str((meta or {}).get("description") or description or "").strip()
    if not tags:
        found = re.findall(r"(?:^|\s)(#[\w-]+)", text)
        if found:
            tags = found
            text = re.sub(r"(?:^|\s)#[\w-]+", "", text).strip()
    return text, tags


def _project_of(store: Any, plan: dict) -> dict | None:
    row = store.db.query_one(
        """
        SELECT p.id, p.name FROM projects p WHERE p.id = COALESCE(
            (SELECT s.project_id FROM sources s WHERE s.id = ?),
            (SELECT pp.project_id FROM plan_projects pp WHERE pp.clip_plan_id = ? LIMIT 1)
        )
        """,
        (int(plan["source_id"]), int(plan["id"])),
    )
    return {"id": int(row["id"]), "name": row["name"]} if row else None


def clip_view(store: Any, clip_plan_id: int) -> dict:
    """Всё для экрана клипа /clips/:id одним ответом."""
    from app.nav import titles_for
    from app.quality_lint import render_is_stale
    from app.store import source_file_present

    plans = group_members(store, clip_plan_id)
    head = plans[-1]
    head_id = int(head["id"])
    plan_ids = [int(p["id"]) for p in plans]
    renders = _renders(store, plan_ids)
    pieces = _pieces(store, plan_ids)
    card = _card(plans, renders, pieces)
    source_row = store.db.query_one(
        """
        SELECT id, source_type, original_filename, original_url, local_path, duration_sec, width, height, created_at,
               project_id, status FROM sources WHERE id = ?
        """,
        (int(head["source_id"]),),
    )
    source = dict(source_row)
    source_name = titles_for(store, [source]).get(int(source["id"])) or f"Видео #{source['id']}"
    canvas = str(source["source_type"]) == "canvas"
    project = _project_of(store, head)
    # Выпуски — только холсты и компиляции: у клипа из записи список выпусков не читаем.
    release = None
    described = store.db.query_one("SELECT description FROM clip_plans WHERE id = ?", (head_id,))
    if canvas or str((described or {"description": ""})["description"] or "").startswith("Компиляция: "):
        from app.releases import of_plan

        release = of_plan(store, head_id)
    if project is None and release and release.get("project_id"):
        # Сборка старых дней не приписала клип к проекту: проект всё равно известен по выпуску.
        row = store.db.query_one("SELECT id, name FROM projects WHERE id = ?", (int(release["project_id"]),))
        project = {"id": int(row["id"]), "name": row["name"]} if row else None

    # Соседи: те же клипы, что в сетке видео, в том же порядке. У холстов «видео» — проект.
    if canvas and project:
        rows = _member_plans(
            store, "cp.id IN (SELECT pp.clip_plan_id FROM plan_projects pp WHERE pp.project_id = ?)", (project["id"],)
        )
        siblings = _cards_from_plans(store, rows)
    elif canvas:
        siblings = [card]
    else:
        siblings = _cards_from_plans(store, _member_plans(store, "cp.source_id = ?", (int(source["id"]),)), {int(source["id"])})
    siblings = [c for c in siblings if not c["hidden"] or c["id"] == head_id]
    sibling_pieces = _pieces(store, [c["id"] for c in siblings])
    _link_moments(siblings, sibling_pieces)
    me = next((c for c in siblings if c["id"] == head_id), None)
    index = siblings.index(me) if me in siblings else -1
    neighbors = [
        {"id": c["id"], "name": c["name"], "format": c["format"], "status": c["status"], "versions": c["versions"],
         "start_sec": c["start_sec"]}
        for c in siblings
    ]

    head_plan = store.get_clip_plan(head_id)
    segments = [
        {"id": s["id"], "title": s.get("title") or "", "start_sec": float(s["start_sec"]), "end_sec": float(s["end_sec"])}
        for s in head_plan.get("segments") or []
    ]
    latest = card["latest"]
    stale = False
    if latest and int(latest.get("clip_plan_id") or 0) == head_id:
        stale = render_is_stale(store, head_id, latest)
    elif latest:
        stale = True  # новый план группы ещё не рендерили
    meta = (latest or {}).get("publish_meta") if latest else None
    text, tags = _hashtags(meta, str((latest or {}).get("description") or head_plan.get("description") or ""))

    cover_doc = None
    render_ids = [int(c["id"]) for rows in renders.values() for c in rows]
    with_cover = sorted((c for rows in renders.values() for c in rows if c.get("cover_url")), key=lambda c: int(c["id"]))
    for chunk in _chunks(render_ids):
        marks = ",".join("?" for _ in chunk)
        row = store.db.query_one(f"SELECT id, title, rev FROM cover_docs WHERE clip_id IN ({marks}) ORDER BY id DESC LIMIT 1", chunk)
        if row:
            cover_doc = {"id": int(row["id"]), "title": row["title"]}

    older_chat: list[dict] = []
    for plan in plans[:-1]:
        for row in store.db.query_all(
            "SELECT id, role, text, created_at FROM clip_agent_messages WHERE clip_plan_id = ? ORDER BY id DESC LIMIT 30",
            (int(plan["id"]),),
        ):
            older_chat.append({**dict(row), "plan_id": int(plan["id"])})
    older_chat.sort(key=lambda m: int(m["id"]))

    numbers = _version_labels(plans)
    has_file = source_file_present(source.get("local_path"))
    # Что изменилось после последнего файла главного плана: подпись строки «Текущий монтаж».
    pending: list[str] = []
    if stale:
        last_done = [c for c in renders.get(head_id, []) if c.get("status") == "succeeded"]
        since = str(last_done[-1].get("created_at") or "") if last_done else ""
        rows = store.db.query_all(
            """
            SELECT label, created_at FROM clip_plan_history WHERE clip_plan_id = ?
            UNION ALL
            SELECT label, created_at FROM clip_versions WHERE clip_plan_id = ? AND kind = 'rollback_mark'
            ORDER BY created_at
            """,
            (head_id, head_id),
        )
        for row in rows:
            label = str(row["label"] or "").replace("агент: ", "")
            if str(row["created_at"]) > since and label and label not in pending:
                pending.append(label)
    return {
        "id": head_id,
        "requested": int(clip_plan_id),
        "group": head.get("clip_group_id"),
        "name": card["name"],
        "title": head["title"],
        "format": card["format"],
        "status": card["status"],
        "start_sec": card["start_sec"],
        "duration_sec": card["duration_sec"],
        "published": card["published"],
        "favorite": card["favorite"],
        "stale": stale,
        "pending": pending[-4:],
        "canvas": canvas,
        "source": {
            "id": int(source["id"]),
            "name": source_name,
            "source_type": source["source_type"],
            "has_file": has_file,
            "original_url": source.get("original_url") or "",
            "status": source.get("status"),
            "project_id": source.get("project_id"),
            "duration_sec": float(source.get("duration_sec") or 0),
        },
        "project": project,
        "release": release,
        "plans": [
            {
                "id": int(p["id"]),
                "title": p["title"],
                "label": f"v{numbers[int(p['id'])]}",
                "renders": sum(1 for c in renders.get(int(p["id"]), []) if c.get("status") == "succeeded"),
                "created_at": p["created_at"],
                "is_head": int(p["id"]) == head_id,
            }
            for p in plans
        ],
        "versions": clip_versions(store, plans),
        "version_count": card["versions"],
        "drafts": card["drafts"],
        "segments": segments,
        "latest": latest,
        "meta": {
            "title": str((latest or {}).get("title") or head_plan.get("title") or ""),
            "description": text,
            "hashtags": tags,
            "source": (meta or {}).get("source") or "",
        },
        "cover_url": with_cover[-1]["cover_url"] if with_cover else None,
        "cover_doc": cover_doc,
        "moments": me["moments"] if me else [],
        "neighbors": neighbors,
        "prev_id": siblings[index - 1]["id"] if index > 0 else None,
        "next_id": siblings[index + 1]["id"] if 0 <= index < len(siblings) - 1 else None,
        "older_chat": older_chat[-40:],
    }


# ---- новые версии, объединение, разъединение -------------------------------------------------------


def attach_new_plan(store: Any, plan_id: int, *, version_of: int | None = None) -> int | None:
    """Новый план — новая версия клипа? ``version_of`` указал агент или кнопка; без него — только если
    в названии есть номер версии и то же название, начало и формат находятся у плана этого видео.
    Возвращает id плана, если он вошёл в группу, или None, если остался отдельным клипом."""
    if version_of is not None:
        members = group_members(store, int(version_of))
        plan = store.get_clip_plan(int(plan_id), include_segments=False)
        if not any(int(plan["source_id"]) == int(m["source_id"]) for m in members):
            raise ValueError("версия клипа должна быть из того же видео")
        number = _group_number(members)
        _write_group(store, members, number)
        store.db.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", (number, int(plan_id)))
        return int(plan_id)
    plan = store.db.query_one("SELECT title FROM clip_plans WHERE id = ?", (int(plan_id),))
    if not plan or version_mark(plan["title"]) is None:
        return None
    return _attach_by_rule(store, int(plan_id))


def _attach_by_rule(store: Any, plan_id: int) -> int | None:
    facts = _facts_for_source(store, plan_id)
    me = facts.get(plan_id)
    if me is None or me.locked or not me.key or not me.pieces:
        return None
    candidates = [
        f for f in facts.values()
        if f.id != plan_id and not f.locked and f.fmt == me.fmt and f.key == me.key and same_moment(me, f)
    ]
    if not candidates:
        return None
    existing = sorted({f.group for f in candidates if f.group is not None})
    if len(existing) > 1:
        return None  # две разные группы подходят: неясно, оставляем отдельным клипом
    number = existing[0] if existing else min(f.id for f in candidates + [me])
    for fact in candidates + [me]:
        if fact.group != number:
            store.db.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", (number, fact.id))
    return plan_id


def _facts_for_source(store: Any, plan_id: int) -> dict[int, PlanFact]:
    """Факты по планам того же места (источник, а у холстов — проект): автопривязка не читает всю базу."""
    row = store.db.query_one("SELECT source_id FROM clip_plans WHERE id = ?", (int(plan_id),))
    if not row:
        return {}
    facts = load_facts(store.db.query_all, int(row["source_id"]))
    me = next((f for f in facts if f.id == int(plan_id)), None)
    if me is None:
        return {}
    return {f.id: f for f in facts if f.scope == me.scope}


def _group_number(members: list[dict]) -> int:
    existing = [int(m["clip_group_id"]) for m in members if m.get("clip_group_id") is not None]
    return min(existing) if existing else min(int(m["id"]) for m in members)


def _write_group(store: Any, members: list[dict], number: int) -> None:
    for member in members:
        if member.get("clip_group_id") != number:
            store.db.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", (number, int(member["id"])))


def on_format_change(store: Any, plan_id: int) -> None:
    """Формат плана сменили: вертикаль и горизонталь — разные клипы. Отделяем от группы другого формата
    и пробуем найти группу своего. Владелец отделил план руками (group_locked) — не трогаем."""
    row = store.db.query_one("SELECT clip_group_id, group_locked FROM clip_plans WHERE id = ?", (int(plan_id),))
    if not row or row["group_locked"]:
        return
    facts = _facts_for_source(store, plan_id)
    me = facts.get(int(plan_id))
    if me is None:
        return
    if me.group is not None:
        others = [f for f in facts.values() if f.group == me.group and f.id != me.id]
        if others and all(f.fmt == me.fmt for f in others):
            return
        store.db.execute("UPDATE clip_plans SET clip_group_id = NULL WHERE id = ?", (int(plan_id),))
    if version_mark(me.title) is not None:
        _attach_by_rule(store, int(plan_id))


def merge_clips(store: Any, clip_plan_id: int, other_plan_id: int) -> dict:
    """Объединить два клипа в один. Формат должен совпадать; видео — то же или того же проекта."""
    a = group_members(store, clip_plan_id)
    b = group_members(store, other_plan_id)
    if {int(m["id"]) for m in a} & {int(m["id"]) for m in b}:
        raise ValueError("это уже один клип")
    renders = _renders(store, [int(m["id"]) for m in a + b])
    pieces = _pieces(store, [int(m["id"]) for m in a + b])
    fmt_a, fmt_b = _card(a, renders, pieces)["format"], _card(b, renders, pieces)["format"]
    if fmt_a != fmt_b:
        raise ValueError(f"разные форматы ({fmt_a} и {fmt_b}): вертикаль и горизонталь одного момента — разные клипы")
    project_a = _project_of(store, a[-1])
    project_b = _project_of(store, b[-1])
    same_source = int(a[-1]["source_id"]) == int(b[-1]["source_id"])
    same_project = project_a is not None and project_b is not None and project_a["id"] == project_b["id"]
    if not same_source and not same_project:
        raise ValueError("объединять можно клипы одного видео или одного проекта")
    number = _group_number(a + b)
    for member in a + b:
        store.db.execute(
            "UPDATE clip_plans SET clip_group_id = ?, group_locked = 0 WHERE id = ?", (number, int(member["id"]))
        )
    return {"id": head_plan_id(store, clip_plan_id)}


def split_plan(store: Any, plan_id: int) -> dict:
    """Отделить план в отдельный клип. Запоминаем выбор: скрипт группировки его больше не тронет."""
    members = group_members(store, plan_id)
    if len(members) < 2:
        raise ValueError("план и так отдельный клип")
    store.db.execute("UPDATE clip_plans SET clip_group_id = NULL, group_locked = 1 WHERE id = ?", (int(plan_id),))
    rest = [m for m in members if int(m["id"]) != int(plan_id)]
    return {"id": int(plan_id), "rest_id": int(rest[-1]["id"])}


def new_version(store: Any, clip_plan_id: int) -> dict:
    """Кнопка «Новая версия»: копия монтажа главного плана новым планом в той же группе."""
    from app.clip_history import capture, restore

    members = group_members(store, clip_plan_id)
    head = members[-1]
    head_id = int(head["id"])
    state = capture(store, head_id)
    plan = store.get_clip_plan(head_id)
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("у клипа нет кусков")
    number = _version_labels(members)[head_id] + 1
    name = display_name(head["title"])
    prefix = head["title"].split(":", 1)[0] if ":" in head["title"] else ""
    base = _VERSION.sub(" ", prefix).strip() if prefix else ""
    title = f"{base} v{number}: {name}" if base else f"{name} v{number}"
    # Куски могли прийти из разных анализов (ручная правка): тогда план без привязки к анализу.
    analysis = plan.get("analysis_id")
    if any(s.get("analysis_id") != analysis for s in segments):
        analysis = None
    created = store.create_clip_plan(
        int(head["source_id"]), analysis, title[:100], description=plan.get("description") or "",
        segment_ids=[int(s["id"]) for s in segments],
    )
    attach_new_plan(store, int(created["id"]), version_of=head_id)
    spec = {k: v for k, v in (state.get("spec") or {}).items() if k not in ("title", "description", "notes", "favorite")}
    fresh = store.get_clip_plan(int(created["id"]), include_segments=False)
    spec.update({"title": fresh["title"], "description": fresh.get("description") or "", "notes": "", "favorite": False})
    restore(store, int(created["id"]), {"spec": spec, "subtitles": state.get("subtitles")})
    project = store.db.query_one("SELECT project_id FROM plan_projects WHERE clip_plan_id = ?", (head_id,))
    if project:
        from app import projects as P

        P.assign_plan_project(store, int(created["id"]), int(project["project_id"]))
    return {"id": int(created["id"]), "title": fresh["title"]}


def rollback(store: Any, clip_plan_id: int, *, render_id: int | None = None, snapshot_id: int | None = None) -> dict:
    """Вернуть главному плану правки монтажа выбранной версии. Текст клипа не трогаем, рендеры не удаляем,
    текущий монтаж остаётся версией: либо у него уже есть файл с тем же отпечатком, либо он сохраняется
    снимком «правки до отката». Новый рендер ставит кнопка: без записи стрима он невозможен."""
    from app.clip_history import capture, restore, snapshot

    members = group_members(store, clip_plan_id)
    plan_ids = [int(m["id"]) for m in members]
    head_id = plan_ids[-1]
    label = "снимок"
    state: dict | None = None
    if snapshot_id is not None:
        row = store.db.query_one("SELECT clip_plan_id, label, state_json FROM clip_versions WHERE id = ?", (int(snapshot_id),))
        if not row or int(row["clip_plan_id"]) not in plan_ids:
            raise KeyError(f"снимка нет: {snapshot_id}")
        state = json.loads(row["state_json"])
        label = row["label"] or "снимок"
    elif render_id is not None:
        clip = store.get_clip(int(render_id))
        if int(clip.get("clip_plan_id") or 0) not in plan_ids:
            raise KeyError(f"рендер {render_id} не из этого клипа")
        state = state_of_render(store, clip)
        found = [v for v in clip_versions(store, members) if v["render_id"] == int(render_id)]
        label = found[0]["label"] if found else f"рендер {render_id}"
    else:
        raise ValueError("нужна версия: render_id или snapshot_id")
    if not state:
        raise ValueError("снимок правок этой версии не сохранился: смотреть и скачать можно, откатить нельзя")

    current = capture(store, head_id)
    stamp = state_fingerprint(current)
    has_file = store.db.query_one(
        "SELECT 1 AS ok FROM clips WHERE clip_plan_id = ? AND plan_fingerprint = ? AND status = 'succeeded' LIMIT 1",
        (head_id, stamp),
    )
    has_snapshot = any(
        state_fingerprint(json.loads(r["state_json"])) == stamp
        for r in store.db.query_all(
            "SELECT state_json FROM clip_versions WHERE clip_plan_id = ? AND kind = 'before_rollback'", (head_id,)
        )
    )
    if not has_file and not has_snapshot:
        add_snapshot(store, head_id, current, clip_id=None, kind="before_rollback",
                     label=f"Правки до отката к {label}")

    snapshot(store, head_id, f"откат к {label}")
    # Подпись для следующего рендера: история не пишет два одинаковых снимка подряд, метка нужна своя.
    add_snapshot(store, head_id, {}, clip_id=None, kind="rollback_mark", label=f"откат к {label}")
    head = store.get_clip_plan(head_id, include_segments=False)
    spec = {k: v for k, v in (state.get("spec") or {}).items() if k not in ("title", "description", "notes", "favorite")}
    spec.update({
        "title": head["title"], "description": head.get("description") or "",
        "notes": head.get("notes") or "", "favorite": bool(head.get("favorite")),
    })
    fresh, changes = restore(store, head_id, {"spec": spec, "subtitles": state.get("subtitles")})
    return {"id": head_id, "label": label, "changes": changes}


def save_meta(store: Any, clip_plan_id: int, title: str, description: str, hashtags: list[str]) -> dict:
    """Заголовок, описание и хэштеги клипа: на последний готовый рендер, новый рендер унаследует их
    (app/ai/publish_meta._inherit). Рендера ещё нет — в сам план."""
    from app.ai.publish_meta import save_publish_meta

    members = group_members(store, clip_plan_id)
    plan_ids = [int(m["id"]) for m in members]
    renders = _renders(store, plan_ids)
    done = sorted((c for rows in renders.values() for c in rows if c.get("status") == "succeeded"), key=lambda c: int(c["id"]))
    title = " ".join(str(title or "").split())[:100]
    tags = []
    for tag in hashtags:
        clean = str(tag).strip().lstrip("#").strip()
        if clean:
            tags.append(f"#{clean}")
    description = str(description or "").strip()
    if done:
        latest = done[-1]
        previous = latest.get("publish_meta") or {}
        record = save_publish_meta(store, int(latest["id"]), {
            "title": title or latest.get("title") or "",
            "description": description,
            "hashtags": tags,
            # Варианты площадок писала модель по старому тексту: правку владельца они бы перекрыли.
            "platforms": {},
            "cover_hooks": previous.get("cover_hooks") or [],
            "model": "",
        }, source="manual")
        return {"clip_id": int(latest["id"]), "title": record["title"], "description": record["description"],
                "hashtags": record["hashtags"]}
    head = members[-1]
    plan = store.get_clip_plan(int(head["id"]), include_segments=False)
    store.update_clip_plan_text(int(head["id"]), title or plan["title"], description, plan.get("notes") or "")
    return {"clip_id": None, "title": title or plan["title"], "description": description, "hashtags": tags}


# ---- пересборка выпуска: новая версия того же клипа (решение 14) ------------------------------------


def hold_assembly(store: Any, plan_id: int) -> None:
    """План только что создан сборкой: пока она не дошла до конца, его не видно. Упавшая сборка не становится главным
    планом клипа и не появляется в проекте лишней строкой; ничего не удаляется, прямая ссылка работает."""
    store.db.execute("UPDATE clip_plans SET hidden = 1 WHERE id = ?", (int(plan_id),))


def finish_assembly(store: Any, plan_id: int, previous: int | None) -> dict:
    """Сборка дошла до конца: план виден и, если выпуск уже собирали, стал новой версией того же клипа.
    Без прошлого клипа, с удалённым прошлым или с прошлым другого формата (вертикаль и горизонталь — разные клипы,
    решение 7) план остаётся самостоятельным клипом, и ``note`` говорит почему."""
    store.db.execute("UPDATE clip_plans SET hidden = 0 WHERE id = ?", (int(plan_id),))
    if not previous or int(previous) == int(plan_id):
        return {"linked": False, "first": True, "note": ""}
    try:
        members = group_members(store, int(previous))
    except KeyError:
        return {"linked": False, "first": False, "note": f"прошлого клипа #{previous} нет: собран новый клип"}
    head = members[-1]
    done = [c for c in _renders(store, [int(head["id"])]).get(int(head["id"]), []) if c.get("status") == "succeeded"]
    head_fmt = _fmt_of_plan(head.get("fmt"), done[-1] if done else None)
    fmt = _fmt_of_plan(group_members(store, int(plan_id))[-1].get("fmt"), None)
    if head_fmt != fmt:
        return {"linked": False, "first": False,
                "note": f"прошлый клип #{head['id']} в формате {head_fmt}, сборка в {fmt}: это разные клипы"}
    attach_new_plan(store, int(plan_id), version_of=int(head["id"]))
    project = store.db.query_one("SELECT project_id FROM plan_projects WHERE clip_plan_id = ?", (int(head["id"]),))
    if project:
        # Клип могли перенести в другой проект: новая версия живёт там же.
        from app import projects as P

        P.assign_plan_project(store, int(plan_id), int(project["project_id"]))
    fresh = group_members(store, int(plan_id))
    return {"linked": True, "first": False, "group": fresh[-1].get("clip_group_id"), "of": int(head["id"]),
            "version": _version_labels(fresh)[int(plan_id)],
            "note": "новая версия клипа: прошлая осталась, откат на экране клипа"}


def hand_edits(store: Any, clip_plan_id: int) -> list[str]:
    """Что правили в главном плане клипа после сборки: слои и монтаж новая сборка не переносит, это надо сказать."""
    try:
        head = head_plan_id(store, int(clip_plan_id))
    except KeyError:
        return []
    out: list[str] = []
    for row in store.db.query_all("SELECT label FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id", (head,)):
        text = str(row["label"] or "").replace("агент: ", "").strip()
        if not text or text.endswith(": сборка") or text == "правка субтитров" or text in out:
            continue
        out.append(text)
    return out
