import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { coverDocsApi, type CoverApi, type CoverDocState, type CoverFont, type CoverLibrary, type CoverLink, type CoverTemplate, type FrameOrigin } from "@/api/coverDocs";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { layoutText, paintCoverDoc, type CoverDoc, type CoverImageLayer, type CoverImageLike, type CoverLayer, type CoverShapeLayer, type CoverTextLayer } from "@/motion/coverdoc";
import { CoverInspector, type LayerPatch } from "./CoverInspector";
import { contains, isCorner, moveWithSnap, resizeBox, rotationAt, snapLines, type Box, type Guide, type Handle, type SnapLines } from "./geometry";
import "@/styles/cover.css";

const HISTORY_LIMIT = 100;
const SAVE_DELAY_MS = 700;
const COALESCE_MS = 700;
const SNAP_CSS_PX = 7;
const ALL_HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const TEXT_HANDLES: Handle[] = ["nw", "ne", "e", "se", "sw", "w"];
// Что поменяло разбивку на строки: высота рамки текста идёт за этим сама.
const TEXT_FLOW_KEYS = ["text", "font", "weight", "italic", "size", "lineHeight", "letterSpacing", "uppercase", "w"];

const newId = () => `l${Math.random().toString(36).slice(2, 12)}`;

function fontFaceWeight(f: CoverFont): string {
  if (!f.variable) return String(f.weight || 400);
  const [lo, hi] = f.weight_range ?? [100, 900];
  return `${lo} ${hi}`;
}

function makeText(width: number, height: number): CoverTextLayer {
  const w = Math.round(width * 0.8);
  return {
    id: newId(), type: "text", name: "Текст", x: (width - w) / 2, y: height * 0.2, w, h: 120, rotation: 0, opacity: 1,
    locked: false, hidden: false, text: "Текст обложки", font: "", weight: 900, italic: false, size: Math.round(width * 0.1),
    color: "#ffe600", gradient: null, stroke: { color: "#000000", width: Math.round(width * 0.012) }, shadow: null, glow: null,
    plate: null, align: "center", valign: "middle", lineHeight: 1.05, letterSpacing: 0, uppercase: true,
  };
}

function makeShape(width: number, height: number, shape: "rect" | "ellipse"): CoverShapeLayer {
  const side = Math.round(width * 0.4);
  return {
    id: newId(), type: "shape", name: shape === "rect" ? "Прямоугольник" : "Эллипс", x: (width - side) / 2, y: (height - side) / 2,
    w: side, h: side, rotation: 0, opacity: 1, locked: false, hidden: false, shape, radius: 0, fill: "#ffe600", gradient: null,
    stroke: null, shadow: null,
  };
}

function makeImage(src: string, name: string, doc: CoverDoc, size?: { width: number; height: number }, fill = false): CoverImageLayer {
  let box = { x: 0, y: 0, w: doc.width, h: doc.height };
  if (!fill) {
    const ratio = size && size.width && size.height ? size.width / size.height : 1;
    const w = Math.min(doc.width * 0.7, doc.height * 0.6 * ratio);
    const h = w / ratio;
    box = { x: (doc.width - w) / 2, y: (doc.height - h) / 2, w, h };
  }
  return {
    id: newId(), type: "image", name, ...box, rotation: 0, opacity: 1, locked: false, hidden: false, src,
    fit: fill ? "cover" : "contain", crop: null, mask: "none", radius: 0, flipX: false, stroke: null, shadow: null,
  };
}

const layerKindName = (layer: CoverLayer) =>
  layer.name || (layer.type === "text" ? layer.text.slice(0, 24) || "Текст" : layer.type === "image" ? "Картинка" : "Фигура");

type Gesture =
  | { kind: "move"; startX: number; startY: number; base: CoverDoc; layer: CoverLayer; lines: SnapLines; moved: boolean }
  | { kind: "resize"; handle: Handle; base: CoverDoc; layer: CoverLayer; lines: SnapLines; moved: boolean }
  | { kind: "rotate"; base: CoverDoc; layer: CoverLayer; moved: boolean };

type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";
const SAVE_LABEL: Record<SaveState, string> = {
  saved: "Сохранено",
  dirty: "Есть правки",
  saving: "Сохраняю…",
  error: "Не сохранилось, повторю при следующей правке",
  conflict: "Документ изменили в другом окне",
};

/** Размеры, обводка и поля текста растут вместе с рамкой, когда тянут за угол. */
function scaleText(base: CoverTextLayer, box: Box): CoverTextLayer {
  const k = box.w / base.w;
  const round = (v: number) => Math.round(v * 100) / 100;
  return {
    ...base, ...box, size: round(Math.max(8, base.size * k)), letterSpacing: round(base.letterSpacing * k),
    stroke: base.stroke ? { ...base.stroke, width: round(base.stroke.width * k) } : null,
    shadow: base.shadow ? { ...base.shadow, blur: round(base.shadow.blur * k), x: round(base.shadow.x * k), y: round(base.shadow.y * k) } : null,
    glow: base.glow ? { ...base.glow, blur: round(base.glow.blur * k) } : null,
    plate: base.plate ? { ...base.plate, padX: round(base.plate.padX * k), padY: round(base.plate.padY * k), radius: round(base.plate.radius * k) } : null,
  };
}

function TemplateThumb({ template, width, height, images, version }: { template: CoverTemplate; width: number; height: number; images: Map<string, HTMLImageElement>; version: number }) {
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

function ShareDialog({ docId, onClose }: { docId: number; onClose: () => void }) {
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

export function CoverEditor({ api, docId }: { api: CoverApi; docId?: number }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [state, setState] = useState<CoverDocState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [library, setLibrary] = useState<CoverLibrary | null>(null);
  const [doc, setDoc] = useState<CoverDoc | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [past, setPast] = useState<CoverDoc[]>([]);
  const [future, setFuture] = useState<CoverDoc[]>([]);
  const [guides, setGuides] = useState<Guide[]>([]);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [zoomMode, setZoomMode] = useState<"fit" | number>("fit");
  const [fitZoom, setFitZoom] = useState(0.3);
  const [panel, setPanel] = useState<"templates" | "frame" | "files" | "layers" | "props">("templates");
  const [paintVersion, setPaintVersion] = useState(0);
  const [fontVersion, setFontVersion] = useState(0);
  const [assets, setAssets] = useState<string[]>([]);
  const [showZones, setShowZones] = useState(true);
  const [shareOpen, setShareOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [frameT, setFrameT] = useState(0);
  const [frameOrigin, setFrameOrigin] = useState<FrameOrigin>("clean");
  const [thumbT, setThumbT] = useState(0);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const imagesRef = useRef(new Map<string, HTMLImageElement>());
  const fontsRef = useRef(new Set<string>());
  const measureRef = useRef<CanvasRenderingContext2D | null>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const coalesceRef = useRef<{ key: string; at: number } | null>(null);
  const clipboardRef = useRef<CoverLayer | null>(null);
  const revRef = useRef(0);
  const savedDocRef = useRef<CoverDoc | null>(null);
  const docRef = useRef<CoverDoc | null>(null);
  const savingRef = useRef(false);
  const conflictRef = useRef(false);
  docRef.current = doc;

  const readOnly = state?.perm !== "edit";
  const readOnlyRef = useRef(true);
  readOnlyRef.current = readOnly;
  const zoom = zoomMode === "fit" ? fitZoom : zoomMode;
  const selected = useMemo(() => doc?.layers.find((l) => l.id === selectedId) ?? null, [doc, selectedId]);

  /* ---------- загрузка ---------- */

  const load = useCallback(async () => {
    try {
      const fresh = await api.doc();
      setState(fresh);
      setDoc(fresh.doc);
      setAssets(fresh.assets);
      setPast([]);
      setFuture([]);
      savedDocRef.current = fresh.doc;
      revRef.current = fresh.rev;
      conflictRef.current = false;
      setSaveState("saved");
      setLoadError(null);
      setFrameT((fresh.clip?.duration ?? 0) / 2);
      setThumbT((fresh.clip?.duration ?? 0) / 2);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Не удалось открыть обложку");
    }
  }, [api]);

  useEffect(() => {
    void load();
    api.library().then(setLibrary).catch(() => setLibrary({ pictures: [], templates: [], fonts: [] }));
  }, [api, load]);

  // Шрифты — те же файлы, что у компоновщика; без них перенос строк был бы другим, чем в экспорте.
  // Качаем только семейства с холста, а не всю библиотеку.
  const families = useMemo(() => {
    const set = new Set<string>(["Montserrat"]);
    for (const layer of doc?.layers ?? []) if (layer.type === "text" && layer.font) set.add(layer.font);
    return Array.from(set).sort().join("\n");
  }, [doc]);
  useEffect(() => {
    if (!library) return;
    const wanted = new Set(families.split("\n"));
    for (const f of library.fonts) {
      if (!wanted.has(f.family) || fontsRef.current.has(f.file)) continue;
      fontsRef.current.add(f.file);
      const face = new FontFace(f.family, `url(${api.fontUrl(f.file)})`, {
        weight: fontFaceWeight(f),
        style: f.style === "italic" ? "italic" : "normal",
      });
      face.load().then((loaded) => {
        document.fonts.add(loaded);
        setFontVersion((v) => v + 1);
      }).catch(() => undefined);
    }
  }, [api, library, families]);

  // Шрифт доехал — строки могли переноситься иначе: подгоняем рамки текста и перерисовываем.
  useEffect(() => {
    if (!fontVersion || !docRef.current) return;
    const id = window.setTimeout(() => {
      const current = docRef.current;
      if (!current) return;
      setPaintVersion((v) => v + 1);
      if (gestureRef.current) return;
      const layers = current.layers.map((l) => (l.type === "text" ? fitTextBox(l) : l));
      if (layers.some((l, i) => l !== current.layers[i])) setDoc({ ...current, layers });
    }, 120);
    return () => window.clearTimeout(id);
  }, [fontVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  // Картинки документа: новые имена подгружаем, готовые берём из кэша.
  useEffect(() => {
    if (!doc) return;
    const wanted = new Set<string>(assets);
    for (const layer of doc.layers) if (layer.type === "image" && layer.src) wanted.add(layer.src);
    for (const name of wanted) {
      if (imagesRef.current.has(name)) continue;
      const img = new Image();
      img.onload = () => setPaintVersion((v) => v + 1);
      img.src = api.assetUrl(name);
      imagesRef.current.set(name, img);
    }
  }, [api, doc, assets]);

  /* ---------- холст ---------- */

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!doc || !canvas) return;
    const id = requestAnimationFrame(() => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      paintCoverDoc(ctx, doc, {
        getImage: (src) => {
          const img = imagesRef.current.get(src);
          return img && img.complete && img.naturalWidth ? (img as unknown as CoverImageLike) : null;
        },
        createCanvas: (w, h) => {
          const scratch = document.createElement("canvas");
          scratch.width = w;
          scratch.height = h;
          return scratch;
        },
        placeholders: !readOnly,
      });
    });
    return () => cancelAnimationFrame(id);
  }, [doc, paintVersion, readOnly]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || !doc) return;
    const fit = () => {
      const padX = window.innerWidth < 900 ? 16 : 48;
      // Снизу панель масштаба: кадр не должен уходить под неё.
      const padY = window.innerWidth < 900 ? 56 : 84;
      setFitZoom(Math.max(0.05, Math.min((stage.clientWidth - padX) / doc.width, (stage.clientHeight - padY) / doc.height)));
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [doc?.width, doc?.height, doc !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  const measure = useCallback((layer: CoverTextLayer): number => {
    if (!measureRef.current) measureRef.current = document.createElement("canvas").getContext("2d");
    return measureRef.current ? Math.ceil(layoutText(measureRef.current, layer).height) : layer.h;
  }, []);

  /** Рамка текста обнимает строки; при смене высоты неподвижен край по вертикальному выравниванию. */
  const fitTextBox = useCallback((layer: CoverTextLayer): CoverTextLayer => {
    const h = Math.max(1, measure(layer));
    if (h === layer.h) return layer;
    const y = layer.valign === "top" ? layer.y : layer.valign === "bottom" ? layer.y + layer.h - h : layer.y + (layer.h - h) / 2;
    return { ...layer, h, y };
  }, [measure]);

  /* ---------- сохранение ---------- */

  const flushSave = useCallback(async (): Promise<boolean> => {
    const current = docRef.current;
    if (!current || readOnlyRef.current || conflictRef.current) return !conflictRef.current;
    if (current === savedDocRef.current) return true;
    if (savingRef.current) return false;
    savingRef.current = true;
    setSaveState("saving");
    try {
      const saved = await api.save(current, revRef.current);
      revRef.current = saved.rev;
      savedDocRef.current = current;
      setSaveState(docRef.current === current ? "saved" : "dirty");
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        conflictRef.current = true;
        setSaveState("conflict");
      } else {
        setSaveState("error");
      }
      return false;
    } finally {
      savingRef.current = false;
    }
  }, [api]);

  useEffect(() => {
    if (!doc || readOnly || doc === savedDocRef.current || conflictRef.current) return;
    setSaveState("dirty");
    const id = window.setTimeout(() => void flushSave(), SAVE_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [doc, readOnly, flushSave, saveState === "saving"]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (docRef.current !== savedDocRef.current && !readOnlyRef.current) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  /* ---------- история ---------- */

  const commit = useCallback((next: CoverDoc, coalesce?: string) => {
    const now = Date.now();
    const last = coalesceRef.current;
    const same = !!coalesce && last?.key === coalesce && now - last.at < COALESCE_MS;
    coalesceRef.current = coalesce ? { key: coalesce, at: now } : null;
    const before = docRef.current;
    if (before && !same) setPast((p) => [...p.slice(-(HISTORY_LIMIT - 1)), before]);
    setFuture([]);
    setDoc(next);
  }, []);

  const undo = useCallback(() => {
    setPast((p) => {
      const prev = p[p.length - 1];
      if (!prev || !docRef.current) return p;
      const current = docRef.current;
      setFuture((f) => [current, ...f]);
      setDoc(prev);
      return p.slice(0, -1);
    });
    coalesceRef.current = null;
  }, []);

  const redo = useCallback(() => {
    setFuture((f) => {
      const next = f[0];
      if (!next || !docRef.current) return f;
      const current = docRef.current;
      setPast((p) => [...p, current]);
      setDoc(next);
      return f.slice(1);
    });
    coalesceRef.current = null;
  }, []);

  /* ---------- слои ---------- */

  const replaceLayer = useCallback((source: CoverDoc, layer: CoverLayer): CoverDoc => ({
    ...source, layers: source.layers.map((l) => (l.id === layer.id ? layer : l)),
  }), []);

  const updateLayer = useCallback((id: string, patch: LayerPatch, coalesce?: string) => {
    const current = docRef.current;
    const layer = current?.layers.find((l) => l.id === id);
    if (!current || !layer) return;
    let next = { ...layer, ...patch } as CoverLayer;
    if (next.type === "text" && Object.keys(patch).some((k) => TEXT_FLOW_KEYS.includes(k))) next = fitTextBox(next);
    commit(replaceLayer(current, next), coalesce ? `${id}:${coalesce}` : undefined);
  }, [commit, fitTextBox, replaceLayer]);

  const addLayer = useCallback((layer: CoverLayer, atBottom = false) => {
    const current = docRef.current;
    if (!current) return;
    const placed = layer.type === "text" ? fitTextBox(layer) : layer;
    commit({ ...current, layers: atBottom ? [placed, ...current.layers] : [...current.layers, placed] });
    setSelectedId(layer.id);
  }, [commit, fitTextBox]);

  const removeSelected = useCallback(() => {
    const current = docRef.current;
    if (!current || !selectedId) return;
    commit({ ...current, layers: current.layers.filter((l) => l.id !== selectedId) });
    setSelectedId(null);
  }, [commit, selectedId]);

  const duplicate = useCallback((source: CoverLayer) => {
    addLayer({ ...source, id: newId(), x: source.x + 40, y: source.y + 40, locked: false, name: source.name ? `${source.name}, копия` : source.name });
  }, [addLayer]);

  const moveInStack = useCallback((id: string, delta: number) => {
    const current = docRef.current;
    if (!current) return;
    const from = current.layers.findIndex((l) => l.id === id);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= current.layers.length) return;
    const layers = [...current.layers];
    const [item] = layers.splice(from, 1);
    layers.splice(to, 0, item);
    commit({ ...current, layers });
  }, [commit]);

  /* ---------- жесты ---------- */

  const docPoint = (e: { clientX: number; clientY: number }) => {
    const rect = overlayRef.current!.getBoundingClientRect();
    return { x: (e.clientX - rect.left) / zoom, y: (e.clientY - rect.top) / zoom };
  };

  const linesFor = (source: CoverDoc, id: string) =>
    snapLines(source.width, source.height, source.layers.filter((l) => l.id !== id && !l.hidden));

  const onStagePointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (readOnly || !doc) return;
    const p = docPoint(e);
    const hit = [...doc.layers].reverse().find((l) => !l.hidden && !l.locked && contains(l, p.x, p.y)) ?? null;
    // Клик по уже выбранному слою ниже другого не сбрасывает выбор: так можно тянуть перекрытый слой.
    const target = selected && !selected.locked && contains(selected, p.x, p.y) ? selected : hit;
    setSelectedId(target?.id ?? null);
    if (!target) return;
    overlayRef.current?.setPointerCapture(e.pointerId);
    gestureRef.current = { kind: "move", startX: p.x, startY: p.y, base: doc, layer: target, lines: linesFor(doc, target.id), moved: false };
  };

  const startHandle = (e: ReactPointerEvent, handle: Handle | "rotate") => {
    if (!doc || !selected || selected.locked) return;
    e.stopPropagation();
    overlayRef.current?.setPointerCapture(e.pointerId);
    gestureRef.current = handle === "rotate"
      ? { kind: "rotate", base: doc, layer: selected, moved: false }
      : { kind: "resize", handle, base: doc, layer: selected, lines: linesFor(doc, selected.id), moved: false };
  };

  const onPointerMove = (e: ReactPointerEvent) => {
    const gesture = gestureRef.current;
    if (!gesture) return;
    const p = docPoint(e);
    const tolerance = SNAP_CSS_PX / zoom;
    let next: CoverLayer;
    let lines: Guide[] = [];
    if (gesture.kind === "move") {
      const dx = p.x - gesture.startX;
      const dy = p.y - gesture.startY;
      if (!gesture.moved && Math.hypot(dx, dy) * zoom < 3) return;
      const snapped = moveWithSnap(gesture.layer, dx, dy, gesture.lines, e.altKey ? 0 : tolerance);
      next = { ...gesture.layer, x: snapped.x, y: snapped.y } as CoverLayer;
      lines = snapped.guides;
    } else if (gesture.kind === "rotate") {
      next = { ...gesture.layer, rotation: rotationAt(gesture.layer, p.x, p.y, e.shiftKey) } as CoverLayer;
    } else {
      const layer = gesture.layer;
      const corner = isCorner(gesture.handle);
      // Угол картинки и текста держит пропорции, Shift отпускает; у фигуры наоборот.
      const keep = corner && (layer.type === "shape" ? e.shiftKey : layer.type === "text" || !e.shiftKey);
      const out = resizeBox(layer, gesture.handle, p.x, p.y, keep, e.altKey ? null : gesture.lines, tolerance);
      lines = out.guides;
      if (layer.type === "text") {
        next = corner ? scaleText(layer, out.box) : fitTextBox({ ...layer, ...out.box });
      } else {
        next = { ...layer, ...out.box } as CoverLayer;
      }
    }
    gesture.moved = true;
    setGuides(lines);
    setDoc(replaceLayer(gesture.base, next));
  };

  const onPointerUp = () => {
    const gesture = gestureRef.current;
    gestureRef.current = null;
    setGuides([]);
    if (gesture?.moved) {
      setPast((p) => [...p.slice(-(HISTORY_LIMIT - 1)), gesture.base]);
      setFuture([]);
      coalesceRef.current = null;
    }
  };

  /* ---------- клавиши ---------- */

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      if (readOnlyRef.current) return;
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      const current = docRef.current;
      const layer = current?.layers.find((l) => l.id === selectedId) ?? null;
      if (mod && (e.code === "KeyZ" || key === "z")) {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (mod && (e.code === "KeyY" || key === "y")) {
        e.preventDefault();
        redo();
      } else if (mod && (e.code === "KeyC" || key === "c")) {
        if (layer) clipboardRef.current = layer;
      } else if (mod && (e.code === "KeyV" || key === "v")) {
        if (clipboardRef.current) {
          e.preventDefault();
          duplicate(clipboardRef.current);
        }
      } else if (mod && (e.code === "KeyD" || key === "d")) {
        e.preventDefault();
        if (layer) duplicate(layer);
      } else if ((e.key === "Delete" || e.key === "Backspace") && layer) {
        e.preventDefault();
        removeSelected();
      } else if (e.key === "Escape") {
        setSelectedId(null);
      } else if (e.key === "[" && layer) {
        moveInStack(layer.id, -1);
      } else if (e.key === "]" && layer) {
        moveInStack(layer.id, 1);
      } else if (e.key.startsWith("Arrow") && layer && !layer.locked) {
        e.preventDefault();
        const step = e.shiftKey ? 10 : 1;
        const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
        const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
        updateLayer(layer.id, { x: layer.x + dx, y: layer.y + dy }, "nudge");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, undo, redo, duplicate, removeSelected, moveInStack, updateLayer]);

  /* ---------- файлы, кадры, шаблоны ---------- */

  const upload = async (file: File, layerId?: string) => {
    setBusy("upload");
    try {
      const made = await api.upload(file);
      setAssets((list) => (list.includes(made.name) ? list : [...list, made.name]));
      if (layerId) {
        updateLayer(layerId, { src: made.name });
      } else if (docRef.current) {
        addLayer(makeImage(made.name, file.name.replace(/\.[^.]+$/, "").slice(0, 60) || "Картинка", docRef.current, made));
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Не удалось загрузить картинку");
    } finally {
      setBusy(null);
    }
  };

  const insertFrame = async (replace: boolean) => {
    setBusy("frame");
    try {
      const made = await api.frame(frameT, frameOrigin);
      setAssets((list) => [...list, made.name]);
      const current = docRef.current;
      if (!current) return;
      if (replace && selected?.type === "image") {
        updateLayer(selected.id, { src: made.name });
      } else {
        // Под текстом, но поверх прежних картинок: иначе новый кадр на весь экран никто не увидит.
        const layer = makeImage(made.name, `Кадр ${made.t.toFixed(1)} с`, current, made, true);
        const at = current.layers.findIndex((l) => l.type === "text");
        const layers = [...current.layers];
        layers.splice(at < 0 ? layers.length : at, 0, layer);
        commit({ ...current, layers });
        setSelectedId(layer.id);
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Кадр не получился");
    } finally {
      setBusy(null);
    }
  };

  const applyTemplate = async (template: CoverTemplate) => {
    setBusy("template");
    try {
      const out = await api.template(template.id);
      setAssets(out.assets);
      const current = docRef.current;
      if (!current) return;
      const layers = out.layers.map((l) => (l.type === "text" ? fitTextBox(l) : l));
      commit({ ...current, layers });
      setSelectedId(layers.find((l) => l.type === "text")?.id ?? null);
      const empty = layers.filter((l) => l.type === "image" && !l.src);
      if (empty.length) toast.success(`Шаблон разложен. Осталось поставить картинку: ${empty.map((l) => l.name).join(", ")}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Шаблон не разложился");
    } finally {
      setBusy(null);
    }
  };

  const exportFile = async (kind: "png" | "jpg") => {
    setBusy("export");
    try {
      if (!readOnly) await flushSave();
      const a = document.createElement("a");
      a.href = api.exportUrl(kind, true);
      a.download = `cover.${kind}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } finally {
      setBusy(null);
    }
  };

  const applyToClip = async () => {
    if (!docId) return;
    setBusy("apply");
    try {
      await flushSave();
      const out = await coverDocsApi.apply(docId, true);
      toast.success(out.note || "Обложка клипа сменена");
      qc.invalidateQueries({ queryKey: ["clips"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Не удалось сделать обложкой");
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    const id = window.setTimeout(() => setThumbT(frameT), 150);
    return () => window.clearTimeout(id);
  }, [frameT]);

  /* ---------- вид ---------- */

  if (loadError) {
    return (
      <div className="cov-gone">
        <h1>{state ? "Обложка не открылась" : "Ссылка не работает"}</h1>
        <p>{loadError}. Попросите новую ссылку у того, кто её прислал.</p>
      </div>
    );
  }
  if (!state || !doc) return <div className="cov-gone"><Loading label="Открываю обложку…" /></div>;

  const size = { width: doc.width * zoom, height: doc.height * zoom };
  const portrait = doc.height > doc.width;
  const handles = selected?.type === "text" ? TEXT_HANDLES : ALL_HANDLES;
  const duration = state.clip?.duration ?? 0;
  const tabs: [typeof panel, string][] = readOnly
    ? [["layers", "Слои"]]
    : [["templates", "Шаблоны"], ["frame", "Кадр"], ["files", "Файлы"], ["layers", "Слои"], ["props", "Свойства"]];

  return (
    <div className={`cov-app${readOnly ? " view" : ""}`} data-panel={panel}>
      <header className="cov-top">
        {state.owner ? <Link className="btn ghost sm" to="/clips">К клипам</Link> : null}
        <h1 title={state.title}>{state.title}</h1>
        {readOnly ? <span className="cov-state">Только просмотр</span> : <span className={`cov-state ${saveState}`} role="status">{SAVE_LABEL[saveState]}</span>}
        {saveState === "conflict" ? <button type="button" className="btn sm" onClick={() => void load()}>Открыть свежую версию</button> : null}
        {readOnly ? null : (
          <div className="cov-tools" role="toolbar" aria-label="Добавить на холст">
            <button type="button" className="btn sm" onClick={() => addLayer(makeText(doc.width, doc.height))}>Текст</button>
            <button type="button" className="btn sm" onClick={() => addLayer(makeShape(doc.width, doc.height, "rect"))}>Прямоугольник</button>
            <button type="button" className="btn sm" onClick={() => addLayer(makeShape(doc.width, doc.height, "ellipse"))}>Эллипс</button>
            <label className="btn sm cov-file">
              <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void upload(file);
                e.target.value = "";
              }} />
              Картинка
            </label>
            <span className="cov-sep" />
            <button type="button" className="btn sm ghost" disabled={!past.length} onClick={undo} title="Ctrl+Z">Отменить</button>
            <button type="button" className="btn sm ghost" disabled={!future.length} onClick={redo} title="Ctrl+Shift+Z">Вернуть</button>
          </div>
        )}
        <div className="cov-actions">
          <button type="button" className="btn sm" disabled={busy === "export"} onClick={() => void exportFile("png")}>Скачать PNG</button>
          {state.owner && !readOnly ? <button type="button" className="btn sm" onClick={() => setShareOpen(true)}>Ссылка</button> : null}
          {state.owner && state.clip_id ? (
            <button type="button" className="btn sm primary" disabled={busy === "apply"} onClick={() => void applyToClip()}>
              {busy === "apply" ? "Ставлю…" : "Сделать обложкой"}
            </button>
          ) : null}
        </div>
      </header>

      <nav className="cov-tabs" aria-label="Панели редактора">
        {tabs.map(([id, name]) => (
          <button key={id} type="button" className={`${panel === id ? "on" : ""} ${id === "props" ? "only-narrow" : ""}`} onClick={() => setPanel(id)}>{name}</button>
        ))}
      </nav>

      <aside className="cov-left">
        {panel === "templates" || panel === "props" ? (
          <div className="cov-pane cov-pane-templates">
            <p className="muted">Раскладка заменит все слои. Кнопка «Отменить» вернёт прежние.</p>
            <div className="cov-templates">
              {(library?.templates ?? []).map((t) => (
                <button key={t.id} type="button" disabled={!!busy} onClick={() => void applyTemplate(t)} title={t.note}>
                  <TemplateThumb template={t} width={doc.width} height={doc.height} images={imagesRef.current} version={fontVersion} />
                  <span>{t.name}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {panel === "frame" ? (
          <div className="cov-pane">
            {state.clip ? (
              <>
                <img className="cov-thumb" style={{ aspectRatio: `${doc.width} / ${doc.height}` }} src={api.thumbUrl(thumbT)} alt={`Кадр на ${thumbT.toFixed(1)} с`} />
                <label className="field">
                  <span>Момент ролика: {frameT.toFixed(1)} с из {duration.toFixed(1)}</span>
                  <input type="range" min={0} max={Math.max(0, duration - 0.1)} step={0.1} value={frameT} onChange={(e) => setFrameT(Number(e.target.value))} />
                </label>
                <label className="field">
                  <span>Какой кадр взять</span>
                  <select className="input" value={frameOrigin} onChange={(e) => setFrameOrigin(e.target.value as FrameOrigin)}>
                    <option value="clean">Ролик без текста и субтитров</option>
                    <option value="render">Как в готовом ролике</option>
                    <option value="source">Из исходника, весь кадр</option>
                  </select>
                </label>
                <button type="button" className="btn primary" disabled={!!busy} onClick={() => void insertFrame(false)}>
                  {busy === "frame" ? "Беру кадр…" : "Вставить новым слоем"}
                </button>
                {selected?.type === "image" ? (
                  <button type="button" className="btn" disabled={!!busy} onClick={() => void insertFrame(true)}>Заменить в выбранном слое</button>
                ) : null}
              </>
            ) : <p className="muted">Эта обложка не привязана к ролику: загрузите картинку.</p>}
          </div>
        ) : null}
        {panel === "files" ? (
          <div className="cov-pane">
            {library?.pictures.length ? (
              <>
                <strong>Картинки этого клипа</strong>
                <div className="cov-assets">
                  {library.pictures.map((p) => (
                    <button key={p.name} type="button" title={p.label} onClick={() => addLayer(makeImage(p.name, p.label, doc, p))}>
                      <img src={api.assetUrl(p.name)} alt={p.label} loading="lazy" />
                    </button>
                  ))}
                </div>
              </>
            ) : null}
            <strong>Файлы обложки</strong>
            {assets.length ? (
              <div className="cov-assets">
                {assets.map((name) => (
                  <button key={name} type="button" onClick={() => addLayer(makeImage(name, "Картинка", doc))} aria-label="Добавить картинку на холст">
                    <img src={api.assetUrl(name)} alt="" loading="lazy" />
                  </button>
                ))}
              </div>
            ) : <p className="muted">Пока ничего. Загрузите PNG аватара или возьмите кадр из ролика.</p>}
            <label className="btn cov-file">
              <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void upload(file);
                e.target.value = "";
              }} />
              {busy === "upload" ? "Загружаю…" : "Загрузить картинку"}
            </label>
          </div>
        ) : null}
        {panel === "layers" ? (
          <div className="cov-pane cov-layers">
            {doc.layers.length ? [...doc.layers].reverse().map((layer) => (
              <div key={layer.id} className={`cov-layer${layer.id === selectedId ? " on" : ""}${layer.hidden ? " off" : ""}`}>
                <button type="button" className="cov-layer-name" onClick={() => setSelectedId(layer.id)}>{layerKindName(layer)}</button>
                {readOnly ? null : (
                  <>
                    <button type="button" aria-label="Выше" title="Выше ( ] )" onClick={() => moveInStack(layer.id, 1)}>▲</button>
                    <button type="button" aria-label="Ниже" title="Ниже ( [ )" onClick={() => moveInStack(layer.id, -1)}>▼</button>
                    <button type="button" aria-pressed={layer.hidden} title={layer.hidden ? "Показать" : "Скрыть"} onClick={() => updateLayer(layer.id, { hidden: !layer.hidden })}>{layer.hidden ? "Скрыт" : "Виден"}</button>
                    <button type="button" aria-pressed={layer.locked} title={layer.locked ? "Открепить" : "Закрепить"} onClick={() => updateLayer(layer.id, { locked: !layer.locked })}>{layer.locked ? "Закреплён" : "Свободен"}</button>
                  </>
                )}
              </div>
            )) : <p className="muted">Слоёв нет: выберите шаблон или добавьте текст.</p>}
          </div>
        ) : null}
      </aside>

      <main className="cov-stage" ref={stageRef}>
        <div className="cov-sheet" style={size}>
          <canvas ref={canvasRef} className="cov-canvas" width={doc.width} height={doc.height} style={size} />
          <div ref={overlayRef} className="cov-overlay" onPointerDown={onStagePointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
               onDoubleClick={() => {
                 if (selected?.type === "text") {
                   setPanel("props");
                   window.setTimeout(() => textRef.current?.focus(), 0);
                 }
               }}>
            {showZones && portrait && !readOnly ? (
              <>
                <div className="cov-zone" style={{ top: 0, height: doc.height * 0.1 * zoom }} />
                <div className="cov-zone" style={{ bottom: 0, height: doc.height * 0.2 * zoom }} />
              </>
            ) : null}
            {guides.map((g, i) => (
              <div key={i} className={`cov-guide ${g.axis}`} style={g.axis === "x" ? { left: g.at * zoom } : { top: g.at * zoom }} />
            ))}
            {selected && !readOnly ? (
              <div
                className={`cov-frame${selected.locked ? " locked" : ""}`}
                style={{ left: selected.x * zoom, top: selected.y * zoom, width: selected.w * zoom, height: selected.h * zoom, transform: `rotate(${selected.rotation}deg)` }}
              >
                {selected.locked ? null : (
                  <>
                    {handles.map((h) => <span key={h} className={`cov-handle ${h}`} onPointerDown={(e) => startHandle(e, h)} />)}
                    <span className="cov-rotate" onPointerDown={(e) => startHandle(e, "rotate")} title="Повернуть. Shift: без притяжения" />
                  </>
                )}
              </div>
            ) : null}
          </div>
        </div>
        <div className="cov-zoom">
          <button type="button" aria-label="Мельче" onClick={() => setZoomMode(Math.max(0.05, zoom / 1.2))}>−</button>
          <button type="button" onClick={() => setZoomMode("fit")} title="По размеру окна">{Math.round(zoom * 100)} %</button>
          <button type="button" aria-label="Крупнее" onClick={() => setZoomMode(Math.min(3, zoom * 1.2))}>+</button>
          {portrait && !readOnly ? (
            <label className="cov-check" title="Сверху и снизу кнопки площадок перекрывают кадр">
              <input type="checkbox" checked={showZones} onChange={(e) => setShowZones(e.target.checked)} />
              <span>Зоны площадок</span>
            </label>
          ) : null}
        </div>
      </main>

      {readOnly ? null : (
        <aside className="cov-right">
          {selected ? (
            <>
              <CoverInspector
                layer={selected}
                update={(patch, key) => updateLayer(selected.id, patch, key)}
                fonts={library?.fonts ?? []}
                assets={assets}
                assetUrl={api.assetUrl}
                onUpload={(file, id) => void upload(file, id)}
                textRef={textRef}
              />
              <div className="cov-row">
                <button type="button" className="btn sm" onClick={() => duplicate(selected)}>Копия</button>
                <button type="button" className="btn sm danger" onClick={removeSelected}>Удалить слой</button>
              </div>
            </>
          ) : (
            <div className="cov-pane">
              <label className="cov-color">
                <span>Фон кадра</span>
                <input type="color" value={doc.bg.slice(0, 7)} onChange={(e) => commit({ ...doc, bg: e.target.value }, "bg")} />
              </label>
              <p className="muted">Выберите слой на холсте. Двойной клик по тексту открывает его правку. Стрелки двигают слой, Delete удаляет, Ctrl+D делает копию.</p>
            </div>
          )}
        </aside>
      )}

      {shareOpen && docId ? <ShareDialog docId={docId} onClose={() => setShareOpen(false)} /> : null}
    </div>
  );
}
