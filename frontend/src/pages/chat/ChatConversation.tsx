import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import {
  chatApi,
  chatKeys,
  type ChatAttachment,
  type ChatChange,
  type ChatConfirmation,
  type ChatMsg,
  type ChatStep,
  type ChatThreadView,
  type LiveRun,
  type StepPreview,
} from "@/api/chat";
import { projectsApi } from "@/api/projects";
import { qk } from "@/api/keys";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { projectPath } from "@/lib/paths";
import { ApprovalCard } from "@/pages/ApprovalsPage";
import { Markdown, type MediaRef } from "@/pages/chat/Markdown";
import "@/styles/chat.css";

/**
 * Переписка с агентом: реплики, живой ответ (SSE), шаги-карточки инструментов с превью,
 * подтверждение опасного, «Откатить» правки клипа, поле ввода с файлами.
 * Используется страницей «Чат», вкладкой «Чат» проекта, чатом видео и общим чатом.
 */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

const PHASE_LABEL: Record<string, string> = {
  thinking: "думает…",
  streaming: "пишет…",
  tool: "работает инструментами…",
};

const GROUP_LABEL: Record<string, string> = {
  montage: "монтаж",
  subtitles: "субтитры",
  audio: "звук",
  cuts: "куски и кадр",
  screens: "плашки и концовки",
  assets: "библиотека",
  sources: "видео",
  other: "прочее",
};

const SUGGESTIONS = [
  "Что сейчас есть в проектах?",
  "Найди клип с самым сильным хуком и покажи кадр",
  "Заглуши мат в последнем клипе",
  "Какие правила у проекта?",
];

const PROJECT_SUGGESTIONS = [
  "Что ты знаешь об этом проекте?",
  "Нарежь новый стрим",
  "Какие правила у проекта?",
  "Что из клипов ещё не отрендерено?",
];

const VIDEO_SUGGESTIONS = [
  "Что ты знаешь про это видео?",
  "Найди ещё моменты, не повторяй готовые клипы",
  "Какие черновики этого видео ещё не смонтированы?",
];

const MAX_FACT_LEN = 600;

function suggestionsFor(view: ChatThreadView): string[] {
  if (view.video) return VIDEO_SUGGESTIONS;
  return view.project ? PROJECT_SUGGESTIONS : SUGGESTIONS;
}

function useLiveRun(threadId: number, running: boolean, onDone: () => void) {
  const [live, setLive] = useState<LiveRun | null>(null);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  useEffect(() => {
    if (!running) {
      setLive(null);
      return;
    }
    const es = new EventSource(`/api/chat/threads/${threadId}/stream`);
    es.addEventListener("text", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setLive((prev) => ({ text: d.text, steps: prev?.steps ?? [], phase: prev?.phase ?? "streaming", reply_id: d.reply_id }));
    });
    es.addEventListener("steps", (e) => {
      const d = JSON.parse((e as MessageEvent).data);
      setLive((prev) => ({ text: prev?.text ?? "", steps: d.steps, phase: d.phase, reply_id: d.reply_id }));
    });
    es.addEventListener("done", () => {
      es.close();
      doneRef.current();
    });
    es.onerror = () => {
      // Обрыв (перезапуск сервера, сеть): закрываем — дальше ведёт опрос переписки.
      es.close();
      doneRef.current();
    };
    return () => es.close();
  }, [threadId, running]);
  return live;
}

function messageRef(m: ChatMsg): MediaRef | null {
  const clipIds = [...m.steps.map((s) => s.clip_id), ...(m.changes ?? []).map((c) => c.clip_plan_id)].filter(
    (x): x is number => typeof x === "number",
  );
  const distinct = Array.from(new Set(clipIds));
  return distinct.length === 1 ? { kind: "clip", id: distinct[0] } : null;
}

// --------------------------------------------------------------------------- //
// Плеер по таймкоду
// --------------------------------------------------------------------------- //

function clipToSourceSec(pieces: [number, number][], t: number): number {
  let acc = 0;
  for (const [a, b] of pieces) {
    if (t <= acc + (b - a)) return a + Math.max(0, t - acc);
    acc += b - a;
  }
  const last = pieces[pieces.length - 1];
  return last ? last[1] : t;
}

function MediaPanel({ target, onClose }: { target: MediaRef; onClose: () => void }) {
  const clip = useQuery({
    queryKey: chatKeys.clip(target.id),
    queryFn: () => chatApi.clip(target.id),
    enabled: target.kind === "clip",
    retry: false,
  });
  const videoRef = useRef<HTMLVideoElement>(null);
  const [noFile, setNoFile] = useState(false);
  const t = target.t ?? 0;
  // Клип без рендера — играем исходник с его кусков: секунда клипа → секунда исходника.
  const pieces = clip.data?.pieces ?? [];
  const fromSource = target.kind === "clip" && !!clip.data && !clip.data.video_url && pieces.length > 0 && !!clip.data.source_id;
  const seekTo = fromSource ? clipToSourceSec(pieces, t) : t;
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const seek = () => {
      v.currentTime = seekTo;
    };
    if (v.readyState >= 1) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
  }, [seekTo, clip.data?.video_url, target.kind, target.id]);
  let body;
  let editor: string | null = null;
  if (target.kind === "source") {
    editor = `/v/${target.id}/source`;
    body = noFile ? (
      <div className="muted">Запись стрима удалена — открой видео, чтобы скачать её заново</div>
    ) : (
      <video ref={videoRef} className="chat-media-video wide" src={`/media/sources/${target.id}`} controls autoPlay muted={false} onError={() => setNoFile(true)} />
    );
  } else if (clip.isLoading) body = <Loading />;
  else if (clip.isError || !clip.data) body = <div className="muted">Клип #{target.id} не найден</div>;
  else {
    editor = `/v/${clip.data.source_id}/candidates?clip=${target.id}${target.t != null ? `&t=${t}` : ""}`;
    body = clip.data.video_url ? (
      <video ref={videoRef} className="chat-media-video" src={clip.data.video_url} poster={clip.data.thumb_url} controls autoPlay />
    ) : fromSource ? (
      <>
        {noFile ? (
          <div className="muted">Рендера ещё нет, а запись стрима удалена</div>
        ) : (
          <>
            <video ref={videoRef} className="chat-media-video wide" src={`/media/sources/${clip.data.source_id}`} controls autoPlay onError={() => setNoFile(true)} />
            <span className="muted chat-small">Рендера ещё нет — показано видео на этом месте клипа</span>
          </>
        )}
      </>
    ) : (
      <div className="chat-media-thumb">
        <img src={clip.data.thumb_url} alt="" />
        <span className="muted">Рендера ещё нет — открой в редакторе</span>
      </div>
    );
  }
  return (
    <div className="chat-media" role="dialog">
      <div className="chat-media-head">
        <strong>
          {target.kind === "clip" ? `Клип #${target.id}` : `Видео #${target.id}`}
          {target.t != null ? ` · ${t} с` : ""}
        </strong>
        {clip.data?.title ? <span className="muted chat-ellipsis">{clip.data.title}</span> : null}
        <button className="btn ghost sm" onClick={onClose} aria-label="Закрыть">
          ✕
        </button>
      </div>
      {body}
      {editor ? (
        <Link className="btn sm" to={editor}>
          Открыть в {target.kind === "clip" ? "редакторе" : "проекте"}
        </Link>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Шаги
// --------------------------------------------------------------------------- //

function ApprovalPreview({ id }: { id: number }) {
  const q = useQuery({ queryKey: chatKeys.approval(id), queryFn: () => chatApi.approval(id), retry: false });
  if (q.isLoading) return <div className="muted chat-small">Карточка #{id}…</div>;
  if (!q.data) return <div className="muted chat-small">Карточка #{id} недоступна</div>;
  return (
    <div className="chat-approval">
      <ApprovalCard approval={q.data} />
    </div>
  );
}

function Preview({ preview, onRef }: { preview: StepPreview; onRef: (r: MediaRef) => void }) {
  const [zoom, setZoom] = useState(false);
  if (preview.kind === "image") {
    return (
      <div className={`chat-frame${preview.sheet ? " sheet" : ""}${zoom ? " zoom" : ""}`}>
        <img src={preview.url} alt="кадр" onClick={() => setZoom((z) => !z)} />
        {preview.clip_id && preview.t != null ? (
          <button className="md-time" onClick={() => onRef({ kind: "clip", id: preview.clip_id!, t: Number(preview.t) })}>
            {preview.t} с
          </button>
        ) : null}
      </div>
    );
  }
  if (preview.kind === "clip") {
    return (
      <div className="chat-clip">
        {preview.video_url ? (
          <video src={preview.video_url} poster={preview.thumb_url} controls preload="none" />
        ) : (
          <img src={preview.thumb_url} alt="" />
        )}
        <div className="chat-clip-info">
          <button className="md-ref" onClick={() => onRef({ kind: "clip", id: preview.clip_plan_id })}>
            Клип #{preview.clip_plan_id}
          </button>
          <span className="chat-ellipsis">{preview.title}</span>
          {preview.source_id ? (
            <Link className="chat-small" to={`/v/${preview.source_id}/candidates?clip=${preview.clip_plan_id}`}>
              в редактор →
            </Link>
          ) : null}
        </div>
      </div>
    );
  }
  if (preview.kind === "approval") return <ApprovalPreview id={preview.approval.id} />;
  return (
    <ul className="chat-approvals">
      {preview.approvals.map((a) => (
        <li key={a.id}>
          <Link to={`/inbox?tab=approve&id=${a.id}`}>#{a.id}</Link> {a.title || a.question} <span className="muted">· {a.status}</span>
        </li>
      ))}
    </ul>
  );
}

function StepCard({ step, onRef }: { step: ChatStep; onRef: (r: MediaRef) => void }) {
  const [open, setOpen] = useState(false);
  if (step.kind === "say" || step.tool === "_say") return <div className="chat-say">{step.label}</div>;
  const state = step.ok === false ? "bad" : step.ok == null ? "run" : step.partial ? "warn" : "ok";
  const hasArgs = step.args && Object.keys(step.args).length > 0;
  return (
    <div className={`chat-step ${state}`}>
      <button type="button" className="chat-step-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="chat-step-ico">{state === "bad" ? "✗" : state === "run" ? <span className="chat-spin" /> : state === "warn" ? "!" : "✓"}</span>
        <span className="chat-step-label">{step.label}</span>
        <code className="chat-step-tool">{step.tool}</code>
        <span className="chat-step-chev">{open ? "▾" : "▸"}</span>
      </button>
      {step.error && !open ? <div className="chat-step-err">{step.error}</div> : null}
      {open ? (
        <div className="chat-step-body">
          {hasArgs ? (
            <>
              <div className="chat-step-cap">аргументы</div>
              <pre>{JSON.stringify(step.args, null, 1)}</pre>
            </>
          ) : null}
          {step.result ? (
            <>
              <div className="chat-step-cap">результат</div>
              <pre>{prettyResult(step.result)}</pre>
            </>
          ) : null}
          {step.error ? <div className="chat-step-err">{step.error}</div> : null}
          {step.problems?.length ? (
            <ul className="chat-step-list bad">
              {step.problems.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {step.warnings?.length ? (
        <ul className="chat-step-list warn">
          {step.warnings.slice(0, open ? 8 : 2).map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : null}
      {step.preview ? <Preview preview={step.preview} onRef={onRef} /> : null}
    </div>
  );
}

function prettyResult(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 1);
  } catch {
    return text;
  }
}

function ConfirmCard({
  step,
  conf,
  threadId,
}: {
  step: ChatStep;
  conf?: ChatConfirmation;
  threadId: number;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const decide = useMutation({
    mutationFn: (approve: boolean) => chatApi.confirm(step.confirmation_id!, approve),
    onSuccess: (r) => {
      if (r.status === "failed") toast.error("Не вышло — подробности в чате");
      qc.invalidateQueries({ queryKey: chatKeys.thread(threadId) });
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (e) => toast.error(errText(e, "Не получилось")),
  });
  const status = conf?.status ?? step.confirm_status ?? "pending";
  const args = step.args ? Object.entries(step.args).filter(([k]) => k !== "_truncated") : [];
  return (
    <div className={`chat-confirm ${status}`}>
      <div className="chat-confirm-head">
        <span className="chat-confirm-badge">Нужно подтверждение</span>
        <strong>{step.danger || step.label}</strong>
      </div>
      <div className="chat-small">
        {step.label}
        {args.length ? (
          <span className="muted"> · {args.map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(", ")}</span>
        ) : null}
      </div>
      {status === "pending" && step.confirmation_id ? (
        <div className="chat-confirm-actions">
          <button className="btn primary sm" disabled={decide.isPending} onClick={() => decide.mutate(true)}>
            Подтвердить
          </button>
          <button className="btn ghost sm" disabled={decide.isPending} onClick={() => decide.mutate(false)}>
            Отмена
          </button>
        </div>
      ) : (
        <div className={`chat-small chat-confirm-status ${status}`}>
          {status === "done" ? "✓ Выполнено" : status === "rejected" ? "Отменено" : status === "failed" ? `✗ ${step.error || "Не вышло"}` : "…"}
        </div>
      )}
      {step.preview ? <Preview preview={step.preview} onRef={() => undefined} /> : null}
    </div>
  );
}

function Changes({ changes, threadId, onRef }: { changes: ChatChange[]; threadId: number; onRef: (r: MediaRef) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const revert = useMutation({
    mutationFn: (cp: number) => chatApi.revert(cp),
    onSuccess: () => {
      toast.success("Откатил");
      qc.invalidateQueries({ queryKey: chatKeys.thread(threadId) });
    },
    onError: (e) => toast.error(errText(e, "Не откатилось")),
  });
  return (
    <div className="chat-changes">
      {changes.map((c) => (
        <div key={c.checkpoint_id} className={`chat-change${c.reverted ? " reverted" : ""}`}>
          <span className="chat-change-ico">✎</span>
          <button className="md-ref" onClick={() => onRef({ kind: "clip", id: c.clip_plan_id })}>
            Клип #{c.clip_plan_id}
          </button>
          <span className="chat-ellipsis muted">{c.title}</span>
          {c.reverted ? (
            <span className="muted chat-small">откачено</span>
          ) : (
            <button className="btn ghost sm" disabled={revert.isPending} onClick={() => revert.mutate(c.checkpoint_id)} title="Вернуть клип к состоянию до этого ответа (Ctrl+Z в редакторе тоже работает)">
              ↶ Откатить
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function AssistantMessage({
  m,
  live,
  threadId,
  onRef,
}: {
  m: ChatMsg;
  live: LiveRun | null;
  threadId: number;
  onRef: (r: MediaRef) => void;
}) {
  const running = m.status === "running";
  const steps = running && live && live.reply_id === m.id ? live.steps : m.steps;
  const text = running ? (live && live.reply_id === m.id ? live.text : m.text) : m.text;
  const phase = running ? live?.phase ?? "thinking" : null;
  const confs = new Map((m.confirmations ?? []).map((c) => [c.id, c]));
  const ref = messageRef({ ...m, steps });
  if (m.meta?.service) return <div className="chat-service">{m.text}</div>;
  return (
    <div className={`chat-msg assistant status-${m.status}`}>
      <div className="chat-avatar" aria-hidden>
        ✦
      </div>
      <div className="chat-msg-body">
        {steps.length ? (
          <div className="chat-steps">
            {steps.map((s, i) =>
              s.kind === "confirm" ? (
                <ConfirmCard key={i} step={s} conf={s.confirmation_id ? confs.get(s.confirmation_id) : undefined} threadId={threadId} />
              ) : (
                <StepCard key={i} step={s} onRef={onRef} />
              ),
            )}
          </div>
        ) : null}
        {text ? (
          <div className={`chat-text${running ? " live" : ""}`}>
            <Markdown text={text} defaultRef={ref} onRef={onRef} />
          </div>
        ) : null}
        {running ? (
          <div className="chat-phase">
            <span className="chat-dots">
              <i />
              <i />
              <i />
            </span>
            {PHASE_LABEL[phase ?? "thinking"] ?? "думает…"}
          </div>
        ) : null}
        {m.meta?.remembered && !m.text.includes("Запомнил") ? (
          <div className="chat-remembered">
            Запомнил: «{m.meta.remembered.text}» · <Link to={projectPath(m.meta.remembered.project_id, "know")}>что агент знает</Link>
          </div>
        ) : null}
        {m.changes?.length ? <Changes changes={m.changes} threadId={threadId} onRef={onRef} /> : null}
        {m.status === "error" || m.status === "interrupted" || m.status === "cancelled" ? (
          <div className={`chat-small chat-status-${m.status}`}>
            {m.status === "error" ? "Ошибка ответа" : m.status === "cancelled" ? "Остановлено" : "Прервано"}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Поле ввода
// --------------------------------------------------------------------------- //

interface Pending {
  key: string;
  file: File;
  target: "asset" | "source";
  state: "uploading" | "ready" | "error";
  att?: ChatAttachment;
  error?: string;
}

const isVideo = (f: File) => f.type.startsWith("video/") || /\.(mp4|mov|mkv|webm|m4v)$/i.test(f.name);

function Composer({
  disabled,
  running,
  onSend,
  onStop,
  projectId,
}: {
  disabled: boolean;
  running: boolean;
  /** Чат проекта: приложенный файл становится файлом этого проекта. */
  projectId?: number | null;
  onSend: (text: string, attachments: ChatAttachment[]) => Promise<unknown>;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<Pending[]>([]);
  const [drag, setDrag] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(220, el.scrollHeight)}px`;
  }, [text]);

  const upload = useCallback((p: Pending) => {
    const run = p.target === "source" ? chatApi.uploadSource(p.file) : chatApi.uploadAsset(p.file, projectId);
    run
      .then((r) => {
        const att: ChatAttachment =
          p.target === "source"
            ? { kind: "source", id: r.id, name: p.file.name, media: "video" }
            : { kind: "asset", id: r.id, name: p.file.name, media: (r as { kind?: string }).kind ?? "" };
        setFiles((list) => list.map((x) => (x.key === p.key ? { ...x, state: "ready", att } : x)));
      })
      .catch((e) => setFiles((list) => list.map((x) => (x.key === p.key ? { ...x, state: "error", error: errText(e, "не загрузилось") } : x))));
  }, []);

  const addFiles = (list: FileList | File[]) => {
    const added = Array.from(list).map((file) => ({
      key: `${file.name}-${file.size}-${Math.random()}`,
      file,
      target: "asset" as const,
      state: "uploading" as const,
    }));
    setFiles((prev) => [...prev, ...added]);
    added.forEach(upload);
  };

  const retarget = (p: Pending) => {
    // Видео можно отдать как источник для нарезки, а не в «Файлы для монтажа».
    const next: Pending = { ...p, target: p.target === "asset" ? "source" : "asset", state: "uploading", att: undefined };
    setFiles((list) => list.map((x) => (x.key === p.key ? next : x)));
    upload(next);
  };

  const uploading = files.some((f) => f.state === "uploading");
  const ready = files.filter((f) => f.state === "ready" && f.att).map((f) => f.att!);
  const canSend = !disabled && !running && !uploading && (text.trim().length > 0 || ready.length > 0);
  const submit = () => {
    if (!canSend) return;
    const body = text.trim();
    onSend(body, ready).then(() => {
      setText("");
      setFiles([]);
    });
  };
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    if (e.clipboardData.files.length) {
      e.preventDefault();
      addFiles(e.clipboardData.files);
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };
  const link = /https?:\/\/\S+/.exec(text)?.[0];

  return (
    <div
      className={`chat-composer${drag ? " drag" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={onDrop}
    >
      {files.length ? (
        <div className="chat-files">
          {files.map((f) => (
            <div key={f.key} className={`chat-file ${f.state}`}>
              <span className="chat-ellipsis">{f.file.name}</span>
              <span className="muted chat-small">
                {f.state === "uploading" ? "загружаю…" : f.state === "error" ? f.error : f.target === "source" ? "→ новое видео" : "→ файлы для монтажа"}
              </span>
              {isVideo(f.file) && f.state !== "uploading" ? (
                <button className="chat-file-btn" onClick={() => retarget(f)} title="Куда загрузить видео">
                  {f.target === "asset" ? "сделать новым видео" : "в файлы для монтажа"}
                </button>
              ) : null}
              <button className="chat-file-btn" onClick={() => setFiles((list) => list.filter((x) => x.key !== f.key))} aria-label="Убрать">
                ✕
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <div className="chat-input-row">
        <button className="chat-icon-btn" onClick={() => picker.current?.click()} title="Прикрепить видео, картинку или звук" aria-label="Прикрепить файл">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21.4 11.1 12.2 20.3a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" />
          </svg>
        </button>
        <input
          ref={picker}
          type="file"
          hidden
          multiple
          accept="video/*,image/*,audio/*,.gif"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <textarea
          ref={area}
          className="chat-textarea"
          rows={1}
          value={text}
          placeholder="Напиши агенту…"
          title="Enter — отправить, Shift+Enter — перенос строки"
          onChange={(e) => setText(e.target.value)}
          onPaste={onPaste}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {running ? (
          <button className="btn chat-stop" onClick={onStop} title="Остановить ответ">
            <span className="chat-stop-sq" /> Стоп
          </button>
        ) : (
          <button className="btn primary chat-send" disabled={!canSend} onClick={submit} aria-label="Отправить">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
            </svg>
          </button>
        )}
      </div>
      {link ? <div className="chat-hint muted">Ссылка уйдёт агенту: видео — скачает как новое видео, картинку или мем — вставит в клип.</div> : null}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Переписка
// --------------------------------------------------------------------------- //

/** «Запомнить» у своей реплики: факт проекта без модели и без волшебных слов. */
function RememberButton({ m, view }: { m: ChatMsg; view: ChatThreadView }) {
  const qc = useQueryClient();
  const toast = useToast();
  const projectId = view.project?.id;
  const [done, setDone] = useState(false);
  const remember = useMutation({
    mutationFn: () => projectsApi.addFact(projectId!, m.text, { source_id: view.video?.id, origin_ref: `chat#${m.id}` }),
    onSuccess: () => {
      setDone(true);
      qc.invalidateQueries({ queryKey: qk.channelKnowledge(projectId!) });
      toast.success("Запомнил, это видно в «Что агент знает»");
    },
    onError: (e) => toast.error(errText(e, "Не запомнилось")),
  });
  if (!projectId || m.text.length > MAX_FACT_LEN) return null;
  return (
    <button className="chat-remember" disabled={done || remember.isPending} onClick={() => remember.mutate()} title="Записать эту реплику в то, что агент знает о проекте">
      {done ? "Запомнил" : "Запомнить"}
    </button>
  );
}

export function ChatConversation({
  threadId,
  compact,
  header,
}: {
  threadId: number;
  compact?: boolean;
  header?: (view: ChatThreadView | undefined) => ReactNode;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const listRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [media, setMedia] = useState<MediaRef | null>(null);
  const view = useQuery({
    queryKey: chatKeys.thread(threadId),
    queryFn: () => chatApi.thread(threadId),
    refetchInterval: (q) => (q.state.data?.running ? 5000 : false),
  });
  const running = !!view.data?.running;
  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: chatKeys.thread(threadId) });
    qc.invalidateQueries({ queryKey: chatKeys.threads });
  }, [qc, threadId]);
  const liveSse = useLiveRun(threadId, running, refresh);
  const live = liveSse ?? view.data?.live ?? null;

  const send = useMutation({
    mutationFn: ({ text, attachments }: { text: string; attachments: ChatAttachment[] }) => chatApi.send(threadId, text, attachments),
    onSuccess: () => {
      stick.current = true;
      refresh();
    },
    onError: (e) => toast.error(errText(e, "Не отправилось")),
  });
  const stop = useMutation({ mutationFn: () => chatApi.cancel(threadId), onSuccess: refresh });

  const messages = view.data?.messages ?? [];
  const lastLen = (live?.text.length ?? 0) + (live?.steps.length ?? 0) * 1000;
  useEffect(() => {
    const el = listRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages.length, lastLen, running]);
  useEffect(() => {
    stick.current = true;
    setMedia(null);
  }, [threadId]);

  const onRef = useCallback((r: MediaRef) => setMedia(r), []);
  const groups = useMemo(() => (view.data?.tool_groups ?? []).map((g) => GROUP_LABEL[g] ?? g), [view.data?.tool_groups]);

  if (view.isLoading) return <Loading />;
  if (view.isError) return <div className="muted chat-empty">Переписка не открылась: {errText(view.error, "ошибка")}</div>;
  const ai = view.data!.ai;

  return (
    <div className={`chat-conv${compact ? " compact" : ""}`}>
      {header ? header(view.data) : null}
      <div
        className="chat-scroll"
        ref={listRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
        onLoadCapture={(e) => {
          // Кадры и обложки грузятся позже текста — держим низ переписки в кадре.
          const el = e.currentTarget;
          if (stick.current) el.scrollTop = el.scrollHeight;
        }}
      >
        <div className="chat-thread">
          {!ai.available ? (
            <div className="chat-banner warn">
              <strong>ИИ-агент недоступен.</strong> {ai.hint}
            </div>
          ) : null}
          {view.data!.summary ? (
            <details className="chat-summary">
              <summary>Резюме прежней переписки</summary>
              <pre>{view.data!.summary}</pre>
            </details>
          ) : null}
          {!messages.length ? (
            <div className="chat-empty">
              <div className="chat-empty-mark">✦</div>
              <div className="chat-empty-title">
                {view.data!.video ? `Агент видео «${view.data!.video.title}»` : view.data!.project ? `Агент проекта «${view.data!.project.name}»` : "Агент сервиса"}
              </div>
              <p className="muted">
                Пиши как обычному ассистенту: агент читает проекты и клипы, правит монтаж, субтитры и звук, запускает нарезку. Опасное — публикацию и
                удаление — сделает только после твоей кнопки. Правки клипа откатываются.
              </p>
              <div className="chat-suggest">
                {suggestionsFor(view.data!).map((s) => (
                  <button key={s} className="agent-chip" disabled={!ai.available || send.isPending} onClick={() => send.mutate({ text: s, attachments: [] })}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={m.id} className="chat-msg user">
                <div className="chat-bubble">
                  {m.text}
                  {m.meta?.attachments?.length ? (
                    <div className="chat-atts">
                      {m.meta.attachments.map((a) => (
                        <span key={`${a.kind}${a.id}`} className="chat-att">
                          {a.kind === "source" ? "видео" : "файл"} #{a.id} · {a.name}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
                <div className="chat-msg-foot">
                  {m.origin === "tg" ? <span className="muted chat-small">из Telegram</span> : null}
                  {messages[i + 1]?.meta?.remembered ? null : <RememberButton m={m} view={view.data!} />}
                </div>
              </div>
            ) : (
              <AssistantMessage key={m.id} m={m} live={live} threadId={threadId} onRef={onRef} />
            ),
          )}
        </div>
      </div>
      {media ? <MediaPanel target={media} onClose={() => setMedia(null)} /> : null}
      <div className="chat-bottom">
        <Composer
          projectId={view.data?.project?.id}
          disabled={false}
          running={running || send.isPending}
          onSend={(text, attachments) => send.mutateAsync({ text, attachments })}
          onStop={() => stop.mutate()}
        />
        <div className="chat-foot muted">
          Enter — отправить · Shift+Enter — перенос · {view.data!.model}
          {groups.length ? ` · подгружено: ${groups.join(", ")}` : ""}
          {view.data!.project?.auto_rules ? ` · правила из разговора: ${view.data!.project.auto_rules === "auto" ? "сразу" : "с вопросом"}` : ""}
        </div>
      </div>
    </div>
  );
}
