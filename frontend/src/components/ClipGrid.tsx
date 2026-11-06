import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { clipGroupKeys, clipGroupsApi, type ClipGridData, type ClipGridItem } from "@/api/clipGroups";
import { clipPlansApi } from "@/api/clipPlans";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { BatchPublishDialog } from "@/components/BatchPublishDialog";
import { ClipCard } from "@/components/ClipCard";
import { Icon } from "@/components/Icon";
import { ShowMore, useShowMore } from "@/components/ShowMore";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading, formatDuration, plural } from "@/components/ui";
import { useModuleOn } from "@/hooks/useModules";
import { clipEditorPath, clipPath } from "@/lib/paths";

const STATUS: Record<ClipGridItem["status"], { label: string; cls: string }> = {
  ready: { label: "готов", cls: "ok" },
  // Клип без готового файла — черновик: всё, что нашёл агент, и всё, что ещё не рендерили.
  no_render: { label: "черновик", cls: "warn" },
  rendering: { label: "рендер идёт", cls: "run" },
  failed: { label: "ошибка рендера", cls: "bad" },
};

export const isHorizontal = (format: string) => format === "16:9";

/** Плитка клипа: один элемент на группу планов, чипы формата и версий, статус. */
export function ClipTile({
  clip,
  showVideo = false,
  selected,
  onSelect,
}: {
  clip: ClipGridItem;
  showVideo?: boolean;
  selected?: boolean;
  onSelect?: (on: boolean) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [menu, setMenu] = useState(false);
  const flags = useMutation({
    mutationFn: (body: { favorite?: boolean; hidden?: boolean }) => clipPlansApi.setFlags(clip.id, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clipGroupKeys.all });
      void qc.invalidateQueries({ queryKey: qk.source(clip.source_id) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menu]);
  const thumb = clip.latest ? `/media/clips/${clip.latest.id}/thumb?v=${encodeURIComponent(clip.latest.updated_at)}` : "";
  const status = STATUS[clip.status];
  const wide = isHorizontal(clip.format);
  const draft = clip.status === "no_render";
  return (
    <div className={`ctile${selected ? " picked" : ""}`}>
      <Link to={clipPath(clip.id)} className={`ctile-th${wide ? "" : " tall"}`} title={clip.title}>
        {thumb ? (
          <>
            {wide ? null : <img className="b" src={thumb} alt="" loading="lazy" />}
            <img className="m" src={thumb} alt="" loading="lazy" />
          </>
        ) : (
          <span className="ctile-empty">черновик</span>
        )}
        <span className="ctile-fmt">{clip.format}</span>
        {clip.versions > 1 ? (
          <span className="ctile-ver">
            <Icon name="layers" size={13} />
            {plural(clip.versions, "версия", "версии", "версий")}
          </span>
        ) : null}
        {clip.duration_sec ? <span className="ctile-dur">{formatDuration(clip.duration_sec)}</span> : null}
      </Link>
      <div className="ctile-b">
        <div className="ctile-text">
          <Link to={clipPath(clip.id)} className="ctile-name" title={clip.title}>
            {clip.favorite ? <span className="ctile-star" aria-label="избранное">★ </span> : null}
            {clip.name}
          </Link>
          {showVideo && clip.video_name ? <div className="ctile-video" title={clip.video_name}>{clip.video_name}</div> : null}
          <div className="ctile-st">
            <span className={`cchip ${status.cls}`}>{status.label}{draft && clip.picked ? ", в работе" : ""}</span>
            {clip.published ? <span className="cchip">опубликован{clip.published > 1 ? `: ${clip.published}` : ""}</span> : null}
            {clip.moments.map((m) => (
              <Link key={m.id} className="ctile-moment" to={clipPath(m.id)} title="Этот момент в другом формате">
                {m.format}
              </Link>
            ))}
          </div>
        </div>
        <div className="ctile-acts">
          {onSelect && clip.latest ? (
            <input
              type="checkbox"
              className="ctile-check"
              checked={Boolean(selected)}
              aria-label={`Выбрать: ${clip.name}`}
              onChange={(e) => onSelect(e.target.checked)}
            />
          ) : null}
          <button
            type="button"
            className="ctile-more"
            aria-label={`Действия: ${clip.name}`}
            aria-expanded={menu}
            onClick={(e) => {
              e.stopPropagation();
              setMenu((v) => !v);
            }}
          >
            ⋯
          </button>
          {menu ? (
            <div className="ctile-menu" role="menu">
              <Link role="menuitem" to={clipEditorPath(clip.source_id, clip.id)}>Открыть монтаж</Link>
              {clip.latest ? <a role="menuitem" href={`/media/clips/${clip.latest.id}`} download>Скачать</a> : null}
              <button role="menuitem" type="button" onClick={() => flags.mutate({ favorite: !clip.favorite })}>
                {clip.favorite ? "Убрать ★" : draft ? "Взять в работу ★" : "В избранное ★"}
              </button>
              <button role="menuitem" type="button" onClick={() => flags.mutate({ hidden: true })}>Скрыть</button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

type Filter = "all" | "vertical" | "horizontal" | "ready" | "published" | "failed" | "no_render" | "favorite";

const FILTERS: [Filter, string, (c: ClipGridItem) => boolean][] = [
  ["all", "Все", () => true],
  ["vertical", "Вертикальные", (c) => !isHorizontal(c.format)],
  ["horizontal", "Горизонтальные", (c) => isHorizontal(c.format)],
  ["ready", "Готово", (c) => c.status === "ready"],
  ["no_render", "Черновики", (c) => c.status === "no_render"],
  ["published", "Опубликованные", (c) => c.published > 0],
  ["failed", "Ошибка", (c) => c.status === "failed"],
  ["favorite", "★", (c) => c.favorite],
];

/** Сетка клипов видео или проекта: фильтры, поиск, пачка публикаций, черновики отдельно. */
export function ClipGrid({
  sourceId,
  projectId,
  actions,
  emptyActions,
  onData,
}: {
  sourceId?: number;
  projectId?: number;
  actions?: React.ReactNode;
  emptyActions?: React.ReactNode;
  onData?: (data: ClipGridData) => void;
}) {
  const query = useQuery({
    queryKey: projectId ? clipGroupKeys.project(projectId) : clipGroupKeys.source(sourceId ?? 0),
    queryFn: () => (projectId ? clipGroupsApi.forProject(projectId) : clipGroupsApi.forSource(sourceId ?? 0)),
    refetchOnMount: "always",
    // Идёт рендер: статус плитки обновляется сам.
    refetchInterval: (q) => (q.state.data?.clips.some((c) => c.status === "rendering") ? 5000 : false),
  });
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [batch, setBatch] = useState(false);
  const publishingOn = useModuleOn("publishing");

  useEffect(() => {
    if (query.data && onData) onData(query.data);
  }, [query.data, onData]);

  const clips = useMemo(() => (query.data?.clips ?? []).filter((c) => !c.hidden), [query.data]);
  const needle = q.trim().toLowerCase();
  const test = FILTERS.find((f) => f[0] === filter)?.[2] ?? (() => true);
  const list = useMemo(
    () =>
      clips.filter(
        (c) => test(c) && (!needle || `${c.name} ${c.title} ${c.video_name ?? ""}`.toLowerCase().includes(needle)),
      ),
    [clips, test, needle],
  );
  // На вкладке «Все» черновики уходят вниз: главное — готовые клипы.
  const foldEmpty = filter === "all" && !needle;
  const main = foldEmpty ? list.filter((c) => c.status !== "no_render") : list;
  const empties = foldEmpty ? list.filter((c) => c.status === "no_render") : [];
  const shown = useShowMore(main, `${filter}|${needle}`);
  const emptiesShown = useShowMore(empties, `${filter}|${needle}`);

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;

  const pickedClips = clips.filter((c) => picked.has(c.id) && c.latest).map((c) => c.latest!);
  const setPick = (id: number, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const orphans = query.data?.orphans ?? [];

  return (
    <>
      <div className="cgrid-head">
        <h2>Клипы <span className="cgrid-n">{clips.length}</span></h2>
        <div className="cgrid-acts">
          {publishingOn && pickedClips.length ? (
            <button type="button" className="btn primary sm" onClick={() => setBatch(true)}>
              Опубликовать выбранные ({pickedClips.length})
            </button>
          ) : null}
          {actions}
        </div>
      </div>
      {clips.length ? (
        <div className="proj-toolbar cgrid-filters">
          {FILTERS.map(([key, label, match]) => {
            const n = clips.filter(match).length;
            return key === "all" || n ? (
              <button
                key={key}
                type="button"
                className={`schip schip--${key === "ready" ? "done" : key === "failed" ? "error" : "all"}${filter === key ? " active" : ""}`}
                onClick={() => setFilter(key)}
              >
                {label} <span className="schip-n">{n}</span>
              </button>
            ) : null;
          })}
          {clips.length > 8 ? (
            <input
              className="input cgrid-search"
              value={q}
              placeholder="Найти клип"
              aria-label="Найти клип"
              onChange={(e) => setQ(e.target.value)}
            />
          ) : null}
        </div>
      ) : null}
      {!clips.length ? (
        <EmptyState
          icon="✂️"
          title="Клипов пока нет"
          hint={projectId ? "Клипы появятся, когда видео проекта нарежут" : "Нажмите «Нарезать ещё» или создайте клип на вкладке «Черновики»"}
          actions={emptyActions}
        />
      ) : !main.length && !empties.length ? (
        <p className="muted">Ничего не нашлось.</p>
      ) : (
        <>
          <div className="ctiles">
            {shown.visible.map((clip) => (
              <ClipTile
                key={clip.id}
                clip={clip}
                showVideo={Boolean(projectId)}
                selected={picked.has(clip.id)}
                onSelect={publishingOn ? (on) => setPick(clip.id, on) : undefined}
              />
            ))}
          </div>
          <ShowMore rest={shown.rest} onMore={shown.more} />
          {empties.length ? (
            <details className="cgrid-more">
              <summary>Черновики: {empties.length}</summary>
              <div className="ctiles">
                {emptiesShown.visible.map((clip) => (
                  <ClipTile key={clip.id} clip={clip} showVideo={Boolean(projectId)} />
                ))}
              </div>
              <ShowMore rest={emptiesShown.rest} onMore={emptiesShown.more} />
            </details>
          ) : null}
        </>
      )}
      {orphans.length ? (
        <details className="cgrid-more">
          <summary>Рендеры без клипа (его монтаж удалён): {orphans.length}</summary>
          <div className="card-grid clip-grid">
            {orphans.slice(0, 48).map((clip) => (
              <ClipCard key={clip.id} clip={clip} />
            ))}
          </div>
        </details>
      ) : null}
      {batch && pickedClips.length ? (
        <BatchPublishDialog
          clips={pickedClips}
          onClose={() => {
            setBatch(false);
            setPicked(new Set());
          }}
        />
      ) : null}
    </>
  );
}

/** Вкладка «Клипы» проекта. */
export function ProjectClipsTab({ projectId }: { projectId: number }) {
  return (
    <ClipGrid
      projectId={projectId}
      emptyActions={<Link className="btn primary" to={`/p/${projectId}`}>К видео проекта</Link>}
      actions={<Link className="btn ghost sm" to="/pubs?tab=ready" title="Все готовые файлы всех проектов, загрузка своего клипа">Готовые ролики</Link>}
    />
  );
}
