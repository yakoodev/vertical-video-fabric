import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { sourcesApi } from "@/api/sources";
import { clipPlansApi, type SubtitleLine } from "@/api/clipPlans";
import { segmentsApi } from "@/api/segments";
import { ffmpegPresetsApi, bannersApi, audioTracksApi, subtitleProfilesApi, montageAssetsApi } from "@/api/assets";
import { qk } from "@/api/keys";
import { brandsApi } from "@/api/brands";
import { type AiSegment, type ColorFix, type FocusPoint, type SourceDetail, type SpeedAudio, colorFixCss } from "@/api/types";
import { audioMixApi } from "@/api/audioMix";
import { api, ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { isSourceFileMissing } from "@/components/MissingSourceNotice";
import { useModules } from "@/hooks/useModules";
import { readPref } from "@/pages/workspace/InspectorParts";
import { type SubtitleStyle, type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { frameSize, reframeBetween, type AssetSizes, type ChatMessage, type Layer, type PngtuberLayer } from "@/motion";
import { motionApi } from "@/api/motion";
import { layerAssetSizes, loadMotionFonts, preloadLayerImages } from "@/pages/workspace/motion/runtime";
import { type TransformHandle } from "@/pages/workspace/motion/edit";
import { previewSkip } from "@/api/cuts";
import { CENSOR_STYLES, isMutedAt, type CensorStyle } from "@/api/mute";
import { previewVolume } from "@/api/gain";
import { jlSourceAt } from "@/api/audioEdit";
import { useLayerAudio } from "@/pages/workspace/motion/useLayerAudio";
import { playbackApi } from "@/api/playback";
import { useClipMusic } from "@/pages/workspace/preview/useClipMusic";
import { lookFilterCss, type InsertPreview, type MotionView } from "@/pages/workspace/preview/frame";
import { setPreviewSound } from "@/pages/workspace/motion/sound";
import { clipToSource, type Selection } from "@/pages/workspace/ClipTimeline";
import { clipTimeOf, formatTimecode, INSP_TABS, LIB_TABS, type InspTab, type LibTab, type SpecPiece } from "@/pages/workspace/editor/editorHelpers";
import { useEditorShortcuts } from "@/pages/workspace/editor/useEditorShortcuts";
import { useDraftsFilters } from "@/pages/workspace/drafts/useDraftsFilters";
import { useClipPanelSettings } from "@/pages/workspace/editor/useClipPanelSettings";

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
  const query = useQuery({ queryKey: qk.source(sourceId), queryFn: () => sourcesApi.get(sourceId) });
  const plans = query.data?.clip_plans ?? [];
  // Запись удалена (клипы и монтаж живы). Рендера без неё нет, а смотреть и править монтаж можно с
  // прокси 480p (app/preview_proxy.py): он сам собирается в фоне, пока запись на месте.
  const recordMissing = isSourceFileMissing(query.data);
  const proxyInfo = useQuery({
    queryKey: ["source-proxy", Number(sourceId)],
    queryFn: () => playbackApi.proxyStatus(sourceId),
    enabled: query.isSuccess,
    refetchInterval: (q) => (["queued", "running"].includes(q.state.data?.status ?? "") ? 10_000 : false),
  });
  const proxyReady = proxyInfo.data?.status === "ready";
  // Нет ни записи, ни прокси: ни кадров, ни волны, ни видео исходника не запрашиваем.
  const sourceMissing = recordMissing && !proxyReady;
  // Чем играет превью: прокси, если он уже есть, иначе запись. На время открытого проекта адрес не
  // меняется (собрался прокси — новый адрес при следующем открытии), чтобы плеер не сбрасывало.
  const [mediaSrc, setMediaSrc] = useState<string | null>(null);
  useEffect(() => setMediaSrc(null), [sourceId]);
  useEffect(() => {
    if (!query.isSuccess || !proxyInfo.isFetched) return;
    if (proxyReady && proxyInfo.data?.url) setMediaSrc((prev) => (prev && !recordMissing ? prev : proxyInfo.data!.url));
    else setMediaSrc((prev) => prev ?? `/media/sources/${sourceId}`);
  }, [query.isSuccess, proxyInfo.isFetched, proxyReady, proxyInfo.data?.url, recordMissing, sourceId]);
  const presets = useQuery({ queryKey: qk.ffmpegPresets, queryFn: ffmpegPresetsApi.list });
  const banners = useQuery({ queryKey: qk.banners, queryFn: bannersApi.list });
  const tracks = useQuery({ queryKey: qk.audioTracks, queryFn: audioTracksApi.list });
  const subs = useQuery({ queryKey: qk.subtitleProfiles, queryFn: subtitleProfilesApi.list });
  // One real frame from the source powers the look thumbnails.
  const storyboard = useQuery({
    queryKey: ["storyboard", sourceId],
    queryFn: () => sourcesApi.storyboard(sourceId),
    staleTime: 5 * 60_000,
    enabled: query.isSuccess && !sourceMissing,
  });

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
  const [loop, setLoop] = useState(true);
  const [rate, setRate] = useState(1);
  const rateRef = useRef(rate);
  rateRef.current = rate;
  const loopRef = useRef(loop);
  loopRef.current = loop;
  // Ordered ranges to play back as one clip preview (segments stitched in time).
  const playbackRef = useRef<{ ranges: { start: number; end: number; speed?: number }[]; idx: number } | null>(null);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => {
      const pb = playbackRef.current;
      const cur = pb?.ranges[pb.idx];
      if (!pb || !cur) return;
      if (v.currentTime >= cur.end - 0.02) {
        const next = pb.idx + 1;
        if (next < pb.ranges.length) {
          pb.idx = next;
          v.currentTime = pb.ranges[next].start;
          v.playbackRate = rateRef.current * (pb.ranges[next].speed ?? 1); // скорость куска
        } else if (loopRef.current) {
          pb.idx = 0;
          v.currentTime = pb.ranges[0].start;
          v.playbackRate = rateRef.current * (pb.ranges[0].speed ?? 1);
        } else {
          v.pause();
          playbackRef.current = null;
        }
      }
    };
    v.addEventListener("timeupdate", onTime);
    return () => v.removeEventListener("timeupdate", onTime);
    // Re-bind whenever the <video> element is (re)mounted: after load and on
    // every triage ↔ editor switch — each view renders its own player.
  }, [view, query.isSuccess]); // eslint-disable-line react-hooks/exhaustive-deps

  // Opening a clip in the editor parks the player on its first frame, so the
  // stage, crop window and phone preview show that moment, not 0:00.
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
  useEffect(() => {
    const v = videoRef.current;
    const plan = query.data?.clip_plans.find((p) => p.id === editorPlanId) ?? null;
    const start = plan?.segments[0]?.start_sec;
    if (!v || start == null) return;
    const park = () => {
      if (!playbackRef.current) v.currentTime = start;
    };
    if (v.readyState >= 1) park();
    else v.addEventListener("loadedmetadata", park, { once: true });
    return () => v.removeEventListener("loadedmetadata", park);
  }, [editorPlanId, query.isSuccess]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = rate;
  }, [rate, view, query.isSuccess, editorPlanId]);

  const playRanges = (ranges: { start: number; end: number; speed?: number }[]) => {
    const v = videoRef.current;
    if (!v || !ranges.length || sourceMissing) return;
    playbackRef.current = { ranges, idx: 0 };
    // Кусок со скоростью (slow-mo ×0.5) превью играет с той же скоростью; реверс — вперёд.
    v.playbackRate = Math.min(16, Math.max(0.0625, rate * (ranges[0].speed ?? 1)));
    v.currentTime = ranges[0].start;
    void v.play();
  };
  const stopPlayback = () => {
    playbackRef.current = null;
    videoRef.current?.pause();
  };

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
  // Музыка по ссылке (POST /api/audio-tracks/from-url): сервер качает звук через yt-dlp.
  const [musicUrl, setMusicUrl] = useState("");
  const musicFromUrl = useMutation({
    mutationFn: () => audioTracksApi.fromUrl(musicUrl.trim()),
    onSuccess: async (track) => {
      await qc.invalidateQueries({ queryKey: qk.audioTracks });
      setMusicOn(true);
      setTrackId(track.id);
      setMusicStartSec(0);
      setMusicUrl("");
      toast.success(`Трек «${track.label}» добавлен и выбран`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось скачать трек"),
  });
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
  // 🖼 Обложка с заголовком: кадр под курсором + крупный титр (app/cover_compose.py).
  const [coverTitle, setCoverTitle] = useState("");
  const [coverStyle, setCoverStyle] = useState("yellow");
  const composeCover = useMutation({
    mutationFn: async () => {
      const plan = activePlan;
      if (!plan) throw new ApiError(400, "клип не открыт");
      await flushSettings();
      const segs = plan.segments;
      const t = clipTimeOf(segs, videoRef.current?.currentTime ?? 0) ?? 0;
      return api.post<{ url: string }>(`/api/clip-plans/${plan.id}/cover/compose`, {
        t: Number(t.toFixed(2)),
        title: coverTitle.trim() || plan.title || "СМОТРИ",
        style: coverStyle,
      });
    },
    onSuccess: async () => {
      toast.success("Обложка готова и включена — вшита в первые кадры");
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Обложка не получилась"),
  });
  // 🧩 Компиляция избранных в один клип «ТОП-N» — открывается сразу в монтаже.
  const compile = useMutation({
    mutationFn: (ids: number[]) =>
      api.post<{ clip_plan_id: number; clips: number; skipped: number[]; duration: number }>(
        `/api/sources/${sourceId}/compilation`,
        { plan_ids: ids, countdown: true },
      ),
    onSuccess: async (r) => {
      toast.success(
        `Компиляция из ${r.clips} клипов, ${Math.round(r.duration)} с` +
          (r.skipped.length ? ` · не влезло ${r.skipped.length} (лимит 12 кусков)` : ""),
      );
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      openEditor(r.clip_plan_id);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Компиляция не собралась"),
  });
  // ⚡ Автомонтаж всех избранных подряд: речь распознаётся, где её ещё нет.
  const [autoProgress, setAutoProgress] = useState("");
  const batchAuto = useMutation({
    mutationFn: async (ids: number[]) => {
      await flushSettings();
      let done = 0;
      const failed: string[] = [];
      for (const id of ids) {
        setAutoProgress(`${done + 1}/${ids.length}…`);
        try {
          const subs = await clipPlansApi.getSubtitles(id);
          if (!subs?.words?.length) await clipPlansApi.generateSubtitles(id, "whisper");
          await api.post(`/api/clip-plans/${id}/auto-edit`, { keep_layers: true });
          done += 1;
        } catch (e) {
          failed.push(`#${id}: ${e instanceof ApiError ? e.message : "ошибка"}`);
        }
      }
      return { done, failed };
    },
    onSuccess: async ({ done, failed }) => {
      setAutoProgress("");
      if (failed.length) toast.error(`Автомонтаж: готово ${done}, не вышло ${failed.length} — ${failed.slice(0, 2).join("; ")}`);
      else toast.success(`Автомонтаж готов у ${done} клипов — открой любой в монтаже, Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
  });
  // ⚡ Автомонтаж без ИИ — правила по словам и громкости (app/auto_edit.py).
  const autoEdit = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return api.post<{ layers: number; camera: number }>(`/api/clip-plans/${clipPlanId}/auto-edit`, {
        keep_layers: true,
      });
    },
    onSuccess: async (r) => {
      toast.success(`Автомонтаж: ${r.layers} слоёв, ${r.camera ? "наезды по речи" : "без наездов"} — Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Автомонтаж не удался"),
  });
  // 🎚 Сведение звука (app/audio_mix.py): замер по стемам и автосведение.
  const mixReport = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return audioMixApi.report(clipPlanId);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Сведение не измерено"),
  });
  const autoMix = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return audioMixApi.autoMix(clipPlanId);
    },
    onSuccess: async (r) => {
      const a = r.before.voice_vs_music_db;
      const b = r.after.voice_vs_music_db;
      toast.success(
        a != null && b != null
          ? `Свёл: голос над музыкой ${a.toFixed(0)} → ${b.toFixed(0)} дБ — перерендерь; Ctrl+Z откатит`
          : "Свёл звук — перерендерь; Ctrl+Z откатит",
      );
      mixReport.reset();
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Автосведение не вышло"),
  });
  // 🤬 Мат (app/censor.py): середина слова глушится (стиль — тишина, писк, шум…) + звёздочки в
  // субтитрах, без рендера. Проверку рендером и Whisper (verify) зовут агенты — это минуты.
  // Стиль "" — прежний стиль клипа (хранится у отрезков), у нового клипа — по умолчанию сервера.
  const [censorStyle, setCensorStyle] = useState<CensorStyle | "">("");
  const censorMat = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return api.post<{
        words: { i: number; word: string; level: number; style?: CensorStyle }[];
        stars: number;
        manual_kept: number;
      }>(`/api/clip-plans/${clipPlanId}/censor`, { verify: false, ...(censorStyle ? { style: censorStyle } : {}) });
    },
    onSuccess: async (r) => {
      const style = r.words[0]?.style;
      if (!r.words.length) toast.success("Мата в субтитрах не найдено");
      else
        toast.success(
          `Мат: ${r.words.length} сл. заглушено серединой${style ? ` (${CENSOR_STYLES[style]})` : ""}` +
            (r.stars ? `, в субтитрах звёздочки (${r.stars})` : "") +
            (r.manual_kept ? ` · ручные отрезки целы (${r.manual_kept})` : "") +
            " — Ctrl+Z откатит",
        );
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      await qc.invalidateQueries({ queryKey: ["plan-subtitles"] });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Мат не заглушён"),
  });
  // Бренд заказчика (app/brand_kit.py): субтитры и хук в его цветах, логотип, призыв в конце.
  const brands = useQuery({ queryKey: qk.brands, queryFn: brandsApi.list, staleTime: 60_000 });
  const [brandPick, setBrandPick] = useState(0);
  const applyBrand = useMutation({
    mutationFn: async ({ clipPlanId, brandId }: { clipPlanId: number; brandId: number }) => {
      await flushSettings();
      return brandsApi.apply(clipPlanId, brandId);
    },
    onSuccess: async (r) => {
      const note = r.warnings.length ? ` · ${r.warnings[0]}` : "";
      toast.success(`Бренд «${r.brand.name}»: ${r.changes.join(", ") || "без изменений"}${note} — Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Бренд не применился"),
  });
  const undoEdit = useMutation({
    mutationFn: async (clipPlanId: number) => {
      // Незаписанные правки панели сначала уезжают на сервер: иначе автосохранение
      // сработает после отмены и вернёт то, что мы только что откатили.
      await flushSettings();
      return clipPlansApi.undo(clipPlanId);
    },
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
      toast.success(`Шаг назад: ${r.label}${r.left ? ` · осталось ${r.left}` : ""}`);
    },
    onError: (e) =>
      e instanceof ApiError && e.status === 404
        ? toast.push("Отменять нечего", "info")
        : toast.error(e instanceof ApiError ? e.message : "Не удалось отменить"),
  });

  // ⏩ Скорость/реверс куска — тем же инструментом, что у агента (set_segment_speed): сервер
  // сдвигает слои, камеру и субтитры за новой длиной куска, поэтому панель перечитываем.
  const segmentSpeed = useMutation({
    mutationFn: async (body: { planId: number; piece: number; speed: number; reverse: boolean; audio?: SpeedAudio }) => {
      await flushSettings();
      const { planId, ...rest } = body;
      return clipPlansApi.setSegmentSpeed(planId, rest);
    },
    onSuccess: async (r, body) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      await qc.invalidateQueries({ queryKey: ["plan-subtitles", body.planId] });
      setSettingsNonce((n) => n + 1);
      const what = r.reverse ? `реверс${r.speed !== 1 ? ` ×${r.speed}` : ""}` : r.speed !== 1 ? `×${r.speed}` : "как в видео";
      toast.success(`Кусок ${r.piece + 1}: ${what} · ${r.length.toFixed(1)} с`);
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось поменять скорость куска"),
  });

  // ♪ К долям (snap_to_beats): сервер двигает слои/ключи/камеру/склейки — настройки перечитываем.
  const [snapMode, setSnapMode] = useState<"beat" | "downbeat" | "joins">("beat");
  const snapBeats = useMutation({
    mutationFn: async (body: { planId: number; mode: "beat" | "downbeat" | "joins" }) => {
      await flushSettings();
      return clipPlansApi.snapBeats(body.planId, {
        targets: body.mode === "joins" ? ["all"] : ["layers", "keys", "camera"],
        grid: body.mode === "downbeat" ? "downbeat" : "beat",
      });
    },
    onSuccess: async (r, body) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      await qc.invalidateQueries({ queryKey: ["plan-subtitles", body.planId] });
      setSettingsNonce((n) => n + 1);
      const bpm = r.grid.bpm ? ` · ${Math.round(r.grid.bpm)} BPM` : "";
      if (r.moved) toast.success(`К долям: сдвинуто ${r.moved}${bpm}`);
      else toast.push(r.note, "info");
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось привязать к долям"),
  });

  // 🎨 Цвет куска и подгонка кусков — инструментами агента (set_piece_color / match_piece_colors).
  const pieceColor = useMutation({
    mutationFn: async (body: { planId: number; piece: number; reset?: boolean } & ColorFix) => {
      await flushSettings();
      const { planId, ...rest } = body;
      return clipPlansApi.setPieceColor(planId, rest);
    },
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось поменять цвет куска"),
  });
  const matchColors = useMutation({
    mutationFn: async (body: { planId: number; reference: number }) => {
      await flushSettings();
      return clipPlansApi.matchPieceColors(body.planId, { reference: body.reference });
    },
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      const gaps = r.pieces.map((p) => `${p.piece + 1}: ${p.luma_gap_before}→${p.luma_gap_after}`).join(", ");
      toast.success(`Цвет кусков подогнан к куску ${r.reference + 1} (разница яркости ${gaps})`);
      for (const w of r.warnings ?? []) toast.push(w, "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось подогнать цвет"),
  });

  const batch = useMutation({
    // No ids = everything ticked «в рендер»; the editor passes just its clip.
    mutationFn: async (ids: number[] | undefined) => {
      // Unsaved edits of the open clip go in before the render reads them.
      await flushSettings();
      return clipPlansApi.renderBatch(sourceId, {
        clip_plan_ids: ids ?? [...selected],
        ffmpeg_preset_id: presetId || undefined,
        subtitle_profile_id: subsOn ? subId || undefined : undefined,
        subtitle_provider: subsOn ? subEngine || undefined : undefined,
        // Запасной отступ для клипов без своих настроек (они 9:16); настроенные берут свой — от кадра своего формата.
        subtitle_margin_v: subsOn ? Math.round((subPosPct / 100) * frameSize("9:16").h) : undefined,
        banner_id: bannerOn ? bannerId || undefined : undefined,
        banner_height_frac: bannerOn ? bannerHeightPct / 100 : undefined,
        banner_y_frac: bannerOn ? bannerPosPct / 100 : undefined,
        mirror: mirror || undefined,
        music_track_id: musicOn ? trackId || undefined : undefined,
        // Fallback for clips never set up in the editor; set-up clips use their own.
        transition,
      });
    },
    onSuccess: (clips) => {
      toast.success(`Рендер запущен: ${clips.length} клип(ов)`);
      qc.invalidateQueries({ queryKey: qk.activeTasks });
      qc.invalidateQueries({ queryKey: qk.clips(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить рендер"),
  });

  const focusOptions = useQuery({ queryKey: ["focus-options"], queryFn: sourcesApi.focusOptions, staleTime: Infinity });

  const setFocus = useMutation({
    mutationFn: (body: { focus_preset?: string; focus_strategy?: string }) => sourcesApi.setFocus(sourceId, body),
    onSuccess: () => {
      toast.success("Сохранено. Перезапустите авто-фокус, чтобы пересчитать.");
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });

  const cutStrategies = useQuery({ queryKey: ["cut-strategies"], queryFn: sourcesApi.cutStrategies, staleTime: Infinity });
  const refineCuts = useMutation({
    mutationFn: (strategy: string) => sourcesApi.refineCuts(sourceId, strategy),
    onSuccess: (r) => {
      toast.success(`Границы пересчитаны (${r.updated} сегм.)`);
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось пересчитать границы"),
  });

  const autofocus = useMutation({
    mutationFn: (segmentIds: number[]) => segmentsApi.autofocus(sourceId, segmentIds, useVlmFocus),
    onSuccess: (res) => {
      toast.success(`Авто-фокус: обновлено ${res.updated} сегм.`);
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось вычислить фокус"),
  });

  const commit = useMutation({
    mutationFn: ({ id, s, e }: { id: number; s: number; e: number }) => segmentsApi.patchTimecodes(id, s, e),
    onMutate: async ({ id, s, e }) => {
      await qc.cancelQueries({ queryKey: qk.source(sourceId) });
      const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
      if (prev) {
        const patch = <T extends { id: number; start_sec: number; end_sec: number }>(seg: T): T =>
          seg.id === id ? { ...seg, start_sec: s, end_sec: e } : seg;
        qc.setQueryData<SourceDetail>(qk.source(sourceId), {
          ...prev,
          segments: prev.segments.map(patch),
          clip_plans: prev.clip_plans.map((p) => ({ ...p, segments: p.segments.map(patch) })),
        });
      }
      return { prev };
    },
    onError: (err, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.source(sourceId), ctx.prev);
      toast.error(err instanceof ApiError ? err.message : "Не удалось сохранить таймкоды");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: qk.source(sourceId) }),
  });

  // Manual "fixed frame": write a CONSTANT focus track to the plan's segments
  // via the same focus-points system autofocus uses — so it never fights the
  // per-segment keyframe editor (which it used to override). Patch the cache
  // immediately (preview moves live), persist after a short debounce.
  const manualSaveTimer = useRef<number | null>(null);
  // «Следовать за…» (трекинг слоя): следующий клик по превью — точка цели, а не выбор слоя.
  const pickPointRef = useRef<((p: { x: number; y: number; t: number }) => void) | null>(null);
  const [pickingPoint, setPickingPoint] = useState(false);
  const constTrack = (seg: AiSegment, cx: number): FocusPoint[] => [
    { t: 0, x: cx },
    { t: Math.max(0.01, seg.end_sec - seg.start_sec), x: cx },
  ];
  const setFixedFrame = (planId: number, x: number) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    const segs = prev?.clip_plans.find((p) => p.id === planId)?.segments ?? [];
    if (!segs.length) return;
    const cx = Math.min(1, Math.max(0, x));
    const ids = new Set(segs.map((s) => s.id));
    if (prev) {
      const patch = (seg: AiSegment): AiSegment => (ids.has(seg.id) ? { ...seg, focus: constTrack(seg, cx) } : seg);
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        segments: prev.segments.map(patch),
        clip_plans: prev.clip_plans.map((p) => ({ ...p, segments: p.segments.map(patch) })),
      });
    }
    if (manualSaveTimer.current) window.clearTimeout(manualSaveTimer.current);
    manualSaveTimer.current = window.setTimeout(() => {
      segs.forEach((seg) =>
        segmentsApi
          .setFocus(seg.id, constTrack(seg, cx))
          .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить рамку")),
      );
    }, 350);
  };

  // Triage flags: ⭐ favourite / hide a candidate. Optimistic cache patch + save.
  const setPlanFlag = (planId: number, flags: { favorite?: boolean; hidden?: boolean }) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    if (prev) {
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        // ★ в браузере — выбор владельца: сервер отметит то же (app/agent_settings.py).
        clip_plans: prev.clip_plans.map((p) =>
          p.id === planId ? { ...p, ...flags, ...(flags.favorite === undefined ? {} : { owner_picked: flags.favorite }) } : p,
        ),
      });
    }
    clipPlansApi
      .setFlags(planId, flags)
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"));
  };

  const activePlan = useMemo(() => plans.find((p) => p.id === activePlanId) ?? plans[0], [plans, activePlanId]);
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
  // Превью цвета куска: поправка того куска, что сейчас играет (по секунде исходника).
  const [pieceCss, setPieceCss] = useState("");
  useEffect(() => {
    const v = videoRef.current;
    const segs = activePlan?.segments ?? [];
    if (!v || !segs.some((s) => s.color_fix)) {
      setPieceCss("");
      return;
    }
    const update = () => {
      const t = v.currentTime;
      const seg = segs.find((s) => t >= s.start_sec - 0.01 && t < s.end_sec + 0.01);
      setPieceCss(colorFixCss(seg?.color_fix));
    };
    update();
    v.addEventListener("timeupdate", update);
    v.addEventListener("seeked", update);
    return () => {
      v.removeEventListener("timeupdate", update);
      v.removeEventListener("seeked", update);
    };
  }, [activePlan?.segments, view, query.isSuccess]); // eslint-disable-line react-hooks/exhaustive-deps
  const lookCss = [pieceCss, lookFilterCss(selectedPreset)].filter(Boolean).join(" ");
  // A few moments spread across the video — one frame can hide what a look does.
  const lookFrames = useMemo(() => {
    const all = storyboard.data?.frames ?? [];
    if (all.length <= 3) return all;
    return [0.2, 0.5, 0.8].map((p) => all[Math.floor(all.length * p)]);
  }, [storyboard.data]);

  // Playhead on the moments heat strip: where the source player is right now.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const head = heatHeadRef.current;
      const v = videoRef.current;
      const dur = query.data?.duration_sec ?? 0;
      if (head && v && dur > 0) head.style.left = `${Math.min(100, (v.currentTime / dur) * 100)}%`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [query.data?.duration_sec]);

  // Таймкод клипа под превью. Пишем прямо в DOM из кадра анимации: React-стейт
  // на каждый кадр перерисовывал бы весь редактор 60 раз в секунду.
  const tcSegsRef = useRef<AiSegment[]>([]);
  tcSegsRef.current = activePlan?.segments ?? [];
  // Вырезы и звуки-слои — свойства открытого в монтаже клипа: в списке моментов
  // превью играет другой клип, и чужие вырезы/звуки к нему не относятся.
  const inEditorRef = useRef(false);
  inEditorRef.current = view === "editor";
  // Звуки-слои звучат в превью в такт видео.
  useLayerAudio(videoRef, motionViewRef, () =>
    inEditorRef.current ? clipTimeOf(tcSegsRef.current, videoRef.current?.currentTime ?? 0) : null,
  );
  // Музыка клипа тоже звучит в превью: в такт видео, с той секунды трека, что выбрана для рендера.
  const musicTrack = musicOn && trackId ? tracks.data?.find((t) => t.id === trackId) : undefined;
  useClipMusic(
    videoRef,
    musicTrack && view === "editor"
      ? { url: `/media/audio-tracks/${musicTrack.id}`, startSec: musicStartSec, volume: musicTrack.volume ?? 0.5 }
      : null,
    () => (inEditorRef.current ? clipTimeOf(tcSegsRef.current, videoRef.current?.currentTime ?? 0) : null),
  );
  // Таймкод из чата агента (?clip=<id>&t=<секунда клипа>): один раз встать на эту секунду.
  const seekT = searchParams.get("t");
  const seekedRef = useRef("");
  useEffect(() => {
    const v = videoRef.current;
    const segs = activePlan?.segments ?? [];
    const key = `${activePlan?.id}:${seekT}`;
    if (!v || seekT == null || !segs.length || activePlan?.id !== clipParam || seekedRef.current === key) return;
    const src = clipToSource(segs, Number(seekT));
    if (src == null) return;
    const apply = () => {
      v.currentTime = src;
      seekedRef.current = key;
    };
    if (v.readyState >= 1) apply();
    else v.addEventListener("loadedmetadata", apply, { once: true });
  });
  // Вырезанные паузы превью перепрыгивает на ходу — смотришь клип таким, каким он выйдет.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v && !v.paused && inEditorRef.current && cutsRef.current.length) {
        const t = clipTimeOf(tcSegsRef.current, v.currentTime);
        const jump = t == null ? null : previewSkip(cutsRef.current, t);
        if (jump != null) {
          const src = clipToSource(tcSegsRef.current, jump);
          if (src != null) {
            v.currentTime = src;
            // Прыжок мог перейти в другой кусок — плеер кусков должен знать, где он.
            const pb = playbackRef.current;
            if (pb) {
              const idx = pb.ranges.findIndex((r) => src >= r.start - 0.05 && src < r.end);
              if (idx >= 0) pb.idx = idx;
            }
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  // Громкость исходника на отрезках — в превью тоже: громче 1 тег <video> не умеет, поэтому при
  // подъёмах всё превью тише на самый большой подъём (баланс кусков как в рендере). Свою громкость
  // плеера владелец не теряет: base — то, что он выставил, мы только умножаем.
  useEffect(() => {
    let raf = 0;
    let base: number | null = null;
    let lastSet = -1;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        const spans = inEditorRef.current ? gainRef.current : [];
        if (spans.length) {
          if (base == null || Math.abs(v.volume - lastSet) > 0.005) base = v.volume; // владелец двинул громкость
          const t = clipTimeOf(tcSegsRef.current, v.currentTime);
          const want = t == null ? base : previewVolume(spans, t, base);
          if (Math.abs(v.volume - want) > 0.005) v.volume = want;
          lastSet = v.volume;
        } else if (base != null) {
          v.volume = base;
          base = null;
          lastSet = -1;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      if (base != null && videoRef.current) videoRef.current.volume = base;
    };
  }, []);
  // Заглушённые отрезки превью проигрывает без звука видео — как выйдет в рендере
  // (звуки слоёв и музыка играют). Глушим только то, что заглушили сами: свой
  // «без звука» в плеере владельца не трогаем.
  // Видео-слои с audio: true звучат в превью, только если владелец не заглушил превью
  // (наше автоглушение вырезанных отрезков звук слоёв не трогает — как в рендере).
  const autoMutedRef = useRef(false);
  useEffect(() => {
    setPreviewSound(() => {
      const v = videoRef.current;
      return Boolean(v && (!v.muted || autoMutedRef.current) && v.volume > 0);
    });
    return () => setPreviewSound(() => false);
  }, []);
  // J/L в превью: у стыка звук картинки молчит, а второй плеер того же исходника играет звук
  // соседнего куска (хвост прошлого — L, начало следующего — J), как склеит рендер.
  const jlMutedRef = useRef(false);
  useEffect(() => {
    let raf = 0;
    const side = new Audio();
    side.preload = "auto";
    const tick = () => {
      const v = videoRef.current;
      const joins = audioEditRef.current.joins;
      let src: number | null = null;
      // Свой «без звука» владельца уважаем: глушил плеер он — второй плеер тоже молчит.
      if (v && !v.paused && inEditorRef.current && joins?.length && (!v.muted || autoMutedRef.current)) {
        const t = clipTimeOf(tcSegsRef.current, v.currentTime);
        src = t == null ? null : jlSourceAt(tcSegsRef.current, joins, t);
      }
      if (src != null && v) {
        if (side.src !== v.currentSrc) side.src = v.currentSrc;
        if (Math.abs(side.currentTime - src) > 0.12) side.currentTime = src;
        side.volume = v.volume;
        if (side.paused) void side.play().catch(() => undefined);
        jlMutedRef.current = true;
      } else {
        if (!side.paused) side.pause();
        jlMutedRef.current = false;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      side.pause();
      side.removeAttribute("src");
      jlMutedRef.current = false;
    };
  }, []);
  useEffect(() => {
    let raf = 0;
    let ours = false;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        const t = inEditorRef.current && muteRef.current.length ? clipTimeOf(tcSegsRef.current, v.currentTime) : null;
        const want = (t != null && isMutedAt(muteRef.current, t)) || jlMutedRef.current;
        if (want && !ours && !v.muted) {
          v.muted = true;
          ours = true;
        } else if (!want && ours) {
          v.muted = false;
          ours = false;
        }
        autoMutedRef.current = ours;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      if (ours && videoRef.current) videoRef.current.muted = false;
    };
  }, []);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const el = tcRef.current;
      const v = videoRef.current;
      if (el && v) {
        const t = clipTimeOf(tcSegsRef.current, v.currentTime);
        const text = t == null ? "—" : formatTimecode(t);
        if (el.textContent !== text) el.textContent = text;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

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
  // ✂ и 🗑 на таймлайне. Куски живут в клип-файле, поэтому правим его целиком
  // через тот же эндпоинт, что и ручная правка: одна проверка, один снимок для Ctrl+Z.
  const editPieces = useMutation({
    mutationFn: async (make: (pieces: SpecPiece[]) => SpecPiece[]) => {
      if (!activePlan) throw new ApiError(400, "клип не открыт");
      // Несохранённые правки панели — сначала на сервер, иначе перезагрузка их потеряет.
      await flushSettings();
      const spec = (await clipPlansApi.getSpec(activePlan.id)) as Record<string, unknown>;
      const pieces = make(spec.pieces as SpecPiece[]);
      if (!pieces.length) throw new ApiError(400, "в клипе должен остаться хотя бы один кусок");
      return clipPlansApi.putSpec(activePlan.id, { ...spec, pieces });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не вышло изменить куски"),
  });

  return {
    ...editorShortcuts,
    ...draftsFilters,
    ...panelSettingsState,
    sourceId, qc, toast, libTabs, videoRef, phoneRef, phoneOverlayRef, heatHeadRef, tcRef, subLinesRef, subWordsRef,
    bannerImgRef, bannerGeomRef, subStyleRef, subPosPctRef, inspScrollRef, assetMediaRef, triagePhoneRef, query,
    recordMissing, proxyReady, sourceMissing, banners, tracks, subs, assetsQuery, pickableAssets, navigate, view,
    playbackRef, playRanges, stopPlayback, openEditor, backToMoments, musicPreviewRef, musicFromUrl, reframedView,
    motionViewRef, inlineCancelRef, planChat, planVoice, planLevels, motionFonts, clipNowRef, clipNow,
    reframeHintRef, layerDragRef, insertsPreviewRef, insertDragRef, hoverPlanRef, planWave, planSubs, plans,
    composeCover, compile, batchAuto, autoEdit, mixReport, autoMix, censorMat, brands, applyBrand, undoEdit,
    segmentSpeed, snapBeats, pieceColor, matchColors, batch, focusOptions, setFocus, cutStrategies, refineCuts,
    autofocus, commit, pickPointRef, setFixedFrame, setPlanFlag, activePlan, uniquePresets, selectedPreset, lookCss,
    lookFrames, tcSegsRef, editPieces, libTab, setLibTab, tlSelected, setTlSelected, inspTab, setInspTab, subLines,
    setSubLines, mediaSrc, activePlanId, setActivePlanId, selectedSeg, setSelectedSeg, loop, setLoop, rate, setRate,
    musicUrl, setMusicUrl, inlineEdit, setInlineEdit, coverUploading, setCoverUploading, clipFileOpen,
    setClipFileOpen, watchOpen, setWatchOpen, aiMontageOpen, setAiMontageOpen, aiGoal, setAiGoal, setSubsWide,
    useVlmFocus, setUseVlmFocus, packagePieces, setPackagePieces, coverTitle, setCoverTitle, coverStyle,
    setCoverStyle, autoProgress, censorStyle, setCensorStyle, brandPick, setBrandPick, snapMode, setSnapMode,
    pickingPoint, setPickingPoint, moduleOn, subsWide,
  };
}

export type DraftsEditorState = ReturnType<typeof useDraftsEditorState>;
