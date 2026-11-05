import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { chatApi, chatKeys } from "@/api/chat";
import { Icon } from "@/components/Icon";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { ChatConversation } from "@/pages/chat/ChatConversation";

/**
 * «Общий чат» из верхней панели: переписки без проекта поверх любого экрана. Чат проекта живёт на
 * его вкладке, все переписки разом — /chat. Пустая переписка заводится только по кнопке.
 */
export function GeneralChatDrawer({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: chatKeys.threads, queryFn: chatApi.threads, refetchInterval: 15000 });
  const threads = list.data?.general ?? [];
  const [picked, setPicked] = useState<number | null>(null);
  const latest = [...threads].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))[0];
  const threadId = picked && threads.some((t) => t.id === picked) ? picked : latest?.id ?? null;
  const create = useMutation({
    mutationFn: () => chatApi.create({ project_id: null }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: chatKeys.threads });
      setPicked(t.id);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не создалось"),
  });
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
      <aside className="drawer" role="dialog" aria-label="Общий чат">
        <div className="drawer-head">
          <h2>Общий чат</h2>
          <div className="drawer-acts">
            <button className="btn ghost sm" disabled={create.isPending} onClick={() => create.mutate()}>
              + Новый
            </button>
            <Link className="btn ghost sm" to="/chat" onClick={onClose}>
              Все переписки
            </Link>
            <button className="btn ghost sm" aria-label="Закрыть" title="Закрыть" onClick={onClose}>
              <Icon name="close" size={15} />
            </button>
          </div>
        </div>
        {threads.length > 1 ? (
          <div className="drawer-threads">
            <select
              className="input"
              aria-label="Переписка"
              value={threadId ?? ""}
              onChange={(e) => setPicked(Number(e.target.value) || null)}
            >
              {threads.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title || "Новый чат"}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div className="drawer-body">
          {list.isLoading ? <Loading /> : null}
          {list.data && !threadId ? (
            <div className="drawer-empty">
              <p className="muted">Вопросы не про один проект: что есть в сервисе, что идёт, что посмотреть. Чат проекта живёт на его вкладке «Чат».</p>
              <button className="btn primary" disabled={create.isPending} onClick={() => create.mutate()}>
                Начать общий чат
              </button>
            </div>
          ) : null}
          {threadId ? <ChatConversation key={threadId} threadId={threadId} compact /> : null}
        </div>
      </aside>
    </>
  );
}
