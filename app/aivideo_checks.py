"""ИИ-видео: проверки готового — кадры видео для QA, стыки планов (из ``app/aivideo_gen.py``)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import projects as P
from app.settings import settings
from app.aivideo_prompts import start_frame_ref, end_frame_ref, _asset_path, _prev_frame

def _vision_client() -> Any:
    from app.ai.gemini import GeminiClient

    return GeminiClient(timeout=90)


# --------------------------------------------------------------------------- #
# Проверка ГОТОВОГО видео плана (Gemini-зрение, бесплатно): 4–6 кадров против state и карт
# --------------------------------------------------------------------------- #

VIDEO_QA_FRAMES = 5


def sample_video_frames(video: Path, dest_dir: Path, count: int = VIDEO_QA_FRAMES) -> list[tuple[float, Path]]:
    """4–6 кадров видео равномерно, первый и последний — у краёв (jpeg ≤ 512 px по высоте)."""
    import subprocess

    from app.dubbing import probe_duration

    count = max(4, min(6, int(count)))
    try:
        duration = float(probe_duration(video) or 0)
    except Exception:  # noqa: BLE001
        duration = 0.0
    duration = duration if duration > 0 else 4.0
    dest_dir.mkdir(parents=True, exist_ok=True)
    last = max(0.0, duration - 0.15)
    times = [round(0.05 + (last - 0.05) * k / (count - 1), 2) for k in range(count)]
    out = []
    for k, t in enumerate(times):
        path = dest_dir / f"qa-{k}.jpg"
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                               "-vf", "scale=-2:'min(512,ih)'", "-q:v", "4", str(path)],
                              capture_output=True, timeout=60, check=False)
        if proc.returncode == 0 and path.is_file() and path.stat().st_size:
            out.append((t, path))
    return out


def video_qa_flags(store: Any, script_id: int) -> list[dict]:
    """Итоги проверки выбранных видео планов серии: план, ok, оценка, проблемы."""
    try:
        script = V.get_script(store, script_id)
    except KeyError:
        return []
    out = []
    for shot in script["shots"]:
        vid = V.selected_ref(store, int(script["project_id"]), "shot", int(shot["id"]), "shot_video")
        qa = (vid or {}).get("score") or {}
        if not vid or qa.get("kind") != "video_qa" or qa.get("error"):
            continue
        out.append({"idx": shot["idx"], "shot_id": shot["id"], "ref_id": vid["id"], "ok": bool(qa.get("ok")),
                    "score": qa.get("score"), "issues": qa.get("issues") or [], "stale": bool(vid["stale"])})
    return out


# --------------------------------------------------------------------------- #
# Постоянство склеек (Gemini-зрение, бесплатно; перед этапом video)
# --------------------------------------------------------------------------- #

CONTINUITY_PROMPT = """\
Ты монтажёр мультсериала и до оплаты видео проверяешь ключевые кадры. Проверки двух видов:
* kind "cut" — склейка: ПРОШЛЫЙ КАДР (последний кадр предыдущего плана) и СТАРТОВЫЙ КАДР этого плана,
  действие предыдущего плана (оно происходит МЕЖДУ кадрами) и заявленное состояние на старте этого
  плана (поза, куда смотрит, где в кадре, что в руках, одежда, реквизит). Смену ракурса ошибкой не считай.
* kind "pair" — пара кадров ОДНОГО плана: СТАРТ и КОНЕЦ; видеомодель будет плавно двигаться от старта
  к концу. Конец должен показывать результат ОДНОГО действия плана и заявленное конечное состояние; всё
  остальное (персонажи и их дизайн, одежда, фон, свет, ракурс, число предметов) — как на старте.
Найди несоответствия: поза/взгляд/руки/одежда/реквизит не как заявлено, без причины поменялись одежда,
реквизит, причёска, свет, локация, дизайн персонажа, лишние/пропавшие/раздвоившиеся персонажи или
предметы, персонаж или предмет висит в воздухе.
Для каждой проверки: idx плана, kind, score 0–10 (10 — чисто), ok, mismatches — список
{what: pose|facing|position|holding|clothing|props|count|character|design|floating|lighting|location|end_state,
detail — коротко по-русски}. Верни ТОЛЬКО JSON."""


def _inline(path: Path) -> dict:
    import base64

    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode("ascii")}}


def _save_continuity(store: Any, shot_id: int, data: dict) -> None:
    store.db.execute("UPDATE aiv_shots SET continuity_json = ? WHERE id = ?",
                     (json.dumps(data, ensure_ascii=False), int(shot_id)))


def check_continuity(store: Any, script_id: int, client: Any = None) -> dict:
    """Ключевые кадры серии ДО видео: склейки (стартовый кадр плана против конца прошлого и
    заявленного состояния; общий кадр сцепки совпадает по построению) и пары кадров планов (старт
    против конца: одно действие, конечное состояние, тот же дизайн и счёт). Результат — на
    раскадровке (``shot.continuity``) и в карточке этапа video; платное само не перезапускается."""
    script = V.get_script(store, script_id)
    project_id = int(script["project_id"])
    if client is None and not settings.gemini_api_key:
        return {"ok": True, "skipped": True, "shots": [],
                "note": "Gemini не настроен — проверка кадров пропущена (сравни кадры глазами: aiv_get_script)"}
    shots = script["shots"]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    checks: list[tuple[dict, str]] = []  # (план, kind)
    entries: dict[int, dict] = {}  # shot_id → итог
    parts: list[dict] = [{"text": CONTINUITY_PROMPT}]
    for n, shot in enumerate(shots):
        prev = shots[n - 1] if n else None
        nxt = shots[n + 1] if n + 1 < len(shots) else None
        start, shared = start_frame_ref(store, project_id, shots, n)
        end = end_frame_ref(store, project_id, shot)
        entry = {"ok": True, "score": None, "mismatches": [], "checked_at": stamp,
                 "start_ref_id": start["id"] if start else None, "end_ref_id": end["id"] if end else None}
        start_path, end_path = _asset_path(store, start), _asset_path(store, end)
        if prev is not None and shared:
            entry.update({"chain": True, "note": f"старт — конечный кадр плана {prev['idx']} (общий кадр)"})
        elif prev is not None and start_path is not None:
            prev_frame, prev_kind = _prev_frame(store, project_id, prev)
            if prev_frame is not None:
                entry["against"] = prev_kind
                checks.append((shot, "cut"))
                prev_state = V.state_text(store, project_id, V.end_state_of(prev, shot)) or "не задано"
                state = V.state_text(store, project_id, shot.get("state") or {}) or "не задано"
                parts.append({"text": f"ПРОВЕРКА idx={shot['idx']} kind=cut (продолжает прошлый: "
                                      f"{'да' if shot.get('continues_from_prev') else 'нет, новый момент/ракурс'}). "
                                      f"Действие предыдущего плана: {prev.get('action') or '—'}. Конец предыдущего: "
                                      f"{prev_state}. Заявленное состояние на старте этого: {state}. ПРОШЛЫЙ КАДР:"})
                parts.append(_inline(prev_frame))
                parts.append({"text": "СТАРТОВЫЙ КАДР:"})
                parts.append(_inline(start_path))
        if start_path is not None and end_path is not None:
            checks.append((shot, "pair"))
            parts.append({"text": f"ПРОВЕРКА idx={shot['idx']} kind=pair. Одно действие плана: "
                                  f"{shot.get('action') or '—'}. Конечное состояние: "
                                  f"{V.state_text(store, project_id, V.end_state_of(shot, nxt)) or 'не задано'}. "
                                  f"Реквизит: {V.prop_counts(shot.get('state') or {}) or '—'}. СТАРТ:"})
            parts.append(_inline(start_path))
            parts.append({"text": "КОНЕЦ:"})
            parts.append(_inline(end_path))
        if prev is not None or end_path is not None:
            entries[int(shot["id"])] = entry
    if checks:
        schema = {"type": "object", "properties": {"checks": {"type": "array", "items": {
            "type": "object", "properties": {
                "idx": {"type": "integer"}, "kind": {"type": "string", "enum": ["cut", "pair"]},
                "score": {"type": "number"}, "ok": {"type": "boolean"},
                "mismatches": {"type": "array", "items": {"type": "object", "properties": {
                    "what": {"type": "string"}, "detail": {"type": "string"}}, "required": ["what", "detail"]}}},
            "required": ["idx", "kind", "score", "ok", "mismatches"]}}}, "required": ["checks"]}
        from app.ai.gemini import GeminiClient, _extract_response_text

        payload = {"contents": [{"role": "user", "parts": parts}],
                   "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                                        "responseSchema": schema}}
        response = (client or GeminiClient()).generate_content(settings.gemini_text_model, payload)
        try:
            data = json.loads(_extract_response_text(response))
        except json.JSONDecodeError as exc:
            raise V.AivError("Gemini вернул не-JSON — повтори проверку кадров") from exc
        items = data.get("checks") if isinstance(data.get("checks"), list) else data.get("shots") or []
        got = {(int(i.get("idx") or 0), str(i.get("kind") or "cut")): i for i in items if isinstance(i, dict)}
        for shot, kind in checks:
            item = got.get((int(shot["idx"]), kind))
            if item is None:
                continue
            entry = entries[int(shot["id"])]
            mism = [{"what": P._text(m.get("what"), 30), "detail": P._text(m.get("detail"), 200), "check": kind}
                    for m in item.get("mismatches") or [] if isinstance(m, dict)][:8]
            score = max(0.0, min(10.0, float(item.get("score") or 0)))
            entry["mismatches"] += mism
            entry["score"] = score if entry["score"] is None else min(entry["score"], score)
            entry["ok"] = entry["ok"] and bool(item.get("ok")) and score >= 6 and not mism
            entry.setdefault("checks", []).append(kind)
            entry["model"] = settings.gemini_text_model
    results = []
    for shot in shots:
        entry = entries.get(int(shot["id"]))
        if entry is None or (not entry.get("checks") and not entry.get("chain")):
            continue
        _save_continuity(store, shot["id"], entry)
        results.append({"idx": shot["idx"], **entry})
    flagged = [r["idx"] for r in results if not r.get("ok")]
    return {"ok": True, "shots": results, "flagged": flagged,
            "note": ("кадры с несоответствиями: " + ", ".join(f"план {i}" for i in flagged)
                     + " — перерисуй кадр (aiv_regenerate shot:ID, дёшево) или поправь state/действие; "
                       "платное само не перезапускается") if flagged else "склейки и пары кадров чистые"}


def continuity_flags(store: Any, script_id: int) -> list[dict]:
    """Сохранённые проверки кадров: план, ok, что не так, устарела ли (кадр с тех пор сменился)."""
    try:
        script = V.get_script(store, script_id)
    except KeyError:
        return []
    out = []
    shots = script["shots"]
    pid = int(script["project_id"])
    for n, shot in enumerate(shots):
        data = shot.get("continuity")
        if not data:
            continue
        start = start_frame_ref(store, pid, shots, n)[0]
        end = end_frame_ref(store, pid, shot)
        outdated = any(data.get(key) and (not ref or int(ref["id"]) != int(data[key]))
                       for key, ref in (("start_ref_id", start), ("end_ref_id", end)))
        out.append({"idx": shot["idx"], "shot_id": shot["id"], "ok": bool(data.get("ok")), "score": data.get("score"),
                    "mismatches": data.get("mismatches") or [], "outdated": outdated,
                    "chain": bool(data.get("chain")), "checks": data.get("checks") or []})
    return out
