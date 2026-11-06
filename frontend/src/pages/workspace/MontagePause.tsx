import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { streamQuestionsApi as Q, streamQuestionsKey, type MontageGate } from "@/api/streamQuestions";
import { useToast } from "@/components/Toast";
import { plural } from "@/components/ui";
import { projectPath, videoPath } from "@/lib/paths";
import "@/styles/stream-questions.css";

/**
 * Пауза монтажа: пока на вопросы видео нет ответов, агент не начинает новые клипы и вырезы
 * (app/stream_questions.py). Снять её может только владелец, здесь. Руками в редакторе всё работает как обычно.
 */
export function MontagePause({
  sourceId,
  gate,
  projectId,
  onOpen,
  compact = false,
}: {
  sourceId: number | string;
  gate: MontageGate;
  projectId: number | null;
  /** Страница видео: показать вкладку с вопросами. */
  onOpen?: () => void;
  /** Экран клипа: одна строка, главное там сам клип. */
  compact?: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: streamQuestionsKey(sourceId) });
    qc.invalidateQueries({ queryKey: ["stream-questions"] });
    qc.invalidateQueries({ queryKey: ["clip-questions"] });
  };
  const problem = (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Не получилось");
  const release = useMutation({ mutationFn: () => Q.releaseMontage(sourceId), onSuccess: refresh, onError: problem });
  const restore = useMutation({ mutationFn: () => Q.restoreMontagePause(sourceId), onSuccess: refresh, onError: problem });

  if (gate.paused && compact) {
    return (
      <div className="sq-pause sq-pause-compact" role="status">
        <span className="sq-pause-t">
          Агент ждёт ответов ({gate.open}), новые вырезы на паузе. <Link to={videoPath(sourceId, "questions")}>К вопросам видео</Link>
        </span>
        <button type="button" className="btn sm" disabled={release.isPending} onClick={() => release.mutate()}>
          Монтировать без ответов
        </button>
      </div>
    );
  }
  if (gate.paused) {
    const own = gate.open - gate.project_open - gate.clip_open;
    return (
      <div className="sq-pause" role="status">
        <div className="sq-pause-t">
          <strong>Монтаж этого видео ждёт ваших ответов</strong>
          <p>
            Агент не начнёт новые клипы и вырезы, пока вы не ответите на {plural(gate.open, "вопрос", "вопроса", "вопросов")}.
            {own > 0 && onOpen ? (
              <>
                {" "}
                <button type="button" className="sq-pause-link" onClick={onOpen}>
                  Про это видео: {own}
                </button>
              </>
            ) : null}
            {gate.project_open > 0 && projectId ? (
              <>
                {own > 0 && onOpen ? " · " : " "}
                <Link to={projectPath(projectId, "questions")}>Про весь проект: {gate.project_open}</Link>
              </>
            ) : null}
          </p>
          <p className="sq-pause-note">Ваши правки в редакторе, рендер и просмотр монтажа работают как обычно.</p>
        </div>
        <button type="button" className="btn sm" disabled={release.isPending} onClick={() => release.mutate()}>
          Монтировать без ответов
        </button>
      </div>
    );
  }
  if (gate.released && gate.released_open > 0 && !compact) {
    return (
      <div className="sq-pause sq-pause-off" role="status">
        <div className="sq-pause-t">
          <p>
            Вы разрешили монтировать без ответов: агент не ждёт {plural(gate.released_open, "вопрос", "вопроса", "вопросов")}.
            Новые вопросы снова поставят монтаж на паузу.
          </p>
        </div>
        <button type="button" className="btn ghost sm" disabled={restore.isPending} onClick={() => restore.mutate()}>
          Снова ждать ответов
        </button>
      </div>
    );
  }
  return null;
}
