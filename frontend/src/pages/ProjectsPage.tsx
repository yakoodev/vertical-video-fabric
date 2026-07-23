import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { sourcesApi } from "@/api/sources";
import { qk } from "@/api/keys";
import type { Source } from "@/api/types";
import { ApiError } from "@/api/client";
import { useDeleteMutation } from "@/hooks/useDeleteMutation";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { StoryboardPreview } from "@/components/StoryboardPreview";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, Loading, PageHead, formatDuration, plural } from "@/components/ui";

function ProjectCard({ source, onDelete }: { source: Source; onDelete: (s: Source) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const name = source.original_filename || source.original_url || `Источник #${source.id}`;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);

  const rename = useMutation({
    mutationFn: (n: string) => sourcesApi.rename(source.id, n),
    onMutate: async (n) => {
      await qc.cancelQueries({ queryKey: qk.sources });
      const prev = qc.getQueryData<Source[]>(qk.sources);
      qc.setQueryData<Source[]>(qk.sources, (old) =>
        old?.map((s) => (s.id === source.id ? { ...s, original_filename: n } : s)),
      );
      return { prev };
    },
    onError: (e, _n, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.sources, ctx.prev);
      toast.error(e instanceof ApiError ? e.message : "Не удалось переименовать");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: qk.sources }),
  });

  const commit = () => {
    const n = value.trim();
    setEditing(false);
    if (n && n !== name) rename.mutate(n);
    else setValue(name);
  };

  return (
    <div className="project-card">
      <button className="card-del" title="Удалить проект" onClick={() => onDelete(source)}>
        🗑
      </button>
      <Link to={`/projects/${source.id}`} className="pcard-link" aria-label={name}>
        <StoryboardPreview
          sourceId={source.id}
          type={source.source_type}
          duration={formatDuration(source.duration_sec)}
        />
      </Link>
      <div className="pcard-body">
        {editing ? (
          <input
            className="input"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") {
                setEditing(false);
                setValue(name);
              }
            }}
          />
        ) : (
          <div className="pcard-titlerow">
            <strong className="pcard-title" title={name}>
              {name}
            </strong>
            <button
              className="pcard-edit"
              title="Переименовать"
              onClick={() => {
                setValue(name);
                setEditing(true);
              }}
            >
              ✎
            </button>
          </div>
        )}
        <div className="pcard-stats">
          <Badge status={source.status} />
          {source.width ? <span className="mono">{source.width}×{source.height}</span> : null}
          <span>{plural(source.analyses_count ?? 0, "анализ", "анализа", "анализов")}</span>
          <span>{plural(source.clips_count ?? 0, "клип", "клипа", "клипов")}</span>
        </div>
      </div>
    </div>
  );
}

function AddSource() {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [url, setUrl] = useState("");

  const onDone = (s: Source) => {
    toast.success("Источник добавлен");
    qc.invalidateQueries({ queryKey: qk.sources });
    navigate(`/projects/${s.id}`);
  };
  const onErr = (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Не удалось добавить источник");

  const upload = useMutation({ mutationFn: (file: File) => sourcesApi.uploadFile(file), onSuccess: onDone, onError: onErr });
  const ingest = useMutation({ mutationFn: (u: string) => sourcesApi.ingestUrl(u), onSuccess: onDone, onError: onErr });
  const busy = upload.isPending || ingest.isPending;

  return (
    <div className="panel" style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center", marginBottom: 18 }}>
      <label className={`btn ${busy ? "" : "primary"}`} style={{ cursor: busy ? "wait" : "pointer" }}>
        {upload.isPending ? "Загрузка…" : "📤 Загрузить файл"}
        <input
          type="file"
          accept="video/*"
          hidden
          disabled={busy}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = "";
          }}
        />
      </label>
      <div style={{ display: "flex", gap: 8, flex: 1, minWidth: 280 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          placeholder="…или ссылка (mp4, YouTube, Twitch, Smotvibe)"
          value={url}
          disabled={busy}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && url.trim() && ingest.mutate(url.trim())}
        />
        <button className="btn" disabled={busy || !url.trim()} onClick={() => ingest.mutate(url.trim())}>
          {ingest.isPending ? "Скачивание…" : "Добавить"}
        </button>
      </div>
    </div>
  );
}

export function ProjectsPage() {
  const query = useQuery({ queryKey: qk.sources, queryFn: sourcesApi.list });
  const [pending, setPending] = useState<Source | null>(null);

  const del = useDeleteMutation<Source>({
    listKey: qk.sources,
    mutationFn: sourcesApi.remove,
    successMessage: () => "Проект удалён",
    onSuccess: () => setPending(null),
  });

  return (
    <>
      <PageHead title="Проекты" sub="Исходные видео для анализа, нарезки и публикации" />
      <AddSource />
      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !query.data?.length ? (
        <EmptyState icon="🎬" title="Пока нет проектов" hint="Загрузите видео или добавьте ссылку, чтобы начать" />
      ) : (
        <div className="card-grid">
          {query.data.map((source) => (
            <ProjectCard key={source.id} source={source} onDelete={setPending} />
          ))}
        </div>
      )}

      <ConfirmDialog
        open={Boolean(pending)}
        title="Удалить проект?"
        body={
          <>
            Будут удалены исходник, все анализы, кандидаты и клипы этого проекта, а файлы — стёрты с диска.
            История публикаций сохранится. Действие необратимо.
          </>
        }
        busy={del.isPending}
        onCancel={() => setPending(null)}
        onConfirm={() => pending && del.mutate(pending.id)}
      />
    </>
  );
}
