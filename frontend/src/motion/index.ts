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
  layerProgress,
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
  inPlaceCenter,
  type CameraState,
  type CameraMatrix,
} from "./camera";
export {
  DEFAULT_FOV,
  FOV_MIN,
  FOV_MAX,
  BASE_Z,
  has3dCamera,
  camera3dAt,
  focal,
  makeProjector,
  planePoint,
  baseCover,
  drawBasePlane,
  warpImage,
  type Camera3D,
  type Plane,
  type Projector,
} from "./space3d";
export { drawFrame, drawLayer, inScene, has3dFields, layerPlane, visibleLayers, holdFinalLayers, liftOverGrayscale, FINAL_HOLD_EPS, layerBox, hitLayer, layerShutter, layerBlurPlan, type LayerBox } from "./render";
export {
  MOTION_BLUR_MIN_SAMPLES,
  MOTION_BLUR_MAX_SAMPLES,
  MOTION_BLUR_PREVIEW_SAMPLES,
  MOTION_BLUR_THRESHOLD,
  MOTION_BLUR_PX_PER_SAMPLE,
  shutterValue,
  blurSamples,
  shutterTimes,
  cameraShutterAt,
  cameraBlurTimes,
  maxTravel,
  paintOpaqueBlur,
  paintLayerBlur,
  type CameraBlurOptions,
} from "./motionblur";
export { DEFAULT_FONT, fontString, emojiRef, isEmoji, graphemes, assetUrl, type FontSpec } from "./fonts";
export { collectImageRefs, collectAudioRefs, collectVideoLayers, collectAnimRefs } from "./assets";
export { animFrameIndex, voiceStateAt, voiceLevelAt, type VoiceState } from "./anim";
export * from "./scene/index";
export { sceneRenderer, sceneViewport } from "./layers/scene";
export { pngtuberRenderer, pngtuberStateRef, pngtuberSize, PIXEL_ART_MAX, PNGTUBER_DEFAULT_W } from "./layers/pngtuber";
export {
  invalidateTextCache,
  layoutText,
  textBoxSize,
  backdropGeometry,
  highlightProgress,
  scrambleChar,
  scrambleFront,
  SCRAMBLE_RATE,
  MARKER_COLOR,
  type TextLayout,
} from "./layers/text";
export {
  FX_KINDS,
  FX_DEFAULTS,
  LEAK_COLORS,
  GLITCH_STYLES,
  GLITCH_SFX,
  glitchKeys,
  resolveFx,
  drawFx,
  flashEnvelope,
  type GlitchStyle,
  type ResolvedFx,
} from "./layers/fx";
export { REACT_SOURCES, REACT_PROPS, REACT_MAX_WINDOW, levelAt, reactAt, type Levels, type ReactEffect } from "./levels";
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
export { DECOR_GLYPHS, decorGlyph, sparklePath, sparklePoints, PLATFORM_ICONS, platformIcon, drawPlatformIcon, type DecorGlyph, type PlatformIcon } from "./glyphs";
export { socialRenderer, formatSocialCount, socialLikes, SOCIAL_STYLES, SOCIAL_THEMES, SOCIAL_MAX_LINES, SOCIAL_IMAGE_RATIO } from "./layers/social";
export { pageRenderer, pageRef, pageRegion, pageWindow, pageKeyValue, activeTaps, PAGE_DEFAULT_WIDTH } from "./layers/page";
export { parsePageParam } from "./templates/page";
export { parseRows, AD_CATEGORY } from "./templates/ads";
export { screenTiltKeys } from "./templates/ads2";
export { roughen, scribblePts, cursorPts, sketchOutline, BOIL_RATE } from "./layers/sketch";
export { parsePath, parseTimes } from "./templates/sketch";
export { nameplateRenderer, nameplateAccent, nameplateMotion, PLATFORM_COLORS, NAMEPLATE_PLATFORMS, NAMEPLATE_STYLES, NAMEPLATE_ANIMS, NAMEPLATE_OUTROS, NAMEPLATE_OUTRO_SEC, NAMEPLATE_SIZE, type NameplateMotion } from "./layers/nameplate";
export { formatCounter, counterValue } from "./layers/counter";
export { odometerPlan, odometerCells, ROLL_STAGGER, ROLL_LAPS, type OdometerPlan } from "./layers/odometer";
export { AD_ICONS, AD_ICON_LABELS, isAdIcon, drawAdIcon } from "./layers/adicons";
export { AD_ACCENT, AD_FONT, AD_PALETTES, accentTone, hueShift, mixHex, compactNumber } from "./layers/adkit";
export { bubblesRenderer, bubbleLayout, bubblePoses, bubbleFocus, bubbleAppearAt, BUBBLES_MAX, type BubbleSpot } from "./layers/bubbles";
export { carouselRenderer, carouselIndex, carouselOffset, carouselSwitches, CAROUSEL_MAX } from "./layers/carousel";
export { waveformRenderer, waveHeights, waveTrack, fakeVoice, WAVE_STYLES, WAVE_SOURCES } from "./layers/waveform";
export { tileWallRenderer, wallProject, wallShift, TILE_COLORS } from "./layers/tilewall";
export {
  FLOW_KINDS, FLOW_DIRECTIONS, FLOW_SHAPES, flowGroups, flowRegion, flowState, flowProgress, morphLayers, isTransition, groupable,
  type FlowKind, type FlowDirection, type FlowShape, type TransitionProps, type TransitionLayer, type FlowState, type GroupXf, type FlowMask,
} from "./flow";
export { flowRenderer, flowGeometry, flowTimes, FLOW_MAX, type FlowGeo } from "./layers/flow";
export { waveLevelKey } from "./levels";
export { DEFAULT_PALETTE } from "./layers/chart";
export {
  CAPTION_STYLES,
  DEFAULT_CAPTION_STYLE,
  captionStyle,
  resolveCaptions,
  captionPages,
  captionPageAt,
  captionWordsFrom,
  isCensoredWord,
  ORIG_SPEAKER,
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
export { readEndScreen, endScreenAt, endScreenFor, paintEndScreen, END_SCREEN_LAYER_ID, END_SCREEN_MAX_BLUR, END_SCREEN_MAX_DIM, type EndScreenFx } from "./endscreen";
// Плоская тема (flat/): токены, палитры тем, значки Lucide, компоненты слоя ui — те же рисуют плоские экраны в 3D.
export { FLAT_THEMES, FLAT_THEME_LABELS, FLAT_PALETTES, FLAT_FONTS, RADII, SPACE, SHADOWS, TYPE, flatTheme, brandPalette, type FlatThemeName, type FlatPalette } from "./flat/tokens";
export { FLAT_ICONS, FLAT_ICON_NAMES, ICON_ALIASES, LUCIDE_LICENSE, LUCIDE_VERSION, flatIconName, drawFlatIcon, parseSvgPath } from "./flat/icons";
export { FLAT_COMPONENTS, FLAT_COMPONENT_LABELS, itemAt, type FlatComponent, type FlatItem, type FlatUiProps } from "./flat/kit";
export { drawFlat, measureFlat } from "./flat/components";
export { uiSize } from "./reframe";
