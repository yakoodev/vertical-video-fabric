import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { accountsApi } from "@/api/accounts";
import { clipGroupKeys, clipGroupsApi, type ClipSegment, type ClipVersion, type ClipView } from "@/api/clipGroups";
import { clipPlansApi } from "@/api/clipPlans";
import { clipsApi } from "@/api/clips";
import { ApiError } from "@/api/client";
import { coverDocsApi } from "@/api/coverDocs";
import { qk } from "@/api/keys";
import { navKeys } from "@/api/nav";
import { playbackApi } from "@/api/playback";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Icon } from "@/components/Icon";
import { MissingSourceNotice } from "@/components/MissingSourceNotice";
import { PublishDialog } from "@/components/PublishDialog";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading, formatDuration, plural } from "@/components/ui";
import { useModules } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";
import { clipEditorPath, clipPath, projectPath, videoPath } from "@/lib/paths";
import { AgentPanel } from "@/pages/workspace/motion/AgentPanel";

// Плеер монтажа тянет за собой движок моушна: грузится только по кнопке.
const WatchMontage = lazyPage<{ clipPlanId: number; title?: string; onClose: () => void }>(
  () => import("@/pages/workspace/preview/WatchMontage"),
  "WatchMontage",
);

const errorText = (e: unknown, fallback: string): string => {
  if (e instanceof ApiError) {
    const problems = (e.detail as { problems?: unknown } | undefined)?.problems;
    if (Array.isArray(problems) && problems.length) return problems.slice(0, 3).join("; ");
    return e.message;
  }
  return fallback;
};

/** Время базы (UTC) по-человечески: «5 ноября, 08:58». */
export function dateTimeRu(value: string): string {
  const iso = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return value;
  const day = date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${time}`;
}

/** Таймкод куска без потерь: «17:23.5», целые секунды — «17:23». */
export function timeText(sec: number): string {
  const whole = Math.floor(sec);
  const tenths = Math.round((sec - whole) * 10);
  return `${formatDuration(whole)}${tenths && tenths < 10 ? `.${tenths}` : ""}`;
}

/** «19:05», «1:02:33.5» или просто секунды. */
export function parseTime(text: string): number | null {
  const parts = text.trim().replace(",", ".").split(":");
  if (!parts.length || parts.length > 3 || parts.some((p) => p === "" || Number.isNaN(Number(p)))) return null;
  return parts.reduce((total, part) => total * 60 + Number(part), 0);
}

function SegmentsCard({ view, onChanged }: { view: ClipView; onChanged: () => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState({ title: "", from: "", to: "" });
  const total = view.segments.reduce((sum, s) => sum + Math.max(0, s.end_sec - s.start_sec), 0);

  type SpecPiece = { segment_id?: number; title: string; start_sec: number; end_sec: number };
  const save = useMutation({
    mutationFn: async (change: (pieces: SpecPiece[]) => SpecPiece[]) => {
      const spec = await clipPlansApi.getSpec(view.id);
      spec.pieces = change((spec.pieces as SpecPiece[]) ?? []);
      return clipPlansApi.putSpec(view.id, spec);
    },
    onSuccess: () => {
      setEditing(null);
      onChanged();
    },
    onError: (e) => toast.error(errorText(e, "Не удалось сохранить куски")),
  });

  const open = (segment: ClipSegment) => {
    setEditing(segment.id);
    setDraft({ title: segment.title, from: timeText(segment.start_sec), to: timeText(segment.end_sec) });
  };
  const apply = () => {
    const from = parseTime(draft.from);
    const to = parseTime(draft.to);
    if (from == null || to == null || to <= from) {
      toast.error("Время: от меньше до, например 19:05 и 19:44");
      return;
    }
    // Поле не трогали — время остаётся точным: в поле оно округлено до десятых.
    const same = (seg: SpecPiece) => {
      const start = draft.from === timeText(seg.start_sec) ? seg.start_sec : from;
      const end = draft.to === timeText(seg.end_sec) ? seg.end_sec : to;
      return { ...seg, title: draft.title.trim() || seg.title, start_sec: start, end_sec: end };
    };
    save.mutate((pieces) =>
      editing === "new"
        ? [...pieces, { title: draft.title.trim() || `Кусок ${pieces.length + 1}`, start_sec: from, end_sec: to }]
        : pieces.map((p) => (p.segment_id === editing ? same(p) : p)),
    );
  };
  const move = (id: number, delta: number) =>
    save.mutate((pieces) => {
      const next = [...pieces];
      const at = next.findIndex((p) => p.segment_id === id);
      const to = at + delta;
      if (at < 0 || to < 0 || to >= next.length) return next;
      [next[at], next[to]] = [next[to], next[at]];
      return next;
    });
  const form = (
    <div className="cseg-form">
      <input className="input" value={draft.title} placeholder="Название куска" aria-label="Название куска" onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
      <input className="input mono" value={draft.from} aria-label="Начало" placeholder="от" onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
      <input className="input mono" value={draft.to} aria-label="Конец" placeholder="до" onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
      <button type="button" className="btn primary sm" disabled={save.isPending} onClick={apply}>Сохранить</button>
      <button type="button" className="btn ghost sm" onClick={() => setEditing(null)}>Отмена</button>
    </div>
  );
  return (
    <section className="ccard cseg">
      <div className="ccard-h">
        <h2>Сегменты</h2>
        <span className="muted">{plural(view.segments.length, "кусок", "куска", "кусков")}, {formatDuration(total)}</span>
        <button
          type="button"
          className="btn ghost sm"
          title="Добавить кусок записи"
          onClick={() => {
            const last = view.segments[view.segments.length - 1];
            const at = last ? last.end_sec : 0;
            setEditing("new");
            setDraft({ title: "", from: timeText(at), to: timeText(at + 5) });
          }}
        >
          + Кусок
        </button>
      </div>
      <div className="cseg-bar" aria-hidden>
        {view.segments.map((s) => <i key={s.id} style={{ flex: Math.max(0.1, s.end_sec - s.start_sec) }} title={s.title} />)}
      </div>
      <div className="cseg-list">
        {view.segments.map((segment, i) =>
          editing === segment.id ? (
            <div key={segment.id} className="cseg-row editing">
              {form}
              <div className="cseg-tools">
                <button type="button" className="btn ghost sm" disabled={i === 0 || save.isPending} onClick={() => move(segment.id, -1)}>↑ Выше</button>
                <button type="button" className="btn ghost sm" disabled={i === view.segments.length - 1 || save.isPending} onClick={() => move(segment.id, 1)}>↓ Ниже</button>
                <button
                  type="button"
                  className="btn ghost sm"
                  disabled={view.segments.length < 2 || save.isPending}
                  title={view.segments.length < 2 ? "Единственный кусок убрать нельзя" : ""}
                  onClick={() => save.mutate((pieces) => pieces.filter((p) => p.segment_id !== segment.id))}
                >
                  Убрать кусок
                </button>
              </div>
            </div>
          ) : (
            <button key={segment.id} type="button" className="cseg-row" title="Изменить кусок" onClick={() => open(segment)}>
              <span className="cseg-n">{i + 1}</span>
              <span className="cseg-t">{segment.title || "Кусок"}</span>
              <span className="cseg-time mono">{formatDuration(segment.start_sec)}–{formatDuration(segment.end_sec)}</span>
            </button>
          ),
        )}
        {editing === "new" ? <div className="cseg-row editing">{form}</div> : null}
      </div>
    </section>
  );
}

function RollbackDialog({
  view,
  version,
  busy,
  canRender,
  onCancel,
  onConfirm,
}: {
  view: ClipView;
  version: ClipVersion;
  busy: boolean;
  canRender: boolean;
  onCancel: () => void;
  onConfirm: (render: boolean) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  const otherFormat = version.format && version.format !== view.format;
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Откат версии" onClick={(e) => e.stopPropagation()}>
        <h3 style={{ margin: "0 0 8px" }}>Откат к версии {version.label} от {dateTimeRu(version.at)}</h3>
        <div className="muted" style={{ fontSize: 14, marginBottom: 16, display: "grid", gap: 6 }}>
          <span>Вернутся правки монтажа этой версии: куски, слои, субтитры, звук. Заголовок и описание останутся.</span>
          <span>Ни одна версия не удаляется: текущий монтаж останется в списке, к нему можно вернуться.</span>
          {otherFormat ? <span>Формат этой версии {version.format}, у клипа сейчас {view.format}: клип вернётся к её формату.</span> : null}
          {!canRender ? <span>Записи стрима нет: правки вернутся, новый рендер можно будет сделать после скачивания записи.</span> : null}
        </div>
        <div className="crb-actions">
          <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>Отмена</button>
          <button type="button" className="btn" onClick={() => onConfirm(false)} disabled={busy}>Только вернуть правки</button>
          <button
            type="button"
            className="btn primary"
            onClick={() => onConfirm(true)}
            disabled={busy || !canRender}
            title={canRender ? "" : "Для рендера нужна запись стрима"}
          >
            {busy ? "…" : "Вернуть правки и рендерить"}
          </button>
        </div>
      </div>
    </div>
  );
}

function VersionsCard({
  view,
  selected,
  onSelect,
  onChanged,
  canRender,
  onRender,
}: {
  view: ClipView;
  selected: ClipVersion | null;
  onSelect: (key: string) => void;
  onChanged: () => void;
  canRender: boolean;
  onRender: () => void;
}) {
  const toast = useToast();
  const navigate = useNavigate();
  const [target, setTarget] = useState<ClipVersion | null>(null);
  const [mergeWith, setMergeWith] = useState("");
  const [splitPlan, setSplitPlan] = useState<number | null>(null);
  const rollback = useMutation({
    mutationFn: ({ version }: { version: ClipVersion; render: boolean }) =>
      clipGroupsApi.rollback(view.id, { render_id: version.render_id, snapshot_id: version.snapshot_id }),
    onSuccess: (result, { render }) => {
      setTarget(null);
      toast.success(`Правки ${result.label} возвращены${render ? ", рендер поставлен" : ""}`);
      if (render) onRender();
      onChanged();
    },
    onError: (e) => toast.error(errorText(e, "Не удалось откатить")),
  });
  const newVersion = useMutation({
    mutationFn: () => clipGroupsApi.newVersion(view.id),
    onSuccess: (created) => {
      toast.success(`Новая версия: ${created.title}`);
      onChanged();
      navigate(clipEditorPath(view.source.id, created.id));
    },
    onError: (e) => toast.error(errorText(e, "Не удалось создать версию")),
  });
  const merge = useMutation({
    mutationFn: (other: number) => clipGroupsApi.merge(view.id, other),
    onSuccess: (result) => {
      toast.success("Клипы объединены");
      setMergeWith("");
      onChanged();
      navigate(clipPath(result.id));
    },
    onError: (e) => toast.error(errorText(e, "Не удалось объединить")),
  });
  const split = useMutation({
    mutationFn: (planId: number) => clipGroupsApi.split(view.id, planId),
    onSuccess: (result) => {
      toast.success("План стал отдельным клипом");
      setSplitPlan(null);
      onChanged();
      navigate(clipPath(result.rest_id));
    },
    onError: (e) => toast.error(errorText(e, "Не удалось разъединить")),
  });
  // «Последняя» — последний готовый файл; снимок «до отката» файла не имеет.
  const newest = view.versions.find((v) => v.status === "succeeded");
  const mergeable = view.neighbors.filter((n) => n.id !== view.id && n.format === view.format);
  return (
    <section className="ccard cvers">
      <div className="ccard-h">
        <h2>Версии монтажа <span className="ccard-n">{view.version_count}</span></h2>
        <button
          type="button"
          className="btn ghost sm"
          disabled={newVersion.isPending}
          title="Копия текущего монтажа новым планом этого клипа: старый останется версией"
          onClick={() => newVersion.mutate()}
        >
          + Новая версия
        </button>
      </div>
      <div className="cvers-list">
        {view.stale && view.versions.length ? (
          <div className="cver now" role="status">
            <div className="cver-th none"><Icon name="scissors" size={18} /></div>
            <div className="cver-b">
              <b>Текущий монтаж</b>
              <div className="cver-m">правки ещё не отрендерены</div>
              {view.pending.length ? <div className="cver-c" title={view.pending.join(", ")}>{view.pending.join(", ")}</div> : null}
            </div>
            <button type="button" className="btn sm" disabled={!canRender} title={canRender ? "" : "Для рендера нужна запись стрима"} onClick={onRender}>
              Рендерить
            </button>
          </div>
        ) : null}
        {view.versions.map((version) => {
          const isNewest = version === newest && !view.stale;
          const isSelected = selected?.key === version.key;
          const failed = ["failed", "error"].includes(version.status);
          const running = ["queued", "rendering"].includes(version.status);
          return (
            <div key={version.key} className={`cver${isSelected ? " on" : ""}`}>
              <button
                type="button"
                className="cver-main"
                disabled={!version.url}
                aria-pressed={isSelected}
                title={version.url ? "Показать этот рендер в плеере" : ""}
                onClick={() => onSelect(version.key)}
              >
                {version.thumb ? <img className="cver-th" src={version.thumb} alt="" loading="lazy" /> : <span className="cver-th none" />}
                <span className="cver-b">
                  <span className="cver-l">
                    <b title={version.label}>{version.label}</b>
                    {version === newest ? <span className="cchip ok">последняя</span> : null}
                    {running ? <span className="cchip run">рендер идёт</span> : null}
                    {failed ? <span className="cchip bad" title={version.error}>ошибка</span> : null}
                    {version.status === "snapshot" ? <span className="cchip">без рендера</span> : null}
                  </span>
                  <span className="cver-m">
                    {[version.format, version.duration_sec ? formatDuration(version.duration_sec) : "", dateTimeRu(version.at)].filter(Boolean).join(", ")}
                  </span>
                  <span className="cver-c" title={version.changes.join(", ")}>
                    {version.changes.join(", ") || (version.render_id ? "без новых правок" : "снимок перед откатом")}
                  </span>
                </span>
              </button>
              {isNewest ? null : version.can_rollback ? (
                <button type="button" className="btn sm cver-rb" onClick={() => setTarget(version)}>Откатить</button>
              ) : version.why_not ? (
                <span className="cver-no" title={version.why_not}>снимка нет</span>
              ) : null}
            </div>
          );
        })}
        {!view.versions.length ? <p className="muted cvers-empty">Рендеров пока нет. Смотреть монтаж можно и без них.</p> : null}
      </div>
      {view.drafts ? <div className="cvers-drafts muted">Ещё {plural(view.drafts, "пустой черновик", "пустых черновика", "пустых черновиков")} спрятано</div> : null}
      <details className="cvers-group">
        <summary>Объединить или разъединить</summary>
        <div className="cvers-group-b">
          {view.plans.length > 1 ? (
            <ul className="cplans">
              {view.plans.map((plan) => (
                <li key={plan.id}>
                  <span title={plan.title}><b>{plan.label}</b> {plan.title}</span>
                  <span className="muted">{plural(plan.renders, "рендер", "рендера", "рендеров")}</span>
                  <button type="button" className="btn ghost sm" onClick={() => setSplitPlan(plan.id)}>Отделить</button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">В этом клипе один монтаж.</p>
          )}
          <div className="cmerge">
            <select className="input" aria-label="С каким клипом объединить" value={mergeWith} onChange={(e) => setMergeWith(e.target.value)}>
              <option value="">Объединить с клипом…</option>
              {mergeable.map((n) => (
                <option key={n.id} value={n.id}>{n.name} ({formatDuration(n.start_sec)}, {plural(n.versions, "версия", "версии", "версий")})</option>
              ))}
            </select>
            <button type="button" className="btn sm" disabled={!mergeWith || merge.isPending} onClick={() => merge.mutate(Number(mergeWith))}>Объединить</button>
          </div>
        </div>
      </details>
      {target ? (
        <RollbackDialog
          view={view}
          version={target}
          busy={rollback.isPending}
          canRender={canRender}
          onCancel={() => setTarget(null)}
          onConfirm={(render) => rollback.mutate({ version: target, render })}
        />
      ) : null}
      <ConfirmDialog
        open={splitPlan != null}
        title="Отделить версию в отдельный клип?"
        body="Рендеры и правки останутся у неё. Скрипт группировки больше не свяжет её с этим клипом сам; объединить обратно можно здесь же."
        confirmLabel="Отделить"
        danger={false}
        busy={split.isPending}
        onCancel={() => setSplitPlan(null)}
        onConfirm={() => splitPlan != null && split.mutate(splitPlan)}
      />
    </section>
  );
}

function MetaCard({ view, onChanged }: { view: ClipView; onChanged: () => void }) {
  const toast = useToast();
  const { on } = useModules();
  const [title, setTitle] = useState(view.meta.title);
  const [description, setDescription] = useState(view.meta.description);
  const [tags, setTags] = useState(view.meta.hashtags.join(" "));
  const [savedAt, setSavedAt] = useState(false);
  // Текст сменился на сервере (рендер, агент, своё же сохранение): поля перечитываются,
  // но то, что владелец прямо сейчас правит, не затирается. Другой клип — всё заново.
  const known = useRef({ id: view.id, title: view.meta.title, description: view.meta.description, tags: view.meta.hashtags.join(" ") });
  useEffect(() => {
    const fresh = { id: view.id, title: view.meta.title, description: view.meta.description, tags: view.meta.hashtags.join(" ") };
    const prev = known.current;
    const sameClip = prev.id === fresh.id;
    if (!sameClip || title === prev.title) setTitle(fresh.title);
    if (!sameClip || description === prev.description) setDescription(fresh.description);
    if (!sameClip || tags === prev.tags) setTags(fresh.tags);
    if (!sameClip) setSavedAt(false);
    known.current = fresh;
  }, [view.id, view.meta.title, view.meta.description, view.meta.hashtags.join(" ")]); // eslint-disable-line react-hooks/exhaustive-deps
  const parse = (text: string) => text.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);
  const save = useMutation({
    mutationFn: (body: { title: string; description: string; hashtags: string[] }) => clipGroupsApi.saveMeta(view.id, body),
    onSuccess: () => {
      setSavedAt(true);
      onChanged();
    },
    onError: (e) => toast.error(errorText(e, "Не удалось сохранить текст")),
  });
  const dirty = title !== view.meta.title || description !== view.meta.description || tags !== view.meta.hashtags.join(" ");
  const persist = () => {
    if (dirty) save.mutate({ title, description, hashtags: parse(tags) });
  };
  const generate = useMutation({
    mutationFn: () => clipsApi.publishMeta(view.latest!.id),
    onSuccess: (m) => {
      setTitle(m.title);
      setDescription(m.description);
      setTags(m.hashtags.join(" "));
      save.mutate({ title: m.title, description: m.description, hashtags: m.hashtags });
      toast.success(m.has_transcript ? "Сгенерировано по речи клипа" : "Сгенерировано по описанию момента (транскрипта нет)");
    },
    onError: (e) => toast.error(errorText(e, "Не удалось сгенерировать")),
  });
  return (
    <section className="ccard cmeta">
      <div className="ccard-h">
        <h2>Описание и хэштеги</h2>
        {on("publish_meta") ? (
          <button
            type="button"
            className="btn sm"
            disabled={!view.latest || generate.isPending}
            title={view.latest ? "Заголовок, описание и хэштеги по речи и сути клипа. Стиль: пресет «Публикация» в Настройки → Промпты" : "Сначала нужен рендер"}
            onClick={() => generate.mutate()}
          >
            {generate.isPending ? "Генерирую…" : "✨ Сгенерировать"}
          </button>
        ) : null}
      </div>
      <label className="field">
        <span>Заголовок <span className="muted mono">{title.length}/100</span></span>
        <input className="input" maxLength={100} value={title} onChange={(e) => { setTitle(e.target.value); setSavedAt(false); }} onBlur={persist} />
      </label>
      <label className="field">
        <span>Описание</span>
        <textarea className="input" rows={3} value={description} onChange={(e) => { setDescription(e.target.value); setSavedAt(false); }} onBlur={persist} />
      </label>
      <label className="field">
        <span>Хэштеги</span>
        <input className="input" value={tags} onChange={(e) => { setTags(e.target.value); setSavedAt(false); }} onBlur={persist} />
      </label>
      <div className="cmeta-state muted" role="status" aria-live="polite">
        {save.isPending ? "Сохраняю…" : dirty ? "Сохранится, когда уйдёте с поля" : savedAt ? "Сохранено" : ""}
      </div>
    </section>
  );
}

function CoverCard({ view }: { view: ClipView }) {
  const toast = useToast();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const latest = view.latest;
  const covers = useQuery({
    queryKey: qk.clipCovers(latest?.id ?? 0),
    queryFn: () => clipsApi.covers(latest!.id),
    enabled: Boolean(latest),
  });
  const list = covers.data?.covers ?? [];
  const refresh = () => {
    if (latest) void qc.invalidateQueries({ queryKey: qk.clipCovers(latest.id) });
    void qc.invalidateQueries({ queryKey: clipGroupKeys.view(view.id) });
  };
  const editor = useMutation({
    mutationFn: async () => (view.cover_doc ? view.cover_doc.id : (await coverDocsApi.create({ clip_id: latest!.id, template: "avatar-bottom" })).id),
    onSuccess: (id) => navigate(`/covers/${id}`),
    onError: (e) => toast.error(errorText(e, "Не удалось открыть редактор превью")),
  });
  const generate = useMutation({
    mutationFn: () => clipsApi.generateCovers(latest!.id, { count: 3 }),
    onSuccess: (r) => {
      toast.success(`Готово: ${plural(r.new.length, "вариант", "варианта", "вариантов")}`);
      refresh();
    },
    onError: (e) => toast.error(errorText(e, "Не удалось сделать варианты")),
  });
  const choose = useMutation({
    mutationFn: (coverId: number) => clipsApi.chooseCover(latest!.id, coverId, true),
    onSuccess: (r) => {
      toast.success(r.note || "Превью выбрано");
      refresh();
    },
    onError: (e) => toast.error(errorText(e, "Не удалось выбрать")),
  });
  const image = view.cover_url || (latest ? `/media/clips/${latest.id}/thumb?v=${encodeURIComponent(latest.updated_at)}` : "");
  const wide = view.format === "16:9";
  return (
    <section className="ccard ccov">
      <h2>Превью</h2>
      <div className={`ccov-row${wide ? " wide" : ""}`}>
        <div className="ccov-img" data-empty={!view.cover_url || undefined}>
          {image ? <img src={image} alt="Превью клипа" loading="lazy" /> : <span className="muted">нет</span>}
        </div>
        <div className="ccov-b">
          <div className="ccov-t" title={view.cover_doc?.title}>
            {view.cover_doc?.title ? `«${view.cover_doc.title}»` : view.cover_url ? "Своё превью клипа" : "Своего превью нет"}
          </div>
          <button type="button" className="btn sm" disabled={(!latest && !view.cover_doc) || editor.isPending} title={latest || view.cover_doc ? "" : "Сначала нужен рендер"} onClick={() => editor.mutate()}>
            {editor.isPending ? "Открываю…" : "Редактор превью"}
          </button>
          <button type="button" className="btn sm" disabled={!latest || generate.isPending} title={latest ? "Сильные кадры клипа и крупный текст 2–5 слов" : "Сначала нужен рендер"} onClick={() => generate.mutate()}>
            {generate.isPending ? "Делаю…" : list.length ? "Ещё 3 варианта" : "Сделать 3 варианта"}
          </button>
        </div>
      </div>
      {list.length ? (
        <div className="ccov-vars">
          {list.slice(0, 6).map((c) => (
            <button key={c.id} type="button" className={c.chosen ? "on" : ""} title={c.text} disabled={choose.isPending} onClick={() => choose.mutate(c.id)}>
              <img src={c.url} alt={c.text} loading="lazy" />
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function PublishCard({ view, render }: { view: ClipView; render: { id: number; label: string } | null }) {
  const toast = useToast();
  const qc = useQueryClient();
  const { on } = useModules();
  const publishingOn = on("publishing");
  const accounts = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list, enabled: publishingOn });
  const [targets, setTargets] = useState<number[]>([]);
  const [privacy, setPrivacy] = useState("public");
  const [when, setWhen] = useState<"now" | "later">("now");
  const [scheduledAt, setScheduledAt] = useState("");
  const [jobId, setJobId] = useState<number | null>(null);
  const [dialog, setDialog] = useState(false);
  const description = [view.meta.description, view.meta.hashtags.join(" ")].filter(Boolean).join("\n\n");
  const publish = useMutation({
    mutationFn: () =>
      clipsApi.publish(render!.id, {
        title: view.meta.title.trim(),
        description,
        targets,
        privacy,
        scheduled_at: when === "later" && scheduledAt ? scheduledAt.replace("T", " ") : undefined,
      }),
    onSuccess: (job) => {
      toast.success("Поставлено в очередь публикации");
      setJobId(job.id);
      setTargets([]);
      void qc.invalidateQueries({ queryKey: qk.jobs });
      void qc.invalidateQueries({ queryKey: qk.activeTasks });
      void qc.invalidateQueries({ queryKey: clipGroupKeys.view(view.id) });
    },
    onError: (e) => toast.error(errorText(e, "Не удалось опубликовать")),
  });
  const accountList = accounts.data ?? [];
  return (
    <section className="ccard cpub">
      <h2>Публикация</h2>
      {publishingOn ? (
        <>
          {accountList.length ? (
            <div className="cpub-acc">
              {accountList.map((a) => (
                <label key={a.id} className="check">
                  <input
                    type="checkbox"
                    checked={targets.includes(a.id)}
                    onChange={() => setTargets((prev) => (prev.includes(a.id) ? prev.filter((x) => x !== a.id) : [...prev, a.id]))}
                  />
                  <span>{a.platform} · {a.label}</span>
                </label>
              ))}
            </div>
          ) : (
            <p className="muted cpub-none">{accounts.isLoading ? "…" : <>Аккаунтов нет. <Link to="/pubs?tab=accounts">Добавить</Link></>}</p>
          )}
          <div className="cpub-row">
            <label className="field">
              <span>Приватность</span>
              <select className="input" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                <option value="public">Открыто</option>
                <option value="unlisted">По ссылке</option>
                <option value="private">Скрыто</option>
              </select>
            </label>
            <label className="field">
              <span>Когда</span>
              <select className="input" value={when} onChange={(e) => setWhen(e.target.value as "now" | "later")}>
                <option value="now">Сейчас</option>
                <option value="later">Выбрать время</option>
              </select>
            </label>
          </div>
          {when === "later" ? (
            <input className="input" type="datetime-local" aria-label="Время публикации" value={scheduledAt} onChange={(e) => setScheduledAt(e.target.value)} />
          ) : null}
        </>
      ) : null}
      <div className="cpub-btns">
        {render ? (
          <a className="btn big" href={`/media/clips/${render.id}`} download={`${view.name} ${render.label}.mp4`} title={`Файл версии ${render.label}`}>Скачать</a>
        ) : (
          <button type="button" className="btn big" disabled title="Сначала нужен рендер">Скачать</button>
        )}
        {publishingOn ? (
          <button
            type="button"
            className="btn big"
            disabled={!render || !targets.length || publish.isPending || (when === "later" && !scheduledAt)}
            title={!render ? "Сначала нужен рендер" : targets.length ? `Опубликовать версию ${render.label}` : "Выберите аккаунт"}
            onClick={() => publish.mutate()}
          >
            {publish.isPending ? "…" : "Опубликовать"}
          </button>
        ) : null}
      </div>
      <div className="cpub-more">
        <a href={playbackApi.packageUrl(view.id)} title="Zip для Premiere и DaVinci Resolve: монтаж, субтитры, файлы, без рендера">Экспорт для монтажки</a>
        {publishingOn && render && view.latest ? <button type="button" className="linkish" onClick={() => setDialog(true)}>Все настройки публикации</button> : null}
        {jobId ? <Link to={`/pubs/${jobId}`}>Публикация №{jobId} в очереди</Link> : view.published ? <Link to="/pubs?tab=pubs">Опубликован: {view.published}</Link> : null}
      </div>
      {dialog && view.latest ? <PublishDialog clip={view.latest} onClose={() => setDialog(false)} /> : null}
    </section>
  );
}

function OlderChat({ view }: { view: ClipView }) {
  if (!view.older_chat.length) return null;
  const labels = new Map(view.plans.map((p) => [p.id, p.label]));
  return (
    <details className="cchat-old">
      <summary>Переписка прошлых версий: {view.older_chat.length}</summary>
      <div className="cchat-old-list">
        {view.older_chat.map((m) => (
          <div key={m.id} className={`cchat-old-m ${m.role}`}>
            <span className="muted">{labels.get(m.plan_id)}</span> {m.text}
          </div>
        ))}
      </div>
    </details>
  );
}

/** Экран клипа: видео и сегменты, версии и чат, описание, превью, публикация. */
export function ClipPage() {
  const { clipId = "" } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const { on } = useModules();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [watching, setWatching] = useState(false);
  const [rendering, setRendering] = useState(false);
  const query = useQuery({
    queryKey: clipGroupKeys.view(clipId),
    queryFn: () => clipGroupsApi.view(clipId),
    enabled: /^\d+$/.test(clipId),
    retry: false,
    refetchOnMount: "always",
    refetchInterval: (q) => (q.state.data?.status === "rendering" || rendering ? 3000 : false),
  });
  useEffect(() => setSelectedKey(null), [clipId]);
  const view = query.data;
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: clipGroupKeys.anyView });
    void qc.invalidateQueries({ queryKey: clipGroupKeys.all });
    void qc.invalidateQueries({ queryKey: navKeys.all });
    if (view) void qc.invalidateQueries({ queryKey: qk.source(view.source.id) });
  };
  const render = useMutation({
    mutationFn: () => clipPlansApi.render(view!.id),
    onMutate: () => setRendering(true),
    onSuccess: () => toast.success("Рендер готов"),
    onError: (e) => toast.error(errorText(e, "Рендер не удался")),
    onSettled: () => {
      setRendering(false);
      refresh();
    },
  });
  // Выбранная версия, иначе последняя готовая: её показывает плеер, её скачивают и публикуют.
  const ready = useMemo(() => (view?.versions ?? []).filter((v) => v.status === "succeeded"), [view]);
  const selected = ready.find((v) => v.key === selectedKey) ?? ready[0] ?? null;

  if (!/^\d+$/.test(clipId)) return <ErrorState error={new Error("Такого клипа нет")} />;
  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!view) return null;

  const fileGone = !view.canvas && !view.source.has_file;
  const canRender = !fileGone && view.status !== "rendering" && !rendering;
  const start = view.start_sec && !view.canvas ? `, ${formatDuration(view.start_sec)}` : "";
  const parent = view.canvas ? view.project?.name ?? "холст" : view.source.name;
  const wide = view.format === "16:9";
  const stageRatio = selected?.width && selected.height ? `${selected.width} / ${selected.height}` : wide ? "16 / 9" : view.format === "9:16" ? "9 / 16" : "1 / 1";

  return (
    <div className={`cpage${wide ? " wide" : " tall"}`}>
      <div className="cpage-h">
        <div className="cpage-title">
          <h1 title={view.title}>{view.name}</h1>
          <div className="cpage-sub">
            <span>
              из {view.canvas ? "проекта" : "видео"} «
              {view.canvas ? (
                view.project ? <Link to={projectPath(view.project.id)}>{parent}</Link> : parent
              ) : (
                <Link to={videoPath(view.source.id)}>{parent}</Link>
              )}
              »{start}
            </span>
            <span className="cchip fmt">{view.format}</span>
            {view.moments.map((m) => (
              <Link key={m.id} className="cpage-moment" to={clipPath(m.id)}>этот момент в {m.format}</Link>
            ))}
          </div>
        </div>
        <div className="cpage-acts">
          {view.prev_id ? (
            <Link className="btn" to={clipPath(view.prev_id)}><Icon name="arrow" className="flip" size={16} /> Предыдущий клип</Link>
          ) : (
            <span className="btn" aria-disabled="true"><Icon name="arrow" className="flip" size={16} /> Предыдущий клип</span>
          )}
          {view.next_id ? (
            <Link className="btn" to={clipPath(view.next_id)}><Icon name="arrow" size={16} /> Следующий клип</Link>
          ) : (
            <span className="btn" aria-disabled="true"><Icon name="arrow" size={16} /> Следующий клип</span>
          )}
          <Link className="btn primary" to={clipEditorPath(view.source.id, view.id)}><Icon name="scissors" size={16} /> Открыть монтаж</Link>
        </div>
      </div>
      {fileGone ? <MissingSourceNotice source={{ id: view.source.id, original_url: view.source.original_url }} /> : null}
      <div className="cpage-grid">
        <div className="cpage-col">
          <div className="cstage" style={{ aspectRatio: wide ? "16 / 9" : undefined }}>
            {selected?.url ? (
              <video key={selected.key} src={selected.url} poster={selected.thumb ?? undefined} controls preload="metadata" playsInline style={wide ? undefined : { aspectRatio: stageRatio, width: "auto" }} />
            ) : (
              <div className="cstage-empty">
                <p>{view.status === "rendering" ? "Рендер идёт…" : "Этот клип ещё не рендерили."}</p>
                <button type="button" className="btn" disabled={!canRender} title={canRender ? "" : "Для рендера нужна запись стрима"} onClick={() => render.mutate()}>
                  Рендерить
                </button>
              </div>
            )}
          </div>
          <div className="cstage-bar">
            <span className="cstage-info">
              {selected ? (
                <>
                  {selected.qc_ok === true ? <span className="cstage-ok" aria-hidden>✓</span> : null}
                  {selected.label}: {selected.width}×{selected.height}, {formatDuration(selected.duration_sec)}
                  {selected.qc_ok === true ? ", проверка качества пройдена" : selected.qc_ok === false ? ", проверка качества нашла проблемы" : ""}
                </>
              ) : (
                `${view.format}, ${formatDuration(view.duration_sec)}`
              )}
            </span>
            <button type="button" className="btn sm" title="Весь монтаж без рендера: склейки, слои, субтитры, музыка" onClick={() => setWatching(true)}>
              ▶ Смотреть монтаж
            </button>
            {view.stale && selected ? (
              <button type="button" className="btn sm" disabled={!canRender} title={canRender ? "Монтаж новее этого файла" : "Для рендера нужна запись стрима"} onClick={() => render.mutate()}>
                {rendering || view.status === "rendering" ? "Рендер идёт…" : "Рендерить"}
              </button>
            ) : null}
          </div>
          <SegmentsCard view={view} onChanged={refresh} />
        </div>
        <div className="cpage-col">
          <VersionsCard
            view={view}
            selected={selected}
            onSelect={setSelectedKey}
            onChanged={refresh}
            canRender={canRender}
            onRender={() => render.mutate()}
          />
          {on("editor_agent") ? (
            <section className="ccard cchat">
              <h2>Чат с агентом о клипе</h2>
              <OlderChat view={view} />
              <AgentPanel clipPlanId={view.id} onChanged={refresh} compact />
            </section>
          ) : null}
        </div>
        <div className="cpage-col">
          <MetaCard view={view} onChanged={refresh} />
          {on("covers") ? <CoverCard view={view} /> : null}
          <PublishCard view={view} render={selected?.render_id ? { id: selected.render_id, label: selected.label } : null} />
        </div>
      </div>
      {watching ? (
        <Suspense fallback={null}>
          <WatchMontage clipPlanId={view.id} title={view.name} onClose={() => setWatching(false)} />
        </Suspense>
      ) : null}
    </div>
  );
}
