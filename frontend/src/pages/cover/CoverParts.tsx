import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { coverDocsApi, type CoverLink, type CoverTemplate } from "@/api/coverDocs";
import { useToast } from "@/components/Toast";
import { paintCoverDoc, type CoverImageLike } from "@/motion/coverdoc";

/* Миниатюра шаблона и окно ссылки на редактирование — вынесены из CoverEditor.tsx, чтобы он не рос. */

export function TemplateThumb({ template, width, height, images, version }: { template: CoverTemplate; width: number; height: number; images: Map<string, HTMLImageElement>; version: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    paintCoverDoc(ctx, { version: 1, width: width, height: height, bg: "#101018", layers: template.layers }, {
      getImage: (src) => (images.get(src) as CoverImageLike | undefined) ?? null,
      placeholders: true,
      scale: canvas.width / width,
    });
  }, [template, width, height, images, version]);
  const w = 132;
  return <canvas ref={ref} width={w} height={Math.round((w * height) / width)} aria-hidden="true" />;
}

export function ShareDialog({ docId, onClose }: { docId: number; onClose: () => void }) {
  const toast = useToast();
  const [links, setLinks] = useState<CoverLink[]>([]);
  const [perm, setPerm] = useState<"edit" | "view">("edit");
  const [days, setDays] = useState(30);
  const [made, setMade] = useState<{ url: string; warning?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = useCallback(() => {
    coverDocsApi.links(docId).then((r) => setLinks(r.links)).catch(() => undefined);
  }, [docId]);
  useEffect(reload, [reload]);
  const absolute = (url: string) => (url.startsWith("/") ? `${window.location.origin}${url}` : url);
  const create = async () => {
    setBusy(true);
    try {
      const out = await coverDocsApi.createLink(docId, perm, days);
      setMade({ url: absolute(out.url), warning: out.warning });
      reload();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Не удалось создать ссылку");
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    if (!made) return;
    try {
      await navigator.clipboard.writeText(made.url);
      toast.success("Ссылка скопирована");
    } catch {
      toast.error("Браузер не дал скопировать: выделите ссылку и скопируйте вручную");
    }
  };
  const revoke = async (id: number) => {
    try {
      await coverDocsApi.revokeLink(docId, id);
      reload();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Не удалось отозвать ссылку");
    }
  };
  const active = links.filter((l) => !l.revoked_at && new Date(l.expires_at) > new Date());
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal cov-share" role="dialog" aria-modal="true" aria-label="Ссылка на редактирование" onClick={(e) => e.stopPropagation()}>
        <h3>Ссылка на редактирование</h3>
        <p className="muted">По ссылке открывается только эта обложка, без входа в сервис и без доступа к другим клипам.</p>
        <div className="cov-row">
          <label className="cov-num">
            <span>Что можно</span>
            <select className="input" value={perm} onChange={(e) => setPerm(e.target.value as "edit" | "view")}>
              <option value="edit">Править</option>
              <option value="view">Только смотреть</option>
            </select>
          </label>
          <label className="cov-num">
            <span>Срок, дней</span>
            <input className="input" type="number" min={1} max={365} value={days} onChange={(e) => setDays(Number(e.target.value) || 30)} />
          </label>
        </div>
        <button type="button" className="btn primary" disabled={busy} onClick={create}>Создать ссылку</button>
        {made ? (
          <div className="cov-link">
            <input className="input" readOnly value={made.url} onFocus={(e) => e.target.select()} aria-label="Новая ссылка" />
            <button type="button" className="btn sm" onClick={copy}>Скопировать</button>
            <p className="muted">Ссылка показывается один раз: сервис хранит только её отпечаток.</p>
            {made.warning ? <p className="cov-warn">{made.warning}</p> : null}
          </div>
        ) : null}
        {active.length ? (
          <div className="cov-links">
            <strong>Действующие ссылки</strong>
            {active.map((l) => (
              <div key={l.id} className="cov-link-row">
                <span>{l.perm === "edit" ? "Править" : "Смотреть"}, до {new Date(l.expires_at).toLocaleDateString("ru-RU")}</span>
                <button type="button" className="btn sm danger" onClick={() => revoke(l.id)}>Отозвать</button>
              </div>
            ))}
          </div>
        ) : null}
        <button type="button" className="btn ghost" onClick={onClose}>Закрыть</button>
      </div>
    </div>
  );
}
