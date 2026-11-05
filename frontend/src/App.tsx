import { Suspense } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { RequireModule } from "@/components/RequireModule";
import { Loading } from "@/components/ui";
import { lazyPage } from "@/lazyPage";

// Каждая страница — свой файл: редактор монтажа и моушн не грузятся ради списка проектов.
const ProjectsPage = lazyPage(() => import("@/pages/ProjectsPage"), "ProjectsPage");
const ProjectWorkspace = lazyPage(() => import("@/pages/ProjectWorkspace"), "ProjectWorkspace");
const SeriesPage = lazyPage(() => import("@/pages/SeriesPage"), "SeriesPage");
const ClipsPage = lazyPage(() => import("@/pages/ClipsPage"), "ClipsPage");
const MontageAssetsPage = lazyPage(() => import("@/pages/MontageAssetsPage"), "MontageAssetsPage");
const AutomationPage = lazyPage(() => import("@/pages/AutomationPage"), "AutomationPage");
const PublicationsPage = lazyPage(() => import("@/pages/PublicationsPage"), "PublicationsPage");
const AccountsPage = lazyPage(() => import("@/pages/AccountsPage"), "AccountsPage");
const TasksPage = lazyPage(() => import("@/pages/TasksPage"), "TasksPage");
const HelpPage = lazyPage(() => import("@/pages/HelpPage"), "HelpPage");
const SettingsPage = lazyPage(() => import("@/pages/SettingsPage"), "SettingsPage");
const ApprovalsPage = lazyPage(() => import("@/pages/ApprovalsPage"), "ApprovalsPage");
const ChatPage = lazyPage(() => import("@/pages/ChatPage"), "ChatPage");
const OwnerCoverEditor = lazyPage(() => import("@/pages/cover/CoverEditorPages"), "OwnerCoverEditor");
const SharedCoverEditor = lazyPage(() => import("@/pages/cover/CoverEditorPages"), "SharedCoverEditor");

// Redirect old server-rendered paths to their SPA equivalents, preserving the id.
function LegacyRedirect({ to }: { to: (params: Record<string, string | undefined>) => string }) {
  const params = useParams();
  return <Navigate to={to(params)} replace />;
}

export function App() {
  return (
    <Routes>
      {/* Редактор превью — весь экран. Ссылка /c/<токен> открывается без входа и не ходит в API сервиса. */}
      <Route path="c/:token" element={<Suspense fallback={<Loading />}><SharedCoverEditor /></Suspense>} />
      <Route path="covers/:docId" element={<RequireModule id="covers"><Suspense fallback={<Loading />}><OwnerCoverEditor /></Suspense></RequireModule>} />
      <Route element={<AppShell />}>
        {/* legacy paths from the old Jinja client */}
        <Route path="sources" element={<Navigate to="/projects" replace />} />
        <Route path="sources/:sourceId" element={<LegacyRedirect to={(p) => `/projects/${p.sourceId}`} />} />
        <Route path="sources/:sourceId/studio" element={<LegacyRedirect to={(p) => `/projects/${p.sourceId}/candidates`} />} />
        <Route path="jobs" element={<Navigate to="/publications" replace />} />
        <Route path="jobs/:jobId" element={<LegacyRedirect to={(p) => `/publications/${p.jobId}`} />} />
        <Route path="auto" element={<Navigate to="/automation" replace />} />
        <Route path="clips/:clipId" element={<Navigate to="/clips" replace />} />
        <Route path="posts/new" element={<Navigate to="/clips" replace />} />
        <Route path="presets" element={<Navigate to="/settings" replace />} />
        <Route index element={<Navigate to="/projects" replace />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:sourceId/*" element={<ProjectWorkspace />} />
        {/* Проект-серия/канал (app/projects.py): несколько источников, лор, правила, гипотезы. */}
        {/* Страницы модулей (app/modules.py): модуль выключен — подсказка, где включить. */}
        <Route path="series/:projectId" element={<RequireModule id="series"><SeriesPage /></RequireModule>} />
        {/* Чат с агентом: переписки по проектам и общие (app/project_chat.py). */}
        <Route path="chat" element={<RequireModule id="agent_chat"><ChatPage /></RequireModule>} />
        <Route path="chat/:threadId" element={<RequireModule id="agent_chat"><ChatPage /></RequireModule>} />
        <Route path="clips" element={<ClipsPage />} />
        <Route path="assets" element={<MontageAssetsPage />} />
        <Route path="automation" element={<RequireModule id="automation"><AutomationPage /></RequireModule>} />
        {/* Автопилот канала: очередь одобрения (app/autopilot.py). */}
        <Route path="approvals" element={<RequireModule id="autopilot"><ApprovalsPage /></RequireModule>} />
        <Route path="publications" element={<RequireModule id="publishing"><PublicationsPage /></RequireModule>} />
        <Route path="publications/:jobId" element={<RequireModule id="publishing"><PublicationsPage /></RequireModule>} />
        <Route path="accounts" element={<RequireModule id="publishing"><AccountsPage /></RequireModule>} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="help" element={<HelpPage />} />
        <Route path="settings/*" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Route>
    </Routes>
  );
}
