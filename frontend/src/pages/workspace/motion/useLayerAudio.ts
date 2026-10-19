/**
 * Звуки-слои в превью: whoosh и boom должны звучать, пока смотришь клип, иначе
 * их не подогнать к движению. Каждый кадр анимации сверяем клиповое время с
 * отрезками звуковых слоёв: вошли в отрезок — играем с нужного места, вышли или
 * превью на паузе — глушим.
 */
import { useEffect, useRef } from "react";
import type { Layer } from "@/motion";
import { packFileUrl } from "@/api/motion";

/** URL звука слоя: файл пака (`sfx:<пак>/<файл>`) или файл из «Файлов для монтажа». */
export function audioUrl(ref: string): string {
  if (ref.startsWith("sfx:")) {
    const rest = ref.slice(4);
    const slash = rest.indexOf("/");
    return slash > 0 ? packFileUrl(rest.slice(0, slash), rest.slice(slash + 1)) : `/api/motion/packs/${encodeURIComponent(rest)}`;
  }
  return `/media/montage-assets/${encodeURIComponent(ref)}`;
}

export function useLayerAudio(
  videoRef: React.RefObject<HTMLVideoElement>,
  layersRef: React.MutableRefObject<{ layers: Layer[] }>,
  clipTime: () => number | null,
) {
  const clipTimeRef = useRef(clipTime);
  clipTimeRef.current = clipTime;
  useEffect(() => {
    const players = new Map<string, HTMLAudioElement>();
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      const t = clipTimeRef.current();
      const playing = Boolean(v && !v.paused && t != null);
      const alive = new Set<string>();
      for (const layer of layersRef.current.layers) {
        if (layer.type !== "audio" || layer.hidden || !layer.props.asset_id) continue;
        alive.add(layer.id);
        let a = players.get(layer.id);
        const inside = playing && t != null && t >= layer.start && t < layer.end;
        if (!inside) {
          if (a && !a.paused) a.pause();
          continue;
        }
        const url = audioUrl(String(layer.props.asset_id));
        if (!a || a.dataset.src !== url) {
          a?.pause();
          a = new Audio(url);
          a.dataset.src = url;
          a.preload = "auto";
          players.set(layer.id, a);
        }
        a.volume = Math.min(1, Math.max(0, layer.props.volume ?? 1));
        const want = (layer.props.offset ?? 0) + (t! - layer.start);
        if (a.paused) {
          a.currentTime = want;
          void a.play().catch(() => undefined);
        } else if (Math.abs(a.currentTime - want) > 0.25) {
          // Перемотали превью — догоняем, а не играем со старого места.
          a.currentTime = want;
        }
      }
      for (const [id, a] of players) {
        if (!alive.has(id)) {
          a.pause();
          players.delete(id);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      for (const a of players.values()) a.pause();
    };
  }, [videoRef, layersRef]);
}
