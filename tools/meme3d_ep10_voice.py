"""Мем-серия 10 «Сделай за ночь» — тексты синтеза и озвучка (платно, Polza ElevenLabs, кэш пробы).

    python tools/meme3d_ep10_voice.py D:/vvf-offload/memes/ep10/work      # Клод одной пробой + юзер по пробе на реплику

Голоса — как в серии 9 v2: Клод — callum, юзер — liam (владелец после серии 9: «реплики юзера озвучивать»). Готовая
проба не перегенерируется (meme3d_voice.tts кэширует по файлу); траты — строкой в spend.log рядом с роликом.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

CLAUDE_VOICE = "callum"
USER_VOICE = "liam"
T1 = f"take1_{CLAUDE_VOICE}.mp3"
# Клод — одна проба на все реплики (тон и тембр не скачут между фразами); «...» — паузы, по ним режется на реплики.
CLAUDE_TTS = ("Одна кнопка. ... За ночь. ... Принято. ... Кнопке, конечно, нужна база данных. ... Базе нужны микросервисы. ... "
              "Микросервисам — очередь сообщений. ... И документация. ... Тридцать страниц. ... Без неё никак. ... "
              "Сорок семь файлов. ... Красота. ... Кнопка… ... в планах на второй спринт.")
# Юзер — по пробе на реплику: сонное «я спать» и утреннее «где кнопка» — разная подача.
USER_TTS = [
    ("u1", "Клод, сделай кнопку «Купить»… Я спать."),
    ("u2", "Ну что, где кнопка?"),
]
SPEND_CAP_RUB = 15.0  # потолок серии (бриф ночи 22→23.11)


def user_take(lid: str) -> str:
    return f"{lid}_{USER_VOICE}.mp3"


def spent(log: Path) -> float:
    """Сумма трат серии по spend.log (поле «… N RUB (balance …»)."""
    import re

    if not log.is_file():
        return 0.0
    return round(sum(float(m.group(1)) for m in re.finditer(r" ([0-9.]+) RUB \(balance", log.read_text("utf-8"))), 4)


def synth(work: Path) -> int:
    import meme3d_voice as V

    work.mkdir(parents=True, exist_ok=True)
    log = work.parent / "spend.log"
    jobs = [("c", T1, CLAUDE_TTS, CLAUDE_VOICE)] + [(lid, user_take(lid), text, USER_VOICE) for lid, text in USER_TTS]
    for lid, name, text, voice in jobs:
        out = work / name
        fresh = not out.is_file()
        if fresh and spent(log) + len(text) * 9525.888 / 1e6 * 2 > SPEND_CAP_RUB:
            raise SystemExit(f"{name}: оценка выводит за потолок {SPEND_CAP_RUB} ₽")
        info = V.tts(text, out, voice)
        ws = V.words(out)
        print(lid, info["chars"], info["cost_rub"], " ".join(f"{w['w']}[{w['t0']:.2f}-{w['t1']:.2f}]" for w in ws))
        if fresh:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d')} {out.name} {info['model']} {info['voice']} {info['chars']} chars "
                        f"{info['cost_rub']:.4f} RUB (balance {info['balance_before']} -> {info['balance_after']})\n")
    print("итого", spent(log), "₽")
    return 0


if __name__ == "__main__":
    sys.exit(synth(Path(sys.argv[1])))
