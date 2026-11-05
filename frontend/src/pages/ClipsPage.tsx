import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import type { Clip } from "@/api/types";
import { ApiError } from "@/api/client";
import { useDeleteMutation } from "@/hooks/useDeleteMutation";
import { useToast } from "@/components/Toast";
import { ClipCard } from "@/components/ClipCard";
import { ShowMore, useShowMore } from "@/components/ShowMore";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PublishDialog } from "@/components/PublishDialog";
import { useModuleOn } from "@/hooks/useModules";
import { EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";

type ClipFilter = "all" | "ready" | "published" | "error" | "vertical" | "horizontal";
const clipMatches = (c: Clip, f: ClipFilter): boolean =>
  f === "all"
    ? true
    : f === "vertical"
      ? c.status === "succeeded" && c.height >= c.width
      : f === "horizontal"
        ? c.status === "succeeded" && c.width > c.height
        : f === "published"
          ? (c.published_targets_count ?? 0) > 0
          : f === "error"
            ? ["failed", "error"].includes(c.status)
            : c.status === "succeeded";

/** Клипы всех проектов («Готовые ролики») или одного (projectId, вкладка «Клипы»); карточки те же. */
export function ClipsPage({ embedded, projectId }: { embedded?: boolean; projectId?: number } = {}) {
  const qc = useQueryClient();
  const toast = useToast();
  const listKey = projectId ? (["clips", "project", String(projectId)] as const) : qk.clips();
  const query = useQuery({
    queryKey: listKey,
    queryFn: () => (projectId ? clipsApi.listForProject(projectId) : clipsApi.list()),
  });
  const [toDelete, setToDelete] = useState<Clip | null>(null);
  const [toPublish, setToPublish] = useState<Clip | null>(null);
  const publishingOn = useModuleOn("publishing"); // app/modules.py
  const [filter, setFilter] = useState<ClipFilter>("all");
  const shown = useShowMore((query.data ?? []).filter((c) => clipMatches(c, filter)), filter);

  const upload = useMutation({
    mutationFn: (file: File) => clipsApi.uploadEdited(file),
    onSuccess: () => {
      toast.success("Клип загружен");
      qc.invalidateQueries({ queryKey: qk.clips() });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось загрузить клип"),
  });

  const del = useDeleteMutation<Clip>({
    listKey,
    mutationFn: clipsApi.remove,
    successMessage: () => "Клип удалён",
    onSuccess: () => setToDelete(null),
  });

  // Загруженный клип без видео-основы не лежит в проекте: кнопка живёт в «Готовых роликах».
  const uploadButton = projectId ? null : (
    <label className="btn primary" style={{ cursor: upload.isPending ? "wait" : "pointer" }}>
      {upload.isPending ? "Загрузка…" : "📤 Загрузить клип"}
      <input
        type="file"
        accept="video/*"
        hidden
        disabled={upload.isPending}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload.mutate(f);
          e.target.value = "";
        }}
      />
    </label>
  );

  return (
    <>
      {uploadButton && embedded ? (
        <div className="page-acts">
          <p className="muted">Все готовые ролики всех проектов. Клипы одного проекта: его вкладка «Клипы».</p>
          {uploadButton}
        </div>
      ) : null}
      {embedded || projectId ? null : <PageHead title="Клипы" sub="Отрендеренные и загруженные клипы" actions={uploadButton} />}
      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !query.data?.length ? (
        <EmptyState
          icon="✂️"
          title="Клипов пока нет"
          hint="Отрендерите моменты видео или загрузите готовый клип"
          actions={
            <Link className="btn primary" to={projectId ? `/p/${projectId}` : "/projects"}>
              {projectId ? "К видео проекта" : "Все видео"}
            </Link>
          }
        />
      ) : (
        <>
        <div className="proj-toolbar">
          {(
            [
              ["all", "Все"],
              ["vertical", "Вертикальные"],
              ["horizontal", "Горизонтальные"],
              ["ready", "Готово"],
              ["published", "Опубликованные"],
              ["error", "Ошибка"],
            ] as const
          ).map(([key, label]) => {
            const n = query.data.filter((c) => clipMatches(c, key)).length;
            return key === "all" || n ? (
              <button
                key={key}
                className={`schip schip--${key === "ready" ? "done" : key === "published" ? "all" : key}${filter === key ? " active" : ""}`}
                onClick={() => setFilter(key)}
              >
                {label} <span className="schip-n">{n}</span>
              </button>
            ) : null;
          })}
        </div>
        <div className="card-grid clip-grid">
          {shown.visible.map((clip) => (
            <ClipCard
              key={clip.id}
              clip={clip}
              actions={
                <>
                  {publishingOn ? (
                    <button
                      className="btn primary sm"
                      disabled={clip.status !== "succeeded"}
                      onClick={() => setToPublish(clip)}
                    >
                      Опубликовать
                    </button>
                  ) : null}
                  <button className="btn danger-outline sm" onClick={() => setToDelete(clip)}>
                    Удалить
                  </button>
                </>
              }
            />
          ))}
        </div>
        <ShowMore rest={shown.rest} onMore={shown.more} />
        </>
      )}

      <ConfirmDialog
        open={Boolean(toDelete)}
        title="Удалить клип?"
        body="Файл клипа будет удалён. История публикаций сохранится."
        busy={del.isPending}
        onCancel={() => setToDelete(null)}
        onConfirm={() => toDelete && del.mutate(toDelete.id)}
      />
      {toPublish ? <PublishDialog clip={toPublish} onClose={() => setToPublish(null)} /> : null}
    </>
  );
}
