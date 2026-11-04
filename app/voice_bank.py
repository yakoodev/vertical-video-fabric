"""Банк голосов: пары «голос → человек» на канал.

Владелец: «почему мы не анализируем голоса людей и не делаем пары?». Буквы A/B/C от разбора голосов
(app/ai/speakers.py) в каждом клипе свои и ничего не говорят о том, кто это. Здесь голос — отпечаток:
эмбеддинг CAM++ (3D-Speaker, Apache-2.0, 30 МБ, CPU). Новых пакетов нет: onnxruntime уже приезжает
с faster-whisper, fbank считаем сами, сессия живёт только на время вызова — RAM ограничена.

Точность зависит от длины: на стриме с игрой от 15 с речи различаются чётко (свой голос 0.62–0.84, чужой
до 0.49), короткие реплики заметно хуже. Поэтому имя ставится букве по всей её речи, неуверенное не ставится,
а спорные реплики показываются, а не правятся.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from app.settings import settings

RATE = 16000
MODEL_FILE = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/" + MODEL_FILE
MODEL_MIN_BYTES = 20_000_000
MIN_ENROLL_SEC = 6.0  # меньше — отпечаток случайный
GOOD_ENROLL_SEC = 15.0
MIN_IDENTIFY_SEC = 1.5
MIN_LINE_SEC = 2.5  # реплика короче — сравнивать бессмысленно
MAX_AUDIO_SEC = 90.0
MIN_MARGIN = 0.10  # лучшее совпадение настолько выше второго
SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS voice_bank (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'person',
        embedding_json TEXT NOT NULL,
        seconds REAL NOT NULL DEFAULT 0,
        samples_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_voice_bank_project ON voice_bank(project_id, name)",
)


def ensure_voice_schema(conn: Any) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


def min_score(seconds: float) -> float:
    """Порог совпадения: косинус растёт с длиной речи (2 с — 0.45, 8 с и больше — 0.60)."""
    return 0.40 + 0.025 * min(max(0.0, seconds), 8.0)


# --------------------------------------------------------------------------- #
# Отпечаток
# --------------------------------------------------------------------------- #

_FRAME, _SHIFT, _NFFT, _MELS = 400, 160, 512, 80


def _mel_bank() -> Any:
    import numpy as np

    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)  # noqa: E731
    pts = np.linspace(mel(20.0), mel(RATE / 2.0), _MELS + 2)
    freqs = mel(np.arange(_NFFT // 2 + 1) * RATE / _NFFT)
    bank = np.zeros((_MELS, _NFFT // 2 + 1), dtype=np.float32)
    for m in range(_MELS):
        up = (freqs - pts[m]) / (pts[m + 1] - pts[m])
        down = (pts[m + 2] - freqs) / (pts[m + 2] - pts[m + 1])
        bank[m] = np.maximum(0.0, np.minimum(up, down))
    return bank


def fbank(pcm: Any) -> Any:
    """Kaldi fbank 80 (окно Пови, предыскажение 0.97) с вычетом среднего — вход моделей 3D-Speaker."""
    import numpy as np

    count = 1 + (len(pcm) - _FRAME) // _SHIFT
    if count <= 0:
        return np.zeros((0, _MELS), dtype=np.float32)
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_FRAME) / (_FRAME - 1))) ** 0.85
    index = np.arange(_FRAME)[None, :] + _SHIFT * np.arange(count)[:, None]
    frames = pcm[index].astype(np.float32)
    frames = frames - frames.mean(axis=1, keepdims=True)
    pre = frames.copy()
    pre[:, 1:] -= 0.97 * frames[:, :-1]
    pre[:, 0] -= 0.97 * frames[:, 0]
    spectrum = np.abs(np.fft.rfft(pre * window, _NFFT)) ** 2
    feat = np.log(np.maximum(spectrum @ _mel_bank().T, 1.19e-7))
    return (feat - feat.mean(axis=0, keepdims=True)).astype(np.float32)


def model_path() -> Path:
    """Файл модели; нет — скачать в данные сервиса (как модели whisper). Не вышло — RuntimeError."""
    folder = settings.data_dir / "voice-models"
    path = folder / MODEL_FILE
    if path.is_file() and path.stat().st_size >= MODEL_MIN_BYTES:
        return path
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    try:
        urllib.request.urlretrieve(MODEL_URL, tmp)
        if tmp.stat().st_size < MODEL_MIN_BYTES:
            raise OSError("файл слишком маленький")
        tmp.replace(path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"не удалось скачать модель голосов: {exc}") from exc
    return path


def embedder() -> Any:
    """Функция pcm → единичный вектор. Сессия живёт, пока жива функция."""
    import numpy as np
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(model_path()), options, providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name

    def embed(pcm: Any) -> Any:
        vector = session.run(None, {name: fbank(pcm)[None, ...]})[0].reshape(-1)
        return vector / (np.linalg.norm(vector) + 1e-9)

    return embed


def _cut(pcm: Any, spans: list[tuple[float, float]], limit: float = MAX_AUDIO_SEC) -> Any:
    """Отрезки сэмплов подряд, не длиннее ``limit`` — равномерно по всем, не только начало."""
    import numpy as np

    clean = [(a, b) for a, b in spans if b > a]
    total = sum(b - a for a, b in clean)
    keep = min(1.0, limit / total) if total else 1.0
    parts = [pcm[int(a * RATE):int((a + (b - a) * keep) * RATE)] for a, b in clean]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def _voice_spans(words: list[dict], gap: float = 0.35) -> dict[str, list[tuple[float, float]]]:
    """Речь каждой буквы: отрезки слов без наложений с чужими — смесь двух голосов портит отпечаток."""
    letters = sorted({str(w.get("speaker") or "A") for w in words})
    timed = sorted(((float(w["start"]), float(w["end"]), str(w.get("speaker") or "A")) for w in words))
    out: dict[str, list[tuple[float, float]]] = {}
    for letter in letters:
        spans: list[list[float]] = []
        others = [(a, b) for a, b, s in timed if s != letter]
        for a, b, s in timed:
            if s != letter or any(x < b and y > a for x, y in others):
                continue
            if spans and a - spans[-1][1] <= gap:
                spans[-1][1] = max(spans[-1][1], b)
            else:
                spans.append([a, b])
        out[letter] = [(a, b) for a, b in spans]
    return out


# --------------------------------------------------------------------------- #
# Банк
# --------------------------------------------------------------------------- #


def plan_project(store: Any, clip_plan_id: int) -> int | None:
    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    project = source.get("project_id")
    return int(project) if project else None


def _decode(row: Any) -> dict:
    item = dict(row)
    item["samples"] = json.loads(item.pop("samples_json") or "[]")
    item["embedding"] = json.loads(item.pop("embedding_json") or "[]")
    return item


def _public(item: dict) -> dict:
    return {k: item[k] for k in ("id", "project_id", "name", "kind", "seconds", "samples", "updated_at")}


def bank_rows(store: Any, project_id: int | None) -> list[dict]:
    """Голоса канала и общие (без проекта). Канал не задан — только общие."""
    rows = store.db.query_all(
        "SELECT * FROM voice_bank WHERE project_id IS NULL OR project_id = ? ORDER BY name COLLATE NOCASE, id", (project_id,)
    )
    return [_decode(r) for r in rows]


def list_voices(store: Any, project_id: int | None = None) -> list[dict]:
    return [_public(v) for v in bank_rows(store, project_id)]


def delete_voice(store: Any, voice_id: int) -> None:
    if not store.db.query_one("SELECT id FROM voice_bank WHERE id = ?", (voice_id,)):
        raise KeyError(f"voice not found: {voice_id}")
    store.db.execute("DELETE FROM voice_bank WHERE id = ?", (voice_id,))


def _source_pcm(store: Any, source_id: int, start: float, end: float) -> Any:
    import numpy as np

    from app.source_audio import speech_mix

    source = store.get_source(source_id)
    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ValueError("исходник недоступен")
    mix = speech_mix(source) or []
    track = int(mix[0]["track"]) if mix else 0
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(path), "-map", f"0:a:{track}",
         "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, check=True, timeout=300,
    ).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def register_voice(
    store: Any, name: str, *, clip_plan_id: int | None = None, source_id: int | None = None, start: float | None = None,
    end: float | None = None, speaker: str | None = None, kind: str = "person", project_id: int | None = None,
) -> dict:
    """Запомнить голос: отрезок клипа/источника или все слова буквы ``speaker`` клипа.

    То же имя в том же проекте — новый образец к прежнему отпечатку (среднее по секундам).
    Слушается только речь (VAD): паузы и игра в отпечаток не идут."""
    import numpy as np

    from app.audio_map import vad_segments
    from app.speech_activity import plan_pcm

    name = str(name or "").strip()[:40]
    if not name:
        raise ValueError("нужно имя голоса")
    if kind not in ("person", "bot"):
        raise ValueError("kind — person или bot")
    sample: dict = {}
    if clip_plan_id is not None:
        project_id = project_id if project_id is not None else plan_project(store, clip_plan_id)
        pcm = plan_pcm(store, clip_plan_id)
        sample["clip_id"] = int(clip_plan_id)
        if speaker:
            letter = str(speaker).strip().upper()[:1]
            words = (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
            spans = _voice_spans(words).get(letter) or []
            if not spans:
                raise ValueError(f"у голоса {letter} в клипе нет слов")
            pcm = _cut(pcm, spans)
            sample["speaker"] = letter
        elif start is not None and end is not None and end > start:
            pcm = _cut(pcm, [(float(start), float(end))])
            sample.update(start=float(start), end=float(end))
        else:
            raise ValueError("нужен отрезок start/end или голос speaker")
    elif source_id is not None and start is not None and end is not None and end > start:
        source = store.get_source(int(source_id))
        project_id = project_id if project_id is not None else (int(source["project_id"]) if source.get("project_id") else None)
        pcm = _source_pcm(store, int(source_id), float(start), float(end))
        sample.update(source_id=int(source_id), start=float(start), end=float(end))
    else:
        raise ValueError("нужен клип (speaker или start/end) или source_id со start/end")
    voiced = _cut(pcm, vad_segments(pcm, RATE)[0])
    seconds = round(len(voiced) / RATE, 1)
    if seconds < MIN_ENROLL_SEC:
        raise ValueError(f"чистой речи {seconds:g} с, нужно хотя бы {MIN_ENROLL_SEC:g} — возьми отрезок длиннее")
    vector = embedder()(voiced)
    sample["seconds"] = seconds
    row = store.db.query_one(
        "SELECT * FROM voice_bank WHERE name = ? COLLATE NOCASE AND project_id IS ?", (name, project_id)
    )
    if row:
        old = _decode(row)
        merged = np.asarray(old["embedding"]) * float(old["seconds"]) + vector * seconds
        merged = merged / (np.linalg.norm(merged) + 1e-9)
        total = round(float(old["seconds"]) + seconds, 1)
        store.db.execute(
            "UPDATE voice_bank SET kind = ?, embedding_json = ?, seconds = ?, samples_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (kind, json.dumps([round(float(x), 5) for x in merged]), total, json.dumps(old["samples"] + [sample], ensure_ascii=False), old["id"]),
        )
        voice_id = int(old["id"])
    else:
        cur = store.db.execute(
            "INSERT INTO voice_bank (project_id, name, kind, embedding_json, seconds, samples_json) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, name, kind, json.dumps([round(float(x), 5) for x in vector]), seconds, json.dumps([sample], ensure_ascii=False)),
        )
        voice_id = int(cur.lastrowid)
    saved = _decode(store.db.query_one("SELECT * FROM voice_bank WHERE id = ?", (voice_id,)))
    out = _public(saved)
    if saved["seconds"] < GOOD_ENROLL_SEC:
        out["warning"] = f"в отпечатке {saved['seconds']:g} с речи — надёжнее от {GOOD_ENROLL_SEC:g} с: добавь образец"
    return out


# --------------------------------------------------------------------------- #
# Опознание
# --------------------------------------------------------------------------- #


def best_match(vector: Any, rows: list[dict], seconds: float) -> dict:
    """``{name, voice_id, score, margin, sure}``; лучшего совпадения мало — ``sure: False``."""
    import numpy as np

    scored = sorted(((float(np.dot(vector, np.asarray(r["embedding"]))), r) for r in rows), key=lambda x: -x[0])
    if not scored:
        return {"name": None, "voice_id": None, "score": 0.0, "margin": 0.0, "sure": False}
    score, row = scored[0]
    margin = score - scored[1][0] if len(scored) > 1 else score
    return {"name": row["name"], "voice_id": int(row["id"]), "score": round(score, 3), "margin": round(margin, 3),
            "sure": score >= min_score(seconds) and margin >= MIN_MARGIN}


def identify_voices(store: Any, clip_plan_id: int, *, labels: bool = True, reassign: bool = False) -> dict:
    """Кто есть кто в клипе. Буква получает имя, только если совпадение уверенное (``sure``).

    ``labels`` — подписывать реплики не главного голоса именем («Глеб: …» в субтитрах и в прожиге).
    ``conflicts`` — реплики ≥ 2.5 с, которые звучат как другой человек из банка: разбор голосов мог
    ошибиться. ``reassign: true`` отдаёт такие реплики букве этого человека (если у него есть буква)."""
    from app.ai.speakers import _words_key, lines_from_words
    from app.speech_activity import plan_pcm

    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    words = [dict(w) for w in payload.get("words") or [] if str(w.get("word") or "").strip()]
    if not words:
        raise ValueError("у клипа нет субтитров — сначала распознай речь и раздели голоса")
    rows = bank_rows(store, plan_project(store, clip_plan_id))
    if not rows:
        raise ValueError("в банке голосов пусто — сначала register_voice")
    pcm = plan_pcm(store, clip_plan_id)
    embed = embedder()
    voices: dict[str, dict] = {}
    for letter, spans in _voice_spans(words).items():
        chunk = _cut(pcm, spans)
        seconds = round(len(chunk) / RATE, 1)
        if seconds < MIN_IDENTIFY_SEC:
            voices[letter] = {"name": None, "voice_id": None, "score": 0.0, "margin": 0.0, "sure": False, "seconds": seconds,
                              "why": "меньше 1.5 с речи без наложений"}
            continue
        voices[letter] = {**best_match(embed(chunk), rows, seconds), "seconds": seconds}
    named = {letter: v["name"] for letter, v in voices.items() if v.get("sure")}
    # Один человек на двух буквах — так бывает: разбор разделил интонации одного голоса.
    by_name: dict[str, list[str]] = {}
    for letter, name in named.items():
        by_name.setdefault(name, []).append(letter)
    conflicts: list[dict] = []
    letter_of = {name: sorted(letters)[0] for name, letters in by_name.items()}
    for line in lines_from_words(words):
        a, b = float(line["start"]), float(line["end"])
        letter = str(line.get("speaker") or "A")
        if b - a < MIN_LINE_SEC:
            continue
        guess = best_match(embed(_cut(pcm, [(a, b)])), rows, b - a)
        if guess["sure"] and named.get(letter) and guess["name"] != named[letter]:
            conflicts.append({"start": round(a, 2), "end": round(b, 2), "text": str(line["text"])[:60], "voice": letter,
                              "was": named[letter], "sounds_like": guess["name"], "score": guess["score"]})
    moved = 0
    if reassign:
        for item in conflicts:
            target = letter_of.get(item["sounds_like"])
            if not target:
                continue
            for w in words:
                if item["start"] - 1e-3 <= float(w["start"]) < item["end"]:
                    w["speaker"] = target
                    moved += 1
    names = {letter: {"name": v["name"], "score": v["score"], "voice_id": v["voice_id"]}
             for letter, v in voices.items() if v.get("sure")}
    speakers = dict(payload.get("speakers") or {})
    speakers.update({letter: v["name"] for letter, v in names.items()})
    fresh = {**payload, "speakers": speakers, "voice_names": names, "speaker_labels": bool(labels)}
    if moved:
        fresh.update(words=words, lines=lines_from_words(words), edited=True, speakers_key=_words_key(words))
    store.set_clip_plan_subtitles(clip_plan_id, fresh)
    unsure = [{"voice": letter, "closest": v.get("name"), "score": v.get("score"), "seconds": v.get("seconds"),
               **({"why": v["why"]} if v.get("why") else {})}
              for letter, v in voices.items() if not v.get("sure")]
    return {"voices": voices, "names": {k: v["name"] for k, v in names.items()}, "unsure": unsure,
            "conflicts": conflicts[:30], "moved": moved, "labels": bool(labels), "subtitles": fresh}


def with_speaker_labels(payload: Any) -> Any:
    """Подпись говорящего — в готовых субтитрах, а не в тексте: первое слово каждой реплики не главного
    голоса с именем идёт как «Глеб: Я». Хранимые слова не меняются, время слова — то же."""
    if not isinstance(payload, dict) or not payload.get("speaker_labels") or not payload.get("voice_names"):
        return payload
    names = {k: str((v or {}).get("name") or "") for k, v in payload["voice_names"].items()}
    main = str(payload.get("main_speaker") or "A")
    out, last = [], None
    for word in sorted((w for w in payload.get("words") or [] if isinstance(w, dict)), key=lambda w: float(w.get("start") or 0)):
        voice = str(word.get("speaker") or "A")
        if voice != last and voice != main and names.get(voice):
            word = {**word, "word": f"{names[voice]}: {word.get('word')}"}
        last = voice
        out.append(word)
    return {**payload, "words": out}

