import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { clipsApi, clipOrigin } from "@/api/clips";
import { qk } from "@/api/keys";
import type { Clip } from "@/api/types";
import { useDeleteMutation } from "@/hooks/useDeleteMutation";
import { ClipCard } from "@/components/ClipCard";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PublishDialog } from "@/components/PublishDialog";
import { BatchPublishDialog } from "@/components/BatchPublishDialog";
import { EmptyState, ErrorState, Loading } from "@/components/ui";

// Shared between the "Клипы" (rendered) and "Смонтированные" (montage) tabs.
export function ProjectClipsGrid({
  sourceId,
  origin,
  emptyIcon,
  emptyTitle,
  emptyHint,
}: {
  sourceId: string;
  origin: "rendered" | "montage";
  emptyIcon: string;
  emptyTitle: string;
  emptyHint: string;
}) {
  const query = useQuery({ queryKey: qk.clips(sourceId), queryFn: () => clipsApi.list(sourceId) });
  const [toDelete, setToDelete] = useState<Clip | null>(null);
  const [toPublish, setToPublish] = useState<Clip | null>(null);
  // Batch: tick finished clips, publish them in one go (each under its own title).
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [batchOpen, setBatchOpen] = useState(false);

  const del = useDeleteMutation<Clip>({
    listKey: qk.clips(sourceId),
    mutationFn: clipsApi.remove,
    successMessage: () => "Клип удалён",
    onSuccess: () => setToDelete(null),
  });

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const clips = (query.data ?? []).filter((c) => clipOrigin(c) === origin);

  if (!clips.length) return <EmptyState icon={emptyIcon} title={emptyTitle} hint={emptyHint} />;

  const ready = clips.filter((c) => c.status === "succeeded");
  const pickedClips = ready.filter((c) => picked.has(c.id));
  const setPick = (id: number, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  return (
    <>
      <div className="clips-toolbar">
        <span className="muted">
          Готово: {ready.length}
          {pickedClips.length ? ` · выбрано: ${pickedClips.length}` : ""}
        </span>
        <button
          className="btn sm"
          disabled={!ready.length}
          onClick={() =>
            setPicked(pickedClips.length === ready.length ? new Set() : new Set(ready.map((c) => c.id)))
          }
        >
          {pickedClips.length === ready.length && ready.length ? "Снять все" : "Выбрать все готовые"}
        </button>
        <button className="btn primary sm" disabled={!pickedClips.length} onClick={() => setBatchOpen(true)}>
          Опубликовать выбранные ({pickedClips.length})
        </button>
      </div>
      <div className="card-grid clip-grid">
        {clips.map((clip) => (
          <ClipCard
            key={clip.id}
            clip={clip}
            selected={picked.has(clip.id)}
            onSelect={clip.status === "succeeded" ? (on) => setPick(clip.id, on) : undefined}
            actions={
              <>
                <button
                  className="btn primary sm"
                  disabled={clip.status !== "succeeded"}
                  onClick={() => setToPublish(clip)}
                >
                  Опубликовать
                </button>
                <button className="btn danger-outline sm" onClick={() => setToDelete(clip)}>
                  Удалить
                </button>
              </>
            }
          />
        ))}
      </div>
      <ConfirmDialog
        open={Boolean(toDelete)}
        title="Удалить клип?"
        body="Файл клипа будет удалён. История публикаций сохранится."
        busy={del.isPending}
        onCancel={() => setToDelete(null)}
        onConfirm={() => toDelete && del.mutate(toDelete.id)}
      />
      {toPublish ? <PublishDialog clip={toPublish} onClose={() => setToPublish(null)} /> : null}
      {batchOpen && pickedClips.length ? (
        <BatchPublishDialog
          clips={pickedClips}
          onClose={() => {
            setBatchOpen(false);
            setPicked(new Set());
          }}
        />
      ) : null}
    </>
  );
}

export function ClipsTab({ sourceId }: { sourceId: string }) {
  return (
    <ProjectClipsGrid
      sourceId={sourceId}
      origin="rendered"
      emptyIcon="✂️"
      emptyTitle="Клипов нет"
      emptyHint="Отрендерите моменты на вкладке «Моменты»"
    />
  );
}
