import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { ProjectWorkspace } from "@/pages/ProjectWorkspace";
import { ClipsPage } from "@/pages/ClipsPage";
import { AutomationPage } from "@/pages/AutomationPage";
import { PublicationsPage } from "@/pages/PublicationsPage";
import { AccountsPage } from "@/pages/AccountsPage";
import { TasksPage } from "@/pages/TasksPage";
import { HelpPage } from "@/pages/HelpPage";
import { SettingsPage } from "@/pages/SettingsPage";

// Redirect old server-rendered paths to their SPA equivalents, preserving the id.
function LegacyRedirect({ to }: { to: (params: Record<string, string | undefined>) => string }) {
  const params = useParams();
  return <Navigate to={to(params)} replace />;
}

export function App() {
  return (
    <Routes>
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
        <Route path="clips" element={<ClipsPage />} />
        <Route path="automation" element={<AutomationPage />} />
        <Route path="publications" element={<PublicationsPage />} />
        <Route path="publications/:jobId" element={<PublicationsPage />} />
        <Route path="accounts" element={<AccountsPage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="help" element={<HelpPage />} />
        <Route path="settings/*" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Route>
    </Routes>
  );
}
