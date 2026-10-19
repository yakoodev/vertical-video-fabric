/**
 * Публичное API моушн-движка. Один и тот же код рисует превью в браузере и
 * кадры финального видео в Node (`node/motion-render.mjs`).
 */
export * from "./types";
export { EASINGS, EASING_NAMES, DEFAULT_EASE, ease, easingFn, cubicBezier, clamp, clamp01, lerp } from "./easing";
export { interpolateKeys, noise1, hashString } from "./keys";
export {
  resolveLayer,
  isLayerActive,
  layerDuration,
  animDuration,
  DEFAULT_IN_DURATION,
  DEFAULT_OUT_DURATION,
  type LayerState,
  type Reveal,
} from "./animate";
export {
  cameraAt,
  cameraView,
  cameraMatrix,
  cameraCss,
  isIdentityCamera,
  drawCameraFrame,
  type CameraState,
  type CameraMatrix,
} from "./camera";
export { drawFrame, drawLayer, visibleLayers, layerBox, hitLayer, type LayerBox } from "./render";
export { DEFAULT_FONT, fontString, emojiRef, isEmoji, graphemes, assetUrl, type FontSpec } from "./fonts";
export { collectImageRefs, collectAudioRefs } from "./assets";
export { invalidateTextCache, layoutText, type TextLayout } from "./layers/text";
export { formatCounter, counterValue } from "./layers/counter";
export { DEFAULT_PALETTE } from "./layers/chart";
export {
  TEMPLATES,
  TEMPLATE_CATEGORIES,
  getTemplate,
  buildTemplate,
  newLayerId,
  type MotionTemplate,
  type TemplateParam,
  type TemplateResult,
} from "./templates";
