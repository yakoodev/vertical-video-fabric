import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { sourcesApi, looksLikePlayerPage, QUALITY_OPTIONS } from "@/api/sources";
import { downloadsApi } from "@/api/downloads";
import { PROJECT_KINDS, projectsApi, type ChannelProject, type ProjectKind } from "@/api/projects";
import { qk } from "@/api/keys";
import { navKeys } from "@/api/nav";
import type { Source } from "@/api/types";
import { ApiError } from "@/api/client";
import { useDeleteMutation } from "@/hooks/useDeleteMutation";
import { useModuleOn, useModules } from "@/hooks/useModules";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PlayerOptionsDialog } from "@/components/PlayerOptionsDialog";
import { StoryboardPreview } from "@/components/StoryboardPreview";
import { isSourceFileMissing } from "@/components/MissingSourceNotice";
import { NewCanvas } from "@/components/NewCanvas";
import { ShowMore, useShowMore } from "@/components/ShowMore";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, Loading, formatDuration, plural } from "@/components/ui";

function ProjectCard({
  source,
  onDelete,
  series,
}: {
  source: Source;
  onDelete: (s: Source) => void;
  series: ChannelProject[];
}) {
  const qc = useQueryClient();
  const toast = useToast();
  // Источник — в одну серию/канал (app/projects.py) или ни в какую.
  const setSeries = useMutation({
    mutationFn: (projectId: number | null) => projectsApi.setSourceProject(source.id, projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.sources });
      qc.invalidateQueries({ queryKey: qk.channelProjects });
      qc.invalidateQueries({ queryKey: navKeys.all });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось"),
  });
  const name = source.display_name || source.original_filename || source.original_url || `Видео #${source.id}`;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);

  const rename = useMutation({
    mutationFn: (n: string) => sourcesApi.rename(source.id, n),
    onMutate: async (n) => {
      await qc.cancelQueries({ queryKey: qk.sources });
      const prev = qc.getQueryData<Source[]>(qk.sources);
      qc.setQueryData<Source[]>(qk.sources, (old) =>
        old?.map((s) => (s.id === source.id ? { ...s, original_filename: n, display_name: n } : s)),
      );
      return { prev };
    },
    onError: (e, _n, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.sources, ctx.prev);
      toast.error(e instanceof ApiError ? e.message : "Не удалось переименовать");
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: qk.sources });
      qc.invalidateQueries({ queryKey: navKeys.all });
    },
  });

  const commit = () => {
    const n = value.trim();
    setEditing(false);
    if (n && n !== name) rename.mutate(n);
    else setValue(name);
  };

  return (
    <div className="project-card">
      <div className="pcard-coverwrap">
        <Link to={`/v/${source.id}`} className="pcard-link" aria-label={name}>
          <StoryboardPreview
            sourceId={source.id}
            noFile={isSourceFileMissing(source)}
            type={SOURCE_TYPE_LABEL[source.source_type] ?? source.source_type}
            duration={formatDuration(source.duration_sec)}
          />
        </Link>
        <button className="card-del" title="Удалить видео" onClick={() => onDelete(source)}>
          🗑
        </button>
      </div>
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
        <div className="pcard-statusrow">
          <Badge status={source.status} />
          {statusGroup(source.status) === "processing" ? (
            <span className="pcard-progress" title="Идёт обработка — детали в уведомлениях">
              <span />
            </span>
          ) : null}
        </div>
        <div className="pcard-stats">
          {source.width ? <span className="mono">{source.width}×{source.height}</span> : null}
          <span>{plural(source.clip_plans_count ?? 0, "черновик", "черновика", "черновиков")}</span>
          <span>{plural(source.clip_cards_count ?? source.clips_count ?? 0, "клип", "клипа", "клипов")}</span>
        </div>
        {series.length ? (
          <select
            className="input pcard-series"
            title="Проект этого видео: общий лор, правила и статистика"
            value={source.project_id ?? ""}
            disabled={setSeries.isPending}
            onChange={(e) => setSeries.mutate(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Вне проектов</option>
            {series.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        ) : null}
      </div>
    </div>
  );
}

/** Проекты над видео (app/projects.py): лор, правила, статистика, гипотезы. */
function SeriesStrip({ series }: { series: ChannelProject[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ProjectKind>("stream_channel");
  const create = useMutation({
    mutationFn: () => projectsApi.create({ name: name.trim(), kind }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: qk.channelProjects });
      qc.invalidateQueries({ queryKey: navKeys.all });
      navigate(`/p/${p.id}`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось создать"),
  });
  return (
    <section className="series-strip">
      <div className="series-strip-head">
        <strong>Проекты</strong>
        <span className="muted">несколько видео одного канала или сериала: общий лор, правила владельца, статистика</span>
        <button className="btn sm" onClick={() => setAdding((v) => !v)}>
          ＋ Новый проект
        </button>
      </div>
      {adding ? (
        <div className="proj-add-row">
          <input
            className="input"
            autoFocus
            placeholder="Название: «Мура — стримы», «Сериал …»"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && name.trim() && create.mutate()}
          />
          <select className="input" style={{ width: 170, flex: "none" }} value={kind} onChange={(e) => setKind(e.target.value as ProjectKind)}>
            {PROJECT_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
          <button className="btn primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>
            Создать
          </button>
        </div>
      ) : null}
      {series.length ? (
        <div className="series-cards">
          {series.map((p) => (
            <Link key={p.id} to={`/p/${p.id}`} className="panel series-card">
              <strong>{p.name}</strong>
              <span className="muted">{p.kind_label}</span>
              <span className="muted">
                {plural(p.sources_count ?? 0, "видео", "видео", "видео")} · {plural(p.active_rules_count ?? 0, "правило", "правила", "правил")}
              </span>
            </Link>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function AddSource({ onStarted, projectId }: { onStarted?: () => void; projectId?: number | null } = {}) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [quality, setQuality] = useState("");
  const [picking, setPicking] = useState("");

  const onDone = (s: Source) => {
    onStarted?.();
    toast.success("Видео добавлено");
    qc.invalidateQueries({ queryKey: qk.sources });
    qc.invalidateQueries({ queryKey: navKeys.all });
    navigate(`/v/${s.id}`);
  };
  const onErr = (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Не удалось добавить видео");

  // Из проекта файл сразу ложится в него (ссылка — когда докачается, app/downloads.py on_source).
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const source = await sourcesApi.uploadFile(file);
      if (projectId) await projectsApi.setSourceProject(source.id, projectId);
      return source;
    },
    onSuccess: onDone,
    onError: onErr,
  });
  // Downloads run in the background: the task shows up in the bell with progress
  // and a cancel button, and the project card appears when it finishes.
  const ingest = useMutation({
    mutationFn: (u: string) => downloadsApi.start(u, quality, undefined, projectId),
    onSuccess: () => {
      toast.success("Скачивание началось: прогресс в «Очереди задач»");
      setUrl("");
      onStarted?.();
      qc.invalidateQueries({ queryKey: qk.activeTasks });
    },
    onError: onErr,
  });
  const busy = upload.isPending || ingest.isPending;
  const canPick = looksLikePlayerPage(url);
  // «Загрузка по ссылкам» выключена в «Настройки → Модули» — только файл и холст.
  const downloadsOn = useModuleOn("downloads");

  return (
    <div className="proj-add">
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
      {downloadsOn ? <div className="proj-add-row">
        <input
          className="input"
          style={{ flex: 1, minWidth: 200 }}
          placeholder="…или ссылка (mp4, YouTube, Twitch, Smotvibe и похожие плееры)"
          value={url}
          disabled={busy}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && url.trim() && ingest.mutate(url.trim())}
        />
        <select
          className="input"
          style={{ width: 150, flex: "none" }}
          value={quality}
          disabled={busy}
          title="Качество скачивания (для ссылок). «Авто» — лучшее для YouTube, 720p для стримов Twitch/Smotvibe."
          onChange={(e) => setQuality(e.target.value)}
        >
          {QUALITY_OPTIONS.map((q) => (
            <option key={q.value} value={q.value}>
              {q.label}
            </option>
          ))}
        </select>
        {canPick ? (
          <button
            className="btn"
            disabled={busy}
            title="Выбрать сезон, серию и озвучку на странице плеера"
            onClick={() => setPicking(url.trim())}
          >
            🎞 Серия и озвучка
          </button>
        ) : null}
        <button className="btn primary" disabled={busy || !url.trim()} onClick={() => ingest.mutate(url.trim())}>
          {ingest.isPending ? "…" : "Добавить"}
        </button>
      </div> : null}
      {/* Ролик без видео: анимированный фон, картинки и титры (app/canvas.py). */}
      <NewCanvas onCreated={onStarted} disabled={busy} projectId={projectId} />
      {picking ? (
        <PlayerOptionsDialog
          url={picking}
          quality={quality}
          projectId={projectId}
          onClose={() => setPicking("")}
          onStarted={() => {
            setPicking("");
            setUrl("");
            onStarted?.();
          }}
        />
      ) : null}
    </div>
  );
}

type StatusGroup = "done" | "processing" | "error";
const STATUS_GROUP: Record<string, StatusGroup> = {
  ready: "done",
  analyzed: "done",
  analyzing: "processing",
  downloading: "processing",
  uploading: "processing",
  queued: "processing",
  rendering: "processing",
  scheduling: "processing",
  failed: "error",
  error: "error",
  needs_reauth: "error",
};
const statusGroup = (s: string): StatusGroup => STATUS_GROUP[s] ?? "processing";
const SOURCE_TYPE_LABEL: Record<string, string> = {
  upload: "Файл",
  direct_url: "Ссылка",
  youtube_url: "YouTube",
  twitch_url: "Twitch",
  smotvibe_url: "Плеер",
};

export function ProjectsPage() {
  const query = useQuery({ queryKey: qk.sources, queryFn: sourcesApi.list });
  const seriesOn = useModules().ready("series");
  const seriesQuery = useQuery({ queryKey: qk.channelProjects, queryFn: projectsApi.list, enabled: seriesOn });
  const series = seriesOn ? seriesQuery.data ?? [] : [];
  // "" — все, "none" — вне проектов, иначе id проекта.
  const [seriesFilter, setSeriesFilter] = useState("");
  const [pending, setPending] = useState<Source | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusGroup | "all">("all");

  const del = useDeleteMutation<Source>({
    listKey: qk.sources,
    mutationFn: sourcesApi.remove,
    successMessage: () => "Видео удалено",
    onSuccess: () => setPending(null),
  });

  const sources = query.data ?? [];
  const q = search.trim().toLowerCase();
  const counts: Record<string, number> = { all: sources.length, done: 0, processing: 0, error: 0 };
  for (const s of sources) counts[statusGroup(s.status)] += 1;
  const filtered = sources.filter(
    (s) =>
      (statusFilter === "all" || statusGroup(s.status) === statusFilter) &&
      (!seriesFilter ||
        (seriesFilter === "none" ? !s.project_id : String(s.project_id ?? "") === seriesFilter)) &&
      (!q || `${s.display_name ?? ""} ${s.original_filename || s.original_url || `Видео #${s.id}`}`.toLowerCase().includes(q)),
  );
  const shown = useShowMore(filtered, `${statusFilter}|${seriesFilter}|${q}`);

  return (
    <>
      <section className="proj-hero">
        <h1>Все проекты и видео</h1>
        <p className="sub">Записи всех проектов и те, что пока вне проектов: анализ, нарезка, публикация</p>
        <AddSource />
      </section>
      {seriesOn ? <SeriesStrip series={series} /> : null}
      {query.isLoading ? (
        <Loading />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !sources.length ? (
        <EmptyState icon="🎬" title="Пока нет видео" hint="Загрузите видео или добавьте ссылку, чтобы начать" />
      ) : (
        <>
          <div className="proj-toolbar">
            {(
              [
                ["all", "Все"],
                ["processing", "Обработка"],
                ["done", "Готово"],
                ["error", "Ошибка"],
              ] as const
            ).map(([key, label]) =>
              // All four always: the bar must not jump as projects change state.
              true ? (
                <button
                  key={key}
                  className={`schip schip--${key}${statusFilter === key ? " active" : ""}`}
                  onClick={() => setStatusFilter(key)}
                >
                  {label} <span className="schip-n">{counts[key]}</span>
                </button>
              ) : null,
            )}
            <span className="muted proj-count">
              {q || seriesFilter ? `${filtered.length} из ${sources.length}` : plural(sources.length, "видео", "видео", "видео")}
            </span>
            {series.length ? (
              <select
                className="input proj-series-filter"
                value={seriesFilter}
                onChange={(e) => setSeriesFilter(e.target.value)}
                title="Показать видео одного проекта"
              >
                <option value="">Все проекты</option>
                <option value="none">Вне проектов</option>
                {series.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            ) : null}
            <div className="mo-search proj-search">
              <span aria-hidden>⌕</span>
              <input
                className="input"
                placeholder="Поиск видео по названию…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
          {filtered.length ? (
            <>
              <div className="card-grid proj-grid">
                {shown.visible.map((source) => (
                  <ProjectCard key={source.id} source={source} onDelete={setPending} series={series} />
                ))}
              </div>
              <ShowMore rest={shown.rest} onMore={shown.more} />
            </>
          ) : (
            <EmptyState icon="🔍" title="Ничего не найдено" hint="Измените запрос" />
          )}
        </>
      )}

      <ConfirmDialog
        open={Boolean(pending)}
        title="Удалить видео?"
        body={
          <>
            Будут удалены запись, все анализы, черновики и клипы этого видео, а файлы — стёрты с диска.
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
