import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { downloadsApi } from "@/api/downloads";
import { qk } from "@/api/keys";
import { useToast } from "@/components/Toast";
import { useModules } from "@/hooks/useModules";

const ACTIVE = new Set(["queued", "downloading", "cancelling"]);

/** Файл записи удалён, источник при этом жив (моменты, клипы, монтаж). Загрузка и ошибка загрузки — не этот случай. */
export function isSourceFileMissing(source?: { has_file?: boolean | null; status?: string } | null): boolean {
  return !!source && source.has_file === false && !["created", "downloading", "failed"].includes(source.status ?? "");
}

/**
 * Плашка «Запись стрима удалена»: что осталось и кнопка скачать заново в этот же источник.
 * Загрузка фоновая (POST /api/sources/{id}/redownload): прогресс здесь и в «Очереди».
 */
export function MissingSourceNotice({
  source,
  compact = false,
  proxyReady = false,
}: {
  source: { id: number; original_url?: string | null };
  compact?: boolean;
  /** Остался лёгкий прокси 480p: монтаж смотреть и править можно, нельзя только рендерить. */
  proxyReady?: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const { on } = useModules();
  const canDownload = Boolean(source.original_url) && on("downloads");
  const tasks = useQuery({
    queryKey: ["downloads"],
    queryFn: downloadsApi.list,
    enabled: canDownload,
    retry: false,
    refetchInterval: (q) => ((q.state.data ?? []).some((t) => t.restore_source_id === source.id && ACTIVE.has(t.status)) ? 3000 : false),
  });
  const task = (tasks.data ?? []).find((t) => t.restore_source_id === source.id);
  const running = !!task && ACTIVE.has(task.status);
  const start = useMutation({
    mutationFn: () => downloadsApi.redownload(source.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["downloads"] }),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось начать загрузку"),
  });
  const cancel = useMutation({
    mutationFn: (id: number) => downloadsApi.cancel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["downloads"] }),
  });
  // Докачалось — источник снова с файлом: перечитать, плашка исчезнет сама.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (running) wasRunning.current = true;
    else if (wasRunning.current && task?.status === "succeeded") {
      wasRunning.current = false;
      void qc.invalidateQueries({ queryKey: qk.source(source.id) });
      void qc.invalidateQueries({ queryKey: qk.sources });
    }
  }, [running, task?.status, qc, source.id]);

  return (
    <div className={`src-missing${compact ? " compact" : ""}`} role="status">
      <div className="src-missing-text">
        <strong>Запись стрима удалена</strong>
        <span className="muted">
          {proxyReady
            ? "Монтаж играет с лёгкого прокси 480p: смотреть и править можно. Новый рендер — только с записью."
            : "Готовый рендер, субтитры и настройки на месте. Новый рендер и кадры исходника — только с записью."}
          {!source.original_url ? " Ссылки нет: запись загружали файлом." : ""}
        </span>
        {task?.status === "failed" ? <span className="src-missing-err" title={task.error}>Не скачалось: {task.error.length > 200 ? `${task.error.slice(0, 200)}…` : task.error}</span> : null}
      </div>
      {canDownload ? (
        running ? (
          <div className="src-missing-actions">
            <Link to="/inbox?tab=tasks" className="src-missing-progress mono" title="Открыть очередь задач">
              {task?.message || "Скачивание…"}
            </Link>
            <button type="button" className="btn sm" disabled={cancel.isPending} onClick={() => task && cancel.mutate(task.id)}>
              Отменить
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn sm src-missing-btn"
            disabled={start.isPending}
            onClick={() => start.mutate()}
            title={source.original_url ?? ""}
          >
            {start.isPending ? "Запускаю…" : task?.status === "failed" ? "Повторить" : "⬇ Скачать запись заново"}
          </button>
        )
      ) : null}
    </div>
  );
}
