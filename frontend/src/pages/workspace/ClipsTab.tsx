import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { clipsApi, clipOrigin } from "@/api/clips";
import { qk } from "@/api/keys";
import type { Clip } from "@/api/types";
import { useDeleteMutation } from "@/hooks/useDeleteMutation";
import { ClipCard } from "@/components/ClipCard";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PublishDialog } from "@/components/PublishDialog";
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

  return (
    <>
      <div className="card-grid">
        {clips.map((clip) => (
          <ClipCard
            key={clip.id}
            clip={clip}
            actions={
              <>
                <button
                  className="btn primary sm"
                  disabled={clip.status !== "succeeded"}
                  onClick={() => setToPublish(clip)}
                >
                  Опубликовать
                </button>
                <button className="btn ghost sm" onClick={() => setToDelete(clip)}>
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
      emptyHint="Отрендерите кандидатов на вкладке «Кандидаты»"
    />
  );
}
