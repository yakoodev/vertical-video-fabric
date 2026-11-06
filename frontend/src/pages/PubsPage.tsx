import { Suspense } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { PageTabs } from "@/components/PageTabs";
import { Loading } from "@/components/ui";
import { useModules } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";
import { SECTIONS, visibleTabs } from "@/lib/tabs";

const PublicationsPage = lazyPage<{ embedded?: boolean }>(() => import("@/pages/PublicationsPage"), "PublicationsPage");
const AccountsPage = lazyPage<{ embedded?: boolean }>(() => import("@/pages/AccountsPage"), "AccountsPage");
const ClipsPage = lazyPage<{ embedded?: boolean }>(() => import("@/pages/ClipsPage"), "ClipsPage");

/** Публикации и аккаунты: очередь публикаций, аккаунты площадок и готовые ролики всех проектов. */
export function PubsPage() {
  const [params] = useSearchParams();
  const { jobId } = useParams();
  const { on } = useModules();
  const publishing = on("publishing");
  const tabs = visibleTabs(SECTIONS.pubs.tabs, on);
  const asked = jobId ? "pubs" : params.get("tab") ?? "";
  const tab = tabs.some((t) => t.key === asked) ? asked : tabs[0].key;
  return (
    <>
      <div className="page-title">
        <h1>{publishing ? "Публикации и аккаунты" : "Готовые ролики"}</h1>
      </div>
      <PageTabs tabs={tabs} active={tab} label="Разделы публикаций" />
      <Suspense fallback={<Loading />}>
        {tab === "pubs" ? <PublicationsPage embedded /> : null}
        {tab === "accounts" ? <AccountsPage embedded /> : null}
        {tab === "ready" ? <ClipsPage embedded /> : null}
      </Suspense>
    </>
  );
}
