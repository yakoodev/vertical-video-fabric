import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PUB_PLATFORMS, pubPlatformLabel, shortDate, todayIso } from "@/api/clipMarks";
import { projectsApi, type NumbersInput, type StatsItem } from "@/api/projects";
import { formatCount, formatSnapshotTime } from "@/api/stats";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";

/** Вкладка «Статистика»: числа проекта, лучшие и худшие клипы, опубликованное руками. */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1).replace(".", ",")}%`);

export function ProjectStatsTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const stats = useQuery({ queryKey: qk.channelStats(projectId), queryFn: () => projectsApi.stats(projectId) });
  const pubs = useQuery({ queryKey: qk.channelPublications(projectId), queryFn: () => projectsApi.publications(projectId) });
  const brief = useQuery({ queryKey: qk.channelBrief(projectId), queryFn: () => projectsApi.brief(projectId) });
  const blank = () => ({ url: "", platform: "", account: "", clip: "", title: "", published_at: todayIso(), views: "", likes: "", retention: "" });
  const [form, setForm] = useState(blank);
  const [numbersFor, setNumbersFor] = useState<number | null>(null);
  const [numbers, setNumbers] = useState({ views: "", likes: "", retention: "" });

  const reload = () => {
    qc.invalidateQueries({ queryKey: qk.channelStats(projectId) });
    qc.invalidateQueries({ queryKey: qk.channelPublications(projectId) });
  };
  const num = (v: string) => (v.trim() ? Number(v.replace(",", ".")) : null);
  const toNumbers = (n: { views: string; likes: string; retention: string }): NumbersInput | undefined => {
    const out: NumbersInput = { views: num(n.views), likes: num(n.likes), avg_retention: num(n.retention) };
    return Object.values(out).some((v) => v != null) ? out : undefined;
  };
  const add = useMutation({
    mutationFn: () => {
      const statsIn = toNumbers({ views: form.views, likes: form.likes, retention: form.retention });
      return projectsApi.addPublication(projectId, {
        url: form.url.trim(),
        // Площадка не выбрана — по ссылке (как раньше).
        platform: form.platform || undefined,
        account: form.account.trim(),
        clip_plan_id: form.clip ? Number(form.clip) : null,
        title: form.title,
        published_at: form.published_at,
        stats: statsIn,
        fetch: !statsIn && Boolean(form.url.trim()),
      });
    },
    onSuccess: () => {
      toast.success("Публикация добавлена");
      setForm(blank());
      reload();
    },
    onError: (e) => toast.error(errText(e, "Не удалось добавить")),
  });
  const refresh = useMutation({
    mutationFn: (pubId: number) => projectsApi.refreshPublication(pubId),
    onSuccess: (res) => {
      if (!res.ok) toast.push(res.error || "Числа не получены", "info");
      reload();
    },
  });
  const saveNumbers = useMutation({
    mutationFn: () => projectsApi.publicationNumbers(numbersFor!, toNumbers(numbers) ?? {}),
    onSuccess: () => {
      setNumbersFor(null);
      reload();
    },
    onError: (e) => toast.error(errText(e, "Не удалось сохранить числа")),
  });
  const remove = useMutation({ mutationFn: (pubId: number) => projectsApi.removePublication(pubId), onSuccess: reload });

  if (stats.isLoading) return <Loading />;
  if (stats.isError || !stats.data) return <ErrorState error={stats.error} onRetry={() => stats.refetch()} />;
  const s = stats.data;
  const t = s.totals;
  return (
    <div className="series-col">
      <div className="series-tiles">
        <Tile label="Просмотры" value={formatCount(t.views)} />
        <Tile label="Медиана на клип" value={formatCount(t.median_views == null ? null : Math.round(t.median_views))} />
        <Tile label="Лайки" value={formatCount(t.likes)} />
        <Tile label="Удержание" value={pct(t.avg_retention)} />
        <Tile label="Публикаций" value={String(t.publications)} sub={`${t.items_with_stats} из ${t.items} с числами`} />
      </div>
      <p className="muted series-hint">{s.note}</p>
      <div className="series-grid">
        <TopList title="Лучшие" items={s.best} empty="Пока нет клипов с числами." />
        <TopList title="Худшие" items={s.worst} empty="Пока не с чем сравнивать — мало клипов с числами." />
      </div>
      <section className="panel series-panel">
        <h3>Выложено руками</h3>
        <p className="muted series-hint">
          Видео, опубликованное не через сервис. Ссылка необязательна: по ссылке YouTube и TikTok подтянут числа
          сами (тем же сбором, что «Публикации»), Instagram — только вручную. Один клип можно отметить сколько
          угодно раз; на экране клипа — блок «Где выложен».
        </p>
        <div className="series-row">
          <input className="input wide" placeholder="Ссылка (необязательно)" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
          <select className="input" aria-label="Площадка" value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}>
            <option value="">{form.url.trim() ? "Площадка по ссылке" : "Площадка…"}</option>
            {PUB_PLATFORMS.map((p) => (
              <option key={p.key} value={p.key}>{p.label}</option>
            ))}
          </select>
          <input className="input" placeholder="Аккаунт" value={form.account} onChange={(e) => setForm({ ...form, account: e.target.value })} />
          <select className="input" value={form.clip} onChange={(e) => setForm({ ...form, clip: e.target.value })}>
            <option value="">Без клипа (к проекту)</option>
            {(brief.data?.covered ?? []).map((c) => (
              <option key={c.clip_id} value={c.clip_id}>
                {c.title}
              </option>
            ))}
          </select>
        </div>
        <div className="series-row">
          <input className="input wide" placeholder="Название (необязательно)" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          <input className="input" type="date" value={form.published_at} onChange={(e) => setForm({ ...form, published_at: e.target.value })} />
          <input className="input series-num" placeholder="просмотры" value={form.views} onChange={(e) => setForm({ ...form, views: e.target.value })} />
          <input className="input series-num" placeholder="лайки" value={form.likes} onChange={(e) => setForm({ ...form, likes: e.target.value })} />
          <input className="input series-num" placeholder="удерж. %" value={form.retention} onChange={(e) => setForm({ ...form, retention: e.target.value })} />
          <button
            className="btn primary"
            disabled={(!form.url.trim() && !form.platform) || add.isPending}
            title={form.url.trim() || form.platform ? "" : "Нужна ссылка или площадка"}
            onClick={() => add.mutate()}
          >
            Добавить
          </button>
        </div>
        <ul className="series-list">
          {(pubs.data ?? []).map((p) => (
            <li key={p.id}>
              <div className="series-li-main">
                <span className="chip">{p.platform ? pubPlatformLabel(p.platform) : "ссылка"}</span>
                {p.url ? (
                  <a href={p.url} target="_blank" rel="noreferrer">
                    {p.title || p.clip_title || p.url}
                  </a>
                ) : (
                  <span>{p.title || p.clip_title || "без ссылки"}</span>
                )}
                {p.account ? <span className="muted">{p.account}</span> : null}
                <span className="mono">{formatCount(p.stats?.views ?? null)} просм.</span>
                {p.url ? (
                  <button className="btn ghost sm" title="Подтянуть числа с площадки" disabled={refresh.isPending} onClick={() => refresh.mutate(p.id)}>
                    ↻
                  </button>
                ) : null}
                <button className="btn ghost sm" onClick={() => setNumbersFor(numbersFor === p.id ? null : p.id)}>
                  Числа
                </button>
                <button className="btn ghost sm" title="Убрать" onClick={() => remove.mutate(p.id)}>
                  ✕
                </button>
              </div>
              <div className="muted series-li-sub">
                {p.published_at ? `${shortDate(p.published_at)} · ` : ""}
                {p.clip_title ? `клип «${p.clip_title}» · ` : ""}
                {p.stats?.captured_at ? `сняты ${formatSnapshotTime(p.stats.captured_at)} (${p.stats.origin === "manual" ? "вручную" : "с площадки"})` : "чисел нет"}
                {p.stats?.error ? ` · ${p.stats.error}` : ""}
              </div>
              {numbersFor === p.id ? (
                <div className="series-row">
                  <input className="input series-num" placeholder="просмотры" value={numbers.views} onChange={(e) => setNumbers({ ...numbers, views: e.target.value })} />
                  <input className="input series-num" placeholder="лайки" value={numbers.likes} onChange={(e) => setNumbers({ ...numbers, likes: e.target.value })} />
                  <input className="input series-num" placeholder="удерж. %" value={numbers.retention} onChange={(e) => setNumbers({ ...numbers, retention: e.target.value })} />
                  <button className="btn sm primary" disabled={!toNumbers(numbers) || saveNumbers.isPending} onClick={() => saveNumbers.mutate()}>
                    Сохранить
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="panel series-tile">
      <span className="muted">{label}</span>
      <strong>{value}</strong>
      {sub ? <small className="muted">{sub}</small> : null}
    </div>
  );
}

function TopList({ title, items, empty }: { title: string; items: StatsItem[]; empty: string }) {
  return (
    <section className="panel series-panel">
      <h3>{title}</h3>
      {!items.length ? <p className="muted">{empty}</p> : null}
      <ol className="series-list">
        {items.map((i) => (
          <li key={i.key} className="series-li-main">
            <span>{i.title || i.key}</span>
            <span className="mono">{formatCount(i.views)}</span>
            <span className="muted">{i.views_per_day != null ? `${formatCount(Math.round(i.views_per_day))}/день` : ""}</span>
            {i.tags.map((t) => (
              <span key={t} className="chip">
                #{t}
              </span>
            ))}
          </li>
        ))}
      </ol>
    </section>
  );
}
