import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  APPROVAL_STATUS_LABEL,
  PLATFORM_LABEL,
  PUBLISH_STATUS_LABEL,
  apKeys,
  autopilotApi,
  decisionMessage,
  type Approval,
  type ApprovalAction,
  type FeedEvent,
} from "@/api/autopilot";
import { projectsApi } from "@/api/projects";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { formatSnapshotTime } from "@/api/stats";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";

/**
 * Очередь одобрения автопилота (app/autopilot.py): клипы на публикацию, правила, карточки
 * статистики. Без «Публиковать» ничего не выходит; «Переделать» и «В мусор» с комментарием —
 * урок проекту (правило с цитатой). Те же карточки приходят в Telegram, если бот подключён.
 */

const FILTERS: [string, string][] = [
  ["pending", "Ждут"],
  ["changes_requested", "На переделке"],
  ["approved", "Одобрено"],
  ["rejected,expired", "Мусор и просрочка"],
  ["all", "Все"],
];

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function ApprovalsPage() {
  const [params, setParams] = useSearchParams();
  const focus = Number(params.get("id") || 0);
  const status = params.get("status") || (focus ? "all" : "pending");
  const projectId = Number(params.get("project") || 0) || undefined;
  const projects = useQuery({ queryKey: qk.channelProjects, queryFn: projectsApi.list });
  const filter = `${status}:${projectId ?? ""}`;
  const list = useQuery({
    queryKey: apKeys.approvals(filter),
    queryFn: () => autopilotApi.approvals({ status, project_id: projectId }),
    refetchInterval: 15000,
  });
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("id");
    setParams(next, { replace: true });
  };
  const items = (list.data ?? []).filter((a) => !focus || a.id === focus || status !== "all");

  return (
    <>
      <PageHead
        title="Одобрение"
        sub="Что автопилот хочет опубликовать или запомнить. Без «Публиковать» ничего не выходит."
      />
      <div className="series-row" style={{ marginBottom: 14 }}>
        <nav className="pipeline" style={{ margin: 0 }}>
          {FILTERS.map(([key, label]) => (
            <button key={key} className={`tab${status === key ? " active" : ""}`} onClick={() => set("status", key)}>
              {label}
            </button>
          ))}
        </nav>
        <select className="input" style={{ width: "auto" }} value={projectId ?? ""} onChange={(e) => set("project", e.target.value)}>
          <option value="">Все проекты</option>
          {(projects.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      {list.isLoading ? <Loading /> : null}
      {list.isError ? <ErrorState error={list.error} onRetry={() => list.refetch()} /> : null}
      {list.data && !items.length ? (
        <EmptyState title="Пусто" hint="Агент отправляет клипы сюда инструментом submit_for_approval." />
      ) : null}
      <div className="approval-list">
        {items.map((a) => (
          <ApprovalCard key={a.id} approval={a} highlighted={a.id === focus} />
        ))}
      </div>
      <Feed projectId={projectId} />
    </>
  );
}

export function ApprovalCard({ approval: a, highlighted }: { approval: Approval; highlighted?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<ApprovalAction | null>(null);
  const [comment, setComment] = useState("");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["approvals"] });
    if (a.project_id) qc.invalidateQueries({ queryKey: ["channel-projects", String(a.project_id)] });
  };
  const decide = useMutation({
    mutationFn: ({ action, text }: { action: ApprovalAction; text: string }) => autopilotApi.decide(a.id, action, text),
    onSuccess: (res) => {
      toast.success(decisionMessage(res));
      setMode(null);
      setComment("");
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });
  const addComment = useMutation({
    mutationFn: () => autopilotApi.comment(a.id, comment),
    onSuccess: (res) => {
      toast.success(decisionMessage(res));
      setComment("");
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });
  const resend = useMutation({
    mutationFn: () => autopilotApi.resend(a.id),
    onSuccess: (r) => (r.ok ? toast.success("Отправлено в Telegram") : toast.error(r.error || "Не отправилось")),
    onError: (e) => toast.error(errText(e, "Не отправилось")),
  });

  const click = (action: ApprovalAction) => {
    if (action === "changes" || (action === "reject" && a.kind === "clip_publish")) setMode(mode === action ? null : action);
    else decide.mutate({ action, text: "" });
  };
  const pending = a.status === "pending";
  const quote = typeof a.payload?.quote === "string" ? a.payload.quote : "";

  return (
    <article className={`panel approval-card${highlighted ? " approval-card--focus" : ""}`}>
      {a.kind === "clip_publish" || (a.kind === "alert" && a.thumb_url) ? (
        <div className="approval-preview">
          {a.media_url ? (
            <video src={a.media_url} poster={a.cover_url ?? a.thumb_url ?? undefined} controls preload="none" />
          ) : a.thumb_url ? (
            <img src={a.thumb_url} alt="" />
          ) : null}
        </div>
      ) : null}
      <div className="approval-body">
        <div className="series-li-main muted" style={{ fontSize: 12.5 }}>
          <span>#{a.id}</span>
          {a.project_id ? <Link to={`/series/${a.project_id}`}>{a.project_name}</Link> : null}
          <span className={`chip${a.kind === "alert" ? " approval-kind--alert" : ""}`}>{a.kind_label}</span>
          {a.content_type ? <span className="chip">{a.content_type}</span> : null}
          <span className={`chip approval-status approval-status--${a.status}`}>{APPROVAL_STATUS_LABEL[a.status]}</span>
          {a.decided_by === "auto" ? <span className="chip">доверие auto</span> : null}
          <span>{formatSnapshotTime(a.created_at)}</span>
        </div>
        {a.kind === "clip_publish" ? (
          <>
            <h3 className="approval-title">{a.title}</h3>
            {a.description ? <p className="approval-desc">{a.description}</p> : null}
            {a.tags.length ? <div className="muted">{a.tags.map((t) => `#${t}`).join(" ")}</div> : null}
            <div className="muted" style={{ fontSize: 13 }}>
              {a.platforms.length ? a.platforms.map((p) => PLATFORM_LABEL[p] ?? p).join(", ") : "площадки проекта"} ·{" "}
              {a.scheduled_local
                ? `${a.scheduled_local} (${a.timezone})`
                : a.proposed_local
                  ? `предложено ${a.proposed_local}`
                  : "ближайший свободный слот"}
              {a.publish_status ? ` · ${PUBLISH_STATUS_LABEL[a.publish_status] ?? a.publish_status}` : ""}
            </div>
            {a.publish_error ? <div style={{ color: "var(--danger)", fontSize: 13 }}>{a.publish_error}</div> : null}
          </>
        ) : a.kind === "alert" ? (
          <>
            <h3 className="approval-title">{a.title}</h3>
            <p className="approval-desc" style={{ whiteSpace: "pre-wrap" }}>
              {a.question}
            </p>
            {typeof a.payload?.url === "string" && a.payload.url ? (
              <a href={a.payload.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)", fontSize: 13 }}>
                открыть пост ↗
              </a>
            ) : null}
          </>
        ) : (
          <>
            <h3 className="approval-title">{a.question || a.title}</h3>
            {quote ? <blockquote className="series-quote">«{quote}»</blockquote> : null}
          </>
        )}
        {a.decision_comment ? (
          <blockquote className="series-quote">
            {a.decided_by ? `${a.decided_by}: ` : ""}
            {a.decision_comment}
          </blockquote>
        ) : null}
        {pending ? (
          <div className="series-row">
            {a.buttons.map((b) => (
              <button
                key={b.action}
                className={`btn sm${b.action === "approve" ? " primary" : b.action === "reject" ? " ghost" : ""}${mode === b.action ? " active" : ""}`}
                disabled={decide.isPending}
                onClick={() => click(b.action)}
              >
                {b.label}
              </button>
            ))}
            {a.tg_state === "failed" ? (
              <button className="btn ghost sm" title={a.tg_error} onClick={() => resend.mutate()}>
                ↻ В Telegram
              </button>
            ) : null}
          </div>
        ) : null}
        {mode ? (
          <div className="approval-comment">
            <textarea
              className="input"
              rows={2}
              autoFocus
              placeholder={
                mode === "changes"
                  ? "Что переделать? (например: не ставь музыку поверх голоса) — запомню как правило"
                  : "Почему в мусор? Необязательно; если напишешь — запомню"
              }
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            <button
              className="btn primary sm"
              disabled={decide.isPending || (mode === "changes" && !comment.trim())}
              onClick={() => decide.mutate({ action: mode, text: comment })}
            >
              {mode === "changes" ? "Отправить на переделку" : "В мусор"}
            </button>
          </div>
        ) : null}
        {!pending && a.kind === "clip_publish" && a.status !== "approved" ? (
          <div className="approval-comment">
            <input
              className="input"
              placeholder="Дописать комментарий (тоже станет уроком проекту)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            <button className="btn sm" disabled={!comment.trim() || addComment.isPending} onClick={() => addComment.mutate()}>
              Добавить
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

const EVENT_LABEL: Record<string, string> = {
  submitted: "на одобрение",
  auto_approved: "без одобрения (auto)",
  approved: "одобрено",
  rejected: "в мусор",
  changes_requested: "на переделку",
  expired: "просрочено",
  scheduled: "в расписании",
  rescheduled: "перенесено",
  cancelled: "отменено",
  published: "опубликовано",
  failed: "не опубликовано",
  schedule_failed: "не поставлено",
  rule_added: "правило записано",
  rule_revoked: "правило отменено",
  rule_kept: "правило оставлено",
  comment: "комментарий",
  card_sent: "карточка в Telegram",
  alert_resolved: "тревога прошла",
  topics_added: "темы",
  topic_approve: "тема взята",
  topic_reject: "тема отклонена",
  topic_retry: "тема заново",
  video_started: "ролик начат",
  video_ready: "ролик готов",
  video_failed: "ролик не вышел",
  digest: "сводка",
  config: "настройки",
};

export function Feed({ projectId }: { projectId?: number }) {
  const feed = useQuery({ queryKey: apKeys.feed(projectId), queryFn: () => autopilotApi.feed(projectId), refetchInterval: 30000 });
  const events: FeedEvent[] = feed.data ?? [];
  return (
    <section className="panel series-panel" style={{ marginTop: 18 }}>
      <h3>Лента автопилота</h3>
      {feed.isLoading ? <Loading /> : null}
      {!feed.isLoading && !events.length ? <p className="muted">Пока тихо.</p> : null}
      <ul className="series-list approval-feed">
        {events.map((e) => (
          <li key={e.id}>
            <span className="muted">{formatSnapshotTime(e.created_at)}</span> <strong>{EVENT_LABEL[e.event] ?? e.event}</strong>
            {e.approval_id ? (
              <>
                {" "}
                · <Link to={`/approvals?id=${e.approval_id}`}>#{e.approval_id}</Link> {e.approval_title}
              </>
            ) : null}
            {!projectId && e.project_name ? <span className="muted"> · {e.project_name}</span> : null}
            {e.note ? <span className="muted"> — {e.note}</span> : null}
            {e.actor ? <span className="muted"> ({e.actor})</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
