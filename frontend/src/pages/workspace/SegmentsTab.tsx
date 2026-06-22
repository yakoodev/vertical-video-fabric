import { useQuery } from "@tanstack/react-query";
import { sourcesApi } from "@/api/sources";
import { qk } from "@/api/keys";
import { EmptyState, ErrorState, Loading, formatDuration } from "@/components/ui";

export function SegmentsTab({ sourceId }: { sourceId: string }) {
  const query = useQuery({ queryKey: qk.source(sourceId), queryFn: () => sourcesApi.get(sourceId) });
  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const segments = query.data!.segments;

  if (!segments.length) {
    return <EmptyState icon="🧠" title="Сегментов нет" hint="Запустите анализ на вкладке «Исходник»" />;
  }

  return (
    <div style={{ display: "grid", gap: 8 }}>
      {segments.map((s) => (
        <div key={s.id} className="ws-row" style={{ borderLeft: `3px solid ${s.color || "var(--accent)"}` }}>
          <span className="mono" style={{ color: "var(--text-dim)", fontSize: 12.5, minWidth: 96 }}>
            {formatDuration(s.start_sec)} – {formatDuration(s.end_sec)}
          </span>
          <span style={{ flex: 1, fontWeight: 600 }}>{s.title || "—"}</span>
          {s.category ? <span className="badge">{s.category}</span> : null}
          {s.score ? <span className="muted" style={{ fontSize: 12 }}>★ {s.score.toFixed(2)}</span> : null}
        </div>
      ))}
    </div>
  );
}
