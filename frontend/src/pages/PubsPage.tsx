import { Suspense } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { PageTabs } from "@/components/PageTabs";
import { Loading } from "@/components/ui";
import { useModules } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";
import { SECTION_SUBS, SECTIONS, visibleTabs } from "@/lib/tabs";

const PublicationsPage = lazyPage<{ embedded?: boolean }>(() => import("@/pages/PublicationsPage"), "PublicationsPage");
const AccountsPage = lazyPage<{ embedded?: boolean }>(() => import("@/pages/AccountsPage"), "AccountsPage");
const ReadyClipsPage = lazyPage<{ embedded?: boolean }>(() => import("@/pages/ReadyClipsPage"), "ReadyClipsPage");

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
        {/* Чем вкладка отличается от Ленты и соседей — одной фразой (lib/tabs.ts SECTION_SUBS). */}
        <p className="page-sub">{SECTION_SUBS[tab as keyof typeof SECTION_SUBS] ?? ""}</p>
      </div>
      <PageTabs tabs={tabs} active={tab} label="Разделы публикаций" />
      <Suspense fallback={<Loading />}>
        {tab === "pubs" ? <PublicationsPage embedded /> : null}
        {tab === "accounts" ? <AccountsPage embedded /> : null}
        {tab === "ready" ? <ReadyClipsPage embedded /> : null}
      </Suspense>
    </>
  );
}
