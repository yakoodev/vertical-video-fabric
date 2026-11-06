import { Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { navApi, navKeys } from "@/api/nav";
import { PageTabs } from "@/components/PageTabs";
import { Loading } from "@/components/ui";
import { useModules } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";
import { SECTIONS, visibleTabs } from "@/lib/tabs";

const ApprovalsPage = lazyPage<{ embedded?: boolean }>(() => import("@/pages/ApprovalsPage"), "ApprovalsPage");
const AllStreamQuestions = lazyPage(() => import("@/pages/workspace/AllStreamQuestions"), "AllStreamQuestions");
const TasksPage = lazyPage<{ embedded?: boolean }>(() => import("@/pages/TasksPage"), "TasksPage");

/**
 * Входящие: всё, что ждёт владельца, на одном экране. Одобрение и очередь — те же компоненты, что были
 * страницами (/approvals и /tasks теперь ведут сюда), вопросы — всех проектов сразу.
 */
export function InboxPage() {
  const [params] = useSearchParams();
  const { on } = useModules();
  const nav = useQuery({ queryKey: navKeys.all, queryFn: navApi.get, staleTime: 15_000 });
  const inbox = nav.data?.inbox;
  const counts: Record<string, number> = {
    approve: inbox?.approvals ?? 0,
    questions: (inbox?.questions ?? 0) + (inbox?.memes ?? 0),
  };
  const tabs = visibleTabs(SECTIONS.inbox.tabs, on).map((t) => ({ ...t, count: counts[t.key] }));
  const asked = params.get("tab") ?? (params.has("id") ? "approve" : "");
  const tab = tabs.some((t) => t.key === asked) ? asked : tabs[0].key;
  return (
    <>
      <div className="page-title">
        <h1>Входящие</h1>
      </div>
      <PageTabs tabs={tabs} active={tab} label="Разделы входящих" />
      <Suspense fallback={<Loading />}>
        {tab === "approve" ? <ApprovalsPage embedded /> : null}
        {tab === "questions" ? <AllStreamQuestions /> : null}
        {tab === "tasks" ? <TasksPage embedded /> : null}
      </Suspense>
    </>
  );
}
