"""Joining montage parts with transitions and join sounds.

A clip plan with several segments is rendered part by part (same preset, same
size and fps), then joined here. The old join was a stream-copy concat: a hard
cut on picture AND sound, with an audible click where the waveform got chopped.

Then every join got the SAME effect — so a jump-cut inside one phrase dissolved
just like a jump to another scene half an hour later, and a 0.35 s dissolve on a
Shorts cut reads as slow. Now:

* the set is punchy: ``whip``, ``zoompunch``, ``glitch``, ``flash`` are forced
  short (0.12–0.16 s), the soft ones default to 0.25 s;
* joins are planned one by one (``plan_joins``): a jump-cut inside the same
  scene stays a hard cut, the effect is spent only where the scene really
  changes. Turn it off with ``smart: False`` to get the same effect everywhere.

Settings (all optional, see ``normalize_transition``):

* ``type``      — how the picture changes at a scene change (``cut`` = hard cut,
                  or an xfade transition such as ``whip``/``flash``/``fade``);
* ``duration``  — transition length in seconds (ignored for ``cut`` and for the
                  snappy types, which are forced short);
* ``smart``     — plan joins one by one instead of one effect everywhere;
* ``audio``     — ``smooth``: crossfade the sound under the transition (or a
                  30 ms de-click for hard cuts); ``hard``: cut the sound in the
                  middle of the transition;
* ``sfx``       — a short sound on every join: ``none``/``whoosh``/``click``/
                  ``pop``/``boom``, synthesised by ffmpeg itself, so no asset
                  files are needed;
* ``sfx_volume``— 0..1.

Pure functions only (argument builders): the caller runs ffmpeg. That keeps the
filter graph testable without media.
"""

from __future__ import annotations

from pathlib import Path

# key -> (label, xfade transition name or None for a hard cut)
TRANSITIONS: dict[str, tuple[str, str | None]] = {
    "cut": ("Встык", None),
    "whip": ("Вжух (whip)", "slideleft"),
    "zoompunch": ("Зум-панч", "zoomin"),
    "glitch": ("Глитч", "pixelize"),
    "flash": ("Вспышка", "fadewhite"),
    "fadeblack": ("Через чёрный", "fadeblack"),
    "fadewhite": ("Через белый", "fadewhite"),
    "fade": ("Наплыв", "fade"),
    "dissolve": ("Растворение", "dissolve"),
    "slide": ("Сдвиг", "slideleft"),
    "smooth": ("Плавный сдвиг", "smoothleft"),
    "wipe": ("Шторка", "wipeleft"),
    "zoom": ("Зум", "zoomin"),
}

SFX_KINDS: dict[str, str] = {
    "none": "Без звука",
    "whoosh": "Вжух",
    "click": "Щелчок",
    "pop": "Поп",
    "boom": "Удар",
}

AUDIO_MODES: dict[str, str] = {
    "smooth": "Плавно",
    "hard": "Резко",
}

DEFAULT_TRANSITION = {
    "type": "cut",
    "duration": 0.25,
    "smart": True,
    "audio": "smooth",
    "sfx": "none",
    "sfx_volume": 0.5,
}

# Резкие переходы читаются только короткими — длину для них не спрашиваем.
SNAP_DURATIONS = {
    "flash": 0.12,
    "whip": 0.14,
    "glitch": 0.14,
    "zoompunch": 0.16,
}
MIN_DURATION = 0.08
MAX_DURATION = 1.5
# Дырка в исходнике меньше этого — та же сцена (jump-cut по паузе): встык.
SAME_SCENE_GAP_SEC = 2.5
# De-click fade on hard cuts: long enough to kill the pop, too short to hear.
DECLICK_SEC = 0.03
# Джамп-кат (вырезанная пауза внутри куска, app/cuts.py): склейка посреди
# речи — фейд короче, чтобы не «проглотить» начало слога, но щелчка нет.
JUMP_DECLICK_SEC = 0.018


def normalize_transition(raw: object) -> dict:
    """Coerce stored/requested settings into a complete, safe dict."""
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_TRANSITION)
    kind = str(src.get("type") or "cut")
    out["type"] = kind if kind in TRANSITIONS else "cut"
    try:
        duration = float(src.get("duration", DEFAULT_TRANSITION["duration"]))
    except (TypeError, ValueError):
        duration = float(DEFAULT_TRANSITION["duration"])
    out["duration"] = min(MAX_DURATION, max(MIN_DURATION, duration))
    if out["type"] in SNAP_DURATIONS:
        out["duration"] = SNAP_DURATIONS[out["type"]]
    out["smart"] = bool(src.get("smart", True))
    audio = str(src.get("audio") or "smooth")
    out["audio"] = audio if audio in AUDIO_MODES else "smooth"
    sfx = str(src.get("sfx") or "none")
    out["sfx"] = sfx if sfx in SFX_KINDS else "none"
    try:
        volume = float(src.get("sfx_volume", DEFAULT_TRANSITION["sfx_volume"]))
    except (TypeError, ValueError):
        volume = float(DEFAULT_TRANSITION["sfx_volume"])
    out["sfx_volume"] = min(1.0, max(0.0, volume))
    return out


def is_plain_concat(transition: dict) -> bool:
    """Hard cut, hard sound, no join sound — the fast stream-copy path works."""
    return transition["type"] == "cut" and transition["audio"] == "hard" and transition["sfx"] == "none"


def effective_duration(transition: dict, durations: list[float]) -> float:
    """Transition length, clamped so it never eats more than 40% of any part."""
    if transition["type"] == "cut" or len(durations) < 2:
        return 0.0
    shortest = min(durations)
    return max(0.0, min(float(transition["duration"]), shortest * 0.4))


def _is_jump(jump_joins: list[bool] | None, index: int) -> bool:
    return bool(jump_joins) and index < len(jump_joins) and bool(jump_joins[index])


def plan_joins(
    transition: dict,
    durations: list[float],
    gaps: list[float | None] | None = None,
    jump_joins: list[bool] | None = None,
) -> list[float]:
    """Overlap for every join: 0 = hard cut, >0 = xfade of that length.

    ``gaps[i]`` — сколько секунд исходника выброшено между частями i и i+1.
    Маленькая дырка значит, что это та же сцена и склейка должна быть встык:
    наплыв посреди фразы выглядит как ошибка монтажа. Дырки нет (None) —
    считаем сменой сцены.

    ``jump_joins[i]`` — склейка i это джамп-кат (вырез внутри одного куска,
    app/cuts.py): всегда встык, что бы ни было в настройках перехода.
    """

    joins = max(0, len(durations) - 1)
    if joins == 0 or transition["type"] == "cut":
        return [0.0] * joins
    smart = bool(transition.get("smart", True))
    out: list[float] = []
    for index in range(joins):
        if _is_jump(jump_joins, index):
            out.append(0.0)
            continue
        gap = None
        if gaps is not None and index < len(gaps):
            gap = gaps[index]
        same_scene = smart and gap is not None and 0 <= gap <= SAME_SCENE_GAP_SEC
        if same_scene:
            out.append(0.0)
            continue
        # Переход не должен съедать больше 40% ни одной из двух соседних частей.
        room = min(durations[index], durations[index + 1]) * 0.4
        out.append(max(0.0, min(float(transition["duration"]), room)))
    return out


def part_overlaps(
    parts: list[dict],
    transition: object,
    jump_joins: list[bool] | None = None,
) -> list[float]:
    """Overlap каждой склейки этих частей — ровно как их спланирует рендер.

    ``parts`` — то, что рендерится подряд (куски клипа или под-куски после
    вырезов, app/cuts.py) с ``start_sec``/``end_sec`` исходника: длины частей
    и дырки в исходнике между ними — те же входы, что у ``render_montage``.
    """
    durations = [max(0.0, float(p["end_sec"]) - float(p["start_sec"])) for p in parts]
    gaps: list[float | None] = []
    for left, right in zip(parts, parts[1:]):
        gap = float(right["start_sec"]) - float(left["end_sec"])
        gaps.append(gap if gap >= 0 else None)
    return plan_joins(normalize_transition(transition), durations, gaps, jump_joins)


SPEECH_MARGIN_SEC = 0.05


def speech_under_joins(segments: list[dict], transition: object, words: list[dict]) -> dict:
    """Наплыв перехода на речь: где склейка уводит слова под переход и какой длины он не мешал бы.

    Агент стримов: fadeblack ложился на последние слова куска — речь звучала на чёрном.
    Наплыв длиной ``o`` занимает последние ``o`` секунд куска A и первые ``o`` куска B
    (картинка и звук перетекают). ``words`` — слова субтитров «кусками подряд».
    Возвращает ``{joins: [{join, overlap, safe, words_a, words_b, extend_a, extend_b}],
    safe_duration}``: ``safe`` — наплыв, который не задевает речь на этом стыке;
    ``extend_a``/``extend_b`` — на сколько продлить конец A / начало B, чтобы речь ушла из-под
    перехода при текущей длине."""
    overlaps = part_overlaps(segments, transition)
    joins = []
    bound = 0.0
    lengths = [max(0.0, float(s["end_sec"]) - float(s["start_sec"])) for s in segments]
    clean = []
    for w in words or []:
        try:
            clean.append((float(w["start"]), float(w["end"]), str(w.get("word") or "").strip()))
        except (KeyError, TypeError, ValueError):
            continue
    for index, overlap in enumerate(overlaps):
        start_a = bound
        bound += lengths[index]
        end_b = bound + lengths[index + 1]
        if overlap <= 0:
            continue
        in_a = [w for w in clean if start_a <= w[0] < bound]
        in_b = [w for w in clean if bound <= w[0] < end_b]
        last_a = max((min(w[1], bound) for w in in_a), default=None)
        first_b = min((w[0] for w in in_b), default=None)
        room_a = bound - last_a if last_a is not None else lengths[index]
        room_b = first_b - bound if first_b is not None else lengths[index + 1]
        safe = max(0.0, min(room_a, room_b) - SPEECH_MARGIN_SEC)
        under_a = [w[2] for w in in_a if w[1] > bound - overlap]
        under_b = [w[2] for w in in_b if w[0] < bound + overlap]
        joins.append({
            "join": index, "overlap": round(overlap, 3), "safe": round(safe, 3),
            "words_a": under_a[-4:], "words_b": under_b[:4],
            "extend_a": round(max(0.0, overlap - room_a + SPEECH_MARGIN_SEC), 2) if under_a else 0.0,
            "extend_b": round(max(0.0, overlap - room_b + SPEECH_MARGIN_SEC), 2) if under_b else 0.0,
        })
    hit = [j for j in joins if j["words_a"] or j["words_b"]]
    return {"joins": joins, "hit": hit,
            "safe_duration": round(min((j["safe"] for j in joins), default=MAX_DURATION), 3)}


def speech_join_warnings(report: dict, transition: object) -> list[str]:
    """Человеческие предупреждения по ``speech_under_joins``."""
    kind = normalize_transition(transition)["type"]
    out = []
    for j in report.get("hit") or []:
        said = " / ".join(x for x in ("«" + " ".join(j["words_a"]) + "»" if j["words_a"] else "",
                                      "«" + " ".join(j["words_b"]) + "»" if j["words_b"] else "") if x)
        fix = []
        if j["safe"] >= MIN_DURATION:
            fix.append(f"короче: duration ≤ {j['safe']:.2f} (или set_transition fit_speech: true)")
        if j["extend_a"]:
            fix.append(f"продли кусок {j['join']} в конце на {j['extend_a']:.2f} с (trim_piece end_sec)")
        if j["extend_b"]:
            fix.append(f"начни кусок {j['join'] + 1} раньше на {j['extend_b']:.2f} с (trim_piece start_sec)")
        out.append(f"переход {kind} {j['overlap']:.2f} с на стыке кусков {j['join']}→{j['join'] + 1} ложится на речь "
                   f"{said} — слова уйдут под переход" + (": " + "; ".join(fix) if fix else ""))
    return out


def join_times(durations: list[float], overlaps: float | list[float]) -> list[float]:
    """Where each join lands on the OUTPUT timeline (middle of the transition)."""
    joins = max(0, len(durations) - 1)
    per_join = [float(overlaps)] * joins if isinstance(overlaps, (int, float)) else list(overlaps)
    times: list[float] = []
    elapsed = 0.0
    eaten = 0.0
    for index in range(joins):
        elapsed += durations[index]
        overlap = per_join[index] if index < len(per_join) else 0.0
        # Каждая предыдущая склейка укоротила таймлайн на свой overlap.
        times.append(elapsed - eaten - overlap / 2)
        eaten += overlap
    return times


# Звук стыка — настоящая запись из пака freesound (tools/fetch_sfx.py).
# Владелец: синтетика (шум + свип) звучала металлически — «убрать нахуй»;
# интерфейсный клик — дёшево, поэтому «щелчок» — механический затвор камеры.
# kind → (файл, громкость, сколько секунд до склейки начинается звук).
JOIN_SFX_FILES: dict[str, tuple[str, float, float]] = {
    "whoosh": ("whoosh-1.ogg", 0.7, 0.22),
    "boom": ("impact-low.ogg", 0.8, 0.03),
    "pop": ("pop.ogg", 0.7, 0.02),
    "click": ("camera-2.ogg", 0.7, 0.0),
}


def _filter_path(path: Path) -> str:
    """Путь для movie/amovie в графе фильтров: прямые слэши, «:» и «'» экранированы."""
    bs = chr(92)  # обратная косая
    text = path.resolve().as_posix()
    return text.replace(bs, bs * 2).replace(":", bs + ":").replace("'", bs + "'")


def _sfx_chain(kind: str, index_label: str) -> tuple[list[str], str, float]:
    """Filter statements making one join sound, its output label and its lead."""
    from app.settings import settings

    name, volume, lead = JOIN_SFX_FILES.get(kind, JOIN_SFX_FILES["click"])
    path = Path(settings.motion_packs_dir) / "freesound" / name
    if not path.exists():
        # Пака нет (локальная разработка без fetch_packs) — тишина, но граф цел.
        return ([f"anullsrc=r=48000:cl=stereo,atrim=0:0.05[{index_label}]"], index_label, 0.0)
    return (
        [f"amovie='{_filter_path(path)}',volume={volume:.2f}[{index_label}]"],
        index_label,
        lead,
    )


def build_join_args(
    parts: list[Path],
    durations: list[float],
    output_path: Path,
    transition: dict,
    *,
    has_audio: bool,
    gaps: list[float | None] | None = None,
    video_codec: str = "libx264",
    video_bitrate: str = "",
    crf: object = None,
    audio_codec: str = "aac",
    audio_bitrate: str = "",
    jump_joins: list[bool] | None = None,
) -> list[str]:
    """ffmpeg args joining ``parts`` with the given transition settings.

    ``jump_joins`` — какие склейки это джамп-каты (app/cuts.py): встык, без
    звука стыка и всегда с коротким фейдом звука против щелчка.
    """
    if len(parts) != len(durations) or not parts:
        raise ValueError("parts and durations must be non-empty and the same length")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    n = len(parts)
    overlaps = plan_joins(transition, durations, gaps, jump_joins)
    xfade_name = TRANSITIONS[transition["type"]][1]

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for part in parts:
        args.extend(["-i", str(part)])

    filters: list[str] = []

    # ---- picture ---------------------------------------------------------
    if xfade_name and n > 1 and any(o > 0 for o in overlaps):
        # Одна шкала времени у всех входов: concat (встык) отдаёт 1/1000000, а
        # сам кусок — 1/15360, и следующий xfade падает «timebase do not match».
        for k in range(n):
            filters.append(f"[{k}:v]settb=AVTB,setpts=PTS-STARTPTS[p{k}]")
        previous = "p0"
        elapsed = durations[0]
        for k in range(1, n):
            overlap = overlaps[k - 1]
            label = "vout" if k == n - 1 else f"v{k}"
            if overlap > 0:
                filters.append(
                    f"[{previous}][p{k}]xfade=transition={xfade_name}:"
                    f"duration={overlap:.3f}:offset={elapsed - overlap:.3f}[{label}]"
                )
                elapsed += durations[k] - overlap
            else:
                filters.append(f"[{previous}][p{k}]concat=n=2:v=1:a=0[{label}]")
                elapsed += durations[k]
            previous = label
    else:
        # Метки времени каждого куска — с нуля: после -ss кусок начинается не с 0,
        # и concat оставлял щели (10 кусков — картинка на 0.16 с длиннее звука).
        for k in range(n):
            filters.append(f"[{k}:v]setpts=PTS-STARTPTS[c{k}]")
        inputs = "".join(f"[c{k}]" for k in range(n))
        filters.append(f"{inputs}concat=n={n}:v=1:a=0[vout]")

    # ---- sound -----------------------------------------------------------
    audio_label = None
    if has_audio:
        smooth = transition["audio"] == "smooth"
        if smooth and any(o > 0 for o in overlaps):
            # Плавно: кроссфейд под переходом, встык — микрофейд от щелчка.
            # Звук каждого куска — ровно длины его картинки (apad+atrim), иначе
            # склейки копят сдвиг звука относительно картинки.
            for k in range(n):
                filters.append(f"[{k}:a]apad,atrim=end={durations[k]:.3f},asetpts=PTS-STARTPTS[n{k}]")
            previous = "n0"
            for k in range(1, n):
                overlap = overlaps[k - 1]
                label = "amain" if k == n - 1 else f"a{k}"
                if overlap > 0:
                    filters.append(f"[{previous}][n{k}]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[{label}]")
                else:
                    fade = JUMP_DECLICK_SEC if _is_jump(jump_joins, k - 1) else DECLICK_SEC
                    filters.append(
                        f"[{previous}]afade=t=out:st={max(0.0, _elapsed(durations, overlaps, k) - fade):.3f}:"
                        f"d={fade}[{label}f]"
                    )
                    filters.append(f"[n{k}]afade=t=in:st=0:d={fade}[{label}i]")
                    filters.append(f"[{label}f][{label}i]concat=n=2:v=0:a=1[{label}]")
                previous = label
        else:
            # Резко: режем звук ровно посередине перехода (или на стыке).
            declick = smooth
            pieces = []
            for k in range(n):
                left = overlaps[k - 1] / 2 if k > 0 else 0.0
                right = overlaps[k] / 2 if k < n - 1 else 0.0
                start, end = left, durations[k] - right
                # apad: звук куска бывает короче картинки — добиваем тишиной до её
                # длины, иначе каждая склейка сдвигала звук раньше картинки.
                chain = f"[{k}:a]apad,atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
                length = max(0.0, end - start)
                jump_in = k > 0 and _is_jump(jump_joins, k - 1)
                jump_out = k < n - 1 and _is_jump(jump_joins, k)
                if k > 0 and (declick or jump_in):
                    fade = JUMP_DECLICK_SEC if jump_in else DECLICK_SEC
                    chain += f",afade=t=in:st=0:d={fade}"
                if k < n - 1 and (declick or jump_out):
                    fade = JUMP_DECLICK_SEC if jump_out else DECLICK_SEC
                    if length > fade:
                        chain += f",afade=t=out:st={length - fade:.3f}:d={fade}"
                pieces.append(f"{chain}[p{k}]")
            filters.extend(pieces)
            filters.append("".join(f"[p{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1[amain]")
        audio_label = "amain"

        sfx_joins = [i for i in range(n - 1) if not _is_jump(jump_joins, i)]
        if transition["sfx"] != "none" and sfx_joins and transition["sfx_volume"] > 0:
            times = join_times(durations, overlaps)
            delayed = []
            for i, t in enumerate(times):
                if i not in sfx_joins:
                    continue  # джамп-кат — не смена сцены, звук стыка не нужен
                chains, label, lead = _sfx_chain(transition["sfx"], f"x{i}")
                filters.extend(chains)
                ms = max(0, int(round((t - lead) * 1000)))
                filters.append(
                    f"[{label}]volume={transition['sfx_volume']:.3f},"
                    f"aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[sd{i}]"
                )
                delayed.append(f"[sd{i}]")
            filters.append(
                "[amain]aformat=sample_rates=48000:channel_layouts=stereo[am];"
                f"[am]{''.join(delayed)}amix=inputs={len(delayed) + 1}:duration=first:normalize=0[aout]"
            )
            audio_label = "aout"

    args.extend(["-filter_complex", ";".join(filters), "-map", "[vout]"])
    if audio_label:
        args.extend(["-map", f"[{audio_label}]", "-c:a", audio_codec or "aac"])
        if audio_bitrate:
            args.extend(["-b:a", audio_bitrate])
    else:
        args.append("-an")
    args.extend(["-c:v", video_codec or "libx264"])
    if video_bitrate:
        args.extend(["-b:v", video_bitrate])
    if crf is not None:
        args.extend(["-crf", str(crf)])
    args.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path)])
    return args


def _elapsed(durations: list[float], overlaps: list[float], upto: int) -> float:
    """Длина выходного таймлайна до начала части ``upto``."""
    return sum(durations[:upto]) - sum(overlaps[:upto])


def transition_options() -> dict:
    """What the UI offers — one source of truth for labels."""
    return {
        "types": [{"key": k, "label": v[0]} for k, v in TRANSITIONS.items()],
        "audio": [{"key": k, "label": v} for k, v in AUDIO_MODES.items()],
        "sfx": [{"key": k, "label": v} for k, v in SFX_KINDS.items()],
        "snap": dict(SNAP_DURATIONS),
        "default": dict(DEFAULT_TRANSITION),
    }
