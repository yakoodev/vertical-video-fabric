/**
 * Слой `ui` — плоский интерфейс на токенах темы (frontend/src/motion/flat): чип, заголовок, хук во весь
 * кадр, карточка с числом, сравнение полосами, счётчик-пилюля, чек-лист, уведомление, окно.
 *
 * Тут только переходник движка: палитра темы (`theme` + `accent`/`plate`), шрифты, ход числа и полос
 * (`progress`), масштаб устройства для теней и рамка кадра для хука с заливкой. Рисуют сами компоненты
 * (flat/components.ts, flat/cards.ts) — те же, что плоские экраны в 3D (node/scene3d).
 */
import { drawFlat, measureFlat } from "../flat/components";
import { fontsOf } from "../flat/kit";
import { flatTheme } from "../flat/tokens";
import type { UiLayer } from "../types";
import { refSize } from "../types";
import { deviceScale } from "./counter";
import type { LayerRenderer } from "./common";

/** Запас вокруг компонента под тень и свечение (рамка промежуточного холста, маска wipe). */
const SHADOW_PAD = 90;

export const uiRenderer: LayerRenderer<UiLayer> = {
  multipart: true,
  measure(ctx, layer, st, env) {
    if (layer.props.component === "hook" && layer.props.variant === "fill") {
      // Заливка во весь кадр: рамка — с кадр вокруг якоря в любую сторону (промежуточный холст на фейде).
      const { w: W, h: H } = refSize(env);
      const k = Math.max(0.05, st.scale);
      return { w: (W * 2.2) / k, h: (H * 2.2) / k };
    }
    const box = measureFlat(ctx, layer.props);
    return { w: box.w + SHADOW_PAD * 2, h: box.h + SHADOW_PAD * 2 };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const pal = flatTheme(p.theme, { accent: p.accent, plate: p.plate });
    let frame: { x: number; y: number; w: number; h: number } | undefined;
    if (p.component === "hook" && p.variant === "fill") {
      // Заливка хука — весь опорный кадр вокруг якоря слоя (с запасом на поворот и наезд).
      const { w: W, h: H } = refSize(env);
      const k = Math.max(0.05, st.scale);
      frame = { x: (-st.x * W) / k - 200, y: (-st.y * H) / k - 200, w: W / k + 400, h: H / k + 400 };
    }
    drawFlat(
      { g: ctx, pal, fonts: fontsOf(p), local: st.local, dur: st.duration, progress: st.progress, ds: deviceScale(ctx), frame },
      p,
    );
  },
};
