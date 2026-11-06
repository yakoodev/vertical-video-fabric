/**
 * ▶ Встраиваемый просмотр смонтированного клипа: рендер 480p прямо во время показа (app/stream_preview.py).
 * Нужен только `clipPlanId`. Сервер собирает ролик тем же кодом, что и рендер, и отдаёт его HLS по мере готовности;
 * здесь обычный <video> со своими кнопками браузера: одинаково в Firefox, Chrome и на телефоне.
 *
 * Спиннера без конца нет: на каждом шаге видно, что происходит (звук и план кадров, «собрано N из M»), а если
 * сборка стоит или сервер молчит, плеер говорит об этом и даёт выход (повтор, готовый рендер).
 * Правка монтажа (агент, другая вкладка, редактор) видна сама: отпечаток сменился — старая сборка отменена, новая
 * пересобирает только изменённое, плеер остаётся на той же секунде.
 *
 * Горячие клавиши работают, пока фокус внутри плеера (на весь экран — всегда): пробел — плей,
 * ←/→ — кадр, Shift+←/→ — секунда, Home/End, M — звук, F — на весь экран.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { playbackApi, type StreamStatus } from "@/api/playback";

/** Как часто спрашивать отпечаток монтажа: правка агента видна не позже этого, мс. */
export const REVISION_POLL_MS = 2000;
/** Как часто спрашивать ход сборки, мс; опрос заодно говорит серверу, что зритель на месте. */
const STATUS_POLL_MS = 1000;
const STATUS_IDLE_POLL_MS = 8000;
/** Столько без единого готового чанка и без кадра, пока ждём: говорим честно и даём выход (ждать не перестаём), мс. */
const STALL_MS = 45000;
const FPS_GUESS = 30;

type HlsModule = typeof import("hls.js");

let hlsModule: Promise<HlsModule> | null = null;
const loadHls = () => (hlsModule ??= import("hls.js"));

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

const STAGE_TEXT: Record<string, string> = {
  queued: "Встаю в очередь сборки…",
  recording: "Готовлю монтаж: звук и план кадров…",
  rendering: "Готовлю видео…",
  ready: "Загружаю видео…",
};

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
  const videoRef = useRef<HTMLVideoElement>(null);
  const selfRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<import("hls.js").default | null>(null);
  const [status, setStatus] = useState<StreamStatus | null>(null);
  const [lostServer, setLostServer] = useState(false);
  const [hasFrame, setHasFrame] = useState(false);
  const [waiting, setWaiting] = useState(true);
  const [needsTap, setNeedsTap] = useState(false);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false);
  const [showRender, setShowRender] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const attachedKey = useRef<string | null>(null);
  const resumeAt = useRef(0);
  const statusRef = useRef<StreamStatus | null>(null);
  const pollNow = useRef<() => void>(() => undefined);

  // Ход сборки: опрос пока плеер открыт. Один и тот же запрос и ставит сборку, и держит её живой.
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    let fails = 0;
    const poll = async (retry = false) => {
      window.clearTimeout(timer);
      try {
        const next = await playbackApi.stream(clipPlanId, retry);
        if (stopped) return;
        fails = 0;
        setLostServer(false);
        statusRef.current = next;
        setStatus(next);
      } catch {
        if (stopped) return;
        fails += 1;
        if (fails >= 3) setLostServer(true);
      }
      const st = statusRef.current;
      const done = st && st.state === "ready" && st.ready === st.total;
      timer = window.setTimeout(() => void poll(), done ? STATUS_IDLE_POLL_MS : STATUS_POLL_MS);
    };
    pollNow.current = () => void poll();
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [clipPlanId]);
  useClipRevision(clipPlanId, () => {
    setUpdatedAt(Date.now());
    pollNow.current();
  });

  const playlist = status?.playlist;
  const streamKey = status?.key ?? null;

  // Подключить сборку к <video>. Правка: новый ключ — та же секунда, кадр предыдущей остаётся до первого нового.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !playlist || !streamKey || attachedKey.current === streamKey) return;
    let cancelled = false;
    const wasPlaying = attachedKey.current != null && !video.paused;
    if (attachedKey.current != null) resumeAt.current = video.currentTime;
    attachedKey.current = streamKey;
    setMediaError(null);
    const start = () => {
      if (cancelled) return;
      if (resumeAt.current > 0) {
        try {
          video.currentTime = resumeAt.current;
        } catch {
          /* ещё нельзя: начнём с начала */
        }
      }
      if (autoPlay || wasPlaying) {
        video.play().then(() => setNeedsTap(false)).catch(() => setNeedsTap(true));
      }
    };
    void loadHls().then(({ default: Hls }) => {
      if (cancelled) return;
      hlsRef.current?.destroy();
      hlsRef.current = null;
      if (Hls.isSupported()) {
        const patient = {
          // Чанк может ещё собираться: сервер отвечает 503, спрашиваем снова, пока он не будет готов.
          maxTimeToFirstByteMs: 30000,
          maxLoadTimeMs: 120000,
          timeoutRetry: { maxNumRetry: 20, retryDelayMs: 500, maxRetryDelayMs: 2000 },
          errorRetry: { maxNumRetry: 600, retryDelayMs: 500, maxRetryDelayMs: 2000 },
        };
        const hls = new Hls({
          maxBufferLength: 20,
          maxMaxBufferLength: 40,
          backBufferLength: 30,
          // Один вариант качества: автоматике нечего выбирать, и первый чанк не ждёт замера сети.
          startLevel: 0,
          capLevelToPlayerSize: false,
          fragLoadPolicy: { default: patient },
          playlistLoadPolicy: { default: patient },
          manifestLoadPolicy: { default: patient },
        });
        hlsRef.current = hls;
        let recovered = 0;
        hls.on(Hls.Events.MANIFEST_PARSED, () => start());
        hls.on(Hls.Events.ERROR, (_evt, data) => {
          if (!data.fatal || cancelled) return;
          if (data.details === "manifestIncompatibleCodecsError" || data.details === "bufferIncompatibleCodecsError" || data.details === "bufferAddCodecError") {
            // Вечного спиннера здесь быть не должно: без H.264 в браузере не поможет никакой повтор.
            setMediaError("Браузер не умеет играть H.264. В Firefox на Linux для этого нужен системный ffmpeg (libavcodec).");
          } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
            window.setTimeout(() => !cancelled && hls.startLoad(), 1500);
          } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR && recovered < 3) {
            recovered += 1;
            hls.recoverMediaError();
          } else {
            setMediaError(`Видео не играется: ${data.details}`);
          }
        });
        hls.loadSource(playlist);
        hls.attachMedia(video);
      } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        // Старый iPhone без MSE: родной HLS браузера.
        video.src = playlist;
        video.addEventListener("loadedmetadata", start, { once: true });
      } else {
        setMediaError("Этот браузер не умеет играть такое видео.");
      }
    }).catch(() => setMediaError("Не загрузился код плеера: обновите страницу."));
    return () => {
      cancelled = true;
    };
  }, [playlist, streamKey, autoPlay]);
  useEffect(() => () => {
    hlsRef.current?.destroy();
    hlsRef.current = null;
    attachedKey.current = null;
  }, []);

  // Честный статус: что с кадром прямо сейчас.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const on = () => setWaiting(false);
    const off = () => setWaiting(true);
    const first = () => setHasFrame(true);
    const error = () => setMediaError(video.error ? `Видео не играется: ${video.error.message || `ошибка ${video.error.code}`}` : "Видео не играется.");
    video.addEventListener("playing", on);
    video.addEventListener("canplay", on);
    video.addEventListener("waiting", off);
    video.addEventListener("seeking", off);
    video.addEventListener("seeked", on);
    video.addEventListener("loadeddata", first);
    video.addEventListener("timeupdate", first);
    video.addEventListener("error", error);
    return () => {
      video.removeEventListener("playing", on);
      video.removeEventListener("canplay", on);
      video.removeEventListener("waiting", off);
      video.removeEventListener("seeking", off);
      video.removeEventListener("seeked", on);
      video.removeEventListener("loadeddata", first);
      video.removeEventListener("timeupdate", first);
      video.removeEventListener("error", error);
    };
  });

  // Предел ожидания: сборка не движется и кадра нет — говорим честно, ждать не перестаём.
  const readyCount = status?.ready ?? 0;
  const blocked = !mediaError && status?.state !== "failed" && status?.state !== "no_source" && (!hasFrame || waiting);
  useEffect(() => {
    setStalled(false);
    if (!blocked) return;
    const timer = window.setTimeout(() => setStalled(true), STALL_MS);
    return () => window.clearTimeout(timer);
  }, [blocked, readyCount, status?.state, streamKey]);

  const retry = useCallback(() => {
    setStalled(false);
    setShowRender(false);
    setMediaError(null);
    attachedKey.current = null;
    hlsRef.current?.destroy();
    hlsRef.current = null;
    void playbackApi.stream(clipPlanId, true).then((next) => {
      statusRef.current = next;
      setStatus(next);
    }).catch(() => setLostServer(true));
  }, [clipPlanId]);

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
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return false;
      const video = videoRef.current;
      if (!video) return false;
      const key = e.key.toLowerCase();
      if (e.key === " ") {
        if (video.paused) void video.play().catch(() => setNeedsTap(true));
        else video.pause();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        video.pause();
        const dir = e.key === "ArrowLeft" ? -1 : 1;
        video.currentTime = Math.max(0, video.currentTime + dir * (e.shiftKey ? 1 : 1 / FPS_GUESS));
      } else if (e.key === "Home") video.currentTime = 0;
      else if (e.key === "End") video.currentTime = Math.max(0, (video.duration || 0) - 1 / FPS_GUESS);
      else if (key === "m" || key === "ь") video.muted = !video.muted;
      else if (key === "f" || key === "а") toggleFullscreen();
      else if (e.key === "Escape" && onClose) onClose();
      else return false;
      e.preventDefault();
      e.stopPropagation();
      return true;
    },
    [onClose, toggleFullscreen],
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

  const render = status?.render ?? null;
  const noSource = status?.state === "no_source";
  const problem = mediaError
    ?? (status?.state === "failed" ? `Сборка не удалась: ${status.error || "причина неизвестна"}` : null)
    ?? (lostServer ? "Нет связи с сервером. Пробую снова…" : null)
    ?? (stalled ? "Сборка стоит: сервер занят другой работой или завис. Ждать можно дальше." : null);
  const total = status?.total ?? 0;
  const ready = status?.ready ?? 0;
  const statusText = !status
    ? "Готовлю монтаж…"
    : status.playlist && total
      ? ready < total ? `Готовлю видео: собрано ${ready} из ${total}` : "Загружаю видео…"
      : STAGE_TEXT[status.state] ?? "Готовлю монтаж…";
  const frame = status?.width && status?.height ? { w: status.width, h: status.height } : null;
  const showStatus = !noSource && !showRender && !problem && (!hasFrame || waiting) && !needsTap;

  if (noSource || (showRender && render)) {
    return (
      <div ref={selfRef} className={`mplayer ${className}`} tabIndex={0} onKeyDown={onClose ? undefined : (e) => void onKeyDown(e)}>
        <div className="mplayer-stage">
          <div className="mplayer-fallback">
            {render ? <video key={render.url} src={render.url} poster={render.poster} controls autoPlay={autoPlay} preload="metadata" playsInline /> : null}
            <div className="mplayer-note" role="status">
              {noSource ? status?.message : `Это готовый рендер${render?.stale ? ", собранный до последних правок: их в нём нет" : ""}.`}{" "}
              {showRender ? <button type="button" className="btn sm" onClick={retry}>Вернуться к сборке</button> : null}
            </div>
          </div>
        </div>
        <div className="mplayer-meta muted">
          {title ? <span className="mplayer-title">{title}</span> : null}
          {onClose ? <button type="button" className="btn sm ghost" onClick={onClose} title="Закрыть (Esc)">✕</button> : null}
        </div>
      </div>
    );
  }

  return (
    <div
      ref={selfRef}
      className={`mplayer ${className}`}
      tabIndex={0}
      onKeyDown={onClose ? undefined : (e) => void onKeyDown(e)}
      data-state={showStatus ? "waiting" : "video"}
    >
      <div className="mplayer-stage">
        <div
          className="mplayer-frame"
          style={frame ? ({ aspectRatio: `${frame.w} / ${frame.h}`, "--frame-k": frame.w / frame.h } as React.CSSProperties) : undefined}
        >
          <video ref={videoRef} className="mplayer-video" controls playsInline preload="auto" />
          {showStatus ? (
            <div className="mplayer-status" role="status" data-testid="stream-status">
              <div className="mplayer-spinner" aria-hidden />
              <span>{statusText}</span>
              {status?.playlist && total && ready < total ? (
                <span className="mplayer-progress" aria-hidden>
                  <i style={{ width: `${Math.round((ready / total) * 100)}%` }} />
                </span>
              ) : null}
            </div>
          ) : null}
          {problem ? (
            <div className="mplayer-status mplayer-status--problem" role="alert">
              <span>{problem}</span>
              <span className="mplayer-actions">
                <button type="button" className="btn sm primary" onClick={retry}>Повторить</button>
                {render ? (
                  <button type="button" className="btn sm" onClick={() => setShowRender(true)}>
                    Смотреть готовый рендер{render.stale ? " (старый)" : ""}
                  </button>
                ) : null}
              </span>
            </div>
          ) : null}
          {needsTap && !problem ? (
            <button
              type="button"
              className="mplayer-bigplay mplayer-bigplay--tap"
              onClick={() => void videoRef.current?.play().then(() => setNeedsTap(false)).catch(() => undefined)}
            >
              ▶<small>Нажмите, чтобы смотреть</small>
            </button>
          ) : null}
        </div>
      </div>
      <div className="mplayer-meta muted">
        {title ? <span className="mplayer-title">{title}</span> : null}
        {status?.playlist && total && ready < total && hasFrame ? (
          <span className="mplayer-chip" data-testid="stream-chip">собрано {ready} из {total}</span>
        ) : null}
        {updatedAt ? <span className="mplayer-chip mplayer-chip--ok" key={updatedAt}>монтаж обновлён</span> : null}
        {status?.notes?.length ? <span className="mplayer-chip" title={status.notes.join("\n")}>{status.notes[0]}</span> : null}
        <span className="mplayer-spacer" />
        <button type="button" className="btn sm ghost" onClick={toggleFullscreen} title="На весь экран (F)">⛶</button>
        {onClose ? <button type="button" className="btn sm ghost" onClick={onClose} title="Закрыть (Esc)">✕</button> : null}
      </div>
    </div>
  );
}
