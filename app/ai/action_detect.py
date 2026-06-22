from __future__ import annotations

import subprocess
from pathlib import Path

from app.ai.contracts import AnalysisClip, AnalysisResult, AnalysisSegment
from app.settings import settings


# Low-res grayscale sampling for the motion signal. Small frames keep the decode
# cheap while still capturing per-second movement energy.
_MOTION_W = 64
_MOTION_H = 36
_MOTION_FPS = 2
_AUDIO_SR = 16000


class ActionVideoAnalyzer:
    """Find action scenes from the raw video signal — no LLM, no API tokens.

    Fights, chases and other action share a clear signature: a burst of on-screen
    motion plus loud audio (hits, music, shouting). We sample the whole episode's
    per-second motion (low-res frame differences) and audio loudness, combine them
    into an action score, and return the strongest sustained regions as clips.
    This is deterministic and free, and it localizes action far more reliably than
    a multimodal model skimming one frame per second of a long episode.
    """

    provider = "action"

    def analyze(self, source: dict, prompt: str, model: str) -> AnalysisResult:
        path = Path(source.get("local_path") or "")
        duration = float(source.get("duration_sec") or 0)
        if not path.exists():
            raise RuntimeError("source media not found for action detection")
        regions = detect_action_regions(
            path,
            duration,
            max_clips=int(settings.action_max_clips),
            min_len=float(settings.action_min_clip_seconds),
            max_len=float(settings.action_max_clip_seconds),
            lead=float(settings.action_lead_seconds),
            tail=float(settings.action_tail_seconds),
            merge_gap=float(settings.action_merge_gap_seconds),
        )
        if not regions:
            raise RuntimeError("no action regions detected in the source")
        clips: list[AnalysisClip] = []
        for index, (score, start, end) in enumerate(regions, start=1):
            stamp = f"{int(start // 60)}:{int(start % 60):02d}"
            segment = AnalysisSegment(
                start_sec=round(start, 1),
                end_sec=round(end, 1),
                title=f"Экшн-сцена {index} ({stamp})",
                description=f"Высокая динамика (движение и звук) около {stamp}.",
                score=round(min(1.0, max(0.0, score)), 3),
                category="Экшн",
                color="#ef4444",
                reason="Пик движения в кадре и громкости звука — вероятная экшн/боевая сцена.",
            )
            clips.append(
                AnalysisClip(
                    title=segment.title,
                    description=segment.description,
                    score=segment.score,
                    category="Экшн",
                    color="#ef4444",
                    segments=[segment],
                )
            )
        segments = [clip.segments[0] for clip in clips]
        return AnalysisResult(
            segments=segments,
            clips=clips,
            response={"provider": "action", "regions": len(regions)},
            usage={"provider": "action", "model": "motion+audio", "actionRegions": len(regions)},
        )


def detect_action_regions(
    path: Path,
    duration: float,
    *,
    max_clips: int = 18,
    min_len: float = 20.0,
    max_len: float = 60.0,
    lead: float = 4.0,
    tail: float = 3.0,
    merge_gap: float = 12.0,
) -> list[tuple[float, float, float]]:
    """Return ``[(score, start_sec, end_sec), ...]`` for the strongest action regions.

    Each region is padded with a lead-in/tail so the clip carries the build-up and
    aftermath (not just the peak), nearby bursts within ``merge_gap`` are stitched
    into one fuller scene, and only genuinely long scenes are split into <= max_len
    chunks.
    """

    import math

    import numpy as np

    raw = _run_ffmpeg(
        ["-i", str(path), "-vf", f"fps={_MOTION_FPS},scale={_MOTION_W}:{_MOTION_H},format=gray", "-f", "rawvideo", "-"]
    )
    pixels = _MOTION_W * _MOTION_H
    frame_count = len(raw) // pixels
    if frame_count < 4:
        return []
    frames = np.frombuffer(raw, dtype=np.uint8)[: frame_count * pixels].reshape(frame_count, pixels).astype(np.float32)
    motion = np.abs(np.diff(frames, axis=0)).mean(axis=1)

    wav = _run_ffmpeg(["-i", str(path), "-ac", "1", "-ar", str(_AUDIO_SR), "-f", "s16le", "-"])
    audio = np.frombuffer(wav, dtype=np.int16).astype(np.float32) / 32768.0
    hop = _AUDIO_SR // _MOTION_FPS
    audio_windows = len(audio) // hop
    if audio_windows:
        loudness = np.sqrt((audio[: audio_windows * hop].reshape(audio_windows, hop) ** 2).mean(axis=1))
    else:
        loudness = np.zeros(len(motion), dtype=np.float32)

    length = min(len(motion), len(loudness)) or len(motion)
    motion = motion[:length]
    loudness = loudness[:length] if audio_windows else np.zeros(length, dtype=np.float32)
    times = np.arange(length) / _MOTION_FPS

    def _norm(values):
        lo, hi = np.percentile(values, 10), np.percentile(values, 97)
        return np.clip((values - lo) / (hi - lo + 1e-9), 0, 1)

    score = 0.62 * _norm(motion) + 0.38 * _norm(loudness)
    smooth = np.convolve(score, np.ones(6) / 6, mode="same")  # ~3s smoothing

    threshold = np.percentile(smooth, 78)
    hot = np.where(smooth > threshold)[0]
    bridge = max(1, int(merge_gap * _MOTION_FPS))
    raw_regions: list[tuple[int, int]] = []
    if len(hot):
        start = prev = int(hot[0])
        for index in hot[1:]:
            index = int(index)
            if index - prev <= bridge:  # stitch nearby bursts into one scene
                prev = index
            else:
                raw_regions.append((start, prev))
                start = prev = index
        raw_regions.append((start, prev))

    cap = duration if duration > 0 else (float(times[-1]) if length else 0.0)
    # Pad each region with lead-in/tail, then merge overlaps into fuller scenes.
    intervals: list[list[float]] = []
    for start_idx, end_idx in raw_regions:
        clip_start = max(0.0, float(times[start_idx]) - lead)
        clip_end = min(cap, float(times[min(end_idx, length - 1)]) + tail)
        if intervals and clip_start <= intervals[-1][1] + 1.0:
            intervals[-1][1] = max(intervals[-1][1], clip_end)
        else:
            intervals.append([clip_start, clip_end])

    candidates: list[tuple[float, float, float]] = []
    for clip_start, clip_end in intervals:
        span = clip_end - clip_start
        if span < min_len:
            center = (clip_start + clip_end) / 2
            clip_start = max(0.0, center - min_len / 2)
            clip_end = min(cap, clip_start + min_len)
            span = clip_end - clip_start
            if span < min_len * 0.6:
                continue
        # Keep a scene whole up to max_len; split only genuinely long sequences.
        chunks = max(1, math.ceil(span / max_len))
        step = span / chunks
        for chunk_index in range(chunks):
            cs = clip_start + chunk_index * step
            ce = min(clip_end, cs + step)
            if ce - cs < min_len * 0.6:
                continue
            lo, hi = int(cs * _MOTION_FPS), int(ce * _MOTION_FPS)
            clip_score = float(smooth[lo : hi + 1].mean()) if hi > lo else 0.0
            candidates.append((clip_score, round(cs, 1), round(ce, 1)))

    candidates.sort(reverse=True)
    return candidates[:max_clips]


def _run_ffmpeg(args: list[str]) -> bytes:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", *args],
        capture_output=True,
        timeout=60 * 20,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed during action detection: {proc.stderr.decode('utf-8', 'ignore')[:300]}")
    return proc.stdout
