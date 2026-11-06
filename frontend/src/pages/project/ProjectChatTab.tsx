import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apKeys, autopilotApi } from "@/api/autopilot";
import { ApiError } from "@/api/client";
import { chatApi, chatKeys } from "@/api/chat";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { ChatConversation } from "@/pages/chat/ChatConversation";
import { KnowledgeCard } from "@/pages/project/ProjectKnowledge";

/** Вкладка проекта «Чат»: разговор с агентом проекта (app/project_chat.py). */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function ProjectChatTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  // Переписка (реплики, поток, шаги) — ChatConversation; здесь — Telegram, резюме, thread_id.
  const chat = useQuery({
    queryKey: apKeys.chat(projectId),
    queryFn: () => autopilotApi.chat(projectId),
    refetchInterval: 30000,
  });
  const topic = useMutation({
    mutationFn: () => autopilotApi.chatTopic(projectId),
    onSuccess: (r) => {
      toast.success(r.mode === "topic" ? "Тема проекта создана в Telegram" : "Тем нет — пиши ответом на сообщение бота");
      qc.invalidateQueries({ queryKey: apKeys.chat(projectId) });
    },
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });
  const tg = chat.data?.telegram;
  // Переписки проекта: основная (её же видит Telegram), свои новые и чаты его видео.
  const list = useQuery({ queryKey: chatKeys.threads, queryFn: chatApi.threads, refetchInterval: 30000 });
  const [picked, setPicked] = useState<number | null>(null);
  const threads = list.data?.projects.find((p) => p.id === projectId)?.threads ?? [];
  const threadId = picked ?? chat.data?.thread_id ?? 0;
  const create = useMutation({
    mutationFn: () => chatApi.create({ project_id: projectId }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: chatKeys.threads });
      setPicked(t.id);
    },
    onError: (e) => toast.error(errText(e, "Не создалось")),
  });

  return (
    <div className="series-grid">
      <section className="panel series-panel project-chat">
        {threadId ? (
          <ChatConversation
            key={threadId}
            threadId={threadId}
            compact
            header={(view) => (
              <div className="agent-head project-chat-head">
                <span className={`agent-dot${view?.running ? " on" : ""}`} />
                <strong>Задания на весь проект</strong>
                {chat.data && !chat.data.ai_available ? <span className="chip series-review">ИИ недоступен</span> : null}
                <div className="project-chat-acts">
                  {threads.length > 1 ? (
                    <select className="input" aria-label="Переписка" value={threadId} onChange={(e) => setPicked(Number(e.target.value))}>
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
                  <Link className="btn ghost sm" to={`/chat/${threadId}`}>
                    На весь экран
                  </Link>
                </div>
              </div>
            )}
          />
        ) : (
          <Loading />
        )}
      </section>
      <div className="series-col">
      <KnowledgeCard projectId={projectId} />
      <section className="panel series-panel">
        <h3>Как это работает</h3>
        <p className="muted series-hint">
          Агент видит всё, что знает о проекте, статистику, гипотезы, очередь одобрения и расписание. Напишите «запомни: …»,
          и это останется в проекте. Длинная переписка сжимается в резюме.
        </p>
        <p className="muted series-hint">
          Правила из разговора:{" "}
          {chat.data?.auto_rules === "auto" ? "однозначное записывается сразу" : "сначала спрошу «Записать правило?» — ответь «да»"}
          {" "}(вкладка «Автопилот»).
        </p>
        <div className="series-section">
          <strong>Telegram</strong>
          {!tg?.enabled ? (
            <p className="muted series-hint">Бот не подключён — Настройки → Telegram.</p>
          ) : tg.thread_id ? (
            <p className="muted series-hint">Тема проекта есть (#{tg.thread_id}) — пиши туда, ответ придёт туда же.</p>
          ) : tg.root_message_id ? (
            <p className="muted series-hint">В группе нет тем: отвечай (reply) на сообщения бота про этот проект.</p>
          ) : (
            <button className="btn sm" style={{ justifySelf: "start" }} disabled={topic.isPending} onClick={() => topic.mutate()}>
              Завести чат проекта в Telegram
            </button>
          )}
        </div>
        {chat.data?.summary ? (
          <details>
            <summary className="muted">Резюме прежней переписки</summary>
            <pre className="approval-digest">{chat.data.summary}</pre>
          </details>
        ) : null}
      </section>
      </div>
    </div>
  );
}
