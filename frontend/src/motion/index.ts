/**
 * Публичное API моушн-движка. Один и тот же код рисует превью в браузере и
 * кадры финального видео в Node (`node/motion-render.mjs`).
 */
export * from "./types";
export { DEFAULT_FORMAT, FORMATS, FORMAT_IDS, normalizeFormat, clipFormat, layersFormat, frameSize, formatRefSize, formatEnv, isLandscape, sourceStrategy, type FormatId, type FormatInfo } from "./formats";
export { reframeBetween, reframeMotion, reframePlan, layerSize, assetKey, pyRound, type AssetSizes, type ReframePlan } from "./reframe";
export { EASINGS, EASING_NAMES, DEFAULT_EASE, ease, easingFn, cubicBezier, clamp, clamp01, lerp } from "./easing";
export { interpolateKeys, noise1, hashString, rand01 } from "./keys";
export {
  resolveLayer,
  clampCrop,
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
  frameCameraMatrix,
  type CameraState,
  type CameraMatrix,
} from "./camera";
export { drawFrame, drawLayer, visibleLayers, holdFinalLayers, FINAL_HOLD_EPS, layerBox, hitLayer, type LayerBox } from "./render";
export { DEFAULT_FONT, fontString, emojiRef, isEmoji, graphemes, assetUrl, type FontSpec } from "./fonts";
export { collectImageRefs, collectAudioRefs, collectVideoLayers } from "./assets";
export { invalidateTextCache, layoutText, textBoxSize, backdropGeometry, type TextLayout } from "./layers/text";
export { videoFileTime, VIDEO_MIN_SPEED, VIDEO_MAX_SPEED, VIDEO_TAIL } from "./layers/video";
export { DEVICE_KINDS, DEFAULT_DEVICE_COLOR, STATUS_BAR_AUTO, STATUS_BAR_MAX, STATUS_ISLAND_GAP, phoneMetrics, statusBarHeight, statusBarLayout, type PhoneMetrics, type StatusBarLayout } from "./layers/device";
export {
  PARTICLE_KINDS,
  PARTICLE_SHAPES,
  PARTICLE_DEFAULTS,
  MAX_PARTICLES,
  DEFAULT_PARTICLE_COLORS,
  particlesAt,
  particleSeed,
  type Particle,
} from "./layers/particles";
export { DECOR_GLYPHS, decorGlyph, sparklePath, sparklePoints, type DecorGlyph } from "./glyphs";
export { formatCounter, counterValue } from "./layers/counter";
export { DEFAULT_PALETTE } from "./layers/chart";
export {
  CAPTION_STYLES,
  DEFAULT_CAPTION_STYLE,
  captionStyle,
  resolveCaptions,
  captionPages,
  captionPageAt,
  captionWordsFrom,
  speakerColor,
  type CaptionStyle,
  type CaptionPage,
  type CaptionPageWord,
  type ResolvedCaptions,
} from "./layers/captions";
export type { FrameInfo } from "./layers/common";
export type { FrameData } from "./render";
export {
  CHAT_STYLES,
  chatImageRef,
  resolveChat,
  chatEntries,
  chatMessagePasses,
  collectChatImageRefs,
  type ChatStyleInfo,
  type ResolvedChat,
} from "./layers/chat";
export {
  TEMPLATES,
  TEMPLATE_CATEGORIES,
  getTemplate,
  buildTemplate,
  fitTemplate,
  newLayerId,
  type MotionTemplate,
  type TemplateParam,
  type TemplateResult,
} from "./templates";
export {
  BACKGROUND_KINDS,
  ANIMATED_BACKGROUNDS,
  BACKGROUND_INFO,
  BACKGROUND_PERIOD,
  DEFAULT_BACKGROUND,
  DEFAULT_ACCENT,
  LEGACY_DEFAULT_COLOR,
  normalizeBackground,
  isAnimatedBackground,
  backgroundLoop,
  backgroundPhase,
  backgroundPalette,
  clipAccent,
  paintBackground,
  paintBackgroundAround,
  type BackgroundOptions,
} from "./backgrounds";
