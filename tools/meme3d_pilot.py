"""Пилот мем-канала «3D-анимация с мемами»: робот-помощник Клод ворчит на своего пользователя.

    python tools/meme3d_pilot.py D:/vvf-offload/memes/pilot1 [--preview 0,3,9.5] [--frames-only] [--gl gpu]

Шаги (кэш в OUT/work, всё пересчитывается быстро, кроме рендера 3D):

1. **Голос** — одна проба ElevenLabs (tools/meme3d_voice.py, take1_callum.mp3) режется на реплики по словам Whisper,
   реплики встают на свои места монтажа (``LINES``: откуда в пробе → когда в ролике), между ними — паузы под действие.
2. **Рот** — огибающая громкости голоса 30 к/с → ``mouth`` робота (deskbot.ts).
3. **Сцена** — tools/meme3d_scene.py (стол, монитор, toon ``ink``) + хореография ниже (поза, лицо, камера, облачка,
   вкладки браузера) — всё от времени реплик.
4. **Рендер** — node/scene3d/render.mjs, GPU, один браузер.
5. **Субтитры и обложка** — компоновщик моушна: слой captions «hormozi» (слово подсвечено), заголовок на кадре 0.
6. **Звук** — голос + музыка паков (обрывается на «скретче») + эффекты паков → −14 LUFS.
7. **Выдача** — 1080×1920 CFR 30, H.264 high 4.2, GOP 30, faststart + копия ≤ 20 МБ для Telegram; лист кадров,
   кадры на реплику и расшифровка Whisper готового звука.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
MAIN_ROOT = ROOT.parent.parent.parent if ".claude" in str(ROOT) else ROOT
PACKS = MAIN_ROOT / "vendor" / "packs"
MUSIC_DIR = Path(r"D:\vvf-offload\vtubika\music")

import meme3d_scene as S  # noqa: E402

FPS = 30
SR = 48000
TAKE = "take1_callum.mp3"
TAKE2 = "take2_callum.mp3"  # «Он мне… изменяет» отдельно: в первой пробе «мне» Whisper слышал как «не»
TITLE = "ИИ о своём\nпользователе"

# Реплики: (id, начало и конец в пробе, когда звучит в ролике, текст субтитров, усиление дБ[, файл пробы]).
LINES = [
    ("l1", 0.00, 2.36, 0.25, "Вот у всех пользователи как пользователи…", 0.0),
    ("l2", 2.50, 5.92, 2.75, "У одного — монтаж, эффекты, миллион просмотров.", 0.0),
    ("l3", 6.20, 9.06, 6.55, "У другого — свой сайт, график в небо.", 0.0),
    ("l4", 9.36, 10.02, 9.75, "А мой?", 0.0),
    ("l5", 10.30, 11.08, 13.25, "Эх…", 7.0),
    ("l6", 11.25, 15.14, 15.55, "Готово. Четыре. Три страницы, с таблицей.", 0.0),
    ("l7", 0.0, 2.2, 22.6, "…Он мне… изменяет.", 0.0, TAKE2),
    ("l8", 17.10, 18.62, 25.0, "И даже вопрос не поменял.", 0.0),
]
DURATION = 28.2
QUESTION = "Сколько будет 2 + 2? Распиши подробно, по шагам"


def _run(args: list[str], what: str, **kw) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, check=False, **kw)
    if proc.returncode != 0:
        raise RuntimeError(f"{what}: {proc.stderr.strip()[-1500:]}")
    return proc


def _log(msg: str) -> None:
    print(f"[meme3d] {msg}", file=sys.stderr, flush=True)


def free_ram_gb() -> float:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                         capture_output=True, text=True, check=False).stdout.strip()
    try:
        return int(out) / 1024 / 1024
    except ValueError:
        return 0.0


def wait_ram(min_gb: float = 6.0, tries: int = 40) -> None:
    for i in range(tries):
        gb = free_ram_gb()
        if gb >= min_gb:
            _log(f"свободно {gb:.1f} ГБ")
            return
        _log(f"свободно {gb:.1f} ГБ < {min_gb} — жду")
        time.sleep(30)
    raise SystemExit("мало памяти — рендер отложен")


# --------------------------------------------------------------------------- голос


def decode(path: Path):
    import numpy as np

    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def write_wav(path: Path, x) -> None:
    import wave

    import numpy as np

    y = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(y.tobytes())


def assemble_voice(work: Path) -> tuple[Path, list[dict], dict]:
    """Реплики пробы → дорожка ролика; слова Whisper пробы сдвигаются вместе с репликами."""
    import numpy as np

    takes: dict[str, tuple] = {}
    for name in {ln[6] if len(ln) > 6 else TAKE for ln in LINES}:
        take = work / name
        takes[name] = (decode(take), json.loads(take.with_suffix(".words.json").read_text("utf-8")))
    out = np.zeros(int(DURATION * SR), dtype=np.float32)
    words: list[dict] = []
    spans: dict[str, tuple[float, float]] = {}
    fade = int(0.012 * SR)
    for lid, a, b, at, text, gain, *rest in LINES:
        src, heard = takes[rest[0] if rest else TAKE]
        seg = src[int(a * SR):int(b * SR)].copy()
        seg[:fade] *= np.linspace(0, 1, fade)
        seg[-fade:] *= np.linspace(1, 0, fade)
        seg *= 10 ** (gain / 20)
        k = int(at * SR)
        out[k:k + len(seg)] += seg[:max(0, len(out) - k)]
        shift = at - a
        spans[lid] = (at, at + (b - a))
        got = [w for w in heard if a - 0.05 <= (w["t0"] + w["t1"]) / 2 <= b + 0.05]
        words.append({"line": lid, "text": text, "heard": [{**w, "t0": round(w["t0"] + shift, 3),
                                                             "t1": round(w["t1"] + shift, 3)} for w in got],
                      "start": round(at, 3), "end": round(at + (b - a), 3)})
    path = work / "voice.wav"
    write_wav(path, out)
    return path, words, spans


def caption_words(lines: list[dict]) -> list[dict]:
    """Слова субтитров — текст сценария на времени слов Whisper (по порядку; не совпало число — по длине слов)."""
    out: list[dict] = []
    for ln in lines:
        toks = [w for w in ln["text"].replace("—", " ").split() if any(ch.isalnum() for ch in w)]
        heard = [w for w in ln["heard"] if any(ch.isalnum() for ch in w["w"])]
        if heard and len(heard) == len(toks):
            out += [{"t0": h["t0"], "t1": h["t1"], "w": tok} for tok, h in zip(toks, heard)]
            continue
        t0 = heard[0]["t0"] if heard else ln["start"]
        t1 = heard[-1]["t1"] if heard else ln["end"]
        total = sum(len(t) for t in toks) or 1
        cur = t0
        for tok in toks:
            d = (t1 - t0) * len(tok) / total
            out.append({"t0": round(cur, 3), "t1": round(cur + d, 3), "w": tok})
            cur += d
    return out


def mouth_envelope(voice: Path) -> list[float]:
    """Огибающая для рта: RMS окна кадра, гейт тишины, нормировка по громким местам, лёгкое сглаживание."""
    import numpy as np

    x = decode(voice)
    hop = SR // FPS
    n = len(x) // hop
    rms = np.array([math.sqrt(float(np.mean(x[i * hop:(i + 1) * hop] ** 2)) + 1e-12) for i in range(n)])
    db = 20 * np.log10(rms + 1e-9)
    ref = np.percentile(db[db > -45], 90) if np.any(db > -45) else -20
    v = np.clip((db - (ref - 26)) / 26, 0, 1)
    v = np.convolve(v, [0.25, 0.5, 0.25], mode="same")
    return [round(float(a), 3) for a in v]


# --------------------------------------------------------------------------- хореография


def plan(spans: dict[str, tuple[float, float]]) -> dict:
    """Поза, лицо, камера, облачка, монитор — всё от времени реплик."""
    a = {k: v[0] for k, v in spans.items()}
    b = {k: v[1] for k, v in spans.items()}
    c = S.screen_center()
    yaw = math.radians(S.MONITOR_YAW)
    nx, nz = math.sin(yaw), math.cos(yaw)
    bx, by, bz = S.BOT_POS
    face_y = by + 0.168

    def cam(t, pos, tgt, fov=34, ease="inOutSine", cut=False):
        k = {"t": round(t, 3), "position": [round(v, 4) for v in pos], "target": [round(v, 4) for v in tgt],
             "fov": fov, "ease": ease}
        if cut:
            k["cut"] = True
        return k

    def mon(d, dx=0.0, dy=0.0, look_dx=0.0, look_dy=0.0):
        """Камера перед экраном монитора на расстоянии d (сдвиг вдоль экрана dx, вверх dy)."""
        rx, rz = nz, -nx  # вправо вдоль экрана
        pos = [c[0] + nx * d + rx * dx, c[1] + dy, c[2] + nz * d + rz * dx]
        tgt = [c[0] + rx * look_dx, c[1] + look_dy, c[2] + rz * look_dx]
        return pos, tgt

    START = ([bx + 0.05, face_y + 0.03, bz + 1.42], [bx - 0.01, face_y + 0.01, bz])
    MCU = ([bx + 0.03, face_y + 0.015, bz + 1.15], [bx, face_y - 0.0, bz])
    BUB = ([bx - 0.03, face_y + 0.17, bz + 1.4], [bx - 0.05, face_y + 0.145, bz])
    CU = ([bx + 0.01, face_y + 0.008, bz + 0.97], [bx, face_y - 0.008, bz])
    ECU = ([bx + 0.005, face_y + 0.004, bz + 0.82], [bx, face_y - 0.01, bz])
    MED = ([bx + 0.12, face_y + 0.06, bz + 1.35], [bx - 0.06, face_y + 0.01, bz])
    OTS = ([bx + 0.26, face_y + 0.2, bz + 1.32], [c[0] + 0.2, c[1] - 0.12, c[2] + 0.12])
    MON_Q = mon(1.9, dx=-0.14, dy=0.0, look_dx=0.0, look_dy=0.0)
    MON_Q2 = mon(1.74, dx=-0.13, dy=0.01, look_dx=0.01, look_dy=0.01)
    MON_G = mon(1.9, dx=-0.14, dy=0.0, look_dx=0.0, look_dy=0.0)
    MON_G2 = mon(1.72, dx=-0.13, dy=0.015, look_dx=-0.005, look_dy=0.02)

    t_chat = b["l4"] + 0.32  # монитор: вопрос набирается
    t_sent = t_chat + 0.85
    t_back = t_sent + 1.55  # обратно к роботу
    t_type = a["l5"] + 0.95  # печатает ответ
    t_ready = a["l6"] - 0.25
    t_ding = b["l6"] + 0.35
    t_gpt = t_ding + 0.22
    t_freeze = t_gpt + 1.45

    keys = [
        cam(0, *START),
        cam(b["l1"] - 0.05, *MCU, ease="inOutSine"),
        cam(b["l1"] + 0.25, *BUB, ease="inOutCubic"),
        cam(b["l3"] + 0.1, BUB[0], BUB[1]),
        cam(a["l4"] - 0.12, *CU, cut=True),
        cam(b["l4"] + 0.25, *ECU, ease="outCubic"),
        cam(t_chat, *MON_Q, cut=True),
        cam(t_back - 0.02, *MON_Q2, ease="linear"),
        cam(t_back, *MED, cut=True),
        cam(t_type - 0.15, MED[0], MED[1]),
        cam(t_type + 0.25, *OTS, ease="inOutCubic"),
        cam(t_ready - 0.05, OTS[0], OTS[1]),
        cam(t_ready + 0.2, *MCU, ease="outCubic"),
        cam(b["l6"], [MCU[0][0], MCU[0][1], MCU[0][2] - 0.04], MCU[1], ease="linear"),
        cam(t_gpt, *MON_G, cut=True),
        cam(t_gpt + 0.45, *MON_G2, ease="outCubic"),
        cam(t_freeze - 0.02, MON_G2[0], MON_G2[1]),
        cam(t_freeze, *MCU, cut=True),
        cam(t_freeze + 0.55, MCU[0], MCU[1]),
        cam(a["l7"] - 0.05, *CU, ease="inOutSine"),
        cam(b["l7"] + 0.2, *ECU, ease="outCubic"),
        cam(a["l8"] - 0.1, *ECU),
        cam(b["l8"] + 0.1, *CU, ease="inOutSine"),
        cam(b["l8"] + 0.55, *START, ease="inOutCubic"),
        cam(DURATION, *START),
    ]
    shake = [{"t": round(t_freeze, 3), "amp": 0.006, "duration": 0.5},
             {"t": round(b["l7"] + 0.02, 3), "amp": 0.004, "duration": 0.6}]

    # --- поза робота
    P = []

    def pose(t, **kw):
        P.append({"t": round(t, 3), **kw})

    MON_TURN = -32  # повернуться к монитору
    pose(0, turn=-18, lean=2, tilt=-4, armL="cross", armR="cross", squash=1, hop=0, type=0, shake=0)
    pose(0.9, turn=-12, tilt=3, lean=4, ease="inOutSine")
    pose(1.7, turn=-20, tilt=-5, ease="inOutSine")
    pose(b["l1"] - 0.2, turn=-8, tilt=0, lean=0, squash=0.96)
    # Облачко 1: мечтательно смотрит вверх, рука под подбородком.
    pose(b["l1"] + 0.25, turn=6, tilt=6, lean=-6, squash=1.04, armL="clasp", armR="clasp", ease="outBack")
    pose(a["l2"] + 1.6, tilt=9, lean=-8)
    pose(b["l2"] + 0.1, tilt=2, lean=-4, squash=1.0)
    pose(b["l2"] + 0.4, turn=0, tilt=-7, lean=-9, armL="clasp", armR="clasp", squash=1.05, ease="outBack")
    pose(b["l3"], tilt=-3, lean=-4)
    # «А мой?» — анфас, тяжёлый взгляд в камеру.
    pose(a["l4"] - 0.12, turn=0, tilt=0, lean=3, squash=0.97, armL="hips", armR="hips", ease="step")
    pose(b["l4"] + 0.3, lean=5, squash=0.95)
    # Смотрит на монитор (вопрос), вздох — сдувается.
    pose(t_back, turn=MON_TURN, lean=4, tilt=0, squash=1.0, armL="rest", armR="rest", ease="step")
    pose(a["l5"] - 0.1, squash=1.06, lean=-3, ease="outSine")
    pose(a["l5"] + 0.35, squash=0.86, lean=10, tilt=4, armL="back", armR="back", ease="inOutSine")
    pose(t_type - 0.2, squash=0.92, lean=8)
    # Печатает (ответ льётся).
    pose(t_type, turn=MON_TURN - 6, lean=14, tilt=0, squash=1.0, armL="type", armR="type", type=1, ease="outCubic")
    pose(t_ready - 0.05, type=1)
    # «Готово» — разворот к камере с подскоком и фальшивой улыбкой.
    pose(t_ready, turn=MON_TURN + 4, lean=6, squash=0.84, type=0, hop=0, ease="outCubic")
    pose(t_ready + 0.14, turn=-4, lean=-4, squash=1.14, hop=0.03, armL="hips", armR="hips", ease="outQuad")
    pose(t_ready + 0.32, squash=0.92, hop=0, ease="inQuad")
    pose(t_ready + 0.5, squash=1.0, lean=0, ease="outBack")
    pose(a["l6"] + 1.0, tilt=5)
    pose(a["l6"] + 2.2, tilt=-4)
    pose(b["l6"], tilt=0, turn=-2)
    # Динь — глаза к монитору, улыбка застыла.
    pose(t_ding + 0.05, turn=-10, squash=1.03, ease="outCubic")
    # Заморозка → медленный поворот в камеру.
    pose(t_freeze + 0.5, turn=-10, squash=1.0)
    pose(a["l7"] - 0.1, turn=0, lean=2, ease="inOutSine")
    pose(a["l7"] + 0.5, shake=0.35, squash=0.97, armL="rest", armR="rest")
    pose(b["l7"], shake=0.2)
    pose(a["l8"], shake=0.4, squash=0.95, lean=4)
    pose(b["l8"] - 0.1, shake=0.0)
    # Финал: «хм!» — руки скрестил, отвернулся, косится (и петля к кадру 0).
    pose(b["l8"] + 0.05, squash=0.88, lean=6, ease="outCubic")
    pose(b["l8"] + 0.25, turn=-18, tilt=-4, lean=2, squash=1.08, armL="cross", armR="cross", ease="outBack")
    pose(b["l8"] + 0.55, squash=1.0, ease="outCubic")
    pose(DURATION - 0.6, turn=-18, tilt=-4, lean=2)
    pose(DURATION, turn=-18, tilt=-4, lean=2)

    # --- лицо
    F = []

    def face(t, expr, look=None, blend=0.12, ease=None):
        k = {"t": round(t, 3), "expr": expr, "blend": blend}
        if look is not None:
            k["look"] = look
        if ease:
            k["ease"] = ease
        F.append(k)

    face(0, "grumpy", [-0.75, 0.1])
    face(0.95, "side", [-0.85, 0.05])
    face(1.55, "roll", [0.1, 1.0], blend=0.2)
    face(b["l1"] - 0.15, "meh", [0.2, 0.6])
    face(b["l1"] + 0.25, "dreamy", [-0.45, 0.85], blend=0.25)
    face(b["l2"] + 0.4, "dreamy", [-0.3, 0.9])
    face(a["l3"] + 1.7, "shocked", [-0.2, 0.9], blend=0.1)
    face(b["l3"] - 0.1, "dreamy", [-0.2, 0.7])
    face(a["l4"] - 0.12, "stare", [0, 0], blend=0.05)
    face(b["l4"] + 0.2, "stare", [0.05, 0])
    face(t_back, "stare", [-0.9, 0.1], blend=0.05)
    face(a["l5"] + 0.05, "sigh", [0, 0], blend=0.2)
    face(t_type, "working", [-0.7, 0.3], blend=0.15)
    face(t_ready + 0.12, "fake", [0, 0], blend=0.08)
    face(t_ding + 0.05, "fake", [-0.95, 0.1], blend=0.08)
    face(t_freeze + 0.55, "stare", [-0.9, 0.1], blend=0.25)
    face(a["l7"] - 0.1, "stare", [0, 0], blend=0.3)
    face(a["l7"] + 0.3, "offended", [0, 0], blend=0.2)
    face(a["l8"] + 0.1, "hurt", [0.1, -0.1], blend=0.2)
    face(b["l8"] + 0.15, "angry", [0.6, 0.1], blend=0.1)
    face(b["l8"] + 0.6, "grumpy", [0.75, 0.1], blend=0.25)
    blinks = [b["l4"] + 0.5, t_freeze + 0.4]

    # --- облачка мыслей над роботом (хвостик к голове)
    trail = [[0.03, -0.15, 0.028], [0.05, -0.195, 0.02], [0.065, -0.228, 0.013]]
    bub_pos = [bx - 0.12, by + 0.43, bz + 0.03]
    bubbles = [
        {"id": "bubble_video", "primitive": "thought_bubble", "position": bub_pos, "rotation": [0, 2, 0],
         "params": {"panel": "editor", "width": 0.32, "height": 0.22, "trail": trail, "seed": 11,
                    "panelParams": {"from": 87000, "to": 1000000, "dur": 2.6}},
         "state": [{"t": round(b["l1"] + 0.12, 3), "open": 1}, {"t": round(b["l2"] + 0.12, 3), "open": 0}]},
        {"id": "bubble_site", "primitive": "thought_bubble", "position": bub_pos, "rotation": [0, 2, 0],
         "params": {"panel": "graph", "width": 0.32, "height": 0.22, "trail": trail, "seed": 23,
                    "panelParams": {"to": 340, "dur": 2.2}},
         "state": [{"t": round(b["l2"] + 0.3, 3), "open": 1}, {"t": round(b["l3"] + 0.12, 3), "open": 0}]},
    ]

    # --- браузер на мониторе
    answer = [
        {"h": "Шаг 1. Берём число 2."},
        {"p": "Это одна двойка. Проверено."},
        {"h": "Шаг 2. Берём ещё одно число 2."},
        {"p": "Да, такое же."},
        {"h": "Шаг 3. Складываем."},
        {"table": [["a", "b", "a + b"], ["2", "2", "4"]]},
        {"h": "Вывод: 4."},
        {"p": "Источники: таблица выше, математика, здравый смысл."},
    ]
    browser = {"scale": 1.3, "tabs": [
        {"title": "Клод", "icon": "#d9774b", "letter": "К",
         "page": {"name": "Клод", "accent": "#d9774b", "letter": "К", "placeholder": "Сообщение Клоду…", "messages": [
             {"t": round(t_chat, 3), "from": "user", "text": QUESTION, "type": round(t_sent - t_chat, 3)},
             {"t": round(t_type, 3), "from": "bot", "think": 0.3, "blocks": answer, "stream": round(t_ready - t_type - 0.4, 3)},
         ]}},
        {"title": "ChatGPT", "icon": "#8a8f98",
         "page": {"name": "ChatGPT", "accent": "#8a8f98", "bg": "#ffffff", "bubble": "#e9ecef", "botBubble": "#f1f3f5",
                  "placeholder": "Спросите что-нибудь…", "messages": [
                      {"t": round(t_gpt - 2.0, 3), "from": "user", "text": QUESTION},
                  ]}},
    ]}
    monitor_state = [{"t": 0, "tab": 0},
                     {"t": round(t_ding, 3), "badge": {"tab": 1, "text": "(1)", "t": round(t_ding, 3)}},
                     {"t": round(t_gpt, 3), "tab": 1}]
    events = {"t_chat": t_chat, "t_sent": t_sent, "t_back": t_back, "t_type": t_type, "t_ready": t_ready,
              "t_ding": t_ding, "t_gpt": t_gpt, "t_freeze": t_freeze,
              "bubble1": b["l1"] + 0.12, "bubble1_off": b["l2"] + 0.12, "bubble2": b["l2"] + 0.3,
              "bubble2_off": b["l3"] + 0.12, "punch": a["l4"] - 0.12, "drama": b["l7"] + 0.02}
    return {"camera": {"fov": 34, "keys": keys, "shake": shake}, "pose": P, "face": F, "blinks": blinks,
            "bubbles": bubbles, "browser": browser, "monitor_state": monitor_state, "events": events}


def build_spec(pl: dict, mouth: list[float]) -> dict:
    return {
        "width": 1080, "height": 1920, "fps": FPS, "duration": DURATION,
        "environment": S.environment(), "lights": S.lights(),
        "objects": S.objects(pl["browser"], pl["monitor_state"], pl["bubbles"]),
        "characters": [S.deskbot(pl["pose"], pl["face"], {"fps": FPS, "start": 0, "values": mouth, "gain": 1.15},
                                 pl["blinks"])],
        "camera": pl["camera"],
        "quality": {"shadows": True, "shadowSize": 2048, "antialias": True},
    }


# --------------------------------------------------------------------------- звук


def sfx_list(ev: dict, spans: dict) -> list[dict]:
    fs = PACKS / "freesound"
    ki = PACKS / "kenney-interface"
    a = {k: v[0] for k, v in spans.items()}
    return [
        {"t": ev["bubble1"] + 0.1, "path": fs / "pop-cartoon.ogg", "volume": 0.8},
        {"t": ev["bubble2"] + 0.1, "path": fs / "pop-cartoon.ogg", "volume": 0.8},
        {"t": ev["bubble2_off"], "path": fs / "pop.ogg", "volume": 0.55},
        {"t": ev["punch"] - 0.08, "path": fs / "whip.ogg", "volume": 0.6},
        {"t": ev["t_chat"], "path": fs / "typing-digital.ogg", "volume": 0.45, "len": ev["t_sent"] - ev["t_chat"]},
        {"t": ev["t_sent"], "path": ki / "pluck_001.ogg", "volume": 0.9},
        {"t": a["l5"] + 0.05, "path": fs / "whoosh-4.ogg", "volume": 0.35},
        {"t": ev["t_type"], "path": fs / "typing.ogg", "volume": 0.6, "len": ev["t_ready"] - ev["t_type"]},
        {"t": ev["t_ready"] + 0.12, "path": fs / "boing.ogg", "volume": 0.45},
        {"t": ev["t_ding"], "path": fs / "ding.ogg", "volume": 0.9},
        # Хвост скретча не должен лезть на «Он мне изменяет» (иначе «мне» слышно как «не»).
        {"t": ev["t_freeze"], "path": fs / "record-scratch.ogg", "volume": 0.85, "len": 0.8},
        {"t": ev["drama"], "path": fs / "drama-boom.ogg", "volume": 0.5},
        {"t": spans["l8"][1] + 0.2, "path": fs / "whoosh-2.ogg", "volume": 0.4},
    ]


def mix(video: Path, voice: Path, ev: dict, spans: dict, out: Path, work: Path, report: dict) -> None:
    from app.audio_mix_model import ebur128
    from app.motion import asset_key
    from app.motion_render import build_audio_layers_args, speech_level_db
    from app.stories_3d import voice_lufs

    music = MUSIC_DIR / "chill-keys.mp3"
    voice_db = voice_lufs(voice)
    layers = [{"id": "voice", "type": "audio", "start": 0.0, "end": DURATION,
               "props": {"asset_id": 9001, "mode": "track", "volume": 1.0, "role": "voice"}}]
    assets = {asset_key(9001): str(voice)}
    m_lufs = float(ebur128(music)["i"])
    gain = round(voice_db - 13.0 - m_lufs, 2)
    assets[asset_key(9002)] = str(music)
    # Музыка обрывается на «скретче» (заморозка), возвращается тихо к финальной петле.
    layers.append({"id": "music", "type": "audio", "start": 0.0, "end": round(ev["t_freeze"] + 0.05, 3),
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music",
                             "fadeIn": 0.4, "fadeOut": 0.08}})
    back = round(spans["l8"][1] + 0.2, 3)
    layers.append({"id": "music2", "type": "audio", "start": back, "end": DURATION,
                   "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music",
                             "offset": 0.0, "fadeIn": 0.6, "fadeOut": 0.3}})
    report["music"] = {"file": music.name, "lufs": m_lufs, "voice_lufs": voice_db, "gain_db": gain}
    notes: list[str] = []
    for n, s in enumerate(sfx_list(ev, spans)):
        key = 9100 + n
        assets[asset_key(key)] = str(s["path"])
        end = s["t"] + float(s.get("len") or 2.5)
        layers.append({"id": f"sfx{n}", "type": "audio", "start": round(s["t"], 3), "end": round(min(DURATION, end), 3),
                       "props": {"asset_id": key, "volume": s["volume"], "mode": "effect",
                                 **({"fadeOut": 0.15} if s.get("len") else {})}})
    pre = work / "mixed.mp4"
    args = build_audio_layers_args(video, pre, layers, assets, DURATION, clip_has_audio=False,
                                   speech_db=speech_level_db(voice), notes=notes)
    _run(args, "сведение")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)], "громкость")
    report["mix_notes"] = notes


# --------------------------------------------------------------------------- субтитры и обложка


def captions(video: Path, words: list[dict], out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    total = DURATION
    layers = [
        {"id": "title", "type": "text", "start": 0.0, "end": 2.2, "x": 0.5, "y": 0.13,
         "props": {"text": TITLE, "font": "Montserrat", "weight": 900, "size": 96, "color": "#fff6e8", "align": "center",
                   "uppercase": True, "lineHeight": 1.05,
                   "stroke": {"color": "#2b1810", "width": 12},
                   "bg": {"color": "#d9774b", "opacity": 1, "radius": 30, "padX": 40, "padY": 22}},
         "out": {"type": "fade", "duration": 0.25}},
        {"id": "caps", "type": "captions", "start": 0.0, "end": round(total, 3), "x": 0.5, "y": 0.75,
         "props": {"style": "hormozi", "size": 84, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 3,
                   "maxLines": 2, "stroke": {"color": "#2b1810", "width": 11}}},
    ]
    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=FPS, duration=total, layers=layers, camera=[],
                      words=words)
    (work / "assets.json").write_text("{}", "utf-8")
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-1200:]}")


# --------------------------------------------------------------------------- выдача и проверки


def encode(src: Path, out: Path, tg: Path) -> None:
    common = ["-fps_mode", "cfr", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
              "-level", "4.2", "-g", str(FPS), "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=1080:1920:flags=lanczos,setsar=1", *common,
          "-preset", "slow", "-crf", "17", "-maxrate", "12M", "-bufsize", "24M", str(out)], "финал")
    # Telegram: ≤ 20 МБ — средний битрейт с запасом.
    dur = DURATION
    kbps = int((19.0 * 8 * 1024) / dur) - 200
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=1080:1920:flags=lanczos,setsar=1", *common[:-6],
          "-preset", "slow", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k",
          "-movflags", "+faststart", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", str(tg)], "копия для Telegram")


def frame_sheet(video: Path, times: list[float], labels: list[str], out: Path, cols: int = 6) -> None:
    tmp = out.parent / "_sheet"
    tmp.mkdir(exist_ok=True)
    files = []
    for i, t in enumerate(times):
        f = tmp / f"{i:02d}.jpg"
        _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=360:640",
              "-q:v", "3", str(f)], "кадр")
        files.append(f)
    inputs: list[str] = []
    for f in files:
        inputs += ["-i", str(f)]
    n = len(files)
    chains = []
    for i in range(n):
        lab = labels[i].replace(":", "\\:").replace("'", "")
        chains.append(f"[{i}:v]drawtext=text='{lab}':x=8:y=8:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.6[v{i}]")
    layout = "|".join(f"{(i % cols) * 360}_{(i // cols) * 640}" for i in range(n))
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=white[o]"
    _run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", "-frames:v", "1", "-q:v", "3",
          str(out)], "лист")
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="pilot1")
    ap.add_argument("--skip-render", action="store_true", help="взять work/scene.mp4 как есть")
    args = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(PACKS))
    out_dir = Path(args.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    voice, lines, spans = assemble_voice(work)
    words = caption_words(lines)
    mouth = mouth_envelope(voice)
    pl = plan(spans)
    spec = build_spec(pl, mouth)
    spec_path = work / "scene.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"spans": spans, "events": pl["events"], "words": words},
                                                 ensure_ascii=False, indent=1), "utf-8")
    _run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(spec_path), "--packs", str(PACKS),
              "--gl", args.gl, "--workers", "1"]
    if args.preview:
        wait_ram()
        prev = work / "preview"
        shutil.rmtree(prev, ignore_errors=True)
        r = _run([*render, "--preview", args.preview, "--preview-dir", str(prev)], "превью")
        print(r.stdout.strip().splitlines()[-1][:400])
        return 0
    raw = work / "scene.mp4"
    if not args.skip_render:
        wait_ram()
        t0 = time.time()
        r = _run([*render, "--out", str(raw), "--crf", "14"], "рендер")
        report["render"] = json.loads(r.stdout.strip().splitlines()[-1])
        report["render_sec"] = round(time.time() - t0, 1)
    capped = work / "caps.mp4"
    captions(raw, words, capped, work)
    mixed = work / "final_mix.mp4"
    mix(capped, voice, pl["events"], spans, mixed, work, report)
    final = out_dir / f"{args.name}.mp4"
    tg = out_dir / f"{args.name}_tg.mp4"
    encode(mixed, final, tg)
    times = [0.0, 1.2, 3.6, 7.8, 9.9, 11.6, 13.5, 14.6, 16.2, 19.8, 21.3, 23.4, 25.5, DURATION - 0.6]
    frame_sheet(final, times, [f"{t:.1f}s" for t in times], out_dir / "sheet.jpg", cols=7)
    report.update({"video": str(final), "tg": str(tg), "size_mb": round(final.stat().st_size / 2**20, 2),
                   "tg_mb": round(tg.stat().st_size / 2**20, 2), "duration": DURATION})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
