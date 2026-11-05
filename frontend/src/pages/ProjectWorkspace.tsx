import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useLocation, useParams, useSearchParams, Link } from "react-router-dom";
import { videoPath } from "@/lib/paths";
import { countClips } from "@/api/clipGroups";
import { formatBytes } from "@/components/ui";
import { KIND_LABELS } from "@/lib/videoKinds";
import { sourcesApi } from "@/api/sources";
import { qk } from "@/api/keys";
import { Badge, ErrorState, Loading, formatDuration } from "@/components/ui";
import { Icon } from "@/components/Icon";
import { RequireModule } from "@/components/RequireModule";
import { useModules } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";

type TabProps = { sourceId: string };
// Вкладка «Моменты» тянет за собой весь редактор: остальным вкладкам он не нужен.
const SourceTab = lazyPage<TabProps>(() => import("@/pages/workspace/SourceTab"), "SourceTab");
const SegmentsTab = lazyPage<TabProps>(() => import("@/pages/workspace/SegmentsTab"), "SegmentsTab");
const CandidatesTab = lazyPage<TabProps>(() => import("@/pages/workspace/CandidatesTab"), "CandidatesTab");
const ClipsTab = lazyPage<TabProps>(() => import("@/pages/workspace/ClipsTab"), "ClipsTab");
const VideoOverview = lazyPage<TabProps>(() => import("@/pages/workspace/VideoOverview"), "VideoOverview");
const MontagedTab = lazyPage<TabProps>(() => import("@/pages/workspace/MontagedTab"), "MontagedTab");
const StreamQuestionsTab = lazyPage<TabProps>(() => import("@/pages/workspace/StreamQuestionsTab"), "StreamQuestionsTab");

// "Монтаж" is not a route of its own: it opens the clip editor inside the
// moments tab (?clip=<id>), so it shares the moments' selection and settings.
const TABS = [
  // Сама страница видео: запись, транскрипт, чат и сетка клипов (один элемент на клип).
  { seg: "clips", label: "Клипы" },
  { seg: "source", label: "Запись и анализ" },
  { seg: "candidates", label: "Моменты" },
  { seg: "edit", label: "Монтаж" },
  { seg: "segments", label: "Сегменты" },
  // Каждый готовый файл отдельно: переименовать, удалить, опубликовать пачкой.
  { seg: "renders", label: "Рендеры" },
  { seg: "montaged", label: "Смонтированные" },
  { seg: "questions", label: "Вопросы", module: "stream_questions" },
];

export function ProjectWorkspace() {
  const { sourceId = "" } = useParams();
  const location = useLocation();
  const { on } = useModules();
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
  // Клипов столько, сколько групп: версии v2 и v3 одного момента — один клип. Рендеров и моментов больше.
  const clipCount = countClips(visible);
  const tabCount = (seg: string): number =>
    seg === "candidates"
      ? visible.length
      : seg === "edit"
        ? favorites.length
        : seg === "clips"
          ? clipCount
          : seg === "renders"
            ? source.clips_count ?? source.clips.length
            : seg === "segments"
              ? source.segments.length
              : 0;
  const created = /^(\d{4})-(\d{2})-(\d{2})/.exec(source.created_at ?? "");
  const metaBits = [
    `${KIND_LABELS[source.source_type] ?? "Видео"}${created ? `, ${created[3]}.${created[2]}.${created[1]}` : ""}`,
    source.size_bytes ? formatBytes(source.size_bytes) : "",
  ].filter(Boolean);

  return (
    <>
      {editing ? null : (
      <div className="ws-head">
        <div className="ws-headrow">
          <h1 className="ws-title" title={source.original_filename || undefined}>
            {source.display_name || source.original_filename || source.original_url || `Видео #${source.id}`}
          </h1>
          <Link
            className={`btn primary ws-cut${source.has_file === false ? " is-off" : ""}`}
            aria-disabled={source.has_file === false || undefined}
            title={source.has_file === false ? "Для нарезки нужна запись стрима" : "Найти ещё клипы в этой записи"}
            to={source.has_file === false ? videoPath(sourceId, "clips") : `${videoPath(sourceId, "clips")}?cut=1`}
          >
            <Icon name="scissors" size={16} /> Нарезать ещё
          </Link>
        </div>
        <div className="ws-pills">
          <Badge status={source.status} />
          <span className="ws-pill mono">⏱ {formatDuration(source.duration_sec)}</span>
          {source.width ? <span className="ws-pill mono">▭ {source.width}×{source.height}</span> : null}
          {metaBits.map((bit) => <span key={bit} className="ws-pill">{bit}</span>)}
          {source.original_url ? (
            <a className="ws-pill ws-url" href={source.original_url} target="_blank" rel="noreferrer" title={source.original_url}>
              {source.original_url.replace(/^https?:\/\/(www\.)?/, "")}
            </a>
          ) : null}
          <span className="ws-sum">
            <b>{clipCount}</b> клипов · <b>{visible.length}</b> моментов · <b>{favorites.length}</b> ★ ·{" "}
            <b>{source.clips_count ?? source.clips.length}</b> рендеров
          </span>
        </div>
        <nav className="pipeline">
          {TABS.filter((tab) => !tab.module || on(tab.module)).map((tab, i) => {
            const n = tabCount(tab.seg);
            if (tab.seg === "edit") {
              return editTarget ? (
                <Link
                  key={tab.seg}
                  to={`${videoPath(sourceId, "candidates")}?clip=${editTarget.id}`}
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
                to={videoPath(sourceId, tab.seg)}
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

      <Suspense fallback={<Loading />}>
      <Routes>
        <Route index element={<Navigate to="clips" replace />} />
        <Route path="source" element={<SourceTab sourceId={sourceId} />} />
        <Route path="segments" element={<SegmentsTab sourceId={sourceId} />} />
        <Route path="candidates" element={<CandidatesTab sourceId={sourceId} />} />
        <Route path="clips" element={<VideoOverview sourceId={sourceId} />} />
        <Route path="renders" element={<ClipsTab sourceId={sourceId} />} />
        <Route path="montaged" element={<MontagedTab sourceId={sourceId} />} />
        <Route path="questions" element={<RequireModule id="stream_questions"><StreamQuestionsTab sourceId={sourceId} /></RequireModule>} />
        <Route path="*" element={<Navigate to="clips" replace />} />
      </Routes>
      </Suspense>
    </>
  );
}
