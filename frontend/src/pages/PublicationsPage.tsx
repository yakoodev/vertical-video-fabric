import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import { jobsApi } from "@/api/jobs";
import type { Job } from "@/api/types";
import { qk } from "@/api/keys";
import { formatCount, formatSnapshotTime, statsApi, sumKnown, type PostAlert, type TargetStats, type ZeroViewsSettings } from "@/api/stats";
import { usePublicationStats } from "@/components/StatsBadge";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";
import { useModuleOn } from "@/hooks/useModules";

/** Значок тревоги «0 просмотров» (app/zero_views.py) — ссылка на карточку «внимание». */
function AlertMark({ alerts }: { alerts: (PostAlert | undefined)[] }) {
  const open = alerts.find((a) => a && a.status === "open");
  if (!open) return null;
  return (
    <Link
      to={open.approval_id ? `/inbox?tab=approve&id=${open.approval_id}` : "/inbox?tab=approve"}
      className="chip stats-badge stats-badge--alert"
      style={{ display: "inline-block", marginTop: 3 }}
      title="Мало просмотров после публикации — причины и чек-лист в карточке"
    >
      ⚠ {formatCount(open.views)} за {open.hours} ч
    </Link>
  );
}

/** Настройка тревоги: через сколько часов и при скольких просмотрах. */
function ZeroViewsPanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const statsOn = useModuleOn("platform_stats");
  const q = useQuery({ queryKey: ["stats", "zero-views"], queryFn: statsApi.zeroViews, enabled: statsOn });
  const [draft, setDraft] = useState<ZeroViewsSettings | null>(null);
  useEffect(() => {
    if (q.data) setDraft(q.data);
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => statsApi.saveZeroViews(draft!),
    onSuccess: (cfg) => {
      qc.setQueryData(["stats", "zero-views"], cfg);
      toast.success("Сохранено");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не сохранилось"),
  });
  if (!statsOn || !draft) return null;
  return (
    <div className="panel" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 16 }}>
      <label className="check">
        <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
        Тревога «0 просмотров»
      </label>
      <span className="muted">через</span>
      <input
        className="input series-num"
        style={{ width: 70 }}
        type="number"
        min={0.25}
        step={0.5}
        value={draft.hours}
        onChange={(e) => setDraft({ ...draft, hours: Number(e.target.value) })}
      />
      <span className="muted">ч, если просмотров не больше</span>
      <input
        className="input series-num"
        style={{ width: 70 }}
        type="number"
        min={0}
        value={draft.threshold}
        onChange={(e) => setDraft({ ...draft, threshold: Number(e.target.value) })}
      />
      <button className="btn sm" disabled={save.isPending} onClick={() => save.mutate()}>
        Сохранить
      </button>
      <span className="muted" style={{ fontSize: 12.5 }}>
        Карточка «Внимание» в «Одобрении» и в Telegram: вероятные причины и что проверить.
      </span>
    </div>
  );
}

/** Кнопка «Обновить статистику»: сбор идёт в фоне, по одной ссылке с паузой. */
function RefreshStatsButton() {
  const qc = useQueryClient();
  const toast = useToast();
  const stats = usePublicationStats();
  const running = Boolean(stats.data?.status.running);
  const refresh = useMutation({
    mutationFn: statsApi.refresh,
    onSuccess: (r) => {
      toast.success(r.started ? "Собираю статистику — по одной ссылке, с паузой" : "Сбор уже идёт");
      qc.invalidateQueries({ queryKey: qk.publicationStats });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить сбор статистики"),
  });
  const statsOn = useModuleOn("platform_stats");
  if (!statsOn) return null;
  return (
    <button
      className="btn sm"
      disabled={running || refresh.isPending}
      onClick={() => refresh.mutate()}
      title="Просмотры и лайки с площадок. Сам сервис обновляет их раз в несколько часов."
    >
      {running ? "Собираю статистику…" : "Обновить статистику"}
    </button>
  );
}

/** Ячейка «просмотры / лайки» с датой снимка; ошибка последней попытки — в подсказке. */
function StatsCell({ stats, alerts = [] }: { stats: (TargetStats | undefined)[]; alerts?: (PostAlert | undefined)[] }) {
  const known = stats.filter((s): s is TargetStats => Boolean(s));
  const views = sumKnown(known.map((s) => s.views));
  const likes = sumKnown(known.map((s) => s.likes));
  const captured = known.map((s) => s.captured_at).filter(Boolean).sort().pop();
  const errors = known.map((s) => s.error).filter(Boolean);
  if (views == null && likes == null) {
    return (
      <span className="muted" title={errors.join("\n") || "Статистику ещё не собирали"}>
        {errors.length ? "нет данных" : "—"}
        <AlertMark alerts={alerts} />
      </span>
    );
  }
  return (
    <span title={errors.join("\n") || undefined} style={{ fontVariantNumeric: "tabular-nums" }}>
      {formatCount(views)} · {formatCount(likes)}
      {captured ? (
        <span className="muted" style={{ display: "block", fontSize: 11.5 }}>
          на {formatSnapshotTime(captured)}
        </span>
      ) : null}
      <AlertMark alerts={alerts} />
    </span>
  );
}

/** Поставил агент сам, по настройке проекта «Публиковать самому». */
function MadeByAgent({ job }: { job: Job }) {
  return job.made_by === "agent" ? (
    <span className="chip" title="Поставил агент сам: настройка проекта «Публиковать самому»">Агент</span>
  ) : null;
}

function JobDetail({ jobId }: { jobId: string }) {
  const query = useQuery({ queryKey: qk.job(jobId), queryFn: () => jobsApi.get(jobId) });
  const stats = usePublicationStats();
  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const job = query.data!;

  return (
    <>
      <PageHead
        title={job.title || `Публикация #${job.id}`}
        sub={`Создано ${job.created_at} · ${job.privacy}`}
        actions={
          <>
            <RefreshStatsButton />
            <Link to="/pubs" className="btn ghost sm">
              ← К списку
            </Link>
          </>
        }
      />
      <div className="panel" style={{ display: "flex", gap: 14, alignItems: "center", marginBottom: 16 }}>
        <Badge status={job.status} />
        <MadeByAgent job={job} />
        {job.scheduled_at ? <span className="muted">по расписанию: {job.scheduled_at}</span> : null}
        {job.error ? <span style={{ color: "var(--danger)" }}>{job.error}</span> : null}
      </div>
      <div className="panel">
        <table className="table">
          <thead>
            <tr>
              <th>Платформа</th>
              <th>Аккаунт</th>
              <th>Статус</th>
              <th>Ссылка</th>
              <th>Просмотры</th>
              <th>Лайки</th>
              <th>Комм.</th>
              <th>Снимок</th>
              <th>Ошибка</th>
            </tr>
          </thead>
          <tbody>
            {job.targets.map((t) => {
              const st = stats.data?.targets[String(t.id)];
              return (
                <tr key={t.id}>
                  <td>{t.platform}</td>
                  <td>{t.account_label}</td>
                  <td>
                    <Badge status={t.status} />
                  </td>
                  <td>
                    {t.remote_url ? (
                      <a href={t.remote_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
                        открыть ↗
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>
                    {formatCount(st?.views)}
                    <AlertMark alerts={[stats.data?.alerts?.[String(t.id)]]} />
                  </td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{formatCount(st?.likes)}</td>
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{formatCount(st?.comments)}</td>
                  <td className="muted" title={st?.error || undefined}>
                    {st?.captured_at ? formatSnapshotTime(st.captured_at) : st?.error ? "нет данных" : "—"}
                  </td>
                  <td style={{ color: t.error ? "var(--danger)" : "inherit" }}>{t.error || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function PublicationsPage({ embedded }: { embedded?: boolean } = {}) {
  const { jobId } = useParams();
  const query = useQuery({ queryKey: qk.jobs, queryFn: jobsApi.list, enabled: !jobId });
  const stats = usePublicationStats(!jobId);

  if (jobId) return <JobDetail jobId={jobId} />;

  return (
    <>
      {embedded ? (
        query.data?.length ? <div className="page-acts"><RefreshStatsButton /></div> : null
      ) : (
        <PageHead
          title="Публикации"
          sub="Очередь задач публикации и что из опубликованного зашло"
          actions={query.data?.length ? <RefreshStatsButton /> : null}
        />
      )}
      {query.data?.length ? <ZeroViewsPanel /> : null}
      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !query.data?.length ? (
        <EmptyState
          icon="📡"
          title="Публикаций пока нет"
          hint="Опубликуйте клип, чтобы поставить задачу в очередь"
          actions={
            <>
              <Link className="btn primary" to="/pubs?tab=ready">
                Готовые ролики
              </Link>
              <Link className="btn" to="/pubs?tab=accounts">
                Добавить аккаунт
              </Link>
            </>
          }
        />
      ) : (
        <div className="panel">
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>Статус</th>
                <th>Заголовок</th>
                <th>Аккаунтов</th>
                <th>Просмотры · лайки</th>
                <th>Расписание</th>
                <th>Создано</th>
              </tr>
            </thead>
            <tbody>
              {query.data.map((job) => (
                <tr key={job.id} style={{ cursor: "pointer" }}>
                  <td>
                    <Link to={`/pubs/${job.id}`}>{job.id}</Link>
                  </td>
                  <td>
                    <Badge status={job.status} />
                  </td>
                  <td>
                    <Link to={`/pubs/${job.id}`}>{job.title || "—"}</Link> <MadeByAgent job={job} />
                  </td>
                  <td>{job.targets?.length ?? 0}</td>
                  <td>
                    <StatsCell
                      stats={(job.targets ?? []).map((t) => stats.data?.targets[String(t.id)])}
                      alerts={(job.targets ?? []).map((t) => stats.data?.alerts?.[String(t.id)])}
                    />
                  </td>
                  <td>{job.scheduled_at || "—"}</td>
                  <td className="muted">{job.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
