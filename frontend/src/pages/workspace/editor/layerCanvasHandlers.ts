import { frameCameraMatrix, interpolateKeys, layerBox, type Layer } from "@/motion";
import { browserEnv } from "@/pages/workspace/motion/runtime";
import { handleAt, layerAt, setAnimated, type TransformHandle } from "@/pages/workspace/motion/edit";
import { INSERT_GEOM, LEGACY_W, legacyCv } from "@/pages/workspace/preview/frame";
import { insertHit } from "@/pages/workspace/editor/editorHelpers";
import { type DraftsEditorState } from "@/pages/workspace/editor/useDraftsEditorState";

/**
 * Мышь на кадре превью: сдвиг, масштаб и поворот слоя, его рамка, перетаскивание мема, правка текста на кадре,
 * выбор точки для трекинга. Считается на каждый рендер, как и остальной сеанс; хуков здесь нет.
 */
export function layerCanvasHandlers({
  toast, motionViewRef, inlineCancelRef, reframeHintRef, layerDragRef, insertsPreviewRef, insertDragRef,
  pickPointRef, tlSelected, setTlSelected, setInserts, setLayers, camera, aspect, layersFmt, inlineEdit,
  setInlineEdit, setPickingPoint, clipTimeExact, clipTimeNow,
}: Pick<
  DraftsEditorState,
  | "toast"
  | "motionViewRef"
  | "inlineCancelRef"
  | "reframeHintRef"
  | "layerDragRef"
  | "insertsPreviewRef"
  | "insertDragRef"
  | "pickPointRef"
  | "tlSelected"
  | "setTlSelected"
  | "setInserts"
  | "setLayers"
  | "camera"
  | "aspect"
  | "layersFmt"
  | "inlineEdit"
  | "setInlineEdit"
  | "setPickingPoint"
> & {
  clipTimeExact: () => number;
  clipTimeNow: () => number;
}) {
  // Мем двигают прямо по кадру: тянут за картинку — едет, тянут за уголок —
  // меняет размер. Пока тянем, правим объект превью (кадр рисуется каждый rAF),
  // а в состояние клипа пишем один раз на отпускании.
  const canvasPoint = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const cv = e.currentTarget;
    const r = cv.getBoundingClientRect();
    return {
      px: ((e.clientX - r.left) / (r.width || 1)) * cv.width,
      py: ((e.clientY - r.top) / (r.height || 1)) * cv.height,
      cv,
    };
  };
  // Слой под мышью или ручка выделенного слоя: сначала ручки (они могут торчать
  // за рамку), потом сами слои сверху вниз.
  const pickLayer = (cv: HTMLCanvasElement, px: number, py: number) => {
    const ctx = cv.getContext("2d");
    if (!ctx) return null;
    const t = clipTimeExact();
    const env = browserEnv(cv.width, cv.height, aspect);
    const cssScale = cv.width / (cv.getBoundingClientRect().width || cv.width);
    const view = motionViewRef.current;
    // Слои followCamera нарисованы через камеру — рамка и попадание тоже.
    const cam = view.camera?.length ? frameCameraMatrix(view.camera, t, cv.width, cv.height) : null;
    const sel = view.selected ? view.layers.find((l) => l.id === view.selected) : null;
    if (sel && !sel.locked && sel.type !== "audio") {
      const box = layerBox(ctx, sel, t, env, { words: view.words, chat: view.chat, voice: view.voice, levels: view.levels }, cam);
      const handle = box ? handleAt(box, px, py, cssScale) : null;
      if (box && handle) return { layer: sel, handle, box, t };
    }
    const hit = layerAt(ctx, view.layers, t, env, px, py, view.words, cam);
    if (!hit) return null;
    const box = layerBox(ctx, hit, t, env, { words: view.words, chat: view.chat, voice: view.voice, levels: view.levels }, cam);
    return box ? { layer: hit, handle: "move" as TransformHandle, box, t } : null;
  };

  const onInsertPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { px, py, cv } = canvasPoint(e);
    const pickPoint = pickPointRef.current;
    if (pickPoint) {
      // Цель трекинга: точка кадра как её видно на превью (с наездом камеры) и момент клипа.
      pickPointRef.current = null;
      setPickingPoint(false);
      e.preventDefault();
      pickPoint({ x: px / cv.width, y: py / cv.height, t: clipTimeExact() });
      return;
    }
    const picked = pickLayer(cv, px, py);
    if (picked) {
      const { layer, handle, box, t } = picked;
      setTlSelected({ kind: "layer", id: layer.id });
      motionViewRef.current = { ...motionViewRef.current, selected: layer.id };
      if (layer.locked) return;
      if (motionViewRef.current.reframed) {
        // Показана перекомпоновка: x/y на экране — не те, что в слое. Править — инспектором
        // (числа формата разметки) или после «Разметить слои в этом формате».
        if (!reframeHintRef.current) {
          reframeHintRef.current = true;
          toast.push(`Слои размечены в ${layersFmt} — тут показан их перенос в ${aspect}. Двигать мышью — после «Разметить слои в этом формате»`, "info");
        }
        return;
      }
      const localT = t - layer.start;
      layerDragRef.current = {
        id: layer.id,
        handle,
        startX: px,
        startY: py,
        cx: box.cx,
        cy: box.cy,
        zoom: layer.followCamera && camera.length ? frameCameraMatrix(camera, t, cv.width, cv.height).scale : 1,
        from: layer,
        localT,
        x: interpolateKeys(layer.keys?.x, localT, layer.x ?? 0.5),
        y: interpolateKeys(layer.keys?.y, localT, layer.y ?? 0.5),
        scale: interpolateKeys(layer.keys?.scale, localT, layer.scale ?? 1),
        rotation: interpolateKeys(layer.keys?.rotation, localT, layer.rotation ?? 0),
      };
      cv.setPointerCapture?.(e.pointerId);
      e.preventDefault();
      return;
    }
    const k = LEGACY_W / cv.width;
    const hit = insertHit(insertsPreviewRef.current, clipTimeNow(), legacyCv(aspect), px * k, py * k);
    if (!hit) {
      if (tlSelected?.kind === "layer") setTlSelected(null);
      return;
    }
    const ins = insertsPreviewRef.current[hit.index];
    insertDragRef.current = {
      ...hit,
      startX: px,
      startY: py,
      from: { x: ins.x, y: ins.y, scale: ins.scale },
    };
    // Взяли мем на кадре — на таймлайне подсвечивается его блок: одно выделение
    // на обе стороны редактора.
    setTlSelected({ kind: "insert", index: hit.index });
    cv.setPointerCapture?.(e.pointerId);
    e.preventDefault();
  };
  const onInsertPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { px, py, cv } = canvasPoint(e);
    const ld = layerDragRef.current;
    if (ld) {
      let next = ld.from;
      if (ld.handle === "move") {
        let nx = ld.x + (px - ld.startX) / cv.width / ld.zoom;
        let ny = ld.y + (py - ld.startY) / cv.height / ld.zoom;
        // Прилипание к центру кадра — самое частое место для титра.
        if (Math.abs(nx - 0.5) < 0.012) nx = 0.5;
        if (Math.abs(ny - 0.5) < 0.008) ny = 0.5;
        next = setAnimated(setAnimated(next, "x", nx, ld.localT), "y", ny, ld.localT);
        motionViewRef.current = { ...motionViewRef.current, guides: { v: nx === 0.5, h: ny === 0.5 } };
      } else if (ld.handle === "scale") {
        const d0 = Math.hypot(ld.startX - ld.cx, ld.startY - ld.cy) || 1;
        const d1 = Math.hypot(px - ld.cx, py - ld.cy);
        next = setAnimated(next, "scale", Math.min(10, Math.max(0.05, ld.scale * (d1 / d0))), ld.localT);
      } else {
        const a0 = Math.atan2(ld.startY - ld.cy, ld.startX - ld.cx);
        const a1 = Math.atan2(py - ld.cy, px - ld.cx);
        let rot = ld.rotation + ((a1 - a0) * 180) / Math.PI;
        if (e.shiftKey) rot = Math.round(rot / 15) * 15;
        next = setAnimated(next, "rotation", Number(rot.toFixed(1)), ld.localT);
      }
      motionViewRef.current = {
        ...motionViewRef.current,
        layers: motionViewRef.current.layers.map((l) => (l.id === ld.id ? next : l)),
      };
      e.preventDefault();
      return;
    }
    const drag = insertDragRef.current;
    if (!drag) {
      // Курсор подсказывает, что слой или вставку можно взять.
      const picked = pickLayer(cv, px, py);
      if (picked) {
        cv.style.cursor = picked.layer.locked
          ? "not-allowed"
          : picked.handle === "rotate"
            ? "crosshair"
            : picked.handle === "scale"
              ? "nwse-resize"
              : "move";
        return;
      }
      const k = LEGACY_W / cv.width;
      const hover = insertHit(insertsPreviewRef.current, clipTimeNow(), legacyCv(aspect), px * k, py * k);
      cv.style.cursor = hover ? (hover.mode === "resize" ? "nwse-resize" : "grab") : "default";
      return;
    }
    const ins = insertsPreviewRef.current[drag.index];
    if (!ins) return;
    if (drag.mode === "move") {
      ins.x = Math.min(1, Math.max(0, drag.from.x + (px - drag.startX) / cv.width));
      ins.y = Math.min(1, Math.max(0, drag.from.y + (py - drag.startY) / cv.height));
    } else {
      // Тянем угол: ширина растёт вдвое быстрее сдвига, потому что центр на месте.
      ins.scale = Math.min(
        INSERT_GEOM.max,
        Math.max(INSERT_GEOM.min, drag.from.scale + (2 * (px - drag.startX)) / cv.width),
      );
    }
    e.preventDefault();
  };
  // Двойной клик по тексту на кадре — поле ввода прямо поверх слоя.
  const onOverlayDoubleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const cv = e.currentTarget;
    const r = cv.getBoundingClientRect();
    const px = ((e.clientX - r.left) / (r.width || 1)) * cv.width;
    const py = ((e.clientY - r.top) / (r.height || 1)) * cv.height;
    const picked = pickLayer(cv, px, py);
    if (!picked || picked.layer.locked) return;
    const l = picked.layer;
    const field = l.type === "text" ? "text" : l.type === "callout" ? "title" : l.type === "chart" ? "title" : null;
    if (!field) return;
    const text = String((l.props as Record<string, unknown>)[field] ?? "");
    const w = Math.max(picked.box.w, cv.width * 0.5);
    setInlineEdit({
      id: l.id,
      field,
      text,
      left: (Math.max(0, picked.box.cx - w / 2) / cv.width) * 100,
      top: (Math.max(0, picked.box.cy - picked.box.h / 2) / cv.height) * 100,
      width: (Math.min(w, cv.width) / cv.width) * 100,
    });
  };
  const commitInlineEdit = (value: string) => {
    if (inlineCancelRef.current) {
      inlineCancelRef.current = false;
      return;
    }
    const edit = inlineEdit;
    setInlineEdit(null);
    if (!edit || value === edit.text) return;
    setLayers((prev) =>
      prev.map((l) => (l.id === edit.id ? ({ ...l, props: { ...l.props, [edit.field]: value } } as Layer) : l)),
    );
  };

  const onInsertPointerUp = () => {
    const ld = layerDragRef.current;
    if (ld) {
      layerDragRef.current = null;
      motionViewRef.current = { ...motionViewRef.current, guides: undefined };
      const moved = motionViewRef.current.layers.find((l) => l.id === ld.id);
      if (moved && moved !== ld.from) setLayers(motionViewRef.current.layers);
      return;
    }
    const drag = insertDragRef.current;
    insertDragRef.current = null;
    if (!drag) return;
    const ins = insertsPreviewRef.current[drag.index];
    if (!ins) return;
    const next = { x: Number(ins.x.toFixed(4)), y: Number(ins.y.toFixed(4)), scale: Number(ins.scale.toFixed(4)) };
    if (next.x === drag.from.x && next.y === drag.from.y && next.scale === drag.from.scale) return;
    setInserts((prev) => prev.map((item, i) => (i === drag.index ? { ...item, ...next } : item)));
  };

  return {
    onInsertPointerDown, onInsertPointerMove, onOverlayDoubleClick, commitInlineEdit, onInsertPointerUp,
  };
}
