import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { ProjectWorkspace } from "@/pages/ProjectWorkspace";
import { SeriesPage } from "@/pages/SeriesPage";
import { ClipsPage } from "@/pages/ClipsPage";
import { MontageAssetsPage } from "@/pages/MontageAssetsPage";
import { AutomationPage } from "@/pages/AutomationPage";
import { PublicationsPage } from "@/pages/PublicationsPage";
import { AccountsPage } from "@/pages/AccountsPage";
import { TasksPage } from "@/pages/TasksPage";
import { HelpPage } from "@/pages/HelpPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { ApprovalsPage } from "@/pages/ApprovalsPage";
import { ChatPage } from "@/pages/ChatPage";
import { RequireModule } from "@/components/RequireModule";
import { OwnerCoverEditor, SharedCoverEditor } from "@/pages/cover/CoverEditorPages";

// Redirect old server-rendered paths to their SPA equivalents, preserving the id.
function LegacyRedirect({ to }: { to: (params: Record<string, string | undefined>) => string }) {
  const params = useParams();
  return <Navigate to={to(params)} replace />;
}

export function App() {
  return (
    <Routes>
      {/* Редактор превью — весь экран. Ссылка /c/<токен> открывается без входа и не ходит в API сервиса. */}
      <Route path="c/:token" element={<SharedCoverEditor />} />
      <Route path="covers/:docId" element={<RequireModule id="covers"><OwnerCoverEditor /></RequireModule>} />
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
