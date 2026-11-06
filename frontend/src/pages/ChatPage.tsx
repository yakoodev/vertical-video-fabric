import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { chatApi, chatKeys, type ChatThread, type ChatThreadView } from "@/api/chat";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { ChatConversation } from "@/pages/chat/ChatConversation";

/**
 * 💬 Страница «Чат»: переписки с агентом «как с Claude». Слева — переписки по проектам
 * (серии и каналы) и общие, справа — переписка. Бэкенд — app/project_chat.py.
 */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function ThreadItem({ t, active }: { t: ChatThread; active: boolean }) {
  return (
    <Link to={`/chat/${t.id}`} className={`chat-nav-item${active ? " active" : ""}`}>
      <span className="chat-nav-title">
        {t.running ? <span className="agent-dot on" /> : null}
        {t.title || "Новый чат"}
      </span>
      {t.last_text ? <span className="chat-nav-last">{t.last_text}</span> : null}
    </Link>
  );
}

function Header({ view }: { view: ChatThreadView | undefined }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const models = useQuery({ queryKey: chatKeys.models, queryFn: chatApi.models, staleTime: 60000 });
  const id = view?.thread.id ?? 0;
  const refresh = () => {
    qc.invalidateQueries({ queryKey: chatKeys.thread(id) });
    qc.invalidateQueries({ queryKey: chatKeys.threads });
  };
  const update = useMutation({
    mutationFn: (body: { title?: string; model?: string }) => chatApi.update(id, body),
    onSuccess: refresh,
    onError: (e) => toast.error(errText(e, "Не сохранилось")),
  });
  const clear = useMutation({
    mutationFn: () => chatApi.clear(id),
    onSuccess: refresh,
    onError: (e) => toast.error(errText(e, "Не очистилось")),
  });
  const remove = useMutation({
    mutationFn: () => chatApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: chatKeys.threads });
      navigate("/chat");
    },
    onError: (e) => toast.error(errText(e, "Не удалилось")),
  });
  if (!view) return null;
  const t = view.thread;
  return (
    <div className="chat-header">
      <Link to="/chat" className="chat-back btn ghost sm" aria-label="К списку переписок">
        ←
      </Link>
      <span className={`agent-dot${view.running ? " on" : ""}`} />
      <div className="chat-header-title">
        {editing ? (
          <input
            className="input chat-title-input"
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => {
              setEditing(false);
              if (title.trim() && title.trim() !== t.title) update.mutate({ title: title.trim() });
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") setEditing(false);
            }}
          />
        ) : (
          <button
            className="chat-title-btn"
            title="Переименовать"
            onClick={() => {
              setTitle(t.title);
              setEditing(true);
            }}
          >
            {t.title || "Новый чат"}
          </button>
        )}
        {view.video ? (
          <Link to={`/v/${view.video.id}`} className="chat-project-chip" title={`Чат видео ${view.video.title}`}>
            Чат видео {view.video.title}
          </Link>
        ) : null}
        {view.project ? (
          <Link to={`/p/${view.project.id}`} className="chat-project-chip" title={`Проект ${view.project.name}`}>
            {view.video ? `Проект ${view.project.name}` : `Чат проекта ${view.project.name}`}
          </Link>
        ) : view.video ? null : (
          <span className="chat-project-chip muted">Общий чат</span>
        )}
      </div>
      <div className="chat-header-actions">
        <label className="chat-model" title="Провайдер и модель ответа">
          <span className="muted">Gemini</span>
          <select
            className="input"
            value={t.model || models.data?.default || view.model}
            disabled={!models.data || view.running}
            onChange={(e) => update.mutate({ model: e.target.value === models.data?.default ? "" : e.target.value })}
          >
            {(models.data?.models ?? [{ id: view.model, label: view.model }]).map((m) => (
              <option key={m.id} value={m.id} title={"note" in m ? m.note : undefined}>
                {"note" in m && m.note ? `${m.label} · ${m.note}` : m.label}
              </option>
            ))}
          </select>
        </label>
        <button
          className="btn ghost sm"
          disabled={!view.messages.length || view.running || clear.isPending}
          onClick={() => window.confirm("Очистить переписку? Правила проекта останутся.") && clear.mutate()}
        >
          Очистить
        </button>
        {!t.is_default ? (
          <button
            className="btn ghost sm"
            disabled={view.running || remove.isPending}
            onClick={() => window.confirm("Удалить переписку целиком?") && remove.mutate()}
          >
            Удалить
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function ChatPage() {
  const { threadId } = useParams();
  const id = Number(threadId) || 0;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [filter, setFilter] = useState("");
  const list = useQuery({
    queryKey: chatKeys.threads,
    queryFn: chatApi.threads,
    refetchInterval: 15000,
  });
  const create = useMutation({
    mutationFn: (projectId: number | null) => chatApi.create({ project_id: projectId }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: chatKeys.threads });
      navigate(`/chat/${t.id}`);
    },
    onError: (e) => toast.error(errText(e, "Не создалось")),
  });
  const openProject = useMutation({
    mutationFn: (projectId: number) => chatApi.projectThread(projectId),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: chatKeys.threads });
      navigate(`/chat/${r.thread_id}`);
    },
    onError: (e) => toast.error(errText(e, "Не открылось")),
  });
  // Без выбранной переписки на широком экране — открыть последнюю.
  useEffect(() => {
    if (id || !list.data || window.innerWidth < 860) return;
    const all = [...list.data.general, ...list.data.videos, ...list.data.projects.flatMap((p) => p.threads)];
    const latest = all.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))[0];
    if (latest) navigate(`/chat/${latest.id}`, { replace: true });
  }, [id, list.data, navigate]);

  const q = filter.trim().toLowerCase();
  const projects = (list.data?.projects ?? []).filter(
    (p) => !q || p.name.toLowerCase().includes(q) || p.threads.some((t) => t.title.toLowerCase().includes(q)),
  );
  const general = (list.data?.general ?? []).filter((t) => !q || t.title.toLowerCase().includes(q));
  const looseVideos = (list.data?.videos ?? []).filter((t) => !q || t.title.toLowerCase().includes(q));

  return (
    <div className={`chat-page${id ? " has-thread" : ""}`}>
      <aside className="chat-side panel">
        <div className="chat-side-head">
          <h2>Чат</h2>
          <button className="btn primary sm" disabled={create.isPending} onClick={() => create.mutate(null)}>
            + Новый чат
          </button>
        </div>
        <input className="input chat-filter" placeholder="Найти переписку…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        {list.isLoading ? <Loading /> : null}
        <div className="chat-nav">
          <div className="chat-nav-group">
            <div className="chat-nav-cap">Общие</div>
            {general.length ? general.map((t) => <ThreadItem key={t.id} t={t} active={t.id === id} />) : <div className="chat-nav-none muted">Нет общих переписок</div>}
          </div>
          {looseVideos.length ? (
            <div className="chat-nav-group">
              <div className="chat-nav-cap">Видео вне проектов</div>
              {looseVideos.map((t) => <ThreadItem key={t.id} t={t} active={t.id === id} />)}
            </div>
          ) : null}
          {projects.map((p) => (
            <div key={p.id} className="chat-nav-group">
              <div className="chat-nav-cap">
                <span className="chat-ellipsis">{p.name}</span>
                <button className="chat-nav-add" title="Новая переписка в проекте" disabled={create.isPending} onClick={() => create.mutate(p.id)}>
                  +
                </button>
              </div>
              {p.threads.length ? (
                p.threads.map((t) => <ThreadItem key={t.id} t={t} active={t.id === id} />)
              ) : (
                <button className="chat-nav-item ghost" onClick={() => openProject.mutate(p.id)}>
                  <span className="chat-nav-title">Основной чат проекта</span>
                  <span className="chat-nav-last">ещё не начат</span>
                </button>
              )}
            </div>
          ))}
        </div>
        {list.data && !list.data.ai.available ? <div className="chat-banner warn chat-small">{list.data.ai.hint}</div> : null}
      </aside>
      <section className="chat-main panel">
        {id ? (
          <ChatConversation key={id} threadId={id} header={(view) => <Header view={view} />} />
        ) : (
          <div className="chat-empty chat-empty-page">
            <div className="chat-empty-mark">✦</div>
            <div className="chat-empty-title">Выбери переписку или начни новую</div>
            <p className="muted">Общий чат — про весь сервис. Чат проекта и его видео знают лор и правила этого проекта, очередь одобрения и расписание.</p>
            <button className="btn primary" onClick={() => create.mutate(null)}>
              + Новый чат
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
