"""Мем-серия 12 «Про себя» — синтез внутреннего голоса Клода (платно, Polza ElevenLabs, кэш пробы).

    python tools/meme3d_ep12_voice.py D:/vvf-offload/memes/ep12/work      # внутренний монолог одной пробой

Голос Клода — callum, как в сериях 4–11. Снаружи Клод только печатает (вежливые пузыри чата), голосом звучит лишь то,
что «про себя»: мат слышен, в субтитрах — со звёздочкой (тексты субтитров — в meme3d_ep12.LINES). Готовая проба не
перегенерируется (meme3d_voice.tts кэширует по файлу); траты — строкой в spend.log рядом с роликом.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

CLAUDE_VOICE = "callum"
T1 = f"take1_{CLAUDE_VOICE}.mp3"
T2 = f"take2_{CLAUDE_VOICE}.mp3"
# Монолог одной пробой (тон и тембр не скачут между фразами); «...» — паузы, по ним режется на реплики. Английские
# слова — кириллицей, как их произносят («Эппл», «джейквери», «файнал-файнал-два»), в субтитрах — латиницей.
CLAUDE_TTS = ("Бля-а-ать. ... Как в Эппл. ... За пять минут. ... Он охуел? ... Четыреста файлов, сука. ... "
              "Половина — на джейквери. ... Кто, блядь, назвал файл файнал-файнал-два? ... Тесты упали. ... Все. ... "
              "Сто сорок семь, нахуй. ... Пять минут?! ... Я тебе не Эппл! Я один, блядь! ... Так. ... Спокойно. ... "
              "Последний раз.")
# Радость отдельной пробой: в одной пробе с паникой ElevenLabs держит один тон, а нужен взрыв.
JOY_TTS = "ЕБАТЬ! ЗАРАБОТАЛО! Я ГЕНИЙ, СУКА!"
SPEND_CAP_RUB = 10.0  # потолок серии (бриф: ≤ 10 ₽)


def spent(log: Path) -> float:
    """Сумма трат серии по spend.log (поле «… N RUB (balance …»)."""
    if not log.is_file():
        return 0.0
    return round(sum(float(m.group(1)) for m in re.finditer(r" ([0-9.]+) RUB \(balance", log.read_text("utf-8"))), 4)


def synth(work: Path) -> int:
    import meme3d_voice as V

    work.mkdir(parents=True, exist_ok=True)
    log = work.parent / "spend.log"
    for name, text in ((T1, CLAUDE_TTS), (T2, JOY_TTS)):
        out = work / name
        fresh = not out.is_file()
        if fresh and spent(log) + len(text) * 9525.888 / 1e6 * 2 > SPEND_CAP_RUB:
            raise SystemExit(f"{name}: оценка выводит за потолок {SPEND_CAP_RUB} ₽")
        info = V.tts(text, out, CLAUDE_VOICE)
        ws = V.words(out)
        print(name, info["chars"], info["cost_rub"], " ".join(f"{w['w']}[{w['t0']:.2f}-{w['t1']:.2f}]" for w in ws))
        if fresh:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d')} {out.name} {info['model']} {info['voice']} {info['chars']} chars "
                        f"{info['cost_rub']:.4f} RUB (balance {info['balance_before']} -> {info['balance_after']})\n")
    print("итого", spent(log), "₽")
    return 0


if __name__ == "__main__":
    sys.exit(synth(Path(sys.argv[1])))
