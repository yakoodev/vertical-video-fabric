import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { navKeys } from "@/api/nav";
import { clipQuestionsKey, streamQuestionsApi as Q, streamQuestionsKey, type ClipQuestionsState, type QuestionLevel, type StreamQuestion } from "@/api/streamQuestions";
import { useToast } from "@/components/Toast";
import { MontagePause } from "@/pages/workspace/MontagePause";
import { levelMovedText, QuestionCard } from "@/pages/workspace/StreamQuestionsTab";

/** Экран клипа: только вопросы про этот клип. Нет их и монтаж не стоит — блока нет. */
export function ClipQuestions({ clipPlanId }: { clipPlanId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const key = clipQuestionsKey(clipPlanId);
  const query = useQuery({ queryKey: key, queryFn: () => Q.clip(clipPlanId) });
  const data = query.data;
  const expanded = useRef(false);

  const keep = (fresh: StreamQuestion) => {
    qc.setQueryData<ClipQuestionsState>(key, (old) =>
      old ? { ...old, questions: old.questions.map((q) => (q.id === fresh.id ? fresh : q)) } : old);
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: navKeys.all });
    qc.invalidateQueries({ queryKey: ["stream-questions"] });
    qc.invalidateQueries({ queryKey: streamQuestionsKey(fresh.source_id) });
    if (data?.project_id) qc.invalidateQueries({ queryKey: qk.channelKnowledge(data.project_id) });
  };
  const problem = (e: unknown, fallback: string) => toast.error(e instanceof ApiError ? e.message : fallback);
  const answer = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { option: number | null; text: string; skip?: boolean } }) => Q.answer(id, body),
    onSuccess: keep,
    onError: (e) => problem(e, "Ответ не сохранился"),
  });
  const changeLevel = useMutation({
    mutationFn: ({ id, level }: { id: number; level: QuestionLevel }) => Q.setLevel(id, level),
    onSuccess: (fresh) => {
      keep(fresh);
      toast.push(levelMovedText(fresh), "info");
    },
    onError: (e) => problem(e, "Не получилось"),
  });

  if (!data || (!data.questions.length && !data.gate.paused)) return null;
  const open = data.questions.filter((q) => q.status === "open").length;
  // Ответил — блок не схлопывается под рукой: уровень можно поправить сразу.
  if (open > 0) expanded.current = true;
  return (
    <div className="sq-clip">
      <MontagePause sourceId={data.source_id} gate={data.gate} projectId={data.project_id} compact />
      {data.questions.length ? (
        <details className="ccard sq-clip-box" open={expanded.current}>
          <summary>
            Вопросы про этот клип <span className="ccard-n">{open ? `${open} без ответа` : `${data.questions.length}, всё отвечено`}</span>
          </summary>
          <div className="sq-list">
            {data.questions.map((item) => (
              <QuestionCard
                key={item.id}
                item={item}
                inProject={Boolean(data.project_id)}
                onSave={(body) => answer.mutateAsync({ id: item.id, body })}
                onLevel={(level) => changeLevel.mutateAsync({ id: item.id, level })}
              />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
