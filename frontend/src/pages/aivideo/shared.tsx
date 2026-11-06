// ИИ-видео: общее для подвкладок. Подписи денег и ошибок, обновление данных, выбор серии, кадр.
import { createContext, useContext, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { aivApi, aivKey, aivScriptKey, type AivRef, type Overview } from "@/api/aivideo";
import { ApiError } from "@/api/client";
import { navKeys } from "@/api/nav";
import { releaseKeys } from "@/api/releases";

export const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
export const rub = (v: number | null | undefined) => `${(v ?? 0).toFixed(v && v < 10 ? 2 : 0).replace(".", ",")} ₽`;

export function useInvalidate(projectId: number) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: aivKey(projectId) });
    qc.invalidateQueries({ queryKey: ["ai-video", "script"] });
    qc.invalidateQueries({ queryKey: releaseKeys.all });
    qc.invalidateQueries({ queryKey: navKeys.all });
  };
}

/** Страница выпуска: все подвкладки работают с этой серией, выбора серии нет. */
export const FixedScript = createContext<number | null>(null);

export function useEpisode(o: Overview) {
  const fixed = useContext(FixedScript);
  const [picked, setId] = useState<number | null>(o.scripts[0]?.id ?? null);
  const id = fixed ?? picked;
  useEffect(() => {
    if (picked == null && o.scripts.length) setId(o.scripts[0].id);
  }, [o.scripts, picked]);
  const q = useQuery({ queryKey: aivScriptKey(id ?? 0), queryFn: () => aivApi.script(id as number), enabled: id != null });
  return { id, setId, script: q.data, q };
}

export function EpisodePicker({ o, id, setId }: { o: Overview; id: number | null; setId: (v: number) => void }) {
  const fixed = useContext(FixedScript);
  if (fixed != null) return null;
  if (!o.scripts.length) return <span className="muted">Серий пока нет. Новая серия: «Новое видео» → ИИ-видео.</span>;
  return (
    <select className="input" value={id ?? ""} onChange={(e) => setId(Number(e.target.value))}>
      {o.scripts.map((s) => (
        <option key={s.id} value={s.id}>
          Серия {s.episode_no}: {s.title} ({s.shots_count ?? 0} планов)
        </option>
      ))}
    </select>
  );
}

// --------------------------------------------------------------------------- //
// Раскадровка
// --------------------------------------------------------------------------- //

export function Frame({ ref_, label, empty, badge }: { ref_?: AivRef | null; label: string; empty: string; badge?: string }) {
  return (
    <figure className="aiv-frame">
      <div className="aiv-shot-img">
        {ref_?.url ? <img src={ref_.url} alt={label} loading="lazy" /> : <span className="muted">{empty}</span>}
        {ref_?.stale ? <span className="aiv-badge">устарело</span> : null}
        {badge ? <span className="aiv-badge review">{badge}</span> : null}
      </div>
      <figcaption>{label}</figcaption>
    </figure>
  );
}
