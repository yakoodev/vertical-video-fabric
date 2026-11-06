import { useSearchParams } from "react-router-dom";
import { useModules } from "@/hooks/useModules";
import { MATERIAL_TABS, visibleTabs } from "@/lib/tabs";
import { AiVideoTab } from "@/pages/AiVideoTab";
import { ExplainerChannelTab } from "@/pages/ExplainerChannelTab";
import { ExplainerTab } from "@/pages/ExplainerTab";
import { StoriesTab } from "@/pages/StoriesTab";

/**
 * Проект → Материалы (решение 16): то, из чего делаются выпуски и что живёт дольше одного ролика: маскоты и позы, миры с актёрами
 * и сценами, стиль, персонажи и бюджет ИИ-видео, темы и расписание канала. Сами ролики здесь не создаются: это «Новое видео».
 */
export function ProjectMaterials({ projectId }: { projectId: number }) {
  const { on } = useModules();
  const [params, setParams] = useSearchParams();
  const tabs = visibleTabs(MATERIAL_TABS, on);
  const asked = params.get("mat");
  const shown = tabs.find((t) => t.key === asked)?.key ?? tabs[0]?.key;
  const pick = (mat: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("mat", mat);
        next.delete("sub");
        return next;
      },
      { replace: true },
    );
  return (
    <>
      <nav className="pills" aria-label="Материалы проекта">
        {tabs.map((t) => (
          <button key={t.key} className={`pill${shown === t.key ? " on" : ""}`} onClick={() => pick(t.key)}>
            {t.label}
          </button>
        ))}
      </nav>
      {shown === "mascots" ? <ExplainerTab projectId={projectId} /> : null}
      {shown === "worlds" ? <StoriesTab projectId={projectId} /> : null}
      {shown === "aiv" ? <AiVideoTab projectId={projectId} /> : null}
      {shown === "channel" ? <ExplainerChannelTab projectId={projectId} /> : null}
    </>
  );
}
