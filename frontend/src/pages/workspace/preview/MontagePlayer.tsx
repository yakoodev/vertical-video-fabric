/**
 * ▶ Встраиваемый просмотр смонтированного клипа. Нужен только `clipPlanId`: сам берёт план с сервера
 * (app/playback.py), сам следит за правками (отпечаток монтажа, app/playback.revision) и играет весь
 * клип без рендера. Записи и прокси нет — показывает готовый рендер и честно говорит об этом.
 *
 * Горячие клавиши работают, пока фокус внутри плеера (и не мешают редактору вокруг): пробел — плей,
 * ←/→ — кадр, Shift+←/→ — секунда, Home/End, M — звук, F — на весь экран.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { playbackApi, type PlaybackPlan } from "@/api/playback";
import { colorFixCss } from "@/api/types";
import { frameSize } from "@/motion";
import { lookFilterCss } from "./frame";
import { MontageEngine, type EngineState } from "./engine";

/** Как часто спрашивать отпечаток монтажа: правка агента видна не позже этого, мс. */
export const REVISION_POLL_MS = 2000;

function formatTime(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${String(m).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

/** Следить за отпечатком монтажа клипа: сменился — зовём `onChange`. */
export function useClipRevision(clipPlanId: number | null, onChange: (revision: string) => void, enabled = true): void {
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const known = useRef<string | null>(null);
  useEffect(() => {
    known.current = null;
    if (!clipPlanId || !enabled) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      if (stopped) return;
      if (!document.hidden) {
        try {
          const { revision } = await playbackApi.revision(clipPlanId);
          if (stopped) return;
          if (known.current != null && known.current !== revision) onChangeRef.current(revision);
          known.current = revision;
        } catch {
          /* сеть моргнула — спросим в следующий раз */
        }
      }
      timer = window.setTimeout(() => void poll(), REVISION_POLL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [clipPlanId, enabled]);
}

export function MontagePlayer({
  clipPlanId,
  autoPlay = false,
  className = "",
  onClose,
  rootRef,
  title,
}: {
  clipPlanId: number;
  autoPlay?: boolean;
  className?: string;
  /** Кнопка «закрыть» (и Esc) — у полноэкранной обёртки. */
  onClose?: () => void;
  /** Элемент, который разворачивать на весь экран (по умолчанию сам плеер). */
  rootRef?: React.RefObject<HTMLElement>;
  title?: string;
}) {
  const qc = useQueryClient();
  const planQuery = useQuery({
    queryKey: ["playback", clipPlanId],
    queryFn: () => playbackApi.plan(clipPlanId),
    staleTime: 0,
    placeholderData: (previous) => previous,
  });
  const plan = planQuery.data;
  const [engine] = useState(() => new MontageEngine());
  const baseRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const selfRef = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const timeRef = useRef<HTMLSpanElement>(null);
  const [state, setState] = useState<EngineState>(() => engine.state());
  const [volume, setVolume] = useState(() => {
    try {
      const saved = Number(localStorage.getItem("vvf.player.volume"));
      return Number.isFinite(saved) && saved > 0 && saved <= 1 ? saved : 1;
    } catch {
      return 1;
    }
  });
  const [muted, setMuted] = useState(false);
  const [loop, setLoop] = useState(false);
  const [pieceCss, setPieceCss] = useState("");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const firstPlanRef = useRef(true);
  const autoPlayedRef = useRef(false);
  const scrubRef = useRef<{ wasPlaying: boolean } | null>(null);

  useEffect(() => () => engine.destroy(), [engine]);
  // Для проверок и отладки: движок открытого плеера.
  useEffect(() => {
    const host = window as unknown as { __montage?: MontageEngine };
    host.__montage = engine;
    return () => {
      if (host.__montage === engine) delete host.__montage;
    };
  }, [engine]);
  useEffect(() => engine.onState(setState), [engine]);
  useEffect(() => {
    engine.setOutput(volume, muted);
    try {
      localStorage.setItem("vvf.player.volume", String(volume));
    } catch {
      /* приватное окно */
    }
  }, [engine, volume, muted]);
  useEffect(() => engine.setLoop(loop), [engine, loop]);
  useEffect(() => {
    if (baseRef.current && overlayRef.current) engine.attach(baseRef.current, overlayRef.current);
  });
  useEffect(() => {
    if (!plan) return;
    engine.setPlan(plan);
    if (firstPlanRef.current) firstPlanRef.current = false;
    else setUpdatedAt(Date.now());
    if (autoPlay && !autoPlayedRef.current && plan.media.kind !== "render" && plan.media.kind !== "none") {
      autoPlayedRef.current = true;
      engine.play();
    }
  }, [engine, plan, autoPlay]);
  // Правка извне (агент, другая вкладка): новый план без перезагрузки, та же секунда.
  useClipRevision(clipPlanId, () => void qc.invalidateQueries({ queryKey: ["playback", clipPlanId] }));

  // Лёгкий прокси: попросить, когда смотрят оригинал; дособрался — пересесть на него на паузе.
  const sourceId = plan?.source.id;
  const kind = plan?.media.kind;
  const proxy = useQuery({
    queryKey: ["source-proxy", sourceId],
    queryFn: () => playbackApi.proxyEnsure(sourceId as number),
    enabled: Boolean(sourceId) && kind === "original",
    refetchInterval: (q) => (["queued", "running"].includes(q.state.data?.status ?? "") ? 5000 : false),
  });
  const proxyStatus = proxy.data?.status;
  useEffect(() => {
    if (proxyStatus === "ready" && kind === "original") void qc.invalidateQueries({ queryKey: ["playback", clipPlanId] });
  }, [proxyStatus, kind, clipPlanId, qc]);

  // Время и бегунок — прямо в DOM каждый кадр: React-стейт перерисовывал бы плеер 60 раз в секунду.
  useEffect(
    () =>
      engine.onFrame((t) => {
        const total = engine.plan?.duration || 0;
        if (timeRef.current) timeRef.current.textContent = formatTime(t);
        if (headRef.current) headRef.current.style.left = `${total > 0 ? Math.min(100, (t / total) * 100) : 0}%`;
        const css = colorFixCss(engine.activePart()?.color_fix);
        setPieceCss((prev) => (prev === css ? prev : css));
      }),
    [engine],
  );

  const seekFromPointer = useCallback(
    (clientX: number, el: HTMLElement) => {
      const r = el.getBoundingClientRect();
      const k = Math.min(1, Math.max(0, (clientX - r.left) / (r.width || 1)));
      engine.seek(k * (engine.plan?.duration ?? 0), scrubRef.current ? false : undefined);
    },
    [engine],
  );

  const toggleFullscreen = useCallback(() => {
    const el = rootRef?.current ?? selfRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    else void el.requestFullscreen?.().catch(() => undefined);
  }, [rootRef]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent | React.KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return false;
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) && (target as HTMLInputElement).type !== "range") return false;
      const fps = engine.plan?.fps || 30;
      const key = e.key.toLowerCase();
      if (e.key === " ") engine.toggle();
      else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        engine.pause();
        const dir = e.key === "ArrowLeft" ? -1 : 1;
        engine.seek(engine.t + dir * (e.shiftKey ? 1 : 1 / fps));
      } else if (e.key === "Home") engine.seek(0);
      else if (e.key === "End") engine.seek((engine.plan?.duration ?? 0) - 1 / fps);
      else if (key === "m" || key === "ь") setMuted((m) => !m);
      else if (key === "f" || key === "а") toggleFullscreen();
      else if (e.key === "Escape" && onClose) onClose();
      else return false;
      e.preventDefault();
      e.stopPropagation();
      return true;
    },
    [engine, onClose, toggleFullscreen],
  );
  // На весь экран и в оверлее клавиши ловим раньше редактора: пробел здесь — этот плеер.
  useEffect(() => {
    if (!onClose) return;
    const handler = (e: KeyboardEvent) => void onKeyDown(e);
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [onClose, onKeyDown]);
  // Вышли из полного экрана Esc-ом браузера: оверлей закрываем тем же нажатием.
  const wasFsRef = useRef(false);
  useEffect(() => {
    const onChange = () => {
      const on = Boolean(document.fullscreenElement);
      if (wasFsRef.current && !on && onClose) onClose();
      wasFsRef.current = on;
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, [onClose]);

  const frame = plan ? frameSize(plan.format) : { w: 1080, h: 1920 };
  const lookCss = plan ? [pieceCss, lookFilterCss(plan.look)].filter(Boolean).join(" ") : "";
  const chips = useMemo(() => mediaChip(plan, proxy.data?.status, proxy.data?.progress), [plan, proxy.data?.status, proxy.data?.progress]);
  const isFallback = plan?.media.kind === "render" || plan?.media.kind === "none";

  return (
    <div
      ref={selfRef}
      className={`mplayer ${className}`}
      tabIndex={0}
      onKeyDown={onClose ? undefined : (e) => void onKeyDown(e)}
      data-state={state.playing ? "playing" : "paused"}
    >
      <div className="mplayer-stage">
        {!plan && planQuery.isError ? <div className="mplayer-msg">Монтаж не загрузился: {String(planQuery.error)}</div> : null}
        {isFallback && plan ? (
          <div className="mplayer-fallback">
            {plan.media.kind === "render" ? (
              <video key={plan.media.url ?? ""} src={plan.media.url ?? undefined} poster={plan.media.poster} controls autoPlay={autoPlay} preload="metadata" />
            ) : null}
            <div className="mplayer-note" role="status">{plan.media.message}</div>
          </div>
        ) : (
          <div
            className="mplayer-frame"
            style={{ aspectRatio: `${frame.w} / ${frame.h}`, "--frame-k": frame.w / frame.h } as React.CSSProperties}
            onClick={() => engine.toggle()}
            title="Пробел — плей/пауза"
          >
            <canvas
              ref={baseRef}
              width={frame.w / 2}
              height={frame.h / 2}
              style={{ filter: lookCss || undefined, transform: plan?.mirror ? "scaleX(-1)" : undefined }}
            />
            <canvas ref={overlayRef} className="mplayer-overlay" width={frame.w} height={frame.h} />
            {plan?.look?.vignette ? <div className="stage-vignette" style={{ opacity: Math.min(1, plan.look.vignette) }} /> : null}
            {state.buffering || (plan && !state.firstFrame) ? <div className="mplayer-spinner" aria-label="Загрузка" /> : null}
            {plan && state.firstFrame && !state.playing && !state.buffering ? <div className="mplayer-bigplay" aria-hidden>▶</div> : null}
          </div>
        )}
      </div>
      {plan?.media.note ? <div className="mplayer-note" role="status">{plan.media.note}</div> : null}
      {state.error ? <div className="mplayer-note mplayer-note--err" role="alert">{state.error}</div> : null}
      {isFallback ? null : (
        <div className="mplayer-bar">
          <button type="button" className="btn primary sm mplayer-play" onClick={() => engine.toggle()} disabled={!plan} title="Пробел">
            {state.playing ? "⏸ Пауза" : state.ended ? "↻ Сначала" : "▶ Смотреть"}
          </button>
          <span className="mplayer-time mono">
            <span ref={timeRef}>{formatTime(state.t)}</span> / {formatTime(plan?.duration ?? 0)}
          </span>
          <div
            className="mplayer-scrub"
            role="slider"
            aria-label="Прокрутка клипа"
            aria-valuemin={0}
            aria-valuemax={plan?.duration ?? 0}
            aria-valuenow={state.t}
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId);
              scrubRef.current = { wasPlaying: state.playing };
              if (state.playing) engine.pause();
              seekFromPointer(e.clientX, e.currentTarget);
            }}
            onPointerMove={(e) => {
              if (scrubRef.current) seekFromPointer(e.clientX, e.currentTarget);
            }}
            onPointerUp={() => {
              const was = scrubRef.current?.wasPlaying;
              scrubRef.current = null;
              if (was) engine.play();
            }}
            onPointerCancel={() => {
              scrubRef.current = null;
            }}
          >
            {plan?.parts.map((p) => (
              <span
                key={p.index}
                className={`mplayer-part${p.piece % 2 ? " alt" : ""}${p.index && plan.parts[p.index - 1].piece !== p.piece ? " scene" : ""}`}
                style={{ left: `${(p.start / plan.duration) * 100}%`, width: `${((p.end - p.start) / plan.duration) * 100}%` }}
                title={`Кусок ${p.piece + 1}: ${formatTime(p.src_start)}–${formatTime(p.src_end)} записи`}
              />
            ))}
            <div ref={headRef} className="mplayer-head" style={{ left: 0 }} />
          </div>
          <button type="button" className="btn sm ghost" onClick={() => setMuted((m) => !m)} title="Звук (M)" aria-pressed={muted}>
            {muted ? "🔇" : "🔊"}
          </button>
          <input
            type="range"
            className="mplayer-volume"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            aria-label="Громкость"
            onChange={(e) => {
              setVolume(Number(e.target.value));
              setMuted(false);
            }}
          />
          <label className="switch mplayer-loop" title="Играть по кругу">
            <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} />
            <span className="switch-track" />
            <span>Круг</span>
          </label>
          <button type="button" className="btn sm ghost" onClick={toggleFullscreen} title="На весь экран (F)">⛶</button>
          {onClose ? (
            <button type="button" className="btn sm ghost" onClick={onClose} title="Закрыть (Esc)">✕</button>
          ) : null}
        </div>
      )}
      <div className="mplayer-meta muted">
        {title ? <span className="mplayer-title">{title}</span> : null}
        {chips ? <span className="mplayer-chip" title={chips.hint}>{chips.text}</span> : null}
        {updatedAt ? <span className="mplayer-chip mplayer-chip--ok" key={updatedAt}>монтаж обновлён</span> : null}
        {plan?.warnings.length && !isFallback ? (
          <span className="mplayer-chip" title={plan.warnings.join("\n")}>чем отличается от рендера</span>
        ) : null}
      </div>
    </div>
  );
}

function mediaChip(plan: PlaybackPlan | undefined, status?: string, progress?: number): { text: string; hint: string } | null {
  if (!plan) return null;
  const media = plan.media;
  if (media.kind === "proxy") return { text: "прокси 480p", hint: "Лёгкая копия записи: те же секунды, быстрая перемотка. Рендер — всегда из записи" };
  if (media.kind === "canvas") return { text: "холст", hint: "Клип без записи: фон и слои" };
  if (media.kind === "original") {
    if (status === "running" || status === "queued") {
      return { text: `запись; прокси ${status === "queued" ? "в очереди" : `${Math.round((progress ?? 0) * 100)}%`}`, hint: "Лёгкая копия собирается в фоне, потом плеер пересядет на неё сам" };
    }
    return { text: "запись", hint: "Играет сам файл записи" };
  }
  return null;
}
