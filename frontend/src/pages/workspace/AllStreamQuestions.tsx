import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { navKeys } from "@/api/nav";
import { streamQuestionsApi as Q, streamQuestionsAllKey, streamQuestionsKey, type StreamQuestionsAll } from "@/api/streamQuestions";
import { ShowMore, useShowMore } from "@/components/ShowMore";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import { videoPath } from "@/lib/paths";
import { MemeCard, QuestionCard } from "@/pages/workspace/StreamQuestionsTab";
import "@/styles/stream-questions.css";

/**
 * Вопросы и мемы агента сразу по многим видео: «Входящие → Вопросы» (все проекты) и вкладка «Вопросы»
 * проекта (projectId). Карточки — те же, что на странице видео. Отвеченное остаётся на месте до
 * следующего открытия, чтобы лента не прыгала под рукой.
 */
export function AllStreamQuestions({ projectId }: { projectId?: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [picked, setPicked] = useState<string>("");
  const scope = projectId ?? (picked ? Number(picked) : undefined);
  const key = useMemo(() => streamQuestionsAllKey(scope, onlyOpen), [scope, onlyOpen]);
  const query = useQuery({ queryKey: key, queryFn: () => Q.all(scope, onlyOpen) });
  const problem = (e: unknown, fallback: string) => toast.error(e instanceof ApiError ? e.message : fallback);

  const refresh = (sourceId: number) => {
    qc.invalidateQueries({ queryKey: navKeys.all });
    qc.invalidateQueries({ queryKey: streamQuestionsKey(sourceId) });
  };
  const answer = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { option: number | null; text: string; skip?: boolean } }) => Q.answer(id, body),
    onSuccess: (fresh) => {
      qc.setQueryData<StreamQuestionsAll>(key, (old) =>
        old ? { ...old, questions: old.questions.map((x) => (x.id === fresh.id ? fresh : x)) } : old);
      refresh(fresh.source_id);
      if (fresh.scope === "channel" && fresh.status === "answered" && fresh.brief && !fresh.brief.written) {
        toast.push(fresh.brief.reason ?? "В лор проекта ответ не записан", "info");
      }
    },
    onError: (e) => problem(e, "Ответ не сохранился"),
  });
  const choose = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { option_id?: number | null; none?: boolean } }) => Q.choose(id, body),
    onSuccess: (fresh) => {
      qc.setQueryData<StreamQuestionsAll>(key, (old) =>
        old ? { ...old, moments: old.moments.map((x) => (x.id === fresh.id ? fresh : x)) } : old);
      refresh(fresh.source_id);
    },
    onError: (e) => problem(e, "Выбор не сохранился"),
  });

  const data = query.data;
  const cards = useMemo(() => {
    if (!data) return [];
    const names = new Map(data.sources.map((s) => [s.id, s]));
    return [
      ...data.questions.map((item) => ({ key: `q${item.id}`, sourceId: item.source_id, question: item, moment: null, source: names.get(item.source_id) })),
      ...data.moments.map((item) => ({ key: `m${item.id}`, sourceId: item.source_id, question: null, moment: item, source: names.get(item.source_id) })),
    ].sort((a, b) => b.sourceId - a.sourceId);
  }, [data]);
  const shown = useShowMore(cards, `${scope}|${onlyOpen}`);

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;

  return (
    <div className="sq-page">
      <div className="sq-head sq-head-all">
        {projectId == null ? (
          <select className="input" style={{ width: 230 }} aria-label="Проект" value={picked} onChange={(e) => setPicked(e.target.value)}>
            <option value="">Все проекты</option>
            {(data?.open_by_project ?? []).filter((p) => p.project_id != null).map((p) => (
              <option key={p.project_id} value={p.project_id ?? ""}>
                {p.name} · {p.open}
              </option>
            ))}
          </select>
        ) : null}
        <label className="sq-filter">
          <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} />
          Только без ответа
        </label>
      </div>
      {cards.length ? (
        <div className="sq-list">
          {shown.visible.map((card) => (
            <div key={card.key} className="sq-all-item">
              <div className="sq-all-from">
                {projectId == null && card.source?.project_name ? <span>{card.source.project_name}</span> : null}
                <Link to={videoPath(card.sourceId, "questions")}>{card.source?.title || `Видео #${card.sourceId}`}</Link>
              </div>
              {card.question ? (
                <QuestionCard item={card.question} onSave={(body) => answer.mutateAsync({ id: card.question!.id, body })} />
              ) : (
                <MemeCard moment={card.moment!} onChoose={(body) => choose.mutate({ id: card.moment!.id, body })} />
              )}
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          icon="💬"
          title={onlyOpen ? "Вопросов без ответа нет" : "Вопросов пока нет"}
          hint="Они появляются, когда агент смотрит стрим и чего-то не понимает."
        />
      )}
      <ShowMore rest={shown.rest} onMore={shown.more} />
    </div>
  );
}
