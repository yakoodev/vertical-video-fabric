import { useEffect, useRef, useState } from "react";
import { type UseQueryResult } from "@tanstack/react-query";
import { type JumpCut, previewSkip } from "@/api/cuts";
import { type MuteSpan, isMutedAt } from "@/api/mute";
import { type GainSpan, previewVolume } from "@/api/gain";
import { type AudioEdit, jlSourceAt } from "@/api/audioEdit";
import { type AiSegment, type SourceDetail, colorFixCss, type ClipPlan } from "@/api/types";
import { useLayerAudio } from "@/pages/workspace/motion/useLayerAudio";
import { useClipMusic } from "@/pages/workspace/preview/useClipMusic";
import { type MotionView } from "@/pages/workspace/preview/frame";
import { setPreviewSound } from "@/pages/workspace/motion/sound";
import { clipToSource } from "@/pages/workspace/ClipTimeline";
import { clipTimeOf, formatTimecode } from "@/pages/workspace/editor/editorHelpers";
import { usePreviewPlayer } from "@/pages/workspace/editor/usePreviewPlayer";
import { type AudioTrack } from "@/api/assets";

/**
 * Что превью делает на каждом кадре, пока идёт видео: метка на полосе, таймкод, прыжок через вырезы, громкость
 * на отрезках, тишина, J/L, музыка и звуки слоёв. Всё пишется прямо в DOM и в плеер, минуя React. Порядок важен:
 * цикл J/L выставляет флаг, который следом читает цикл тишины.
 */
export function usePreviewLoops({
  videoRef, heatHeadRef, tcRef, query, tracks, clipParam, view, motionViewRef, activePlan, searchParams, playbackRef,
  cutsRef, muteRef, gainRef, audioEditRef, musicOn, trackId, musicStartSec,
}: {
  videoRef: React.RefObject<HTMLVideoElement>;
  heatHeadRef: React.RefObject<HTMLSpanElement>;
  tcRef: React.RefObject<HTMLSpanElement>;
  query: UseQueryResult<SourceDetail>;
  tracks: UseQueryResult<AudioTrack[]>;
  clipParam: number | null;
  view: "triage" | "editor";
  motionViewRef: React.MutableRefObject<MotionView>;
  activePlan: ClipPlan | undefined;
  searchParams: URLSearchParams;
  playbackRef: ReturnType<typeof usePreviewPlayer>["playbackRef"];
  cutsRef: React.MutableRefObject<JumpCut[]>;
  muteRef: React.MutableRefObject<MuteSpan[]>;
  gainRef: React.MutableRefObject<GainSpan[]>;
  audioEditRef: React.MutableRefObject<AudioEdit>;
  musicOn: boolean;
  trackId: number;
  musicStartSec: number;
}) {
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

  return {
    pieceCss, tcSegsRef,
  };
}
