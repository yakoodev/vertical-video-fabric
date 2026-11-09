import { useState } from "react";
import { formatSnapshotTime } from "@/api/stats";
import { secText, sharePct, type StudioBrief } from "@/api/studio";

/** Числа TikTok Studio публикации одной строкой + маленькая кривая удержания первых секунд. */

const W = 96;
const H = 24;
const PAD = 2;

/** Кривая удержания: сколько зрителей ещё смотрят на каждой секунде (0–15 с). Наведение — точное число. */
export function RetentionSpark({ points }: { points: [number, number][] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return null;
  const maxSec = points[points.length - 1][0] || 1;
  const x = (sec: number) => PAD + (sec / maxSec) * (W - PAD * 2);
  const y = (share: number) => PAD + (1 - share) * (H - PAD * 2);
  const d = points.map(([s, v], i) => `${i ? "L" : "M"}${x(s).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const half = y(0.5);
  const hp = hover == null ? null : points[hover];
  const label = hp ? `${hp[0]} с — ${sharePct(hp[1])}` : "удержание, 0–15 с";
  return (
    <span className="studio-spark">
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Удержание: ${points.map(([s, v]) => `${s} с ${sharePct(v)}`).join(", ")}`}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const sec = ((e.clientX - box.left) / box.width) * maxSec;
          let best = 0;
          points.forEach(([s], i) => {
            if (Math.abs(s - sec) < Math.abs(points[best][0] - sec)) best = i;
          });
          setHover(best);
        }}
        onMouseLeave={() => setHover(null)}
      >
        <line className="studio-spark-half" x1={PAD} x2={W - PAD} y1={half} y2={half} />
        <path className="studio-spark-line" d={d} />
        {hp ? <circle className="studio-spark-dot" cx={x(hp[0])} cy={y(hp[1])} r={2.5} /> : null}
      </svg>
      <span className="muted studio-spark-label">{label}</span>
    </span>
  );
}

export function StudioLine({ studio, compact = false }: { studio: StudioBrief | null | undefined; compact?: boolean }) {
  if (!studio) return null;
  if (!studio.captured_at) {
    return studio.error ? <div className="studio-line muted" title={studio.error}>TikTok Studio: {studio.error}</div> : null;
  }
  const kept3 = studio.hook?.["3"];
  const facts: string[] = [];
  if (studio.avg_watch_sec != null) {
    facts.push(`смотрят ${secText(studio.avg_watch_sec)}${studio.duration_sec ? ` из ${secText(studio.duration_sec)}` : ""}`);
  }
  if (studio.finish_rate != null) facts.push(`досматривают ${sharePct(studio.finish_rate)}`);
  if (studio.half_gone_sec != null) facts.push(`половина ушла к ${secText(studio.half_gone_sec)}`);
  else if (kept3 != null) facts.push(`к 3-й с осталось ${sharePct(kept3)}`);
  if (!compact && studio.main_source) facts.push(`${studio.main_source.label} ${sharePct(studio.main_source.share)}`);
  if (!compact && studio.new_followers) facts.push(`+${studio.new_followers} подписч.`);
  return (
    <div className="studio-line" title={`${studio.text ?? ""}\nTikTok Studio, снято ${formatSnapshotTime(studio.captured_at)}`}>
      {studio.retention?.length ? <RetentionSpark points={studio.retention} /> : null}
      <span className="studio-facts">{facts.join(" · ")}</span>
    </div>
  );
}
