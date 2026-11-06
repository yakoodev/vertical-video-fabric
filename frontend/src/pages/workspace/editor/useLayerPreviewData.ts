import { useEffect, useMemo, useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useModules } from "@/hooks/useModules";
import { reframeBetween, type AssetSizes, type ChatMessage, type Layer, type PngtuberLayer, type CameraKey } from "@/motion";
import { motionApi } from "@/api/motion";
import { layerAssetSizes, loadMotionFonts, preloadLayerImages } from "@/pages/workspace/motion/runtime";
import { type MontageAsset } from "@/api/assets";
import { type Aspect } from "@/api/types";

/**
 * Что нужно превью слоёв помимо самих слоёв: перенос в другой формат, шрифты и картинки, чат, голос и громкость
 * клипа. Запросы идут, только когда соответствующий слой есть.
 */
export function useLayerPreviewData({
  assetsQuery, editorPlanId, musicOn, trackId, layers, camera, aspect, layersFmt,
}: {
  assetsQuery: UseQueryResult<MontageAsset[]>;
  editorPlanId: number | null;
  musicOn: boolean;
  trackId: number;
  layers: Layer[];
  camera: CameraKey[];
  aspect: Aspect;
  layersFmt: Aspect;
}) {
  const { on: moduleOn } = useModules();
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

  return {
    reframedView, planChat, planVoice, planLevels, motionFonts,
  };
}
