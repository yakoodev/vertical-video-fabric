"""Независимый критик готового ролика: свежий вызов Gemini, который видит только результат.

Идея — из motion-video-kit (``references/gauntlet.md``, MIT): тот, кто монтировал, не судит свою работу. Монтажёр
знает, что хотел сделать, и «видит» это в ролике; критик видит только пиксели и звук. Поэтому модели уходят ТОЛЬКО:

* сам ролик — лёгкая копия рендера (480 px по высоте, 15 к/с, моно-звук), сжатая разметка кадра у модели (LOW);
* задача (``brief``) — словами владельца или агента, без плана монтажа;
* что владелец уже говорил по этому проекту: сквозные правила (``app/owner_rules.py``), правила и ответы проекта
  (``app/project_knowledge.prompt_block`` — бриф, правила, ответы на вопросы), его вердикты прошлым работам ленты;
* замеры готового файла (громкость, тишина, застывшие куски — ffmpeg), чтобы критик судил и по цифрам;
* прошлый отчёт (повторный круг): по каждому пункту «исправлено / частично / осталось».

НЕ уходят: план клипа, слои, рассуждения и отчёт монтажёра, заметка версии ленты «что поменял».

Ответ: вердикт «сдавать / ещё проход», замечания «t=… · где · что не так → что поменять · что оставить» с P0/P1/P2.
Отчёты лежат в ``runtime/critiques/<работа или клип>.json`` — следующий вызов сам берёт прошлый отчёт. Траты —
обычный учёт Gemini (``gemini_usage``, purpose ``render_critique``, сессия ``critique:<id>``).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from app.settings import settings

_log = logging.getLogger(__name__)

VERDICTS = ("сдавать", "ещё проход")
PRIORITIES = ("P0", "P1", "P2")
STATUSES = ("исправлено", "частично", "осталось")
MAX_ISSUES = 12
MAX_VIDEO_SEC = 600.0
PROXY_HEIGHT = 480
PROXY_FPS = 15
INLINE_MAX_BYTES = 12 * 1024 * 1024
MAX_VERDICTS = 12
MAX_BRIEF = 3000
PURPOSE = "render_critique"


class CritiqueError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Что критикуем: рендер, клип или версия ленты
# --------------------------------------------------------------------------- #


def resolve_target(store: Any, *, clip_id: Any = None, render_clip_id: Any = None, feed_version_id: Any = None,
                   variant_id: Any = None) -> dict:
    """→ {path, render_clip_id, clip_plan_id, project_id, work_id, feed_version_id, key, title, duration}."""
    from app import feed as FEED

    given = [k for k, v in (("clip_id", clip_id), ("render_clip_id", render_clip_id),
                            ("feed_version_id", feed_version_id)) if v not in (None, "")]
    if len(given) != 1:
        raise CritiqueError("нужен ровно один из clip_id (клип — его последний готовый рендер), render_clip_id "
                            "(готовый рендер) или feed_version_id (версия ленты)")
    out: dict = {"work_id": None, "feed_version_id": None, "project_id": None, "title": ""}
    if feed_version_id not in (None, ""):
        try:
            version = FEED.get_version(store, int(feed_version_id))
            work = FEED.get_work(store, int(version["work_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CritiqueError(f"нет версии ленты {feed_version_id}") from exc
        variants = version.get("variants") or []
        if variant_id not in (None, ""):
            variants = [v for v in variants if int(v["id"]) == int(variant_id)]
        variant = next((v for v in variants if v.get("file_present")), None)
        if variant is None:
            raise CritiqueError("у версии нет варианта с файлом")
        path = FEED.variant_media_path(store, FEED._variant_row(store, int(variant["id"])))
        out.update({"work_id": int(work["id"]), "feed_version_id": int(version["id"]),
                    "project_id": work.get("project_id"), "title": work.get("title") or "",
                    "render_clip_id": variant.get("clip_id"), "clip_plan_id": variant.get("clip_plan_id"),
                    "variant": variant.get("label") or "", "version_name": version.get("name") or "",
                    "key": f"work-{int(work['id'])}"})
    else:
        clip = None
        if render_clip_id not in (None, ""):
            try:
                clip = store.get_clip(int(render_clip_id))
            except (KeyError, TypeError, ValueError) as exc:
                raise CritiqueError(f"нет рендера {render_clip_id}") from exc
        else:
            try:
                renders = store.list_clips_for_plan(int(clip_id))
            except (TypeError, ValueError) as exc:
                raise CritiqueError("clip_id — число") from exc
            clip = next((c for c in renders if c.get("status") == "succeeded" and c.get("output_path")), None)
            if clip is None:
                raise CritiqueError(f"у клипа #{clip_id} нет готового рендера: render_clip → render_status до done")
        if clip.get("status") != "succeeded":
            raise CritiqueError(f"рендер {clip['id']} не готов ({clip.get('status')})")
        path = Path(str(clip.get("output_path") or ""))
        plan_id = clip.get("clip_plan_id")
        out.update({"render_clip_id": int(clip["id"]), "clip_plan_id": plan_id, "title": clip.get("title") or "",
                    "key": f"plan-{int(plan_id)}" if plan_id else f"render-{int(clip['id'])}"})
    if not path or not Path(path).is_file():
        raise CritiqueError("файла ролика нет на диске")
    out["path"] = str(path)
    if out.get("project_id") is None and out.get("clip_plan_id"):
        try:
            from app.asset_scope import clip_project

            out["project_id"] = clip_project(store, int(out["clip_plan_id"]))
        except Exception:  # noqa: BLE001 - без проекта: только сквозные правила
            out["project_id"] = None
    return out


# --------------------------------------------------------------------------- #
# Лёгкая копия и замеры
# --------------------------------------------------------------------------- #


def make_proxy(src: Path, dst: Path) -> Path:
    """480 px по высоте, 15 к/с, моно 64 кбит/с — модели хватает, запрос маленький."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-t", f"{MAX_VIDEO_SEC:g}",
            "-vf", f"scale=-2:'min({PROXY_HEIGHT},ih)',fps={PROXY_FPS}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-movflags", "+faststart",
            str(dst)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0 or not dst.is_file():
        raise CritiqueError("не удалось сделать лёгкую копию ролика: " + (proc.stderr or "")[-300:])
    return dst


_FLOAT = r"(-?\d+(?:\.\d+)?)"


def parse_measures(log: str) -> dict:
    """Замеры из журнала ffmpeg (ebur128, silencedetect, freezedetect)."""
    out: dict = {}
    summary = log[log.rfind("Summary:"):] if "Summary:" in log else ""
    for key, pattern in (("loudness_lufs", rf"I:\s+{_FLOAT} LUFS"), ("loudness_range_lu", rf"LRA:\s+{_FLOAT} LU"),
                         ("true_peak_dbfs", rf"Peak:\s+{_FLOAT} dBFS")):
        match = re.search(pattern, summary)
        if match:
            out[key] = float(match.group(1))

    def spans(start_key: str, end_key: str) -> list[list[float]]:
        starts = [float(x) for x in re.findall(rf"{start_key}[:=]\s*{_FLOAT}", log)]
        ends = [float(x) for x in re.findall(rf"{end_key}[:=]\s*{_FLOAT}", log)]
        return [[round(a, 2), round(ends[i], 2) if i < len(ends) else None] for i, a in enumerate(starts)][:20]

    out["silences"] = spans("silence_start", "silence_end")
    out["frozen"] = spans("lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end")
    return out


def _ffmpeg_log(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"
    return proc.stderr or ""


def measure(original: Path, proxy: Path, has_audio: bool = True) -> dict:
    """Громкость (EBU R128) и тишина (< −45 дБ дольше 0.8 с) — по звуку ГОТОВОГО файла (у копии AAC 64 кбит/с свои
    пики), застывшая картинка (дольше 1 с) — по копии (быстрее, кадры те же)."""
    log = _ffmpeg_log(["ffmpeg", "-hide_banner", "-nostats", "-i", str(proxy), "-an", "-vf",
                       "freezedetect=n=0.003:d=1", "-f", "null", "-"])
    if has_audio:
        log += "\n" + _ffmpeg_log(["ffmpeg", "-hide_banner", "-nostats", "-t", f"{MAX_VIDEO_SEC:g}", "-i",
                                   str(original), "-vn", "-af", "ebur128=peak=true,silencedetect=n=-45dB:d=0.8",
                                   "-f", "null", "-"])
    return parse_measures(log)


# --------------------------------------------------------------------------- #
# Что владелец уже говорил
# --------------------------------------------------------------------------- #


def owner_context(store: Any, target: dict) -> dict:
    """{rules: сквозные правила, project: блок проекта, verdicts: [его слова о прошлых работах]}."""
    from app.owner_rules import OWNER_RULES

    out = {"rules": OWNER_RULES.strip(), "project": "", "verdicts": []}
    project_id = target.get("project_id")
    try:
        from app import project_knowledge as K

        source_id = None
        if target.get("clip_plan_id"):
            plan = store.get_clip_plan(int(target["clip_plan_id"]), include_segments=False)
            source_id = int(plan["source_id"])
        out["project"] = K.prompt_block(store, project_id, source_id, clip_plan_id=target.get("clip_plan_id"))
    except Exception as exc:  # noqa: BLE001 - нет проекта/источника — только сквозные правила
        _log.info("critique: project block failed: %s", exc)
    if project_id or target.get("work_id"):
        clauses, params = [], []
        if project_id:
            clauses.append("w.project_id = ?")
            params.append(int(project_id))
        if target.get("work_id"):
            clauses.append("w.id = ?")
            params.append(int(target["work_id"]))
        try:
            rows = store.db.query_all(
                "SELECT w.title, v.n, v.label, v.status, v.verdict FROM feed_versions v "
                "JOIN feed_works w ON w.id = v.work_id WHERE v.verdict != '' AND (" + " OR ".join(clauses) + ") "
                "ORDER BY COALESCE(v.status_at, v.created_at) DESC LIMIT ?", [*params, MAX_VERDICTS])
        except Exception:  # noqa: BLE001 - нет таблиц ленты (старая база)
            rows = []
        out["verdicts"] = [f"«{r['title']}» {r['label'] or 'v' + str(r['n'])} ({r['status']}): {r['verdict']}"
                           for r in rows]
    return out


# --------------------------------------------------------------------------- #
# Отчёты: хранение и прошлый круг
# --------------------------------------------------------------------------- #


def _reports_file(key: str) -> Path:
    folder = settings.runtime_dir / "critiques"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{re.sub(r'[^a-z0-9-]', '_', key)}.json"


def load_reports(key: str) -> list[dict]:
    try:
        data = json.loads(_reports_file(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_report(key: str, entry: dict) -> None:
    reports = load_reports(key)[-19:]
    reports.append(entry)
    _reports_file(key).write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")


def previous_items(previous: Any) -> list[dict]:
    """Пункты прошлого отчёта — {id, priority, t, where, problem, change} (из сохранённого или переданного)."""
    if isinstance(previous, str):
        try:
            previous = json.loads(previous)
        except ValueError:
            # Свободный текст: каждая непустая строка — пункт.
            lines = [s.strip(" -•\t") for s in previous.splitlines() if s.strip()]
            return [{"id": str(i + 1), "problem": s[:400]} for i, s in enumerate(lines[:MAX_ISSUES])]
    if isinstance(previous, dict):
        # Сохранённая запись ({report: {issues}}), сам отчёт ({issues}) или ответ инструмента.
        report = previous.get("report") if isinstance(previous.get("report"), dict) else previous
        previous = report.get("issues") or []
    out = []
    for i, item in enumerate(previous or []):
        if isinstance(item, dict) and (item.get("problem") or item.get("text")):
            out.append({"id": str(item.get("id") or i + 1), **{k: str(item.get(k) or "")[:400]
                        for k in ("priority", "t", "where", "problem", "change")}})
            if not out[-1]["problem"]:
                out[-1]["problem"] = str(item.get("text"))[:400]
    return out[:MAX_ISSUES]


# --------------------------------------------------------------------------- #
# Запрос к модели
# --------------------------------------------------------------------------- #


def response_schema() -> dict:
    issue = {
        "type": "object",
        "properties": {
            "priority": {"type": "string", "enum": list(PRIORITIES)},
            "t": {"type": "string", "description": "секунда или отрезок ролика: 3.2 или 3.2–4.0"},
            "where": {"type": "string", "description": "где: место в кадре или звук"},
            "problem": {"type": "string", "description": "что не так, простыми словами"},
            "change": {"type": "string", "description": "что поменять, конкретно"},
            "keep": {"type": "string", "description": "что здесь оставить как есть"},
        },
        "required": ["priority", "t", "where", "problem", "change"],
    }
    return {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "summary": {"type": "string", "description": "1–2 предложения: каков ролик целиком"},
            "previous": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "status": {"type": "string", "enum": list(STATUSES)},
                "note": {"type": "string"}}, "required": ["id", "status"]}},
            "issues": {"type": "array", "items": issue},
            "keep": {"type": "array", "items": {"type": "string"}, "description": "что в ролике хорошо — не трогать"},
        },
        "required": ["verdict", "summary", "issues"],
    }


def build_prompt(brief: str, context: dict, measures: dict, duration: float, previous: list[dict]) -> str:
    parts = [
        "Ты — независимый строгий критик готового короткого ролика. Ты его НЕ делал и не знаешь, что задумывал "
        "монтажёр: суди только то, что видно и слышно в приложенном видео, и замеры ниже. Пиши по-русски, простыми "
        "словами, без жаргона; каждое замечание — конкретное место и конкретная правка.",
        f"Длина ролика: {duration:.1f} с. Время в замечаниях — секунды этого ролика (t=3.2 или 3.2–4.0).",
        "ЗАДАЧА РОЛИКА (что просили):\n" + (brief.strip() or "не указана — суди как короткий ролик для TikTok/Shorts"),
        "СКВОЗНЫЕ ПРАВИЛА ВЛАДЕЛЬЦА (из его прошлых жалоб — нарушение правила = P0):\n" + context.get("rules", ""),
    ]
    if context.get("project"):
        parts.append("ЧТО ВЛАДЕЛЕЦ ГОВОРИЛ ПО ПРОЕКТУ:\n" + context["project"])
    if context.get("verdicts"):
        parts.append("ЕГО ВЕРДИКТЫ ПРОШЛЫМ РАБОТАМ (его словами — то, что ему не нравилось, не должно повториться):\n"
                     + "\n".join(f"- {v}" for v in context["verdicts"]))
    parts.append("ЗАМЕРЫ ГОТОВОГО ФАЙЛА (ffmpeg): " + json.dumps(measures, ensure_ascii=False)
                 + ". Громкость площадок — около −14 LUFS; тишина дольше секунды и застывшая картинка — повод "
                   "проверить, не задумано ли это.")
    parts.append(
        "ЧТО ПРОВЕРИТЬ: первые 2 секунды (цепляют ли, понятно ли, о чём ролик); текст и субтитры — успевают ли "
        "прочитать, совпадают ли со сказанным, не под кнопками площадок (правый край и нижние ~20 % кадра) и не на "
        "лицах; застывшие и пустые места; рывки, вспышки, обрывки на склейках; звук — голос разборчив, музыка не "
        "громче голоса, музыка не обрывается посреди фразы, концовка не обрублена; соответствие задаче и правилам "
        "владельца.")
    parts.append(
        "ПРИОРИТЕТЫ: P0 — ломает ролик или нарушает правило владельца, сдавать нельзя; P1 — заметно портит, лучше "
        "поправить до сдачи; P2 — шлифовка. Вердикт «сдавать» — только если нет P0 и не больше двух P1; иначе "
        "«ещё проход». Не выдумывай проблем ради списка: нет замечаний — пустой issues. Не больше "
        f"{MAX_ISSUES} замечаний, самые важные первыми. В keep замечания — что в этом месте хорошо и должно "
        "остаться (чтобы правка не сломала удачное).")
    if previous:
        parts.append(
            "ПРОШЛЫЙ КРУГ: другой критик нашёл в прошлой версии эти пункты. Для КАЖДОГО дай статус в previous "
            "(id как здесь): «исправлено», «частично» или «осталось», и коротко почему (note). Новые проблемы — "
            "в issues, повторять там оставшиеся не нужно.\n"
            + "\n".join(f"- id {p['id']}: [{p.get('priority') or '?'}] t={p.get('t') or '?'} · {p.get('where') or ''} "
                        f"· {p['problem']}" + (f" → {p['change']}" if p.get("change") else "") for p in previous))
    parts.append("Ответ — только JSON по схеме.")
    return "\n\n".join(parts)


def _video_part(client: Any, proxy: Path, duration: float) -> tuple[dict, dict | None]:
    """Видео в запрос: маленькое — прямо в теле, большое — через Files API (как анализ источников)."""
    fps = 2 if duration <= 90 else 1
    meta = {"fps": fps}
    if proxy.stat().st_size <= INLINE_MAX_BYTES:
        data = base64.b64encode(proxy.read_bytes()).decode("ascii")
        return {"inline_data": {"mime_type": "video/mp4", "data": data}, "video_metadata": meta}, None
    info = client.wait_file_active(client.upload_file(proxy, "video/mp4"))
    return {"file_data": {"mime_type": "video/mp4", "file_uri": info["uri"]}, "video_metadata": meta}, info


def build_payload(video_part: dict, prompt: str) -> dict:
    return {
        "contents": [{"role": "user", "parts": [video_part, {"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json",
                             "responseJsonSchema": response_schema(), "mediaResolution": "MEDIA_RESOLUTION_LOW"},
    }


def _text_of(response: dict) -> str:
    from app.ai.gemini import _extract_response_text

    return _extract_response_text(response)


def clean_report(raw: Any, previous: list[dict]) -> dict:
    """Ответ модели → отчёт: поля обрезаны, пункты с номерами, вердикт согласован с P0/P1."""
    if not isinstance(raw, dict):
        raise CritiqueError("критик ответил не JSON-объектом")
    issues = []
    for item in raw.get("issues") or []:
        if not isinstance(item, dict) or not str(item.get("problem") or "").strip():
            continue
        priority = item.get("priority") if item.get("priority") in PRIORITIES else "P1"
        issues.append({"id": str(len(issues) + 1), "priority": priority,
                       **{k: str(item.get(k) or "").strip()[:400] for k in ("t", "where", "problem", "change", "keep")}})
    order = {p: i for i, p in enumerate(PRIORITIES)}
    issues.sort(key=lambda x: order[x["priority"]])
    for i, item in enumerate(issues[:MAX_ISSUES]):
        item["id"] = str(i + 1)
    issues = issues[:MAX_ISSUES]
    known = {p["id"]: p for p in previous}
    statuses = {}
    for item in raw.get("previous") or []:
        if isinstance(item, dict) and str(item.get("id")) in known and item.get("status") in STATUSES:
            statuses[str(item["id"])] = {"status": item["status"], "note": str(item.get("note") or "")[:300]}
    prev_out = [{"id": pid, "was": known[pid]["problem"][:160], "priority": known[pid].get("priority") or "",
                 **statuses.get(pid, {"status": "не проверено", "note": "критик не ответил по этому пункту"})}
                for pid in known]
    verdict = raw.get("verdict") if raw.get("verdict") in VERDICTS else "ещё проход"
    p0 = sum(1 for x in issues if x["priority"] == "P0")
    p1 = sum(1 for x in issues if x["priority"] == "P1")
    left_p0 = sum(1 for x in prev_out if x["priority"] == "P0" and x["status"] in ("осталось", "частично"))
    forced = None
    if verdict == "сдавать" and (p0 or left_p0 or p1 > 2):
        verdict, forced = "ещё проход", "есть P0 или больше двух P1 — сдавать рано"
    report = {"verdict": verdict, "summary": str(raw.get("summary") or "").strip()[:600], "issues": issues,
              "keep": [str(x).strip()[:200] for x in raw.get("keep") or [] if str(x).strip()][:8]}
    if previous:
        report["previous"] = prev_out
    if forced:
        report["verdict_note"] = forced
    return report


def report_lines(report: dict) -> list[str]:
    """Отчёт строками: «P0 · t=… · где · что не так → что поменять · оставить: …»."""
    lines = [f"Вердикт: {report['verdict']}" + (f" — {report['summary']}" if report.get("summary") else "")]
    for x in report.get("issues") or []:
        line = f"{x['priority']} · t={x['t'] or '?'} · {x['where'] or '—'} · {x['problem']} → {x['change'] or '?'}"
        if x.get("keep"):
            line += f" · оставить: {x['keep']}"
        lines.append(line)
    if not report.get("issues"):
        lines.append("Замечаний нет.")
    for p in report.get("previous") or []:
        lines.append(f"Прошлое {p['id']} ({p['was'][:60]}): {p['status']}" + (f" — {p['note']}" if p.get("note") else ""))
    if report.get("keep"):
        lines.append("Не трогать: " + "; ".join(report["keep"]))
    return lines


def critique(store: Any, target: dict, brief: str = "", previous: Any = None, fresh: bool = False,
             client: Any = None) -> dict:
    """Один круг критики. ``previous`` — прошлый отчёт (иначе берётся сохранённый для этой работы/клипа, если
    там был ДРУГОЙ файл; ``fresh`` — без прошлого круга)."""
    from app import gemini_usage
    from app.ai.gemini import GeminiClient
    from app.ingest import probe_media

    meta = probe_media(Path(target["path"]))
    duration = float(meta.duration_sec or 0)
    if duration <= 0.2:
        raise CritiqueError("у ролика нет длины")
    stored = load_reports(target["key"])
    prev_entry = None
    if previous not in (None, "") and not fresh:
        prev_items = previous_items(previous)
    else:
        prev_entry = None if fresh else next((r for r in reversed(stored) if r.get("file") != target["path"]
                                              or r.get("render_clip_id") != target.get("render_clip_id")), None)
        prev_items = previous_items(prev_entry["report"]["issues"]) if prev_entry else []
    context = owner_context(store, target)
    brief = (brief or "").strip()[:MAX_BRIEF] or (f"Ролик «{target['title']}»" if target.get("title") else "")
    run_id = uuid.uuid4().hex[:10]
    proxy = settings.tmp_dir / f"critique-{run_id}.mp4"
    uploaded = None
    client = client or GeminiClient()
    model = settings.gemini_video_model
    started = time.monotonic()
    try:
        make_proxy(Path(target["path"]), proxy)
        measures = measure(Path(target["path"]), proxy, has_audio=_has_audio(meta))
        prompt = build_prompt(brief, context, measures, min(duration, MAX_VIDEO_SEC), prev_items)
        session = f"critique:{run_id}"
        with gemini_usage.scope(PURPOSE, session=session, project_id=target.get("project_id"),
                                clip_id=target.get("clip_plan_id")):
            part, uploaded = _video_part(client, proxy, duration)
            response = client.generate_content(model, build_payload(part, prompt))
        text = _text_of(response)
        try:
            raw = json.loads(text.strip().strip("`").removeprefix("json").strip())
        except ValueError as exc:
            raise CritiqueError("критик ответил не JSON") from exc
        report = clean_report(raw, prev_items)
    finally:
        proxy.unlink(missing_ok=True)
    cost = _cost(store, f"critique:{run_id}")
    entry = {"id": run_id, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "file": target["path"],
             "render_clip_id": target.get("render_clip_id"), "feed_version_id": target.get("feed_version_id"),
             "brief": brief, "report": report, "model": model, "cost": cost,
             "round": (len(stored) + 1), "previous_id": prev_entry["id"] if prev_entry else None}
    save_report(target["key"], entry)
    return {"critique_id": run_id, "round": entry["round"], "model": model, "report": report,
            "lines": report_lines(report), "measures": measures, "cost": cost,
            "seconds": round(time.monotonic() - started, 1),
            "saw": {"video": f"копия {PROXY_HEIGHT}p {PROXY_FPS} к/с", "brief": bool(brief),
                    "owner_rules": True, "project_notes": bool(context.get("project")),
                    "owner_verdicts": len(context.get("verdicts") or []), "previous_items": len(prev_items),
                    "uploaded_file": bool(uploaded)}}


def _has_audio(meta: Any) -> bool:
    streams = (getattr(meta, "raw", None) or {}).get("streams")
    if not isinstance(streams, list):
        return True  # не знаем — пробуем со звуком (замер без звука просто не найдёт громкость)
    return any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in streams)


def _cost(store: Any, session: str) -> dict:
    from app import gemini_usage

    try:
        total = gemini_usage.totals(store, session=session)["total"]
    except Exception:  # noqa: BLE001 - учёт не роняет ответ
        return {}
    return {"calls": total["calls"], "tokens": total["total_tokens"], "cost_usd": total["cost_usd"],
            "cost_rub": total["cost_rub"]}
