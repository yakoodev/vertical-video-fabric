import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { sourcesApi } from "@/api/sources";

// Video cover that, on hover, plays a strip of stop-frames sampled across the
// whole video — auto-advancing slideshow, and scrubbable by horizontal mouse
// position. Frames are fetched lazily on first hover (the backend generates and
// caches them), so idle cards never trigger ffmpeg.
export function StoryboardPreview({
  sourceId,
  type,
  duration,
}: {
  sourceId: number;
  type?: string;
  duration?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const [idx, setIdx] = useState(0);

  const sb = useQuery({
    queryKey: ["storyboard", sourceId],
    queryFn: () => sourcesApi.storyboard(sourceId),
    enabled: hover,
    staleTime: Infinity,
    retry: false,
  });
  const frames = sb.data?.frames ?? [];

  useEffect(() => {
    for (const f of frames) {
      const img = new Image();
      img.src = f;
    }
  }, [frames]);

  // Auto-advance while hovering and not actively scrubbing.
  useEffect(() => {
    if (!hover || scrubbing || frames.length < 2) return;
    const t = window.setInterval(() => setIdx((i) => (i + 1) % frames.length), 550);
    return () => window.clearInterval(t);
  }, [hover, scrubbing, frames.length]);

  const onMove = (e: React.MouseEvent) => {
    if (!frames.length || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const p = (e.clientX - rect.left) / rect.width;
    setScrubbing(true);
    setIdx(Math.max(0, Math.min(frames.length - 1, Math.floor(p * frames.length))));
  };

  const active = hover && frames.length > 0;
  return (
    <div
      ref={ref}
      className="pcard-cover"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => {
        setHover(false);
        setScrubbing(false);
        setIdx(0);
      }}
      onMouseMove={onMove}
    >
      <video src={`/media/sources/${sourceId}#t=1`} preload="metadata" muted playsInline />
      {active ? <img className="pcard-sb" src={frames[idx]} alt="" draggable={false} /> : null}
      {type ? <span className="pcard-type mono">{type}</span> : null}
      {duration ? <span className="pcard-dur mono">{duration}</span> : null}
      {active && frames.length > 1 ? (
        <div className="pcard-scrub">
          <div style={{ width: `${((idx + 1) / frames.length) * 100}%` }} />
        </div>
      ) : null}
    </div>
  );
}
