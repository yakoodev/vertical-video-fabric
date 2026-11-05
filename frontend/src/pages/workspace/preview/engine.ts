/**
 * Движок «Смотреть монтаж»: играет план клипа (app/playback.py) от первого кадра до последнего.
 *
 * Два <video> одного файла: один играет, второй заранее стоит на начале следующего куска, поэтому
 * склейка — смена плеера, а не перемотка (и наплыв рисуется из двух настоящих кадров). Часы — сам
 * играющий <video>: звук исходника и картинка не расходятся; на стоп-кадрах и в конечном удержании
 * часы настенные. Кадр рисует тот же composeFrame, что телефон редактора.
 */
import { clipAccent, reframeBetween, type AssetSizes, type CameraKey, type Layer, type VideoLayer } from "@/motion";
import type { PlaybackPart, PlaybackPlan } from "@/api/playback";
import { motionApi } from "@/api/motion";
import { normalizeFit, normalizeLayout, type PieceFit } from "@/pages/workspace/layout";
import { normalizeBackdrop, type ChromaKey } from "@/pages/workspace/chromaKey";
import { browserEnv, holdFontsExcept, layerAssetSizes, loadMotionFonts, preloadLayerImages } from "@/pages/workspace/motion/runtime";
import { setPreviewSound } from "@/pages/workspace/motion/sound";
import { composeFrame, DT, drawBaseFrame, focusFeel, frameGeometry, smoothFocus, type ComposeInput, type FrameGeometry, type InsertPreview, type MotionView } from "./frame";
import { MontageAudio } from "./audio";
import { blendTransition } from "./transitions";
import { baseAt, crossedFreeze, finalOf, insertedTotal, motionTime, partIndexAt, srcOf } from "./timeline";

type Slot = {
  el: HTMLVideoElement;
  /** Какой кусок стоит в плеере (-1 — никакой). */
  part: number;
  /** Плеер стоит на нужной секунде и готов отдать кадр. */
  ready: boolean;
  wantSeek: number | null;
};

export type EngineState = {
  t: number;
  duration: number;
  playing: boolean;
  buffering: boolean;
  ended: boolean;
  firstFrame: boolean;
  error: string | null;
};

const SWITCH_EPS = 0.008;
const PAINT_IDLE_MS = 120;

function makeVideo(): HTMLVideoElement {
  const el = document.createElement("video");
  el.preload = "auto";
  el.playsInline = true;
  el.setAttribute("data-montage", "1");
  return el;
}

export class MontageEngine {
  plan: PlaybackPlan | null = null;
  t = 0;
  private base = 0;
  private active = 0;
  private outgoing: number | null = null;
  private playing = false;
  private buffering = false;
  private ended = false;
  private firstFrame = false;
  private error: string | null = null;
  private slots: Slot[] = [];
  private url = "";
  private cv: HTMLCanvasElement | null = null;
  private ov: HTMLCanvasElement | null = null;
  private raf = 0;
  private last = 0;
  private lastPaint = 0;
  private lastPreload = 0;
  private dirty = true;
  private audio: MontageAudio | null = null;
  private geometry: FrameGeometry | null = null;
  private paths: number[][] = [];
  private fits: (PieceFit | null)[] = [];
  private motion: MotionView = { layers: [], camera: [], selected: null };
  private inserts: InsertPreview[] = [];
  private insertEls = new Map<string, HTMLImageElement | HTMLVideoElement | HTMLAudioElement>();
  private banner: HTMLImageElement | null = null;
  private bannerUrl = "";
  private scratch: ComposeInput["scratch"] = {};
  private blendA: HTMLCanvasElement | null = null;
  private blendB: HTMLCanvasElement | null = null;
  private snapshot: HTMLCanvasElement | null = null;
  private volume = 1;
  private muted = false;
  private loop = false;
  private voiceKey = "";
  private levelsKey = "";
  private sizesTimer = 0;
  private destroyed = false;
  private rawLayers: { layers: Layer[]; camera: CameraKey[] } = { layers: [], camera: [] };
  private sizes: AssetSizes = {};
  private frameListeners = new Set<(t: number) => void>();
  private stateListeners = new Set<(s: EngineState) => void>();

  constructor() {
    this.slots = [this.makeSlot(), this.makeSlot()];
    setPreviewSound(() => this.playing && !this.muted && this.volume > 0);
    void loadMotionFonts();
    this.raf = requestAnimationFrame(this.loopFn);
  }

  // ------------------------------------------------------------- внешний интерфейс

  attach(cv: HTMLCanvasElement, ov: HTMLCanvasElement): void {
    this.cv = cv;
    this.ov = ov;
    this.dirty = true;
  }

  onFrame(fn: (t: number) => void): () => void {
    this.frameListeners.add(fn);
    return () => this.frameListeners.delete(fn);
  }

  onState(fn: (s: EngineState) => void): () => void {
    this.stateListeners.add(fn);
    fn(this.state());
    return () => this.stateListeners.delete(fn);
  }

  state(): EngineState {
    return {
      t: this.t,
      duration: this.plan?.duration ?? 0,
      playing: this.playing,
      buffering: this.buffering,
      ended: this.ended,
      firstFrame: this.firstFrame,
      error: this.error,
    };
  }

  /** Новый план: первый — загрузка; следующие (правка) — та же секунда, новый монтаж, без перезагрузки. */
  setPlan(plan: PlaybackPlan): void {
    const url = plan.media.url ?? "";
    const canPlay = plan.media.kind !== "render" && plan.media.kind !== "none" && Boolean(url);
    const first = !this.plan;
    this.plan = plan;
    this.geometry = plan.source.width && plan.source.height
      ? frameGeometry(plan.source.width, plan.source.height, plan.source.content_crop, normalizeLayout(plan.layout as never), plan.format)
      : null;
    const feel = focusFeel(plan.source.focus_preset);
    this.paths = plan.parts.map((p) => smoothFocus(p.focus ?? [], p.src_end - p.src_start, feel));
    this.fits = plan.parts.map((p) => (this.geometry?.landscape ? null : normalizeFit(p.fit)));
    this.rawLayers = { layers: plan.layers, camera: plan.camera };
    this.rebuildMotion();
    this.watchSizes();
    this.loadExtras(plan);
    this.buildInserts(plan);
    this.loadBanner(plan);
    this.audio?.preload(plan);
    if (canPlay && url !== this.url && (first || !this.playing)) {
      // Адрес основы сменился (собрался прокси) — на паузе пересаживаемся на него с той же секунды.
      this.url = url;
      for (const s of this.slots) {
        s.el.src = url;
        s.ready = false;
        s.part = -1;
      }
    }
    if (canPlay) this.seek(Math.min(this.t, plan.duration), this.playing);
    this.emit();
    this.dirty = true;
  }

  play(): void {
    if (!this.plan || this.plan.media.url == null) return;
    this.ensureAudio();
    if (this.ended || this.t >= this.plan.duration - 0.01) this.seek(0);
    this.ended = false;
    this.playing = true;
    this.last = performance.now();
    this.emit();
    this.dirty = true;
  }

  pause(): void {
    if (!this.playing) return;
    this.playing = false;
    this.audio?.stopAll();
    for (const s of this.slots) if (!s.el.paused) s.el.pause();
    this.setBuffering(false);
    this.emit();
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  setLoop(on: boolean): void {
    this.loop = on;
  }

  setOutput(volume: number, muted: boolean): void {
    this.volume = volume;
    this.muted = muted;
    this.audio?.setOutput(volume, muted);
    this.syncInsertMute();
  }

  /** Прыжок в секунду ролика: на паузе — показать кадр, при игре — продолжить оттуда. */
  seek(t: number, keepPlaying = this.playing): void {
    const plan = this.plan;
    if (!plan || !plan.parts.length) return;
    const clamped = Math.min(plan.duration, Math.max(0, t));
    this.t = clamped;
    this.ended = false;
    const st = baseAt(plan.freezes_raw, clamped);
    this.base = Math.min(st.base, plan.base_duration);
    this.active = partIndexAt(plan.parts, this.base);
    this.audio?.stopAll();
    this.outgoing = null;
    const prev = this.active > 0 ? plan.parts[this.active - 1] : null;
    if (prev && this.base < prev.end - 1e-3) this.outgoing = this.active - 1;
    for (const s of this.slots) {
      if (!s.el.paused) s.el.pause();
      if (s.part !== this.active && s.part !== this.outgoing) s.part = -1;
    }
    this.assign(this.active, srcOf(plan.parts[this.active], this.base));
    if (this.outgoing != null) this.assign(this.outgoing, srcOf(plan.parts[this.outgoing], this.base));
    this.playing = keepPlaying;
    this.last = performance.now();
    this.dirty = true;
    this.notifyFrame();
  }

  /** Для проверок: что звучит (RMS на выходе) и какой плеер показывает кадр. */
  debug(): { level: number; active: number; base: number; slots: { part: number; t: number; ready: boolean; paused: boolean; src: string }[]; ctx: string | null } {
    return {
      level: this.audio?.outputLevel() ?? 0,
      active: this.active,
      base: this.base,
      slots: this.slots.map((s) => ({ part: s.part, t: s.el.currentTime, ready: s.ready, paused: s.el.paused, src: s.el.currentSrc })),
      ctx: this.audio ? this.audio.ctx.state : null,
    };
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    window.clearInterval(this.sizesTimer);
    this.audio?.destroy();
    this.audio = null;
    for (const s of this.slots) {
      s.el.pause();
      s.el.removeAttribute("src");
      s.el.load();
    }
    for (const el of this.insertEls.values()) {
      if (el instanceof HTMLMediaElement) el.pause();
    }
    this.frameListeners.clear();
    this.stateListeners.clear();
    setPreviewSound(() => false);
    holdFontsExcept(null);
  }

  // ------------------------------------------------------------- плееры кусков

  private makeSlot(): Slot {
    const slot: Slot = { el: makeVideo(), part: -1, ready: false, wantSeek: null };
    const { el } = slot;
    el.addEventListener("seeked", () => {
      if (slot.wantSeek != null && Math.abs(el.currentTime - slot.wantSeek) > 0.04) {
        // Пока плеер искал, позицию успели сменить (протяжка по таймлайну) — ищем последнюю.
        const next = slot.wantSeek;
        slot.wantSeek = null;
        el.currentTime = next;
        return;
      }
      slot.wantSeek = null;
      slot.ready = el.readyState >= 2;
      this.dirty = true;
    });
    el.addEventListener("canplay", () => {
      if (!el.seeking && slot.wantSeek == null) slot.ready = true;
      this.dirty = true;
    });
    el.addEventListener("loadedmetadata", () => {
      if (slot.wantSeek != null) {
        const x = slot.wantSeek;
        slot.wantSeek = x;
        el.currentTime = x;
      }
    });
    el.addEventListener("error", () => {
      if (el.getAttribute("src")) this.error = "Видео не загрузилось: проверьте сеть или откройте заново";
      this.emit();
    });
    return slot;
  }

  private slotOf(part: number | null): Slot | null {
    if (part == null) return null;
    return this.slots.find((s) => s.part === part) ?? null;
  }

  /** Поставить кусок в плеер на секунду исходника `src`. */
  private assign(part: number, src: number): Slot | null {
    const slot =
      this.slotOf(part) ??
      this.slots.find((s) => s.part === -1) ??
      this.slots.find((s) => s.part !== this.active && s.part !== this.outgoing) ??
      null;
    if (!slot) return null;
    slot.part = part;
    this.seekSlot(slot, src);
    return slot;
  }

  private seekSlot(slot: Slot, src: number): void {
    const { el } = slot;
    if (!el.paused) el.pause();
    if (el.readyState < 1) {
      slot.ready = false;
      slot.wantSeek = src;
      return;
    }
    if (Math.abs(el.currentTime - src) < 0.002 && !el.seeking) {
      slot.ready = el.readyState >= 2;
      slot.wantSeek = null;
      return;
    }
    slot.ready = false;
    slot.wantSeek = src;
    if (!el.seeking) el.currentTime = src;
  }

  /** Следующий кусок — заранее: к склейке плеер уже стоит на его первом кадре. */
  private prepare(index: number): void {
    const plan = this.plan;
    if (!plan || index >= plan.parts.length || this.slotOf(index)) return;
    // На слабой сети сначала текущий кусок: следующий — когда этому уже есть что показать.
    if (!this.slotOf(this.active)?.ready) return;
    const part = plan.parts[index];
    this.assign(index, part.reverse ? part.src_end : part.src_start);
  }

  private startSlot(slot: Slot, part: PlaybackPart): void {
    const rate = Math.min(16, Math.max(0.0625, part.speed > 0 ? part.speed : 1));
    if (slot.el.playbackRate !== rate) slot.el.playbackRate = rate;
    // «tempo» — речь без смены тона (как atempo), «pitch» — тон едет со скоростью.
    slot.el.preservesPitch = part.speed_audio !== "pitch";
    if (slot.el.paused && !part.reverse) void slot.el.play().catch(() => undefined);
  }

  // ------------------------------------------------------------- время

  private loopFn = (now: number) => {
    if (this.destroyed) return;
    this.raf = requestAnimationFrame(this.loopFn);
    const dt = Math.min(0.25, Math.max(0, (now - this.last) / 1000));
    this.last = now;
    const plan = this.plan;
    if (!plan) return;
    if (this.playing && plan.parts.length) {
      this.advance(plan, dt);
      this.updateAudio(plan);
      this.notifyFrame();
    }
    if (now - this.lastPreload > 1000) {
      this.lastPreload = now;
      this.preloadVideoLayers();
    }
    if (plan.parts.length && this.outgoing == null) this.prepare(this.active + 1);
    if (this.playing || this.dirty || now - this.lastPaint > PAINT_IDLE_MS) {
      this.paint(plan);
      this.lastPaint = now;
      this.dirty = false;
    }
  };

  private notifyFrame(): void {
    for (const fn of this.frameListeners) fn(this.t);
  }

  private emit(): void {
    const s = this.state();
    for (const fn of this.stateListeners) fn(s);
  }

  private setBuffering(on: boolean): void {
    if (this.buffering !== on) {
      this.buffering = on;
      if (on) this.audio?.stopAll();
      this.emit();
    }
  }

  private finish(plan: PlaybackPlan): void {
    if (this.loop) {
      this.seek(0, true);
      return;
    }
    this.t = plan.duration;
    this.ended = true;
    this.pause();
    this.dirty = true;
    this.notifyFrame();
  }

  private advance(plan: PlaybackPlan, dt: number): void {
    const parts = plan.parts;
    const freezes = plan.freezes_raw;
    const st = baseAt(freezes, this.t);
    if (st.inserted) {
      // Вставленный стоп-кадр: основа стоит, идут только настенные часы (музыка и звуки играют).
      for (const s of this.slots) if (!s.el.paused) s.el.pause();
      this.t += dt;
      return;
    }
    if (this.base >= plan.base_duration - SWITCH_EPS) {
      // Основа кончилась: конечный стоп-кадр (на нём доигрывает музыка), потом конец.
      for (const s of this.slots) if (!s.el.paused) s.el.pause();
      this.base = plan.base_duration;
      this.t = Math.max(this.t, plan.base_duration + insertedTotal(freezes)) + dt;
      if (this.t >= plan.duration - 1e-3) this.finish(plan);
      return;
    }
    const index = this.active;
    const part = parts[index];
    const next = parts[index + 1];
    // Склейка: следующий кусок начинается по его часам, текущий (наплыв) доигрывает.
    if (next && this.base >= next.start - SWITCH_EPS) {
      const ns = this.slotOf(index + 1);
      if (!ns || !ns.ready) {
        // Следующий кусок ещё не встал (слабая сеть): держим кадр, а не обгоняем звук.
        this.prepare(index + 1);
        const cur = this.slotOf(index);
        if (cur && !cur.el.paused && part.overlap <= 0) cur.el.pause();
        this.setBuffering(true);
        return;
      }
      this.setBuffering(false);
      this.startSlot(ns, next);
      if (part.overlap > 0) {
        this.outgoing = index;
      } else {
        const old = this.slotOf(index);
        if (old) {
          old.el.pause();
          old.part = -1;
        }
        this.outgoing = null;
      }
      this.active = index + 1;
      return;
    }
    const slot = this.slotOf(index);
    if (!slot) {
      this.assign(index, srcOf(part, this.base));
      this.setBuffering(true);
      return;
    }
    if (part.reverse) {
      // Реверс: браузер назад не играет — настенные часы и шаги перемотки (без звука, как у рендера по умолчанию).
      this.base += dt;
      if (!slot.el.seeking) slot.el.currentTime = srcOf(part, this.base);
      this.setBuffering(false);
    } else {
      if (!slot.ready || slot.el.seeking || slot.el.readyState < 2) {
        this.setBuffering(true);
        return;
      }
      this.setBuffering(false);
      this.startSlot(slot, part);
      const measured = part.start + (slot.el.currentTime - part.src_start) / (part.speed > 0 ? part.speed : 1);
      // Часы — сам плеер; назад не идём (нужно на старте, пока плеер ещё не тронулся).
      this.base = Math.max(this.base, Math.min(measured, part.end + 0.1));
    }
    const prevBase = baseAt(freezes, this.t).base;
    const crossed = crossedFreeze(freezes, prevBase, this.base);
    if (crossed) {
      this.base = crossed.at;
      this.t = finalOf(freezes, crossed.at);
      return;
    }
    // Стоп-кадр «поверх»: снимок кадра на его начале, основа идёт дальше ради звука.
    this.t = finalOf(freezes, this.base);
    if (baseAt(freezes, this.t).overlaid != null && !this.snapshot) this.takeSnapshot(slot.el);
    else if (baseAt(freezes, this.t).overlaid == null) this.snapshot = null;
    if (this.outgoing != null && this.base >= parts[this.outgoing].end - SWITCH_EPS) {
      const old = this.slotOf(this.outgoing);
      if (old) {
        old.el.pause();
        old.part = -1;
      }
      this.outgoing = null;
    }
    if (next && this.outgoing == null) this.prepare(index + 1);
    if (!next && this.base >= part.end - SWITCH_EPS) this.base = plan.base_duration;
  }

  private takeSnapshot(el: HTMLVideoElement): void {
    const c = document.createElement("canvas");
    c.width = el.videoWidth;
    c.height = el.videoHeight;
    c.getContext("2d")?.drawImage(el, 0, 0);
    this.snapshot = c;
  }

  // ------------------------------------------------------------- звук

  private ensureAudio(): void {
    if (!this.audio) {
      try {
        this.audio = new MontageAudio();
      } catch {
        return; // браузер без WebAudio: играем только <video> со своим звуком
      }
      for (const s of this.slots) this.audio.connectVideo(s.el);
      this.audio.setOutput(this.volume, this.muted);
      if (this.plan) this.audio.preload(this.plan);
    }
    this.audio.resume();
  }

  private updateAudio(plan: PlaybackPlan): void {
    const audio = this.audio;
    if (!audio || this.buffering) return;
    const st = baseAt(plan.freezes_raw, this.t);
    const parts = plan.parts;
    const activePart = parts[this.active];
    const out = this.outgoing != null ? parts[this.outgoing] : null;
    let activeLevel = 1;
    let outLevel = 0;
    if (out && out.overlap > 0) {
      const p = Math.min(1, Math.max(0, (this.base - activePart.start) / out.overlap));
      if (plan.audio.transition_audio === "hard") {
        activeLevel = p >= 0.5 ? 1 : 0;
        outLevel = 1 - activeLevel;
      } else {
        activeLevel = p;
        outLevel = 1 - p;
      }
    }
    // Звук исходника во время J/L молчит у картинки: играет второй плеер.
    const jl = plan.audio.jl.some((j) => (j.shift > 0 ? this.t >= j.at && this.t < j.at + j.shift : this.t >= j.at + j.shift && this.t < j.at));
    const silentPart = (p: PlaybackPart) => p.reverse || p.speed_audio === "mute";
    for (const s of this.slots) {
      let level = 0;
      if (s.part === this.active) level = silentPart(activePart) || jl ? 0 : activeLevel;
      else if (s.part === this.outgoing && out) level = silentPart(out) ? 0 : outLevel;
      audio.setVideoLevel(s.el, level, activePart && parts[this.active - 1]?.jump ? 0.006 : 0.01);
    }
    let insertDuck = 1;
    for (const ins of plan.inserts) {
      if (ins.duck_volume != null && this.t >= ins.at && this.t < ins.at + ins.duration) insertDuck = Math.min(insertDuck, ins.duck_volume);
    }
    audio.tick(plan, this.t, { inserted: st.inserted, sideUrl: this.url || null, insertDuck });
  }

  // ------------------------------------------------------------- слои и файлы плана

  private rebuildMotion(): void {
    const plan = this.plan;
    if (!plan) return;
    const prev = this.motion;
    const fmtDiffers = plan.layers_format !== plan.format && (plan.layers.length > 0 || plan.camera.length > 0);
    const shown = fmtDiffers ? reframeBetween(this.rawLayers.layers, this.rawLayers.camera, plan.layers_format, plan.format, this.sizes) : this.rawLayers;
    this.motion = {
      layers: shown.layers,
      camera: shown.camera,
      selected: null,
      words: plan.words,
      chat: plan.chat,
      voice: prev.voice,
      levels: prev.levels,
      duration: plan.base_duration + insertedTotal(plan.freezes_raw),
      endScreen: plan.end_screen ?? null,
      motionBlur: plan.motion_blur ?? undefined,
      fps: plan.fps || 30,
    };
    preloadLayerImages(shown.layers);
    const families = new Set<string>();
    for (const layer of shown.layers) {
      const props = layer.props as { font?: unknown; fontFamily?: unknown };
      if (typeof props?.font === "string") families.add(props.font);
      if (typeof props?.fontFamily === "string") families.add(props.fontFamily);
    }
    if (!this.destroyed) holdFontsExcept(families);
  }

  /** Размеры картинок для переноса слоёв между форматами: как editor, пока файлы догружаются. */
  private watchSizes(): void {
    window.clearInterval(this.sizesTimer);
    const plan = this.plan;
    if (!plan || plan.layers_format === plan.format) return;
    let tries = 0;
    const check = () => {
      const { sizes, missing } = layerAssetSizes(plan.layers, {});
      if (JSON.stringify(sizes) !== JSON.stringify(this.sizes)) {
        this.sizes = sizes;
        this.rebuildMotion();
        this.dirty = true;
      }
      tries += 1;
      if (!missing || tries > 40) window.clearInterval(this.sizesTimer);
    };
    this.sizesTimer = window.setInterval(check, 400);
    check();
  }

  /** PNGtuber и реакция на звук: дорожки в шкале готового ролика считает сервер (как рендеру). */
  private loadExtras(plan: PlaybackPlan): void {
    const id = plan.clip_plan_id;
    const avatars = plan.layers.filter((l) => l.type === "pngtuber" && !l.hidden);
    const voiceKey = JSON.stringify(avatars.map((l) => [l.id, l.start, l.end, l.props]));
    if (avatars.length && voiceKey !== this.voiceKey) {
      this.voiceKey = voiceKey;
      void motionApi.voice(id, avatars, plan.fps || 30, "final").then((r) => {
        this.motion = { ...this.motion, voice: r.voice };
        this.dirty = true;
      }).catch(() => undefined);
    }
    const sources = [...new Set(plan.layers.filter((l) => l.react && !l.hidden).map((l) => l.react?.source ?? "voice"))].sort();
    const levelsKey = sources.join(",") + (plan.audio.music?.track_id ?? "");
    if (sources.length && levelsKey !== this.levelsKey) {
      this.levelsKey = levelsKey;
      void motionApi.levels(id, sources, plan.fps || 30, "final").then((r) => {
        this.motion = { ...this.motion, levels: r.levels };
        this.dirty = true;
      }).catch(() => undefined);
    }
  }

  private buildInserts(plan: PlaybackPlan): void {
    this.inserts = plan.inserts.map((ins) => {
      let el = this.insertEls.get(ins.url) ?? null;
      if (!el) {
        if (ins.kind === "video") {
          const v = document.createElement("video");
          v.src = ins.url;
          v.loop = true;
          v.playsInline = true;
          v.preload = "auto";
          el = v;
        } else if (ins.kind === "audio") {
          el = new Audio(ins.url);
        } else {
          const img = new Image();
          img.src = ins.url;
          el = img;
        }
        this.insertEls.set(ins.url, el);
      }
      return {
        at: ins.at,
        duration: ins.duration,
        mode: ins.mode,
        label: ins.label,
        volume: ins.volume,
        x: ins.x ?? 0.5,
        y: ins.y ?? 0.32,
        scale: ins.scale ?? 0.82,
        el,
      };
    });
    this.syncInsertMute();
  }

  private syncInsertMute(): void {
    for (const el of this.insertEls.values()) {
      if (el instanceof HTMLMediaElement) el.muted = this.muted || this.volume <= 0;
    }
  }

  private loadBanner(plan: PlaybackPlan): void {
    const url = plan.banner?.url ?? "";
    if (url === this.bannerUrl) return;
    this.bannerUrl = url;
    this.banner = null;
    if (!url) return;
    const img = new Image();
    img.src = url;
    img.onload = () => {
      this.banner = img;
      this.dirty = true;
    };
  }

  // ------------------------------------------------------------- кадр

  private focusAt(index: number, part: PlaybackPart, src: number): number {
    const path = this.paths[index];
    if (!path?.length) return 0.5;
    return path[Math.min(path.length - 1, Math.max(0, Math.round((src - part.src_start) / DT)))];
  }

  /** Видео-слои, что появятся в ближайшие секунды: файл догружается заранее, без чёрного кадра на входе. */
  private preloadVideoLayers(): void {
    const env = browserEnv(2, 2);
    let count = 0;
    for (const layer of this.motion.layers) {
      if (layer.type !== "video" || layer.hidden || count >= 4) continue;
      const until = layer.start - this.t;
      if (until > 12 || this.t > layer.end) continue;
      if (until > 0) env.getVideo?.(layer as VideoLayer)?.frame(Number(layer.props.from) || 0);
      count += 1;
    }
  }

  private paint(plan: PlaybackPlan): void {
    const cv = this.cv;
    const geometry = this.geometry;
    if (!cv || !geometry || !plan.parts.length) return;
    const st = baseAt(plan.freezes_raw, this.t);
    const base = Math.min(this.base, plan.base_duration);
    const index = this.active;
    const part = plan.parts[index];
    const slot = this.slotOf(index);
    const el = slot?.el;
    if (!el || el.readyState < 2 || !el.videoWidth) return;
    const src = srcOf(part, base);
    const end = plan.base_duration + insertedTotal(plan.freezes_raw);
    const frame = 1 / (plan.fps || 30);
    const liveT = Math.min(this.t, end - frame);
    const layerT = Math.min(motionTime(plan.freezes, this.t), end - frame);
    const prevIndex = this.outgoing;
    let baseOverride: ComposeInput["baseOverride"];
    if (prevIndex != null) {
      const prev = plan.parts[prevIndex];
      const prevEl = this.slotOf(prevIndex)?.el;
      if (prevEl && prevEl.readyState >= 2 && prev.overlap > 0) {
        const p = Math.min(1, Math.max(0, (base - part.start) / prev.overlap));
        baseOverride = (target, tw, th) => {
          const a = this.blendCanvas("a", tw, th);
          const b = this.blendCanvas("b", tw, th);
          const actx = a.getContext("2d");
          const bctx = b.getContext("2d");
          if (!actx || !bctx) return;
          const baseInput = (video: HTMLVideoElement, pi: number, pp: PlaybackPart) => {
            const s = srcOf(pp, base);
            return {
              video,
              vw: video.videoWidth,
              vh: video.videoHeight,
              geometry,
              cur: s,
              clipT: layerT,
              fit: this.fits[pi],
              fx: this.focusAt(pi, pp, s),
              accent: clipAccent(this.motion.layers),
            };
          };
          drawBaseFrame(actx, baseInput(prevEl, prevIndex, prev), tw, th);
          drawBaseFrame(bctx, baseInput(el, index, part), tw, th);
          blendTransition(target, a, b, tw, th, plan.transition.xfade, p);
        };
      }
    }
    const video = st.overlaid != null && this.snapshot ? this.snapshot : el;
    const vw = video === el ? el.videoWidth : (video as HTMLCanvasElement).width;
    const vh = video === el ? el.videoHeight : (video as HTMLCanvasElement).height;
    composeFrame({
      cv,
      ov: this.ov,
      video,
      vw,
      vh,
      geometry,
      cur: src,
      clipT: layerT,
      subT: liveT,
      fit: this.fits[index] ?? null,
      fx: this.focusAt(index, part, src),
      motion: this.motion,
      chromaKey: plan.source.chroma_key as ChromaKey | null,
      backdrop: normalizeBackdrop(plan.backdrop),
      mirror: plan.mirror,
      scratch: this.scratch,
      banner: plan.banner && this.banner ? { img: this.banner, height: plan.banner.height_pct, pos: plan.banner.pos_pct } : null,
      inserts: this.inserts,
      playing: this.playing && !this.buffering,
      editing: false,
      subLines: plan.subtitles.on ? plan.subtitles.lines : undefined,
      subWords: plan.subtitles.on ? plan.subtitles.words : undefined,
      subStyle: plan.subtitles.on ? plan.subtitles.profile : null,
      subPosPct: plan.subtitles.pos_pct,
      baseOverride,
    });
    if (!this.firstFrame) {
      this.firstFrame = true;
      this.emit();
    }
  }

  private blendCanvas(which: "a" | "b", w: number, h: number): HTMLCanvasElement {
    const key = which === "a" ? "blendA" : "blendB";
    let c = this[key];
    if (!c) {
      c = document.createElement("canvas");
      this[key] = c;
    }
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
    return c;
  }

  /** Цвет куска (CSS-фильтр основы): тот кусок, что сейчас на экране. */
  activePart(): PlaybackPart | null {
    return this.plan?.parts[this.active] ?? null;
  }
}
