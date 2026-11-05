/**
 * Музыка клипа в телефоне редактора: без неё превью молчало там, где рендер играет трек.
 * Секунда трека — от времени клипа (как в рендере: start_sec + t), на паузе плеера — тишина.
 * Громкость — настройка трека; подгонку под голос и ducking слышно в «Смотреть монтаж».
 */
import { useEffect, useRef } from "react";

export function useClipMusic(
  videoRef: React.RefObject<HTMLVideoElement>,
  music: { url: string; startSec: number; volume: number } | null,
  clipTime: () => number | null,
): void {
  const clipTimeRef = useRef(clipTime);
  clipTimeRef.current = clipTime;
  const musicRef = useRef(music);
  musicRef.current = music;
  useEffect(() => {
    const el = new Audio();
    el.preload = "auto";
    let raf = 0;
    const tick = () => {
      const m = musicRef.current;
      const v = videoRef.current;
      const t = clipTimeRef.current();
      if (m && v && !v.paused && t != null) {
        if (el.getAttribute("src") !== m.url) el.src = m.url;
        el.volume = Math.min(1, Math.max(0, m.volume)) * (v.muted ? 0 : v.volume);
        const dur = Number.isFinite(el.duration) ? el.duration : 0;
        const want = dur > 0 ? (m.startSec + t) % dur : m.startSec + t;
        if (Math.abs(el.currentTime - want) > 0.3) el.currentTime = want;
        if (el.paused) void el.play().catch(() => undefined);
      } else if (!el.paused) {
        el.pause();
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      el.pause();
      el.removeAttribute("src");
    };
  }, [videoRef]);
}
