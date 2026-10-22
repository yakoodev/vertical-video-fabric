import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import { jobsApi } from "@/api/jobs";
import { qk } from "@/api/keys";
import { formatCount, formatSnapshotTime, statsApi, sumKnown, type TargetStats } from "@/api/stats";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";

/** Числа площадок: при открытой странице, пока идёт сбор — опрашиваем чаще. */
function usePublicationStats(enabled = true) {
  return useQuery({
    queryKey: qk.publicationStats,
    queryFn: statsApi.publications,
    enabled,
    refetchInterval: (q) => (q.state.data?.status.running ? 5000 : 60000),
  });
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
function StatsCell({ stats }: { stats: (TargetStats | undefined)[] }) {
  const known = stats.filter((s): s is TargetStats => Boolean(s));
  const views = sumKnown(known.map((s) => s.views));
  const likes = sumKnown(known.map((s) => s.likes));
  const captured = known.map((s) => s.captured_at).filter(Boolean).sort().pop();
  const errors = known.map((s) => s.error).filter(Boolean);
  if (views == null && likes == null) {
    return (
      <span className="muted" title={errors.join("\n") || "Статистику ещё не собирали"}>
        {errors.length ? "нет данных" : "—"}
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
    </span>
  );
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
            <Link to="/publications" className="btn ghost sm">
              ← К списку
            </Link>
          </>
        }
      />
      <div className="panel" style={{ display: "flex", gap: 14, alignItems: "center", marginBottom: 16 }}>
        <Badge status={job.status} />
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
                  <td style={{ fontVariantNumeric: "tabular-nums" }}>{formatCount(st?.views)}</td>
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

export function PublicationsPage() {
  const { jobId } = useParams();
  const query = useQuery({ queryKey: qk.jobs, queryFn: jobsApi.list, enabled: !jobId });
  const stats = usePublicationStats(!jobId);

  if (jobId) return <JobDetail jobId={jobId} />;

  return (
    <>
      <PageHead
        title="Публикации"
        sub="Очередь задач публикации и что из опубликованного зашло"
        actions={query.data?.length ? <RefreshStatsButton /> : null}
      />
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
              <Link className="btn primary" to="/clips">
                Открыть клипы
              </Link>
              <Link className="btn" to="/accounts">
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
                    <Link to={`/publications/${job.id}`}>{job.id}</Link>
                  </td>
                  <td>
                    <Badge status={job.status} />
                  </td>
                  <td>
                    <Link to={`/publications/${job.id}`}>{job.title || "—"}</Link>
                  </td>
                  <td>{job.targets?.length ?? 0}</td>
                  <td>
                    <StatsCell stats={(job.targets ?? []).map((t) => stats.data?.targets[String(t.id)])} />
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
