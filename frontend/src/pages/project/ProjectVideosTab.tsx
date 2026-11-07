import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
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
import { projectPath, videoPath } from "@/lib/paths";
import "@/styles/releases.css";

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

function releaseMeta(item: NavGenerated): string {
  const clips = item.clip_count > 1 ? plural(item.clip_count, "клип", "клипа", "клипа") : "";
  const versions = item.versions > 1 ? plural(item.versions, "версия", "версии", "версий") : "";
  return [
    `${item.kind_label}, ${dateRu(item.date)}`,
    item.draft ? "черновик без голоса" : "",
    item.duration_sec ? formatDuration(item.duration_sec) : "",
    clips,
    versions,
    !item.clip_count && item.status === "ready" ? "клипа нет" : "",
  ].filter(Boolean).join(" · ");
}

/** Выпуск: ролик с нуля, холст или ТОП-N. Один готовый клип — строка ведёт сразу в него, шаги рядом. */
function ReleaseRow({ item }: { item: NavGenerated }) {
  const status = item.status === "ready"
    ? null
    : item.status === "approval" && item.estimate_rub > 0
      ? `${item.status_label} · ≈ ${Math.round(item.estimate_rub)} ₽`
      : item.status_label;
  return (
    <div className="vrow vrow-rel">
      <Link to={item.to} className="vrow-th vrow-th-gen" aria-label={item.name}>
        {item.thumb_clip_id ? <img className="vrow-rel-thumb" src={`/media/clips/${item.thumb_clip_id}/thumb`} alt="" loading="lazy" /> : <span>{item.kind_label}</span>}
      </Link>
      <div className="vrow-main">
        <div className="vrow-title">
          <Link to={item.to} title={item.name}>{item.name}</Link>
        </div>
        <div className="vrow-meta">{releaseMeta(item)}</div>
        {item.foreign_project ? (
          <div className="vrow-foreign">
            Материалы в проекте <Link to={projectPath(item.foreign_project.id)}>{item.foreign_project.name}</Link>
          </div>
        ) : null}
      </div>
      <div className="vrow-act">
        {status ? <span className={`tag vrow-status ${item.status}`}>{status}</span> : null}
        {item.steps_to && item.to.startsWith("/clips/") ? (
          <Link className="btn ghost sm" to={item.steps_to}>
            Шаги
          </Link>
        ) : null}
      </div>
    </div>
  );
}

// Фильтр по типу: «Видео» — записи, остальное — типы выпусков. ?kind= сохраняется в адресе, туда ведут старые вкладки.
const KIND_TITLES: Record<string, string> = {
  video: "Видео",
  expl: "Объяснялки",
  story: "Истории",
  "3d": "3D-серии",
  aiv: "ИИ-видео",
  promo: "Промо",
  top: "ТОП-N",
  canvas: "Холсты",
  upload: "Загруженные",
};

/** Проект → Видео: записи и выпуски разными разделами. Безымянные холсты — настоящие клипы, свёрнуты отдельно. */
export function ProjectVideosTab({ projectId, onNewVideo }: { projectId: number; onNewVideo: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const setKind = (next: string) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next) p.set("kind", next);
        else p.delete("kind");
        return p;
      },
      { replace: true },
    );
  const query = useQuery({ queryKey: navKeys.videos(projectId), queryFn: () => navApi.videos(projectId) });
  const [filter, setFilter] = useState<StatusGroup | "all">("all");
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState<NavVideo | null>(null);
  const videos = useMemo(() => query.data?.videos ?? [], [query.data]);
  const generated = useMemo(() => query.data?.generated ?? [], [query.data]);
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
  const named = generated.filter((g) => !g.unnamed && (!kind || g.kind_key === kind) && (!needle || (g.name || "").toLowerCase().includes(needle)));
  const unnamed = generated.filter((g) => g.unnamed && (!kind || g.kind_key === kind));
  const releasesShown = useShowMore(named, `rel|${projectId}|${kind}|${needle}`);
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
  const kinds: Record<string, number> = {};
  if (videos.length) kinds.video = videos.length;
  for (const g of generated) kinds[g.kind_key] = (kinds[g.kind_key] ?? 0) + 1;
  const kindKeys = Object.keys(KIND_TITLES).filter((k) => kinds[k]);
  const showVideos = !kind || kind === "video";
  const showReleases = kind !== "video";
  const hasMaterials = ["expl", "story", "3d", "aiv"].includes(kind);
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
      {kindKeys.length > 1 ? (
        <div className="vkind-chips" role="group" aria-label="Тип">
          <button className={`pill${kind ? "" : " on"}`} onClick={() => setKind("")}>
            Все {videos.length + generated.length}
          </button>
          {kindKeys.map((k) => (
            <button key={k} className={`pill${kind === k ? " on" : ""}`} data-kind={k} onClick={() => setKind(k)}>
              {KIND_TITLES[k]} {kinds[k]}
            </button>
          ))}
        </div>
      ) : null}
      {hasMaterials ? (
        <p className="inbox-line">
          Маскоты, миры, персонажи и бюджет: <Link to={`${projectPath(projectId, "materials")}&mat=${kind === "expl" ? "mascots" : kind === "aiv" ? "aiv" : "worlds"}`}>материалы проекта →</Link>
        </p>
      ) : null}
      {showReleases && named.length ? (
        <section className="vsec" aria-label="Выпуски">
          {videos.length ? <h3 className="vsec-h">Выпуски · {named.length}</h3> : null}
          <div className="vrows">
            {releasesShown.visible.map((g) => (
              <ReleaseRow key={g.key} item={g} />
            ))}
          </div>
          <ShowMore rest={releasesShown.rest} onMore={releasesShown.more} />
        </section>
      ) : null}
      {showVideos && filtered.length ? (
        <section className="vsec" aria-label="Видео">
          {generated.length ? <h3 className="vsec-h">Видео · {filtered.length}</h3> : null}
          <div className="vrows">
            {shown.visible.map((v) => (
              <VideoRow key={`${v.id}:${v.name}`} video={v} onDelete={setPending} />
            ))}
          </div>
          <ShowMore rest={shown.rest} onMore={shown.more} />
        </section>
      ) : null}
      {showReleases && unnamed.length ? (
        <details className="vrows-unnamed">
          <summary>Холсты без названия · {unnamed.length}</summary>
          <div className="vrows">
            {unnamed.map((g) => (
              <ReleaseRow key={g.key} item={g} />
            ))}
          </div>
        </details>
      ) : null}
      {(!showVideos || !filtered.length) && (!showReleases || !named.length) && !unnamed.length ? (
        <EmptyState icon="🔍" title="Ничего не найдено" hint="Измените запрос" />
      ) : null}
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
