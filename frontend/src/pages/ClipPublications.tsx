import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { accountsApi } from "@/api/accounts";
import type { ClipView } from "@/api/clipGroups";
import {
  PUB_PLATFORMS,
  clipMarksApi,
  pubPlatformLabel,
  shortDate,
  todayIso,
  type ClipPublication,
  type ManualPubInput,
} from "@/api/clipMarks";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { formatCount } from "@/api/stats";
import { NoteEditor, useMarksRefresh } from "@/components/ClipMarks";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { useModuleOn } from "@/hooks/useModules";

/** Экран клипа: заметка владельца и «Где выложен» — сервисом и руками (app/clip_marks.py). */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function ClipNoteCard({ view }: { view: ClipView }) {
  return (
    <section className="ccard cnote">
      <div className="ccard-h">
        <h2>Заметка</h2>
        {view.note.updated_at ? <span className="muted">{shortDate(view.note.updated_at)}</span> : null}
      </div>
      <NoteEditor planId={view.id} text={view.note.text} rows={3} />
    </section>
  );
}

type Draft = Omit<ManualPubInput, "clip_id"> & { clip_id: string };

function emptyDraft(view: ClipView): Draft {
  const last = view.publications.find((p) => p.kind === "manual");
  return {
    platform: last?.platform ?? "tiktok",
    account: last?.account ?? "",
    published_at: todayIso(),
    url: "",
    notes: "",
    clip_id: view.latest ? String(view.latest.id) : "",
  };
}

function draftOf(p: ClipPublication): Draft {
  return {
    platform: p.platform || "other",
    account: p.account,
    published_at: (p.published_at || p.date).slice(0, 10),
    url: p.url,
    notes: p.note,
    clip_id: p.clip_id ? String(p.clip_id) : "",
  };
}

function PubForm({
  view,
  initial,
  busy,
  onSave,
  onCancel,
}: {
  view: ClipView;
  initial: Draft;
  busy: boolean;
  onSave: (d: Draft) => void;
  onCancel: () => void;
}) {
  const [d, setD] = useState(initial);
  const publishingOn = useModuleOn("publishing");
  const accounts = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list, enabled: publishingOn });
  // Подсказки аккаунта: подключённые к сервису и уже вписанные у этого клипа, сначала — этой площадки.
  const suggestions = useMemo(() => {
    const all = [
      ...(accounts.data ?? []).map((a) => ({ platform: a.platform, label: a.label })),
      ...view.publications.filter((p) => p.account).map((p) => ({ platform: p.platform, label: p.account })),
    ];
    const mine = all.filter((a) => a.platform === d.platform).map((a) => a.label);
    return [...new Set([...mine, ...all.map((a) => a.label)])];
  }, [accounts.data, view.publications, d.platform]);
  const files = view.versions.filter((v) => v.render_id && v.status === "succeeded");
  const listId = `pub-acc-${view.id}`;
  return (
    <form
      className="cpubs-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(d);
      }}
    >
      <div className="cpubs-grid">
        <label className="field">
          <span>Площадка</span>
          <select className="input" value={d.platform} onChange={(e) => setD({ ...d, platform: e.target.value })}>
            {PUB_PLATFORMS.map((p) => (
              <option key={p.key} value={p.key}>{p.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Когда</span>
          <input className="input" type="date" required value={d.published_at} onChange={(e) => setD({ ...d, published_at: e.target.value })} />
        </label>
      </div>
      <label className="field">
        <span>Аккаунт</span>
        <input className="input" list={listId} value={d.account} placeholder="@ник или название канала" onChange={(e) => setD({ ...d, account: e.target.value })} />
        <datalist id={listId}>
          {suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      </label>
      <label className="field">
        <span>Ссылка <span className="muted">необязательно: по ссылке YouTube и TikTok сервис сам соберёт просмотры</span></span>
        <input className="input" type="url" inputMode="url" value={d.url} placeholder="https://…" onChange={(e) => setD({ ...d, url: e.target.value })} />
      </label>
      {files.length > 1 ? (
        <label className="field">
          <span>Какой файл</span>
          <select className="input" value={d.clip_id} onChange={(e) => setD({ ...d, clip_id: e.target.value })}>
            <option value="">не указывать</option>
            {files.map((v, i) => (
              <option key={v.key} value={String(v.render_id)}>
                {v.label}{i === 0 ? " (последний)" : ""}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <label className="field">
        <span>Пометка</span>
        <input className="input" maxLength={300} value={d.notes} placeholder="Например: без хэштегов, в 19:00" onChange={(e) => setD({ ...d, notes: e.target.value })} />
      </label>
      <div className="cpubs-btns">
        <button type="submit" className="btn primary sm" disabled={busy || !d.published_at}>{busy ? "Сохраняю…" : "Сохранить"}</button>
        <button type="button" className="btn ghost sm" onClick={onCancel}>Отмена</button>
      </div>
    </form>
  );
}

export function ClipPublicationsCard({ view }: { view: ClipView }) {
  const toast = useToast();
  const refresh = useMarksRefresh();
  const location = useLocation();
  const ref = useRef<HTMLElement>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [removing, setRemoving] = useState<ClipPublication | null>(null);
  // «Отметить публикацию» из меню плитки ведёт сюда (/clips/:id#pubs): сразу открыть форму.
  useEffect(() => {
    if (location.hash !== "#pubs") return;
    setAdding(true);
    ref.current?.scrollIntoView({ block: "center" });
  }, [location.hash, view.id]);
  const body = (d: Draft): ManualPubInput => ({ ...d, url: d.url.trim(), account: d.account.trim(), clip_id: d.clip_id ? Number(d.clip_id) : null });
  const add = useMutation({
    mutationFn: (d: Draft) => clipMarksApi.addPublication(view.id, body(d)),
    onSuccess: () => {
      toast.success("Публикация отмечена");
      setAdding(false);
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не удалось сохранить")),
  });
  const save = useMutation({
    mutationFn: ({ id, d }: { id: number; d: Draft }) => clipMarksApi.updatePublication(id, body(d)),
    onSuccess: () => {
      setEditing(null);
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не удалось сохранить")),
  });
  const remove = useMutation({
    mutationFn: (id: number) => clipMarksApi.removePublication(id),
    onSuccess: () => {
      setRemoving(null);
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не удалось убрать")),
  });
  const pubs = view.publications;
  return (
    <section className="ccard cpubs" id="pubs" ref={ref}>
      <div className="ccard-h">
        <h2>Где выложен {pubs.length ? <span className="ccard-n">{pubs.length}</span> : null}</h2>
        {adding ? null : (
          <button type="button" className="btn sm" onClick={() => { setAdding(true); setEditing(null); }}>+ Отметить</button>
        )}
      </div>
      {!pubs.length && !adding ? <p className="muted cpub-none">Пока нигде. Выложили сами — отметьте: площадка, аккаунт, дата.</p> : null}
      {adding ? <PubForm view={view} initial={emptyDraft(view)} busy={add.isPending} onSave={(d) => add.mutate(d)} onCancel={() => setAdding(false)} /> : null}
      {pubs.length ? (
        <ul className="cpubs-list">
          {pubs.map((p) =>
            p.kind === "manual" && editing === p.id ? (
              <li key={`${p.kind}-${p.id}`}>
                <PubForm view={view} initial={draftOf(p)} busy={save.isPending} onSave={(d) => save.mutate({ id: p.id, d })} onCancel={() => setEditing(null)} />
              </li>
            ) : (
              <li key={`${p.kind}-${p.id}`} className="cpubs-row">
                <div className="cpubs-main">
                  <span className="cchip fmt">{pubPlatformLabel(p.platform)}</span>
                  <span className="cpubs-acc" title={p.account}>{p.account || <span className="muted">аккаунт не указан</span>}</span>
                  <span className="muted mono">{shortDate(p.date)}</span>
                </div>
                <div className="cpubs-sub muted">
                  {p.kind === "service" ? <span>через сервис</span> : null}
                  {p.url ? <a href={p.url} target="_blank" rel="noreferrer">ссылка ↗</a> : p.kind === "manual" ? <span>без ссылки</span> : null}
                  {p.views != null ? <span>{formatCount(p.views)} просм.</span> : null}
                  {p.note ? <span className="cpubs-note">{p.note}</span> : null}
                </div>
                {p.kind === "manual" ? (
                  <div className="cpubs-acts">
                    <button type="button" className="btn ghost sm" title="Поправить" aria-label="Поправить" onClick={() => { setEditing(p.id); setAdding(false); }}>✎</button>
                    <button type="button" className="btn ghost sm" title="Убрать" aria-label="Убрать" onClick={() => setRemoving(p)}>✕</button>
                  </div>
                ) : null}
              </li>
            ),
          )}
        </ul>
      ) : null}
      <ConfirmDialog
        open={removing != null}
        title="Убрать отметку о публикации?"
        body={removing ? `${pubPlatformLabel(removing.platform)}${removing.account ? ` · ${removing.account}` : ""} · ${shortDate(removing.date)}. Само видео на площадке это не тронет.` : null}
        confirmLabel="Убрать"
        busy={remove.isPending}
        onCancel={() => setRemoving(null)}
        onConfirm={() => removing && remove.mutate(removing.id)}
      />
    </section>
  );
}
