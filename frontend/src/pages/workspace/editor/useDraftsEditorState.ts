import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { sourcesApi } from "@/api/sources";
import { clipPlansApi, type SubtitleLine } from "@/api/clipPlans";
import { segmentsApi } from "@/api/segments";
import { ffmpegPresetsApi, bannersApi, audioTracksApi, subtitleProfilesApi, montageAssetsApi } from "@/api/assets";
import { qk } from "@/api/keys";
import { brandsApi } from "@/api/brands";
import { type AiSegment, type Aspect, type ColorFix, type CoverSettings, type FreezeFrame, type MixSettings, type MontageInsert, type FocusPoint, type RenderSettings, type SourceDetail, type SpeedAudio, type TransitionSettings, colorFixCss } from "@/api/types";
import { audioMixApi } from "@/api/audioMix";
import { api, ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { isSourceFileMissing } from "@/components/MissingSourceNotice";
import { useModules } from "@/hooks/useModules";
import { readPref } from "@/pages/workspace/InspectorParts";
import { type SubtitleStyle, type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { frameSize, layersFormat, reframeBetween, type AssetSizes, type CameraKey, type ChatMessage, type Layer, type PngtuberLayer, clipFormat } from "@/motion";
import { motionApi } from "@/api/motion";
import { layerAssetSizes, loadMotionFonts, preloadLayerImages } from "@/pages/workspace/motion/runtime";
import { type TransformHandle } from "@/pages/workspace/motion/edit";
import { DEFAULT_LAYOUT, normalizeLayout, type StreamLayout } from "@/pages/workspace/layout";
import { DEFAULT_BACKDROP, normalizeBackdrop, type Backdrop } from "@/pages/workspace/chromaKey";
import { previewSkip, type JumpCut } from "@/api/cuts";
import { CENSOR_STYLES, isMutedAt, type CensorStyle, type MuteSpan } from "@/api/mute";
import { previewVolume, type GainSpan } from "@/api/gain";
import { compactAudio, jlSourceAt, type AudioEdit } from "@/api/audioEdit";
import { useLayerAudio } from "@/pages/workspace/motion/useLayerAudio";
import { playbackApi } from "@/api/playback";
import { useClipMusic } from "@/pages/workspace/preview/useClipMusic";
import { useClipRevision } from "@/pages/workspace/preview/MontagePlayer";
import { lookFilterCss, type InsertPreview, type MotionView } from "@/pages/workspace/preview/frame";
import { setPreviewSound } from "@/pages/workspace/motion/sound";
import { clipToSource, type Selection } from "@/pages/workspace/ClipTimeline";
import { clipTimeOf, DEFAULT_COVER, DEFAULT_TRANSITION, formatTimecode, INSP_TABS, LIB_TABS, type InspTab, type LibTab, type SpecPiece } from "@/pages/workspace/editor/editorHelpers";

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
  const [chosen, setChosen] = useState<Set<number> | null>(null);
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

  // render options
  const [presetId, setPresetId] = useState(0);
  const [subsOn, setSubsOn] = useState(false);
  const [subId, setSubId] = useState(0);
  const [subEngine, setSubEngine] = useState(""); // "" = из стиля, иначе whisper/gemini
  const [bannerOn, setBannerOn] = useState(false);
  const [bannerId, setBannerId] = useState(0);
  const [musicOn, setMusicOn] = useState(false);
  const [trackId, setTrackId] = useState(0);
  // С какой секунды трека музыка играет на 0 с клипа (сразу с барабанов, без вступления).
  const [musicStartSec, setMusicStartSec] = useState(0);
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
  const [mirror, setMirror] = useState(false);
  // How the pieces of a multi-piece clip are joined, and the sound on the join.
  const [transition, setTransition] = useState<TransitionSettings>(DEFAULT_TRANSITION);
  const patchTransition = (next: Partial<TransitionSettings>) => setTransition((t) => ({ ...t, ...next }));
  // Cover of the clip (see app/cover.py): a frame of the clip or an uploaded picture.
  const [cover, setCover] = useState<CoverSettings>(DEFAULT_COVER);
  // «Файлы для монтажа» placed in this clip (clip-timeline seconds).
  const [inserts, setInserts] = useState<MontageInsert[]>([]);
  // Моушн-слои и камера клипа — часть его настроек рендера (как вставки).
  const [layers, setLayers] = useState<Layer[]>([]);
  const [camera, setCamera] = useState<CameraKey[]>([]);
  const [layout, setLayout] = useState<StreamLayout>(DEFAULT_LAYOUT);
  const [cuts, setCuts] = useState<JumpCut[]>([]);
  const [backdrop, setBackdrop] = useState<Backdrop>(DEFAULT_BACKDROP);
  // 🔇 Тишина вместо звука исходника (app/audio_mute.py): полосы на дорожке «Звук»
  // таймлайна; агент ставит их же инструментом mute_audio.
  const [mute, setMute] = useState<MuteSpan[]>([]);
  // Стоп-кадры посреди ролика ставит агент (freeze_frame): панель их не правит, но сохраняет как есть.
  const [freezes, setFreezes] = useState<FreezeFrame[]>([]);
  const cutsRef = useRef<JumpCut[]>([]);
  cutsRef.current = cuts;
  const muteRef = useRef<MuteSpan[]>([]);
  muteRef.current = mute;
  // 🔊 Громкость звука исходника на отрезках (render.gain): поднять тихий кусок, приглушить
  // громкий; агент ставит её инструментом set_source_gain.
  const [gain, setGain] = useState<GainSpan[]>([]);
  const gainRef = useRef<GainSpan[]>([]);
  gainRef.current = gain;
  // 🎚 Звуковой монтаж (render.audio, app/audio_edit.py): J/L на стыках тянут на дорожке «Звук»,
  // ключи громкости, ducking, провалы музыки и эффекты ставит агент — панель их показывает и хранит.
  const [audioEdit, setAudioEdit] = useState<AudioEdit>({});
  // 🎚 Сведение (render_settings.mix: auto_mix / set_mix / эффекты голоса и музыки) — целиком, как пришло.
  const [mixSettings, setMixSettings] = useState<MixSettings>({});
  const audioEditRef = useRef<AudioEdit>({});
  audioEditRef.current = audioEdit;
  // Формат вывода клипа (app/formats.py): 9:16, 1:1, 4:5 или 16:9 из того же монтажа.
  // Превью, холст слоёв и окно кропа — в пропорциях этого формата.
  const [aspect, setAspect] = useState<Aspect>("9:16");
  // В каком формате размечены слои (render.layers_format, их опорный кадр). Не совпадает с
  // форматом вывода — превью показывает перекомпоновку, как сделает рендер (motion/reframe.ts).
  const [layersFmt, setLayersFmt] = useState<Aspect>("9:16");
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
  // Стоп-кадр в конце готового ролика, с (render.end_hold_sec): null — как у сервиса, 0 — мем без «замирания».
  const [endHoldSec, setEndHoldSec] = useState<number | null>(null);
  // Motion blur клипа по умолчанию (render.motion_blur), затвор 0..1: null — выключен.
  const [motionBlur, setMotionBlur] = useState<number | null>(null);
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
  // Перетаскивание мема по кадру: что схватили и с чего начали.
  const insertsPreviewRef = useRef<InsertPreview[]>([]);
  const insertDragRef = useRef<
    { index: number; mode: "move" | "resize"; startX: number; startY: number; from: { x: number; y: number; scale: number } } | null
  >(null);
  const patchCover = (next: Partial<CoverSettings>) => setCover((c) => ({ ...c, ...next }));
  const [coverUploading, setCoverUploading] = useState(false);
  // True while a clip's saved settings are being poured into the panel, so the
  // panel's own effects (autosave, subtitle position) don't react to it.
  const loadingSettingsRef = useRef(false);
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
  const keyHandlerRef = useRef<((e: KeyboardEvent) => void) | null>(null);
  const [aiMontageOpen, setAiMontageOpen] = useState(false);
  const [settingsNonce, setSettingsNonce] = useState(0);
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
  // Ctrl+Z: снимок состояния делает сервер перед каждой правкой, здесь только
  // просим шаг назад и перечитываем клип в панель.
  const undoRef = useRef<() => void>(() => undefined);
  // Ctrl+D — копия выделенного слоя (ставится обработчиком последнего рендера).
  const duplicateRef = useRef<() => void>(() => undefined);
  const copyLayerRef = useRef<() => boolean>(() => false);
  const pasteLayerRef = useRef<() => boolean>(() => false);
  const nudgeLayerRef = useRef<(key: string, big: boolean) => boolean>(() => false);
  const [useVlmFocus, setUseVlmFocus] = useState(false);
  const [packagePieces, setPackagePieces] = useState(false);
  const [hideDuplicates, setHideDuplicates] = useState(false);
  const [candidateSearch, setCandidateSearch] = useState("");
  const [hiddenAnalyses, setHiddenAnalyses] = useState<Set<number>>(new Set());
  const [onlyFavorites, setOnlyFavorites] = useState(false);
  const [showHidden, setShowHidden] = useState(false);

  // Safe-zone overlay on the preview: where the banner sits (top) and where the
  // subtitles land (bottom). Percentages of the final 9:16 frame height.
  const [showZones, setShowZones] = useState(true);
  const [bannerHeightPct, setBannerHeightPct] = useState(14);
  const [bannerPosPct, setBannerPosPct] = useState(4); // верх баннера, % сверху
  const [subPosPct, setSubPosPct] = useState(12);

  // When a subtitle style is picked, derive its vertical position from margin_v
  // (ASS units on a 1920-tall frame) so the band matches the real output.
  useEffect(() => {
    // A clip's saved settings carry their own position — loading them must not
    // be overridden by the style's default right after.
    if (loadingSettingsRef.current) return;
    const p = subs.data?.find((s) => s.id === subId);
    if (p && typeof p.margin_v === "number") {
      setSubPosPct(Math.round(Math.min(40, Math.max(2, (p.margin_v / 1920) * 100))));
    }
  }, [subId, subs.data]);

  const plans = query.data?.clip_plans ?? [];

  // Default-select every plan for batch render once they load.
  useEffect(() => {
    if (chosen === null && plans.length) setChosen(new Set(plans.map((p) => p.id)));
  }, [plans.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = chosen ?? new Set<number>();

  // ---- per-clip settings: each clip is set up on its own -------------------
  const transitionOpts = useQuery({
    queryKey: ["transition-options"],
    queryFn: clipPlansApi.transitionOptions,
    staleTime: Infinity,
  });
  const panelSettings = (): RenderSettings => ({
    preset_id: presetId || null,
    subs_on: subsOn,
    sub_id: subId || null,
    sub_engine: subEngine,
    sub_pos_pct: subPosPct,
    banner_on: bannerOn,
    banner_id: bannerId || null,
    banner_height_pct: bannerHeightPct,
    banner_pos_pct: bannerPosPct,
    music_on: musicOn,
    track_id: trackId || null,
    music_start_sec: musicStartSec,
    mirror,
    transition,
    cover,
    inserts,
    layers,
    camera,
    layout,
    cuts,
    backdrop,
    mute,
    gain,
    audio: audioEdit,
    format: aspect,
    layers_format: layersFmt,
    end_hold_sec: endHoldSec,
    motion_blur: motionBlur,
    freezes,
    mix: mixSettings,
  });
  const settingsKey = JSON.stringify(panelSettings());

  const lastJlKeyRef = useRef<Record<number, string>>({});
  // Что сервер последним принял от этой вкладки: правка извне (агент, другая вкладка) — всё остальное.
  const lastServerSettingsRef = useRef<Record<number, string>>({});
  const saveInFlightRef = useRef(0);
  const saveSettings = (planId: number, settings: RenderSettings) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    if (prev) {
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        // Концовку ставит агент (set_end_screen) — панель её не шлёт, сервер её хранит; в кэше тоже держим.
        clip_plans: prev.clip_plans.map((p) =>
          p.id === planId ? { ...p, render_settings: { end_screen: p.render_settings?.end_screen, ...settings } } : p,
        ),
      });
    }
    const jlKey = JSON.stringify(
      ((settings as { audio?: { joins?: unknown } }).audio?.joins ?? null),
    );
    saveInFlightRef.current += 1;
    return clipPlansApi
      .setRenderSettings(planId, settings)
      .then((r) => {
        lastServerSettingsRef.current[planId] = JSON.stringify(r.render_settings);
        // J/L поменялись — субтитры превью (heard_words) идут за звуком: перечитать.
        if (jlKey !== lastJlKeyRef.current[planId]) {
          lastJlKeyRef.current[planId] = jlKey;
          void qc.invalidateQueries({ queryKey: ["plan-subtitles", planId] });
        }
        return r;
      })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить настройки клипа"))
      .finally(() => {
        saveInFlightRef.current -= 1;
      });
  };

  // Autosave with a snapshot: the timer must save what was on the panel FOR THAT
  // clip, not whatever the panel shows when it fires (you may have switched clips).
  const pendingSaveRef = useRef<{ planId: number; settings: RenderSettings; timer: number } | null>(null);
  // Пока ИИ-ассистент правит клип на сервере, панель не сохраняется: иначе
  // автосохранение затёрло бы его правки старым состоянием редактора.
  const agentRunningRef = useRef(false);
  const flushSettings = async () => {
    const pending = pendingSaveRef.current;
    if (!pending) return;
    pendingSaveRef.current = null;
    window.clearTimeout(pending.timer);
    await saveSettings(pending.planId, pending.settings);
  };

  // Switching clips: save the previous clip's pending edits, then load this
  // clip's own settings. A clip never set up keeps the panel as its start.
  useEffect(() => {
    void flushSettings();
    if (!editorPlanId) return;
    const s = plans.find((p) => p.id === editorPlanId)?.render_settings;
    if (s) lastServerSettingsRef.current[editorPlanId] = JSON.stringify(s);
    if (!s) {
      // Ручки панели остаются стартом для нового клипа, но то, что лежит на
      // таймлайне прошлого клипа (слои, вставки), сюда не переезжает.
      setLayers([]);
      setCamera([]);
      setInserts([]);
      setCuts([]);
      setMute([]);
      setGain([]);
      setAudioEdit({});
      setMixSettings({});
      setFreezes([]);
      // Обложка — тоже своя у клипа: титр чужой обложки не должен вшиться сюда.
      setCover(DEFAULT_COVER);
      setTlSelected(null);
      return;
    }
    // Same key order as panelSettings(): if the clip's settings already equal the
    // panel, nothing changes, the autosave effect never runs — and a raised
    // "loading" flag would then swallow the user's NEXT real edit.
    const loaded: RenderSettings = {
      preset_id: s.preset_id ?? null,
      subs_on: s.subs_on,
      sub_id: s.sub_id ?? null,
      sub_engine: s.sub_engine ?? "",
      sub_pos_pct: s.sub_pos_pct,
      banner_on: s.banner_on,
      banner_id: s.banner_id ?? null,
      banner_height_pct: s.banner_height_pct,
      banner_pos_pct: s.banner_pos_pct,
      music_on: s.music_on,
      track_id: s.track_id ?? null,
      music_start_sec: s.music_start_sec ?? 0,
      mirror: s.mirror,
      transition: { ...DEFAULT_TRANSITION, ...(s.transition ?? {}) },
      cover: { ...DEFAULT_COVER, ...(s.cover ?? {}) },
      inserts: s.inserts ?? [],
      layers: s.layers ?? [],
      camera: s.camera ?? [],
      layout: normalizeLayout(s.layout),
      cuts: s.cuts ?? [],
      backdrop: normalizeBackdrop(s.backdrop),
      mute: s.mute ?? [],
      gain: s.gain ?? [],
      audio: compactAudio(s.audio),
      format: clipFormat(s),
      layers_format: layersFormat(s),
      end_hold_sec: s.end_hold_sec ?? null,
      motion_blur: s.motion_blur ?? null,
      freezes: s.freezes ?? [],
      mix: s.mix ?? {},
    };
    if (JSON.stringify(loaded) === settingsKey) return;
    loadingSettingsRef.current = true;
    setPresetId(s.preset_id ?? 0);
    setSubsOn(s.subs_on);
    setSubId(s.sub_id ?? 0);
    setSubEngine(s.sub_engine ?? "");
    setSubPosPct(s.sub_pos_pct);
    setBannerOn(s.banner_on);
    setBannerId(s.banner_id ?? 0);
    setBannerHeightPct(s.banner_height_pct);
    setBannerPosPct(s.banner_pos_pct);
    setMusicOn(s.music_on);
    setTrackId(s.track_id ?? 0);
    setMusicStartSec(s.music_start_sec ?? 0);
    setMirror(s.mirror);
    setTransition({ ...DEFAULT_TRANSITION, ...(s.transition ?? {}) });
    setCover({ ...DEFAULT_COVER, ...(s.cover ?? {}) });
    setInserts(s.inserts ?? []);
    setLayers(s.layers ?? []);
    setCamera(s.camera ?? []);
    setLayout(normalizeLayout(s.layout));
    setCuts(s.cuts ?? []);
    setBackdrop(normalizeBackdrop(s.backdrop));
    setMute(s.mute ?? []);
    setFreezes(s.freezes ?? []);
    setGain(s.gain ?? []);
    setAudioEdit(compactAudio(s.audio));
    setMixSettings(s.mix ?? {});
    setAspect(clipFormat(s));
    setLayersFmt(layersFormat(s));
    setEndHoldSec(s.end_hold_sec ?? null);
    setMotionBlur(s.motion_blur ?? null);
    // Выделение — номер куска/строки/вставки: в другом клипе (или после отката)
    // он указывал бы на чужой блок, и Delete удалил бы не то.
    setTlSelected(null);
  }, [editorPlanId, query.isSuccess, settingsNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (loadingSettingsRef.current) {
      loadingSettingsRef.current = false;
      return;
    }
    if (!editorPlanId || agentRunningRef.current) return;
    const settings = panelSettings();
    if (pendingSaveRef.current) window.clearTimeout(pendingSaveRef.current.timer);
    const timer = window.setTimeout(() => void flushSettings(), 600);
    pendingSaveRef.current = { planId: editorPlanId, settings, timer };
  }, [settingsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Правка мимо этой вкладки (MCP-агент, другая вкладка): новые настройки, куски и субтитры — сами,
  // без перезагрузки. Свою запись узнаём по ответу сервера: она ничего в панели не сбрасывает.
  useClipRevision(editorPlanId, async () => {
    const planId = editorPlanId;
    if (!planId || pendingSaveRef.current || saveInFlightRef.current || agentRunningRef.current || layerDragRef.current) return;
    await Promise.all([
      qc.invalidateQueries({ queryKey: qk.source(sourceId) }),
      qc.invalidateQueries({ queryKey: ["plan-subtitles", planId] }),
    ]);
    const fresh = qc.getQueryData<SourceDetail>(qk.source(sourceId))?.clip_plans.find((p) => p.id === planId);
    const json = JSON.stringify(fresh?.render_settings);
    if (json !== lastServerSettingsRef.current[planId] && !pendingSaveRef.current) {
      lastServerSettingsRef.current[planId] = json;
      setSettingsNonce((n) => n + 1);
    }
  }, view === "editor");

  const applyToFavorites = async () => {
    const targets = plans.filter((p) => p.favorite && p.id !== editorPlanId);
    if (!targets.length) {
      toast.error("Нет других избранных клипов");
      return;
    }
    const settings = panelSettings();
    // Inserts are placed on each clip's own timeline — never copy them across.
    await Promise.all(
      targets.map((p) =>
        saveSettings(p.id, {
          ...settings,
          inserts: p.render_settings?.inserts ?? [],
          layers: p.render_settings?.layers ?? [],
          camera: p.render_settings?.camera ?? [],
          // Вырезы — диапазоны времени ЭТОГО клипа, обложка-картинка — его кадр: не копируем.
          cuts: p.render_settings?.cuts ?? [],
          mute: p.render_settings?.mute ?? [],
          gain: p.render_settings?.gain ?? [],
          freezes: p.render_settings?.freezes ?? [],
          cover: settings.cover.mode === "image" ? (p.render_settings?.cover ?? DEFAULT_COVER) : settings.cover,
        }),
      ),
    );
    toast.success(`Настройки скопированы в избранные: ${targets.length}`);
  };

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
  // Candidates accumulate across analyses — group them so it's clear which run
  // produced what (newest run first).
  const planGroups = useMemo(() => {
    const q = candidateSearch.trim().toLowerCase();
    const by = new Map<number, typeof plans>();
    for (const p of plans) {
      if (hideDuplicates && p.duplicate_of != null) continue;
      if (!showHidden && p.hidden) continue;
      if (onlyFavorites && !p.favorite) continue;
      if (q && !(p.title ?? "").toLowerCase().includes(q)) continue;
      const key = p.analysis_id ?? 0;
      by.set(key, [...(by.get(key) ?? []), p]);
    }
    // Best candidates first inside each analysis.
    for (const [, group] of by) group.sort((a, b) => (b.quality ?? 0) - (a.quality ?? 0));
    return [...by.entries()]
      .filter(([analysisId]) => !hiddenAnalyses.has(analysisId))
      .sort((a, b) => b[0] - a[0]);
  }, [plans, hideDuplicates, candidateSearch, hiddenAnalyses, onlyFavorites, showHidden]);
  const shownCount = planGroups.reduce((n, [, g]) => n + g.length, 0);
  const favCount = plans.filter((p) => p.favorite).length;
  const hiddenCount = plans.filter((p) => p.hidden).length;
  // All analyses present (for the show/hide filter chips), independent of search/hide.
  const analysisChips = useMemo(() => {
    const by = new Map<number, number>();
    for (const p of plans) by.set(p.analysis_id ?? 0, (by.get(p.analysis_id ?? 0) ?? 0) + 1);
    return [...by.entries()].sort((a, b) => b[0] - a[0]);
  }, [plans]);
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
  const analysisMeta = (analysisId: number) => {
    if (!analysisId) return "Добавлены вручную";
    const a = query.data?.analyses?.find((x) => x.id === analysisId);
    return a ? `Анализ #${a.id} · ${a.provider}${a.model ? ` · ${a.model}` : ""}` : `Анализ #${analysisId}`;
  };
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

  // Keyboard: F ★ / X hide / E edit the moment under the pointer; in the editor
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

  // F stars the open clip and Esc goes back to the moments (see «?» in the shell).
  // One stable listener (a hook must not sit after the early returns below); it
  // calls whatever handler the latest render left in keyHandlerRef.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "z") {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        e.preventDefault();
        undoRef.current();
        return;
      }
      // Ctrl+C / Ctrl+V — слой в буфер и обратно (в том числе в другой клип).
      if ((e.ctrlKey || e.metaKey) && !e.altKey && ["c", "v", "с", "м"].includes(e.key.toLowerCase())) {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        if (window.getSelection()?.toString()) return;
        const copy = ["c", "с"].includes(e.key.toLowerCase());
        if ((copy ? copyLayerRef : pasteLayerRef).current()) e.preventDefault();
        return;
      }
      // Alt+стрелки — подвинуть выделенный слой (Shift — крупнее).
      if (e.altKey && !e.ctrlKey && e.key.startsWith("Arrow")) {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
        if (nudgeLayerRef.current(e.key, e.shiftKey)) e.preventDefault();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "d") {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        e.preventDefault();
        duplicateRef.current();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      if (document.querySelector(".modal-backdrop, .drawer-backdrop")) return;
      keyHandlerRef.current?.(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
    sourceId, qc, toast, libTabs, videoRef, phoneRef, phoneOverlayRef, heatHeadRef, tcRef, subLinesRef, subWordsRef,
    bannerImgRef, bannerGeomRef, subStyleRef, subPosPctRef, inspScrollRef, assetMediaRef, triagePhoneRef, query,
    recordMissing, proxyReady, sourceMissing, banners, tracks, subs, assetsQuery, pickableAssets, navigate, view,
    playbackRef, playRanges, stopPlayback, openEditor, backToMoments, musicPreviewRef, musicFromUrl, patchTransition,
    reframedView, motionViewRef, inlineCancelRef, planChat, planVoice, planLevels, motionFonts, clipNowRef, clipNow,
    reframeHintRef, layerDragRef, insertsPreviewRef, insertDragRef, patchCover, hoverPlanRef, keyHandlerRef,
    planWave, planSubs, undoRef, duplicateRef, copyLayerRef, pasteLayerRef, nudgeLayerRef, plans, selected,
    transitionOpts, agentRunningRef, flushSettings, applyToFavorites, composeCover, compile, batchAuto, autoEdit,
    mixReport, autoMix, censorMat, brands, applyBrand, undoEdit, segmentSpeed, snapBeats, pieceColor, matchColors,
    batch, focusOptions, setFocus, cutStrategies, refineCuts, autofocus, commit, pickPointRef, setFixedFrame,
    setPlanFlag, activePlan, planGroups, shownCount, favCount, hiddenCount, analysisChips, uniquePresets,
    analysisMeta, selectedPreset, lookCss, lookFrames, tcSegsRef, editPieces, libTab, setLibTab, tlSelected,
    setTlSelected, inspTab, setInspTab, subLines, setSubLines, mediaSrc, activePlanId, setActivePlanId, selectedSeg,
    setSelectedSeg, setChosen, loop, setLoop, rate, setRate, presetId, setPresetId, subsOn, setSubsOn, subId,
    setSubId, subEngine, setSubEngine, bannerOn, setBannerOn, bannerId, setBannerId, musicOn, setMusicOn, trackId,
    setTrackId, musicStartSec, setMusicStartSec, musicUrl, setMusicUrl, mirror, setMirror, transition, cover,
    inserts, setInserts, layers, setLayers, camera, setCamera, layout, setLayout, cuts, setCuts, backdrop,
    setBackdrop, mute, setMute, gain, setGain, audioEdit, setAudioEdit, mixSettings, setMixSettings, aspect,
    setAspect, layersFmt, setLayersFmt, endHoldSec, setEndHoldSec, motionBlur, setMotionBlur, inlineEdit,
    setInlineEdit, coverUploading, setCoverUploading, clipFileOpen, setClipFileOpen, watchOpen, setWatchOpen,
    aiMontageOpen, setAiMontageOpen, setSettingsNonce, aiGoal, setAiGoal, setSubsWide, useVlmFocus, setUseVlmFocus,
    packagePieces, setPackagePieces, hideDuplicates, setHideDuplicates, candidateSearch, setCandidateSearch,
    hiddenAnalyses, setHiddenAnalyses, onlyFavorites, setOnlyFavorites, showHidden, setShowHidden, showZones,
    setShowZones, bannerHeightPct, setBannerHeightPct, bannerPosPct, setBannerPosPct, subPosPct, setSubPosPct,
    coverTitle, setCoverTitle, coverStyle, setCoverStyle, autoProgress, censorStyle, setCensorStyle, brandPick,
    setBrandPick, snapMode, setSnapMode, pickingPoint, setPickingPoint, moduleOn, subsWide,
  };
}

export type DraftsEditorState = ReturnType<typeof useDraftsEditorState>;
