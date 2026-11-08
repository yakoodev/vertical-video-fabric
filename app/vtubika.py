"""Вселенная «Витубика: истории» (проект 9) на своём 3D-движке: общий рассказчик, музыка, звуки, «образы» комнаты,
герои, ракурсы камеры. Одна история — план ``tools/vtubika_plans/<история>.json`` (фразы сценария, кадры и события
на словах, ``app/vtubika_plan.py``), собирает её ``tools/vtubika_story.py``.

* **Рассказчик** (``NARRATOR``) — один женский голос на всю вселенную (Gemini «Aoede», бесплатно, ``app/dubbing.py``);
  план истории может заменить голос полем ``voice`` (см. ``NARRATOR_ALTERNATIVES``).
* **Образы** (``LOOKS``) — палитра комнаты стримера (``app/voxel3d.streamer_room``): ``cozy`` — тёплый вечер,
  ``neon`` — фиолетовый неон, ``pastel`` — мягкий дневной пастельный. В каждом есть нейтральный ключ на лицо.
* **Герои** (``HEROES``) — внешность героя из примитивов (``voxel3d.voxel_hero``, форма ``soft``).
* **Звуки** (``SFX``) — только настоящие записи паков (freesound, kenney), громкость здесь.
* **Камера** (``camera_preset``) — ракурсы от опорных точек комнаты (голова героя, монитор, планшет, банка):
  крупно лицо, через плечо в монитор, экран крупно, руки на планшете, банка, общий; наезд и голландский угол.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from app import voxel3d as V
from app.vtubika_sets import NOODLES, WH_CAMERAS

VTUBIKA_DIR = Path(os.environ.get("VTUBIKA_DIR", r"D:\vvf-offload\vtubika"))

# Рассказчик всей вселенной: на «ты», спокойно, с иронией, без пафоса (бриф проекта 9).
NARRATOR = {"provider": "gemini", "voice": "Aoede",
            # Без указания к подаче: модель 3.8 читает его вслух (проверено Whisper-ом 08.11).
            "style": ""}

# Как рассказчик читает термины (только текст для TTS; субтитры — как в сценарии), app/vtubika_say.py.
# План дополняет/отключает полем "pronounce". Live2D — выбор владельца 08.11 («лайв два дэ»).
PRONOUNCE = {
    "Live2D": "лайв-два-дэ",
    "PNGtuber": "пи-эн-джи-тюбер",
    "VTube Studio": "ви-тьюб студио",
}

# Чем заменить в плане (``"voice": {...}``). Polza платная: только с ``paid_ok: true`` по команде владельца,
# потолок — tools/vtubika_story.POLZA_CAP_RUB (eleven-v4 ≈ 0.8 ₽ за 200 символов по замеру).
NARRATOR_ALTERNATIVES = {
    "charon": {"provider": "gemini", "voice": "Charon", "style": ""},
    "fenrir": {"provider": "gemini", "voice": "Fenrir", "style": ""},
    "lily": {"provider": "polza", "model": "elevenlabs/eleven-v4", "voice": "lily", "paid_ok": False},
}

# Фон — CC0-треки freesound (tools/fetch_music.py, те же файлы, что дорожки сервиса); тише голоса на under_db.
MUSIC = {"file": "chill-keys.mp3", "under_db": 7.0, "fade_in": 1.0, "fade_out": 2.5}

# Звуки событий: имя → (ссылка пака, громкость). Только записи паков.
SFX = {
    "coin": ("sfx:kenney-impact/impactGlass_light_002.ogg", 0.8),
    "coin2": ("sfx:kenney-impact/impactGlass_light_004.ogg", 0.8),
    "keyboard": ("sfx:kenney-interface/click_001.ogg", 0.3),
    "ping": ("sfx:freesound/pop-soft.ogg", 0.5),
    "pop": ("sfx:freesound/pop.ogg", 0.4),
    "error": ("sfx:kenney-interface/error_006.ogg", 0.7),
    "success": ("sfx:kenney-interface/confirmation_002.ogg", 0.7),
    "donate": ("sfx:freesound/cash.ogg", 0.45),
    "whoosh": ("sfx:freesound/whoosh-soft.ogg", 0.22),
    "click": ("sfx:kenney-interface/click_002.ogg", 0.5),
    "pen": ("sfx:kenney-interface/scratch_002.ogg", 0.35),
    "notify": ("sfx:kenney-interface/maximize_004.ogg", 0.5),
    "paws": ("sfx:kenney-impact/footstep_carpet_001.ogg", 0.55),  # кот мягко приземляется на стол
}

# Концовка каждой истории (app/vtubika_endcard.py): значок, «vtubika», сайт и слоган сайта — без других обещаний.
# План: "end_card": false — без неё, dict — поверх. Звук — мягкий стинг паков kenney, музыка затихает под карточкой.
# Звук карточки — один из стингов END_CARD_STINGS: курируемые паки kenney и freesound образа сервиса (``ref``) или
# свои файлы бренда в репозитории (``path``).
# Время ``at`` — от начала карточки (отрицательное — звук начинается ещё под концом истории), громкость — после
# выравнивания эффекта под голос (app.motion_render.effect_volume); громкости подобраны так, что пик мгновенной
# громкости на карточке ≈ −16 LUFS при ролике −14 LUFS. Сравнение: D:/vvf-offload/vtubika/endcard_sounds.mp4.
BRAND_SFX = "assets/brand/vtubika/sfx"  # свои звуки бренда, от корня репозитория (tools/vtubika_brand_sfx.py)
KALIMBA_VOL = 0.6  # пик мгновенной громкости на карточке ≈ −16 LUFS (замер compare3)
END_CARD_STINGS: dict[str, list[dict]] = {
    # как было: «разворот» + «подтверждение».
    "kenney_classic": [{"ref": "sfx:kenney-interface/maximize_006.ogg", "at": 0.12, "volume": 0.35},
                       {"ref": "sfx:kenney-interface/confirmation_004.ogg", "at": 1.0, "volume": 0.45}],
    # 1 — мягкий вжух под появление значка, звон стекла на его приземлении, искра на точке над «k».
    "chime_whoosh": [{"ref": "sfx:freesound/whoosh-soft.ogg", "at": -0.3, "volume": 0.5},
                     {"ref": "sfx:kenney-interface/glass_004.ogg", "at": 0.42, "volume": 0.75},
                     {"ref": "sfx:kenney-interface/glass_001.ogg", "at": 1.02, "volume": 0.5}],
    # 2 — касса, но тихо: мягкий «поп» значка и негромкий ча-чинг на точке.
    "cha_ching_soft": [{"ref": "sfx:freesound/pop-soft.ogg", "at": 0.14, "volume": 0.9},
                       {"ref": "sfx:freesound/cha-ching.ogg", "at": 0.98, "volume": 0.6}],
    # 3 — тихий райзер подводит к карточке из конца истории, на значке — стеклянный звон, на точке — мягкий поп.
    "riser_glass": [{"ref": "sfx:freesound/riser-subtle.ogg", "at": -2.3, "volume": 0.37},
                    {"ref": "sfx:kenney-interface/glass_004.ogg", "at": 0.36, "volume": 0.75},
                    {"ref": "sfx:freesound/pop-soft.ogg", "at": 1.0, "volume": 0.5}],
    # 4 — стеклянное арпеджио вверх (1.45 → 1.9 → 2.2 кГц) на появлении значка, искра на точке.
    "glass_arpeggio": [{"ref": "sfx:kenney-interface/glass_006.ogg", "at": 0.12, "volume": 0.5},
                       {"ref": "sfx:kenney-interface/glass_001.ogg", "at": 0.24, "volume": 0.5},
                       {"ref": "sfx:kenney-interface/glass_005.ogg", "at": 0.36, "volume": 0.54},
                       {"ref": "sfx:kenney-interface/glass_004.ogg", "at": 1.0, "volume": 0.56}],
    # Калимба (выбор владельца — №10 раунда 2): аккорд на появлении значка + один тихий щипок той же калимбы, когда
    # точка садится на «k». Свои файлы бренда (assets/brand/vtubika/sfx, CC0, LICENSES.md там же). ``cue`` — время
    # от события анимации; ``at`` — минус удар внутри файла (у аккорда «перебором» — главный удар на 87 мс).
    # Щипок на 6 дБ тише аккорда по громким 50 мс (у C — на 9 дБ). Сравнение: endcard_stock/compare3.mp4.
    **{f"kalimba_{v}": [{"path": f"{BRAND_SFX}/kalimba_chord.ogg", "cue": "logo", "at": -0.087, "volume": KALIMBA_VOL},
                        {"path": f"{BRAND_SFX}/{dot}", "cue": "dot", "at": -hit, "volume": round(KALIMBA_VOL * k, 3)}]
       # A — нота B4 из того же аккорда (+5 полутонов, E5); B — одиночная нота той же калимбы (G4 +5, C5);
       # C — A через ФНЧ 3 кГц с быстрым спадом, «тук».
       for v, dot, hit, k in (("a", "dot_a_note.ogg", 0.009, 0.5), ("b", "dot_b_pluck.ogg", 0.004, 0.5),
                              ("c", "dot_c_tap.ogg", 0.01, 0.355))},
}

END_CARD = {
    "duration": 2.8,
    "site": "vtubika.store",
    "slogan": ["Купить VTuber-модель", "с оплатой в рублях"],
    "sting": "kalimba_a",  # выбор владельца 08.11: аккорд калимбы на логотипе + нота на точке (A). Другие — END_CARD_STINGS
    "music_fade": 3.2,  # музыка затихает с ~0.4 с до карточки и до её конца
    "crossfade": 0.3,  # последний кадр истории растворяется в карточке (без чёрного кадра между ними)
}

# Палитры комнаты поверх NEON. Нейтральный ключ (key) у всех — лицо не уходит в цвет RGB.
LOOKS: dict[str, dict] = {
    "neon": {},
    "cozy": {
        "walls": "#5b4436", "floor": "#7a5538", "ceiling": "#3a2a22", "wood": "#6b4429", "rug": "#8a4b3a",
        "bed": "#8a5a3a", "blanket": "#c98a5a", "curtains": "#8a4a2a", "led": "#ffb35c", "led2": "#ff8a4d",
        "ambient": ["#b58a6a", "#2a1a10", 0.7], "rgb": ["#ffa04d", "#ffc27a", 1.8], "monitor_glow": 1.2,
        "lamp": ["#ffb36a", 3.2], "rim": ["#ff8a4d", 4.0], "moon": ["#7d8cff", 0.45], "key": ["#ffeedd", 2.4],
        "sky": ["#1c1b3a", "#4a3a6a", "#d48a62"], "city_lights": 1.4,
        "background": "#140c08", "fog": ["#1c120c", 3.5, 10.0], "exposure": 1.0, "fill": ["#ffe0c0", 0.35],
        "posters": [["#3a2418", "#ff9a4d", "#ffd27a", "#7ec8a0"], ["#2a1a30", "#ffcf6b", "#ff7a5c", "#9a7aff"],
                    ["#2a2018", "#ffb35c", "#ff7a5c", "#7ec8ff"]],
        "hallway": "#ffc48a",
        "grade": {"lift": [0.025, 0.015, 0.008], "gamma": [1.0, 1.0, 1.02], "gain": [1.04, 1.0, 0.95],
                  "shadows": "#4a2e22", "highlights": "#ffd2a0", "split": 0.16, "contrast": 1.06, "saturation": 1.06},
        "bloom": 0.5, "vignette": 0.38,
    },
    "pastel": {
        "walls": "#f0d4e4", "floor": "#d8b48f", "ceiling": "#fbf3f8", "wood": "#e2c3a0", "rug": "#b9d8f0",
        "bed": "#c8b4ec", "blanket": "#f6c9da", "curtains": "#bfe3d8", "led": "#ff9ad5", "led2": "#9ad8ff",
        "ambient": ["#fff6fb", "#d9c4b4", 1.35], "rgb": ["#ff9ad5", "#9ad8ff", 0.5], "monitor_glow": 0.8,
        "lamp": ["#ffe2b0", 0.5], "rim": ["#ffd6ec", 1.6], "moon": ["#fff1dc", 2.6], "key": ["#ffffff", 1.6],
        "sky": ["#86c0ff", "#bfe2ff", "#ffe6f0"], "moon_disc": False, "city_lights": 0.15,
        "background": "#efe2ee", "fog": ["#f2e6f0", 4.0, 14.0], "exposure": 1.0, "fill": ["#ffffff", 0.3],
        "posters": [["#fff0f6", "#ff9ad5", "#9ad8ff", "#ffd27a"], ["#f0f8ff", "#b49aff", "#ff9ad5", "#7ee0c0"],
                    ["#fff6e8", "#ff9ad5", "#9ad8ff", "#c8b4ec"]],
        "hallway": "#fff0d8",
        "grade": {"lift": [0.03, 0.025, 0.035], "gamma": [1.02, 1.0, 1.0], "gain": [1.0, 0.99, 1.0],
                  "shadows": "#8a7ab0", "highlights": "#fff0e4", "split": 0.12, "contrast": 1.0, "saturation": 0.96},
        "bloom": 0.35, "vignette": 0.25,
    },
}

# Расстановка для историй: основной монитор (аватар, страницы), слева — вертикальный монитор с чатом, микрофон — на
# гарнитуре героини (на столе он загораживал чат), лампа справа; справа на столе — планшет и банка-копилка.
STORY_LAYOUT = {"monitor_x": -0.1, "lamp_x": 0.72, "mic_x": None, "keyboard_x": -0.12,
                "phone": [0.72, -0.86, -20], "chat_monitor": [-0.66, -1.34, 24], "floor_lamp": [-1.42, 0.02]}
HERO_X, HERO_Z = 0.08, -0.5
TABLET = [0.22, -0.93]
JAR = [0.52, -1.16]

HEROES: dict[str, dict] = {
    # Алина, 19: длинные каштановые волосы, заколка-лисичка, лавандовое худи с «лисьим» акцентом, белые наушники.
    "alina": {"shape": "soft", "look": {
        "skin": "#f1c7a8", "hair": "#5a2a1f", "hairStyle": "long", "top": "#b9a3e3", "accent": "#ff8a3d",
        "pants": "#3a3f5c", "shoes": "#f4f0fa", "headphones": "#f2eef8", "glow": "#ff8ad8", "eyes": "#9a5a2a",
        "blush": "rgba(255,130,150,0.42)", "lashes": True, "clip": "#ff8a3d", "headsetMic": True}},
    # Тёма (Артём), ~25: ночные смены на складе — круги под глазами, лохматые короткие тёмные волосы, серое худи,
    # тёмные наушники с гарнитурой (стримит без камеры).
    "tema": {"shape": "soft", "look": {
        "skin": "#eec3a2", "hair": "#3b2a24", "hairStyle": "short", "top": "#7c838e", "accent": "#555b66",
        "pants": "#2e3340", "shoes": "#d9d9de", "headphones": "#1d1d24", "glow": "#6f86a8", "eyes": "#55708f",
        "blush": "rgba(230,120,130,0.22)", "eyebags": "rgba(105,62,98,0.55)", "headsetMic": True}},
    # Тёма на складе: то же худи, без наушников.
    "tema_work": {"shape": "soft", "look": {
        "skin": "#eec3a2", "hair": "#3b2a24", "hairStyle": "short", "top": "#7c838e", "accent": "#555b66",
        "pants": "#2e3340", "shoes": "#d9d9de", "headphones": False, "eyes": "#55708f",
        "blush": "rgba(230,120,130,0.22)", "eyebags": "rgba(105,62,98,0.55)"}},
    # Коллега со склада: тёмная кофта, оранжевый рабочий жилет, светлые короткие волосы.
    "kolya": {"shape": "soft", "look": {
        "skin": "#e4b18f", "hair": "#b08a5a", "hairStyle": "short", "top": "#3c4250", "accent": "#2b303b",
        "pants": "#2b2f3a", "shoes": "#3a3a40", "headphones": False, "eyes": "#4f6a3f", "vest": "#ff7a1a",
        "blush": "rgba(230,120,130,0.2)"}},
}


def story_room(look: str = "cozy", palette: dict | None = None, **content: Any) -> dict:
    """Комната истории: стример-рум в образе ``look`` с расстановкой ``STORY_LAYOUT`` + банка-копилка, планшет,
    стилус. ``content`` — то же, что у ``voxel3d.streamer_room`` (chat, viewers, alerts, phone, screen)."""
    if look not in LOOKS:
        raise ValueError(f"образ комнаты: один из {', '.join(LOOKS)}")
    room = V.streamer_room(palette={**LOOKS[look], **(palette or {})}, layout=STORY_LAYOUT, **content)
    top = room["desk_top"]
    room["objects"] += [
        {"id": "jar", "primitive": "coin_jar", "params": {"coins": 10, "drops": [], "max": 60}, "position": [JAR[0], top, JAR[1]]},
        {"id": "tablet", "primitive": "tablet", "params": {"width": 0.3, "depth": 0.2, "draw": [0, 0.01]},
         "position": [TABLET[0], top, TABLET[1]], "rotation": [0, 8, 0]},
    ]
    room["spots"]["chair"] = [HERO_X, HERO_Z, 180.0]
    room["targets"]["jar"] = ([JAR[0], top + 0.08, JAR[1]], [0, 0, 1])
    room["targets"]["tablet"] = ([TABLET[0], top + 0.01, TABLET[1]], [0, 1, 0])
    room["look"] = look
    # Табурет — под героем.
    for o in room["objects"]:
        if o["id"].startswith("stool_"):
            o["position"] = [o["position"][0] + HERO_X, o["position"][1], o["position"][2]]
    return room


# --------------------------------------------------------------------------- #
# Камера: ракурсы от опорных точек
# --------------------------------------------------------------------------- #

# Голова сидящей героини (soft, табурет 0.3 м) и как её смещает действие (наклон к планшету/экрану).
HEAD_UP = [HERO_X, 1.06, HERO_Z]
HEAD_SHIFT = {"Draw_Loop": [0.0, -0.08, -0.1], "Frown_Loop": [0.0, -0.04, -0.09], "Chin_Rest_Loop": [0.02, -0.06, -0.1],
              "Typing_Loop": [0.0, -0.03, -0.05], "Laugh_Loop": [0.0, 0.0, 0.04], "Sleep_Desk_Loop": [0.0, -0.22, -0.35]}

# Вертикальный кадр: монитор 16:9 целиком не влезает — экран снимается по центру (там аватар и карточки), чат —
# на вертикальном мониторе (ракурс chat). Лица — средним планом с широким углом: камера стоит между столом и героиней.
CAMERAS = ("face", "face_left", "face_close", "ots", "monitor", "monitor_close", "chat", "tablet", "jar", "wide", "low",
           "two", "insert", "smile", "webcam", "noodles", "face_card", *WH_CAMERAS)
# Крупные планы: голова целиком с запасом сверху, лицо — 45–60 % ширины кадра; наезд у них короткий.
FACE_SHOTS = ("face", "face_left", "face_close", "insert", "smile", "noodles", "face_card", "wh_face", "wh_kolya", "wh_tema")
FACE_PUSH_MAX = 0.08


def _add(a, b, k=1.0):
    return [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k]


def head_at(act: str | None) -> list[float]:
    return _add(HEAD_UP, HEAD_SHIFT.get(act or "", [0, 0, 0]))


def camera_preset(name: str, room: dict, *, act: str | None = None, push: float = 0.18, roll: float = 0.0,
                  fov: float | None = None, dof: float | None = None, shift: list | None = None) -> tuple[dict, dict]:
    """Ракурс ``name`` → ключи камеры начала и конца кадра (наезд на ``push`` доли дистанции, ``roll`` — голландский
    угол в градусах, ``shift`` — сдвиг цели [x, y, z]: героиня уходит в сторону, место под карточку). Голова — по
    текущему действию героини."""
    if name not in CAMERAS:
        raise ValueError(f"ракурс: один из {', '.join(CAMERAS)}")
    H = head_at(act)
    M = room["targets"]["monitor"][0]
    C, cn = room["targets"].get("chat") or (M, [0, 0, 1])
    T = room["targets"]["tablet"][0]
    J = room["targets"]["jar"][0]
    table: dict[str, tuple[list[float], list[float], float, float]] = {
        # (позиция, цель, fov, сила размытия фона)
        "face": (_add(H, [0.72, 0.16, -0.98]), _add(H, [0, -0.08, 0]), 56, 0.8),
        "face_left": (_add(H, [-0.45, 0.2, -0.9]), _add(H, [0, -0.08, 0]), 60, 0.8),
        "face_close": (_add(H, [0.6, 0.12, -0.86]), _add(H, [0, -0.05, 0]), 54, 0.9),
        # Вставка-«телефон»: тот же ракурс, фокус у самой камеры — героиня за большим телефоном размыта.
        "insert": (_add(H, [0.72, 0.16, -0.98]), _add(H, [0, -0.08, 0]), 56, 3.2),
        # Финал: шире, героиня справа, слева — место под карточку чата.
        "smile": (_add(H, [0.88, 0.22, -0.94]), _add(H, [0.13, -0.12, 0.04]), 60, 0.6),
        "ots": ([H[0] - 0.42, 1.38, H[2] + 0.34], [(M[0] + C[0]) / 2 + 0.16, 0.95, M[2] + 0.02], 50, 0.5),
        "monitor": (_add(M, [0.2, 0.05, 0.62]), _add(M, [0.02, 0.0, 0]), 44, 0.5),
        "monitor_close": (_add(M, [0.1, 0.03, 0.46]), _add(M, [0.01, 0.0, 0]), 42, 0.4),
        # Экран чата — в верхних двух третях кадра: субтитры (y 0.74) ложатся ниже, на стол, а не на сообщения.
        "chat": (_add(C, [cn[0] * 0.95 + 0.03, -0.02, cn[2] * 0.95]), _add(C, [0, -0.06, 0]), 56, 0.5),
        # Справа от героини, чуть впереди: рука со стилусом и лиса на планшете крупно, голова не загораживает.
        "tablet": ([H[0] + 0.55, 1.08, H[2] - 0.12], _add(T, [-0.02, 0.03, 0.0]), 46, 0.6),
        "jar": (_add(J, [0.12, 0.16, 0.42]), _add(J, [-0.05, 0.02, 0]), 42, 1.0),
        # Общий: справа сзади — героиня за столом, оба монитора, клавиатура, планшет, окно с ночным городом.
        "wide": ([1.45, 1.55, -0.2], [-0.22, 0.88, -1.0], 58, 0.25),
        "low": (_add(H, [0.32, -0.34, -0.62]), _add(H, [0, 0.02, 0]), 46, 0.8),
        "two": ([0.8, 1.3, -1.3], [-0.1, 0.96, -0.85], 52, 0.5),
        # Веб-камера на мониторе, заклеенная стикером (крупно, сверху-справа).
        "webcam": ([0.45, 1.25, -0.62], _add(M, [0.0, 0.1, 0.0]), 42, 0.6),
        # Ест лапшу: лицо и стаканчик в одном кадре.
        "noodles": (_add(H, [0.72, 0.1, -0.98]), [(H[0] + NOODLES[0]) / 2, H[1] - 0.22, (H[2] + NOODLES[1]) / 2], 60, 0.7),
        # Лицо ниже середины кадра — сверху место под карточку эфира (аватар повторяет за героем).
        "face_card": (_add(H, [0.62, 0.18, -0.98]), _add(H, [0.04, -0.12, 0.0]), 70, 0.6),
    }
    for k, v in (room.get("cams") or {}).items():
        table[k] = (list(v[0]), list(v[1]), float(v[2]), float(v[3]))
    if name not in table:
        raise ValueError(f"ракурс {name}: локации нет в сцене (план: \"sets\")")
    pos, tgt, f, blur = table[name]
    if name in FACE_SHOTS:
        push = min(push, FACE_PUSH_MAX)
    if shift:
        tgt = _add(tgt, shift)
    end = _add(pos, [tgt[i] - pos[i] for i in range(3)], push)
    r = lambda v: [round(x, 3) for x in v]  # noqa: E731
    k0 = {"position": r(pos), "target": r(tgt), "fov": float(fov or f), "roll": float(roll), "dof": float(blur if dof is None else dof)}
    if name == "insert":
        k0["focus"] = 0.3
    k1 = {**k0, "position": r(end), "roll": float(roll) * 0.7}
    return k0, k1
