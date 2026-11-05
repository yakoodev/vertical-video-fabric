import { useState } from "react";

/** Видео в списке: картинка, а плеер — только по нажатию. Каждый <video preload> занимал соединение браузера. */
export function VideoThumb({ src, thumb, label = "Смотреть" }: { src: string; thumb: string; label?: string }) {
  const [playing, setPlaying] = useState(false);
  const [noThumb, setNoThumb] = useState(false);
  if (playing) return <video src={src} poster={noThumb ? undefined : thumb} controls autoPlay playsInline />;
  return (
    <button type="button" className="vthumb" aria-label={label} onClick={() => setPlaying(true)}>
      {noThumb ? null : <img src={thumb} alt="" loading="lazy" decoding="async" onError={() => setNoThumb(true)} />}
      <span className="vthumb-play" aria-hidden="true">
        ▶
      </span>
    </button>
  );
}
