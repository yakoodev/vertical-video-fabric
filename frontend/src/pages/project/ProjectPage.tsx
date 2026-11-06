import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { navApi, navKeys } from "@/api/nav";
import { Icon } from "@/components/Icon";
import { PageTabs } from "@/components/PageTabs";
import { useShell } from "@/components/AppShell";
import { lazyPage } from "@/lazyPage";
import { projectPath } from "@/lib/paths";
import { LORE_TABS, movedProjectTab, projectTabs } from "@/lib/tabs";
import { projectsApi } from "@/api/projects";
import { qk } from "@/api/keys";
import { ErrorState, Loading, plural } from "@/components/ui";
import { AutopilotTab } from "@/pages/project/AutopilotTab";
import { useModules } from "@/hooks/useModules";
import { ProjectBriefTab } from "@/pages/project/ProjectBriefTab";
import { ProjectChatTab } from "@/pages/project/ProjectChatTab";
import { ProjectHypothesesTab } from "@/pages/project/ProjectHypothesesTab";
import { KnowledgeCard, KnowledgeTab } from "@/pages/project/ProjectKnowledge";
import { ProjectRulesTab } from "@/pages/project/ProjectRulesTab";
import { ProjectStatsTab } from "@/pages/project/ProjectStatsTab";

/**
 * Страница проекта /p/:projectId (app/projects.py): всё про канал или тему. Вкладка в адресе (?tab=);
 * старые значения ?tab= с /series/:id работают как были. Лор, правила и гипотезы — одна вкладка.
 */

type Tab =
  | "videos"
  | "clips"
  | "questions"
  | "files"
  | "chat"
  | "know"
  | "brief"
  | "rules"
  | "hyp"
  | "stats"
  | "autopilot"
  | "materials";
const TAB_ALIAS: Record<string, Tab> = { lore: "know", auto: "autopilot" };

const ProjectVideosTab = lazyPage<{ projectId: number; onNewVideo: () => void }>(
  () => import("@/pages/project/ProjectVideosTab"),
  "ProjectVideosTab",
);
const ProjectClips = lazyPage<{ projectId: number }>(() => import("@/components/ClipGrid"), "ProjectClipsTab");
const ProjectQuestions = lazyPage<{ projectId: number }>(() => import("@/pages/workspace/AllStreamQuestions"), "AllStreamQuestions");
const ProjectFiles = lazyPage<{ projectId: number }>(() => import("@/pages/MontageAssetsPage"), "MontageAssetsPage");
const ProjectMaterials = lazyPage<{ projectId: number }>(() => import("@/pages/project/ProjectMaterials"), "ProjectMaterials");

export function ProjectPage() {
  const { projectId } = useParams();
  const id = Number(projectId);
  // Вкладка — в адресе (?tab=expl): перезагрузка и ссылка открывают то же место.
  const [params, setParams] = useSearchParams();
  const { hash } = useLocation();
  const asked = params.get("tab");
  const moved = movedProjectTab(asked, params.get("sub"));
  const tab = (asked && (TAB_ALIAS[asked] ?? asked)) as Tab | null;
  const setTab = (next: Tab) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set("tab", next);
        p.delete("sub");
        return p;
      },
      { replace: true },
    );
  const project = useQuery({ queryKey: qk.channelProject(id), queryFn: () => projectsApi.get(id), enabled: id > 0 });
  const nav = useQuery({ queryKey: navKeys.all, queryFn: navApi.get, staleTime: 15_000 });
  const { openNewVideo } = useShell();
  const { on } = useModules();
  const tabs = projectTabs(on);
  const lore = tab === "brief" || tab === "rules" || tab === "hyp";
  const shown: Tab = lore || (tab && tabs.some((t) => t.key === tab)) ? (tab as Tab) : "videos";
  const navProject = nav.data?.projects.find((x) => x.id === id);

  // Старые вкладки ?tab=expl и другие: остальные параметры и якорь едут с собой, ссылки из Telegram не ломаются.
  if (moved) {
    const next = new URLSearchParams(params);
    next.delete("tab");
    if (asked !== "aiv" || !params.get("sub")) next.delete("sub");
    for (const [key, value] of Object.entries(moved)) next.set(key, value);
    return <Navigate to={`${projectPath(id)}?${next.toString()}${hash}`} replace />;
  }
  if (project.isLoading) return <Loading />;
  if (project.isError || !project.data) return <ErrorState error={project.error} onRetry={() => project.refetch()} />;
  const p = project.data;

  const videosCount = navProject?.videos ?? p.sources?.length ?? 0;
  const readyCount = (p.sources ?? []).reduce((sum, s) => sum + (s.clips_count ?? 0), 0);
  const inLore = shown === "know" || shown === "brief" || shown === "rules" || shown === "hyp";
  return (
    <>
      <div className="page-title page-title-row">
        <div className="page-title-main">
          <h1>{p.name}</h1>
          <div className="page-sub">
            {navProject?.releases
              ? `${navProject.sources ? `${plural(navProject.sources, "видео", "видео", "видео")} и ` : ""}${plural(navProject.releases, "выпуск", "выпуска", "выпусков")}`
              : plural(videosCount, "видео", "видео", "видео")}
            {readyCount ? ` · ${plural(readyCount, "готовый файл", "готовых файла", "готовых файлов")}` : ""}
            {p.description ? `. ${p.description}` : ""}
            {p.brand ? ` · бренд «${p.brand.name}»` : ""}
          </div>
        </div>
        <button className="btn primary btn-xl" onClick={openNewVideo} title="Ссылка, файл или ролик с нуля: выбрать тип (N)">
          <Icon name="plus" />
          Новое видео
        </button>
      </div>
      <PageTabs
        label="Вкладки проекта"
        active={inLore ? "know" : shown}
        tabs={tabs.map(({ key, label }) => ({
          key,
          label,
          to: projectPath(id, key),
          count: key === "questions" && navProject?.open_questions ? navProject.open_questions : null,
        }))}
      />
      <div className="series-body">
        {inLore ? (
          <nav className="pills" aria-label="Лор и правила">
            {LORE_TABS.map(({ key, label }) => (
              <button key={key} className={`pill${shown === key ? " on" : ""}`} onClick={() => setTab(key)}>
                {label}
              </button>
            ))}
          </nav>
        ) : null}
        <Suspense fallback={<Loading />}>
          {shown === "videos" ? (
            <div className="proj-split">
              <div className="proj-split-main">
                <ProjectVideosTab projectId={id} onNewVideo={openNewVideo} />
              </div>
              <KnowledgeCard projectId={id} />
            </div>
          ) : null}
          {shown === "clips" ? <ProjectClips projectId={id} /> : null}
          {shown === "questions" ? <ProjectQuestions projectId={id} /> : null}
          {shown === "files" ? <ProjectFiles projectId={id} /> : null}
          {shown === "materials" ? <ProjectMaterials projectId={id} /> : null}
        </Suspense>
        {shown === "know" ? <KnowledgeTab projectId={id} /> : null}
        {shown === "brief" ? <ProjectBriefTab project={p} /> : null}
        {shown === "rules" ? <ProjectRulesTab projectId={id} /> : null}
        {shown === "stats" ? <ProjectStatsTab projectId={id} /> : null}
        {shown === "hyp" ? <ProjectHypothesesTab projectId={id} /> : null}
        {shown === "autopilot" ? (
          <>
            {on("automation") ? (
              <p className="inbox-line">
                Разовый запуск по одной ссылке без настройки автопилота: <Link to={`/automation?project=${id}`}>Авто →</Link>
              </p>
            ) : null}
            <AutopilotTab projectId={id} />
          </>
        ) : null}
        {shown === "chat" ? <ProjectChatTab projectId={id} /> : null}
      </div>
    </>
  );
}
