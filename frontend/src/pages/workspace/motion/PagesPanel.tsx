/**
 * Вкладка «Страницы» редактора: снимки сайтов (app/webpages.py). Снять страницу по ссылке, поправить её
 * словами (правка идёт через HTML — новая версия, старые остаются), сравнить «было/стало» и поставить на
 * таймлайн слоем «страница» (листается, можно в телефоне) — или разобрать на отдельные слои (PageBlocksPicker).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { webPagesApi, type WebPage, type WebPageEdit } from "@/api/webPages";
import { useToast } from "@/components/Toast";
import type { AddLayers } from "@/pages/workspace/motion/LibraryPanels";
import { PageBlocksPicker } from "@/pages/workspace/motion/PageBlocksPicker";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function PageCard({ page, clipId, onAdd }: { page: WebPage; clipId?: number | null; onAdd: AddLayers }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [wish, setWish] = useState("");
  const [shown, setShown] = useState<number>(page.current_version);
  const [phone, setPhone] = useState(page.viewport === "mobile");
  const [last, setLast] = useState<WebPageEdit | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["web-pages", clipId ?? null] });
  const edit = useMutation({
    mutationFn: () => webPagesApi.edit(page.id, wish.trim(), shown || undefined),
    onSuccess: (r) => {
      setLast(r);
      setShown(r.version.n);
      setWish("");
      refresh();
      if (r.problems.length) toast.push(`Сделано не всё: ${r.problems.join("; ")}`, "info");
      else toast.success(`Готово — версия ${r.version.n}`);
    },
    onError: (e) => toast.error(errText(e, "Не получилось поправить")),
  });
  const remove = useMutation({
    mutationFn: () => webPagesApi.remove(page.id),
    onSuccess: refresh,
    onError: (e) => toast.error(errText(e, "Не удалилось")),
  });
  const add = async () => {
    try {
      const layer = await webPagesApi.layer(page.id, shown, phone);
      onAdd([layer]);
      if (shown !== page.current_version) await webPagesApi.setCurrent(page.id, shown);
    } catch (e) {
      toast.error(errText(e, "Не получилось добавить"));
    }
  };
  const version = page.versions.find((v) => v.n === shown) ?? page.current;
  const blocked = page.status === "blocked";
  return (
    <div className={`wp-card${open ? " open" : ""}`}>
      <button type="button" className="wp-head" onClick={() => setOpen((v) => !v)} title={page.final_url || page.url}>
        {page.thumb_url ? <img className="wp-thumb" src={page.thumb_url} alt="" loading="lazy" /> : <span className="wp-thumb empty">🌐</span>}
        <span className="wp-meta">
          <strong>{page.title || page.host}</strong>
          <span className="muted">
            {page.host} · {page.viewport === "mobile" ? "телефон" : "компьютер"}
            {page.versions.length > 1 ? ` · версий ${page.versions.length}` : ""}
          </span>
          <span className="wp-badges">
            {blocked ? <span className="wp-badge bad" title={page.status_note}>заблокировано</span> : null}
            {page.foreign_edit ? <span className="wp-badge warn" title="Текст или цена на чужом сайте изменены — проверьте, что так можно">изменён текст на чужой странице</span> : null}
            {page.own_site ? <span className="wp-badge ok">свой сайт</span> : null}
          </span>
        </span>
      </button>
      {open && blocked ? <p className="muted wp-note">Сайт показал капчу или «вы не робот» — снимка нет. {page.status_note}</p> : null}
      {open && !blocked ? (
        <div className="wp-body">
          <div className="wp-versions" aria-label="Версии">
            {page.versions.map((v) => (
              <button
                key={v.n}
                type="button"
                className={`wp-ver${v.n === shown ? " active" : ""}${v.foreign_edit ? " warn" : ""}`}
                title={v.instruction || (v.n === 1 ? "Снимок как есть" : "Правка списком")}
                onClick={() => setShown(v.n)}
              >
                {v.thumb_url ? <img src={v.thumb_url} alt="" loading="lazy" /> : null}
                <span>v{v.n}</span>
              </button>
            ))}
          </div>
          {version?.instruction ? <p className="wp-note">✎ {version.instruction}</p> : null}
          {version?.note ? <p className="wp-note warn">⚠ {version.note}</p> : null}
          {last && last.version.n === shown ? (
            <div className="wp-compare">
              <figure>
                <img src={last.before.thumb_url ?? ""} alt="" />
                <figcaption>было (v{last.before.n})</figcaption>
              </figure>
              <figure>
                <img src={last.version.thumb_url ?? ""} alt="" />
                <figcaption>стало (v{last.version.n})</figcaption>
              </figure>
            </div>
          ) : null}
          <textarea
            className="input lib-prompt"
            rows={2}
            placeholder="Что поправить: «подсвети кнопку Купить», «убери баннер cookie», «обрежь до блока с ценой»"
            value={wish}
            onChange={(e) => setWish(e.target.value)}
          />
          <button type="button" className="btn" disabled={edit.isPending || wish.trim().length < 3} onClick={() => edit.mutate()}>
            {edit.isPending ? "Правлю…" : `Поправить v${shown}`}
          </button>
          <label className="switch">
            <input type="checkbox" checked={phone} onChange={(e) => setPhone(e.target.checked)} />
            <span className="switch-track" />
            <span>в телефоне</span>
          </label>
          <div className="lib-row">
            <button type="button" className="btn primary" onClick={() => void add()}>
              Добавить на таймлайн
            </button>
            {version?.html_url ? (
              <a className="btn ghost" href={version.html_url} target="_blank" rel="noreferrer" title="Копия страницы (HTML без скриптов)">
                HTML
              </a>
            ) : null}
            <button
              type="button"
              className="btn ghost"
              disabled={remove.isPending}
              onClick={() => window.confirm("Удалить снимок со всеми версиями?") && remove.mutate()}
            >
              🗑
            </button>
          </div>
          <PageBlocksPicker key={shown} pageId={page.id} version={shown} clipId={clipId} onAdd={onAdd} />
        </div>
      ) : null}
    </div>
  );
}

export function PagesPanel({ onAdd, clipId }: { onAdd: AddLayers; clipId?: number | null }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [viewport, setViewport] = useState<"mobile" | "desktop">("mobile");
  const list = useQuery({ queryKey: ["web-pages", clipId ?? null], queryFn: () => webPagesApi.list(clipId) });
  const snap = useMutation({
    mutationFn: () => webPagesApi.snapshot(url.trim(), clipId, viewport),
    onSuccess: (page) => {
      qc.invalidateQueries({ queryKey: ["web-pages", clipId ?? null] });
      setUrl("");
      if (page.status === "blocked") toast.push("Сайт показал капчу — снимок не получился", "info");
      else toast.success("Страница снята");
    },
    onError: (e) => toast.error(errText(e, "Не получилось снять страницу")),
  });
  const pages = list.data?.pages ?? [];
  return (
    <div className="lib-panel">
      <div className="lib-sub">🌐 Снять страницу сайта</div>
      <div className="lib-row">
        <input
          className="input"
          placeholder="Ссылка: vtubika.store/…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && url.trim() && !snap.isPending && snap.mutate()}
        />
        <select className="input wp-viewport" value={viewport} onChange={(e) => setViewport(e.target.value as "mobile" | "desktop")}>
          <option value="mobile">телефон</option>
          <option value="desktop">компьютер</option>
        </select>
      </div>
      <button type="button" className="btn primary" disabled={snap.isPending || url.trim().length < 4} onClick={() => snap.mutate()}>
        {snap.isPending ? "Снимаю… (до минуты)" : "Снять страницу"}
      </button>
      <p className="muted wp-note">Только просмотр: без входа, кликов и форм. Править можно словами — правка идёт в копии страницы.</p>
      <div className="lib-sub">Снимки {pages.length ? `· ${pages.length}` : ""}</div>
      {list.isLoading ? <p className="muted">Загружаю…</p> : null}
      {!list.isLoading && !pages.length ? <p className="muted wp-note">Пока пусто. Агент снимает страницы сам, когда делает рекламу.</p> : null}
      <div className="wp-list">
        {pages.map((p) => (
          <PageCard key={p.id} page={p} clipId={clipId} onAdd={onAdd} />
        ))}
      </div>
    </div>
  );
}
