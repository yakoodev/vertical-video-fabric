import { Suspense, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useLocation, useParams, useSearchParams, Link } from "react-router-dom";
import { videoPath } from "@/lib/paths";
import { VIDEO_TABS, visibleTabs, type VideoTab } from "@/lib/tabs";
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
// Вкладка «Черновики» тянет за собой весь редактор: остальным вкладкам он не нужен.
const SourceTab = lazyPage<TabProps>(() => import("@/pages/workspace/SourceTab"), "SourceTab");
const SegmentsTab = lazyPage<TabProps>(() => import("@/pages/workspace/SegmentsTab"), "SegmentsTab");
const CandidatesTab = lazyPage<TabProps>(() => import("@/pages/workspace/CandidatesTab"), "CandidatesTab");
const ClipsTab = lazyPage<TabProps>(() => import("@/pages/workspace/ClipsTab"), "ClipsTab");
const VideoOverview = lazyPage<TabProps>(() => import("@/pages/workspace/VideoOverview"), "VideoOverview");
const MontagedTab = lazyPage<TabProps>(() => import("@/pages/workspace/MontagedTab"), "MontagedTab");
const StreamQuestionsTab = lazyPage<TabProps>(() => import("@/pages/workspace/StreamQuestionsTab"), "StreamQuestionsTab");

/** Подпись вкладки со счётчиком; ноль не показываем. */
function TabLabel({ label, count }: { label: string; count: number }) {
  return (
    <>
      {label}
      {count ? <span className="ptabs-n">{count}</span> : null}
    </>
  );
}

/** Кнопка «Ещё»: вкладки, которые нужны редко. Открытая сейчас видна прямо на кнопке. */
function MoreTabs({ tabs, sourceId, countOf }: { tabs: VideoTab[]; sourceId: string; countOf: (key: string) => number }) {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const current = tabs.find((tab) => location.pathname === videoPath(sourceId, tab.key));

  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return;
    boxRef.current?.querySelector<HTMLElement>("a")?.focus();
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const items = Array.from(boxRef.current?.querySelectorAll<HTMLElement>("a") ?? []);
        const at = items.indexOf(document.activeElement as HTMLElement);
        items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
      }
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="vtabs-more" ref={boxRef}>
      <button
        type="button"
        ref={buttonRef}
        className={`vtabs-btn${current ? " on" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {current ? `Ещё: ${current.label}` : "Ещё"}
        <Icon name="down" size={15} />
      </button>
      {open ? (
        <div className="vtabs-menu" role="menu" aria-label="Ещё вкладки видео">
          {tabs.map((tab) => (
            <NavLink key={tab.key} role="menuitem" data-tab={tab.key} to={videoPath(sourceId, tab.key)} className={({ isActive }) => (isActive ? "on" : "")}>
              <TabLabel label={tab.label} count={countOf(tab.key)} />
            </NavLink>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Вкладки видео: лента и «Ещё». На телефоне лента сама доезжает до открытой вкладки. */
function VideoTabs({ sourceId, counts }: { sourceId: string; counts: Record<string, number> }) {
  const { on } = useModules();
  const location = useLocation();
  const navRef = useRef<HTMLElement>(null);
  const tabs = visibleTabs(VIDEO_TABS, on);
  const countOf = (key: string) => counts[key] ?? 0;

  useEffect(() => {
    const nav = navRef.current;
    const active = nav?.querySelector<HTMLElement>("a.on");
    if (!nav || !active) return;
    const a = active.getBoundingClientRect();
    const n = nav.getBoundingClientRect();
    nav.scrollLeft += a.left - n.left - (n.width - a.width) / 2;
  }, [location.pathname]);

  return (
    <div className="vtabs">
      <nav className="ptabs" aria-label="Вкладки видео" ref={navRef}>
        {tabs.filter((tab) => !tab.more).map((tab) => (
          <NavLink key={tab.key} data-tab={tab.key} to={videoPath(sourceId, tab.key)} className={({ isActive }) => (isActive ? "on" : "")}>
            <TabLabel label={tab.label} count={countOf(tab.key)} />
          </NavLink>
        ))}
      </nav>
      <MoreTabs tabs={tabs.filter((tab) => tab.more)} sourceId={sourceId} countOf={countOf} />
    </div>
  );
}

export function ProjectWorkspace() {
  const { sourceId = "" } = useParams();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  // In the clip editor the project chrome gets out of the way: the editor shows
  // only its own «← Назад к черновикам» bar.
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
  // Клипов столько, сколько групп: версии v2 и v3 одного момента — один клип. Черновики — клипы без готового файла.
  const counts: Record<string, number> = {
    clips: countClips(visible),
    candidates: visible.filter((p) => p.status !== "rendered").length,
    segments: source.segments.length,
    renders: source.clips_count ?? source.clips.length,
  };
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
        <div className="ws-meta">
          <Badge status={source.status} />
          <span>{formatDuration(source.duration_sec)}</span>
          {source.width ? <span>{source.width}×{source.height}</span> : null}
          {metaBits.map((bit) => <span key={bit}>{bit}</span>)}
          {source.original_url ? (
            <a className="ws-url" href={source.original_url} target="_blank" rel="noreferrer" title={source.original_url}>
              {source.original_url.replace(/^https?:\/\/(www\.)?/, "")}
            </a>
          ) : null}
        </div>
        <VideoTabs sourceId={sourceId} counts={counts} />
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
        {/* Неизвестный адрес, в том числе бывшая вкладка /edit. Только полный путь: относительный дописывался бы к адресу без конца. */}
        <Route path="*" element={<Navigate to={videoPath(sourceId, "clips")} replace />} />
      </Routes>
      </Suspense>
    </>
  );
}
