import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { clipPlansApi, type SubtitleLine } from "@/api/clipPlans";
import { montageAssetsApi } from "@/api/assets";
import { qk } from "@/api/keys";
import { api } from "@/api/client";
import { useToast } from "@/components/Toast";
import { useModules } from "@/hooks/useModules";
import { readPref } from "@/pages/workspace/InspectorParts";
import { type SubtitleStyle, type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { reframeBetween, type AssetSizes, type ChatMessage, type Layer, type PngtuberLayer } from "@/motion";
import { motionApi } from "@/api/motion";
import { layerAssetSizes, loadMotionFonts, preloadLayerImages } from "@/pages/workspace/motion/runtime";
import { type TransformHandle } from "@/pages/workspace/motion/edit";
import { playbackApi } from "@/api/playback";
import { lookFilterCss, type InsertPreview, type MotionView } from "@/pages/workspace/preview/frame";
import { type Selection } from "@/pages/workspace/ClipTimeline";
import { INSP_TABS, LIB_TABS, type InspTab, type LibTab } from "@/pages/workspace/editor/editorHelpers";
import { useEditorShortcuts } from "@/pages/workspace/editor/useEditorShortcuts";
import { useDraftsFilters } from "@/pages/workspace/drafts/useDraftsFilters";
import { useClipPanelSettings } from "@/pages/workspace/editor/useClipPanelSettings";
import { usePreviewPlayer } from "@/pages/workspace/editor/usePreviewPlayer";
import { usePreviewLoops } from "@/pages/workspace/editor/usePreviewLoops";
import { useClipActions } from "@/pages/workspace/editor/useClipActions";
import { useVideoSource } from "@/pages/workspace/editor/useVideoSource";

/**
 * Всё состояние вкладки «Черновики»: и списка, и редактора. Оно живёт над обоими экранами, потому что переживает
 * переход между ними: фильтры списка ведут рельс редактора, настройки панели достаются следующему клипу
 * без своих настроек и рендеру из списка, а правка уходит на сервер при уходе из редактора.
 */
export function useDraftsEditorState(sourceId: string) {
  const qc = useQueryClient();
  const toast = useToast();
  // Модули сервиса (app/modules.py): выключенный прячет свои панели и кнопки редактора.
  const { on: moduleOn } = useModules();
  const libTabs = LIB_TABS.filter((t) => !t.modules || t.modules.some(moduleOn));
  const videoRef = useRef<HTMLVideoElement>(null);
  const phoneRef = useRef<HTMLCanvasElement>(null);
  const phoneOverlayRef = useRef<HTMLCanvasElement>(null);
  const heatHeadRef = useRef<HTMLSpanElement>(null);
  const tcRef = useRef<HTMLSpanElement>(null);
  const [libTab, setLibTab] = useState<LibTab>(() => {
    try {
      const saved = localStorage.getItem("vvf.ed.libTab");
      return LIB_TABS.some((t) => t.key === saved) ? (saved as LibTab) : "moments";
    } catch {
      return "moments";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("vvf.ed.libTab", libTab);
    } catch {
      /* приватное окно — просто не запоминаем */
    }
  }, [libTab]);
  // Строки субтитров открытого клипа — рисуются на превью-телефоне.
  const subLinesRef = useRef<SubtitleLine[]>([]);
  // Пословные тайминги и стиль клипа: с ними превью рисует те же субтитры,
  // что выжжет рендер, а не «примерно похожие».
  const subWordsRef = useRef<SubtitleWord[]>([]);
  // Баннер рисуем настоящий: раньше в превью была серая плашка с надписью.
  const bannerImgRef = useRef<HTMLImageElement | null>(null);
  const bannerGeomRef = useRef<{ height: number; pos: number } | null>(null);
  const subStyleRef = useRef<SubtitleStyle | null>(null);
  const subPosPctRef = useRef(12);
  // Что выбрано на таймлайне: кусок, строка субтитров или вставка.
  const [tlSelected, setTlSelected] = useState<Selection>(null);
  // Вкладка правой панели — запоминается; выбор на таймлайне сам открывает нужную.
  const [inspTab, setInspTab] = useState<InspTab>(() => {
    const saved = readPref("vvf.ed.inspTab");
    return INSP_TABS.includes(saved as InspTab) ? (saved as InspTab) : "clip";
  });
  const inspScrollRef = useRef<HTMLDivElement>(null);
  const tlSelKey = tlSelected ? `${tlSelected.kind}:${"id" in tlSelected ? tlSelected.id : tlSelected.index}` : "";
  useEffect(() => {
    if (!tlSelected) return;
    const to: Record<NonNullable<Selection>["kind"], InspTab> = {
      layer: "layers",
      cam: "layers",
      insert: "layers",
      piece: "piece",
      mute: "audio",
      sub: "subs",
    };
    setInspTab(to[tlSelected.kind]);
    if (inspScrollRef.current) inspScrollRef.current.scrollTop = 0;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tlSelKey]);
  const [subLines, setSubLines] = useState<SubtitleLine[]>([]);
  const assetMediaRef = useRef(new Map<number, HTMLImageElement | HTMLVideoElement | HTMLAudioElement>());
  // Своё превью «телефона» для «Моментов»: редактор и моменты не показываются
  // одновременно, но держим раздельно, чтобы не зависеть от порядка монтирования.
  const triagePhoneRef = useRef<HTMLCanvasElement>(null);
  const videoSource = useVideoSource({
    sourceId,
  });
  const {
    query, plans, sourceMissing, presets, tracks, subs, storyboard,
  } = videoSource;
  const [activePlanId, setActivePlanId] = useState<number | null>(null);
  // Файлы клипа (app/asset_scope.py): общие, его проекта и чужие, что уже в нём стоят (in_clip_only: нужны слоям,
  // в списки выбора не идут — там pickableAssets).
  const assetsFilter = activePlanId ? { clipId: activePlanId } : { shared: true };
  const assetsQuery = useQuery({
    queryKey: qk.montageAssetsScoped(assetsFilter),
    queryFn: () => montageAssetsApi.listScoped(assetsFilter),
    staleTime: 60_000,
    placeholderData: (previous) => previous,
  });
  const pickableAssets = useMemo(() => (assetsQuery.data ?? []).filter((a) => !a.in_clip_only), [assetsQuery.data]);
  const [selectedSeg, setSelectedSeg] = useState<number | null>(null);
  // Two-stage flow: triage the moments list, then open one clip in the editor.
  // The open editor lives in the URL (?clip=<id>): the browser's Back returns to
  // the moments list instead of leaving the project, and a clip's editor can be
  // opened by link. Before, the view was component state and Back threw you out.
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const clipParam = Number(searchParams.get("clip")) || null;
  const view: "triage" | "editor" = clipParam ? "editor" : "triage";
  // Opened from the moments list in this visit → Back pops history; opened by a
  // link → Back replaces the URL (there is nothing to pop to inside the app).
  const openedFromListRef = useRef(false);
  // The URL is the source of truth for which clip the editor shows (deep links,
  // browser Back/Forward); the moments list's selection follows it.
  const editorPlanId = view === "editor" ? clipParam : null;
  // Студия на весь экран: боковое меню сервиса в монтаже только отнимает место
  // у превью и таймлайна, а назад ведёт кнопка «Назад к черновикам».
  useEffect(() => {
    if (view !== "editor") return;
    document.body.classList.add("vvf-studio");
    return () => document.body.classList.remove("vvf-studio");
  }, [view]);
  useEffect(() => {
    if (clipParam) setActivePlanId(clipParam);
  }, [clipParam]);
  // Открыли клип в монтаже — пусть в фоне собирается лёгкий прокси (идемпотентно; запись не трогает).
  const hasFile = Boolean(query.data?.has_file);
  useEffect(() => {
    if (!clipParam || !hasFile) return;
    // Сначала куски этого клипа (на них «Смотреть монтаж» играет сразу), общий прокси им уступает.
    void playbackApi.piecesEnsure(clipParam).catch(() => undefined);
    void playbackApi.proxyEnsure(sourceId).then((st) => qc.setQueryData(["source-proxy", Number(sourceId)], st)).catch(() => undefined);
  }, [clipParam, hasFile, sourceId]); // eslint-disable-line react-hooks/exhaustive-deps
  const previewPlayer = usePreviewPlayer({
    videoRef, query, sourceMissing, view, editorPlanId,
  });
  const {
    playbackRef, stopPlayback,
  } = previewPlayer;
  // The editor works on favourites: jump to the requested moment, else keep the
  // current one if it's starred, else the first starred one.
  const openEditor = (planId?: number) => {
    const favs = plans.filter((p) => p.favorite && !p.hidden);
    const target =
      planId ??
      (activePlan && (activePlan.favorite || !favs.length) ? activePlan.id : favs[0]?.id ?? activePlan?.id);
    if (target == null) return;
    stopPlayback();
    setActivePlanId(target);
    setSelectedSeg(null);
    openedFromListRef.current = true;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("clip", String(target));
      return next;
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const backToMoments = () => {
    stopPlayback();
    if (openedFromListRef.current) {
      openedFromListRef.current = false;
      navigate(-1);
      return;
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("clip");
        return next;
      },
      { replace: true },
    );
  };

  const layerDragRef = useRef<{
    id: string;
    handle: TransformHandle;
    startX: number;
    startY: number;
    cx: number;
    cy: number;
    /** Зум камеры под слоем followCamera: мышь на экране сдвигает его в zoom раз меньше. */
    zoom: number;
    from: Layer;
    localT: number;
    x: number;
    y: number;
    scale: number;
    rotation: number;
  } | null>(null);
  const panelSettingsState = useClipPanelSettings({
    sourceId, query, plans, subs, view, editorPlanId, layerDragRef, setTlSelected,
  });
  const {
    cutsRef, muteRef, gainRef, audioEditRef, flushSettings, presetId, subsOn, subId, subEngine, bannerOn, bannerId,
    musicOn, setMusicOn, trackId, setTrackId, musicStartSec, setMusicStartSec, mirror, transition, layers,
    camera, aspect, layersFmt, setSettingsNonce, bannerHeightPct, bannerPosPct, subPosPct,
  } = panelSettingsState;
  const musicPreviewRef = useRef<HTMLAudioElement>(null);
  const needsReframe = layersFmt !== aspect && (layers.length > 0 || camera.length > 0);
  // Размеры картинок и видео слоёв — от них высота слоя в рамке сцены (как ffprobe на
  // сервере): файлы библиотеки — из её списка, стикеры — когда догрузятся.
  const libSizes = useMemo(() => {
    const out: AssetSizes = {};
    for (const a of assetsQuery.data ?? []) if (a.width > 0 && a.height > 0) out[String(a.id)] = [a.width, a.height];
    return out;
  }, [assetsQuery.data]);
  const [sizesKey, setSizesKey] = useState("{}");
  useEffect(() => {
    if (!needsReframe) return;
    let tries = 0;
    const check = () => {
      const { sizes, missing } = layerAssetSizes(layers, libSizes);
      const key = JSON.stringify(sizes);
      setSizesKey((prev) => (prev === key ? prev : key));
      return missing === 0;
    };
    if (check()) return;
    const id = window.setInterval(() => {
      tries += 1;
      if (check() || tries > 40) window.clearInterval(id);
    }, 400);
    return () => window.clearInterval(id);
  }, [needsReframe, layers, libSizes]);
  const reframedView = useMemo(
    () => (needsReframe ? reframeBetween(layers, camera, layersFmt, aspect, JSON.parse(sizesKey) as AssetSizes) : null),
    [needsReframe, layers, camera, layersFmt, aspect, sizesKey],
  );
  const motionViewRef = useRef<MotionView>({ layers: [], camera: [], selected: null });
  // Правка текста прямо на кадре (двойной клик): где стоит поле — в % экрана телефона.
  const inlineCancelRef = useRef(false);
  const [inlineEdit, setInlineEdit] = useState<{ id: string; field: string; text: string; left: number; top: number; width: number } | null>(null);
  // Шрифты движка — один раз; картинки слоёв — как только слой появился,
  // чтобы к моменту его показа в превью они уже были в кэше.
  useEffect(() => {
    void loadMotionFonts();
  }, []);
  useEffect(() => preloadLayerImages(layers), [layers]);
  // Чат стрима для слоя «чат на экране» — грузим, только когда такой слой есть.
  const hasChatLayer = layers.some((l) => l.type === "chat");
  const planChat = useQuery({
    queryKey: ["plan-chat", editorPlanId],
    queryFn: () => api.get<ChatMessage[]>(`/api/clip-plans/${editorPlanId}/chat?limit=5000`),
    enabled: Boolean(editorPlanId && hasChatLayer && moduleOn("twitch_chat")),
    staleTime: 300_000,
  });
  // PNGtuber: состояния голоса по кадрам считает сервер (как для рендера) — по слоям аватара:
  // их интервалы и настройки голоса меняются — пересчёт.
  const voiceLayers = layers.filter((l): l is PngtuberLayer => l.type === "pngtuber" && !l.hidden);
  const voiceKey = JSON.stringify(voiceLayers.map((l) => [l.id, l.start, l.end, l.props?.voice ?? null, Boolean(l.props?.states?.shout)]));
  const planVoice = useQuery({
    queryKey: ["plan-voice", editorPlanId, voiceKey],
    queryFn: () => motionApi.voice(Number(editorPlanId), voiceLayers),
    enabled: Boolean(editorPlanId && voiceLayers.length && moduleOn("pngtuber")),
    staleTime: 300_000,
  });
  // Реакция на звук (react): огибающие громкости клипа считает сервер один раз — по нужным
  // источникам (голос / музыка / весь звук); сменились источники или музыка клипа — пересчёт.
  const reactSources = [...new Set(layers.filter((l) => l.react && !l.hidden).map((l) => l.react?.source ?? "voice"))].sort();
  const planLevels = useQuery({
    queryKey: ["plan-levels", editorPlanId, reactSources.join(","), musicOn, trackId],
    queryFn: () => motionApi.levels(Number(editorPlanId), reactSources),
    enabled: Boolean(editorPlanId && reactSources.length),
    staleTime: 300_000,
  });
  const motionFonts = useQuery({ queryKey: ["motion-fonts"], queryFn: loadMotionFonts, staleTime: Infinity });
  // Стабильная «который сейчас час клипа» для панелей, которые опрашивают время сами.
  const clipNowRef = useRef<() => number>(() => 0);
  const clipNow = useCallback(() => clipNowRef.current(), []);
  const reframeHintRef = useRef(false);
  // Перетаскивание мема по кадру: что схватили и с чего начали.
  const insertsPreviewRef = useRef<InsertPreview[]>([]);
  const insertDragRef = useRef<
    { index: number; mode: "move" | "resize"; startX: number; startY: number; from: { x: number; y: number; scale: number } } | null
  >(null);
  const [coverUploading, setCoverUploading] = useState(false);
  // «Файл клипа» dialog; bumping the nonce re-reads the clip's settings into the
  // panel after the file was applied (the file may have changed them).
  const [clipFileOpen, setClipFileOpen] = useState(false);
  const [watchOpen, setWatchOpen] = useState(false);
  // Пока открыт «Смотреть монтаж», плеер редактора не качает запись: один её заголовок — десятки МБ,
  // и на слабой сети они забивали канал кускам. Закрыли — вернулись на ту же секунду.
  const watchParkRef = useRef<number | null>(null);
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (watchOpen) {
      watchParkRef.current = v.currentTime;
      v.pause();
      v.removeAttribute("src");
      v.load();
    } else if (watchParkRef.current != null) {
      const at = watchParkRef.current;
      watchParkRef.current = null;
      if (v.getAttribute("src")) {
        v.addEventListener("loadedmetadata", () => { v.currentTime = at; }, { once: true });
        v.load();
      }
    }
  }, [watchOpen]);
  // Moment under the pointer — the target of the F / X / E shortcuts.
  const hoverPlanRef = useRef<number | null>(null);
  const [aiMontageOpen, setAiMontageOpen] = useState(false);
  // С чем открыт диалог ИИ-монтажа: с полки вставок он приходит с готовым
  // пожеланием «только мемы», чтобы не перекраивать уже собранный клип.
  const [aiGoal, setAiGoal] = useState("");
  // Правка строк в узкой колонке инспектора — микроскоп, поэтому у неё есть
  // широкий режим на всё окно (референс editor2: компактный вход, широкая работа).
  const [subsWide, setSubsWide] = useState(false);
  const planWave = useQuery({
    queryKey: ["plan-waveform", editorPlanId],
    queryFn: () => clipPlansApi.waveform(editorPlanId as number),
    enabled: Boolean(editorPlanId) && query.isSuccess && !sourceMissing,
    staleTime: 5 * 60 * 1000,
  });
  const planSubs = useQuery({
    queryKey: ["plan-subtitles", editorPlanId],
    queryFn: () => clipPlansApi.getSubtitles(editorPlanId as number),
    enabled: Boolean(editorPlanId),
  });
  const [useVlmFocus, setUseVlmFocus] = useState(false);
  const [packagePieces, setPackagePieces] = useState(false);
  const draftsFilters = useDraftsFilters({
    query, plans,
  });
  const {
    selected,
  } = draftsFilters;
  // «Следовать за…» (трекинг слоя): следующий клик по превью — точка цели, а не выбор слоя.
  const pickPointRef = useRef<((p: { x: number; y: number; t: number }) => void) | null>(null);
  const [pickingPoint, setPickingPoint] = useState(false);
  const activePlan = useMemo(() => plans.find((p) => p.id === activePlanId) ?? plans[0], [plans, activePlanId]);
  const clipActions = useClipActions({
    sourceId, videoRef, openEditor, activePlan, useVlmFocus, flushSettings, presetId, subsOn, subId, subEngine,
    bannerOn, bannerId, musicOn, setMusicOn, trackId, setTrackId, setMusicStartSec, mirror, transition,
    setSettingsNonce, bannerHeightPct, bannerPosPct, subPosPct, selected,
  });
  // The preset list accumulates user-saved dupes; show each label once so the
  // picker isn't flooded with identical entries.
  const uniquePresets = useMemo(() => {
    const seen = new Set<string>();
    return (presets.data ?? []).filter((p) => {
      const key = (p.label ?? "").trim().toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [presets.data]);
  const selectedPreset = presets.data?.find((p) => p.id === presetId);
  const previewLoops = usePreviewLoops({
    videoRef, heatHeadRef, tcRef, query, tracks, clipParam, view, motionViewRef, activePlan, searchParams,
    playbackRef, cutsRef, muteRef, gainRef, audioEditRef, musicOn, trackId, musicStartSec,
  });
  const {
    pieceCss,
  } = previewLoops;
  const lookCss = [pieceCss, lookFilterCss(selectedPreset)].filter(Boolean).join(" ");
  // A few moments spread across the video — one frame can hide what a look does.
  const lookFrames = useMemo(() => {
    const all = storyboard.data?.frames ?? [];
    if (all.length <= 3) return all;
    return [0.2, 0.5, 0.8].map((p) => all[Math.floor(all.length * p)]);
  }, [storyboard.data]);

  // Картинка баннера для превью: грузим один раз на выбранный баннер.
  useEffect(() => {
    if (!bannerOn || !bannerId) {
      bannerImgRef.current = null;
      return;
    }
    const img = new Image();
    img.src = `/media/banners/${bannerId}`;
    img.onload = () => (bannerImgRef.current = img);
    img.onerror = () => (bannerImgRef.current = null);
  }, [bannerOn, bannerId]);

  const editorShortcuts = useEditorShortcuts();
  return {
    ...editorShortcuts,
    ...draftsFilters,
    ...panelSettingsState,
    ...previewPlayer,
    ...previewLoops,
    ...clipActions,
    ...videoSource,
    sourceId, qc, toast, libTabs, videoRef, phoneRef, phoneOverlayRef, heatHeadRef, tcRef, subLinesRef, subWordsRef,
    bannerImgRef, bannerGeomRef, subStyleRef, subPosPctRef, inspScrollRef, assetMediaRef, triagePhoneRef,
    assetsQuery, pickableAssets, navigate, view, openEditor, backToMoments, musicPreviewRef, reframedView,
    motionViewRef, inlineCancelRef, planChat, planVoice, planLevels, motionFonts, clipNowRef, clipNow,
    reframeHintRef, layerDragRef, insertsPreviewRef, insertDragRef, hoverPlanRef, planWave, planSubs, pickPointRef,
    activePlan, uniquePresets, selectedPreset, lookCss, lookFrames, libTab, setLibTab, tlSelected, setTlSelected,
    inspTab, setInspTab, subLines, setSubLines, activePlanId, setActivePlanId, selectedSeg, setSelectedSeg,
    inlineEdit, setInlineEdit, coverUploading, setCoverUploading, clipFileOpen, setClipFileOpen, watchOpen,
    setWatchOpen, aiMontageOpen, setAiMontageOpen, aiGoal, setAiGoal, setSubsWide, useVlmFocus, setUseVlmFocus,
    packagePieces, setPackagePieces, pickingPoint, setPickingPoint, moduleOn, subsWide,
  };
}

export type DraftsEditorState = ReturnType<typeof useDraftsEditorState>;
