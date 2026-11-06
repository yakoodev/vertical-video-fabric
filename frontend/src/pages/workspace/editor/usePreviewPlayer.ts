import { useEffect, useRef, useState } from "react";
import { type UseQueryResult } from "@tanstack/react-query";
import { type SourceDetail } from "@/api/types";

/**
 * Плеер исходника: куски клипа подряд, скорость, зацикливание, парковка на первом кадре открытого клипа.
 * Сам <video> у каждого экрана свой (ссылка общая), поэтому слушатель перевешивается при смене экрана.
 */
export function usePreviewPlayer({
  videoRef, query, sourceMissing, view, editorPlanId,
}: {
  videoRef: React.RefObject<HTMLVideoElement>;
  query: UseQueryResult<SourceDetail>;
  sourceMissing: boolean;
  view: "triage" | "editor";
  editorPlanId: number | null;
}) {
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

  return {
    playbackRef, stopPlayback, playRanges, loop, setLoop, rate, setRate,
  };
}
