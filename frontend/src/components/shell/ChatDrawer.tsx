import { Suspense, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { chatApi, chatKeys, type ChatThread } from "@/api/chat";
import { clipGroupKeys } from "@/api/clipGroups";
import { navKeys } from "@/api/nav";
import { Icon } from "@/components/Icon";
import { levelTab, levelTitle, type ChatLevel, type ChatPlace } from "@/components/shell/chatPlace";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { lazyPage } from "@/lazyPage";
import { ChatConversation } from "@/pages/chat/ChatConversation";

const AgentPanel = lazyPage<{ clipPlanId: number; onChanged: () => void; compact?: boolean }>(
  () => import("@/pages/workspace/motion/AgentPanel"),
  "AgentPanel",
);

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** Чат клипа: та же переписка, что на экране клипа и во вкладке ИИ в монтаже. Открытый монтаж сам подхватит правки агента. */
function ClipChat({ planId }: { planId: number }) {
  const qc = useQueryClient();
  return (
    <div className="drawer-body">
      <Suspense fallback={<Loading />}>
        <AgentPanel
          clipPlanId={planId}
          compact
          onChanged={() => {
            void qc.invalidateQueries({ queryKey: clipGroupKeys.anyView });
            void qc.invalidateQueries({ queryKey: clipGroupKeys.all });
            void qc.invalidateQueries({ queryKey: navKeys.all });
          }}
        />
      </Suspense>
    </div>
  );
}

/** Чат видео: одна переписка на видео, та же, что на странице видео. */
function VideoChat({ sourceId }: { sourceId: number }) {
  const thread = useQuery({
    queryKey: ["video-chat-thread", sourceId],
    queryFn: () => chatApi.videoThread(sourceId),
    staleTime: Infinity,
  });
  return (
    <div className="drawer-body">
      {thread.isLoading ? <Loading /> : null}
      {thread.isError ? <p className="muted">Чат не открылся: {errText(thread.error, "ошибка")}</p> : null}
      {thread.data ? <ChatConversation key={thread.data.id} threadId={thread.data.id} compact /> : null}
    </div>
  );
}

/** Переписки проекта или общие: сразу основная (у проекта) или последняя, остальные — списком. */
function ThreadChat({ projectId }: { projectId: number | null }) {
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: chatKeys.threads, queryFn: chatApi.threads, refetchInterval: 15000 });
  const main = useQuery({
    queryKey: ["project-chat-thread", projectId],
    queryFn: () => chatApi.projectThread(projectId!),
    enabled: projectId != null,
    staleTime: Infinity,
  });
  // Чаты клипов — на своей ступени и в «Все переписки»: в переключателе проекта их не видно.
  const threads: ChatThread[] =
    projectId != null ? (list.data?.projects.find((p) => p.id === projectId)?.threads ?? []).filter((t) => !t.clip_plan_id) : list.data?.general ?? [];
  const [picked, setPicked] = useState<number | null>(null);
  const latest = [...threads].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))[0];
  const fallback = projectId != null ? main.data?.thread_id ?? null : latest?.id ?? null;
  const threadId = picked && threads.some((t) => t.id === picked) ? picked : fallback;
  const create = useMutation({
    mutationFn: () => chatApi.create({ project_id: projectId }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: chatKeys.threads });
      setPicked(t.id);
    },
    onError: (e) => toast.error(errText(e, "Не создалось")),
  });
  const loading = list.isLoading || main.isLoading;
  return (
    <>
      <div className="drawer-threads">
        {threads.length > 1 ? (
          <select className="input" aria-label="Переписка" value={threadId ?? ""} onChange={(e) => setPicked(Number(e.target.value) || null)}>
            {threads.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title || "Новый чат"}
              </option>
            ))}
          </select>
        ) : null}
        <button className="btn ghost sm" disabled={create.isPending} onClick={() => create.mutate()}>
          + Новый
        </button>
      </div>
      <div className="drawer-body">
        {loading ? <Loading /> : null}
        {!loading && !threadId ? (
          <div className="drawer-empty">
            <p className="muted">Вопросы не про один проект: что есть в сервисе, что идёт, что посмотреть.</p>
            <button className="btn primary" disabled={create.isPending} onClick={() => create.mutate()}>
              Начать общий чат
            </button>
          </div>
        ) : null}
        {threadId ? <ChatConversation key={threadId} threadId={threadId} compact /> : null}
      </div>
    </>
  );
}

function LevelChat({ level }: { level: ChatLevel }) {
  if (level.kind === "clip") return <ClipChat key={level.planId} planId={level.planId} />;
  if (level.kind === "video") return <VideoChat key={level.sourceId} sourceId={level.sourceId} />;
  if (level.kind === "project") return <ThreadChat key={level.projectId} projectId={level.projectId} />;
  return <ThreadChat key="general" projectId={null} />;
}

/**
 * Чат поверх любого экрана. Открывается на чате этого места (клип, видео, проект, вне проектов — общий);
 * ступени над ним и общий чат — в один клик, все переписки разом — /chat.
 */
export function ChatDrawer({ place, start, onClose }: { place: ChatPlace; start: "here" | "general"; onClose: () => void }) {
  const [picked, setPicked] = useState<ChatLevel["kind"] | null>(start === "general" ? "general" : null);
  const level = place.levels.find((l) => l.kind === picked) ?? place.levels[0];
  const title = levelTitle(level);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={title}>
        <div className="drawer-head">
          <h2>{title}</h2>
          <div className="drawer-acts">
            <Link className="btn ghost sm" to="/chat" onClick={onClose}>
              Все переписки
            </Link>
            <button className="btn ghost sm" aria-label="Закрыть" title="Закрыть" onClick={onClose}>
              <Icon name="close" size={15} />
            </button>
          </div>
        </div>
        {place.levels.length > 1 ? (
          <div className="drawer-levels" role="tablist" aria-label="К чему привязан чат">
            {place.levels.map((l) => (
              <button
                key={l.kind}
                type="button"
                role="tab"
                aria-selected={l === level}
                className={`drawer-level${l === level ? " on" : ""}`}
                title={levelTitle(l)}
                onClick={() => setPicked(l.kind)}
              >
                {levelTab(l)}
              </button>
            ))}
          </div>
        ) : null}
        <LevelChat level={level} />
      </aside>
    </>
  );
}
