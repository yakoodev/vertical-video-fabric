import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { jobsApi } from "@/api/jobs";
import { qk } from "@/api/keys";
import { Badge, EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";

function JobDetail({ jobId }: { jobId: string }) {
  const query = useQuery({ queryKey: qk.job(jobId), queryFn: () => jobsApi.get(jobId) });
  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const job = query.data!;

  return (
    <>
      <PageHead
        title={job.title || `Публикация #${job.id}`}
        sub={`Создано ${job.created_at} · ${job.privacy}`}
        actions={
          <Link to="/publications" className="btn ghost sm">
            ← К списку
          </Link>
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
              <th>Ошибка</th>
            </tr>
          </thead>
          <tbody>
            {job.targets.map((t) => (
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
                <td style={{ color: t.error ? "var(--danger)" : "inherit" }}>{t.error || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function PublicationsPage() {
  const { jobId } = useParams();
  const query = useQuery({ queryKey: qk.jobs, queryFn: jobsApi.list, enabled: !jobId });

  if (jobId) return <JobDetail jobId={jobId} />;

  return (
    <>
      <PageHead title="Публикации" sub="Очередь задач публикации в YouTube и TikTok" />
      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !query.data?.length ? (
        <EmptyState icon="📡" title="Публикаций пока нет" hint="Опубликуйте клип, чтобы поставить задачу в очередь" />
      ) : (
        <div className="panel">
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>Статус</th>
                <th>Заголовок</th>
                <th>Аккаунтов</th>
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
