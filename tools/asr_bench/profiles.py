"""Профили мест вызова Whisper: те же параметры и те же фильтры, что в коде сервиса и скриптов.

Профиль — функция ``(model_factory, path) -> {text, words, segments}``; модель грузит стенд (CPU int8 как в
контейнере или CUDA для скорости). Провайдер сервиса зовётся настоящим кодом (``WhisperSubtitleProvider``),
сырые скрипты — их собственными аргументами ``transcribe``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

TURBO = "large-v3-turbo"
SMALL = "small"


def _words_of(result: Any) -> dict:
    words = [{"word": w.word, "start": float(w.start), "end": float(w.end)} for w in result.words]
    return {"text": result.text or "", "words": words,
            "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in result.segments]}


def _provider(profile: dict, size: str, post: Callable[[Any, Path], Any] | None = None):
    def run(factory: Callable[[str], Any], path: Path) -> dict:
        from app.subtitles.whisper_local import WhisperSubtitleProvider

        try:
            result = WhisperSubtitleProvider(model_loader=factory)._transcribe(path, dict(profile), size)  # noqa: SLF001
        except RuntimeError as exc:
            if "no word-level timestamps" in str(exc):
                return {"text": "", "words": [], "segments": []}
            raise
        if post is not None:
            result = post(result, path)
        return _words_of(result)

    run.size = size  # type: ignore[attr-defined]
    return run


def _clip_subtitles_post(result: Any, path: Path) -> Any:
    """app/clip_subtitles.py: после распознавания — drop_phantoms по дорожке речи."""
    from app.speech_onsets import wav_pcm
    from app.subtitles.phantoms import drop_phantoms

    return drop_phantoms(result, wav_pcm(path))[0]


def _raw(kwargs: dict, size: str, post: Callable[[list[dict], Path], list[dict]] | None = None):
    """Сырые ``model.transcribe(...)`` скриптов (без фильтров провайдера)."""

    def run(factory: Callable[[str], Any], path: Path) -> dict:
        model = factory(size)
        segs, _info = model.transcribe(str(path), word_timestamps=True, **kwargs)
        segs = list(segs)
        words = [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end),
                  "p": float(getattr(w, "probability", 0.0) or 0.0)}
                 for s in segs for w in (s.words or []) if w.word.strip()]
        if post is not None:
            words = post(words, path)
        return {"text": " ".join(w["word"] for w in words), "words": words,
                "segments": [{"start": s.start, "end": s.end, "text": s.text.strip(), "ns": round(s.no_speech_prob, 3),
                              "lp": round(s.avg_logprob, 3)} for s in segs]}

    run.size = size  # type: ignore[attr-defined]
    return run


def _lyrics_post(words: list[dict], path: Path) -> list[dict]:
    """app/lyrics_audio.py: защита распознанных слов песни (если в коде есть)."""
    try:
        from app.lyrics_audio import guard_words
    except ImportError:
        return words
    kept = guard_words([{"w": w["word"], "s": w["start"], "e": w["end"], "p": w.get("p", 1.0)} for w in words], path)
    return [{"word": w["w"], "start": w["s"], "end": w["e"], "p": w["p"]} for w in kept]


# Параметры lyrics_audio.transcribe (без initial_prompt: текста песни на стенде нет).
LYRICS_KW = dict(language="ru", vad_filter=False, beam_size=5, temperature=0.0, condition_on_previous_text=False,
                 no_speech_threshold=0.6, compression_ratio_threshold=2.4)

def _on_stem(profile):
    """Тот же профиль, но на вокальном стеме demucs (``<стенд>/stems/<набор>__<id>.wav``), как lyrics_audio в сервисе:
    Whisper слушает стем, а не смесь. Стема нет — запись пропускается (пустой ответ помечен ``skipped``)."""

    def run(factory: Callable[[str], Any], path: Path) -> dict:
        stem = path.parents[1] / "stems" / f"{path.parent.name}__{path.stem}.wav"
        if not stem.is_file():
            return {"text": "", "words": [], "segments": [], "skipped": True}
        return profile(factory, stem)

    run.size = getattr(profile, "size", TURBO)  # type: ignore[attr-defined]
    return run


PROFILES: dict[str, Any] = {
    # explainer_gen, voice_asr (voice_check, voiceover), s3d_voice, stories_gen, meme_edit_inputs, censor,
    # tools/viewer_ep, tools/vtubika_story — провайдер, VAD включён, русский, turbo.
    "provider_ru": _provider({"language": "ru"}, TURBO),
    # render_subs / clip_subtitles с моделью профиля по умолчанию (WHISPER_MODEL_SIZE=small).
    "provider_small_ru": _provider({"language": "ru"}, SMALL),
    # clip_subtitles: провайдер + drop_phantoms по дорожке речи.
    "clip_subtitles": _provider({"language": "ru"}, TURBO, _clip_subtitles_post),
    # speech_heard: без VAD, язык из субтитров (русский).
    "speech_heard": _provider({"language": "ru", "vad_filter": False}, TURBO),
    # video_meme_ingest: без VAD, язык сам.
    "meme_ingest": _provider({"language": "", "vad_filter": False}, TURBO),
    # lyrics_audio.transcribe (песни): сырой вызов без VAD.
    "lyrics": _raw(LYRICS_KW, TURBO, _lyrics_post),
    # Песни по-настоящему: стем demucs htdemucs (наборы nospeech и music; demucs из окружения голоса).
    "lyrics_stem": _on_stem(_raw(LYRICS_KW, TURBO, _lyrics_post)),
    # tools/meme3d_voice.words: turbo, без VAD, остальное по умолчанию faster-whisper.
    "meme3d_voice": _raw(dict(language="ru", vad_filter=False, beam_size=5), TURBO),
    "meme3d_voice_new": _raw(dict(language="ru", vad_filter=True, beam_size=5, condition_on_previous_text=False,
                                  vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200}), TURBO),
    # tools/viewer_ep2.asr_words: small, всё по умолчанию.
    "viewer_ep2": _raw(dict(language="ru", beam_size=5), SMALL),
    "viewer_ep2_new": _raw(dict(language="ru", beam_size=5, vad_filter=True, condition_on_previous_text=False,
                                vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200}), SMALL),
    # Как в ролике: turbo «из коробки».
    "vanilla_turbo": _raw(dict(language="ru", beam_size=5), TURBO),
    # Диагностика для подбора правил: всё, что декодер выдал, без порогов, с no_speech / logprob сегмента.
    "diag_novad": _raw(dict(language="ru", vad_filter=False, beam_size=5, temperature=0.0,
                            condition_on_previous_text=False, no_speech_threshold=None, log_prob_threshold=None,
                            compression_ratio_threshold=None), TURBO),
    "diag_novad_small": _raw(dict(language="ru", vad_filter=False, beam_size=5, temperature=0.0,
                                  condition_on_previous_text=False, no_speech_threshold=None, log_prob_threshold=None,
                                  compression_ratio_threshold=None), SMALL),
}
