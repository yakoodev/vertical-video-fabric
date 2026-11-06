import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { navApi, navKeys, type NavGenerated, type NavVideo } from "@/api/nav";
import { sourcesApi } from "@/api/sources";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Icon } from "@/components/Icon";
import { ShowMore, useShowMore } from "@/components/ShowMore";
import { StoryboardPreview } from "@/components/StoryboardPreview";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, Loading, formatDuration, plural } from "@/components/ui";
import { clipEditorPath, projectPath, videoPath } from "@/lib/paths";

type StatusGroup = "done" | "processing" | "error";
const GROUP: Record<string, StatusGroup> = {
  ready: "done",
  analyzed: "done",
  failed: "error",
  error: "error",
  needs_reauth: "error",
};
const groupOf = (s: string): StatusGroup => GROUP[s] ?? "processing";
const FILTERS: [StatusGroup | "all", string][] = [
  ["all", "Все"],
  ["processing", "Обработка"],
  ["done", "Готово"],
  ["error", "Ошибка"],
];

function dateRu(value: string): string {
  const d = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  if (Number.isNaN(d.getTime())) return "";
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", ...(sameYear ? {} : { year: "numeric" }) }).replace(" г.", "");
}

function clipsLine(v: NavVideo): string {
  if (!v.clips) return "клипов пока нет";
  const formats = v.vertical && v.horizontal ? `: ${v.vertical} верт., ${v.horizontal} гориз.` : "";
  const missing = v.no_render ? `, ${plural(v.no_render, "черновик", "черновика", "черновиков")}` : "";
  return `${plural(v.clips, "клип", "клипа", "клипов")}${formats}${missing}`;
}

function VideoRow({ video, onDelete }: { video: NavVideo; onDelete: (v: NavVideo) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(video.name);
  const rename = useMutation({
    mutationFn: (name: string) => sourcesApi.rename(video.id, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: navKeys.all });
      qc.invalidateQueries({ queryKey: qk.source(video.id) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось переименовать"),
  });
  const commit = () => {
    const next = value.trim();
    setEditing(false);
    if (next && next !== video.name) rename.mutate(next);
    else setValue(video.name);
  };
  const meta = [
    `${video.kind}, ${dateRu(video.date)}`,
    video.duration_sec ? formatDuration(video.duration_sec) : "",
    clipsLine(video),
    video.chat ? `чат ${video.chat}` : "",
  ].filter(Boolean).join(" · ");
  return (
    <div className="vrow" title={video.width ? `${video.width}×${video.height}` : undefined}>
      <Link to={videoPath(video.id)} className="vrow-th" aria-label={video.name}>
        <StoryboardPreview
          sourceId={video.id}
          noFile={!video.has_file}
          noFileLabel={false}
          fallbackThumb={video.thumb_clip_id ? `/media/clips/${video.thumb_clip_id}/thumb` : undefined}
          duration={video.duration_sec ? formatDuration(video.duration_sec) : undefined}
        />
      </Link>
      <div className="vrow-main">
        {editing ? (
          <input
            className="input"
            autoFocus
            value={value}
            aria-label="Название видео"
            onChange={(e) => setValue(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") {
                setEditing(false);
                setValue(video.name);
              }
            }}
          />
        ) : (
          <div className="vrow-title">
            <Link to={videoPath(video.id)} title={video.original_filename || video.name}>
              {video.name}
            </Link>
            <button
              className="vrow-edit"
              title="Переименовать"
              onClick={() => {
                setValue(video.name);
                setEditing(true);
              }}
            >
              ✎
            </button>
          </div>
        )}
        <div className="vrow-meta">{meta}</div>
      </div>
      <div className="vrow-act">
        {!video.has_file ? <span className="tag warn">запись удалена</span> : null}
        {groupOf(video.status) !== "done" ? <Badge status={video.status} /> : null}
        <button className="vrow-del" title="Удалить видео" aria-label={`Удалить ${video.name}`} onClick={() => onDelete(video)}>
          <Icon name="trash" size={16} />
        </button>
      </div>
    </div>
  );
}

function GeneratedRow({ item, projectId }: { item: NavGenerated; projectId: number }) {
  const to = item.tab
    ? projectPath(projectId, item.tab)
    : item.source_id && item.plan_id
      ? clipEditorPath(item.source_id, item.plan_id)
      : projectPath(projectId);
  return (
    <div className="vrow vrow-gen">
      <Link to={to} className="vrow-th vrow-th-gen" aria-label={item.name || item.kind}>
        <span>{item.kind}</span>
      </Link>
      <div className="vrow-main">
        <div className="vrow-title">
          <Link to={to}>{item.name || item.kind}</Link>
        </div>
        <div className="vrow-meta">{`${item.kind}, ${dateRu(item.date)}`}</div>
      </div>
      <div className="vrow-act">
        {item.source_id && item.plan_id && item.tab ? (
          <Link className="btn ghost sm" to={clipEditorPath(item.source_id, item.plan_id)}>
            Открыть монтаж
          </Link>
        ) : null}
        <span className="tag ac">{item.kind}</span>
      </div>
    </div>
  );
}

/** Проект → Видео: стримы, загрузки и всё сгенерированное. Лёгкий список, кадры подгружаются по прокрутке. */
export function ProjectVideosTab({ projectId, onNewVideo }: { projectId: number; onNewVideo: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: navKeys.videos(projectId), queryFn: () => navApi.videos(projectId) });
  const [filter, setFilter] = useState<StatusGroup | "all">("all");
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState<NavVideo | null>(null);
  const videos = useMemo(() => query.data?.videos ?? [], [query.data]);
  const generated = query.data?.generated ?? [];
  const needle = search.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      videos.filter(
        (v) =>
          (filter === "all" || groupOf(v.status) === filter) &&
          (!needle || `${v.name} ${v.original_filename}`.toLowerCase().includes(needle)),
      ),
    [videos, filter, needle],
  );
  const shown = useShowMore(filtered, `${projectId}|${filter}|${needle}`);
  const genShown = generated.filter((g) => !needle || (g.name || "").toLowerCase().includes(needle));
  const remove = useMutation({
    mutationFn: (id: number) => sourcesApi.remove(id),
    onSuccess: () => {
      toast.success("Видео удалено");
      setPending(null);
      qc.invalidateQueries({ queryKey: navKeys.all });
      qc.invalidateQueries({ queryKey: qk.sources });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось удалить"),
  });

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!videos.length && !generated.length) {
    return (
      <EmptyState
        icon="🎬"
        title="Видео пока нет"
        hint="Загрузите запись или вставьте ссылку на стрим."
        actions={
          <button className="btn primary" onClick={onNewVideo}>
            Новое видео
          </button>
        }
      />
    );
  }
  const counts: Record<string, number> = { all: videos.length, done: 0, processing: 0, error: 0 };
  for (const v of videos) counts[groupOf(v.status)] += 1;
  return (
    <>
      {videos.length > 8 || counts.processing || counts.error ? (
        <div className="proj-toolbar vrow-tools">
          {FILTERS.map(([key, label]) => (
            <button key={key} className={`schip schip--${key}${filter === key ? " active" : ""}`} onClick={() => setFilter(key)}>
              {label} <span className="schip-n">{counts[key]}</span>
            </button>
          ))}
          <div className="mo-search proj-search">
            <span aria-hidden>⌕</span>
            <input className="input" placeholder="Поиск видео по названию…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>
      ) : null}
      <div className="vrows">
        {genShown.map((g) => (
          <GeneratedRow key={g.key} item={g} projectId={projectId} />
        ))}
        {shown.visible.map((v) => (
          <VideoRow key={`${v.id}:${v.name}`} video={v} onDelete={setPending} />
        ))}
      </div>
      {!filtered.length && !genShown.length ? <EmptyState icon="🔍" title="Ничего не найдено" hint="Измените запрос" /> : null}
      <ShowMore rest={shown.rest} onMore={shown.more} />
      <ConfirmDialog
        open={Boolean(pending)}
        title="Удалить видео?"
        body={
          <>
            Будут удалены запись, все анализы, черновики и клипы этого видео, а файлы стёрты с диска. История публикаций
            сохранится. Действие необратимо.
          </>
        }
        busy={remove.isPending}
        onCancel={() => setPending(null)}
        onConfirm={() => pending && remove.mutate(pending.id)}
      />
    </>
  );
}
