import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { tasksApi } from "@/api/tasks";
import { qk } from "@/api/keys";
import { isActive } from "@/hooks/useActiveTasks";
import type { ActiveTask } from "@/api/types";
import { Badge, EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";

const KIND_RU: Record<string, string> = {
  job: "Публикация",
  clip: "Рендер",
  analysis: "Анализ",
  download: "Скачивание",
};
const KIND_ICO: Record<string, string> = { job: "📡", clip: "✂️", analysis: "🧠", download: "⬇️" };

type Filter = "all" | "active" | "failed" | ActiveTask["kind"];
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "Все" },
  { key: "active", label: "Активные" },
  { key: "failed", label: "С ошибкой" },
  { key: "download", label: "Скачивание" },
  { key: "analysis", label: "Анализ" },
  { key: "clip", label: "Рендер" },
  { key: "job", label: "Публикация" },
];

const FAILED = new Set(["failed", "error", "needs_reauth"]);

function parseTs(s?: string | null): number | null {
  if (!s) return null;
  const d = new Date(s.includes("T") ? s : s.replace(" ", "T") + "Z");
  const t = d.getTime();
  return Number.isNaN(t) ? null : t;
}

function fmtTime(s?: string | null): string {
  const t = parseTs(s);
  if (t == null) return "—";
  return new Date(t).toLocaleString("ru-RU", { hour12: false });
}

function fmtDuration(start?: string | null, end?: string | null): string {
  const a = parseTs(start);
  const b = parseTs(end);
  if (a == null || b == null) return "";
  const sec = Math.max(0, Math.round((b - a) / 1000));
  if (sec < 60) return `${sec}с`;
  const m = Math.floor(sec / 60);
  return `${m}м ${sec % 60}с`;
}

function taskLink(t: ActiveTask): string | null {
  if (t.kind === "job") return `/publications/${t.id}`;
  if (t.source_id) return `/projects/${t.source_id}`;
  return null;
}

export function TasksPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const query = useQuery({
    queryKey: qk.recentTasks,
    queryFn: () => tasksApi.recent(120),
    refetchInterval: (q) => {
      const data = q.state.data as ActiveTask[] | undefined;
      return data?.some((t) => isActive(t.status)) ? 2500 : 8000;
    },
    refetchOnWindowFocus: true,
  });

  const tasks = query.data ?? [];
  const filtered = useMemo(() => {
    if (filter === "all") return tasks;
    if (filter === "active") return tasks.filter((t) => isActive(t.status));
    if (filter === "failed") return tasks.filter((t) => FAILED.has(t.status));
    return tasks.filter((t) => t.kind === filter);
  }, [tasks, filter]);

  return (
    <>
      <PageHead title="Задачи" sub="Лог выполнения: анализы, рендеры, публикации" />
      <div className="task-filters">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`chip${filter === f.key ? " active" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !filtered.length ? (
        <EmptyState icon="🗂️" title="Задач нет" hint="Здесь появятся анализы, рендеры и публикации" />
      ) : (
        <div className="task-log">
          {filtered.map((t) => {
            const link = taskLink(t);
            const failed = FAILED.has(t.status);
            return (
              <div key={`${t.kind}:${t.id}`} className={`task-row${failed ? " failed" : ""}`}>
                <span className="task-ico">{KIND_ICO[t.kind] ?? "•"}</span>
                <div className="task-main">
                  <div className="task-head">
                    <strong>
                      {KIND_RU[t.kind] ?? t.kind} #{t.id}
                    </strong>
                    {t.label ? <span className="muted task-label">{t.label}</span> : null}
                    <Badge status={t.status} />
                  </div>
                  {t.error ? <div className="task-error">{t.error}</div> : null}
                  <div className="task-meta mono">
                    <span>обновлено {fmtTime(t.updated_at)}</span>
                    {fmtDuration(t.created_at, t.updated_at) ? (
                      <span>· длительность {fmtDuration(t.created_at, t.updated_at)}</span>
                    ) : null}
                    {t.scheduled_at ? <span>· запланировано {fmtTime(t.scheduled_at)}</span> : null}
                    {link ? (
                      <Link to={link} className="task-link">
                        открыть →
                      </Link>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
