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
import { playbackApi, type PiecesStatus, type PlaybackPlan } from "@/api/playback";
import { colorFixCss } from "@/api/types";
import { frameSize } from "@/motion";
import { lookFilterCss } from "./frame";
import { MontageEngine, type EngineState } from "./engine";

/** Как часто спрашивать отпечаток монтажа: правка агента видна не позже этого, мс. */
export const REVISION_POLL_MS = 2000;
/** Как часто спрашивать куски, пока собираются, мс. */
const PIECES_POLL_MS = 700;
/** Столько ждём кадр (и дозагрузку на ходу), потом честно говорим и предлагаем выход, мс. */
const STALL_MS = 15000;

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
  const [showRender, setShowRender] = useState(false);
  const [stalled, setStalled] = useState(false);
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
    enabled: Boolean(sourceId) && (kind === "original" || kind === "pieces"),
    refetchInterval: (q) => (["queued", "running"].includes(q.state.data?.status ?? "") ? 5000 : false),
  });
  // Куски 480p: сервер собирает их от той части, что сейчас на экране; готовые плеер берёт на ходу.
  const pieces = useQuery({
    queryKey: ["preview-pieces", clipPlanId, plan?.revision],
    queryFn: () => playbackApi.piecesEnsure(clipPlanId, engine.state().t > 0 ? engine.activePart()?.index ?? 0 : 0),
    enabled: kind === "pieces",
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && d.ready + d.failed >= d.total ? false : PIECES_POLL_MS;
    },
  });
  const piecesData = pieces.data;
  useEffect(() => {
    if (piecesData) engine.updatePieces(piecesData);
  }, [engine, piecesData]);
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

  // Спиннер не вечный: кадра нет или дозагрузка стоит дольше предела — говорим честно и даём выход.
  const waiting = Boolean(plan) && !state.error && !state.needsTap && (state.buffering || (!state.firstFrame && kind !== "render" && kind !== "none"));
  useEffect(() => {
    setStalled(false);
    if (!waiting) return;
    const timer = window.setTimeout(() => setStalled(true), STALL_MS);
    return () => window.clearTimeout(timer);
  }, [waiting, state.firstFrame, piecesData?.ready]);
  const retry = useCallback(() => {
    setStalled(false);
    setShowRender(false);
    engine.unlock();
    engine.retry();
    if (kind === "pieces") {
      void playbackApi.piecesEnsure(clipPlanId, engine.activePart()?.index ?? 0, true).then((st) => engine.updatePieces(st)).catch(() => undefined);
      void pieces.refetch();
    }
    void planQuery.refetch();
  }, [engine, kind, clipPlanId, pieces, planQuery]);

  const frame = plan ? frameSize(plan.format) : { w: 1080, h: 1920 };
  const lookCss = plan ? [pieceCss, lookFilterCss(plan.look)].filter(Boolean).join(" ") : "";
  const chips = useMemo(() => mediaChip(plan, proxy.data?.status, proxy.data?.progress, piecesData), [plan, proxy.data?.status, proxy.data?.progress, piecesData]);
  const render = plan?.media.render ?? null;
  const isFallback = plan?.media.kind === "render" || plan?.media.kind === "none" || (showRender && Boolean(render));
  const failedPiece = piecesData?.failed ? piecesData.parts.find((p) => p.status === "failed") : undefined;
  const problem = state.error ?? (failedPiece && state.waitingPiece ? "Этот кусок не собрался на сервере." : null) ?? (stalled ? (kind === "pieces" && state.waitingPiece ? "Куски собираются дольше обычного: сервер занят." : "Видео не успевает загрузиться: сеть слабая или сервер занят.") : null);
  const loadingText = !plan
    ? "Готовлю монтаж…"
    : state.waitingPiece && piecesData
      ? `Готовлю видео: ${piecesData.ready} из ${piecesData.total}`
      : state.waitingPiece
        ? "Готовлю видео…"
        : "Загружаю видео…";

  return (
    <div
      ref={selfRef}
      className={`mplayer ${className}`}
      tabIndex={0}
      onKeyDown={onClose ? undefined : (e) => void onKeyDown(e)}
      onPointerDown={() => engine.unlock()}
      data-state={state.playing && !state.buffering ? "playing" : "paused"}
    >
      <div className="mplayer-stage">
        {!plan && planQuery.isError ? <div className="mplayer-msg">Монтаж не загрузился: {String(planQuery.error)}</div> : null}
        {isFallback && plan ? (
          <div className="mplayer-fallback">
            {showRender && render ? (
              <>
                <video key={render.url} src={render.url} poster={render.poster} controls autoPlay preload="metadata" playsInline />
                <div className="mplayer-note" role="status">
                  Это готовый рендер{render.stale ? ", собранный до последних правок: их в нём нет" : ""}.{" "}
                  <button type="button" className="btn sm" onClick={retry}>Вернуться к монтажу</button>
                </div>
              </>
            ) : (
              <>
                {plan.media.kind === "render" ? (
                  <video key={plan.media.url ?? ""} src={plan.media.url ?? undefined} poster={plan.media.poster} controls autoPlay={autoPlay} preload="metadata" playsInline />
                ) : null}
                <div className="mplayer-note" role="status">{plan.media.message}</div>
              </>
            )}
          </div>
        ) : (
          <div
            className="mplayer-frame"
            style={{ aspectRatio: `${frame.w} / ${frame.h}`, "--frame-k": frame.w / frame.h } as React.CSSProperties}
            onClick={() => {
              if (state.firstFrame || state.needsTap) engine.toggle();
            }}
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
            {(waiting || !plan) && !problem ? (
              <div className="mplayer-status" role="status">
                <div className="mplayer-spinner" aria-hidden />
                <span>{loadingText}</span>
                {state.waitingPiece && piecesData?.total ? (
                  <span className="mplayer-progress" aria-hidden>
                    <i style={{ width: `${Math.round((piecesData.ready / piecesData.total) * 100)}%` }} />
                  </span>
                ) : null}
              </div>
            ) : null}
            {problem ? (
              <div className="mplayer-status mplayer-status--problem" role="alert" onClick={(e) => e.stopPropagation()}>
                <span>{problem}</span>
                <span className="mplayer-actions">
                  <button type="button" className="btn sm primary" onClick={retry}>Повторить</button>
                  {render ? (
                    <button type="button" className="btn sm" onClick={() => { engine.pause(); setShowRender(true); }}>
                      Смотреть готовый рендер{render.stale ? " (старый)" : ""}
                    </button>
                  ) : null}
                </span>
              </div>
            ) : null}
            {state.needsTap && !problem ? <div className="mplayer-bigplay mplayer-bigplay--tap">▶<small>Нажмите, чтобы смотреть</small></div> : null}
            {plan && state.firstFrame && !state.playing && !state.buffering && !state.needsTap && !problem ? <div className="mplayer-bigplay" aria-hidden>▶</div> : null}
            {state.audioLocked && state.playing && !problem ? <div className="mplayer-audio-lock" aria-hidden>🔇 нажмите, чтобы включить звук</div> : null}
          </div>
        )}
      </div>
      {plan?.media.note ? <div className="mplayer-note" role="status">{plan.media.note}</div> : null}
      {isFallback ? null : (
        <div className="mplayer-bar">
          <button type="button" className="btn primary sm mplayer-play" onClick={() => engine.toggle()} disabled={!plan} title="Пробел">
            {state.playing ? (state.buffering || !state.firstFrame ? "⏳ Загрузка" : "⏸ Пауза") : state.ended ? "↻ Сначала" : "▶ Смотреть"}
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

function mediaChip(plan: PlaybackPlan | undefined, _status?: string, _progress?: number, pieces?: PiecesStatus): { text: string; hint: string } | null {
  if (!plan) return null;
  // Чем именно играет плеер, зрителю знать незачем: только ход подготовки.
  if (plan.media.kind === "pieces" && pieces && pieces.ready < pieces.total) {
    return { text: `готовлю видео: ${pieces.ready} из ${pieces.total}`, hint: "Ещё несколько секунд" };
  }
  return null;
}
