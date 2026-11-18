import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { playbackApi } from "@/api/playback";
import { type ClipPlan } from "@/api/types";
import { clipToSource } from "@/pages/workspace/ClipTimeline";
import { clipTimeOf } from "@/pages/workspace/editor/clipTime";
import { clipToMontage, montageToClip } from "@/pages/workspace/preview/montageClock";

/** Столько стоит на паузе — плеер ролика закрываем, чтобы сервер не собирал монтаж впустую, мс. */
const IDLE_UNMOUNT_MS = 60_000;
/** Предел ожидания кадра редактора после паузы: дальше показываем его как есть, мс. */
const HANDOFF_MAX_MS = 8000;
/** Столько ждём ответа браузера на «весь экран», мс. */
const FULLSCREEN_WAIT_MS = 1500;

type Full = "none" | "native" | "fallback";

/**
 * ▶ Смотреть монтаж прямо в телефоне редактора: настоящий ролик (рендер 480p во время показа, MontagePlayer)
 * поверх кадра правки, плейхед таймлайна и таймкод идут за ним, клик по таймлайну перематывает ролик.
 *
 * Положение между плеями хранит плеер записи (как и раньше: время клипа). Плей — ролик с той же секунды клипа;
 * пауза — запись встаёт на ту же секунду, и ролик прячется, когда кадр правки уже нарисован (без скачка).
 * Полный экран — тот же плеер (тот же <video>), Esc возвращает в страницу на той же секунде.
 */
export function useInlineMontage({
  videoRef, activePlan, view, sourceMissing, sourceReady, loop, rate, flushSettings, stopPlayback,
}: {
  videoRef: React.RefObject<HTMLVideoElement>;
  activePlan: ClipPlan | undefined;
  view: "triage" | "editor";
  sourceMissing: boolean;
  sourceReady: boolean;
  loop: boolean;
  rate: number;
  flushSettings: () => Promise<unknown>;
  stopPlayback: () => void;
}) {
  const qc = useQueryClient();
  const planId = view === "editor" && activePlan ? activePlan.id : null;
  const rootRef = useRef<HTMLDivElement>(null);
  // Плеер ролика смонтирован (сервер собирает монтаж, пока он открыт).
  const [mounted, setMounted] = useState(false);
  // Ролик виден поверх кадра правки.
  const [shown, setShown] = useState(false);
  // Попросили играть (кнопка ⏸, пока собирается и пока играет).
  const [want, setWant] = useState(false);
  const [full, setFull] = useState<Full>("none");
  const [mv, setMv] = useState<HTMLVideoElement | null>(null);
  const [startAt, setStartAt] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);
  const [srcPlaying, setSrcPlaying] = useState(false);
  const shownRef = useRef(false);
  const wantRef = useRef(false);
  const fullRef = useRef<Full>("none");
  const mountedRef = useRef(false);
  mountedRef.current = mounted;
  const mvRef = useRef<HTMLVideoElement | null>(null);
  mvRef.current = mv;
  const loopRef = useRef(loop);
  loopRef.current = loop;
  const rateRef = useRef(rate);
  rateRef.current = rate;
  const segsRef = useRef(activePlan?.segments ?? []);
  segsRef.current = activePlan?.segments ?? [];
  // Где остановились: секунда ролика и секунда клипа. Плей с той же секунды клипа — ровно с этой секунды ролика
  // (на стоп-кадре или наплыве секунда клипа не однозначна).
  const resumeRef = useRef<{ montage: number; clip: number } | null>(null);
  // Куда просили встать, пока ролик ещё не подключился.
  const pendingRef = useRef<{ montage: number; clip: number } | null>(null);
  const ownPauseRef = useRef(false);
  const handoffCancelRef = useRef<() => void>(() => undefined);

  const plan = useQuery({
    queryKey: ["playback-plan", planId],
    queryFn: () => playbackApi.plan(planId as number),
    enabled: mounted && planId != null && !sourceMissing,
    staleTime: Infinity,
  });
  const planRef = useRef(plan.data);
  planRef.current = plan.data && plan.data.clip_plan_id === planId ? plan.data : undefined;

  const clipTotal = () => segsRef.current.reduce((acc, s) => acc + Math.max(0, s.end_sec - s.start_sec) / (s.speed && s.speed > 0 ? s.speed : 1), 0);
  // Без плана (ещё грузится) ролик и клип считаем одной шкалой — до первого ответа это почти всегда так.
  const toClip = (t: number) => montageToClip(planRef.current, segsRef.current, t) ?? Math.min(clipTotal(), Math.max(0, t));
  const toMontage = (c: number) => clipToMontage(planRef.current, segsRef.current, c) ?? c;
  const live = (video: HTMLVideoElement | null): video is HTMLVideoElement => Boolean(video && video.readyState >= 1 && video.currentSrc);
  const srcClip = () => clipTimeOf(segsRef.current, videoRef.current?.currentTime ?? 0) ?? 0;

  const setShownBoth = (on: boolean) => {
    shownRef.current = on;
    setShown(on);
  };
  const setWantBoth = (on: boolean) => {
    wantRef.current = on;
    setWant(on);
  };
  const setFullBoth = (next: Full) => {
    fullRef.current = next;
    setFull(next);
  };
  const cancelHandoff = () => {
    handoffCancelRef.current();
    handoffCancelRef.current = () => undefined;
  };

  // Пауза: запись встаёт на ту же секунду клипа, ролик уходит, когда кадр правки уже на месте.
  const handOff = (t: number | null) => {
    cancelHandoff();
    const v = videoRef.current;
    if (t != null) {
      const clip = toClip(t);
      resumeRef.current = { montage: t, clip };
      const src = clipToSource(segsRef.current, clip);
      if (v && src != null && Math.abs(v.currentTime - src) > 0.002) {
        try {
          v.currentTime = src;
        } catch {
          /* запись ещё не готова: встанет, когда загрузится */
        }
      }
    }
    if (fullRef.current !== "none") return;
    let raf = 0;
    let timer = 0;
    const done = () => {
      cancelHandoff();
      if (!wantRef.current && fullRef.current === "none") setShownBoth(false);
    };
    // Два кадра анимации: телефон успевает нарисовать новый кадр до того, как ролик уйдёт.
    const afterPaint = () => {
      raf = requestAnimationFrame(() => {
        raf = requestAnimationFrame(done);
      });
    };
    const onSeeked = () => afterPaint();
    if (v && v.seeking) {
      v.addEventListener("seeked", onSeeked, { once: true });
      timer = window.setTimeout(done, HANDOFF_MAX_MS);
    } else afterPaint();
    handoffCancelRef.current = () => {
      v?.removeEventListener("seeked", onSeeked);
      window.clearTimeout(timer);
      cancelAnimationFrame(raf);
    };
  };

  const play = () => {
    if (planId == null || sourceMissing || !segsRef.current.length) return;
    stopPlayback();
    cancelHandoff();
    // Несохранённая правка уходит на сервер; ролик подхватит её сам (отпечаток монтажа).
    void flushSettings().catch(() => undefined);
    const clip = srcClip();
    const r = resumeRef.current;
    let t = r && Math.abs(r.clip - clip) < 0.02 ? r.montage : toMontage(clip);
    const video = mvRef.current;
    const total = live(video) && Number.isFinite(video.duration) ? video.duration : duration;
    if (total && t >= total - 0.05) t = 0;
    pendingRef.current = { montage: t, clip: toClip(t) };
    setWantBoth(true);
    setShownBoth(true);
    setStartAt(t);
    setMounted(true);
    if (video && video.currentSrc) {
      try {
        if (Math.abs(video.currentTime - t) > 0.01) video.currentTime = t;
      } catch {
        /* ещё нельзя: плеер начнёт со startAt */
      }
      video.playbackRate = rateRef.current;
      void video.play().catch(() => undefined);
    }
  };

  const pause = () => {
    setWantBoth(false);
    const video = mvRef.current;
    const t = live(video) ? video.currentTime : pendingRef.current?.montage ?? null;
    if (video && !video.paused) {
      ownPauseRef.current = true;
      video.pause();
    }
    handOff(t);
  };

  /** Ролик в сторону без возврата секунды: запись заиграла сама (▶ Сегмент, кнопки плеера записи). */
  const release = () => {
    cancelHandoff();
    setWantBoth(false);
    const video = mvRef.current;
    if (video && !video.paused) {
      ownPauseRef.current = true;
      video.pause();
    }
    resumeRef.current = null;
    if (fullRef.current === "none") setShownBoth(false);
  };

  const toggle = () => (wantRef.current ? pause() : play());

  /** Перемотка по таймлайну, пока ролик играет: секунда клипа → секунда ролика. */
  const seekClip = (clip: number) => {
    const t = toMontage(clip);
    const video = mvRef.current;
    if (live(video)) video.currentTime = t;
    else {
      pendingRef.current = { montage: t, clip };
      setStartAt(t);
    }
  };

  const leaveFull = () => {
    if (fullRef.current === "none") return;
    setFullBoth("none");
    if (!wantRef.current) {
      const video = mvRef.current;
      handOff(live(video) ? video.currentTime : null);
    }
  };
  const exitFull = () => {
    if (document.fullscreenElement && document.fullscreenElement === rootRef.current) {
      void document.exitFullscreen().catch(() => leaveFull());
    } else leaveFull();
  };
  // Тот же плеер на весь экран: в нажатии (иначе браузер не даст), ролик продолжает с той же секунды.
  const enterFull = () => {
    const el = rootRef.current;
    if (!el || planId == null || sourceMissing) return;
    if (el.requestFullscreen) {
      setFullBoth("native");
      const fallback = () => {
        if (fullRef.current === "native" && document.fullscreenElement !== el) setFullBoth("fallback");
      };
      el.requestFullscreen().catch(fallback);
      // Бывает, что браузер не отвечает ни да, ни нет (встроенные окна): тогда — окно поверх всего.
      window.setTimeout(fallback, FULLSCREEN_WAIT_MS);
    } else setFullBoth("fallback");
    if (!wantRef.current) play();
  };

  // Обработчики событий видят свежие функции этого рендера.
  const apiRef = useRef({ handOff, release, leaveFull, stopPlayback });
  apiRef.current = { handOff, release, leaveFull, stopPlayback };

  // Ролик: события своего <video> (кнопки браузера на весь экран, конец, жест «нажмите, чтобы смотреть»).
  useEffect(() => {
    if (!mv) {
      setDuration(null);
      return;
    }
    const onPlay = () => {
      if (wantRef.current) return;
      // Заиграл сам (кнопка браузера, жест): тоже наш плей.
      apiRef.current.stopPlayback();
      cancelHandoff();
      setWantBoth(true);
      setShownBoth(true);
    };
    const onPlaying = () => {
      if (Math.abs(mv.playbackRate - rateRef.current) > 1e-3) mv.playbackRate = rateRef.current;
    };
    const onPause = () => {
      if (ownPauseRef.current) {
        ownPauseRef.current = false;
        return;
      }
      if (mv.ended && loopRef.current) {
        mv.currentTime = 0;
        void mv.play().catch(() => undefined);
        return;
      }
      setWantBoth(false);
      apiRef.current.handOff(mv.currentTime);
    };
    const onDuration = () => setDuration(Number.isFinite(mv.duration) && mv.duration > 0 ? mv.duration : null);
    mv.playbackRate = rateRef.current;
    mv.defaultPlaybackRate = rateRef.current;
    onDuration();
    mv.addEventListener("play", onPlay);
    mv.addEventListener("playing", onPlaying);
    mv.addEventListener("pause", onPause);
    mv.addEventListener("durationchange", onDuration);
    mv.addEventListener("loadedmetadata", onDuration);
    return () => {
      mv.removeEventListener("play", onPlay);
      mv.removeEventListener("playing", onPlaying);
      mv.removeEventListener("pause", onPause);
      mv.removeEventListener("durationchange", onDuration);
      mv.removeEventListener("loadedmetadata", onDuration);
    };
  }, [mv]);
  // Скорость — и ролику (подключение сбрасывает её к defaultPlaybackRate).
  useEffect(() => {
    if (!mv) return;
    mv.playbackRate = rate;
    mv.defaultPlaybackRate = rate;
  }, [rate, mv]);

  // Плеер записи заиграл сам (▶ Сегмент, его кнопки) — ролик в сторону; кнопка плея видит и его.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onPlay = () => {
      setSrcPlaying(true);
      if (wantRef.current || shownRef.current) apiRef.current.release();
    };
    const onPause = () => setSrcPlaying(false);
    setSrcPlaying(!v.paused);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    return () => {
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
    };
  }, [view, sourceReady, planId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Вышли из полного экрана (Esc, кнопка браузера) — плеер снова в странице на той же секунде.
  useEffect(() => {
    const onChange = () => {
      const on = Boolean(document.fullscreenElement) && document.fullscreenElement === rootRef.current;
      if (on && fullRef.current !== "native") setFullBoth("native");
      else if (!on && fullRef.current === "native") apiRef.current.leaveFull();
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // Другой клип, уход из редактора, записи нет — всё сначала.
  useEffect(() => {
    cancelHandoff();
    setMounted(false);
    setShownBoth(false);
    setWantBoth(false);
    resumeRef.current = null;
    pendingRef.current = null;
    if (document.fullscreenElement && document.fullscreenElement === rootRef.current) void document.exitFullscreen().catch(() => undefined);
    setFullBoth("none");
  }, [planId, sourceMissing]);

  // Долго на паузе — закрываем ролик (сервер перестаёт его собирать); следующий плей продолжит с той же секунды.
  useEffect(() => {
    if (!mounted || want || full !== "none") return;
    const timer = window.setTimeout(() => setMounted(false), IDLE_UNMOUNT_MS);
    return () => window.clearTimeout(timer);
  }, [mounted, want, full]);
  useEffect(() => () => cancelHandoff(), []);

  // Плейхед таймлайна: пока ролик виден — его секунда в шкале клипа.
  const clockRef = useRef<() => number | null>(() => null);
  clockRef.current = () => {
    if (!shownRef.current) return null;
    const video = mvRef.current;
    if (live(video)) return toClip(video.currentTime);
    return pendingRef.current?.clip ?? null;
  };
  // Таймкод под телефоном: секунда ролика, пока плеер ролика открыт (на паузе — та, с которой он продолжит).
  const tcNowRef = useRef<() => number | null>(() => null);
  tcNowRef.current = () => {
    if (!mountedRef.current) return null;
    if (shownRef.current) {
      const video = mvRef.current;
      if (live(video)) return video.currentTime;
      return pendingRef.current?.montage ?? null;
    }
    if (!planRef.current) return null;
    const clip = clipTimeOf(segsRef.current, videoRef.current?.currentTime ?? 0);
    if (clip == null) return null;
    const r = resumeRef.current;
    return r && Math.abs(r.clip - clip) < 0.02 ? r.montage : toMontage(clip);
  };

  const onRevision = () => {
    // Монтаж сменился — секунда ролика у прежней остановки уже другая.
    resumeRef.current = null;
    if (planId != null) void qc.invalidateQueries({ queryKey: ["playback-plan", planId] });
  };

  return {
    montageRootRef: rootRef,
    montageMounted: mounted,
    montageShown: shown,
    montageWant: want,
    montageFull: full,
    montageStartAt: startAt,
    montageTotal: mounted ? duration ?? plan.data?.duration ?? null : null,
    srcPlaying,
    setMontageMedia: setMv,
    montageOnRevision: onRevision,
    montageClockRef: clockRef,
    montageTcRef: tcNowRef,
    montageIsOn: () => wantRef.current,
    montagePlay: play,
    montagePause: pause,
    montageToggle: toggle,
    montageRelease: release,
    montageSeekClip: seekClip,
    montageEnterFull: enterFull,
    montageExitFull: exitFull,
  };
}
