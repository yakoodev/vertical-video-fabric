"""Субтитры клипа ДО рендера: сгенерировать, прочитать, поправить, сохранить.

Until now subtitles existed only as a side effect of rendering: the render cut
the clip, transcribed its audio and burned the result in one go, so the owner
first saw the text (and its typos) in the finished video. Now a clip plan can
carry its own subtitles:

    clip_plans.subtitles_json = {
      "lines":    [{"start", "end", "text"}],   # what the editor shows and edits
      "words":    [{"word", "start", "end"}],   # karaoke timing
      "language", "provider", "model", "edited"
    }

Times are in CLIP time (0 = first frame of the clip), exactly like the audio the
render burns onto. The render reuses these words instead of transcribing again,
so what you fixed in the editor is what ends up in the video.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord
from app.subtitles.registry import get_subtitle_provider, subtitle_model_for_profile
from app.subtitles.timing import normalize_subtitle_timeline
from app.settings import settings

MAX_LINES = 400


def build_clip_audio_args(source_path: Path, pieces: list[tuple[float, float]], out_path: Path) -> list[str]:
    """One wav with the clip's pieces played back to back — the audio the render burns onto."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, (start, end) in enumerate(pieces):
        parts.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(pieces)))
    graph += f"concat=n={len(pieces)}:v=0:a=1[out]" if len(pieces) > 1 else "anull[out]"
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source_path),
        "-filter_complex", graph,
        "-map", "[out]", "-ac", "1", "-ar", "16000", str(out_path),
    ]


def _words_of_line(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if float(w["start"]) < end - 0.001 and float(w["end"]) > start + 0.001]


def _split_words(text: str, start: float, end: float) -> list[dict]:
    """Spread a line's words over its span (used for lines the owner retyped)."""
    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    if not tokens:
        return []
    total = sum(len(t) for t in tokens) or 1
    span = max(0.12 * len(tokens), end - start)
    out, cursor = [], start
    for token in tokens:
        share = span * (len(token) / total)
        out.append({"word": token, "start": round(cursor, 3), "end": round(cursor + share, 3)})
        cursor += share
    return out


def _same_text(a: str, b: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s or "").strip().lower()  # noqa: E731
    return norm(a) == norm(b)


def rebuild_words(lines: list[dict], old_words: list[dict]) -> list[dict]:
    """Keep the original per-word timing where the text is untouched; re-spread it
    where the owner rewrote the line (so karaoke never breaks on an edit)."""
    words: list[dict] = []
    for line in lines:
        start, end = float(line["start"]), float(line["end"])
        inside = _words_of_line(old_words, start, end)
        if inside and _same_text(" ".join(w["word"] for w in inside), line["text"]):
            words.extend(
                {"word": w["word"], "start": max(start, float(w["start"])), "end": min(end, float(w["end"]))}
                for w in inside
            )
        else:
            words.extend(_split_words(line["text"], start, end))
    return words


def normalize_lines(raw: Any, duration: float | None = None) -> list[dict]:
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, float(item.get("start", 0)))
            end = float(item.get("end", 0))
        except (TypeError, ValueError):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text or end <= start:
            continue
        if duration:
            end = min(end, duration + 0.2)
        out.append({"start": round(start, 3), "end": round(end, 3), "text": text[:300]})
        if len(out) >= MAX_LINES:
            break
    return sorted(out, key=lambda x: x["start"])


def payload_from_result(result: SubtitleResult, provider: str, model: str) -> dict:
    lines = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()} for s in result.segments]
    lines = normalize_lines(lines, result.duration)
    words = [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)} for w in result.words]
    if not lines and words:  # a provider that only returns words
        lines = normalize_lines(
            [{"start": words[0]["start"], "end": words[-1]["end"], "text": " ".join(w["word"] for w in words)}]
        )
    return {
        "lines": lines,
        "words": words or rebuild_words(lines, []),
        "language": result.language,
        "provider": provider,
        "model": model,
        "edited": False,
        "duration": round(result.duration, 3),
    }


def result_from_payload(payload: dict) -> SubtitleResult:
    """Back to what write_ass_subtitles() expects."""
    lines = payload.get("lines") or []
    words = payload.get("words") or []
    return SubtitleResult(
        text=" ".join(line["text"] for line in lines),
        language=str(payload.get("language") or ""),
        duration=float(payload.get("duration") or (lines[-1]["end"] if lines else 0)),
        segments=[SubtitleSegment(start=float(l["start"]), end=float(l["end"]), text=l["text"]) for l in lines],
        words=[SubtitleWord(word=w["word"], start=float(w["start"]), end=float(w["end"])) for w in words],
    )


def shift_for_transition(payload: dict, pieces: list[tuple[float, float]], overlap: float) -> dict:
    """A real transition overlaps neighbours, so every piece after the first
    starts `overlap` earlier in the rendered clip than in the plain concatenation
    the subtitles were timed against."""
    if overlap <= 0 or len(pieces) < 2:
        return payload
    bounds, t = [], 0.0
    for start, end in pieces:
        t += end - start
        bounds.append(t)

    def shift(value: float) -> float:
        k = sum(1 for b in bounds[:-1] if value >= b)
        return max(0.0, value - overlap * k)

    return {
        **payload,
        "lines": [{**l, "start": shift(float(l["start"])), "end": shift(float(l["end"]))} for l in payload.get("lines") or []],
        "words": [{**w, "start": shift(float(w["start"])), "end": shift(float(w["end"]))} for w in payload.get("words") or []],
    }


def generate_for_plan(store: Any, clip_plan_id: int, provider: str | None = None, model: str | None = None) -> dict:
    """Cut the clip's audio, transcribe it, store editable lines on the plan."""
    from app.render import _run_ffmpeg  # local import: render imports this module

    plan = store.get_clip_plan(clip_plan_id)
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("у клипа нет кусков")
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise ValueError("исходник недоступен")

    saved = store.get_clip_plan_subtitles(clip_plan_id) or {}
    settings_profile = (plan.get("render_settings") or {}).get("sub_id")
    profile = store.get_subtitle_profile(settings_profile) if settings_profile else _default_profile(store)
    if provider:
        profile = {**profile, "provider": provider}
    used_model = model or subtitle_model_for_profile(profile)
    engine = get_subtitle_provider(profile.get("provider"))

    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    audio_path = settings.tmp_dir / f"{uuid4().hex}.wav"
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, audio_path), timeout=60 * 10)
        result = engine.transcribe(audio_path, profile, used_model)
        total = sum(e - s for s, e in pieces)
        result = normalize_subtitle_timeline(result, total)
        payload = payload_from_result(result, str(profile.get("provider") or ""), used_model)
        payload["duration"] = round(total, 3)
        if saved.get("lines"):
            payload["previous_lines"] = len(saved["lines"])
        store.set_clip_plan_subtitles(clip_plan_id, payload)
        return payload
    finally:
        audio_path.unlink(missing_ok=True)


def save_lines(store: Any, clip_plan_id: int, lines: Any) -> dict:
    """Store the owner's edited lines (word timing follows the text)."""
    current = store.get_clip_plan_subtitles(clip_plan_id) or {}
    clean = normalize_lines(lines, current.get("duration"))
    if not clean:
        store.set_clip_plan_subtitles(clip_plan_id, None)
        return {"lines": [], "words": [], "edited": True}
    payload = {
        **current,
        "lines": clean,
        "words": rebuild_words(clean, current.get("words") or []),
        "edited": True,
    }
    store.set_clip_plan_subtitles(clip_plan_id, payload)
    return payload


def _default_profile(store: Any) -> dict:
    saved = store.get_app_setting_value("default_subtitle_profile_id")
    try:
        if saved:
            return store.get_subtitle_profile(int(saved))
    except (KeyError, TypeError, ValueError):
        pass
    profiles = store.list_subtitle_profiles()
    if not profiles:
        raise ValueError("нет ни одного стиля субтитров")
    return profiles[0]
