"""Объяснялки: конвейер «тема → готовый ролик» (docs/guides/explainer.md).

Шаги ролика (``expl_videos.status``), каждый повторяем и идемпотентен; ведёт ``advance`` (работник
``ExplainerWorker`` или агент ``explainer_continue``):

1. ``new`` → **сценарий** (Gemini, бесплатно): крючок в первые 2 с, вступление, 3–5 пунктов,
   призыв; русский, короткие фразы, ~15 символов речи в секунду. Gemini недоступен — ``failed`` с
   подсказкой: агент пишет текст сам (``explainer_set_script``).
2. ``scripted`` → **биты**: текст режется по фразам и частям фраз на биты по 1.5–3 с (оценка по
   длине текста; после озвучки — по настоящим словам). На каждый бит Gemini выбирает позу из
   библиотеки маскота (кандидаты — по тегам) или описывает новую; плюс чипы (числа/время) и
   ключевые слова субтитров. Плашки «#1/#2/#3» — по пунктам сценария, без Gemini. Новая поза,
   почти совпадающая с готовой (``similar_pose``), не рисуется. Gemini недоступен — позы по тегам
   (без новых).
3. ``planned`` → **оценка и деньги**: бесплатная озвучка (Gemini TTS) — сразу, не вышла — ролик
   останавливается с понятной ошибкой (без голоса не выходит); новые позы и платная озвучка — одна
   карточка владельцу (этап ``explainer`` машинерии ИИ-видео: «Разрешить», потолки, журнал трат).
   Ничего нового — сразу дальше.
4. ``awaiting_approval`` / ``generating`` → ждём «Разрешить» и запуски (работник ИИ-видео).
5. ``assembling`` → **сборка**: слова сценария на времени распознанной речи (Whisper large-v3-turbo даёт
   только время: текст голоса известен, его ошибки в субтитры не идут; не распознал речь или в ней нет
   текста сценария — ролик останавливается; только сухой прогон, ``options.dry_run`` — по длине
   текста) → биты по времени (длинные > 3.6 с делятся тем же кадром с наездом) → холст-клип как
   пилот 887: позы слоями ``image`` с чередованием pop/zoom, вжухи на склейках, плашки с «динь»,
   чипы с «поп», слой субтитров (hormozi, крупно, капсом, ключевые слова — ``emph``), музыка
   маскота под голосом тише. По желанию (``options.render``) — рендер, обложки, заголовок/описание.

Где что лежит. Здесь — конвейер и всё, что читает подменяемые тестами имена (``_ask``, ``gemini_client``, ``transcribe_words``, ``_sfx_ok``, ``auto_mix_clip``,
``finish_publish``). Вынесено: сценарий и разбивка на битвы ``app/explainer_gen_script.py``, раскладка слотов ``app/explainer_gen_plan.py``, слова по времени
``app/explainer_gen_words.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import aivideo_gen as G
from app import autopilot as A
from app import clip_groups as CG
from app import explainer as E
from app import projects as P
from app import word_fixes as WF
from app.settings import settings

from app.explainer_gen_script import (  # noqa: F401
    CPS, BEAT_MIN_SEC, BEAT_MAX_SEC, BEAT_MERGE_SEC, HOOK_MAX_CHARS, script_schema, script_context, _sentence,
    normalize_script, set_script, _est, _clauses, _chunk, split_beats,
)
from app.explainer_gen_plan import (  # noqa: F401
    MAX_NEW_POSES, plan_schema, _library_brief, plan_context, _CHIP_RE, _UNITS, _ORDINAL_CHIP, _not_point_number,
    _fallback_chip, _fallback_emphasis, match_scores, assign_by_tags, _assign_fallback, _chip, _badge, _words_of,
    _apply_gemini_plan, _unrepeat, update_beats,
)
from app.explainer_gen_words import (  # noqa: F401
    REAL_SPLIT_SEC, proportional_words, _norm, align, place, heard_share, _DIGITS_RE, _NUMERAL_RE, _digits_for,
    _word_tail, script_words, _word_in, _NUMBER_WORD, emphasis_marks,
)

_log = logging.getLogger(__name__)


TAIL_SEC = 0.8
VOICE_MIN_HEARD = 0.6  # меньше этой доли слов сценария в распознанной речи — озвучка не та (тон, чужой текст)
# Только настоящие записи пака freesound. Пилот звал whoosh-2 и ding — это алиасы пака
# (app/motion_assets.FREESOUND_ALIASES): whoosh-soft и pop-soft; здесь — настоящие имена.
CUT_SFX = ("sfx:freesound/whoosh-1.ogg", "sfx:freesound/whoosh-soft.ogg")
BADGE_SFX = "sfx:freesound/pop-soft.ogg"
CHIP_SFX = "sfx:freesound/pop.ogg"
WHOOSH_VOLUME = 0.35
DING_VOLUME = 0.45
POP_VOLUME = 0.3
MUSIC_DUCK_DB = -8.0
POP_SEC = 0.18
BADGE_MAX_SEC = 2.0
CHIP_SEC = 1.3
PUNCH_SCALE = 1.14
LAYER_BATCH = 28

# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #


def gemini_client() -> Any:
    """Клиент Gemini или None (нет ключа). Тесты подменяют."""
    if not settings.gemini_api_key:
        return None
    from app.ai.gemini import GeminiClient

    return GeminiClient(timeout=120)


def _ask(client: Any, rules: str, context: dict, schema: dict, temperature: float = 0.8) -> dict:
    from app.ai.gemini import _extract_response_text

    payload = {"contents": [{"role": "user", "parts": [{"text": rules},
                                                       {"text": json.dumps(context, ensure_ascii=False)}]}],
               "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                                    "responseSchema": schema}}
    response = client.generate_content(settings.gemini_text_model, payload)
    try:
        data = json.loads(_extract_response_text(response))
    except json.JSONDecodeError as exc:
        raise E.ExplError("Gemini вернул не-JSON") from exc
    if not isinstance(data, dict):
        raise E.ExplError("Gemini вернул не объект")
    return data


# --------------------------------------------------------------------------- #
# 1. Сценарий
# --------------------------------------------------------------------------- #

SCRIPT_RULES = """\
Ты сценарист вертикальных роликов-объяснялок (Shorts/TikTok/Reels, 30–60 с). Маскот из context
рассказывает тему зрителю голосом за кадром (на экране он меняет позы). Пиши ПО-РУССКИ, живым
разговорным языком, на «ты», короткими фразами (до 12 слов), без канцелярита, эмодзи и хэштегов.
Структура:
- hook — крючок: ОДНА фраза до 30 символов, звучит за 2 секунды; вопрос-боль или неожиданный факт
  («Спишь восемь часов, а встаёшь разбитым?»);
- intro — одна короткая фраза: что будет («Есть три ошибки, которые крадут твой сон.»), можно пусто;
- points — 3–5 пунктов (сколько просит тема и длина): title — фраза-заголовок пункта, начинается
  с номера словами («Ошибка номер один: телефон в кровати.», «Совет номер два: …»); body — 2–3
  короткие фразы: почему это важно и что конкретно сделать;
- cta — призыв в конце (результат + «подпишись…»), одна-две фразы.
Объём всего текста — примерно duration_sec × 15 символов (±10%). Числа и время пиши словами
(«в семь», «до двух часов дня») — так их правильно прочитает озвучка. Ничего не выдумывай про
здоровье и деньги без меры: только общеизвестные безопасные советы, без обещаний сумм и дозировок.
Соблюдай правила владельца (owner_rules) и тон (tone). Есть niche — держись ниши канала; angle — подход
к теме; owner_feedback — что владелец просил переделать в прошлой версии ролика: исправь именно это.
title — название ролика до 60 символов.
Верни ТОЛЬКО JSON по схеме.
"""


def write_script(store: Any, video_id: int, client: Any = None) -> dict:
    video = E.get_video(store, video_id)
    client = client or gemini_client()
    if client is None:
        raise E.ExplError("Gemini не настроен (GEMINI_API_KEY) — напиши текст сам: explainer_set_script(video_id, "
                          "hook, intro, points [{title, body}], cta)")
    data = _ask(client, SCRIPT_RULES, script_context(store, video), script_schema(), 0.9)
    return set_script(store, video_id, data, source="gemini")


PLAN_RULES = """\
Ты монтажёр роликов-объяснялок. Маскот (mascot) рассказывает тему; на каждом бите (1.5–3 с) кадр
меняется — маскот в новой позе на плоском фоне. Для КАЖДОГО бита (beats) реши:
- pose_id — id позы из библиотеки (library), если она подходит по смыслу бита: эмоция, жест,
  предмет. ПЕРЕИСПОЛЬЗУЙ библиотеку: готовая поза бесплатна, новая стоит денег. candidates бита —
  лучшие позы по тегам (но можно любую из library);
- если в библиотеке нет ничего близкого по смыслу — pose_id 0 и new_pose_key: ключ новой позы из
  new_poses (одну новую позу можно дать нескольким битам). Новых — как можно меньше;
- соседние биты — РАЗНЫЕ позы;
- бит с point_start N (начало пункта N списка) — поза, где маскот показывает N пальцами
  (number_shown N); нет такой в библиотеке — новая с number_shown N;
- new_poses[]: description — ПО-АНГЛИЙСКИ одна фраза 10–30 слов, только поза: выражение лица,
  жест, 0–2 простых предмета в руках или рядом; без фона, без текста и цифр (пример: "shocked,
  eyes extremely wide, feathers bristled, a bright smartphone screen in front of it"); title — 2–4
  слова по-русски; tags — 5–10 коротких тегов по-русски и по-английски (эмоция, жест, предметы,
  тема); emotion — одно слово по-английски; number_shown — сколько пальцев показывает (или 0);
- chip — маленькая плашка, если в бите есть конкретное число или время: «07:00», «8 ч», «−30%»,
  «2 л» (до 8 символов); chip_word — слово бита, на котором она появляется. 2–5 чипов на ролик,
  пусто, если числа нет. Номер пункта («номер два») — не чип: его показывает отдельная плашка;
- emphasis — 0–2 ключевых слова бита (точно как в тексте бита): их выделит субтитр — числа,
  главное существительное или глагол.
Верни ТОЛЬКО JSON по схеме: beats — ровно столько, сколько в запросе, в том же порядке (i).
"""


def plan_beats(store: Any, video_id: int, client: Any = None) -> dict:
    """Биты ролика с позами и оверлеями (Gemini; без него — по тегам)."""
    video = E.get_video(store, video_id)
    if not E.narration_text(video):
        raise E.ExplError("у ролика нет сценария")
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    beats = split_beats(video["script"])
    poses = E.list_poses(store, int(mascot["id"]))
    notes: list[str] = []
    dry = E.is_dry(video)
    client = client if client is not None else gemini_client()
    source = "tags"
    if client is not None:
        try:
            data = _ask(client, PLAN_RULES, plan_context(store, video, beats, poses), plan_schema(), 0.4)
            notes += _apply_gemini_plan(store, mascot, beats, poses, data, dry)
            source = "gemini"
        except Exception as exc:  # noqa: BLE001 - Gemini часто заблокирован: по тегам
            _log.warning("explainer %s: Gemini plan failed: %s", video_id, exc)
            notes.append(f"Gemini не ответил ({type(exc).__name__}: {str(exc)[:120]})")
            notes += _assign_fallback(beats, poses, dry)
    else:
        notes += _assign_fallback(beats, poses, dry)
    poses = E.list_poses(store, int(mascot["id"]))
    _unrepeat(beats, poses)
    by_id = {int(p["id"]): p for p in poses}
    for beat in beats:
        pose = by_id.get(int(beat["pose_id"]))
        beat["pose_title"] = pose["title"] if pose else ""
        beat["pose_new"] = bool(pose and not E.pose_usable(pose, dry))
    new = sorted({b["pose_id"] for b in beats if b["pose_new"]})
    # Сценарий заменили, пока шёл подбор поз (PUT /script): биты старого текста не пишем, иначе голос нового
    # текста идёт с битами старого (10.11: плашки #1–#3 не на своих словах). Ролик остаётся «scripted» —
    # следующий шаг спланирует новый текст.
    if E.narration_text(E.get_video(store, video_id)) != E.narration_text(video):
        return {"ok": False, "video_id": int(video_id), "stale": True,
                "notes": ["сценарий сменился во время подбора поз — планирую заново"]}
    E.set_status(store, video_id, "planned", None, beats=beats, notes=notes, error="")
    return {"ok": True, "video_id": int(video_id), "beats": len(beats), "source": source,
            "reused": len({b["pose_id"] for b in beats}) - len(new), "new_poses": new, "notes": notes}


# --------------------------------------------------------------------------- #
# 3–4. Деньги и генерация
# --------------------------------------------------------------------------- #


def estimate(store: Any, video_id: int) -> dict:
    """Что ещё нужно ролику и сколько стоит (бесплатно, ничего не запускает)."""
    video = E.get_video(store, video_id)
    est = G.estimate(store, int(video["project_id"]), E.STAGE, None, [E.video_group(video_id)])
    poses = [i for i in est["items"] if i["ref_kind"] == E.POSE_KIND]
    tts = [i for i in est["items"] if i["kind"] == "tts"]
    reused = len({b["pose_id"] for b in video.get("beats") or []}) - len(poses)
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    return {"video_id": int(video_id), "total_rub": est["total_rub"], "paid": est["paid"],
            "voice": E.describe_voice(E.mascot_voice(store, mascot, E.is_dry(video))), "new_poses": len(poses), "poses_rub": round(sum(i["estimate_rub"] for i in poses), 2),
            "narration_rub": round(sum(i["estimate_rub"] for i in tts), 2), "reused_poses": max(0, reused),
            "narration_ready": not tts, "items": [{k: i.get(k) for k in ("label", "kind", "provider", "model",
                                                                           "estimate_rub", "price_note")}
                                                  for i in est["items"]],
            "problems": est["problems"], "budget": est["budget"], "note": est["note"]}


def request_generation(store: Any, video_id: int, requested_by: str = "explainer") -> dict:
    """Бесплатная озвучка — сразу (не вышла — стоп: без голоса ролика нет, и платить за позы незачем);
    платное — одна карточка владельцу."""
    video = E.get_video(store, video_id)
    pid = int(video["project_id"])
    group = E.video_group(video_id)
    narr = G.estimate(store, pid, E.STAGE, None, [f"{group}:{E.NARRATION_KIND}"])
    if narr["items"] and not narr["paid"]:
        G.request_stage(store, pid, E.STAGE, None, [f"{group}:{E.NARRATION_KIND}"], requested_by=requested_by)
        job = _target_jobs(store, pid, "expl_video", int(video_id), E.NARRATION_KIND)
        if job and job["status"] in BAD_JOB and E.narration_ref(store, E.get_video(store, video_id)) is None:
            why = " ".join(str(job.get("error") or job["status"]).split())[:300].replace(" — ", ": ")
            error = (f"Озвучка не получилась ({why}). Ролик без голоса не выпускаю — проверьте, доступен ли Gemini, "
                     "и нажмите «Продолжить»")
            E.set_status(store, video_id, "failed", ("planned",), error=error)
            return {"status": "failed", "error": error}
    est = estimate(store, video_id)
    if not est["items"]:
        E.set_status(store, video_id, "generating", ("planned",), estimate_rub=0.0, approval_id=None)
        return {"status": "ready", "estimate": est}
    note = f"тема: {P._text(video['topic'], 200)}"
    try:
        req = G.request_stage(store, pid, E.STAGE, None, [group], requested_by=requested_by, note=note)
    except G.BudgetError as exc:
        E.set_status(store, video_id, "failed", ("planned",), error=str(exc)[:600])
        return {"status": "failed", "error": str(exc), "estimate": est}
    if req["status"] in ("awaiting_approval", "approved"):
        E.set_status(store, video_id, "awaiting_approval", ("planned",), approval_id=req["approval_id"],
                     estimate_rub=est["total_rub"])
        return {"status": "awaiting_approval", "approval_id": req["approval_id"], "estimate": est,
                "note": req["note"]}
    E.set_status(store, video_id, "generating", ("planned",), estimate_rub=0.0, approval_id=None)
    return {"status": "generating", "estimate": est, "run": req.get("run")}


BAD_JOB = ("failed", "blocked_policy", "uncertain", "cancelled")


def _target_jobs(store: Any, project_id: int, target_type: str, target_id: int, kind: str) -> dict | None:
    row = store.db.query_one("SELECT * FROM aiv_gen_jobs WHERE project_id = ? AND target_type = ? AND target_id = ? "
                             "AND ref_kind = ? ORDER BY id DESC LIMIT 1",
                             (int(project_id), target_type, int(target_id), kind))
    return dict(row) if row else None


def readiness(store: Any, video_id: int) -> dict:
    """Готово ли всё к сборке: озвучка и картинка у каждой позы битов; что упало."""
    video = E.get_video(store, video_id)
    pid = int(video["project_id"])
    dry = E.is_dry(video)
    missing, failed, active, held = [], [], [], []
    checks = []
    if E.narration_ref(store, video) is None:
        checks.append(("expl_video", int(video_id), E.NARRATION_KIND, "озвучка"))
    for pose_id in sorted(set(E.beat_pose_ids(video))):
        pose = E.get_pose(store, pose_id)
        if not E.pose_usable(pose, dry):
            checks.append(("pose", pose_id, E.POSE_KIND, f"поза «{pose['title']}»"))
    for target_type, target_id, kind, label in checks:
        job = _target_jobs(store, pid, target_type, target_id, kind)
        missing.append(label)
        if job is None:
            continue
        if job["status"] in BAD_JOB:
            failed.append(f"{label}: {job['status']} — {(job.get('error') or '')[:160]}")
        elif job["status"] == "held":
            # Одобрено, но держится (AIVIDEO_ENABLED=0, потолок): включат — «Продолжить» у карточки.
            why = job.get("error") or "держится"
            held.append(f"{label}: " + ("платная генерация выключена в настройках сервиса"
                                        if "AIVIDEO_ENABLED" in why else why[:160]))
        elif job["status"] in ("approved", "submitting", "polling", "recovering", "awaiting"):
            active.append(label)
    return {"ready": not missing, "missing": missing, "failed": failed, "active": active, "held": held}


def check_generation(store: Any, video_id: int) -> str:
    """awaiting_approval/generating → дальше. «wait» — ждём владельца или запуски."""
    video = E.get_video(store, video_id)
    if video["status"] == "awaiting_approval" and video.get("approval_id"):
        card = A.get_approval(store, int(video["approval_id"]))
        if card["status"] == "pending":
            return "wait"
        if card["status"] != "approved":
            E.set_status(store, video_id, "cancelled", ("awaiting_approval",),
                         error=f"карточка #{card['id']}: {card['status']}"
                               + (f" — {card.get('decision_comment')}" if card.get("decision_comment") else ""))
            return "cancelled"
        E.set_status(store, video_id, "generating", ("awaiting_approval",))
    state = readiness(store, video_id)
    if state["ready"]:
        E.set_status(store, video_id, "assembling", ("generating", "awaiting_approval"))
        return "ready"
    if state["active"]:
        return "wait"
    if state["held"] and not state["failed"]:
        E.update_video(store, video_id, error="ждёт запуска: " + "; ".join(state["held"][:2])
                       + " — включите генерацию и нажмите «Продолжить» у карточки")
        return "wait"
    if state["failed"]:
        E.set_status(store, video_id, "failed", ("generating", "awaiting_approval"),
                     error="генерация не удалась: " + "; ".join(state["failed"])[:600])
        return "failed"
    # Ничего не идёт и не упало, а чего-то нет (поза удалена/версия устарела) — запросить заново.
    E.set_status(store, video_id, "planned", ("generating", "awaiting_approval"))
    return "replan"


# --------------------------------------------------------------------------- #
# 5. Слова, время битов, сборка
# --------------------------------------------------------------------------- #


def transcribe_words(path: Path) -> list[dict]:
    """Слова озвучки с временем (Whisper large-v3-turbo, русский). Тесты подменяют."""
    from app.subtitles.registry import QUALITY_WHISPER_MODEL
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    result = WhisperSubtitleProvider().transcribe(Path(path), {"language": "ru"}, QUALITY_WHISPER_MODEL)
    return [{"word": w.word.strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
            for w in result.words if str(w.word or "").strip()]


def real_words(store: Any, text: str, ref: dict, path: Path, error: type[Exception] = E.ExplError,
               noun: str = "ролик", transcribe: Any = None) -> tuple[list[dict], float]:
    """Слова сценария на времени настоящей озвучки. Нет речи или в ней не тот текст — стоп: субтитры по длине текста
    не попали бы в голос, а клип с такой озвучкой — подделка. Общее у объяснялок и историй (app/stories_gen.py)."""
    try:
        words = (transcribe or transcribe_words)(path)
    except Exception as exc:  # noqa: BLE001 - причину — владельцу, ролик ждёт
        raise error(f"Не удалось распознать речь в озвучке ({type(exc).__name__}: {str(exc)[:120]}). Без этого "
                    f"субтитры не попадут в голос, {noun} не собираю — нажмите «Продолжить»") from exc
    share = heard_share(text, words)
    if share < VOICE_MIN_HEARD:
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE id = ?", (int(ref["id"]),))  # записать заново
        raise error(f"В озвучке распознано только {share:.0%} текста сценария. Такой голос не выпускаю — "
                    "нажмите «Продолжить», озвучка запишется заново")
    return script_words(text, words), share


def _real_words(store: Any, video: dict, ref: dict, path: Path) -> tuple[list[dict], float]:
    return real_words(store, E.narration_text(video), ref, path)


def _sfx_ok(ref: str) -> bool:
    from app.motion_assets import resolve_pack_ref

    try:
        return resolve_pack_ref(ref) is not None
    except Exception:  # noqa: BLE001
        return False


def build_layers(store: Any, mascot: dict, placed: list[dict], words: list[dict], narration_asset: int,
                 total: float, notes: list[str], dry: bool = False) -> list[dict]:
    """Слои как в пилоте 887 (позы, вжухи, плашки, чипы, голос, субтитры)."""
    poses = {int(p["id"]): p for p in E.list_poses(store, int(mascot["id"]))}
    sfx = {ref: _sfx_ok(ref) for ref in (*CUT_SFX, BADGE_SFX, CHIP_SFX)}
    if not all(sfx.values()):
        notes.append("пак freesound не установлен (tools/fetch_sfx.py) — без вжухов/«динь»/«поп»: синтетику не "
                     "подставляю")
    accent, chip_bg = mascot["accent_color"], mascot["chip_color"]
    layers: list[dict] = []
    for n, item in enumerate(placed):
        pose = poses.get(int(item["pose_id"]))
        if pose is None or not E.pose_usable(pose, dry):
            raise E.ExplError(f"бит {item['i']}: у позы {item['pose_id']} нет картинки")
        end = item["end"]
        layer = {"id": f"pose{n:02d}", "type": "image", "name": f"Поза: {pose['title']}"[:60],
                 "start": item["start"], "end": end, "x": 0.5, "y": 0.5,
                 "props": {"asset_id": str(pose["asset_id"]), "w": 1080},
                 "in": {"type": "pop" if n % 2 == 0 else "zoom", "duration": POP_SEC}}
        if item.get("variant") == "punch":
            layer["scale"] = PUNCH_SCALE
            layer["in"] = {"type": "zoom", "duration": POP_SEC}
        layers.append(layer)
        if n > 0:
            ref = CUT_SFX[n % 2 == 0]
            if sfx[ref]:
                layers.append({"id": f"wh{n:02d}", "type": "audio", "start": round(max(0.0, item["start"] - 0.05), 3),
                               "end": round(min(total, item["start"] + 0.6), 3),
                               "props": {"asset_id": ref, "mode": "track", "volume": WHOOSH_VOLUME, "role": "sfx"}})
    badge_n = 0
    chip_n = 0
    badge_spans: list[tuple[float, float]] = []
    for item in placed:
        if not item.get("badge"):
            continue
        s, e = item["start"], round(min(item["end"], item["start"] + BADGE_MAX_SEC), 3)
        layers.append({"id": f"badge{badge_n}", "type": "text", "start": s, "end": e, "x": 0.5, "y": 0.2,
                       "props": {"text": item["badge"], "font": "Montserrat", "weight": 900, "size": 170,
                                 "color": accent, "stroke": {"color": mascot["outline_color"], "width": 10},
                                 "align": "center"},
                       "in": {"type": "pop", "duration": 0.2}})
        if sfx[BADGE_SFX]:
            layers.append({"id": f"ding{badge_n}", "type": "audio", "start": s, "end": round(min(total, s + 1.0), 3),
                           "props": {"asset_id": BADGE_SFX, "mode": "track", "volume": DING_VOLUME, "role": "sfx"}})
        badge_spans.append((s, e))
        badge_n += 1
    for item in placed:
        chip = item.get("chip")
        if not chip or not chip.get("text"):
            continue
        k = _word_in(words, chip.get("word"), item["w0"], item["w1"])
        s = max(item["start"], round(float(words[k]["start"]) - 0.15, 3)) if k is not None else item["start"]
        e = round(min(item["end"], s + CHIP_SEC), 3)
        if e - s < 0.5:
            s, e = item["start"], round(min(item["end"], item["start"] + CHIP_SEC), 3)
        y = 0.31 if any(a < e and s < b for a, b in badge_spans) else 0.22
        layers.append({"id": f"chip{chip_n}", "type": "text", "start": s, "end": e, "x": 0.5, "y": y,
                       "props": {"text": chip["text"], "font": "Montserrat", "weight": 900, "size": 120,
                                 "color": "#ffffff", "align": "center",
                                 "bg": {"color": chip_bg, "opacity": 1, "radius": 24, "padX": 28, "padY": 10}},
                       "in": {"type": "pop", "duration": POP_SEC}})
        if sfx[CHIP_SFX]:
            layers.append({"id": f"pop{chip_n}", "type": "audio", "start": s, "end": round(min(total, s + 0.6), 3),
                           "props": {"asset_id": CHIP_SFX, "mode": "track", "volume": POP_VOLUME, "role": "sfx"}})
        chip_n += 1
    layers.append({"id": "narr", "type": "audio", "name": "Озвучка", "start": 0.0, "end": round(total, 3),
                   "props": {"asset_id": str(narration_asset), "mode": "track", "volume": 1.0, "role": "voice"}})
    layers.append({"id": "caps", "type": "captions", "name": "Субтитры", "start": 0.0, "end": round(total, 3),
                   "x": 0.5, "y": 0.84,
                   "props": {"style": mascot.get("captions_style") or "hormozi", "size": 92, "activeColor": accent,
                             "uppercase": True, "maxWords": 3, "maxLines": 2, "emphColor": accent}})
    return layers


def _speech_spans(words: list[dict]) -> list[dict]:
    spans: list[list[float]] = []
    for w in words:
        a, b = max(0.0, w["start"] - 0.1), w["end"] + 0.15
        if spans and a - spans[-1][1] < 0.6:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [{"start": round(a, 3), "end": round(b, 3), "gain_db": MUSIC_DUCK_DB} for a, b in spans]


def _music_track(store: Any, mascot: dict, video: dict) -> int | None:
    opt = (video.get("options") or {}).get("music_track_id")
    for value in (opt, mascot.get("music_track_id"), V._style_row(store, int(video["project_id"])).get("music_track_id")):
        if value in (None, "", 0):
            continue
        try:
            store.get_audio_track(int(value))
            return int(value)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _tool(ctx: Any, name: str, args: dict, notes: list[str] | None = None) -> dict:
    """Инструмент монтажа (те же проверки, что у агента). Пропущенные слои пачки — в заметки."""
    from app import agent_tools as T

    result, ok = T.run_tool(ctx, name, args)
    if not ok:
        detail = result.get("problems") if isinstance(result, dict) else None
        raise E.ExplError(f"{name}: {(result or {}).get('error') if isinstance(result, dict) else result}"
                          + (f" — {detail}" if detail else ""))
    if notes is not None and isinstance(result, dict) and result.get("skipped"):
        notes += [f"{name}: пропущено — {s}"[:240] for s in result["skipped"]]
    return result


def auto_mix_clip(ctx: Any, notes: list[str]) -> dict | None:
    """Автосведение собранного клипа (app/audio_mix.auto_mix, настройка AUTO_MIX): музыка под голосом
    на 18 дБ тише, ducking по фразам, звуки под голос, −14 LUFS. Не вышло — заметка, клип остаётся."""
    from app import agent_tools as T

    if not settings.auto_mix:
        return None
    result, ok = T.run_tool(ctx, "auto_mix", {"compare": False})
    if not ok:
        notes.append(f"автосведение не вышло ({str((result or {}).get('error') or result)[:160]}) — "
                     "звук как собран; audio_mix_report покажет, что поправить")
        return None
    return {"voice_vs_music_db": (result.get("after") or {}).get("voice_vs_music_db"),
            "music_db": (result.get("mix") or {}).get("music_db"), "changes": result.get("changes")}


def assemble(store: Any, video_id: int) -> dict:
    """Озвучка → слова → биты по времени → холст-клип как пилот 887. Собирали раньше — новая версия того же клипа."""
    from app import agent_tools as T
    from app.aivideo_gen import _asset_path
    from app.canvas import base_seconds, create_canvas_clip
    from app.clip_history import snapshot
    from app.dubbing import probe_duration

    video = E.get_video(store, video_id)
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    ref = E.narration_ref(store, video)
    if ref is None:
        raise E.ExplError("нет озвучки ролика — этап генерации")
    path = _asset_path(store, ref)
    audio_sec = float(probe_duration(path))
    notes: list[str] = list(video.get("notes") or [])
    dry = E.is_dry(video)
    voice = E.mascot_voice(store, mascot, dry)
    heard = None
    if dry:  # только явный сухой прогон: вместо речи тон, распознавать нечего
        words = proportional_words(E.narration_text(video), audio_sec)
    else:
        words, heard = _real_words(store, video, ref, path)
    total = round(max(words[-1]["end"] + TAIL_SEC, audio_sec + 0.4), 2)
    if total > base_seconds():
        raise E.ExplError(f"ролик {total:.1f} с длиннее холста {base_seconds():.0f} с")
    previous = video.get("clip_id")
    words, sub_fixes = WF.apply_previous(store, previous, video.get("words") or [], words, notes)
    # Чип и плашка старой правки могли записаться строкой-словарём: в кадр такое не идёт.
    # Старый план мог поставить чип с номером пункта: при пересборке рядом с плашкой он не нужен.
    beats = [{**b, "chip": _not_point_number(_chip(b.get("chip")), b), "badge": _badge(b.get("badge"))} for b in video["beats"]]
    placed = place(beats, words, total)
    marks = emphasis_marks(placed, words)
    title = (video.get("title") or video["topic"])[:100]
    clip = create_canvas_clip(store, total, {"kind": "solid", "colors": [mascot["bg_color"], mascot["bg_color"]]},
                              f"{E.DRY_MARK}: {title}"[:100] if dry else title,
                              f"Объяснялка ({mascot['name']}): {video['topic']}"[:500])
    clip_id = int(clip["id"])
    CG.hold_assembly(store, clip_id)
    # Клип — в проект маскота: иначе у холста нет проекта и он виден только по прямой ссылке.
    # Канал объяснялок: в проект канала (одобрение, расписание, статистика).
    channel_id = (video.get("options") or {}).get("channel_project_id")
    try:
        P.assign_plan_project(store, clip_id, int(channel_id or video["project_id"]))
    except KeyError:
        P.assign_plan_project(store, clip_id, int(video["project_id"]))
        notes.append(f"канал #{channel_id} не найден — клип в проекте маскота")
    # trusted_assets: позы маскота живут в его проекте, а клип может лежать в проекте канала.
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id, trusted_assets=True,
                        on_first_change=lambda: snapshot(store, clip_id, "объяснялка: сборка"))
    layers = build_layers(store, mascot, placed, words, int(ref["asset_id"]), total, notes, dry)
    for i in range(0, len(layers), LAYER_BATCH):
        _tool(ctx, "add_layers", {"layers": layers[i:i + LAYER_BATCH]}, notes)
    _tool(ctx, "set_subtitle_words", {"words": [{**w, **({"emph": marks[k]} if k in marks else {})}
                                                for k, w in enumerate(words)]})
    track = _music_track(store, mascot, video)
    if track:
        _tool(ctx, "set_music", {"on": True, "track_id": track})
        _tool(ctx, "set_music_duck", {"duck": _speech_spans(words)})
    else:
        notes.append("музыка не выбрана (маскот без трека) — set_music по list_music")
    mixed = auto_mix_clip(ctx, notes)
    E.mark_used(store, [int(p["pose_id"]) for p in placed])
    job = G.get_job(store, int(ref["job_id"])) if ref.get("job_id") else {}
    skipped = _skipped(store, mascot, video, placed, layers, track, mixed, dry)
    version = CG.finish_assembly(store, clip_id, previous)
    if version["note"] and not version["linked"]:
        notes.append(version["note"])
    timeline = {"duration": total, "clip_id": clip_id, "audio_sec": round(audio_sec, 2),
                "editor_url": f"/clips/{clip_id}", "version": version, "subtitle_fixes": sub_fixes,
                "voice": E.describe_voice(voice, float(job.get("cost_rub") or 0.0)), "dry_run": dry,
                "captions": {"timing": "speech" if heard is not None else "text_length", "heard_share": heard,
                             "text": "script" if heard is not None else "script_no_speech"},
                "skipped": skipped,
                "beats": [{k: p.get(k) for k in ("i", "start", "end", "text", "pose_id", "pose_title", "badge", "chip",
                                                  "variant")} for p in placed],
                "emphasis_words": len(marks), "layers": len(layers), "music_track_id": track,
                **({"mix": mixed} if mixed else {})}
    E.update_video(store, video_id, clip_id=clip_id, words=words, timeline=timeline, notes=notes, error="")
    out = {"ok": True, "clip_id": clip_id, "duration": total, "beats": len(placed), "layers": len(layers),
           "editor_url": timeline["editor_url"], "notes": notes, "voice": timeline["voice"],
           "captions": timeline["captions"], "skipped": skipped, "version": version, "subtitle_fixes": sub_fixes}
    options = video.get("options") or {}
    if options.get("render"):
        # «done» — только когда рендер, обложки и заголовок уже сделаны: канал объяснялок
        # (app/explainer_channel.py) сразу отправляет готовое на одобрение.
        out["publish"] = finish_publish(store, video_id, clip_id, cover=options.get("cover", True))
        if ((out["publish"].get("render") or {}).get("status")) != "succeeded":
            skipped.append("готовый файл: рендер не вышел, причина в заметках")
            E.update_video(store, video_id, timeline=timeline)
    E.set_status(store, video_id, "done", ("assembling",))
    return out


def _skipped(store: Any, mascot: dict, video: dict, placed: list[dict], layers: list[dict], track: int | None,
             mixed: dict | None, dry: bool) -> list[str]:
    """Чего нет в собранном клипе: говорится прямо, чтобы агенту не пришлось додумывать."""
    out: list[str] = []
    if dry:
        by_id = {int(p["id"]): p for p in E.list_poses(store, int(mascot["id"]))}
        fakes = {int(p["pose_id"]) for p in placed if by_id.get(int(p["pose_id"]), {}).get("placeholder")}
        out.append("настоящий голос: это черновик, вместо речи тон, субтитры разложены по длине текста")
        if fakes:
            out.append(f"настоящие позы: заглушек в клипе {len(fakes)}")
    if not track:
        out.append("музыка: у маскота не выбран трек")
    if len(placed) > 1 and not any(str(lay.get("id") or "").startswith("wh") for lay in layers):
        out.append("звуки на склейках: нет пака звуков")
    if settings.auto_mix and not mixed:
        out.append("автосведение звука: не вышло, звук как собран")
    if not (video.get("options") or {}).get("render"):
        out.append("готовый файл: рендер не просили, клип открывается в редакторе")
    return out


def facts(store: Any, video_id: int, video: dict | None = None) -> dict:
    """Что на самом деле с роликом: голос, субтитры, деньги, чего нет. Агент пересказывает это, а не догадки."""
    video = video or E.get_video(store, video_id)
    timeline = video.get("timeline") or {}
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    voice = timeline.get("voice")
    if not voice:
        planned = E.mascot_voice(store, mascot, E.is_dry(video))
        job = _target_jobs(store, int(video["project_id"]), "expl_video", int(video["id"]), E.NARRATION_KIND)
        voice = E.describe_voice(planned, estimate_rub=job["estimate_rub"] if job and job["provider"] == planned[
            "provider"] and job["status"] not in BAD_JOB else None)
    money = G.spent_on_target(store, E.video_group(int(video["id"])), "expl_video", int(video["id"]), video.get("approval_id"))
    spent = money["spent_rub"]
    to_draw = list(video.get("poses_to_draw") or [])
    skipped = list(timeline.get("skipped") or [])
    captions = timeline.get("captions")
    status = video["status"]
    parts: list[str] = []
    if status == "done":
        parts.append(f"Готов клип #{video['clip_id']}, открыть: {video['editor_url']}.")
    elif status == "failed":
        parts.append(f"Ролика нет: {video.get('error') or 'ошибка'}.")
    elif status == "cancelled":
        parts.append(f"Ролик отменён: {video.get('error') or 'отмена'}.")
    elif status == "awaiting_approval":
        parts.append(f"Ролик ждёт «Разрешить» владельца: карточка #{video.get('approval_id')} на "
                     f"{float(video.get('estimate_rub') or 0.0):.2f} ₽.")
    else:
        parts.append(f"В работе: {video['status_label']}. Клипа ещё нет.")
    if to_draw:
        parts.append("Нужно нарисовать позы: " + ", ".join(f"«{t}»" for t in to_draw) + ".")
    fixes = timeline.get("subtitle_fixes") if status == "done" else None
    if fixes is None:
        fixes = WF.summary(WF.from_clip(store, video.get("clip_id"), video.get("words") or []))
    if fixes.get("note"):
        parts.append(fixes["note"])
    if status == "done" and (timeline.get("version") or {}).get("linked"):
        parts.append("Это новая версия того же клипа, прошлая осталась, откат на экране клипа.")
    parts.append(voice["label"])
    if voice["paid"] and status != "done" and not timeline.get("voice"):
        parts.append("Без «Разрешить» озвучки не будет: другим голосом не подменяю. Не хотите платить — решает владелец: "
                     "сменить голос маскота на все следующие ролики и продолжить этот.")
    if captions:
        parts.append("Субтитры: текст сценария на времени распознанной речи." if captions.get("timing") == "speech"
                     else "Субтитры разложены по длине текста, с голосом не сверены.")
    parts.append(f"Потрачено {spent:.2f} ₽." + (f" Ещё ≈ {money['estimated_rub']:.2f} ₽ по оценке: провайдер цену не назвал."
                                               if money["estimated_rub"] else ""))
    if skipped:
        parts.append("Не сделано: " + "; ".join(skipped) + ".")
    return {"voice": voice, "captions": captions, "dry_run": E.is_dry(video), "spent_rub": spent,
            "spent_estimated_rub": money["estimated_rub"], "poses_to_draw": to_draw, "skipped": skipped, "subtitle_fixes": fixes, "summary": " ".join(parts)}


def finish_publish(store: Any, video_id: int, clip_id: int, cover: bool = True) -> dict:
    """По желанию: рендер, обложки, заголовок/описание (модули обложек и метаданных; Gemini может не
    ответить — тогда без текста, ролик всё равно готов)."""
    from app.render import ClipRenderService

    out: dict[str, Any] = {}
    notes = list(E.get_video(store, video_id).get("notes") or [])
    try:
        clip = ClipRenderService(store).render_clip_plan(clip_id)
        out["render"] = {"clip": clip.get("id"), "status": clip.get("status")}
        if clip.get("status") == "succeeded":
            if cover:
                try:
                    from app.cover_variants import generate_covers

                    covers = generate_covers(store, int(clip["id"]), count=3)
                    out["covers"] = len(covers.get("covers") or [])
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"обложки не сделаны: {str(exc)[:160]}")
            try:
                from app.ai.publish_meta import generate_publish_metadata, save_publish_meta

                if settings.gemini_api_key:
                    meta = generate_publish_metadata(store, int(clip["id"]))
                    save_publish_meta(store, int(clip["id"]), meta)
                    out["title"] = meta.get("title")
                else:
                    notes.append("заголовок и описание — без Gemini не сделаны")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"заголовок/описание не сделаны: {str(exc)[:160]}")
        else:
            notes.append(f"рендер: {clip.get('status')} {str(clip.get('error') or '')[:160]}")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"рендер не удался: {str(exc)[:200]}")
    E.update_video(store, video_id, notes=notes)
    return out


# --------------------------------------------------------------------------- #
# Ведущий: advance / работник
# --------------------------------------------------------------------------- #

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(video_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(int(video_id), threading.Lock())


def advance(store: Any, video_id: int, client: Any = None, max_steps: int = 8) -> dict:
    """Двигать ролик по шагам, пока не упрётся в ожидание (карточка, запуски) или конец.
    Ошибка шага — ``failed`` с текстом (продолжить — ``resume``/``explainer_continue``)."""
    lock = _lock(video_id)
    if not lock.acquire(blocking=False):
        return {**E.get_video(store, video_id, full=False), "busy": True}
    try:
        for _ in range(max_steps):
            video = E.get_video(store, video_id, full=False)
            status = video["status"]
            try:
                if status == "new":
                    write_script(store, video_id, client=client)
                elif status == "scripted":
                    plan_beats(store, video_id, client=client)
                elif status == "planned":
                    if request_generation(store, video_id)["status"] in ("awaiting_approval", "failed"):
                        break
                elif status in ("awaiting_approval", "generating"):
                    if check_generation(store, video_id) in ("wait", "failed", "cancelled"):
                        break
                elif status == "assembling":
                    assemble(store, video_id)
                else:
                    break
            except Exception as exc:  # noqa: BLE001 - шаг упал: ролик ждёт правки
                _log.warning("explainer %s step %s failed: %s", video_id, status, exc)
                E.set_status(store, video_id, "failed", None, error=f"{STEP_LABELS.get(status, status)}: "
                                                                   f"{str(exc)[:500]}")
                break
    finally:
        lock.release()
    return E.get_video(store, video_id)


STEP_LABELS = {"new": "сценарий", "scripted": "подбор поз", "planned": "оценка", "awaiting_approval": "одобрение",
               "generating": "генерация", "assembling": "сборка"}


def resume(store: Any, video_id: int, client: Any = None) -> dict:
    """Продолжить упавший/отменённый ролик с того шага, где есть данные."""
    video = E.get_video(store, video_id)
    if video["status"] in ("failed", "cancelled"):
        if not E.narration_text(video):
            status = "new"
        elif not video.get("beats"):
            status = "scripted"
        else:
            status = "planned"
        E.set_status(store, video_id, status, ("failed", "cancelled"), error="")
    return advance(store, video_id, client=client)


def cancel(store: Any, video_id: int) -> dict:
    video = E.get_video(store, video_id, full=False)
    if video.get("approval_id"):
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'ролик отменён' "
                         "WHERE approval_id = ? AND status IN ('awaiting', 'approved', 'held')",
                         (int(video["approval_id"]),))
        try:
            card = A.get_approval(store, int(video["approval_id"]))
            if card["status"] == "pending":
                A._update(store, card["id"], status="expired", decision_comment="ролик отменён")
        except KeyError:
            pass
    E.set_status(store, video_id, "cancelled", None, error="отменено")
    return E.get_video(store, video_id)


def create(store: Any, mascot_id: int, topic: str, duration: Any = 45, tone: str = "", script: dict | None = None,
           options: dict | None = None, requested_by: str = "agent", run: bool = False, client: Any = None) -> dict:
    """Новый ролик. ``script`` — свой текст (без Gemini). ``run`` — сразу вести по шагам (иначе работник)."""
    video = E.create_video(store, mascot_id, topic, duration, tone, options, requested_by)
    if script:
        set_script(store, int(video["id"]), script, source=requested_by)
    if run:
        return advance(store, int(video["id"]), client=client)
    kick()
    return E.get_video(store, int(video["id"]))


_wake: threading.Event | None = None


def kick() -> None:
    if _wake is not None:
        _wake.set()


class ExplainerWorker:
    """Фон: ведёт ролики по шагам (сценарий, позы, карточка, ожидание, сборка). Платные запуски
    выполняет работник ИИ-видео после «Разрешить». ``EXPLAINER_WORKER=0`` — не запускать."""

    IDLE_SECONDS = 5

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import os

        global _wake
        if os.getenv("EXPLAINER_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        _wake = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, _wake), name="explainer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if _wake is not None:
            _wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> list[int]:
        marks = ", ".join("?" for _ in E.ACTIVE_STATUSES)
        rows = self.store.db.query_all(f"SELECT id FROM expl_videos WHERE status IN ({marks}) ORDER BY id",
                                       E.ACTIVE_STATUSES)
        done = []
        for row in rows:
            advance(self.store, int(row["id"]))
            done.append(int(row["id"]))
        return done

    def _run(self, stop: threading.Event, wake: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - фон не умирает
                _log.warning("explainer worker pass failed: %s", exc)
            wake.wait(self.IDLE_SECONDS)
            wake.clear()
