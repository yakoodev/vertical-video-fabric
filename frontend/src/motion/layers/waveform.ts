/**
 * Волна голоса (`waveform`): столбики, гладкая волна или точки, которые двигаются с настоящей
 * громкостью звука. Огибающую считает сервер (app/audio_react.py, как у `react`): своего звукового
 * слоя или файла — `comp.levels["wave:<id слоя>"]`, голоса/музыки/всего звука клипа —
 * `comp.levels.voice|music|mix`. Нет звука (галерея, пустой холст) — образец «речи» по зерну:
 * слоги ~4 Гц, фразы с паузами.
 *
 * `motion: center` — «сейчас» в середине, громкость расходится к краям (как у голосового
 * помощника); `scroll` — лента истории, «сейчас» справа. Чистая функция огибающей и времени.
 */
import { clamp01 } from "../easing";
import { noise1 } from "../keys";
import { levelAt, waveLevelKey } from "../levels";
import type { Ctx2D, LevelTrack, WaveformLayer, WaveformProps } from "../types";
import { AD_ACCENT, mixHex } from "./adkit";
import { clearShadow, rgba, roundRectPath, type FrameInfo, type LayerRenderer } from "./common";
import { deviceScale } from "./counter";

export const WAVE_STYLES = ["bars", "wave", "dots"] as const;
export const WAVE_SOURCES = ["auto", "voice", "music", "mix", "fake"] as const;

/** Огибающая слоя: свой звук → огибающая клипа по `source` → null (образец по зерну). */
export function waveTrack(layer: WaveformLayer, frame: Pick<FrameInfo, "levels">): LevelTrack | null {
  const p = layer.props;
  const src = p.source ?? "auto";
  if (src === "fake") return null;
  if (src === "voice" || src === "music" || src === "mix") return frame.levels?.[src] ?? null;
  return frame.levels?.[waveLevelKey(layer.id)] ?? null;
}

/** Образец речи 0..1 в момент `t`: слоги и фразы из шума по зерну (без Math.random). */
export function fakeVoice(seed: number, t: number): number {
  const phrase = clamp01((noise1(seed + 11, t * 0.55) * 0.5 + 0.5 - 0.2) * 2.4);
  const syllable = 0.5 + 0.5 * Math.sin(Math.PI * 2 * 4.1 * t + 2.2 * noise1(seed, t * 1.4));
  const grain = 0.78 + 0.22 * noise1(seed + 3, t * 7);
  return clamp01(phrase * (0.28 + 0.72 * Math.pow(syllable, 1.4)) * grain);
}

/** Высоты столбиков 0..1 в момент `t` (клиповое время). */
export function waveHeights(layer: WaveformLayer, frame: Pick<FrameInfo, "levels">, t: number): number[] {
  const p = layer.props;
  const n = Math.max(4, Math.min(128, Math.round(p.bars ?? 36)));
  const span = Math.max(0.2, p.span ?? 1.6);
  const sens = Math.max(0.3, Math.min(3, p.sensitivity ?? 1.2));
  const min = clamp01(p.minLevel ?? 0.06);
  const smooth = clamp01(p.smooth ?? 0.12);
  const seed = Math.round(p.seed ?? 5);
  const track = waveTrack(layer, frame);
  const level = (tt: number) => (track ? levelAt(track, tt, smooth) : fakeVoice(seed, tt));
  const center = (p.motion ?? "center") === "center";
  const mid = (n - 1) / 2;
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const d = center ? Math.abs(i - mid) / Math.max(1, mid) : (n - 1 - i) / Math.max(1, n - 1);
    const lag = center ? d * span * 0.5 : d * span;
    // Узор столбиков: середина выше, соседи неровные — живая волна, а не гребёнка.
    const shape = center ? 1 - 0.38 * d * d : 1;
    const texture = 0.8 + 0.2 * Math.sin(i * 1.71 + seed * 0.37);
    const v = clamp01(level(t - lag) * sens) * shape * texture;
    out.push(Math.max(min, v));
  }
  return out;
}

function paint(ctx: Ctx2D, p: WaveformProps, h: number) {
  const color = p.color || AD_ACCENT;
  if (Array.isArray(p.gradient) && p.gradient.length === 2) {
    const g = ctx.createLinearGradient(0, -h / 2, 0, h / 2);
    g.addColorStop(0, rgba(p.gradient[0]));
    g.addColorStop(0.5, rgba(p.gradient[1]));
    g.addColorStop(1, rgba(p.gradient[0]));
    return g;
  }
  const g = ctx.createLinearGradient(0, -h / 2, 0, h / 2);
  g.addColorStop(0, rgba(mixHex(color, "#ffffff", 0.35)));
  g.addColorStop(0.5, rgba(color));
  g.addColorStop(1, rgba(mixHex(color, "#ffffff", 0.35)));
  return g;
}

export const waveformRenderer: LayerRenderer<WaveformLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const p = layer.props;
    return { w: Math.max(40, p.w ?? 820), h: Math.max(20, p.h ?? 220) };
  },
  draw(ctx, layer, _st, _env, frame) {
    const p = layer.props;
    const W = Math.max(40, p.w ?? 820);
    const H = Math.max(20, p.h ?? 220);
    const hs = waveHeights(layer, frame, frame.t);
    const n = hs.length;
    const slot = W / n;
    const bw = Math.max(2, slot * Math.max(0.1, Math.min(1, p.barWidth ?? 0.56)));
    const style = p.style ?? "bars";
    const fill = paint(ctx, p, H);
    const glowColor = Array.isArray(p.gradient) && p.gradient.length === 2 ? p.gradient[1] : p.color || AD_ACCENT;
    // Свечение — одной тенью на весь путь (а не на каждый столбик): дёшево и ровно.
    if (p.glow !== false) {
      ctx.shadowColor = rgba(glowColor, 0.75);
      ctx.shadowBlur = H * 0.16 * deviceScale(ctx);
      ctx.shadowOffsetX = 0;
      ctx.shadowOffsetY = 0;
    }
    ctx.fillStyle = fill;
    ctx.beginPath();
    if (style === "wave") {
      // Гладкая симметричная волна: верхний край — сплайн по вершинам, нижний — его зеркало.
      const top: [number, number][] = [[-W / 2, 0], ...hs.map((v, i) => [-W / 2 + slot * (i + 0.5), (-v * H) / 2] as [number, number]), [W / 2, 0]];
      const edge = (pts: [number, number][], first: boolean) => {
        if (first) ctx.moveTo(pts[0][0], pts[0][1]);
        else ctx.lineTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length - 1; i++) {
          const [x, y] = pts[i];
          const [nx, ny] = pts[i + 1];
          ctx.quadraticCurveTo(x, y, (x + nx) / 2, (y + ny) / 2);
        }
        ctx.lineTo(pts[pts.length - 1][0], pts[pts.length - 1][1]);
      };
      edge(top, true);
      edge(top.map(([x, y]) => [x, -y] as [number, number]).reverse(), false);
      ctx.closePath();
      ctx.fill();
    } else if (style === "dots") {
      // Столбики из точек: сколько точек горит — столько громкости.
      const rows = Math.max(3, Math.floor(H / (bw * 1.5)));
      for (let i = 0; i < n; i++) {
        const lit = Math.max(1, Math.round(hs[i] * rows));
        const x = -W / 2 + slot * (i + 0.5);
        for (let k = 0; k < lit; k++) {
          const y = (k - (lit - 1) / 2) * bw * 1.5;
          ctx.moveTo(x + bw / 2, y);
          ctx.arc(x, y, bw / 2, 0, Math.PI * 2);
        }
      }
      ctx.fill();
    } else {
      for (let i = 0; i < n; i++) {
        const h = Math.max(bw, hs[i] * H);
        roundRectPath(ctx, -W / 2 + slot * i + (slot - bw) / 2, -h / 2, bw, h, bw / 2);
      }
      ctx.fill();
    }
    clearShadow(ctx);
  },
};
