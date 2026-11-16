import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import {
  FEED_KINDS,
  FEED_PLATFORMS,
  FEED_STATUS_LABEL,
  feedApi,
  feedKeys,
  platformName,
  platformShort,
  type FeedFilter,
  type FeedPosting,
  type FeedStatus,
  type FeedVariant,
  type FeedVersion,
  type FeedWork,
} from "@/api/feed";
import { Icon } from "@/components/Icon";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading, PageHead, formatBytes, formatDuration, plural } from "@/components/ui";
import "@/styles/feed.css";

const FILTERS: { key: FeedFilter; label: string; countKey: string }[] = [
  { key: "", label: "Все", countKey: "all" },
  { key: "ready_unposted", label: "Готово, не выложено", countKey: "ready_unposted" },
  { key: "review", label: "На проверке", countKey: "review" },
  { key: "posted", label: "Выложено", countKey: "posted" },
];

/** Дата из базы (UTC без пояса) → «16 нояб., 10:05» по времени телефона. */
function when(text: string | null | undefined): string {
  if (!text) return "";
  const date = new Date(`${text.replace(" ", "T").slice(0, 23)}Z`);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function StatusPill({ status, label }: { status: FeedStatus; label?: string }) {
  return <span className={`feed-st feed-st--${status}`}>{label ?? FEED_STATUS_LABEL[status]}</span>;
}

function PlatformBadges({ platforms }: { platforms: string[] }) {
  if (!platforms.length) return null;
  return (
    <span className="feed-plats">
      {platforms.map((p) => (
        <span key={p} className={`feed-plat feed-plat--${p}`} title={`Выложено: ${platformName(p)}`}>
          {platformShort(p)}
        </span>
      ))}
    </span>
  );
}

/** Карточка работы в ленте. */
function FeedCard({ work, onOpen }: { work: FeedWork; onOpen: () => void }) {
  const latest = work.latest;
  return (
    <button type="button" className="feed-card" onClick={onOpen}>
      <span className="feed-cover">
        {work.cover_url ? <img src={work.cover_url} alt="" loading="lazy" /> : <Icon name="film" size={26} />}
      </span>
      <span className="feed-card-body">
        <span className="feed-card-title">{work.title}</span>
        <span className="feed-card-meta">
          {[work.project_name, work.kind_label].filter(Boolean).join(" · ")}
        </span>
        <span className="feed-card-row">
          {latest ? (
            <>
              <span className="feed-vname">{latest.name}</span>
              <StatusPill status={latest.status} label={latest.status_label} />
            </>
          ) : (
            <span className="muted">версий пока нет</span>
          )}
          <PlatformBadges platforms={work.posted_platforms} />
        </span>
        <span className="feed-card-foot">
          {work.ready_unposted ? <span className="feed-flag">Готово, не выложено</span> : null}
          <span className="muted">{when(work.activity_at)}</span>
        </span>
      </span>
    </button>
  );
}

function PostingChip({ posting, onRemove, busy }: { posting: FeedPosting; onRemove: () => void; busy: boolean }) {
  const by = posting.origin === "service" ? "сервис" : posting.posted_by === "agent" ? "агент" : posting.origin === "clip" ? "экран клипа" : "";
  const label = `${platformName(posting.platform)}${posting.posted_at ? ` · ${posting.posted_at.slice(0, 10)}` : ""}`;
  return (
    <span className={`feed-post feed-plat--${posting.platform}`} title={by ? `Отметил: ${by}` : undefined}>
      {posting.url ? (
        <a href={posting.url} target="_blank" rel="noreferrer">
          {label}
        </a>
      ) : (
        <span>{label}</span>
      )}
      {posting.removable ? (
        <button type="button" className="feed-post-x" disabled={busy} onClick={onRemove} title="Снять отметку" aria-label="Снять отметку">
          ×
        </button>
      ) : null}
    </span>
  );
}

/** Вариант версии: лёгкий плеер, полный файл, отметки «запощено». */
function VariantRow({ variant, workId }: { variant: FeedVariant; workId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [posting, setPosting] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: feedKeys.all });
  const mark = useMutation({
    mutationFn: (platform: string) => feedApi.markPosted(variant.id, { platform, url: url.trim() || undefined }),
    onSuccess: (_d, platform) => {
      toast.success(`Отмечено: ${platformName(platform)}`);
      setPosting(null);
      setUrl("");
      void refresh();
    },
    onError: (e) => toast.error(errorText(e, "Не удалось отметить")),
  });
  const unmark = useMutation({
    mutationFn: (id: number) => feedApi.unmark(id),
    onSuccess: () => void refresh(),
    onError: (e) => toast.error(errorText(e, "Не удалось снять отметку")),
  });
  const rebuild = useMutation({
    mutationFn: () => feedApi.rebuildPreview(variant.id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: feedKeys.work(workId) }),
  });
  const meta = [
    variant.width && variant.height ? `${variant.width}×${variant.height}` : "",
    variant.duration_sec ? formatDuration(variant.duration_sec) : "",
    variant.size_bytes ? formatBytes(variant.size_bytes) : "",
  ].filter(Boolean);
  const preview = variant.preview;
  return (
    <div className={`feed-var feed-var--${variant.orientation}`}>
      <div className="feed-player">
        {preview.status === "ready" && preview.url ? (
          // preload="none": на медленной сети ничего не качается, пока не нажали «плей».
          <video src={preview.url} poster={variant.poster_url ?? undefined} controls preload="none" playsInline />
        ) : (
          <div className="feed-player-wait">
            {variant.poster_url ? <img src={variant.poster_url} alt="" /> : null}
            <span>
              {preview.status === "failed"
                ? `Превью не собралось${preview.error ? `: ${preview.error}` : ""}`
                : "Готовлю лёгкое превью…"}
            </span>
            {preview.status === "failed" && variant.file_present ? (
              <button type="button" className="btn ghost sm" disabled={rebuild.isPending} onClick={() => rebuild.mutate()}>
                Ещё раз
              </button>
            ) : null}
          </div>
        )}
      </div>
      <div className="feed-var-body">
        <div className="feed-var-head">
          <strong>{variant.label}</strong>
          {meta.length ? <span className="muted">{meta.join(" · ")}</span> : null}
        </div>
        {variant.download_url ? (
          <a className="btn sm feed-dl" href={variant.download_url} download>
            <Icon name="down" size={15} />
            Скачать (полное качество{variant.size_bytes ? `, ${formatBytes(variant.size_bytes)}` : ""})
          </a>
        ) : (
          <span className="muted">Файла больше нет на диске</span>
        )}
        {variant.postings.length ? (
          <div className="feed-posts">
            {variant.postings.map((p) => (
              <PostingChip key={`${p.origin}-${p.id}`} posting={p} busy={unmark.isPending} onRemove={() => unmark.mutate(p.id)} />
            ))}
          </div>
        ) : null}
        <div className="feed-mark">
          <span className="muted">Отметить: запощено в</span>
          {FEED_PLATFORMS.map((p) => (
            <button
              key={p.key}
              type="button"
              className={`btn sm feed-mark-btn${posting === p.key ? " on" : ""}`}
              onClick={() => setPosting(posting === p.key ? null : p.key)}
            >
              {p.label}
            </button>
          ))}
        </div>
        {posting ? (
          <form
            className="feed-mark-form"
            onSubmit={(e) => {
              e.preventDefault();
              mark.mutate(posting);
            }}
          >
            <input
              className="input"
              type="url"
              inputMode="url"
              placeholder="Ссылка (необязательно)"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <button type="submit" className="btn primary sm" disabled={mark.isPending}>
              {mark.isPending ? "Отмечаю…" : `Запощено в ${platformName(posting)}`}
            </button>
          </form>
        ) : null}
      </div>
    </div>
  );
}

const STATUS_ACTIONS: { status: FeedStatus; label: string; cls: string }[] = [
  { status: "accepted", label: "Принять", cls: "primary" },
  { status: "review", label: "На проверку", cls: "" },
  { status: "rejected", label: "Отклонить", cls: "danger-outline" },
];

/** Версия: заметка, вердикт, кнопки статуса и варианты. */
function VersionBlock({ version, latest }: { version: FeedVersion; latest: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [verdict, setVerdict] = useState(version.verdict);
  const [open, setOpen] = useState(latest);
  useEffect(() => setVerdict(version.verdict), [version.verdict]);
  const save = useMutation({
    mutationFn: (status?: FeedStatus) =>
      feedApi.setStatus(version.id, { ...(status ? { status } : {}), verdict: verdict.trim() }),
    onSuccess: (v) => {
      toast.success(`${v.name}: ${v.status_label}`);
      void qc.invalidateQueries({ queryKey: feedKeys.all });
    },
    onError: (e) => toast.error(errorText(e, "Не удалось сохранить")),
  });
  return (
    <section className={`feed-ver${latest ? " latest" : ""}`}>
      <button type="button" className="feed-ver-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="feed-vname">{version.name}</span>
        <StatusPill status={version.status} label={version.status_label} />
        <PlatformBadges platforms={version.posted_platforms} />
        <span className="muted feed-ver-when">
          {when(version.created_at)} · {plural(version.variants.length, "файл", "файла", "файлов")}
        </span>
        <Icon name="down" size={16} className={`feed-chev${open ? " open" : ""}`} />
      </button>
      {version.note ? <p className="feed-note">{version.note}</p> : null}
      {version.verdict && !open ? <p className="feed-verdict">«{version.verdict}»</p> : null}
      {open ? (
        <>
          <div className="feed-judge">
            <textarea
              className="input"
              rows={2}
              placeholder="Вердикт: что понравилось, что переделать"
              value={verdict}
              onChange={(e) => setVerdict(e.target.value)}
            />
            <div className="feed-judge-btns">
              {STATUS_ACTIONS.map((a) => (
                <button
                  key={a.status}
                  type="button"
                  className={`btn sm ${a.cls}${version.status === a.status ? " on" : ""}`}
                  disabled={save.isPending}
                  onClick={() => save.mutate(a.status)}
                >
                  {a.label}
                </button>
              ))}
              {verdict.trim() !== version.verdict ? (
                <button type="button" className="btn ghost sm" disabled={save.isPending} onClick={() => save.mutate(undefined)}>
                  Сохранить вердикт
                </button>
              ) : null}
            </div>
            {version.status_at ? (
              <span className="muted feed-judge-by">
                {version.status_by === "agent" ? "Отметил агент" : "Владелец"} · {when(version.status_at)}
              </span>
            ) : null}
          </div>
          <div className="feed-vars">
            {version.variants.length ? (
              version.variants.map((v) => <VariantRow key={v.id} variant={v} workId={version.work_id} />)
            ) : (
              <p className="muted">В этой версии нет файлов</p>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}

function WorkView({ workId, onBack }: { workId: number; onBack: () => void }) {
  const work = useQuery({
    queryKey: feedKeys.work(workId),
    queryFn: () => feedApi.work(workId),
    // Пока собирается превью — подглядываем раз в 5 с.
    refetchInterval: (q) =>
      q.state.data?.versions?.some((v) => v.variants.some((x) => x.preview.status === "queued" || x.preview.status === "running"))
        ? 5000
        : false,
  });
  if (work.isLoading) return <Loading />;
  if (work.isError || !work.data) return <ErrorState error={work.error} onRetry={() => work.refetch()} />;
  const w = work.data;
  const versions = w.versions ?? [];
  return (
    <div className="feed-work">
      <button type="button" className="btn ghost sm feed-back" onClick={onBack}>
        ← Вся лента
      </button>
      <header className="feed-work-head">
        <h1>{w.title}</h1>
        <p className="muted">
          {[w.project_name, w.kind_label, plural(versions.length, "версия", "версии", "версий")].filter(Boolean).join(" · ")}
        </p>
        <div className="feed-work-flags">
          {w.ready_unposted ? <span className="feed-flag">Готово, не выложено</span> : null}
          <PlatformBadges platforms={w.posted_platforms} />
        </div>
      </header>
      {versions.length ? (
        versions.map((v, i) => <VersionBlock key={v.id} version={v} latest={i === 0} />)
      ) : (
        <EmptyState icon="🎞" title="Версий пока нет" hint="Агент добавит версию через feed_add_version" />
      )}
    </div>
  );
}

/** «Лента»: каждая готовая работа одним местом — версии, файлы, вердикт и где уже выложено. */
export function FeedPage() {
  const [params, setParams] = useSearchParams();
  const workId = Number(params.get("work") || 0);
  const filter = (params.get("f") ?? "") as FeedFilter;
  const kind = params.get("kind") ?? "";
  const project = params.get("project") ?? "";
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: key !== "work" });
  };
  const list = useQuery({
    queryKey: feedKeys.list({ filter, kind, project_id: project ? Number(project) : null }),
    queryFn: () => feedApi.list({ filter, kind, project_id: project ? Number(project) : null, limit: 200 }),
    enabled: !workId,
    refetchInterval: 60_000,
  });
  // Проекты для фильтра — из самой ленты (все работы без фильтра проекта).
  const all = useQuery({ queryKey: feedKeys.list({ limit: 200 }), queryFn: () => feedApi.list({ limit: 200 }), enabled: !workId });
  const projects = Array.from(
    new Map((all.data?.items ?? []).filter((w) => w.project_id).map((w) => [w.project_id as number, w.project_name])).entries(),
  );

  useEffect(() => {
    if (workId) window.scrollTo({ top: 0 });
  }, [workId]);

  if (workId) return <WorkView workId={workId} onBack={() => set("work", "")} />;

  const counts = list.data?.counts ?? {};
  return (
    <div className="feed-page">
      <PageHead title="Лента" sub="Готовые работы: версии, вердикты и где уже выложено" />
      <div className="feed-filters">
        <div className="feed-chips">
          {FILTERS.map((f) => (
            <button
              key={f.key || "all"}
              type="button"
              className={`schip schip--${f.key === "ready_unposted" ? "done" : f.key === "review" ? "processing" : "all"}${filter === f.key ? " active" : ""}`}
              onClick={() => set("f", f.key)}
            >
              {f.label} <span className="schip-n">{counts[f.countKey] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="feed-selects">
          <select className="input" value={project} onChange={(e) => set("project", e.target.value)} aria-label="Проект">
            <option value="">Все проекты</option>
            {projects.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
          <select className="input" value={kind} onChange={(e) => set("kind", e.target.value)} aria-label="Вид работы">
            <option value="">Все виды</option>
            {FEED_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {list.isLoading ? (
        <Loading />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : !list.data?.items.length ? (
        <EmptyState
          icon="📰"
          title={filter || kind || project ? "Под этот фильтр ничего нет" : "Лента пока пустая"}
          hint={filter || kind || project ? "Снимите фильтр, чтобы увидеть все работы" : "Готовые работы появятся здесь, когда агент сдаст первую версию"}
        />
      ) : (
        <div className="feed-grid">
          {list.data.items.map((w) => (
            <FeedCard key={w.id} work={w} onOpen={() => set("work", String(w.id))} />
          ))}
        </div>
      )}
    </div>
  );
}
