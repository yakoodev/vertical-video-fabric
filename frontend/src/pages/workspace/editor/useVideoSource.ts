import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { sourcesApi } from "@/api/sources";
import { ffmpegPresetsApi, bannersApi, audioTracksApi, subtitleProfilesApi } from "@/api/assets";
import { qk } from "@/api/keys";
import { isSourceFileMissing } from "@/components/MissingSourceNotice";
import { playbackApi } from "@/api/playback";

/**
 * Видео и его окружение: запись, прокси, чем играть превью, пресеты, баннеры, треки, стили субтитров, кадры.
 */
export function useVideoSource({
  sourceId,
}: {
  sourceId: string;
}) {
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

  return {
    query, plans, sourceMissing, presets, banners, tracks, subs, storyboard, recordMissing, proxyReady, mediaSrc,
  };
}
