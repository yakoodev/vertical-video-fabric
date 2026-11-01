/**
 * Слой `scene` — кодовая 3D/2D-сцена (frontend/src/motion/scene, docs/motion/scene-engine.md).
 *
 * Вьюпорт `w × h` опорных px с центром в якоре слоя; сцена рисуется в нём своей камерой
 * (перспектива или ортография), а сам слой — как любой другой: x/y/scale/rotation/opacity,
 * ключи, in/out, followCamera, z/rotX/rotY и 3D-камера клипа поверх.
 */
import { layerDuration } from "../animate";
import { hashString } from "../keys";
import { renderScene } from "../scene/render";
import type { SceneLayer } from "../types";
import { refSize } from "../types";
import type { LayerRenderer } from "./common";

const fin = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : d);

export function sceneViewport(layer: SceneLayer, env: { refWidth?: number; refHeight?: number }): { w: number; h: number } {
  const ref = refSize(env);
  return { w: fin(layer.props?.w, ref.w), h: fin(layer.props?.h, ref.h) };
}

export const sceneRenderer: LayerRenderer<SceneLayer> = {
  // Грани перекрываются: полупрозрачный слой гасится целиком через промежуточный холст.
  multipart: true,
  measure(_ctx, layer, _st, env) {
    return sceneViewport(layer, env);
  },
  draw(ctx, layer, st, env) {
    const vp = sceneViewport(layer, env);
    renderScene(ctx, layer.props?.scene, {
      w: vp.w,
      h: vp.h,
      t: st.local,
      dur: layerDuration(layer),
      env,
      clip: layer.props?.clip !== false,
      seed: hashString(layer.id || "scene"),
    });
  },
};
