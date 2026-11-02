"""Истории: жанр-шаблон «сериал-катастрофа» (docs/guides/stories.md, раздел «Сериал-катастрофа»).

Формат — по разбору успешного TikTok-сериала «Если блэкаут застал вас на 36 этаже…»: вертикальные
серии 60–90 с, лоу-поли 3D, закадровый голос, цифры на экране как в игре; каждая серия — часть одного
сериала мира, в конце крючок и титр «ЧАСТЬ N+1 — …».

* **Сценарий** (``SCRIPT_RULES``): название по формуле «Если [катастрофа] застала вас [в месте]»; хук на
  «вы» до 12 с с обещанием «одной ошибки»; дальше закадр в 3-м лице: проблема → умное решение → решение
  рождает новую проблему, каждые ~10 с; в каждой второй фразе цифра (у фразы — ``hud``: «02:07»,
  «ЗАРЯД 9%», «−41°C», «22 / 23 ЭТАЖ», «▼ 00:45»); ближе к концу — та самая ошибка; финал «Была лишь
  одна проблема…» + жуткая деталь без объяснения; ``part_title`` — крючок следующей части. Сюжет простой,
  странность — только в деталях. Призыв в конце запрещён: найденный в последней трети — переносится в
  середину (``move_cta``).
* **Серийность**: эпизоды мира — части (``options.part``: следующая по счёту); сценарист видит краткое
  «ранее» (прошлые части: название, ошибка, жуткая деталь, крючок) и продолжает крючок прошлой части.
* **Сборка**: HUD-плашки «игровой интерфейс» (Roboto Condensed, тёмная полупрозрачная подложка; опасность
  красным, ресурсы жёлтым, остальное белым — признак сценария или ключевые слова ``hud_kind``) в верхней
  безопасной зоне, не на субтитрах и не на лице (``hud_slot`` — лица OpenCV на картинке сцены), с
  въездом; название серии поверх первого кадра 1.6 с (без чёрного экрана); конец — сразу после
  последнего слова + титр «ЧАСТЬ N+1 — …» на 2 с; музыка не вшивается (мир ``music: none`` по умолчанию —
  её ставят в TikTok из библиотеки площадки, тихо).
* **Публикация**: обложка по шаблону (крупное белое название, жёлтое «СЕРИЯ N», красная плашка с
  крючком 2–4 слова) — ffmpeg по картинкам сцен, без ИИ; описание — один вопрос зрителю («А ты бы…?»),
  просьба «напиши свою версию» и 3–5 тегов (``publish_meta``, без Gemini).
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import projects as P
from app import stories as S
from app.settings import settings

_log = logging.getLogger(__name__)

FORMAT = S.FORMAT_DISASTER
STYLE_LABEL = "лоу-поли 3D: тёмный кинематографичный свет, туман"
STYLE = ("Stylized low-poly 3D, soft rounded shapes, cinematic dark lighting, soft shadows, light fog, muted colors, "
         "the hero wears one bright clothing item, vertical 9:16")
NARRATOR = ("спокойный напряжённый закадровый голос: в хуке говорит со зрителем на «вы», дальше рассказывает про "
            "героя в третьем лице, коротко и по делу, с цифрами")
CPS = 14.0
HOOK_MAX_SEC = 12.0
TITLE_SEC = 1.6  # название серии поверх первого кадра (1–2 с)
PART_SEC = 2.0  # титр «ЧАСТЬ N+1 — …» после последнего слова
HUD_MIN_SEC = 1.5
HUD_MAX_SEC = 6.0
HUD_MAX_CHARS = 24
HUD_FONT = "Roboto Condensed"
HUD_SIZE = 64
HUD_COLORS = {"danger": "#ff3b30", "resource": "#ffd400", "info": "#ffffff"}
HUD_KINDS = tuple(HUD_COLORS)
HUD_TRACK, TITLE_TRACK = 4, 5
# Места плашки (центр по высоте, сторона): сверху под шапкой площадки (safe top 0.10) — по умолчанию
# слева; лицо на сцене там — справа; потом над субтитрами (у мира y ≈ 0.74).
HUD_SLOTS = (("left", 0.145), ("right", 0.145), ("left", 0.585), ("right", 0.585))
HUD_MARGIN = 0.06
ROLES = ("hook", "problem", "solution", "mistake", "cta", "finale", "story")
MUSIC_NOTE = ("музыка не вшита (сериал-катастрофа): ставь её в TikTok из библиотеки площадки, тихо, 10–20 % — "
              "трек с левых сайтов площадка глушит")
ASK = "Напиши свою версию в комментариях."
DEFAULT_QUESTION = "А ты бы заметил эту ошибку раньше?"
DEFAULT_TAGS = ("#сериал", "#катастрофа", "#выживание", "#история")
MAX_TAGS, MIN_TAGS = 5, 3
COVER_SIZE = (1080, 1920)
COVER_RED, COVER_YELLOW = "#e5262b", "#ffd400"

_DANGER = re.compile(r"▼|°|(^|\s)[−-]\s?\d|огон|пожар|дым|газ|угар|взрыв|радиац|утечк|затоп|вода\s+прибы|урон|"
                     r"кров|опасн|тревог|трещин|обрушен|мороз|холод|жар|до\s+удара|до\s+взрыва|таймер|угроз",
                     re.IGNORECASE)
_RESOURCE = re.compile(r"%|заряд|батар|кислород|воды|вода|еда|еды|топлив|бензин|патрон|аптеч|запас|литр|\d\s?л\b|"
                       r"сил[аы]?\b|сухпа|консерв|свеч|спич|пищ", re.IGNORECASE)
_CTA = re.compile(r"подпиш|лайк|ставь(те)?\s|жми(те)?\b|пиши(те)?\s+в\s+коммент|напиши(те)?\s+в\s+коммент|"
                  r"в\s+комментари|телеграм|\bтг\b|ссылк[аи]\s+в\s+профил|продолжение\s+в\s+профил|1\s+или\s+2",
                  re.IGNORECASE)
_ONE_OR_TWO = re.compile(r"\b1\s*или\s*2\b|\bодин\s+или\s+два\b", re.IGNORECASE)
_YOU = re.compile(r"\b(вы|вас|вам|вами|ваш\w*)\b", re.IGNORECASE)

SCRIPT_RULES = """\
Ты сценарист TikTok-сериала-катастрофы: вертикальные серии 60–90 с, лоу-поли 3D-сцены, закадровый голос,
субтитры и цифры на экране как в игре. Эта серия — часть сериала одного мира: series.part — номер ЭТОЙ части,
series.previously — что было в прошлых частях, series.continue_from — крючок прошлой части: продолжи его
сразу после хука (что было дальше), но так, чтобы серию понял и новый зритель.
Название (title, до 60 символов) — по формуле «Если [катастрофа] застала вас [в месте]» («Если блэкаут застал
вас на 36 этаже»). Пиши ПО-РУССКИ, короткими фразами (до 20 слов), без эмодзи и хэштегов.
Структура (у каждой фразы role):
- hook (0–12 с, 1–3 фразы) — на «вы», совет-ловушка и обещание ОДНОЙ ошибки: «Если обстрел застал вас на
  14 этаже, не подходите к окну и не бегите в ванную. Самое безопасное место там, где вы снимаете обувь. Но
  только если не совершить одну ошибку.»;
- дальше закадр в ТРЕТЬЕМ лице про героя (из cast или new_characters): problem (проблема) → solution (умное
  решение) → решение рождает новую problem; новый поворот примерно каждые 10 секунд (≈ 140 символов текста);
- в каждой второй фразе цифра: время, метры, этажи, проценты, градусы — пиши ЦИФРАМИ («02:07», «9%»,
  «−41 °C», «23 этаж»); у такой фразы hud — короткая надпись на экране как в игре, до 24 символов: «02:07»,
  «ЗАРЯД 9%», «−41°C», «22 / 23 ЭТАЖ», «▼ 00:45»; hud_kind — danger (угроза: огонь, холод, вода, таймер до
  беды), resource (ресурс: заряд, вода, кислород, еда) или info (время, этаж, расстояние);
- ближе к концу (последняя четверть) — та самая одна ошибка: role mistake, поле mistake — она одной фразой;
- finale — фраза «Была лишь одна проблема…» и жуткая деталь без объяснения (cliffhanger), и всё — обрыв.
  НИКАКИХ призывов в конце (подписаться, лайк, комментарий). Призыв, если нужен, — только в середине (role cta).
Сюжет простой и понятный, странность — только в деталях; одна загадка остаётся без ответа, логику до конца не
объясняй. Запутанный сюжет — брак: запутался зритель — свайпнул.
part_title — крючок СЛЕДУЮЩЕЙ части, 2–5 слов капсом без номера («ОНА ОСТАЛАСЬ НАВЕРХУ»): в конце серии встанет
титр «ЧАСТЬ {series.part + 1} — {part_title}».
hook_words — 2–4 слова для красной плашки обложки («НЕ ПОДХОДИ К ОКНУ»).
viewer_question — один вопрос зрителю для описания: «А ты бы вышел на балкон?» (не «1 или 2»).
hashtags — 3–5 тегов по теме.
Объём всего текста — примерно duration_sec × 14 символов (±10%).
Каждой фразе (sentences[].text) — 1–2 кадра (shots), примерно один кадр на 2–3 секунды речи. Камера близко,
чаще за спиной героя на уровне плеч; общие планы — только сверху. Кадр — неподвижная сцена в стиле мира:
- location — key локации из locations (ПЕРЕИСПОЛЬЗУЙ) или из new_locations;
- characters — keys актёров в кадре (0–3) из cast или new_characters;
- action — ПО-АНГЛИЙСКИ 8–25 слов: кто что делает, поза, где камера («over-the-shoulder view behind …»),
  ключевые предметы; без надписей и цифр в кадре (цифры — в hud);
- tags — 4–8 коротких тегов по-русски и по-английски;
- camera — одно из camera_moves; соседние кадры — разные движения;
- sfx — звук действия: tag из available_sfx или короткое слово («ветер», «скрип», «сирена», «удар»), только у
  звучащего действия, не чаще чем в каждом втором кадре;
- accent — ПО-АНГЛИЙСКИ маленькая деталь света/частиц («sparks», «flashlight beam», «snow particles») или пусто;
- scene_id — id сцены из scene_library, если подходит точно (готовая бесплатна).
new_characters[] (не больше 4): key, name (по-русски), identity_card — ПО-АНГЛИЙСКИ 15–40 слов: лоу-поли
персонаж, возраст, одежда, ОДНА яркая вещь (жёлтый дождевик, красная шапка) — по ней героя узнают даже мелким
планом; persona — характер по-русски. new_locations[] (не больше 4): key, name, description — ПО-АНГЛИЙСКИ
15–40 слов: место катастрофы, предметы, тёмный свет.
logline — одна фраза, о чём серия. Соблюдай правила владельца (owner_rules) и тон (tone).
Верни ТОЛЬКО JSON по схеме.
"""


# --------------------------------------------------------------------------- #
# Эпизод и серийность
# --------------------------------------------------------------------------- #


def is_disaster(world: dict, ep: dict | None = None) -> bool:
    return S.episode_format(world, ep) == FORMAT


def _parts(store: Any, world_id: int, exclude: int | None = None) -> list[dict]:
    """Эпизоды-катастрофы мира (без отменённых) с номером части, по номеру."""
    out = []
    for e in S.list_episodes(store, world_id=int(world_id), limit=500):
        opts = e.get("options") or {}
        if opts.get("format") != FORMAT or e["status"] == "cancelled" or (exclude and int(e["id"]) == int(exclude)):
            continue
        out.append({**e, "part": int(opts.get("part") or 0)})
    return sorted(out, key=lambda e: (e["part"], int(e["id"])))


def next_part(store: Any, world_id: int) -> int:
    return max([e["part"] for e in _parts(store, world_id)] or [0]) + 1


def episode_options(store: Any, world: dict, options: dict) -> dict:
    """Опции нового эпизода-катастрофы: номер части (следующий), название в начале, музыка мира."""
    out = dict(options)
    try:
        part = int(out.get("part") or 0)
    except (TypeError, ValueError) as exc:
        raise S.StoryError("part — номер части сериала (1, 2, …)") from exc
    out["part"] = part if part > 0 else next_part(store, int(world["id"]))
    out.setdefault("title_card", True)
    if out.get("music") not in (None, "", "auto", "none"):
        raise S.StoryError("music: auto (вшить трек) | none (без музыки)")
    return out


def part_of(ep: dict) -> int:
    return max(1, int((ep.get("options") or {}).get("part") or 1))


def music_off(world: dict, ep: dict) -> bool:
    """Музыку не вшивать: эпизод/мир ``music: none``; у катастрофы — по умолчанию (если не ``auto``)."""
    mode = str((ep.get("options") or {}).get("music") or world.get("music") or "").strip().lower()
    if mode == "none":
        return True
    if mode == "auto" or (ep.get("options") or {}).get("music_track_id"):
        return False
    return is_disaster(world, ep)


def series_context(store: Any, ep: dict) -> dict:
    """«Ранее» для сценариста: прошлые части сериала мира (коротко) и крючок прошлой части."""
    part = part_of(ep)
    earlier = [e for e in _parts(store, int(ep["world_id"]), exclude=int(ep["id"])) if 0 < e["part"] < part]
    previously = []
    for e in earlier[-3:]:
        script = S.get_episode(store, int(e["id"])).get("script") or {}
        previously.append({"part": e["part"], "title": script.get("title") or e.get("title") or "",
                           "logline": script.get("logline") or P._text(e["premise"], 200),
                           "mistake": script.get("mistake") or "", "cliffhanger": script.get("cliffhanger") or "",
                           "next_part_title": script.get("part_title") or ""})
    out: dict[str, Any] = {"part": part, "previously": previously}
    last = previously[-1] if previously else None
    if last and last["part"] == part - 1:
        out["continue_from"] = " — ".join(x for x in (f"ЧАСТЬ {part} — {last['next_part_title']}"
                                                       if last["next_part_title"] else "",
                                                       last["cliffhanger"]) if x)
    return out


# --------------------------------------------------------------------------- #
# Сценарий: поля фраз, проверки, призыв не в конце
# --------------------------------------------------------------------------- #


def extend_schema(schema: dict) -> dict:
    """Схема ответа сценариста: у фразы hud/hud_kind/role; у серии part_title, hook_words, вопрос, теги."""
    sentence = schema["properties"]["sentences"]["items"]["properties"]
    sentence["hud"] = {"type": "string"}
    sentence["hud_kind"] = {"type": "string", "enum": list(HUD_KINDS)}
    sentence["role"] = {"type": "string", "enum": list(ROLES)}
    for key in ("part_title", "hook_words", "viewer_question", "mistake", "cliffhanger"):
        schema["properties"][key] = {"type": "string"}
    schema["properties"]["hashtags"] = {"type": "array", "items": {"type": "string"}}
    schema["required"] = list(dict.fromkeys([*schema.get("required", []), "part_title"]))
    return schema


def hud_text(value: Any) -> str:
    """Надпись HUD: одна строка капсом, до 24 символов, минус перед цифрой — настоящий «−»."""
    text = " ".join(str(value or "").replace("\n", " ").split()).strip().strip(".")
    text = re.sub(r"(^|\s)-(?=\s?\d)", lambda m: f"{m.group(1)}−", text).upper()
    return text[:HUD_MAX_CHARS].strip()


def hud_kind(text: str, explicit: Any = None) -> str:
    """danger (красным) | resource (жёлтым) | info (белым): признак сценария, иначе по ключевым словам."""
    value = str(explicit or "").strip().lower()
    if value in HUD_KINDS:
        return value
    if _DANGER.search(text or ""):
        return "danger"
    if _RESOURCE.search(text or ""):
        return "resource"
    return "info"


def sentence_extra(raw: dict) -> dict:
    hud = hud_text(raw.get("hud"))
    role = str(raw.get("role") or "").strip().lower()
    out: dict[str, Any] = {"role": role if role in ROLES else ""}
    if hud:
        out["hud"] = hud
        out["hud_kind"] = hud_kind(hud, raw.get("hud_kind"))
    return out


def is_cta(text: str) -> bool:
    return bool(_CTA.search(text or ""))


def move_cta(sentences: list[dict], warnings: list[str]) -> list[dict]:
    """Призыв в последней трети серии (до конца досматривают ~5 %, до призыва ~1 %) → в середину."""
    n = len(sentences)
    if n < 4:
        return sentences
    tail_from = int(n * 0.6)
    late = [s for i, s in enumerate(sentences) if i >= tail_from and (s.get("role") == "cta" or is_cta(s["text"]))]
    if not late:
        return sentences
    rest = [s for s in sentences if not any(s is x for x in late)]
    mid = len(rest) // 2
    for s in late:
        s["role"] = "cta"
    warnings.append(f"призыв в конце серии ({len(late)}) перенесён в середину — конец обрывается на крючке")
    return rest[:mid] + late + rest[mid:]


def _est(text: str) -> float:
    return len(text) / CPS


def finish_script(script: dict, data: dict, ep: dict) -> dict:
    """Поля серии (часть, крючок следующей, обложка, вопрос, теги) и проверки формулы формата."""
    warnings = script["warnings"]
    sentences = move_cta(script["sentences"], warnings)
    script["sentences"] = sentences
    script["text"] = " ".join(s["text"] for s in sentences)
    script["format"] = FORMAT
    script["part"] = part_of(ep)
    # Крючок следующей части капсом, без номера (сценарист иногда пишет «ЧАСТЬ 2 — …» сам).
    part_title = " ".join(P._text(data.get("part_title"), 80).split()).upper()
    script["part_title"] = re.sub(r"^ЧАСТЬ\s*\d+\s*[—–-]?\s*", "", part_title).strip(" .-—–")[:48].strip()
    from app.cover_variants import clean_hook

    script["hook_words"] = " ".join(clean_hook(data.get("hook_words")).split()[:4]).upper()
    script["viewer_question"] = P._text(data.get("viewer_question"), 160)
    script["hashtags"] = [t for t in (data.get("hashtags") or []) if isinstance(t, str)][:8]
    script["mistake"] = P._text(data.get("mistake"), 300)
    script["cliffhanger"] = P._text(data.get("cliffhanger"), 300)
    # Хук на «вы» с обещанием «одной ошибки» — в первые 12 с.
    t, promise = 0.0, None
    for s in sentences:
        t += _est(s["text"])
        if re.search(r"ошибк", s["text"], re.IGNORECASE):
            promise = t
            break
    if not _YOU.search(sentences[0]["text"]):
        warnings.append("хук не на «вы»: первая фраза — «Если [катастрофа] застала вас [в месте]…»")
    if promise is None:
        warnings.append("нет обещания «одной ошибки» в хуке")
    elif promise > HOOK_MAX_SEC:
        warnings.append(f"обещание «одной ошибки» на {promise:.0f} с — хук должен уложиться в {HOOK_MAX_SEC:.0f} с")
    with_number = sum(1 for s in sentences if re.search(r"\d", s["text"]) or s.get("hud"))
    if with_number < len(sentences) * 0.4:
        warnings.append(f"цифры в {with_number} фразах из {len(sentences)} — нужна в каждой второй (время, метры, %)")
    if not any(re.search(r"одна\s+проблем", s["text"], re.IGNORECASE) for s in sentences[-3:]):
        warnings.append("нет финала «Была лишь одна проблема…» в последних фразах")
    if not script["mistake"] and not any(s.get("role") == "mistake" for s in sentences):
        warnings.append("не показана та самая ошибка (role mistake ближе к концу)")
    if not script["part_title"]:
        warnings.append("нет part_title — титр в конце будет просто «ЧАСТЬ N+1»")
    # Новый поворот примерно каждые 10 с: промежутки между проблемами.
    t, last, gaps = 0.0, None, []
    for s in sentences:
        if s.get("role") == "problem":
            if last is not None:
                gaps.append(t - last)
            last = t
        t += _est(s["text"])
    if gaps and max(gaps) > 16:
        warnings.append(f"между проблемами до {max(gaps):.0f} с — новый поворот каждые ~10 с")
    if script["text"]:
        est = round(len(script["text"]) / CPS, 1)
        script["est_sec"] = est
        lo, hi, _d = S.DURATIONS[FORMAT]
        if not lo - 5 <= est <= hi + 5:
            warnings.append(f"текст ≈ {est:.0f} с — серия {lo:.0f}–{hi:.0f} с")
    return script


# --------------------------------------------------------------------------- #
# Сборка: название, HUD-плашки, титр части
# --------------------------------------------------------------------------- #


def _hud_width(text: str, size: int = HUD_SIZE) -> float:
    """Ширина плашки (доля кадра 1080): condensed ≈ 0.5 кегля на знак + поля."""
    return min(0.9, (len(text) * size * 0.5 + 2 * 22 + len(text) * 3) / 1080)


def _faces(path: Path | None) -> list[list[float]]:
    if path is None or not Path(path).is_file():
        return []
    try:
        from app.cover_variants import detect_faces, read_small

        img = read_small(Path(path), (360, 640), crop=(1080, 1920))
        return detect_faces(img) if img is not None else []
    except Exception:  # noqa: BLE001 - без OpenCV/ffmpeg — место по умолчанию
        return []


def hud_slot(text: str, faces: list[list[float]], captions_y: float) -> tuple[float, float]:
    """(x, y) центра плашки: верх слева, если там нет лица; иначе — свободное место, не на субтитрах."""
    w = _hud_width(text)
    h = HUD_SIZE * 1.4 / 1920
    best, best_cost = None, None
    for side, y in HUD_SLOTS:
        if abs(y - captions_y) < 0.12:
            continue
        x = HUD_MARGIN + w / 2 if side == "left" else 1 - HUD_MARGIN - w / 2
        x0, x1, y0, y1 = x - w / 2, x + w / 2, y - h / 2, y + h / 2
        cost = 0.0
        for fx0, fy0, fx1, fy1 in faces:
            pad = (fy1 - fy0) * 0.2
            cost += max(0.0, min(x1, fx1 + pad) - max(x0, fx0 - pad)) * max(0.0, min(y1, fy1 + pad) - max(y0, fy0 - pad))
        if best_cost is None or cost < best_cost - 1e-9:
            best, best_cost = (round(x, 4), y), cost
    return best or (round(HUD_MARGIN + w / 2, 4), HUD_SLOTS[0][1])


def hud_layers(ep: dict, placed: list[dict], speech_end: float, captions_y: float,
               scene_paths: dict[int, Path | None], prefix: str) -> list[dict]:
    """Плашки HUD: на фразу с ``hud`` — от её первого бита до конца фразы (1.5–6 с), до титра части."""
    sentences = (ep.get("script") or {}).get("sentences") or []
    spans: dict[int, list[float]] = {}
    first_scene: dict[int, int] = {}
    for item in placed:
        n = int(item.get("sentence") or 0)
        span = spans.setdefault(n, [float(item["start"]), float(item["end"])])
        span[0], span[1] = min(span[0], float(item["start"])), max(span[1], float(item["end"]))
        first_scene.setdefault(n, int(item["scene_id"]))
    layers: list[dict] = []
    face_cache: dict[int, list] = {}
    for n in sorted(spans):
        if n >= len(sentences) or not sentences[n].get("hud"):
            continue
        text = sentences[n]["hud"]
        kind = sentences[n].get("hud_kind") or hud_kind(text)
        t0 = max(0.25, spans[n][0] + 0.15)
        t1 = min(spans[n][1], t0 + HUD_MAX_SEC, speech_end)
        if t1 - t0 < HUD_MIN_SEC:
            t1 = min(t0 + HUD_MIN_SEC, speech_end)
        if t1 - t0 < 0.6:
            continue
        sid = first_scene[n]
        if sid not in face_cache:
            face_cache[sid] = _faces(scene_paths.get(sid))
        x, y = hud_slot(text, face_cache[sid], captions_y)
        layer = {"id": f"{prefix}hud{n:02d}", "type": "text", "name": f"HUD: {text}"[:60], "start": round(t0, 3),
                 "end": round(t1, 3), "track": HUD_TRACK, "x": x, "y": y,
                 "props": {"text": text, "font": HUD_FONT, "weight": 700, "size": HUD_SIZE, "color": HUD_COLORS[kind],
                           "align": "center", "letterSpacing": 3, "uppercase": True, "stroke": None,
                           "bg": {"color": "#0b0d12", "opacity": 0.62, "radius": 10, "padX": 22, "padY": 10}},
                 "in": {"type": "slideRight" if x < 0.5 else "slideLeft", "duration": 0.22},
                 "out": {"type": "fade", "duration": 0.18}}
        if kind == "danger":
            layer["loop"] = {"type": "pulse", "speed": 1.2, "amount": 0.5}
        layers.append(layer)
    return layers


def title_layers(ep: dict, placed: list[dict], stroke: str, prefix: str) -> list[dict]:
    """Название серии поверх первого кадра (1.6 с, без чёрного экрана) и «ЧАСТЬ N» под ним."""
    title = P._text((ep.get("script") or {}).get("title") or ep.get("title") or ep["premise"], 70)
    end = round(min(TITLE_SEC, placed[0]["end"] if placed else TITLE_SEC), 3)
    # «ЧАСТЬ N» — под блоком названия: строк ≈ длина × 0.5 кегля / ширина блока.
    lines = max(1, -(-len(title) * 92 * 0.5 // 940))
    part_y = round(0.34 + (lines * 92 * 1.05 / 2 + 14 + 60) / 1920, 3)
    common = {"font": HUD_FONT, "weight": 900, "align": "center", "uppercase": True,
              "stroke": {"color": stroke, "width": 8}}
    return [
        {"id": f"{prefix}title", "type": "text", "name": "Название серии", "start": 0.0, "end": end,
         "track": TITLE_TRACK, "x": 0.5, "y": 0.34,
         "props": {"text": title, "size": 92, "color": "#ffffff", "maxWidth": 940, "lineHeight": 1.05, **common,
                   "bg": {"color": "#000000", "opacity": 0.35, "radius": 18, "padX": 28, "padY": 14}},
         "in": {"type": "pop", "duration": 0.15}, "out": {"type": "fade", "duration": 0.2}},
        {"id": f"{prefix}title_part", "type": "text", "name": "Номер части", "start": 0.0, "end": end,
         "track": TITLE_TRACK, "x": 0.5, "y": part_y,
         "props": {"text": f"ЧАСТЬ {part_of(ep)}", "size": 70, "color": COVER_YELLOW, **common},
         "in": {"type": "pop", "duration": 0.15}, "out": {"type": "fade", "duration": 0.2}},
    ]


def part_card_text(ep: dict) -> str:
    nxt = part_of(ep) + 1
    title = str((ep.get("script") or {}).get("part_title") or "").strip()
    return f"ЧАСТЬ {nxt} — {title}" if title else f"ЧАСТЬ {nxt}"


def part_layer(ep: dict, speech_end: float, total: float, stroke: str, prefix: str) -> dict:
    """Титр «ЧАСТЬ N+1 — …» на последние 2 с: сразу после последнего слова, до конца ролика."""
    return {"id": f"{prefix}part", "type": "text", "name": "Титр следующей части", "start": round(speech_end, 3),
            "end": round(total, 3), "track": TITLE_TRACK, "x": 0.5, "y": 0.45,
            "props": {"text": part_card_text(ep), "font": HUD_FONT, "weight": 900, "size": 96, "color": "#ffffff",
                      "align": "center", "uppercase": True, "maxWidth": 960, "lineHeight": 1.05,
                      "stroke": {"color": stroke, "width": 8},
                      "bg": {"color": "#000000", "opacity": 0.55, "radius": 18, "padX": 30, "padY": 16},
                      "highlight": {"words": [0, 1], "color": COVER_YELLOW, "bg": None}},
            "in": {"type": "stomp", "duration": 0.3}}


# --------------------------------------------------------------------------- #
# Публикация: описание и обложка
# --------------------------------------------------------------------------- #


def viewer_question(script: dict) -> str:
    q = " ".join(str(script.get("viewer_question") or "").split()).strip()
    if not q or _ONE_OR_TWO.search(q):
        return DEFAULT_QUESTION
    q = q.rstrip(".!… ")
    if not q.endswith("?"):
        q += "?"
    return q[0].upper() + q[1:]


def publish_meta(ep: dict) -> dict:
    """Заголовок, описание (один вопрос зрителю + «напиши свою версию») и 3–5 тегов — без ИИ."""
    from app.ai.publish_meta import clean_tag

    script = ep.get("script") or {}
    title = P._text(script.get("title") or ep.get("title") or ep["premise"], 80)
    title = f"{title} — часть {part_of(ep)}"[:95]
    tags: list[str] = []
    for raw in [*(script.get("hashtags") or []), *DEFAULT_TAGS]:
        tag = clean_tag(raw)
        if tag and f"#{tag}" not in tags and (len(tags) < MIN_TAGS or raw in (script.get("hashtags") or [])):
            tags.append(f"#{tag}")
        if len(tags) >= MAX_TAGS:
            break
    hooks = [h for h in (script.get("hook_words"), script.get("part_title")) if h]
    return {"title": title, "description": f"{viewer_question(script)} {ASK}", "hashtags": tags,
            "platforms": {}, "cover_hooks": hooks, "model": "", "format": FORMAT}


def cover_texts(ep: dict) -> dict:
    script = ep.get("script") or {}
    from app.cover_variants import clean_hook

    hook = script.get("hook_words") or clean_hook(script.get("part_title")) or "ОДНА ОШИБКА"
    return {"title": P._text(script.get("title") or ep.get("title") or ep["premise"], 70).upper(),
            "series": f"СЕРИЯ {part_of(ep)}", "hook": " ".join(str(hook).split()[:4]).upper()}


def _wrap(text: str, max_chars: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= max_chars:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines or [""]


def _pill_png(width: int, height: int, color: str) -> bytes | None:
    """Красная скруглённая плашка (PNG с прозрачностью) — OpenCV; нет OpenCV — None (тогда box drawtext)."""
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover - зависит от окружения
        return None
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    img = np.zeros((height, width, 4), dtype=np.uint8)
    rad = height // 2
    fill = (b, g, r, 255)
    cv2.rectangle(img, (rad, 0), (width - rad - 1, height - 1), fill, -1, cv2.LINE_AA)
    cv2.circle(img, (rad, rad), rad - 1, fill, -1, cv2.LINE_AA)
    cv2.circle(img, (width - rad - 1, rad), rad - 1, fill, -1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else None


def cover_layout(texts: dict, width: int = COVER_SIZE[0], height: int = COVER_SIZE[1]) -> dict:
    """Строки и кегли обложки: название (до 3 строк), «СЕРИЯ N», плашка с крючком; y — сверху вниз."""
    lines = _wrap(texts["title"], 12)
    while len(lines) > 3:
        lines = _wrap(texts["title"], len(max(lines, key=len)) + 3)
    longest = max(len(x) for x in lines) or 1
    size = int(min(width * 0.15, width * 0.84 / (0.56 * longest)))
    line_h = round(size * 1.04)
    top = round(height * 0.11)
    series_size = round(size * 0.62)
    series_y = top + line_h * len(lines) + round(size * 0.18)
    hook_size = round(min(size * 0.5, width * 0.8 / (0.52 * max(1, len(texts["hook"])))))
    pill_h = round(hook_size * 1.7)
    pill_w = min(width - 80, round(len(texts["hook"]) * hook_size * 0.52 + hook_size * 1.6))
    pill_y = series_y + round(series_size * 1.35)
    return {"lines": lines, "size": size, "line_h": line_h, "top": top, "series_size": series_size,
            "series_y": series_y, "hook_size": hook_size, "pill": [round((width - pill_w) / 2), pill_y, pill_w, pill_h]}


def cover_args(base: str, out: Path, texts: dict, layout: dict, pill: str | None, *, width: int = COVER_SIZE[0],
               height: int = COVER_SIZE[1], font: str = "font.ttf") -> list[str]:
    """ffmpeg: кадр сцены под 9:16, тень сверху, белое название, жёлтое «СЕРИЯ N», красная плашка с крючком.
    Тексты — из файлов title{i}.txt / series.txt / hook.txt в рабочей папке (без экранирования)."""
    from app.cover_variants import _ffcolor

    size = layout["size"]
    chain = [f"scale={width}:{height}:force_original_aspect_ratio=increase", f"crop={width}:{height}", "setsar=1",
             f"drawbox=x=0:y=0:w=iw:h=ih*0.42:color={_ffcolor('#000000', 0.3)}:t=fill",
             f"drawbox=x=0:y=ih*0.42:w=iw:h=ih*0.08:color={_ffcolor('#000000', 0.18)}:t=fill",
             f"drawbox=x=0:y=ih*0.5:w=iw:h=ih*0.06:color={_ffcolor('#000000', 0.08)}:t=fill"]

    def text(file: str, fsize: int, color: str, y: int, border: str | None = "#000000", extra: str = "") -> str:
        parts = [f"fontfile={font}", f"textfile={file}", "expansion=none", f"fontsize={fsize}",
                 f"fontcolor={_ffcolor(color)}", "x=(w-text_w)/2", f"y={y}",
                 f"shadowcolor={_ffcolor('#000000', 0.7)}", "shadowx=0", f"shadowy={max(3, round(fsize * 0.07))}"]
        if border:
            parts += [f"borderw={max(3, round(fsize * 0.06))}", f"bordercolor={_ffcolor(border)}"]
        return "drawtext=" + ":".join(parts) + extra

    for i, _line in enumerate(layout["lines"]):
        chain.append(text(f"title{i}.txt", size, "#ffffff", layout["top"] + i * layout["line_h"]))
    chain.append(text("series.txt", layout["series_size"], COVER_YELLOW, layout["series_y"]))
    px, py, pw, ph = layout["pill"]
    hook_y = py + round((ph - layout["hook_size"]) / 2)
    if pill:
        graph = (f"[0:v]{','.join(chain)}[bg];[1:v]format=rgba[p];[bg][p]overlay={px}:{py},"
                 + text("hook.txt", layout["hook_size"], "#ffffff", hook_y, border=None) + "[out]")
        inputs = ["-i", base, "-i", pill]
    else:
        box = f":box=1:boxcolor={_ffcolor(COVER_RED)}:boxborderw={round(layout['hook_size'] * 0.35)}"
        graph = f"[0:v]{','.join(chain)}," + text("hook.txt", layout["hook_size"], "#ffffff", hook_y, None, box) + "[out]"
        inputs = ["-i", base]
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
            "-frames:v", "1", "-q:v", "3", str(out)]


def cover_bases(store: Any, ep: dict) -> list[Path]:
    """Картинки сцен для обложки: начало (герой на месте), та самая ошибка, середина — разные."""
    from app.aivideo_gen import _asset_path

    beats = (ep.get("timeline") or {}).get("beats") or ep.get("beats") or []
    sentences = (ep.get("script") or {}).get("sentences") or []
    mistake = next((i for i, s in enumerate(sentences) if s.get("role") == "mistake"), None)
    order: list[int] = []
    if beats:
        order.append(int(beats[0]["scene_id"]))
        if mistake is not None:
            order += [int(b["scene_id"]) for b in beats if b.get("sentence") == mistake][:1]
        order.append(int(beats[len(beats) // 2]["scene_id"]))
        order += [int(b["scene_id"]) for b in beats]
    out: list[Path] = []
    for sid in dict.fromkeys(order):
        try:
            scene = S.get_scene(store, sid)
        except KeyError:
            continue
        if not scene["ready"]:
            continue
        ref = {"asset_id": scene["asset_id"]}
        path = _asset_path(store, ref)
        if path and path not in out:
            out.append(path)
        if len(out) >= 3:
            break
    return out


def make_covers(store: Any, episode_id: int, clip_id: int) -> dict:
    """3 обложки по шаблону формата на готовый клип (``clip_covers``, стиль ``disaster``); первая —
    обложка клипа (превью, карточка, миниатюра), в видео не вшивается: первый кадр — название серии."""
    from app.cover_variants import _run, choose_cover, font_path, list_covers

    ep = S.get_episode(store, episode_id)
    clip = store.get_clip(int(clip_id))
    bases = cover_bases(store, ep)
    if not bases:
        raise S.StoryError("нет картинок сцен для обложки")
    texts = cover_texts(ep)
    layout = cover_layout(texts)
    width, height = COVER_SIZE
    work = settings.tmp_dir / f"story-covers-{clip_id}-{uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    out_dir = settings.clip_dir / "covers"
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[int] = []
    try:
        shutil.copyfile(font_path(HUD_FONT), work / "font.ttf")
        for i, line in enumerate(layout["lines"]):
            (work / f"title{i}.txt").write_text(line, encoding="utf-8")
        (work / "series.txt").write_text(texts["series"], encoding="utf-8")
        (work / "hook.txt").write_text(texts["hook"], encoding="utf-8")
        pill_png = _pill_png(layout["pill"][2], layout["pill"][3], COVER_RED)
        if pill_png:
            (work / "pill.png").write_bytes(pill_png)
        for n, base in enumerate(bases):
            ext = base.suffix.lower() or ".png"
            shutil.copyfile(base, work / f"base{n}{ext}")
            out = out_dir / f"{int(clip_id)}-{uuid4().hex[:12]}.jpg"
            _run(cover_args(f"base{n}{ext}", out, texts, layout, "pill.png" if pill_png else None), cwd=work)
            cur = store.db.execute(
                "INSERT INTO clip_covers (clip_id, path, width, height, format, t, text, style, text_y, faces, score, "
                "renderer) VALUES (?, ?, ?, ?, '9:16', 0, ?, 'disaster', ?, 0, ?, 'story')",
                (int(clip["id"]), str(out), width, height, f"{texts['title']} · {texts['series']} · {texts['hook']}",
                 round(layout["top"] / height, 4), float(len(bases) - n)))
            made.append(int(cur.lastrowid))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    chosen = choose_cover(store, made[0], apply_to_render=False)
    return {"covers": len(made), "new": made, "chosen": chosen["cover_id"], "texts": texts,
            "all": [c["id"] for c in list_covers(store, int(clip["id"]))]}

