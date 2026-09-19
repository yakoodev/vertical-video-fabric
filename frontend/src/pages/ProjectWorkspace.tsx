import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useLocation, useParams, useSearchParams, Link } from "react-router-dom";
import { sourcesApi } from "@/api/sources";
import { qk } from "@/api/keys";
import { Badge, ErrorState, Loading, formatDuration } from "@/components/ui";
import { SourceTab } from "@/pages/workspace/SourceTab";
import { SegmentsTab } from "@/pages/workspace/SegmentsTab";
import { CandidatesTab } from "@/pages/workspace/CandidatesTab";
import { ClipsTab } from "@/pages/workspace/ClipsTab";
import { MontagedTab } from "@/pages/workspace/MontagedTab";

// "Монтаж" is not a route of its own: it opens the clip editor inside the
// moments tab (?clip=<id>), so it shares the moments' selection and settings.
const TABS = [
  { seg: "source", label: "Исходник" },
  { seg: "candidates", label: "Моменты" },
  { seg: "edit", label: "Монтаж" },
  { seg: "clips", label: "Клипы" },
  { seg: "montaged", label: "Смонтированные" },
];

export function ProjectWorkspace() {
  const { sourceId = "" } = useParams();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  // In the clip editor the project chrome gets out of the way: the editor shows
  // only its own «← Назад к моментам» bar.
  const editing = location.pathname.endsWith("/candidates") && searchParams.has("clip");
  const query = useQuery({
    queryKey: qk.source(sourceId),
    queryFn: () => sourcesApi.get(sourceId),
    enabled: Boolean(sourceId),
  });

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const source = query.data!;
  const visible = source.clip_plans.filter((p) => !p.hidden);
  const favorites = visible.filter((p) => p.favorite);
  const editTarget = favorites[0] ?? visible[0];
  const tabCount = (seg: string): number =>
    seg === "candidates"
      ? visible.length
      : seg === "edit"
        ? favorites.length
        : seg === "clips"
          ? source.clips_count ?? source.clips.length
          : 0;

  return (
    <>
      {editing ? null : (
      <div className="ws-head">
        <Link to="/projects" className="ws-back">
          ← Все проекты
        </Link>
        <h1 className="ws-title">{source.original_filename || source.original_url || `Проект #${source.id}`}</h1>
        <div className="ws-pills">
          <Badge status={source.status} />
          <span className="ws-pill mono">⏱ {formatDuration(source.duration_sec)}</span>
          {source.width ? <span className="ws-pill mono">▭ {source.width}×{source.height}</span> : null}
          <span className="ws-sum">
            <b>{visible.length}</b> моментов · <b>{favorites.length}</b> ★ ·{" "}
            <b>{source.clips_count ?? source.clips.length}</b> клипов
          </span>
        </div>
        <nav className="pipeline">
          {TABS.map((tab, i) => {
            const n = tabCount(tab.seg);
            if (tab.seg === "edit") {
              return editTarget ? (
                <Link
                  key={tab.seg}
                  to={`/projects/${sourceId}/candidates?clip=${editTarget.id}`}
                  className="tab"
                  title={favorites.length ? "Монтаж избранных моментов" : "Отметьте ★ моменты — здесь будут они"}
                >
                  <span className="tab-no">{String(i + 1).padStart(2, "0")}</span>
                  <span>{tab.label}</span>
                  {n ? <span className="tab-count">· {n} ★</span> : null}
                </Link>
              ) : (
                <span key={tab.seg} className="tab disabled" title="Сначала запустите анализ — монтировать пока нечего">
                  <span className="tab-no">{String(i + 1).padStart(2, "0")}</span>
                  <span>{tab.label}</span>
                </span>
              );
            }
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
      )}

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
