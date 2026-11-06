import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api } from "@/api/client";
import { chatApi } from "@/api/chat";
import type { ClipGridData, ClipGridItem } from "@/api/clipGroups";
import { qk } from "@/api/keys";
import { sourcesApi } from "@/api/sources";
import { ClipGrid } from "@/components/ClipGrid";
import { MissingSourceNotice, isSourceFileMissing } from "@/components/MissingSourceNotice";
import { ErrorState, Loading, formatDuration } from "@/components/ui";
import { useModuleOn } from "@/hooks/useModules";
import { lazyPage } from "@/lazyPage";
import { clipPath, videoPath } from "@/lib/paths";
import { HIDDEN_SOURCE_TYPES } from "@/lib/videoKinds";
import { AiBatchPanel } from "@/pages/workspace/AiBatchPanel";
import { AnalyzeForm } from "@/pages/workspace/AnalyzeForm";

const StreamQuestionsTab = lazyPage<{ sourceId: string }>(() => import("@/pages/workspace/StreamQuestionsTab"), "StreamQuestionsTab");
const ChatConversation = lazyPage<ComponentProps<typeof import("@/pages/chat/ChatConversation").ChatConversation>>(
  () => import("@/pages/chat/ChatConversation"),
  "ChatConversation",
);

type Cue = { start: number; end: number; text: string };
type ChatLine = { t: number; user: string; text: string };

type Pane = "tr" | "chat" | "q" | "agent";

/** Окно строк вокруг текущего времени: 3500 строк транскрипта в DOM не нужны. */
const WINDOW_BACK = 12;
const WINDOW_AHEAD = 60;

function TranscriptPane({ sourceId, ready, time, onSeek }: { sourceId: number; ready: boolean; time: number; onSeek: (t: number) => void }) {
  const query = useQuery({
    queryKey: ["source-transcript-cues", sourceId],
    // Только уже распознанное: запрос без кэша запустил бы Whisper.
    queryFn: () => api.get<{ cues: Cue[] }>(`/api/sources/${sourceId}/transcript`),
    enabled: ready,
    staleTime: 5 * 60_000,
  });
  const cues = query.data?.cues ?? [];
  const current = useMemo(() => {
    let lo = 0;
    let hi = cues.length - 1;
    let found = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (cues[mid].start <= time) {
        found = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    return found;
  }, [cues, time]);
  const listRef = useRef<HTMLDivElement>(null);
  const curRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const list = listRef.current;
    const row = curRef.current;
    if (list && row) list.scrollTop = Math.max(0, row.offsetTop - list.offsetTop - 60);
  }, [current]);
  if (!ready) return <p className="muted vp-empty">Транскрипта пока нет: он появится после распознавания речи (вкладка «Моменты», «Запись и анализ»).</p>;
  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const from = Math.max(0, current - WINDOW_BACK);
  return (
    <div className="vp-lines" ref={listRef}>
      {cues.slice(from, current + WINDOW_AHEAD).map((cue, i) => (
        <button
          key={cue.start + i}
          type="button"
          ref={from + i === current ? curRef : undefined}
          className={`vp-line${from + i === current ? " cur" : ""}`}
          onClick={() => onSeek(cue.start)}
        >
          <span className="vp-t">{formatDuration(cue.start)}</span>
          <span>{cue.text}</span>
        </button>
      ))}
    </div>
  );
}

function ChatPane({ sourceId, time, onSeek }: { sourceId: number; time: number; onSeek: (t: number) => void }) {
  // Окно чата шагает блоками по 5 минут: каждый кадр перезапрашивать незачем.
  const block = Math.floor(time / 300) * 300;
  const query = useQuery({
    queryKey: ["source-chat-window", sourceId, block],
    queryFn: () => api.get<{ chat: { message_count?: number } | null; messages: ChatLine[] }>(`/api/sources/${sourceId}/chat?from=${block}&to=${block + 300}&limit=200`),
    staleTime: 5 * 60_000,
  });
  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const lines = query.data?.messages ?? [];
  if (!lines.length) {
    return (
      <p className="muted vp-empty">
        {query.data?.chat?.message_count ? "В этом месте записи чат молчит: перемотайте видео." : "Чата нет: он есть только у записей Twitch. Импорт: вкладка «Запись и анализ»."}
      </p>
    );
  }
  return (
    <div className="vp-lines">
      {lines.map((line, i) => {
        return (
          <button key={i} type="button" className="vp-line" onClick={() => onSeek(line.t)}>
            <span className="vp-t">{formatDuration(line.t)}</span>
            <span><b>{line.user}</b> {line.text}</span>
          </button>
        );
      })}
    </div>
  );
}

/** Чат этого видео: агент знает, о каком видео речь, и лор его проекта. Переписка заводится при первом открытии. */
function VideoChatPane({ sourceId }: { sourceId: number }) {
  const thread = useQuery({
    queryKey: ["video-chat-thread", sourceId],
    queryFn: () => chatApi.videoThread(sourceId),
    staleTime: Infinity,
  });
  if (thread.isLoading) return <Loading />;
  if (thread.isError || !thread.data) {
    return <p className="muted vp-empty">Чат не открылся: {thread.error instanceof ApiError ? thread.error.message : "ошибка"}</p>;
  }
  const id = thread.data.id;
  return (
    <Suspense fallback={<Loading />}>
      <ChatConversation
        key={id}
        threadId={id}
        compact
        header={() => (
          <div className="vov-chat-head">
            <Link className="btn ghost sm" to={`/chat/${id}`}>
              Открыть на весь экран
            </Link>
          </div>
        )}
      />
    </Suspense>
  );
}

/** Шкала стрима: где на записи лежат клипы. */
function ClipRuler({ clips, duration }: { clips: ClipGridItem[]; duration: number }) {
  if (duration < 60 || !clips.length) return null;
  return (
    <>
      <div className="vruler" aria-label="Клипы на шкале записи">
        {clips.filter((c) => c.span_end > c.span_start).map((c) => (
          <Link
            key={c.id}
            to={clipPath(c.id)}
            title={`${c.name}, ${formatDuration(c.span_start)}`}
            className={c.status === "no_render" ? "empty" : ""}
            style={{
              left: `${(c.span_start / duration) * 100}%`,
              width: `${Math.max(0.5, ((c.span_end - c.span_start) / duration) * 100)}%`,
            }}
          />
        ))}
      </div>
      <div className="vruler-t">
        <span>0:00</span>
        <span>{formatDuration(duration / 2)}</span>
        <span>{formatDuration(duration)}</span>
      </div>
    </>
  );
}

/** Страница видео: запись, транскрипт, чат и вопросы рядом, под ними клипы этого видео. */
export function VideoOverview({ sourceId }: { sourceId: string }) {
  const query = useQuery({
    queryKey: qk.source(sourceId),
    queryFn: () => sourcesApi.get(sourceId),
    refetchInterval: (q) =>
      q.state.data?.analyses?.some((a) => ["queued", "running", "cancelling"].includes(a.status)) ? 4000 : false,
  });
  const [params, setParams] = useSearchParams();
  const questionsOn = useModuleOn("stream_questions");
  const chatOn = useModuleOn("twitch_chat");
  const agentOn = useModuleOn("agent_chat");
  const aiMontageOn = useModuleOn("ai_montage");
  // ?pane=agent — ссылка сразу на чат видео.
  const [pane, setPane] = useState<Pane>(params.get("pane") === "agent" ? "agent" : "tr");
  const [time, setTime] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [clips, setClips] = useState<ClipGridItem[]>([]);
  const onData = useCallback((data: ClipGridData) => setClips(data.clips.filter((c) => !c.hidden)), []);
  const seek = (t: number) => {
    setTime(t);
    if (videoRef.current) videoRef.current.currentTime = t;
  };
  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const source = query.data!;
  const missing = isSourceFileMissing(source);
  const cutOpen = params.get("cut") === "1";
  const closeCut = () => {
    const next = new URLSearchParams(params);
    next.delete("cut");
    setParams(next, { replace: true });
  };
  const favorites = source.clip_plans.filter((p) => !p.hidden && p.favorite).length;
  const ratio = source.width && source.height ? `${source.width} / ${source.height}` : "16 / 9";
  const tabs: [Pane, string][] = [
    ["tr", `Транскрипт${source.transcript_segments ? ` ${source.transcript_segments}` : ""}`],
    ...(chatOn ? ([["chat", "Чат Twitch"]] as [Pane, string][]) : []),
    ...(questionsOn ? ([["q", "Вопросы"]] as [Pane, string][]) : []),
    ...(agentOn && !HIDDEN_SOURCE_TYPES.includes(source.source_type) ? ([["agent", "Чат видео"]] as [Pane, string][]) : []),
  ];
  return (
    <div className="vov">
      {missing ? <MissingSourceNotice source={source} /> : null}
      {cutOpen ? (
        <div className="vov-cut">
          <AnalyzeForm source={source} title="Нарезать ещё" onStarted={closeCut} />
          <button type="button" className="btn ghost sm vov-cut-x" onClick={closeCut}>Закрыть</button>
        </div>
      ) : null}
      <div className="vov-grid">
        <div className="vov-left">
          <div className="vov-stage" style={{ aspectRatio: ratio }}>
            {missing ? (
              <div className="vov-nofile">Файла записи нет</div>
            ) : (
              <video
                ref={videoRef}
                src={`/media/sources/${source.id}`}
                controls
                preload="metadata"
                playsInline
                onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
              />
            )}
          </div>
          <ClipRuler clips={clips} duration={source.duration_sec ?? 0} />
          <Link className="vov-more" to={videoPath(source.id, "source")}>
            Запись и анализ: кадр, полосы, анализы ({source.analyses.length})
          </Link>
        </div>
        <div className={`vov-pane${pane === "agent" ? " vov-pane-chat" : ""}`}>
          <div className="vov-tabs" role="tablist">
            {tabs.map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={pane === key} className={pane === key ? "on" : ""} onClick={() => setPane(key)}>
                {label}
              </button>
            ))}
          </div>
          <div className={`vov-body${pane === "agent" ? " vov-body-chat" : ""}`}>
            {pane === "agent" ? <VideoChatPane sourceId={source.id} /> : null}
            {pane === "tr" ? <TranscriptPane sourceId={source.id} ready={Boolean(source.has_transcript)} time={time} onSeek={seek} /> : null}
            {pane === "chat" ? <ChatPane sourceId={source.id} time={time} onSeek={seek} /> : null}
            {pane === "q" ? (
              <Suspense fallback={<Loading />}>
                <StreamQuestionsTab sourceId={String(source.id)} />
              </Suspense>
            ) : null}
          </div>
        </div>
      </div>
      <div className="vov-clips">
        <ClipGrid
          sourceId={source.id}
          onData={onData}
          actions={aiMontageOn ? <AiBatchPanel sourceId={String(source.id)} favCount={favorites} /> : null}
        />
      </div>
    </div>
  );
}
