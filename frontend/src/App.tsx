import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { navApi, navKeys } from "@/api/nav";
import { AppShell } from "@/components/AppShell";
import { RequireModule } from "@/components/RequireModule";
import { Loading } from "@/components/ui";
import { useModules } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";
import { lastProject, projectPath } from "@/lib/paths";

// Каждая страница — свой файл: редактор монтажа и моушн не грузятся ради списка проектов.
const ProjectsPage = lazyPage(() => import("@/pages/ProjectsPage"), "ProjectsPage");
const ProjectWorkspace = lazyPage(() => import("@/pages/ProjectWorkspace"), "ProjectWorkspace");
const SeriesPage = lazyPage(() => import("@/pages/SeriesPage"), "SeriesPage");
const MontageAssetsPage = lazyPage(() => import("@/pages/MontageAssetsPage"), "MontageAssetsPage");
const AutomationPage = lazyPage(() => import("@/pages/AutomationPage"), "AutomationPage");
const ClipPage = lazyPage(() => import("@/pages/ClipPage"), "ClipPage");
const PubsPage = lazyPage(() => import("@/pages/PubsPage"), "PubsPage");
const InboxPage = lazyPage(() => import("@/pages/InboxPage"), "InboxPage");
const HelpPage = lazyPage(() => import("@/pages/HelpPage"), "HelpPage");
const SettingsPage = lazyPage(() => import("@/pages/SettingsPage"), "SettingsPage");
const ChatPage = lazyPage(() => import("@/pages/ChatPage"), "ChatPage");
const OwnerCoverEditor = lazyPage(() => import("@/pages/cover/CoverEditorPages"), "OwnerCoverEditor");
const SharedCoverEditor = lazyPage(() => import("@/pages/cover/CoverEditorPages"), "SharedCoverEditor");

/** Старый адрес → новый. Параметры и якорь всегда едут с собой: из Telegram и чатов ведут старые ссылки. */
function Moved({
  to,
  extra,
}: {
  to: (params: Record<string, string | undefined>) => string;
  /** Дописать в адрес (вкладка), если такого параметра ещё нет. */
  extra?: Record<string, string>;
}) {
  const params = useParams();
  const { search, hash } = useLocation();
  const [path, ownSearch = ""] = to(params).split("?");
  const merged = new URLSearchParams(ownSearch);
  new URLSearchParams(search).forEach((value, key) => merged.set(key, value));
  for (const [key, value] of Object.entries(extra ?? {})) if (!merged.has(key)) merged.set(key, value);
  const query = merged.toString();
  return <Navigate to={`${path}${query ? `?${query}` : ""}${hash}`} replace />;
}

/** /assets?tab=project&project=N — файлы проекта теперь его вкладка; остальное — «Общие файлы». */
function AssetsMoved() {
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  const project = params.get("project");
  if (params.get("tab") === "project" && project) return <Navigate to={projectPath(project, "files")} replace />;
  return <Navigate to={`/files${search}`} replace />;
}

/** Первый экран — последний открытый проект; нет проектов или модуля — все видео. */
function Home() {
  const { on, isLoading } = useModules();
  const nav = useQuery({ queryKey: navKeys.all, queryFn: navApi.get, staleTime: 15_000 });
  if (isLoading || nav.isLoading) return <Loading />;
  const projects = nav.data?.projects ?? [];
  if (!on("series") || !projects.length) return <Navigate to="/projects" replace />;
  const remembered = lastProject();
  const target = projects.find((p) => p.id === remembered) ?? [...projects].sort((a, b) => b.videos - a.videos)[0];
  return <Navigate to={projectPath(target.id)} replace />;
}

export function App() {
  return (
    <Routes>
      {/* Редактор превью — весь экран. Ссылка /c/<токен> открывается без входа и не ходит в API сервиса. */}
      <Route path="c/:token" element={<Suspense fallback={<Loading />}><SharedCoverEditor /></Suspense>} />
      <Route path="covers/:docId" element={<RequireModule id="covers"><Suspense fallback={<Loading />}><OwnerCoverEditor /></Suspense></RequireModule>} />
      <Route element={<AppShell />}>
        <Route index element={<Home />} />

        {/* Лестница: проект → видео → клип; монтаж клипа — редактор внутри видео (?clip=). */}
        <Route path="p/:projectId" element={<RequireModule id="series"><SeriesPage /></RequireModule>} />
        <Route path="v/:sourceId/*" element={<ProjectWorkspace />} />
        <Route path="clips/:clipId" element={<ClipPage />} />

        {/* Вне проектов. */}
        <Route path="inbox" element={<InboxPage />} />
        <Route path="pubs" element={<PubsPage />} />
        <Route path="pubs/:jobId" element={<RequireModule id="publishing"><PubsPage /></RequireModule>} />
        <Route path="files" element={<MontageAssetsPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        {/* Чат с агентом: переписки по проектам и общие (app/project_chat.py). */}
        <Route path="chat" element={<RequireModule id="agent_chat"><ChatPage /></RequireModule>} />
        <Route path="chat/:threadId" element={<RequireModule id="agent_chat"><ChatPage /></RequireModule>} />
        <Route path="automation" element={<RequireModule id="automation"><AutomationPage /></RequireModule>} />
        <Route path="help" element={<HelpPage />} />
        <Route path="settings/*" element={<SettingsPage />} />

        {/* Старые адреса: закладки, ссылки в Telegram и в ответах агента. */}
        <Route path="projects/:sourceId/*" element={<Moved to={(p) => `/v/${p.sourceId}/${p["*"] ?? ""}`.replace(/\/$/, "")} />} />
        <Route path="series/:projectId" element={<Moved to={(p) => `/p/${p.projectId}`} />} />
        <Route path="approvals" element={<Moved to={() => "/inbox"} extra={{ tab: "approve" }} />} />
        <Route path="tasks" element={<Moved to={() => "/inbox"} extra={{ tab: "tasks" }} />} />
        <Route path="clips" element={<Moved to={() => "/pubs"} extra={{ tab: "ready" }} />} />
        <Route path="accounts" element={<Moved to={() => "/pubs"} extra={{ tab: "accounts" }} />} />
        <Route path="publications" element={<Moved to={() => "/pubs"} extra={{ tab: "pubs" }} />} />
        <Route path="publications/:jobId" element={<Moved to={(p) => `/pubs/${p.jobId}`} />} />
        <Route path="assets" element={<AssetsMoved />} />
        {/* Самые старые, от прежнего клиента. */}
        <Route path="sources" element={<Navigate to="/projects" replace />} />
        <Route path="sources/:sourceId" element={<Moved to={(p) => `/v/${p.sourceId}`} />} />
        <Route path="sources/:sourceId/studio" element={<Moved to={(p) => `/v/${p.sourceId}/candidates`} />} />
        <Route path="jobs" element={<Moved to={() => "/pubs"} extra={{ tab: "pubs" }} />} />
        <Route path="jobs/:jobId" element={<Moved to={(p) => `/pubs/${p.jobId}`} />} />
        <Route path="auto" element={<Moved to={() => "/automation"} />} />
        <Route path="posts/new" element={<Moved to={() => "/pubs"} extra={{ tab: "ready" }} />} />
        <Route path="presets" element={<Moved to={() => "/settings"} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
