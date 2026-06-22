import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import type { Clip } from "@/api/types";
import { ApiError } from "@/api/client";
import { useDeleteMutation } from "@/hooks/useDeleteMutation";
import { useToast } from "@/components/Toast";
import { ClipCard } from "@/components/ClipCard";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PublishDialog } from "@/components/PublishDialog";
import { EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";

export function ClipsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: qk.clips(), queryFn: () => clipsApi.list() });
  const [toDelete, setToDelete] = useState<Clip | null>(null);
  const [toPublish, setToPublish] = useState<Clip | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) => clipsApi.uploadEdited(file),
    onSuccess: () => {
      toast.success("Клип загружен");
      qc.invalidateQueries({ queryKey: qk.clips() });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось загрузить клип"),
  });

  const del = useDeleteMutation<Clip>({
    listKey: qk.clips(),
    mutationFn: clipsApi.remove,
    successMessage: () => "Клип удалён",
    onSuccess: () => setToDelete(null),
  });

  return (
    <>
      <PageHead
        title="Клипы"
        sub="Отрендеренные и загруженные клипы"
        actions={
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
        }
      />
      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !query.data?.length ? (
        <EmptyState icon="✂️" title="Клипов пока нет" hint="Отрендерите сегменты или загрузите готовый клип" />
      ) : (
        <div className="card-grid">
          {query.data.map((clip) => (
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
