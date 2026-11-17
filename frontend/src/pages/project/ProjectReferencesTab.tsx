import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import {
  referenceKeys,
  referencesApi,
  type Curve,
  type Reference,
  type ReferenceKind,
  type ReferencePatch,
} from "@/api/references";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Icon } from "@/components/Icon";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading, formatDuration, plural } from "@/components/ui";
import "@/styles/references.css";

/**
 * Проект → «Референсы» (app/references.py): чужие ролики, картинки, звуки и страницы, на которые хочется быть
 * похожим. Владелец кидает файл с телефона или ссылку, пишет «что взять» и отмечает быстрые теги; сервис фоном качает
 * и разбирает: склейки и темп, доли музыки, удары звука, палитра, лист кадров. Главные (★) агент чата проекта видит
 * как стиль проекта, остальные — по запросу (references_list / reference_get).
 */

const DEFAULT_TAGS = ["темп", "склейки", "подписи", "цвет", "переходы", "звук", "музыка", "сюжет", "крючок"];
type Show = "all" | "pinned" | ReferenceKind;
const SHOW: [Show, string][] = [
  ["all", "Все"],
  ["pinned", "★ Главные"],
  ["video", "Видео"],
  ["image", "Картинки"],
  ["audio", "Звук"],
  ["link", "Ссылки"],
];
const KIND_ICON: Record<ReferenceKind, string> = { video: "film", image: "image", audio: "play", link: "arrow" };

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const pct = (v: number | undefined) => `${Math.round((v ?? 0) * 100)} %`;
const num = (v: number | undefined, digits = 0) =>
  v == null ? "—" : v.toLocaleString("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: 0 });

function dateLabel(sql: string): string {
  const d = new Date(sql.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

function stateLabel(ref: Reference): string {
  if (ref.status === "queued") return ref.url && !ref.has_file ? "в очереди: скачать и разобрать" : "в очереди на разбор";
  if (ref.status === "running") return ref.stage === "download" ? "скачиваю…" : "разбираю…";
  return "";
}

/* ---------- Графики ---------- */

/** Столбики по времени: темп склеек (склеек в минуту) или громкость. Подпись — что значит и шкала. */
function CurveChart({ curve, kind, duration }: { curve: Curve; kind: "pace" | "energy"; duration?: number }) {
  const values = curve.values;
  if (!values.length) return null;
  const max = Math.max(...values, kind === "energy" ? 1 : 1e-6);
  const w = 300;
  const h = 56;
  const gap = values.length > 40 ? 0.6 : 1.5;
  const bw = w / values.length - gap;
  const peak = values.indexOf(Math.max(...values));
  const total = duration ?? curve.step * values.length;
  return (
    <figure className={`ref-chart ref-chart--${kind}`}>
      <figcaption>
        <span>{kind === "pace" ? "Темп склеек по ходу ролика" : "Громкость по ходу"}</span>
        <span className="muted mono">
          {kind === "pace" ? `пик ${num(values[peak])}/мин около ${formatDuration(peak * curve.step)}` : formatDuration(total)}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img"
        aria-label={kind === "pace" ? "Склеек в минуту по окнам" : "Громкость по окнам"}>
        {values.map((v, i) => {
          const bh = Math.max(v > 0 ? 2 : 0.6, (v / max) * (h - 2));
          return (
            <rect key={i} x={i * (bw + gap)} y={h - bh} width={Math.max(0.6, bw)} height={bh} rx={Math.min(1.5, bw / 2)}
              className={i === peak && kind === "pace" ? "is-peak" : undefined}>
              <title>{`${num(i * curve.step, 1)}–${num((i + 1) * curve.step, 1)} с: ${
                kind === "pace" ? `${num(v)} склеек/мин` : `${pct(v)}`}`}</title>
            </rect>
          );
        })}
      </svg>
      <div className="ref-chart-axis mono">
        <span>0:00</span>
        <span>{formatDuration(total)}</span>
      </div>
    </figure>
  );
}

function Palette({ colors }: { colors: { hex: string; share: number }[] }) {
  if (!colors.length) return null;
  return (
    <div className="ref-palette" aria-label="Главные цвета">
      {colors.map((c) => (
        <span key={c.hex} style={{ background: c.hex, flexGrow: Math.max(c.share, 0.04) }}
          title={`${c.hex} · ${pct(c.share)}`} />
      ))}
    </div>
  );
}

/* ---------- Числа разбора ---------- */

function Facts({ refItem }: { refItem: Reference }) {
  const a = refItem.analysis;
  if (!a) return null;
  const facts: { k: string; v: string; hint?: string }[] = [];
  if (a.kind === "video" && a.shots) {
    facts.push({ k: "склеек в минуту", v: num(a.cuts_per_min), hint: `${plural(a.shots, "план", "плана", "планов")}` });
    if (a.shot_len) facts.push({ k: "план в среднем", v: `${num(a.shot_len.median, 1)} с`, hint: `от ${num(a.shot_len.min, 1)} до ${num(a.shot_len.max, 1)} с` });
  }
  const m = a.music;
  if (m?.bpm && m.clear) {
    facts.push({ k: "музыка", v: `${num(m.bpm)} BPM` });
    if (a.kind === "video" && m.cuts_total)
      facts.push({
        k: "склеек в долю",
        v: pct(m.cuts_on_beat_share),
        hint: `${m.cuts_on_beat} из ${m.cuts_total}, случайно было бы ${pct(m.chance_share)}`,
      });
  } else if (m && !m.silent && (a.kind === "video" || a.kind === "audio")) {
    facts.push({ k: "ритм", v: "не явный", hint: "речь или музыка без чёткого бита" });
  }
  if (a.hits?.count) facts.push({ k: "ударов звука", v: num(a.hits.count), hint: `${num(a.hits.on_beat)} из них в долю` });
  if (a.flashes) facts.push({ k: "вспышек", v: num(a.flashes) });
  if (m?.drops?.length) facts.push({ k: "взлётов музыки", v: num(m.drops.length), hint: m.drops.map((t) => `${num(t, 1)} с`).join(", ") });
  if (a.kind === "image" && a.width) facts.push({ k: "размер", v: `${a.width}×${a.height}` });
  if (!facts.length) return null;
  return (
    <dl className="ref-facts">
      {facts.map((f) => (
        <div key={f.k} title={f.hint}>
          <dt>{f.k}</dt>
          <dd>
            {f.v}
            {f.hint ? <small>{f.hint}</small> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/* ---------- Медиа ---------- */

function RefMedia({ refItem }: { refItem: Reference }) {
  const [open, setOpen] = useState(false);
  const { urls } = refItem;
  const poster = urls.poster ?? urls.poster_remote;
  const vertical = (refItem.height ?? 0) > (refItem.width ?? 0);
  const src = urls.preview ?? urls.file;
  if (refItem.kind === "video" && open && src) {
    return (
      <div className={`ref-player${vertical ? " is-vertical" : ""}`}>
        {/* preload="none" и лёгкое превью ≤720p: с телефона на медленной сети не качается заранее. */}
        <video src={src} poster={poster} controls autoPlay playsInline preload="none" />
        <button type="button" className="btn ghost sm ref-player-close" onClick={() => setOpen(false)}>
          Свернуть
        </button>
      </div>
    );
  }
  const thumb = (
    <span className={`ref-thumb ref-thumb--${refItem.kind}${vertical ? " is-vertical" : ""}`}>
      {poster ? <img src={poster} alt="" loading="lazy" /> : <Icon name={KIND_ICON[refItem.kind]} size={26} />}
      {refItem.kind === "video" && src ? <span className="ref-thumb-play" aria-hidden="true">▶</span> : null}
      {refItem.duration_sec ? <span className="ref-thumb-dur mono">{formatDuration(refItem.duration_sec)}</span> : null}
    </span>
  );
  if (refItem.kind === "video" && src)
    return (
      <button type="button" className="ref-thumb-btn" onClick={() => setOpen(true)} aria-label={`Смотреть: ${refItem.title}`}>
        {thumb}
      </button>
    );
  if (refItem.kind === "image" && urls.file)
    return (
      <a className="ref-thumb-btn" href={urls.file} target="_blank" rel="noreferrer" aria-label="Открыть картинку целиком">
        {thumb}
      </a>
    );
  if (refItem.kind === "link" && refItem.url)
    return (
      <a className="ref-thumb-btn" href={refItem.url} target="_blank" rel="noreferrer noopener" aria-label="Открыть страницу">
        {thumb}
      </a>
    );
  return thumb;
}

/* ---------- Карточка ---------- */

function TagPicker({ tags, quick, onChange }: { tags: string[]; quick: string[]; onChange: (t: string[]) => void }) {
  const [own, setOwn] = useState("");
  const all = [...quick, ...tags.filter((t) => !quick.includes(t))];
  const toggle = (t: string) => onChange(tags.includes(t) ? tags.filter((x) => x !== t) : [...tags, t]);
  return (
    <div className="ref-tags" role="group" aria-label="Что взять: быстрые теги">
      {all.map((t) => (
        <button key={t} type="button" className={`ref-tag${tags.includes(t) ? " on" : ""}`} aria-pressed={tags.includes(t)}
          onClick={() => toggle(t)}>
          {t}
        </button>
      ))}
      <input
        className="ref-tag-own"
        placeholder="+ свой"
        value={own}
        maxLength={32}
        onChange={(e) => setOwn(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          e.preventDefault();
          const t = own.trim().toLowerCase().replace(/^#/, "");
          if (t && !tags.includes(t)) onChange([...tags, t]);
          setOwn("");
        }}
      />
    </div>
  );
}

function RefCard({ refItem, quick, onDelete }: { refItem: Reference; quick: string[]; onDelete: (r: Reference) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState(refItem.title);
  const [note, setNote] = useState(refItem.note);
  // Название пришло из фона (заголовок ролика по ссылке) — показать, пока поле не трогали.
  const [seenTitle, setSeenTitle] = useState(refItem.title);
  if (refItem.title !== seenTitle) {
    setSeenTitle(refItem.title);
    setTitle(refItem.title);
  }
  const refresh = () => qc.invalidateQueries({ queryKey: referenceKeys.list(refItem.project_id) });
  const save = useMutation({
    mutationFn: (patch: ReferencePatch) => referencesApi.update(refItem.id, patch),
    onSuccess: () => void refresh(),
    onError: (e) => toast.error(errText(e, "Не удалось сохранить")),
  });
  const again = useMutation({
    mutationFn: () => referencesApi.reanalyze(refItem.id),
    onSuccess: () => void refresh(),
    onError: (e) => toast.error(errText(e, "Не удалось запустить разбор")),
  });
  const a = refItem.analysis;
  const busy = refItem.status === "queued" || refItem.status === "running";
  const meta = [
    refItem.kind_label,
    refItem.aspect,
    a?.page?.site,
    refItem.created_at ? `добавлен ${dateLabel(refItem.created_at)}` : "",
  ].filter(Boolean);
  const sheet = refItem.urls.sheet;

  return (
    <article className={`ref-card${refItem.pinned ? " is-pinned" : ""}`}>
      <div className="ref-head">
        <RefMedia refItem={refItem} />
        <div className="ref-head-body">
          <div className="ref-title-row">
            <input
              className="ref-title"
              value={title}
              placeholder="Без названия"
              maxLength={160}
              aria-label="Название"
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => title.trim() !== refItem.title && save.mutate({ title })}
              onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            />
            <button
              type="button"
              className={`ref-pin${refItem.pinned ? " on" : ""}`}
              aria-pressed={refItem.pinned}
              title={refItem.pinned ? "Главный: агент чата видит его как стиль проекта. Снять" : "Сделать главным: агент будет держаться этого стиля"}
              onClick={() => save.mutate({ pinned: !refItem.pinned })}
            >
              <Icon name="star" size={17} />
              <span>{refItem.pinned ? "главный" : "сделать главным"}</span>
            </button>
          </div>
          <div className="ref-meta muted">{meta.join(" · ")}</div>
          {busy ? (
            <div className="ref-state">
              <span className="spinner" aria-hidden="true" />
              {stateLabel(refItem)}
            </div>
          ) : null}
          {refItem.status === "failed" ? (
            <div className="ref-state is-bad">
              <span>Не вышло: {refItem.error || "ошибка разбора"}</span>
              <button type="button" className="btn ghost sm" disabled={again.isPending} onClick={() => again.mutate()}>
                Ещё раз
              </button>
            </div>
          ) : null}
          {a?.page?.description ? <p className="ref-page-desc">{a.page.description}</p> : null}
        </div>
      </div>

      {refItem.status === "ready" && a ? (
        <div className="ref-analysis">
          <Facts refItem={refItem} />
          {a.pace?.values.length && (a.shots ?? 0) > 1 ? (
            <CurveChart curve={a.pace} kind="pace" duration={a.duration} />
          ) : null}
          {a.kind === "audio" && a.energy?.values.length ? <CurveChart curve={a.energy} kind="energy" duration={a.duration} /> : null}
          {a.palette?.length ? <Palette colors={a.palette} /> : null}
          {refItem.kind === "audio" && refItem.urls.file ? (
            <audio className="ref-audio" src={refItem.urls.file} controls preload="none" />
          ) : null}
          {sheet ? (
            <details className="ref-sheet">
              <summary>Лист кадров{a.keyframes?.length ? ` · ${plural(a.keyframes.length, "кадр", "кадра", "кадров")}` : ""}</summary>
              <a href={sheet} target="_blank" rel="noreferrer">
                <img src={sheet} alt="Ключевые кадры: по кадру на план" loading="lazy" />
              </a>
            </details>
          ) : null}
        </div>
      ) : null}

      <label className="ref-note">
        <span>Что взять</span>
        <textarea
          className="input"
          rows={3}
          placeholder="Например: склейки строго в бит, крупные подписи по 2 слова, финал с резким стопом"
          value={note}
          maxLength={4000}
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => note.trim() !== refItem.note.trim() && save.mutate({ note })}
        />
      </label>
      <TagPicker tags={refItem.tags} quick={quick} onChange={(tags) => save.mutate({ tags })} />

      <footer className="ref-foot">
        {refItem.url ? (
          <a className="ref-source" href={refItem.url} target="_blank" rel="noreferrer noopener">
            {(() => {
              try {
                return new URL(refItem.url).hostname.replace(/^www\./, "");
              } catch {
                return "ссылка";
              }
            })()}
          </a>
        ) : refItem.original_filename ? (
          <span className="muted ref-source">{refItem.original_filename}</span>
        ) : null}
        {refItem.status === "ready" && refItem.kind !== "link" ? (
          <button type="button" className="btn ghost sm" disabled={again.isPending} onClick={() => again.mutate()}
            title="Разобрать заново">
            ↻
          </button>
        ) : null}
        <button type="button" className="btn ghost sm ref-del" onClick={() => onDelete(refItem)} title="Удалить вместе с файлами">
          <Icon name="trash" size={15} />
        </button>
      </footer>
    </article>
  );
}

/* ---------- Добавление ---------- */

function AddBar({ projectId, onDrop }: { projectId: number; onDrop: (files: File[]) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const add = useMutation({
    mutationFn: () => referencesApi.link(projectId, url.trim()),
    onSuccess: () => {
      toast.success("Ссылка добавлена: скачаю и разберу в фоне");
      setUrl("");
      void qc.invalidateQueries({ queryKey: referenceKeys.list(projectId) });
    },
    onError: (e) => toast.error(errText(e, "Не удалось добавить ссылку")),
  });
  return (
    <form
      className="ref-add"
      onSubmit={(e) => {
        e.preventDefault();
        if (url.trim()) add.mutate();
      }}
    >
      <input
        className="input"
        type="url"
        inputMode="url"
        placeholder="Ссылка: TikTok, YouTube, Instagram, картинка, трек, страница"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        aria-label="Ссылка на референс"
      />
      <button className="btn primary" type="submit" disabled={!url.trim() || add.isPending}>
        <Icon name="plus" size={16} />
        {add.isPending ? "Добавляю…" : "Добавить"}
      </button>
      <button className="btn" type="button" onClick={() => fileRef.current?.click()}>
        <Icon name="file" size={16} />
        Файл
      </button>
      <input
        ref={fileRef}
        type="file"
        hidden
        multiple
        accept="video/*,image/*,audio/*"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onDrop(files);
        }}
      />
    </form>
  );
}

export function ProjectReferencesTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [show, setShow] = useState<Show>("all");
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [pending, setPending] = useState<Reference | null>(null);
  const query = useQuery({
    queryKey: referenceKeys.list(projectId),
    queryFn: () => referencesApi.list(projectId),
    // Пока что-то качается или разбирается — подтягиваем.
    refetchInterval: (q) =>
      q.state.data?.references.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: referenceKeys.list(projectId) });
  const remove = useMutation({
    mutationFn: (id: number) => referencesApi.remove(id),
    onSuccess: () => {
      setPending(null);
      void refresh();
    },
    onError: (e) => toast.error(errText(e, "Не удалось удалить")),
  });
  const upload = async (files: File[]) => {
    setUploading((n) => n + files.length);
    for (const file of files) {
      try {
        await referencesApi.upload(projectId, file);
      } catch (e) {
        toast.error(`${file.name}: ${errText(e, "не загрузился")}`);
      } finally {
        setUploading((n) => n - 1);
        void refresh();
      }
    }
  };
  const items = query.data?.references ?? [];
  const quick = query.data?.quick_tags?.length ? query.data.quick_tags : DEFAULT_TAGS;
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length, pinned: items.filter((r) => r.pinned).length };
    for (const r of items) c[r.kind] = (c[r.kind] ?? 0) + 1;
    return c;
  }, [items]);
  const shown = items.filter((r) => (show === "all" ? true : show === "pinned" ? r.pinned : r.kind === show));

  return (
    <div
      className={`ref-tab${drag ? " is-drag" : ""}`}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDrag(true);
        }
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDrag(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const files = Array.from(e.dataTransfer.files);
        if (files.length) void upload(files);
      }}
    >
      <p className="ref-intro">
        Примеры, на которые хочется быть похожим: чужие ролики, картинки, звуки. Напиши, <b>что взять</b>, и отметь
        <b> ★ главные</b>: агент проекта держится их приёмов, но чужие кадры и музыку не берёт.
      </p>
      <AddBar projectId={projectId} onDrop={(f) => void upload(f)} />
      {uploading > 0 ? (
        <div className="ref-uploading">
          <span className="spinner" aria-hidden="true" />
          Загружаю {plural(uploading, "файл", "файла", "файлов")}…
        </div>
      ) : null}

      {query.isLoading ? <Loading /> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={() => query.refetch()} /> : null}
      {query.data && items.length === 0 ? (
        <EmptyState
          icon="🎯"
          title="Референсов пока нет"
          hint="Кинь ссылку на ролик или файл с телефона — сервис посчитает темп склеек, ритм и цвета"
        />
      ) : null}
      {items.length > 0 ? (
        <>
          <nav className="pills ref-filter" aria-label="Какие референсы показать">
            {SHOW.filter(([key]) => key === "all" || counts[key]).map(([key, label]) => (
              <button key={key} type="button" className={`pill${show === key ? " on" : ""}`} onClick={() => setShow(key)}>
                {label} <span className="muted">{counts[key] ?? 0}</span>
              </button>
            ))}
          </nav>
          <div className="ref-grid">
            {shown.map((r) => (
              <RefCard key={r.id} refItem={r} quick={quick} onDelete={setPending} />
            ))}
          </div>
        </>
      ) : null}
      <ConfirmDialog
        open={pending != null}
        title="Удалить референс?"
        body={pending ? `«${pending.title || "без названия"}» удалится вместе с файлом и разбором.` : undefined}
        busy={remove.isPending}
        onConfirm={() => pending && remove.mutate(pending.id)}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}

export default ProjectReferencesTab;
