import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  PLATFORM_LABEL,
  PUBLISH_STATUS_LABEL,
  WEEKDAYS,
  apKeys,
  autopilotApi,
  type AutopilotConfig,
  type Slot,
} from "@/api/autopilot";
import { accountsApi } from "@/api/accounts";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { ApprovalCard, Feed } from "@/pages/ApprovalsPage";
import { VodWatchPanel } from "@/pages/SeriesVodWatch";
import { ChatConversation } from "@/pages/chat/ChatConversation";

/**
 * Вкладки страницы серии: «Автопилот» (доверие, слоты, лимиты, ближайшие публикации,
 * сводка) и «Чат» (разговор с агентом проекта, app/project_chat.py).
 */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const PLATFORMS = ["youtube", "tiktok", "instagram"];

// --------------------------------------------------------------------------- //
// Автопилот
// --------------------------------------------------------------------------- //

export function AutopilotTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const config = useQuery({ queryKey: apKeys.config(projectId), queryFn: () => autopilotApi.config(projectId) });
  const accounts = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list });
  const [draft, setDraft] = useState<AutopilotConfig | null>(null);
  const [newType, setNewType] = useState("");
  useEffect(() => {
    if (config.data) setDraft(config.data);
  }, [config.data]);

  const save = useMutation({
    mutationFn: () => autopilotApi.saveConfig(projectId, draft!),
    onSuccess: (cfg) => {
      qc.setQueryData(apKeys.config(projectId), cfg);
      qc.invalidateQueries({ queryKey: apKeys.schedule(projectId) });
      toast.success("Настройки автопилота сохранены");
    },
    onError: (e) => toast.error(errText(e, "Не сохранилось")),
  });

  if (!draft) return <Loading />;
  const patch = (p: Partial<AutopilotConfig>) => setDraft({ ...draft, ...p });
  const setSlot = (i: number, slot: Slot) => patch({ slots: draft.slots.map((s, k) => (k === i ? slot : s)) });

  return (
    <div className="series-grid">
      <div className="series-col">
        <section className="panel series-panel">
          <h3>Доверие</h3>
          <p className="muted series-hint">
            «Сам» — публикует без одобрения (но пишет в ленту). Тип контента — тег клипа (type:meme) или поле агента.
          </p>
          <ul className="series-list">
            {Object.entries(draft.trust).map(([type, level]) => (
              <li key={type} className="series-row">
                <span style={{ minWidth: 120 }}>{type === "default" ? "всё остальное" : type}</span>
                <select
                  className="input"
                  style={{ width: "auto" }}
                  value={level}
                  onChange={(e) => patch({ trust: { ...draft.trust, [type]: e.target.value as "approve" | "auto" } })}
                >
                  <option value="approve">спросить меня</option>
                  <option value="auto">сам</option>
                </select>
                {type !== "default" ? (
                  <button
                    className="btn ghost sm"
                    onClick={() => {
                      const { [type]: _gone, ...rest } = draft.trust;
                      patch({ trust: rest });
                    }}
                  >
                    ✕
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
          <div className="series-row">
            <input className="input" style={{ width: 160 }} placeholder="meme, ad…" value={newType} onChange={(e) => setNewType(e.target.value)} />
            <button
              className="btn sm"
              disabled={!newType.trim()}
              onClick={() => {
                patch({ trust: { ...draft.trust, [newType.trim().toLowerCase()]: "approve" } });
                setNewType("");
              }}
            >
              + тип
            </button>
          </div>
        </section>

        <section className="panel series-panel">
          <h3>Слоты публикаций</h3>
          <label className="field">
            <span>Часовой пояс</span>
            <input className="input" value={draft.timezone} placeholder="Europe/Moscow или UTC+3" onChange={(e) => patch({ timezone: e.target.value })} />
          </label>
          {draft.slots.map((slot, i) => (
            <div key={i} className="series-section">
              <div className="series-row">
                {WEEKDAYS.map((d, day) => (
                  <label key={d} className="check">
                    <input
                      type="checkbox"
                      checked={slot.days.includes(day)}
                      onChange={(e) =>
                        setSlot(i, { ...slot, days: e.target.checked ? [...slot.days, day].sort() : slot.days.filter((x) => x !== day) })
                      }
                    />
                    {d}
                  </label>
                ))}
              </div>
              <div className="series-row">
                <input
                  className="input"
                  style={{ flex: 1 }}
                  placeholder="12:00, 19:00"
                  defaultValue={slot.times.join(", ")}
                  onBlur={(e) => setSlot(i, { ...slot, times: e.target.value.split(/[\s,;]+/).filter(Boolean) })}
                />
                <button className="btn ghost sm" onClick={() => patch({ slots: draft.slots.filter((_, k) => k !== i) })}>
                  Убрать
                </button>
              </div>
            </div>
          ))}
          <button className="btn sm" style={{ justifySelf: "start" }} onClick={() => patch({ slots: [...draft.slots, { days: [0, 1, 2, 3, 4], times: ["18:00"] }] })}>
            + слот
          </button>
          {!draft.slots.length ? <p className="muted series-hint">Без слотов одобренное уходит сразу.</p> : null}
          <div className="series-section">
            <strong>Не больше в день на площадку</strong>
            <div className="series-row">
              {["default", ...PLATFORMS].map((p) => (
                <label key={p} className="field" style={{ margin: 0 }}>
                  <span>{p === "default" ? "любая" : PLATFORM_LABEL[p]}</span>
                  <input
                    className="input series-num"
                    type="number"
                    min={0}
                    value={draft.per_day[p] ?? ""}
                    placeholder="0 — без лимита"
                    onChange={(e) => patch({ per_day: { ...draft.per_day, [p]: Number(e.target.value || 0) } })}
                  />
                </label>
              ))}
            </div>
          </div>
          <div className="series-section">
            <strong>Площадки и аккаунты по умолчанию</strong>
            {PLATFORMS.map((p) => {
              const own = (accounts.data ?? []).filter((a) => a.platform === p);
              return (
                <div key={p} className="series-row">
                  <label className="check" style={{ minWidth: 110 }}>
                    <input
                      type="checkbox"
                      checked={draft.platforms.includes(p)}
                      onChange={(e) =>
                        patch({ platforms: e.target.checked ? [...draft.platforms, p] : draft.platforms.filter((x) => x !== p) })
                      }
                    />
                    {PLATFORM_LABEL[p]}
                  </label>
                  {own.length > 1 ? (
                    <select
                      className="input"
                      style={{ width: "auto" }}
                      value={draft.accounts[p]?.[0] ?? ""}
                      onChange={(e) => patch({ accounts: { ...draft.accounts, [p]: e.target.value ? [Number(e.target.value)] : [] } })}
                    >
                      <option value="">выбери аккаунт</option>
                      {own.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="muted" style={{ fontSize: 13 }}>
                      {own.length ? own[0].label : "нет аккаунта"}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <section className="panel series-panel">
          <h3>Правила из разговоров и сводка</h3>
          <label className="field">
            <span>Правило из чата проекта</span>
            <select className="input" value={draft.auto_rules} onChange={(e) => patch({ auto_rules: e.target.value as "confirm" | "auto" })}>
              <option value="confirm">сначала спросить «Записать правило?»</option>
              <option value="auto">записывать сразу, если однозначно</option>
            </select>
          </label>
          <div className="series-row">
            <label className="check">
              <input type="checkbox" checked={draft.digest.enabled} onChange={(e) => patch({ digest: { ...draft.digest, enabled: e.target.checked } })} />
              Сводка недели в Telegram
            </label>
            <select className="input" style={{ width: "auto" }} value={draft.digest.weekday} onChange={(e) => patch({ digest: { ...draft.digest, weekday: Number(e.target.value) } })}>
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={i}>
                  {d}
                </option>
              ))}
            </select>
            <input
              className="input series-num"
              type="number"
              min={0}
              max={23}
              value={draft.digest.hour}
              onChange={(e) => patch({ digest: { ...draft.digest, hour: Number(e.target.value) } })}
            />
            <span className="muted">ч</span>
          </div>
          <label className="field">
            <span>Карточка без ответа протухает через, ч (0 — никогда)</span>
            <input className="input series-num" type="number" min={0} value={draft.expire_hours} onChange={(e) => patch({ expire_hours: Number(e.target.value) })} />
          </label>
          <button className="btn primary" style={{ justifySelf: "start" }} disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </button>
        </section>
      </div>
      <div className="series-col">
        <VodWatchPanel projectId={projectId} />
        <ScheduleList projectId={projectId} />
        <PendingList projectId={projectId} />
        <Digest projectId={projectId} />
        <Feed projectId={projectId} />
      </div>
    </div>
  );
}

function ScheduleList({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const schedule = useQuery({ queryKey: apKeys.schedule(projectId), queryFn: () => autopilotApi.schedule(projectId), refetchInterval: 30000 });
  const [moving, setMoving] = useState<{ id: number; at: string } | null>(null);
  const act = useMutation({
    mutationFn: ({ id, action, at }: { id: number; action: "move" | "next_slot" | "cancel"; at?: string }) =>
      autopilotApi.reschedule(id, action, at),
    onSuccess: () => {
      setMoving(null);
      qc.invalidateQueries({ queryKey: apKeys.schedule(projectId) });
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });
  const data = schedule.data;
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Ближайшие публикации</h3>
        {data ? <span className="muted">{data.timezone}</span> : null}
      </div>
      {schedule.isLoading ? <Loading /> : null}
      {data && !data.upcoming.length ? <p className="muted">В расписании пусто.</p> : null}
      <ul className="series-list">
        {data?.upcoming.map((u) => (
          <li key={u.id}>
            <div className="series-li-main">
              <strong>{u.scheduled_local || "—"}</strong>
              <span>{u.title}</span>
              <span className="chip">{PUBLISH_STATUS_LABEL[u.publish_status] ?? u.publish_status}</span>
              <span className="muted">{u.platforms.map((p) => PLATFORM_LABEL[p] ?? p).join(", ")}</span>
            </div>
            {u.publish_error ? <div style={{ color: "var(--danger)", fontSize: 12.5 }}>{u.publish_error}</div> : null}
            {u.publish_status === "scheduled" || u.publish_status === "failed" ? (
              <div className="series-row" style={{ marginTop: 6 }}>
                {moving?.id === u.id ? (
                  <>
                    <input className="input" type="datetime-local" value={moving.at} onChange={(e) => setMoving({ id: u.id, at: e.target.value })} />
                    <button className="btn primary sm" disabled={!moving.at} onClick={() => act.mutate({ id: u.id, action: "move", at: moving.at.replace("T", " ") })}>
                      Перенести
                    </button>
                  </>
                ) : (
                  <button className="btn sm" onClick={() => setMoving({ id: u.id, at: (u.scheduled_local || "").replace(" ", "T") })}>
                    Перенести…
                  </button>
                )}
                <button className="btn sm" onClick={() => act.mutate({ id: u.id, action: "next_slot" })}>
                  В ближайший слот
                </button>
                <button className="btn ghost sm" onClick={() => act.mutate({ id: u.id, action: "cancel" })}>
                  Отменить
                </button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      {data?.free_slots.length ? (
        <p className="muted series-hint">Свободно: {data.free_slots.map((s) => s.local.slice(5)).join(" · ")}</p>
      ) : null}
    </section>
  );
}

function PendingList({ projectId }: { projectId: number }) {
  const list = useQuery({
    queryKey: apKeys.approvals(`pending:${projectId}`),
    queryFn: () => autopilotApi.approvals({ project_id: projectId, status: "pending" }),
    refetchInterval: 20000,
  });
  if (!list.data?.length) return null;
  return (
    <section className="series-panel">
      <h3>Ждут одобрения ({list.data.length})</h3>
      <div className="approval-list approval-list--compact">
        {list.data.slice(0, 5).map((a) => (
          <ApprovalCard key={a.id} approval={a} />
        ))}
      </div>
    </section>
  );
}

function Digest({ projectId }: { projectId: number }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const digest = useQuery({ queryKey: apKeys.digest(projectId), queryFn: () => autopilotApi.digest(projectId), enabled: open });
  const send = useMutation({
    mutationFn: () => autopilotApi.sendDigest(projectId),
    onSuccess: (r) => (r.sent ? toast.success("Сводка ушла в Telegram") : toast.push("Telegram не подключён — сводка только здесь", "info")),
    onError: (e) => toast.error(errText(e, "Не отправилось")),
  });
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>Сводка недели</h3>
        <div className="series-row">
          <button className="btn sm" onClick={() => setOpen(!open)}>
            {open ? "Скрыть" : "Показать"}
          </button>
          <button className="btn sm" disabled={send.isPending} onClick={() => send.mutate()}>
            В Telegram
          </button>
        </div>
      </div>
      {open ? digest.isLoading ? <Loading /> : <pre className="approval-digest">{digest.data?.text}</pre> : null}
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Чат проекта
// --------------------------------------------------------------------------- //

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

  return (
    <div className="series-grid">
      <section className="panel series-panel project-chat">
        {chat.data?.thread_id ? (
          <ChatConversation
            threadId={chat.data.thread_id}
            compact
            header={(view) => (
              <div className="agent-head project-chat-head">
                <span className={`agent-dot${view?.running ? " on" : ""}`} />
                <strong>Агент проекта</strong>
                {chat.data && !chat.data.ai_available ? <span className="chip series-review">ИИ недоступен</span> : null}
                <Link className="btn ghost sm" style={{ marginLeft: "auto" }} to={`/chat/${chat.data!.thread_id}`}>
                  Открыть в «Чате»
                </Link>
              </div>
            )}
          />
        ) : (
          <Loading />
        )}
      </section>
      <section className="panel series-panel">
        <h3>Как это работает</h3>
        <p className="muted series-hint">
          Агент видит бриф, действующие правила, статистику, гипотезы, очередь одобрения и расписание проекта. Длинная
          переписка сжимается в резюме.
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
  );
}
