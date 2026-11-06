import { clipPlansApi } from "@/api/clipPlans";
import { type Clip, type SourceDetail, segLen, segSpeed } from "@/api/types";
import { writePref, type InspTabDef } from "@/pages/workspace/InspectorParts";
import { type SubtitleStyle, type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { endScreenFor, frameCameraMatrix, interpolateKeys, layerBox, type CameraKey, type Layer } from "@/motion";
import { browserEnv } from "@/pages/workspace/motion/runtime";
import { duplicateLayer, handleAt, layerTitle, layerAt, placeLayers, setAnimated, type TransformHandle } from "@/pages/workspace/motion/edit";
import { normalizeBackdrop } from "@/pages/workspace/chromaKey";
import { BackdropStrip, isCanvasSource } from "@/pages/workspace/canvasClip";
import { INSERT_GEOM, LEGACY_W, legacyCv, type InsertPreview } from "@/pages/workspace/preview/frame";
import { clipToSource, pieceBounds, type Selection } from "@/pages/workspace/ClipTimeline";
import { clipTimeOf, captionWordsWithOriginal, FRAME_SEC, insertHit, type InspTab } from "@/pages/workspace/editor/editorHelpers";
import { type DraftsEditorState } from "@/pages/workspace/editor/useDraftsEditorState";

/**
 * Часть, которая считается на каждый рендер, когда видео уже загружено: обработчики и то, что им нужно.
 * Хуков здесь нет: она идёт после ранних выходов обёртки (загрузка, ошибка, нет черновиков).
 */
export function buildDraftsEditorSession(state: DraftsEditorState, source: SourceDetail) {
  const {
    toast, videoRef, subLinesRef, subWordsRef, bannerGeomRef, subStyleRef, subPosPctRef, inspScrollRef,
    assetMediaRef, query, sourceMissing, subs, assetsQuery, view, playbackRef, playRanges, stopPlayback, openEditor,
    backToMoments, reframedView, motionViewRef, inlineCancelRef, planChat, planVoice, planLevels, clipNowRef,
    reframeHintRef, layerDragRef, insertsPreviewRef, insertDragRef, hoverPlanRef, keyHandlerRef, planSubs, undoRef,
    duplicateRef, copyLayerRef, pasteLayerRef, nudgeLayerRef, plans, flushSettings, undoEdit, pickPointRef,
    setPlanFlag, activePlan, planGroups, selectedPreset, editPieces, tlSelected, setTlSelected, inspTab, setInspTab,
    subLines, setSubLines, setActivePlanId, selectedSeg, setSelectedSeg, setChosen, subId, bannerOn, bannerId, 
    inserts, setInserts, layers, setLayers, camera, setCamera, setMute, aspect, layersFmt,
    setLayersFmt, motionBlur, inlineEdit, setInlineEdit, setWatchOpen, bannerHeightPct, bannerPosPct, subPosPct,
    setPickingPoint,
  } = state;
  // Пустой холст (app/canvas.py): основа — зелёная заглушка, видно только фон клипа.
  const isCanvas = isCanvasSource(source);
  const selectedSegment = activePlan?.segments.find((s) => s.id === selectedSeg) ?? null;
  const toggleChosen = (id: number) =>
    setChosen((prev) => {
      const next = new Set(prev ?? []);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  // Clip timeline ⇄ source time for the open clip (pieces play back to back).
  const clipTimeNow = (): number => {
    const segs = activePlan?.segments ?? [];
    const cur = videoRef.current?.currentTime ?? 0;
    let t = 0;
    for (const s of segs) {
      if (cur >= s.start_sec && cur <= s.end_sec) return Number((t + (cur - s.start_sec) / segSpeed(s)).toFixed(1));
      t += segLen(s);
    }
    return 0;
  };
  const seekClipTime = (clipT: number, play = true) => {
    const src = clipToSource(activePlan?.segments ?? [], clipT);
    if (src == null || !videoRef.current) return;
    playbackRef.current = null;
    videoRef.current.currentTime = src;
    if (play && !sourceMissing) void videoRef.current.play();
  };
  // Шаг по клипу в его собственном времени: на стыке кусков курсор перепрыгивает
  // в следующий кусок исходника, а не уезжает в вырезанное.
  const stepClip = (deltaSec: number) => {
    const v = videoRef.current;
    if (!v || !activePlan) return;
    v.pause();
    const now = clipTimeOf(activePlan.segments, v.currentTime) ?? 0;
    const total = activePlan.segments.reduce((acc, s) => acc + segLen(s), 0);
    seekClipTime(Math.min(Math.max(0, now + deltaSec), Math.max(0, total - 0.001)), false);
  };
  // ▶ Весь монтаж на весь экран: сначала сохранить панель, чтобы плеер видел ровно её.
  const openWatch = async () => {
    stopPlayback();
    // Плеер открывается сразу, в нажатии (телефон иначе не даст весь экран и звук); сохранение — следом,
    // отпечаток монтажа подхватит его сам.
    setWatchOpen(true);
    await flushSettings();
  };
  const togglePlay = () => {
    const v = videoRef.current;
    if (!v || !activePlan?.segments.length) return;
    if (!v.paused) {
      stopPlayback();
      return;
    }
    // Плей с места курсора до конца клипа (если курсор вне клипа — с начала).
    const now = clipTimeOf(activePlan.segments, v.currentTime);
    let acc = 0;
    const ranges: { start: number; end: number; speed?: number }[] = [];
    for (const s of activePlan.segments) {
      const len = segLen(s);
      if (now == null || now < acc + len - 0.05) {
        const from = now != null && now > acc ? s.start_sec + (now - acc) * segSpeed(s) : s.start_sec;
        ranges.push({ start: from, end: s.end_sec, speed: segSpeed(s) });
      }
      acc += len;
    }
    playRanges(ranges.length ? ranges : activePlan.segments.map((s) => ({ start: s.start_sec, end: s.end_sec, speed: segSpeed(s) })));
  };
  // Preview media for the open clip's inserts (created once per file, reused).
  const insertsPreview: InsertPreview[] = inserts.map((ins) => {
    const asset = assetsQuery.data?.find((a) => a.id === ins.asset_id);
    let el = asset ? assetMediaRef.current.get(asset.id) ?? null : null;
    if (asset && !el) {
      if (asset.kind === "video") {
        const vid = document.createElement("video");
        vid.src = asset.url;
        vid.loop = true;
        vid.playsInline = true;
        vid.preload = "auto";
        el = vid;
      } else if (asset.kind === "audio") {
        el = new Audio(asset.url);
      } else {
        const img = new Image();
        img.src = asset.url; // GIFs animate when drawn from an <img>
        el = img;
      }
      assetMediaRef.current.set(asset.id, el);
    }
    return {
      at: ins.at,
      duration: ins.duration,
      mode: asset?.kind === "audio" ? "sound" : ins.mode,
      label: asset?.label ?? "файл удалён",
      volume: ins.volume,
      x: ins.x ?? INSERT_GEOM.x,
      y: ins.y ?? INSERT_GEOM.y,
      scale: ins.scale ?? INSERT_GEOM.scale,
      el,
    };
  });
  // Во время перетаскивания правим объекты превью напрямую: кадр перерисовывается
  // каждый rAF, а состояние обновляем один раз на отпускании — иначе весь
  // редактор перерисовывался бы на каждое движение мыши.
  insertsPreviewRef.current = insertsPreview;
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
  const clipTimeExact = () => clipTimeOf(activePlan?.segments ?? [], videoRef.current?.currentTime ?? 0) ?? 0;
  clipNowRef.current = clipTimeExact;
  const selectedLayerId = tlSelected?.kind === "layer" ? tlSelected.id : null;
  // Черновик слоёв живёт в ref, пока тянем мышью; иначе — то, что в стейте.
  // С J/L — слова так, как их слышно (как в рендере), иначе — сохранённые; плюс оригинал перевода.
  const captionWords = captionWordsWithOriginal(planSubs.data);
  const chatMessages = Array.isArray(planChat.data) ? planChat.data : [];
  const voiceTracks = planVoice.data?.voice;
  const levelTracks = planLevels.data?.levels;
  // Длина клипа «кусками подряд» — та же шкала, что у слоёв в превью (clipTimeOf).
  const viewDuration = (activePlan?.segments ?? []).reduce((acc, seg) => acc + segLen(seg), 0);
  // Слои размечены в другом формате — превью рисует их перекомпоновку (как рендер); тянуть
  // мышью такие нельзя (сдвиг лёг бы не в те координаты) — сначала «Разметить слои в этом формате».
  const shown = reframedView ?? { layers, camera };
  // Концовка-баннер (render.end_screen): начало — начало слоя end_banner, как в рендере.
  const endScreen = endScreenFor(activePlan?.render_settings?.end_screen, shown.layers, viewDuration);
  if (!layerDragRef.current)
    motionViewRef.current = {
      layers: shown.layers,
      camera: shown.camera,
      selected: selectedLayerId,
      words: captionWords,
      chat: chatMessages,
      voice: voiceTracks,
      levels: levelTracks,
      duration: viewDuration,
      reframed: Boolean(reframedView),
      endScreen,
      motionBlur: motionBlur ?? undefined,
      fps: selectedPreset?.fps || 30,
    };
  else
    motionViewRef.current = {
      ...motionViewRef.current,
      camera: shown.camera,
      selected: selectedLayerId,
      words: captionWords,
      chat: chatMessages,
      voice: voiceTracks,
      levels: levelTracks,
      duration: viewDuration,
      endScreen,
      motionBlur: motionBlur ?? undefined,
      fps: selectedPreset?.fps || 30,
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

  copyLayerRef.current = () => {
    if (view !== "editor") return false;
    const layer = tlSelected?.kind === "layer" ? layers.find((l) => l.id === tlSelected.id) : null;
    if (!layer) return false;
    try {
      localStorage.setItem("vvf.layerClipboard", JSON.stringify({ layers: [layer] }));
    } catch {
      return false;
    }
    toast.push(`Слой «${layerTitle(layer)}» скопирован — Ctrl+V вставит его под курсор (и в другом клипе)`, "info");
    return true;
  };
  pasteLayerRef.current = () => {
    if (view !== "editor" || document.querySelector(".modal-backdrop, .drawer-backdrop")) return false;
    let raw: string | null = null;
    try {
      raw = localStorage.getItem("vvf.layerClipboard");
    } catch {
      return false;
    }
    if (!raw || !activePlan) return false;
    dropLayersAt(raw, Number(clipTimeExact().toFixed(2)), null);
    return true;
  };
  nudgeLayerRef.current = (key, big) => {
    if (view !== "editor") return false;
    const layer = tlSelected?.kind === "layer" ? layers.find((l) => l.id === tlSelected.id) : null;
    if (!layer || layer.locked || layer.type === "audio") return false;
    const step = big ? 0.02 : 0.004;
    const dx = key === "ArrowLeft" ? -step : key === "ArrowRight" ? step : 0;
    const dy = key === "ArrowUp" ? -step : key === "ArrowDown" ? step : 0;
    const localT = Math.max(0, clipTimeExact() - layer.start);
    const x = interpolateKeys(layer.keys?.x, localT, layer.x ?? 0.5) + dx;
    const y = interpolateKeys(layer.keys?.y, localT, layer.y ?? 0.5) + dy;
    const next = setAnimated(setAnimated(layer, "x", x, localT), "y", y, localT);
    setLayers((prev) => prev.map((l) => (l.id === layer.id ? next : l)));
    return true;
  };
  duplicateRef.current = () => {
    if (view !== "editor" || tlSelected?.kind !== "layer") return;
    const r = duplicateLayer(layers, tlSelected.id, clipDuration);
    setLayers(r.layers);
    if (r.id) setTlSelected({ kind: "layer", id: r.id });
  };
  undoRef.current = () => {
    if (activePlan && !undoEdit.isPending) undoEdit.mutate(activePlan.id);
  };

  const splitAtClip = (clipSec: number) => {
    const segs = activePlan?.segments ?? [];
    const bounds = pieceBounds(segs);
    const hit = bounds.findIndex((r) => clipSec > r.start + 0.4 && clipSec < r.end - 0.4);
    if (hit < 0) {
      toast.push("Курсор не внутри куска — двигать нечего", "info");
      return;
    }
    const cutAt = segs[hit].start_sec + (clipSec - bounds[hit].start);
    editPieces.mutate((pieces) => [
      ...pieces.slice(0, hit),
      { ...pieces[hit], end_sec: Number(cutAt.toFixed(2)) },
      // Второй кусок — новый: без segment_id сервер создаст его сам.
      {
        ...pieces[hit],
        segment_id: undefined,
        start_sec: Number(cutAt.toFixed(2)),
        title: `${pieces[hit].title || "Кусок"} · хвост`,
      },
      ...pieces.slice(hit + 1),
    ]);
  };

  const deleteSelected = () => {
    if (!tlSelected) return;
    if (tlSelected.kind === "piece") {
      editPieces.mutate((pieces) => pieces.filter((_, i) => i !== tlSelected.index));
    } else if (tlSelected.kind === "insert") {
      setInserts((prev) => prev.filter((_, i) => i !== tlSelected.index));
    } else if (tlSelected.kind === "layer") {
      setLayers((prev) => prev.filter((l) => l.id !== tlSelected.id));
    } else if (tlSelected.kind === "cam") {
      setCamera((prev) => prev.filter((_, i) => i !== tlSelected.index));
    } else if (tlSelected.kind === "mute") {
      setMute((prev) => prev.filter((_, i) => i !== tlSelected.index));
    } else {
      const next = subLines.filter((_, i) => i !== tlSelected.index);
      setSubLines(next);
      subLinesRef.current = next;
      if (activePlan) void clipPlansApi.saveSubtitles(activePlan.id, next).catch(() => undefined);
    }
    setTlSelected(null);
  };

  // Положить слои (из шаблона, библиотеки, ИИ) в клип с момента `at`: времена
  // сдвигаются целиком, дорожки — свободные. Новый верхний слой сразу выделен.
  const clipDuration = (activePlan?.segments ?? []).reduce((acc, seg) => acc + segLen(seg), 0);
  const addLayers = (incoming: Layer[], at: number, cameraKeys?: CameraKey[], track?: number | null) => {
    if (!incoming.length && !cameraKeys?.length) return;
    // Первые слои клипа размечаются прямо в его формате (шаблоны собраны под него же).
    if (!layers.length && !camera.length && layersFmt !== aspect) setLayersFmt(aspect);
    const minStart = incoming.length ? Math.min(...incoming.map((l) => l.start)) : at;
    const shift = at - minStart;
    const moved = incoming.map((l) => ({ ...l, start: l.start + shift, end: l.end + shift }) as Layer);
    const next = placeLayers(layers, moved, clipDuration, track);
    setLayers(next);
    if (cameraKeys?.length) {
      const camShift = cameraKeys.map((k) => ({ ...k, t: Number((k.t + shift).toFixed(3)) }));
      setCamera((prev) => [...prev, ...camShift].sort((a, b) => a.t - b.t));
    }
    // Выделяем главный слой пачки — у шаблонов он первый (титр), а искры и
    // подложки идут следом.
    const main = next.slice(layers.length).find((l) => l.type !== "audio") ?? next[next.length - 1];
    if (main) setTlSelected({ kind: "layer", id: main.id });
  };
  const addAtCursor = (incoming: Layer[], cameraKeys?: CameraKey[]) => {
    if (!activePlan) return;
    const at = Number(clipTimeExact().toFixed(2));
    addLayers(incoming, at, cameraKeys);
    // В самый момент начала слой ещё «влетает» (масштаб 0 у pop) — подводим
    // курсор чуть вперёд, чтобы новый слой сразу было видно целиком.
    const graphics = incoming.filter((l) => l.type !== "audio");
    if (graphics.length && videoRef.current?.paused) {
      const minStart = Math.min(...graphics.map((l) => l.start));
      const span = Math.max(...graphics.map((l) => l.end)) - minStart;
      seekClipTime(Math.min(clipDuration - 0.05, at + Math.min(0.7, span * 0.4)), false);
    }
  };
  const dropLayersAt = (payload: string, at: number, track: number | null) => {
    try {
      const data = JSON.parse(payload) as { layers?: Layer[]; camera?: CameraKey[] };
      addLayers(data.layers ?? [], at, data.camera, track);
    } catch {
      toast.error("Не понял, что брошено на таймлайн");
    }
  };

  // Выбрали вставку на таймлайне — везём превью к ней: иначе рамку и ручки не
  // видно, мем показывается только в свои секунды.
  const selectOnTimeline = (next: Selection) => {
    setTlSelected(next);
    if (next?.kind !== "insert") return;
    const ins = inserts[next.index];
    if (!ins) return;
    const now = clipTimeNow();
    if (now < ins.at || now > ins.at + ins.duration) seekClipTime(ins.at + 0.15, false);
  };

  // Холст — один немой кусок: вкладки «Кусок» у него нет.
  const inspTabs: InspTabDef<InspTab>[] = [
    { key: "clip", label: "Клип", hint: "Формат, быстрые действия, лук, раскладка, переходы, обложка, баннер, паузы; «Ещё…» — умный кадр, границы, экспорт" },
    { key: "layers", label: "Слои", count: layers.length + inserts.length, hint: "Выбранный слой, все слои клипа, мемы-вставки, наезды камеры" },
    ...(isCanvas ? [] : [{ key: "piece" as const, label: "Кусок", hint: "Скорость, цвет и фокус кадра выбранного куска" }]),
    { key: "audio", label: "Звук", hint: "Музыка, тишина вместо звука, громкость и звуковые дорожки видео" },
    { key: "subs", label: "Субтитры", hint: "Субтитры: стиль, положение, распознавание, правка строк, голоса, перевод" },
    { key: "check", label: "Проверка", hint: "Проверка перед сдачей: зоны кнопок площадок, читаемость титров, вспышки, луп" },
  ];
  const inspActive: InspTab = isCanvas && inspTab === "piece" ? "clip" : inspTab;
  const pickInspTab = (key: InspTab) => {
    setInspTab(key);
    writePref("vvf.ed.inspTab", key);
    if (inspScrollRef.current) inspScrollRef.current.scrollTop = 0;
    // «Кусок» без выбранного куска пуст — берём первый, чтобы сразу было что править.
    if (key === "piece" && !selectedSegment && activePlan?.segments.length) {
      setSelectedSeg(activePlan.segments[0].id);
      setTlSelected({ kind: "piece", index: 0 });
    }
  };
  const selectedPieceIndex = selectedSegment && activePlan ? activePlan.segments.findIndex((s) => s.id === selectedSegment.id) : -1;

  subPosPctRef.current = subPosPct;
  // «По умолчанию» на панели = первый стиль в списке, как и на сервере.
  subStyleRef.current = (subs.data?.find((s) => s.id === subId) ?? subs.data?.[0] ?? null) as SubtitleStyle | null;
  // Субтитры для превью тянем здесь, а не из панели субтитров: иначе караоке
  // пропадало бы, когда панель свёрнута или не помещается в узкое окно.
  bannerGeomRef.current = bannerOn && bannerId ? { height: bannerHeightPct, pos: bannerPosPct } : null;
  subLinesRef.current = planSubs.data?.lines ?? subLinesRef.current;
  subWordsRef.current = (planSubs.data?.words ?? subWordsRef.current) as SubtitleWord[];
  const visiblePlans = planGroups.flatMap(([, g]) => g);
  // Editor rail: the starred moments (plus the open one if it isn't starred);
  // with nothing starred yet, fall back to what the triage filters show.
  const favPlans = plans.filter((p) => p.favorite && !p.hidden);
  const railPlans = favPlans.length
    ? activePlan && !favPlans.some((p) => p.id === activePlan.id)
      ? [activePlan, ...favPlans]
      : favPlans
    : visiblePlans;
  const planDur = (p: (typeof plans)[number]) =>
    p.segments.reduce((s, seg) => s + Math.max(0, seg.end_sec - seg.start_sec), 0);
  // Cache-busted by the first segment's timing + focus so edits refresh the cover.
  const planThumbUrl = (p: (typeof plans)[number]) => {
    const s = p.segments[0];
    const fx = s?.focus?.[0]?.x ?? 0.5;
    return `/media/clip-plans/${p.id}/thumb?v=${s ? `${s.start_sec.toFixed(1)}_${s.end_sec.toFixed(1)}` : 0}_${fx.toFixed(2)}`;
  };
  // Последний готовый рендер момента: показываем его, когда записи стрима нет.
  const planRender = (planId: number) =>
    (query.data?.clips ?? [])
      .filter((c) => c.clip_plan_id === planId && c.status === "succeeded")
      .reduce<Clip | null>((best, c) => (!best || c.id > best.id ? c : best), null);
  // Холст: вместо зелёного кадра основы — фон этого клипа.
  const planThumb = (p: (typeof plans)[number]) =>
    isCanvas ? (
      <BackdropStrip className="plan-canvas-thumb" bg={normalizeBackdrop(p.render_settings?.backdrop)} width={90} height={160} />
    ) : sourceMissing ? (
      // Записи нет — кадр берём из готового рендера; рендера нет — без запроса в никуда.
      planRender(p.id) ? (
        <img src={`/media/clips/${planRender(p.id)!.id}/thumb?v=${encodeURIComponent(planRender(p.id)!.updated_at)}`} alt="" loading="lazy" />
      ) : (
        <span className="plan-thumb-none">черновик</span>
      )
    ) : (
      <img src={planThumbUrl(p)} alt="" loading="lazy" />
    );
  const playPlan = (planId: number) => {
    const p = plans.find((x) => x.id === planId);
    if (!p) return;
    setActivePlanId(p.id);
    playRanges(p.segments.map((s) => ({ start: s.start_sec, end: s.end_sec })));
  };
  // Latest keyboard handler (reassigned every render, after all early returns).
  keyHandlerRef.current = (e: KeyboardEvent) => {
    const key = e.key.toLowerCase();
    const target = view === "editor" ? activePlan : plans.find((p) => p.id === hoverPlanRef.current) ?? null;
    if (view === "editor" && e.key === "Escape") {
      backToMoments();
    } else if ((key === "f" || key === "а") && target) {
      setPlanFlag(target.id, { favorite: !target.favorite });
    } else if ((key === "x" || key === "ч") && target && view === "triage") {
      setPlanFlag(target.id, { hidden: !target.hidden });
    } else if ((key === "e" || key === "у") && target && view === "triage") {
      openEditor(target.id);
    } else if ((key === "s" || key === "ы") && view === "editor") {
      // S — разрезать кусок по курсору, Del — убрать выбранный блок:
      // те же действия, что кнопками над таймлайном.
      splitAtClip(clipTimeNow());
    } else if ((e.key === "Delete" || e.key === "Backspace") && view === "editor" && tlSelected) {
      deleteSelected();
    } else if (view === "editor" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      // ←/→ — кадр, Shift — секунда: точная подгонка титра или склейки на паузе.
      e.preventDefault();
      const dir = e.key === "ArrowLeft" ? -1 : 1;
      stepClip(dir * (e.shiftKey ? 1 : FRAME_SEC));
    } else if (
      view === "editor" &&
      e.key === " " &&
      // На кнопке пробел и так её нажимает, а у <video> свой плей — не дублируем.
      !(e.target instanceof HTMLButtonElement || e.target instanceof HTMLVideoElement)
    ) {
      e.preventDefault();
      togglePlay();
    } else if (view === "editor" && e.key === "Home") {
      e.preventDefault();
      stepClip(-1e6);
    }
  };

  return {
    ...state,
    source, isCanvas, selectedSegment, toggleChosen, clipTimeNow, seekClipTime, stepClip, openWatch, insertsPreview,
    clipTimeExact, selectedLayerId, captionWords, onInsertPointerDown, onInsertPointerMove, onOverlayDoubleClick,
    commitInlineEdit, onInsertPointerUp, splitAtClip, deleteSelected, clipDuration, addLayers, addAtCursor,
    dropLayersAt, selectOnTimeline, inspTabs, inspActive, pickInspTab, selectedPieceIndex, visiblePlans, railPlans,
    planDur, planRender, planThumb, playPlan,
  };
}

export type DraftsEditorSession = ReturnType<typeof buildDraftsEditorSession>;
