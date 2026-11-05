import { useState } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { clipGroupsApi, type RenderFilter } from "@/api/clipGroups";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import type { Clip } from "@/api/types";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ClipCard } from "@/components/ClipCard";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PublishDialog } from "@/components/PublishDialog";
import { useModuleOn } from "@/hooks/useModules";
import { EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";

const PAGE = 60;

/** Все готовые ролики всех проектов страницами («Публикации и аккаунты → Готовые ролики»). Клипы одного проекта и видео — ClipGrid. */
export function ClipsPage({ embedded }: { embedded?: boolean } = {}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [filter, setFilter] = useState<RenderFilter>("all");
  const query = useInfiniteQuery({
    queryKey: [...qk.clips(), "pages", filter],
    queryFn: ({ pageParam }) => clipGroupsApi.renders(filter, pageParam, PAGE),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined),
  });
  const [toDelete, setToDelete] = useState<Clip | null>(null);
  const [toPublish, setToPublish] = useState<Clip | null>(null);
  const publishingOn = useModuleOn("publishing"); // app/modules.py
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  const first = query.data?.pages[0];

  const upload = useMutation({
    mutationFn: (file: File) => clipsApi.uploadEdited(file),
    onSuccess: () => {
      toast.success("Клип загружен");
      qc.invalidateQueries({ queryKey: qk.clips() });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось загрузить клип"),
  });

  const del = useMutation({
    mutationFn: (id: number) => clipsApi.remove(id),
    onSuccess: () => {
      toast.success("Клип удалён");
      setToDelete(null);
      void qc.invalidateQueries({ queryKey: qk.clips() });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось удалить"),
  });

  // Загруженный клип без видео-основы не лежит в проекте: кнопка живёт в «Готовых роликах».
  const uploadButton = (
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
      {embedded ? null : <PageHead title="Клипы" sub="Отрендеренные и загруженные клипы" actions={uploadButton} />}
      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !first?.counts.all ? (
        <EmptyState
          icon="✂️"
          title="Клипов пока нет"
          hint="Отрендерите моменты видео или загрузите готовый клип"
          actions={
            <Link className="btn primary" to="/projects">
              Все видео
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
            const n = first.counts[key];
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
          {items.map((clip) => (
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
        {query.hasNextPage ? (
          <div className="show-more">
            <button type="button" className="btn sm" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
              {query.isFetchingNextPage ? "Загрузка…" : `Показать ещё (${first.total - items.length})`}
            </button>
          </div>
        ) : null}
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
