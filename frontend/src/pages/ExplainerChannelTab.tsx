import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PLATFORM_LABEL, WEEKDAYS } from "@/api/autopilot";
import { accountsApi } from "@/api/accounts";
import { ApiError } from "@/api/client";
import {
  channelApi,
  channelKey,
  type ChannelSettings,
  type ChannelTopic,
  type ChannelView,
  type TopicStatus,
} from "@/api/explainerChannel";
import { TONES } from "@/api/explainer";
import { qk } from "@/api/keys";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";

/**
 * «Канал» (app/explainer_channel.py): маскот ведёт канал объяснялок. Привязка маскота и ниша, автопилот
 * вкл/выкл, расписание (пишется в автопилот проекта), очередь тем: предложить (Gemini), добавить свои,
 * взять/отклонить (причина отказа — правило), что делается, что на одобрении и в расписании.
 */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const PLATFORMS = ["youtube", "tiktok", "instagram"];
const GROUPS: { title: string; statuses: TopicStatus[]; hint?: string }[] = [
  { title: "Ждут решения", statuses: ["proposed"], hint: "Предложил Gemini или агент. Возьми или отклони — причину запомню." },
  { title: "В очереди", statuses: ["approved"], hint: "Ролики по ним начнутся сами, когда в расписании будут свободные слоты." },
  { title: "В работе", statuses: ["producing", "review", "scheduled"] },
  { title: "Не вышло", statuses: ["failed"] },
  { title: "Вышло", statuses: ["published"] },
  { title: "Отклонено и снято", statuses: ["rejected", "dropped"] },
];

export function ExplainerChannelTab({ projectId }: { projectId: number }) {
  const q = useQuery({
    queryKey: channelKey(projectId),
    queryFn: () => channelApi.get(projectId),
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
    refetchInterval: (query) => {
      const v = query.state.data as ChannelView | undefined;
      return v?.counts && (v.counts.producing || v.counts.review) ? 10000 : 60000;
    },
  });
  if (q.isLoading) return <Loading />;
  if (q.data?.no_channel || (q.error instanceof ApiError && q.error.status === 404)) return <ChannelSetup projectId={projectId} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const v = q.data;
  return (
    <div className="series-grid">
      <div className="series-col">
        <ChannelHead v={v} />
        <TopicBacklog v={v} />
      </div>
      <div className="series-col">
        <ChannelSchedule v={v} />
        <ChannelSettingsPanel v={v} />
      </div>
    </div>
  );
}

function useSave(projectId: number) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (patch: Parameters<typeof channelApi.save>[1]) => channelApi.save(projectId, patch),
    onSuccess: (view) => {
      qc.setQueryData(channelKey(projectId), view);
      qc.invalidateQueries({ queryKey: ["channel-projects", String(projectId)] });
      toast.success("Сохранено");
    },
    onError: (e) => toast.error(errText(e, "Не сохранилось")),
  });
}

function ChannelSetup({ projectId }: { projectId: number }) {
  const mascots = useQuery({ queryKey: ["explainer", "mascot-choices"], queryFn: channelApi.mascots });
  const [mascotId, setMascotId] = useState<number | "">("");
  const [niche, setNiche] = useState("");
  const save = useSave(projectId);
  useEffect(() => {
    const own = mascots.data?.find((m) => m.project_id === projectId) ?? mascots.data?.[0];
    if (own && mascotId === "") setMascotId(own.id);
  }, [mascots.data, projectId, mascotId]);
  return (
    <section className="panel series-panel" style={{ maxWidth: 640 }}>
      <h3>Канал объяснялок</h3>
      <p className="muted series-hint">
        Маскот ведёт канал сам: держит очередь тем, делает ролики заранее к слотам расписания и присылает каждый
        готовый ролик тебе на одобрение (здесь, в «Одобрении» и в Telegram). Без «Публиковать» ничего не выходит.
      </p>
      {mascots.isLoading ? <Loading /> : null}
      {mascots.data && !mascots.data.length ? (
        <p className="muted">Маскотов ещё нет — заведи во вкладке «Объяснялки» (или импортируй пилот «Ух»).</p>
      ) : null}
      {mascots.data?.length ? (
        <>
          <label className="field">
            <span>Маскот</span>
            <select className="input" value={mascotId} onChange={(e) => setMascotId(Number(e.target.value))}>
              {mascots.data.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} · {m.project_name}
                  {m.has_sheet ? "" : " (нет листа)"}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>О чём канал (ниша)</span>
            <textarea
              className="input"
              rows={2}
              placeholder="сон, привычки и мозг простыми словами"
              value={niche}
              onChange={(e) => setNiche(e.target.value)}
            />
          </label>
          <button
            className="btn primary"
            disabled={!mascotId || save.isPending}
            onClick={() => save.mutate({ mascot_id: Number(mascotId), niche })}
          >
            Завести канал
          </button>
        </>
      ) : null}
    </section>
  );
}

function ChannelHead({ v }: { v: ChannelView }) {
  const save = useSave(v.project.id);
  const qc = useQueryClient();
  const toast = useToast();
  const [niche, setNiche] = useState(v.channel.niche);
  useEffect(() => setNiche(v.channel.niche), [v.channel.niche]);
  const run = useMutation({
    mutationFn: () => channelApi.run(v.project.id),
    onSuccess: (r) => {
      toast.success(r.started.length ? `Начал роликов: ${r.started.length}` : "Проход сделан — новых роликов не нужно");
      qc.invalidateQueries({ queryKey: channelKey(v.project.id) });
    },
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });
  const cap = v.capacity;
  const m = v.mascot;
  return (
    <section className="panel series-panel">
      <div className="series-row" style={{ alignItems: "flex-start", gap: 14 }}>
        {m?.sheet_url ? (
          <img src={m.sheet_url} alt="" style={{ width: 96, borderRadius: 8, background: m.bg_color }} />
        ) : null}
        <div style={{ flex: 1, display: "grid", gap: 4 }}>
          <h3 style={{ margin: 0 }}>{m ? `Ведущий — ${m.name}` : "Маскот не выбран"}</h3>
          {m ? (
            <span className="muted" style={{ fontSize: 13 }}>
              голос {m.voice.provider} · {m.voice.voice} · поз готово {m.poses_ready} из {m.poses}
              {m.project_id !== v.project.id ? (
                <>
                  {" "}
                  · <Link to={`/p/${m.project_id}`}>библиотека маскота</Link>
                </>
              ) : null}
            </span>
          ) : null}
          <label className="check" style={{ marginTop: 4 }}>
            <input type="checkbox" checked={v.channel.enabled} onChange={(e) => save.mutate({ enabled: e.target.checked })} />
            Автопилот канала: сам делает ролики к слотам и шлёт на одобрение
          </label>
        </div>
      </div>
      <label className="field">
        <span>О чём канал (ниша)</span>
        <textarea className="input" rows={2} value={niche} onChange={(e) => setNiche(e.target.value)} />
      </label>
      {niche !== v.channel.niche ? (
        <button className="btn sm" style={{ justifySelf: "start" }} onClick={() => save.mutate({ niche })}>
          Сохранить нишу
        </button>
      ) : null}
      <p className="muted series-hint">
        На {cap.horizon_days} дн. вперёд: свободных слотов {cap.free_slots}, роликов делается {cap.in_flight}, на
        одобрении {cap.in_review}
        {cap.need ? ` — нужно ещё ${cap.need}` : " — хватает"}.
      </p>
      {v.warnings.length ? (
        <ul className="series-list" style={{ color: "var(--danger)", fontSize: 13 }}>
          {v.warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      ) : null}
      <div className="series-row">
        <button className="btn sm" disabled={run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? "Прохожу…" : "Прогнать сейчас"}
        </button>
        <Link className="btn ghost sm" to={`/inbox?tab=approve&project=${v.project.id}`}>
          Одобрение канала →
        </Link>
      </div>
    </section>
  );
}

function TopicBacklog({ v }: { v: ChannelView }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: channelKey(v.project.id) });
  const add = useMutation({
    mutationFn: () => channelApi.addTopics(v.project.id, text.split("\n").map((t) => t.trim()).filter(Boolean)),
    onSuccess: (r) => {
      const dup = r.duplicates.length ? ` · дубли пропущены: ${r.duplicates.map((d) => `«${d.topic}» ≈ «${d.like}»`).join(", ")}` : "";
      toast.success(`Добавлено тем: ${r.added.length}${dup}`);
      setText("");
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не добавилось")),
  });
  const propose = useMutation({
    mutationFn: () => channelApi.propose(v.project.id),
    onSuccess: (r) => {
      toast.success(r.proposed.length ? `Gemini предложил тем: ${r.proposed.length}` : "Новых тем без повторов не нашлось");
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Gemini не ответил")),
  });
  return (
    <section className="panel series-panel">
      <h3>Темы</h3>
      <div className="series-row" style={{ alignItems: "flex-start" }}>
        <textarea
          className="input"
          style={{ flex: 1 }}
          rows={2}
          placeholder="Свои темы — по одной в строке (сразу в очередь)"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div style={{ display: "grid", gap: 6 }}>
          <button className="btn primary sm" disabled={!text.trim() || add.isPending} onClick={() => add.mutate()}>
            Добавить
          </button>
          <button className="btn sm" disabled={propose.isPending} onClick={() => propose.mutate()} title="Gemini по нише, правилам и цифрам канала">
            {propose.isPending ? "Думаю…" : "✨ Предложить"}
          </button>
        </div>
      </div>
      {GROUPS.map((g) => {
        const items = v.topics.filter((t) => g.statuses.includes(t.status));
        if (!items.length) return null;
        return (
          <div key={g.title} className="series-section">
            <strong>
              {g.title} <span className="muted">{items.length}</span>
            </strong>
            {g.hint ? <p className="muted series-hint">{g.hint}</p> : null}
            <ul className="series-list">
              {items.map((t) => (
                <TopicRow key={t.id} t={t} onChange={refresh} />
              ))}
            </ul>
          </div>
        );
      })}
      {!v.topics.length ? <p className="muted">Тем пока нет — добавь свои или попроси Gemini.</p> : null}
    </section>
  );
}

function TopicRow({ t, onChange }: { t: ChannelTopic; onChange: () => void }) {
  const toast = useToast();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const decide = useMutation({
    mutationFn: ({ action, comment }: { action: "approve" | "reject" | "retry"; comment?: string }) =>
      channelApi.decide(t.id, action, comment),
    onSuccess: (r) => {
      const learned = r.learned?.rule ? ` · запомнил: «${r.learned.rule.text}»` : r.learned?.text ? ` · спрошу: «${r.learned.text}»` : "";
      toast.success(`${r.topic.status_label}${learned}`);
      setRejecting(false);
      setReason("");
      onChange();
    },
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });
  const remove = useMutation({
    mutationFn: () => channelApi.remove(t.id),
    onSuccess: onChange,
    onError: (e) => toast.error(errText(e, "Не удалилось")),
  });
  const up = useMutation({ mutationFn: () => channelApi.edit(t.id, { priority: t.priority + 10 }), onSuccess: onChange });
  const card = t.approval;
  return (
    <li style={{ display: "grid", gap: 4 }}>
      <div className="series-row" style={{ justifyContent: "space-between" }}>
        <span>
          <b>{t.topic}</b>
          {t.angle ? <span className="muted"> — {t.angle}</span> : null}
          {t.origin !== "owner" ? <span className="chip" style={{ marginLeft: 6 }}>{t.origin}</span> : null}
        </span>
        <span className="chip">{t.status_label}</span>
      </div>
      {t.status === "producing" && t.video ? (
        <span className="muted" style={{ fontSize: 13 }}>
          ролик #{t.video.id}: {t.video.status_label}
          {t.video.status === "awaiting_approval" ? " — разреши траты на новые позы во вкладке «Объяснялки»" : ""}
        </span>
      ) : null}
      {card && ["review", "scheduled", "published"].includes(t.status) ? (
        <span className="muted" style={{ fontSize: 13 }}>
          <Link to={`/inbox?tab=approve&id=${card.id}`}>карточка #{card.id}</Link>
          {card.scheduled_local ? ` · ${card.scheduled_local}` : ""}
          {card.publish_error ? <span style={{ color: "var(--danger)" }}> · {card.publish_error}</span> : null}
        </span>
      ) : null}
      {t.feedback && t.status !== "published" ? <span className="muted" style={{ fontSize: 13 }}>замечание: «{t.feedback}»</span> : null}
      {t.reason ? <span className="muted" style={{ fontSize: 13 }}>{t.reason}</span> : null}
      <div className="series-row">
        {t.status === "proposed" ? (
          <>
            <button className="btn primary sm" disabled={decide.isPending} onClick={() => decide.mutate({ action: "approve" })}>
              ✅ Взять
            </button>
            <button className="btn ghost sm" onClick={() => setRejecting(!rejecting)}>
              🗑 Отклонить
            </button>
          </>
        ) : null}
        {t.status === "approved" ? (
          <>
            <button className="btn ghost sm" title="Раньше остальных" onClick={() => up.mutate()}>
              ↑ раньше
            </button>
            <button className="btn ghost sm" onClick={() => setRejecting(!rejecting)}>
              Убрать из очереди
            </button>
          </>
        ) : null}
        {t.status === "failed" || t.status === "dropped" ? (
          <button className="btn sm" disabled={decide.isPending} onClick={() => decide.mutate({ action: "retry" })}>
            🔁 Сделать заново
          </button>
        ) : null}
        {["proposed", "approved", "rejected", "failed", "dropped"].includes(t.status) ? (
          <button className="btn ghost sm" onClick={() => remove.mutate()}>
            ✕
          </button>
        ) : null}
      </div>
      {rejecting ? (
        <div className="series-row">
          <input
            className="input"
            style={{ flex: 1 }}
            autoFocus
            placeholder="Почему? Необязательно; если напишешь — запомню (например: не бери темы про деньги)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button className="btn sm" disabled={decide.isPending} onClick={() => decide.mutate({ action: "reject", comment: reason })}>
            Отклонить
          </button>
        </div>
      ) : null}
    </li>
  );
}

function ChannelSchedule({ v }: { v: ChannelView }) {
  const save = useSave(v.project.id);
  const accounts = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list });
  const slot = v.schedule.slots[0];
  const [times, setTimes] = useState((slot?.times ?? ["19:00"]).join(", "));
  const [days, setDays] = useState<number[]>(slot?.days ?? [0, 1, 2, 3, 4, 5, 6]);
  const [perDay, setPerDay] = useState(v.schedule.per_day.default || 1);
  const [timezone, setTimezone] = useState(v.schedule.timezone);
  const [platforms, setPlatforms] = useState<string[]>(v.schedule.platforms);
  const [acc, setAcc] = useState<Record<string, number[]>>(v.schedule.accounts);
  const more = v.schedule.slots.length > 1;
  return (
    <section className="panel series-panel">
      <h3>Расписание</h3>
      <p className="muted series-hint">
        Одобренный ролик встаёт в ближайший свободный слот. Это расписание автопилота проекта — подробнее во вкладке
        «Автопилот».
      </p>
      {more ? <p className="muted series-hint">⚠ У проекта несколько слотов — сохранение здесь оставит один.</p> : null}
      <label className="field">
        <span>Во сколько (через запятую)</span>
        <input className="input" value={times} onChange={(e) => setTimes(e.target.value)} placeholder="19:00" />
      </label>
      <div className="series-row">
        {WEEKDAYS.map((d, day) => (
          <label key={d} className="check">
            <input
              type="checkbox"
              checked={days.includes(day)}
              onChange={(e) => setDays(e.target.checked ? [...days, day].sort() : days.filter((x) => x !== day))}
            />
            {d}
          </label>
        ))}
      </div>
      <div className="series-row">
        <label className="field" style={{ margin: 0 }}>
          <span>Не больше в день</span>
          <input className="input series-num" type="number" min={1} value={perDay} onChange={(e) => setPerDay(Number(e.target.value || 1))} />
        </label>
        <label className="field" style={{ margin: 0, flex: 1 }}>
          <span>Пояс</span>
          <input className="input" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </label>
      </div>
      <div className="series-section">
        <strong>Куда</strong>
        {PLATFORMS.map((p) => {
          const own = (accounts.data ?? []).filter((a) => a.platform === p);
          return (
            <div key={p} className="series-row">
              <label className="check" style={{ minWidth: 110 }}>
                <input
                  type="checkbox"
                  checked={platforms.includes(p)}
                  onChange={(e) => setPlatforms(e.target.checked ? [...platforms, p] : platforms.filter((x) => x !== p))}
                />
                {PLATFORM_LABEL[p]}
              </label>
              {own.length > 1 ? (
                <select
                  className="input"
                  style={{ width: "auto" }}
                  value={acc[p]?.[0] ?? ""}
                  onChange={(e) => setAcc({ ...acc, [p]: e.target.value ? [Number(e.target.value)] : [] })}
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
      <button
        className="btn primary sm"
        style={{ justifySelf: "start" }}
        disabled={save.isPending}
        onClick={() =>
          save.mutate({
            schedule: {
              times: times.split(/[\s,;]+/).filter(Boolean),
              days,
              per_day: perDay,
              timezone,
              platforms,
              accounts: Object.fromEntries(Object.entries(acc).filter(([p, ids]) => platforms.includes(p) && ids.length)),
            },
          })
        }
      >
        Сохранить расписание
      </button>
      {v.schedule.upcoming.length ? (
        <div className="series-section">
          <strong>Ближайшие</strong>
          <ul className="series-list">
            {v.schedule.upcoming.map((u) => (
              <li key={u.id}>
                <span className="muted">{u.scheduled_local}</span> <Link to={`/inbox?tab=approve&id=${u.id}`}>{u.title}</Link>
                {u.publish_error ? <span style={{ color: "var(--danger)" }}> — {u.publish_error}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {v.schedule.free_slots.length ? (
        <p className="muted series-hint">Свободно: {v.schedule.free_slots.slice(0, 4).map((s) => s.local).join(", ")}</p>
      ) : null}
    </section>
  );
}

function ChannelSettingsPanel({ v }: { v: ChannelView }) {
  const save = useSave(v.project.id);
  const [draft, setDraft] = useState<ChannelSettings>(v.channel.config);
  useEffect(() => setDraft(v.channel.config), [v.channel.config]);
  const num = (key: keyof ChannelSettings, label: string, min: number, max: number) => (
    <label className="field" style={{ margin: 0 }}>
      <span>{label}</span>
      <input
        className="input series-num"
        type="number"
        min={min}
        max={max}
        value={draft[key] as number}
        onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })}
      />
    </label>
  );
  return (
    <section className="panel series-panel">
      <h3>Как делать</h3>
      <div className="series-row">
        {num("lead_days", "Готовить на дней вперёд", 1, 14)}
        {num("max_parallel", "Роликов сразу", 1, 5)}
        {num("duration", "Длина, с", 30, 60)}
      </div>
      <div className="series-row">
        {num("backlog_min", "Тем в запасе", 0, 50)}
        {num("propose_count", "Тем за раз", 1, 15)}
        {num("max_attempts", "Попыток «Переделать»", 1, 5)}
      </div>
      <label className="field">
        <span>Тон</span>
        <select className="input" value={draft.tone} onChange={(e) => setDraft({ ...draft, tone: e.target.value })}>
          {[...new Set([draft.tone, ...TONES])].map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={draft.auto_approve_topics}
          onChange={(e) => setDraft({ ...draft, auto_approve_topics: e.target.checked })}
        />
        Темы Gemini — сразу в очередь (каждый ролик всё равно на одобрение)
      </label>
      <p className="muted series-hint">
        Деньги — как у любой объяснялки: платны только новые позы (и озвучка Polza), одной карточкой с суммой.
      </p>
      <button className="btn sm" style={{ justifySelf: "start" }} disabled={save.isPending} onClick={() => save.mutate({ settings: draft })}>
        Сохранить
      </button>
    </section>
  );
}
