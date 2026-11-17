import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { CoverRect } from "@/api/coverDocs";
import { imagePlacement, type CoverImageLayer, type CoverImageLike } from "@/motion/coverdoc";

/*
 * «Убрать интерфейс игры» на кадре: прямоугольники в долях КАРТИНКИ (не холста) — сервис закрашивает их
 * (cv2.inpaint, app/cover_photo.clean_frame) и кладёт новый файл в слой. Найденные автоматически и нарисованные
 * пальцем выглядят одинаково: лишний — крестик, недостающий — провести по кадру.
 */

export type HudState = { layerId: string; rects: CoverRect[]; busy: "find" | "clean" | null; note: string };

type Box = { x: number; y: number; w: number; h: number };

/** Прямоугольник картинки (доли) → рамка в px холста документа (слой без поворота). */
export function rectToDoc(layer: CoverImageLayer, img: CoverImageLike, r: CoverRect): Box {
  const p = imagePlacement(layer, img);
  const toX = (fx: number) => {
    const local = p.dx + ((fx * img.width - p.sx) / p.sw) * p.dw;
    return layer.x + (layer.flipX ? layer.w - local : local);
  };
  const toY = (fy: number) => layer.y + p.dy + ((fy * img.height - p.sy) / p.sh) * p.dh;
  const xa = toX(r.x);
  const xb = toX(r.x + r.w);
  return { x: Math.min(xa, xb), y: toY(r.y), w: Math.abs(xb - xa), h: toY(r.y + r.h) - toY(r.y) };
}

/** Точка холста → доли картинки (зажато в 0…1). */
export function docToImage(layer: CoverImageLayer, img: CoverImageLike, x: number, y: number): { x: number; y: number } {
  const p = imagePlacement(layer, img);
  let local = x - layer.x;
  if (layer.flipX) local = layer.w - local;
  const fx = (p.sx + ((local - p.dx) / p.dw) * p.sw) / img.width;
  const fy = (p.sy + ((y - layer.y - p.dy) / p.dh) * p.sh) / img.height;
  return { x: Math.min(1, Math.max(0, fx)), y: Math.min(1, Math.max(0, fy)) };
}

export function HudOverlay({ layer, img, rects, zoom, onChange }: {
  layer: CoverImageLayer;
  img: CoverImageLike;
  rects: CoverRect[];
  zoom: number;
  onChange: (rects: CoverRect[]) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<{ a: { x: number; y: number }; b: { x: number; y: number } } | null>(null);
  const point = (e: ReactPointerEvent) => {
    const rect = ref.current!.getBoundingClientRect();
    return docToImage(layer, img, (e.clientX - rect.left) / zoom, (e.clientY - rect.top) / zoom);
  };
  const finish = () => {
    if (draft) {
      const r = { x: Math.min(draft.a.x, draft.b.x), y: Math.min(draft.a.y, draft.b.y), w: Math.abs(draft.a.x - draft.b.x), h: Math.abs(draft.a.y - draft.b.y) };
      if (r.w * img.width > 6 && r.h * img.height > 6) onChange([...rects, r]);
    }
    setDraft(null);
  };
  const shown = draft ? [{ x: Math.min(draft.a.x, draft.b.x), y: Math.min(draft.a.y, draft.b.y), w: Math.abs(draft.a.x - draft.b.x), h: Math.abs(draft.a.y - draft.b.y) }] : [];
  const box = (r: CoverRect) => {
    const b = rectToDoc(layer, img, r);
    return { left: b.x * zoom, top: b.y * zoom, width: Math.max(2, b.w * zoom), height: Math.max(2, b.h * zoom) };
  };
  return (
    <div
      ref={ref}
      className="cov-hud"
      onPointerDown={(e) => {
        e.stopPropagation();
        ref.current?.setPointerCapture(e.pointerId);
        const p = point(e);
        setDraft({ a: p, b: p });
      }}
      onPointerMove={(e) => {
        if (draft) setDraft({ ...draft, b: point(e) });
      }}
      onPointerUp={finish}
      onPointerCancel={() => setDraft(null)}
    >
      {rects.map((r, i) => (
        <div key={i} className="cov-hud-rect" style={box(r)}>
          <button
            type="button"
            aria-label="Не убирать это место"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onChange(rects.filter((_, j) => j !== i))}
          >
            ×
          </button>
        </div>
      ))}
      {shown.map((r, i) => <div key={`d${i}`} className="cov-hud-rect draft" style={box(r)} />)}
    </div>
  );
}

export function HudPanel({ layer, hud, onStart, onFind, onClean, onCancel }: {
  layer: CoverImageLayer;
  hud: HudState | null;
  onStart: () => void;
  onFind: () => void;
  onClean: () => void;
  onCancel: () => void;
}) {
  const active = hud?.layerId === layer.id;
  if (!layer.src) return null;
  if (!active) {
    return (
      <div className="cov-hud-panel">
        <button type="button" className="btn sm" onClick={onStart} title="Миникарта, лента убийств, плашки очков, панели отряда">
          Убрать интерфейс игры
        </button>
      </div>
    );
  }
  return (
    <div className="cov-hud-panel on" role="group" aria-label="Убрать интерфейс игры">
      <p className="muted">
        {hud.busy === "find" ? "Ищу интерфейс по кадрам ролика…" : hud.note ||
          "Рамки — что закрасить. Лишнее уберите крестиком, недостающее обведите по кадру."}
      </p>
      <div className="cov-row">
        <button type="button" className="btn sm" disabled={!!hud.busy} onClick={onFind}>Найти ещё раз</button>
        <button type="button" className="btn sm ghost" disabled={hud.busy === "clean"} onClick={() => onCancel()}>Отмена</button>
      </div>
      <button type="button" className="btn primary" disabled={!!hud.busy || !hud.rects.length} onClick={onClean}>
        {hud.busy === "clean" ? "Закрашиваю…" : `Закрасить (${hud.rects.length})`}
      </button>
    </div>
  );
}
