"""Текст песни «в мире игры»: строки lyric_layers словом на поверхности, где трек держится (app/world_track.py).

Для каждой поставленной строки (её время — секунды готового ролика → шкала clip): кадры основы клипа под строкой,
место у центра (``suggest_in_frames``: ровная фактурная плоскость под текст строки), трек плоскости от кадра чуть
после начала строки. Трек держится в окне строки не меньше ``world_track.SURFACE_OK`` (и строка в одном куске) —
слои строки на экране заменяются одним ``tracked_text`` (слова — по слогам вокала); иначе строка остаётся на экране,
как была. Дорого (секунды на строку) — не больше ``max_lines`` строк за вызов, остальные — на экране.
"""

from __future__ import annotations

from typing import Any, Callable

from app.lyrics_layers import tracked_lyric_layer

DEFAULT_MAX_LINES = 12
SEED_AFTER_SEC = 0.25


def held_share(samples: list[dict], a: float, b: float) -> float:
    inside = [s for s in samples if a - 1e-6 <= float(s["t"]) <= b + 1e-6]
    if not inside:
        return 0.0
    return sum(1 for s in inside if s.get("s") in ("ok", "weak")) / len(inside)


def place_on_surfaces(store: Any, plan_id: int, built: dict, to_clip: Callable[[float], float], *,
                      max_lines: int = DEFAULT_MAX_LINES, style: dict | None = None, min_held: float | None = None,
                      frames_for: Callable[[float, float], list[dict]] | None = None) -> dict:
    """``built`` (build_lyric_layers, время слоёв уже шкала clip) → ``{layers, world, screen}``: слои с заменой строк
    на ``tracked_text`` там, где поверхность держится; ``world`` — строки на поверхности (track_id, held),
    ``screen`` — оставшиеся на экране с причиной. ``frames_for(a, b)`` — подмена кадров (тесты)."""
    from app import world_track as WT

    need = WT.SURFACE_OK if min_held is None else float(min_held)
    layers = list(built.get("layers") or [])
    world: list[dict] = []
    screen: list[dict] = []

    def frames(a: float, b: float) -> list[dict]:
        if frames_for is not None:
            return frames_for(a, b)
        return WT.clip_frames(store, plan_id, a, b, WT.DEFAULT_FPS)[0]

    for n, placed in enumerate(built.get("placed") or []):
        if n >= max_lines:
            screen.append({"i": placed["i"], "why": f"больше {max_lines} строк за вызов — на экране (only: [...] и снова)"})
            continue
        a, b = to_clip(float(placed["start"])), to_clip(float(placed["end"]))
        seed = min(b, a + SEED_AFTER_SEC)
        try:
            fr = frames(max(0.0, a - 0.15), b + 0.1)
            text = "\n".join(placed.get("lines") or [placed["text"]])
            cands = WT.suggest_in_frames(fr, seed, text, n=1, width=0.62)
            if not cands:
                screen.append({"i": placed["i"], "why": "ровной фактурной поверхности у центра нет"})
                continue
            res = WT.compute_surface(fr, seed, cands[0]["quad"])
        except (WT.TrackError, ValueError) as exc:
            screen.append({"i": placed["i"], "why": f"трек не построен: {str(exc)[:120]}"})
            continue
        held = held_share(res["samples"], a, b)
        if held < need:
            screen.append({"i": placed["i"], "why": f"поверхность держится {held:.0%} строки (< {need:.0%})"})
            continue
        res.update(plan_id=plan_id, fps=WT.DEFAULT_FPS, seed={"t": round(seed, 3), "quad": cands[0]["quad"]})
        track_id = WT.save_track(res)
        ids = set(placed.get("layer_ids") or [])
        word_times = [to_clip(float(w["t"])) for w in placed.get("words") or []]
        keep_track = next((int(l.get("track") or 6) for l in layers if l.get("id") in ids), 6)
        layers = [l for l in layers if l.get("id") not in ids]
        layers.append(tracked_lyric_layer(placed, track_id, start=a, end=b, word_times=word_times, style=style,
                                          track=keep_track))
        world.append({"i": placed["i"], "track_id": track_id, "held": round(held, 2), "quad": cands[0]["quad"]})
    return {"layers": layers, "world": world, "screen": screen}


__all__ = ["place_on_surfaces", "held_share"]
