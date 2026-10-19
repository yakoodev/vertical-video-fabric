/**
 * ИИ-ассистент студии: пишешь, что сделать с клипом, — он сам ставит титры,
 * мемы, звуки, наезды, смотрит кадры и отчитывается списком шагов. Ход идёт на
 * сервере в фоне; здесь — переписка и живые шаги (опрос раз в секунду, пока
 * агент работает). Каждый ход — один снимок истории, так что Ctrl+Z
 * откатывает его целиком.
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";

type Step = { tool: string; label: string; ok: boolean | null; problems?: string[] };
type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
  steps: Step[];
  status: string;
  model?: string;
  created_at?: string;
};
type ChatView = { messages: Message[]; running: boolean; run_id?: string | null; model?: string };

const SUGGESTIONS = [
  "Сделай динамичнее: титры на панчлайны, наезды на реакции, звуки на акценты",
  "Поставь крупный хук-титр в первые 2 секунды",
  "Добавь мем или эмодзи на самый смешной момент",
  "Если в клипе звучат числа — сделай под них инфографику",
  "Подпиши стримера плашкой «нижняя треть»",
  "Убери всё лишнее с кадра, оставь только самое сильное",
];

export function AgentPanel({
  clipPlanId,
  onChanged,
  onRunningChange,
}: {
  clipPlanId: number;
  onChanged: () => void;
  onRunningChange?: (running: boolean) => void;
}) {
  const toast = useToast();
  const [text, setText] = useState("");
  const chat = useQuery({
    queryKey: ["clip-agent", clipPlanId],
    queryFn: () => api.get<ChatView>(`/api/clip-plans/${clipPlanId}/agent`),
    refetchInterval: (q) => (q.state.data?.running ? 1000 : false),
  });
  const send = useMutation({
    mutationFn: (message: string) => api.post(`/api/clip-plans/${clipPlanId}/agent`, { message }),
    onSuccess: async () => {
      setText("");
      const fresh = await chat.refetch();
      // Агент упал сразу (нет ключа, ошибка модели): «работает» так и не появилось —
      // возвращаем редактору автосохранение и подтягиваем то, что успело поменяться.
      if (!fresh.data?.running) {
        onRunningChange?.(false);
        onChanged();
      }
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось отправить"),
  });
  const cancel = useMutation({
    mutationFn: () => api.post(`/api/clip-plans/${clipPlanId}/agent/cancel`, {}),
    onSuccess: () => void chat.refetch(),
  });
  const clear = useMutation({
    mutationFn: () => api.del(`/api/clip-plans/${clipPlanId}/agent`),
    onSuccess: () => void chat.refetch(),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось очистить"),
  });

  // Ход закончился — подтягиваем в редактор то, что агент поменял в клипе.
  const wasRunning = useRef(false);
  const running = Boolean(chat.data?.running);
  useEffect(() => {
    onRunningChange?.(running);
    if (wasRunning.current && !running) onChanged();
    wasRunning.current = running;
  }, [running]); // eslint-disable-line react-hooks/exhaustive-deps
  // Ушли со вкладки ИИ — флаг «агент работает» не должен навсегда выключить
  // автосохранение редактора. Клип перечитаем, чтобы не затереть правки агента.
  useEffect(
    () => () => {
      onRunningChange?.(false);
      if (wasRunning.current) onChanged();
    },
    [clipPlanId], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const listRef = useRef<HTMLDivElement>(null);
  const messages = chat.data?.messages ?? [];
  const lastSteps = messages[messages.length - 1]?.steps.length ?? 0;
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, lastSteps]);

  const submit = (message: string) => {
    const m = message.trim();
    if (!m || running || send.isPending) return;
    // Сначала несохранённые правки панели уезжают на сервер — агент должен видеть их.
    onRunningChange?.(true);
    send.mutate(m, { onError: () => onRunningChange?.(false) });
  };

  return (
    <div className="agent">
      <div className="agent-head">
        <strong>✨ ИИ-ассистент</strong>
        <span className={`agent-dot${running ? " on" : ""}`} title={running ? "работает" : "ждёт"} />
        <span className="muted agent-model">{chat.data?.model ?? ""}</span>
        {messages.length && !running ? (
          <button type="button" className="btn ghost sm" onClick={() => clear.mutate()} title="Очистить переписку">
            🧹
          </button>
        ) : null}
      </div>
      <div className="agent-list" ref={listRef}>
        {!messages.length ? (
          <div className="agent-empty muted">
            Опишите, что сделать с клипом. Ассистент сам расставит титры, мемы, звуки и наезды, посмотрит кадры и
            отчитается. Не понравится — Ctrl+Z откатит весь его ход.
          </div>
        ) : null}
        {messages.map((m) => (
          <div key={m.id} className={`agent-msg agent-msg--${m.role}`}>
            {m.role === "assistant" && m.steps.length ? (
              <ul className="agent-steps">
                {m.steps.map((s, i) =>
                  s.tool === "_say" ? (
                    <li key={i} className="say">
                      <span className="agent-step-ico">💭</span>
                      <span>{s.label}</span>
                    </li>
                  ) : (
                    <li key={i} className={s.ok === false ? "bad" : s.ok ? "ok" : "run"}>
                      <span className="agent-step-ico">{s.ok === false ? "✕" : s.ok ? "✓" : "…"}</span>
                      <span>{s.label}</span>
                      {s.problems?.length ? <em className="muted">{s.problems.slice(0, 2).join("; ")}</em> : null}
                    </li>
                  ),
                )}
              </ul>
            ) : null}
            {m.text ? <div className="agent-text">{m.text}</div> : null}
            {m.role === "assistant" && m.status === "running" && !m.text ? (
              <div className="agent-text muted">Думаю и правлю клип…</div>
            ) : null}
          </div>
        ))}
      </div>
      <div className="agent-chips">
        {SUGGESTIONS.map((s) => (
          <button key={s} type="button" className="agent-chip" disabled={running} onClick={() => submit(s)} title={s}>
            {s}
          </button>
        ))}
      </div>
      <div className="agent-input">
        <textarea
          className="input"
          rows={2}
          value={text}
          placeholder={running ? "Ассистент работает…" : "Опишите, что изменить…  (Enter — отправить)"}
          disabled={running}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(text);
            }
          }}
        />
        {running ? (
          <button type="button" className="btn" onClick={() => cancel.mutate()}>
            ■ Стоп
          </button>
        ) : (
          <button type="button" className="btn primary" disabled={!text.trim() || send.isPending} onClick={() => submit(text)}>
            ➤
          </button>
        )}
      </div>
    </div>
  );
}
