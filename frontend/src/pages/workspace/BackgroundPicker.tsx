/**
 * Выбор фона (за персонажем на зелёнке, под видео meme, фон бренда): стиль с живым
 * превью, свои цвета или «в цветах ролика», скорость и яркость. Превью рисует тот же
 * движок, что и рендер (motion/backgrounds.ts), — что видно в плитке, то и будет в ролике.
 */
import { useEffect, useRef } from "react";
import {
  BACKGROUND_INFO,
  BACKGROUND_KINDS,
  backgroundPalette,
  isAnimatedBackground,
  normalizeBackground,
  paintBackground,
  type Background,
  type BackgroundKind,
} from "@/motion";

type Props = {
  value: Background;
  onChange: (next: Background) => void;
  /** Акцент клипа (clipAccent) — им красится фон без своих цветов. */
  accent?: string | null;
  /** Чьи цвета у фона без своих: «ролика» (по умолчанию) или «бренда». */
  autoLabel?: string;
  /** Цвета «авто», если они известны заранее (бренд) — для превью. */
  autoColors?: string[];
};

const THUMB_W = 54;
const THUMB_H = 96;
const createCanvas = (w: number, h: number) => {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
};

/** Плитки стилей анимируются одним циклом кадров; при reduced-motion — стоп-кадр. */
function useThumbs(refs: React.MutableRefObject<(HTMLCanvasElement | null)[]>, value: Background, accent: string | null | undefined, auto: string[] | undefined) {
  const state = useRef({ value, accent, auto });
  state.current = { value, accent, auto };
  useEffect(() => {
    let raf = 0;
    const still = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const draw = (now: number) => {
      const { value: v, accent: a, auto } = state.current;
      BACKGROUND_KINDS.forEach((kind, i) => {
        const cv = refs.current[i];
        const ctx = cv?.getContext("2d");
        if (!cv || !ctx) return;
        paintBackground(ctx, thumbBackground(v, kind, auto), still ? 2 : now / 1000, cv.width, cv.height, { accent: a, createCanvas });
      });
      if (!still) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [refs]);
}

/** Как будет выглядеть стиль `kind` с текущими цветами/скоростью. */
function thumbBackground(v: Background, kind: BackgroundKind, auto?: string[]): Background {
  const colors = v.colors.length ? v.colors : auto ?? [];
  if (kind === "solid") return normalizeBackground({ kind, colors: [colors[0] ?? "#0b0d14"] });
  if (kind === "gradient") return normalizeBackground({ kind, colors: colors.length >= 2 ? colors.slice(0, 2) : [colors[0] ?? "#0b0d14", "#1a1f33"] });
  return { ...v, kind, colors: isAnimatedBackground(v) ? colors : auto ?? [] };
}

export function BackgroundPicker({ value, onChange, accent, autoLabel = "в цветах ролика", autoColors }: Props) {
  const refs = useRef<(HTMLCanvasElement | null)[]>([]);
  useThumbs(refs, value, accent, autoColors);
  const animated = isAnimatedBackground(value);
  const own = value.colors.length > 0;
  const shown = animated ? backgroundPalette({ ...value, colors: own ? value.colors : autoColors ?? [] }, accent) : value.colors;
  const set = (patch: Partial<Background>) => onChange(normalizeBackground({ ...value, ...patch }));

  const pick = (kind: BackgroundKind) => {
    if (kind === value.kind) return;
    if (kind === "solid" || kind === "gradient") {
      // Статичный фон — из тех цветов, что видны сейчас (основа сверху, второй снизу).
      const base = shown[0] ?? "#0b0d14";
      onChange(normalizeBackground({ ...value, kind, colors: kind === "solid" ? [base] : [base, shown[2] ?? shown[1] ?? "#1a1f33"] }));
    } else {
      onChange(normalizeBackground({ ...value, kind, colors: animated ? value.colors : [] }));
    }
  };

  const setColor = (i: number, c: string) => {
    const next = [...shown];
    next[i] = c;
    set({ colors: next });
  };

  return (
    <div className="bg-picker">
      <div className="bg-kinds" role="radiogroup" aria-label="Стиль фона">
        {BACKGROUND_KINDS.map((kind, i) => (
          <button
            key={kind}
            type="button"
            role="radio"
            aria-checked={value.kind === kind}
            className={`bg-kind${value.kind === kind ? " on" : ""}${isAnimatedBackground({ ...value, kind }) ? "" : " static"}`}
            title={BACKGROUND_INFO[kind].hint}
            onClick={() => pick(kind)}
          >
            <canvas
              ref={(el) => {
                refs.current[i] = el;
              }}
              width={THUMB_W}
              height={THUMB_H}
              aria-hidden="true"
            />
            <span>{BACKGROUND_INFO[kind].label}</span>
          </button>
        ))}
      </div>
      {!animated ? <div className="lay-note warn">Статичный фон — только если просили. По умолчанию фон живой.</div> : null}

      <div className="lay-row bg-colors">
        {animated ? (
          <label className="check" title="Без своих цветов фон берёт тёмную основу и акцент субтитров">
            <input type="checkbox" checked={!own} onChange={(e) => set({ colors: e.target.checked ? [] : shown })} />
            <span>{autoLabel}</span>
          </label>
        ) : null}
        <span className="lay-grow" />
        {(animated ? shown.slice(0, 3) : value.colors).map((c, i) => (
          <input
            key={i}
            type="color"
            className="lay-color"
            value={c}
            disabled={animated && !own}
            title={animated ? ["Основа (тёмная)", "Акцент", "Второй акцент"][i] : i === 0 ? "Цвет сверху" : "Цвет снизу"}
            onChange={(e) => setColor(i, e.target.value)}
          />
        ))}
      </div>

      {animated ? (
        <div className="lay-row">
          <label className="field range lay-grow">
            <span>Скорость · {value.speed.toFixed(2).replace(/\.?0+$/, "")}×</span>
            <input type="range" min={10} max={300} step={5} value={Math.round(value.speed * 100)} onChange={(e) => set({ speed: Number(e.target.value) / 100 })} />
          </label>
          <label className="field range lay-grow">
            <span>Свечение · {Math.round(value.intensity * 100)}%</span>
            <input type="range" min={0} max={100} value={Math.round(value.intensity * 100)} onChange={(e) => set({ intensity: Number(e.target.value) / 100 })} />
          </label>
        </div>
      ) : null}
    </div>
  );
}
