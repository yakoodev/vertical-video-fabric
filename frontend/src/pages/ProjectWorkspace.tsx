import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useParams, Link } from "react-router-dom";
import { sourcesApi } from "@/api/sources";
import { qk } from "@/api/keys";
import { Badge, ErrorState, Loading, formatDuration } from "@/components/ui";
import { SourceTab } from "@/pages/workspace/SourceTab";
import { SegmentsTab } from "@/pages/workspace/SegmentsTab";
import { CandidatesTab } from "@/pages/workspace/CandidatesTab";
import { ClipsTab } from "@/pages/workspace/ClipsTab";
import { MontagedTab } from "@/pages/workspace/MontagedTab";

const TABS = [
  { seg: "source", label: "Исходник" },
  { seg: "candidates", label: "Моменты" },
  { seg: "clips", label: "Клипы" },
  { seg: "montaged", label: "Смонтированные" },
];

export function ProjectWorkspace() {
  const { sourceId = "" } = useParams();
  const query = useQuery({
    queryKey: qk.source(sourceId),
    queryFn: () => sourcesApi.get(sourceId),
    enabled: Boolean(sourceId),
  });

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const source = query.data!;
  const tabCount = (seg: string): number =>
    seg === "candidates"
      ? source.clip_plans.filter((p) => !p.hidden).length
      : seg === "clips"
        ? source.clips_count ?? source.clips.length
        : 0;

  return (
    <>
      <div className="ws-head">
        <Link to="/projects" className="ws-back">
          ← Все проекты
        </Link>
        <h1 className="ws-title">{source.original_filename || source.original_url || `Проект #${source.id}`}</h1>
        <div className="ws-pills">
          <Badge status={source.status} />
          <span className="ws-pill mono">⏱ {formatDuration(source.duration_sec)}</span>
          {source.width ? <span className="ws-pill mono">▭ {source.width}×{source.height}</span> : null}
        </div>
        <nav className="pipeline">
          {TABS.map((tab, i) => {
            const n = tabCount(tab.seg);
            return (
              <NavLink
                key={tab.seg}
                to={`/projects/${sourceId}/${tab.seg}`}
                className={({ isActive }) => `tab${isActive ? " active" : ""}`}
              >
                <span className="tab-no">{String(i + 1).padStart(2, "0")}</span>
                <span>{tab.label}</span>
                {n ? <span className="tab-count">· {n}</span> : null}
              </NavLink>
            );
          })}
        </nav>
      </div>

      <Routes>
        <Route index element={<Navigate to="source" replace />} />
        <Route path="source" element={<SourceTab sourceId={sourceId} />} />
        <Route path="segments" element={<SegmentsTab sourceId={sourceId} />} />
        <Route path="candidates" element={<CandidatesTab sourceId={sourceId} />} />
        <Route path="clips" element={<ClipsTab sourceId={sourceId} />} />
        <Route path="montaged" element={<MontagedTab sourceId={sourceId} />} />
        <Route path="*" element={<Navigate to="source" replace />} />
      </Routes>
    </>
  );
}
