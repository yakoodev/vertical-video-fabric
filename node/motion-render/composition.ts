// Компоновщик: чтение и проверка композиции из JSON.
import { readFileSync } from "node:fs";
import {
  normalizeBackground,
  readEndScreen,
  shutterValue,
  type BackgroundZone,
  type Composition,
  type PixelRect,
  type PinnedRect,
  type FreezeSpan,
  type VoiceTrack,
  type LevelTrack,
} from "../../frontend/src/motion/index";

/* ---------- композиция ---------- */

export function readComp(path: string): Composition {
  const raw = JSON.parse(readFileSync(path, "utf8")) as Partial<Composition>;
  const width = Math.round(Number(raw.width) || 1080);
  const height = Math.round(Number(raw.height) || 1920);
  const fps = Number(raw.fps) || 30;
  const duration = Number(raw.duration) || 0;
  if (width < 16 || height < 16 || width > 8192 || height > 8192) throw new Error(`странный размер кадра ${width}×${height}`);
  // x264 с yuv420p требует чётные стороны.
  return {
    width: width - (width % 2),
    height: height - (height % 2),
    fps: Math.max(1, Math.min(120, fps)),
    duration: Math.max(0, duration),
    layers: Array.isArray(raw.layers) ? raw.layers : [],
    camera: Array.isArray(raw.camera) ? raw.camera : [],
    // Слова речи для слоёв captions: только целые записи {t0, t1, w} (+ s — кто говорит), по времени.
    words: Array.isArray(raw.words)
      ? raw.words
          .filter((w) => w && Number.isFinite(Number(w.t0)) && Number.isFinite(Number(w.t1)) && typeof w.w === "string")
          .map((w) => ({ t0: Number(w.t0), t1: Number(w.t1), w: w.w, ...(typeof w.s === "string" && w.s ? { s: w.s } : {}) }))
          // Слова оригинала перевода (s: "orig", app/translation.py) — после остальных: номера
          // highlightWords русских слов те же, что считал сервер.
          .sort((a, b) => Number(a.s === "orig") - Number(b.s === "orig") || a.t0 - b.t0)
      : [],
    // Сообщения чата для слоёв chat: время — число, остальное движок проверит сам.
    chat: Array.isArray(raw.chat)
      ? raw.chat.filter((m) => m && Number.isFinite(Number(m.t))).sort((a, b) => Number(a.t) - Number(b.t))
      : [],
    // Состояния голоса PNGtuber по id слоя (app/pngtuber.py): только целые записи.
    ...readVoice(raw),
    // Огибающие громкости для react слоёв (app/audio_react.py).
    ...readLevels(raw),
    // Опорный кадр формата (px слоёв): нет — движок берёт 1080×1920, как всегда.
    ...readRef(raw),
    ...readKey(raw),
    ...readRegions(raw, width - (width % 2), height - (height % 2)),
    ...readBaseHeight(raw, height - (height % 2)),
    ...readFreezes(raw),
    ...readZones(raw, width - (width % 2), height - (height % 2)),
    // Концовка-баннер: размытие основы под слоями с comp.endScreen.start (frontend/src/motion/endscreen.ts).
    ...(readEndScreen(raw.endScreen) ? { endScreen: readEndScreen(raw.endScreen)! } : {}),
    // Motion blur клипа по умолчанию (render.motion_blur): слоям и камере без своего затвора.
    ...(shutterValue(raw.motionBlur) ? { motionBlur: shutterValue(raw.motionBlur)! } : {}),
  };
}

/** comp.voice: {id слоя: {t0, fps, states "0012…", levels?}} — мусорные записи отбрасываются. */
function readVoice(raw: Partial<Composition>): Pick<Composition, "voice"> {
  const v = raw.voice;
  if (!v || typeof v !== "object") return {};
  const out: Record<string, VoiceTrack> = {};
  for (const [id, tr] of Object.entries(v as Record<string, Partial<VoiceTrack>>)) {
    if (!tr || typeof tr.states !== "string" || !/^[0-2]*$/.test(tr.states)) continue;
    const fps = Number(tr.fps);
    out[id] = {
      t0: Number(tr.t0) || 0,
      fps: Number.isFinite(fps) && fps > 0 ? fps : 30,
      states: tr.states,
      ...(Array.isArray(tr.levels) ? { levels: tr.levels.map((x) => Number(x) || 0) } : {}),
    };
  }
  return Object.keys(out).length ? { voice: out } : {};
}

/** comp.levels: {voice|music|mix: {t0, fps, v [0..100]}} (app/audio_react.py) — мусор отбрасывается. */
function readLevels(raw: Partial<Composition>): Pick<Composition, "levels"> {
  const v = raw.levels;
  if (!v || typeof v !== "object") return {};
  const out: NonNullable<Composition["levels"]> = {};
  for (const source of ["voice", "music", "mix"] as const) {
    const tr = (v as Record<string, Partial<LevelTrack>>)[source];
    if (!tr || !Array.isArray(tr.v)) continue;
    const fps = Number(tr.fps);
    out[source] = {
      t0: Number(tr.t0) || 0,
      fps: Number.isFinite(fps) && fps > 0 ? fps : 30,
      v: tr.v.map((x) => Math.max(0, Math.min(100, Number(x) || 0))),
    };
  }
  return Object.keys(out).length ? { levels: out } : {};
}

/** Опорный кадр формата (app/formats.py): оба числа в 16..8192 — иначе не задан. */
function readRef(raw: Partial<Composition>): Pick<Composition, "refWidth" | "refHeight"> {
  const w = Number(raw.refWidth);
  const h = Number(raw.refHeight);
  const ok = (v: number) => Number.isFinite(v) && v >= 16 && v <= 8192;
  return ok(w) && ok(h) ? { refWidth: w, refHeight: h } : {};
}

/** Зоны анимированного фона основы (meme, кусок «шире окна»): целые, по времени. */
function readZones(raw: Partial<Composition>, W: number, H: number): Pick<Composition, "backgrounds"> {
  if (!Array.isArray(raw.backgrounds)) return {};
  const zones: BackgroundZone[] = [];
  for (const z of raw.backgrounds.slice(0, 200)) {
    if (!z || typeof z !== "object") continue;
    const t0 = Number(z.t0);
    const t1 = Number(z.t1);
    if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 <= t0) continue;
    zones.push({ t0, t1, bg: normalizeBackground(z.bg), hole: readRect(z.hole, W, H) });
  }
  zones.sort((a, b) => a.t0 - b.t0);
  return zones.length ? { backgrounds: zones } : {};
}

/** Прямоугольник в пикселях кадра, обрезанный по кадру; мусор — null. */
function readRect(raw: unknown, W: number, H: number): PixelRect | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const n = (v: unknown) => (Number.isFinite(Number(v)) ? Math.round(Number(v)) : NaN);
  const x = Math.max(0, n(r.x));
  const y = Math.max(0, n(r.y));
  const w = Math.min(W - x, n(r.w));
  const h = Math.min(H - y, n(r.h));
  return [x, y, w, h].every(Number.isFinite) && w > 0 && h > 0 ? { x, y, w, h } : null;
}

/** Раскладка стрима: где камера двигает кадр (окно игры) и что стоит на месте (окошко вебки). */
function readRegions(raw: Partial<Composition>, W: number, H: number): Pick<Composition, "cameraBox" | "pinned"> {
  const box = readRect(raw.cameraBox, W, H);
  const BH = readBaseHeight(raw, H).baseHeight ?? H;
  const pinned: PinnedRect[] = [];
  for (const p of Array.isArray(raw.pinned) ? raw.pinned.slice(0, 8) : []) {
    const rect = readRect(p, W, H);
    if (!rect) continue;
    // Окошко в полосе под кадром: строка sy, целиком внутри основы.
    const sy = Math.round(Number(p.sy));
    pinned.push(BH > H && Number.isFinite(sy) && sy >= H && sy + rect.h <= BH ? { ...rect, sy } : rect);
  }
  return { ...(box ? { cameraBox: box } : {}), ...(pinned.length ? { pinned } : {}) };
}

/** comp.freezes: стоп-кадры по времени, мусорные записи отбрасываются (не больше 8). */
function readFreezes(raw: Partial<Composition>): Pick<Composition, "freezes"> {
  if (!Array.isArray(raw.freezes)) return {};
  const out: FreezeSpan[] = [];
  for (const f of raw.freezes) {
    const at = Number(f?.at);
    const d = Number(f?.d);
    if (!Number.isFinite(at) || at < 0 || !Number.isFinite(d) || d <= 0) continue;
    out.push({ at, d: Math.min(10, d), insert: !!f.insert, hold: !!f.hold });
  }
  out.sort((a, b) => a.at - b.at);
  return out.length ? { freezes: out.slice(0, 8) } : {};
}

/**
 * Время готового ролика (основа со стоп-кадрами) → время слоёв, камеры и слов — как
 * app/freeze.motion_time: на стоп-кадре с `hold` время стоит, после вставки идёт с вычетом её
 * длины; у стоп-кадра без `hold` слои уже сдвинуты сервисом — их время идёт дальше.
 */
export function motionTime(freezes: FreezeSpan[] | undefined, u: number): number {
  if (!freezes?.length) return u;
  let offAll = 0;
  let offCont = 0;
  for (const f of freezes) {
    const atPost = f.at + offAll;
    if (u < atPost) break;
    if (u < atPost + f.d) {
      if (f.hold) return f.at + offCont;
      if (f.insert) return f.at + offCont + (u - atPost);
      return u - offAll + offCont;
    }
    if (f.insert) {
      offAll += f.d;
      if (!f.hold) offCont += f.d;
    }
  }
  return u - offAll + offCont;
}

/** Высота основы с полосой окошка под кадром (app/render.pin_band): чётная, выше кадра, не больше двух. */
function readBaseHeight(raw: Partial<Composition>, H: number): Pick<Composition, "baseHeight"> {
  const bh = Math.round(Number(raw.baseHeight));
  return Number.isFinite(bh) && bh > H && bh <= 2 * H && bh % 2 === 0 ? { baseHeight: bh } : {};
}

/** Хромакей и фон за основой. Лук идёт прямо в граф ffmpeg — только безопасные символы. */
function readKey(raw: Partial<Composition>): Pick<Composition, "key" | "background"> {
  const k = raw.key;
  if (!k || typeof k.color !== "string" || !/^0x[0-9a-f]{6}$/i.test(k.color)) return {};
  const clamp = (v: unknown, def: number, lo: number, hi: number) => {
    const n = Number(v);
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : def;
  };
  const grade = typeof k.grade === "string" && /^[a-z0-9=:.,_-]*$/i.test(k.grade) ? k.grade : "";
  const bg = raw.background;
  return {
    key: {
      color: k.color,
      similarity: clamp(k.similarity, 0.25, 0.01, 0.6),
      blend: clamp(k.blend, 0.08, 0, 0.5),
      despill: k.despill === "blue" || k.despill === "none" ? k.despill : "green",
      // Мягкое подавление ореола: доля от полного (нет — despill ffmpeg как есть).
      ...(k.spill !== undefined && k.spill !== null && Number.isFinite(Number(k.spill))
        ? { spill: clamp(k.spill, 1, 0, 1) }
        : {}),
      grade,
    },
    // Новый {kind, colors, speed} и старый {color, color2} — app/backgrounds.py.
    background: normalizeBackground(bg),
  };
}
