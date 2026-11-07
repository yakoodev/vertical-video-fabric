/**
 * Чат клипа: тот же компонент, что на всех уровнях (ChatConversation), и та же переписка в редакторе, на экране клипа, в
 * панели поверх экрана и в «Все переписки». Пишешь, что сделать с клипом, — агент сам ставит титры, мемы, звуки, наезды,
 * смотрит кадры и отчитывается. Здесь — только то, что нужно экрану с открытым клипом: пока агент работает, автосохранение
 * ждёт, а когда кончил, экран перечитывает клип. Ход — один снимок истории: Ctrl+Z откатывает его целиком.
 */
import { Suspense, type ComponentProps } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { chatApi, chatKeys } from "@/api/chat";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";
import { lazyPage } from "@/lazyPage";

const ChatConversation = lazyPage<ComponentProps<typeof import("@/pages/chat/ChatConversation").ChatConversation>>(
  () => import("@/pages/chat/ChatConversation"),
  "ChatConversation",
);

const SUGGESTIONS = [
  "Сделай динамичнее: титры на панчлайны, наезды на реакции, звуки на акценты",
  "Поставь крупный хук-титр в первые 2 секунды",
  "Добавь мем или эмодзи на самый смешной момент",
  "Если в клипе звучат числа — сделай под них инфографику",
  "Подпиши стримера плашкой «нижняя треть»",
  "Убери всё лишнее с кадра, оставь только самое сильное",
];

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function ClipChatHead({ threadId, running, model, hasMessages }: { threadId: number; running: boolean; model: string; hasMessages: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const clear = useMutation({
    mutationFn: () => chatApi.clear(threadId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: chatKeys.thread(threadId) });
      qc.invalidateQueries({ queryKey: chatKeys.threads });
    },
    onError: (e) => toast.error(errText(e, "Не удалось очистить")),
  });
  return (
    <div className="agent-head">
      <span className={`agent-dot${running ? " on" : ""}`} title={running ? "работает" : "ждёт"} />
      <span className="muted agent-model">{model}</span>
      {hasMessages && !running ? (
        <button
          type="button"
          className="btn ghost sm"
          disabled={clear.isPending}
          title="Очистить переписку: правки клипа останутся"
          onClick={() => window.confirm("Очистить переписку клипа? Правки клипа останутся.") && clear.mutate()}
        >
          Очистить
        </button>
      ) : null}
      <Link className="btn ghost sm" to={`/chat/${threadId}`} title="Эта же переписка на весь экран">
        На весь экран
      </Link>
    </div>
  );
}

export function AgentPanel({
  clipPlanId,
  onChanged,
  onRunningChange,
  compact = false,
}: {
  clipPlanId: number;
  onChanged: () => void;
  onRunningChange?: (running: boolean) => void;
  /** На экране клипа: без заголовка и подсказок, чат в узкой колонке. */
  compact?: boolean;
}) {
  const thread = useQuery({
    queryKey: chatKeys.clipThread(clipPlanId),
    queryFn: () => chatApi.clipThread(clipPlanId),
    staleTime: Infinity,
  });
  return (
    <div className={`agent${compact ? " agent--compact" : ""}`}>
      {thread.isLoading ? <Loading /> : null}
      {thread.isError ? <p className="muted">Чат не открылся: {errText(thread.error, "ошибка")}</p> : null}
      {thread.data ? (
        <Suspense fallback={<Loading />}>
          <ChatConversation
            key={thread.data.id}
            threadId={thread.data.id}
            compact
            bridge={{ onRunningChange, onChanged }}
            suggestions={[]}
            chips={compact ? undefined : SUGGESTIONS}
            placeholder="Что поменять в этом клипе"
            emptyHint={
              compact
                ? "С агентом по этому монтажу ещё не говорили. Опишите, что поменять: он сам расставит титры, мемы, звуки и наезды, правки появятся в версиях."
                : "Опишите, что сделать с клипом. Ассистент сам расставит титры, мемы, звуки и наезды, посмотрит кадры и отчитается. Не понравится — «Откатить» в чате или Ctrl+Z."
            }
            header={(view) => (
              <ClipChatHead
                threadId={thread.data.id}
                running={!!view?.running}
                model={view?.model ?? ""}
                hasMessages={!!view?.messages.length}
              />
            )}
          />
        </Suspense>
      ) : null}
    </div>
  );
}
