import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { navKeys } from "@/api/nav";
import {
  streamQuestionsApi as Q,
  streamQuestionsAllKey,
  streamQuestionsKey,
  type MemeMoment,
  type QuestionLevel,
  type StreamQuestion,
  type StreamQuestionsAll,
} from "@/api/streamQuestions";
import { ShowMore, useShowMore } from "@/components/ShowMore";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import { clipPath, videoPath } from "@/lib/paths";
import { levelMovedText, MemeCard, QuestionCard } from "@/pages/workspace/StreamQuestionsTab";
import "@/styles/stream-questions.css";

type Section = QuestionLevel | "memes";
const SECTIONS: [Section, string, string][] = [
  ["project", "Проект", "Про весь проект: ответ сразу работает на всех его видео"],
  ["video", "Видео", "Только про одно видео"],
  ["clip", "Клип", "Только про один клип"],
  ["memes", "Мемы", "Какой мем поставить в момент видео"],
];
type Card = {
  key: string;
  section: Section;
  group: string;
  sourceId: number;
  question: StreamQuestion | null;
  moment: MemeMoment | null;
};

/**
 * Вопросы и мемы сразу по многим видео: «Входящие → Вопросы» (все проекты) и вкладка «Вопросы»
 * проекта (projectId). Разложены по уровням: проект, видео, клип, а не общей кучей. Карточки — те же,
 * что на странице видео. Отвеченное и перенесённое на другой уровень остаётся на месте до следующего
 * открытия, чтобы лента не прыгала под рукой.
 */
export function AllStreamQuestions({ projectId }: { projectId?: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const asked = params.get("level");
  const filter: Section | "all" = SECTIONS.some(([s]) => s === asked) ? (asked as Section) : "all";
  const setFilter = (next: Section | "all") =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next === "all") p.delete("level");
        else p.set("level", next);
        return p;
      },
      { replace: true },
    );
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [picked, setPicked] = useState<string>("");
  const scope = projectId ?? (picked ? Number(picked) : undefined);
  const key = useMemo(() => streamQuestionsAllKey(scope, onlyOpen), [scope, onlyOpen]);
  const query = useQuery({ queryKey: key, queryFn: () => Q.all(scope, onlyOpen) });
  const problem = (e: unknown, fallback: string) => toast.error(e instanceof ApiError ? e.message : fallback);

  const refresh = (sourceId: number, owner?: number | null) => {
    qc.invalidateQueries({ queryKey: navKeys.all });
    qc.invalidateQueries({ queryKey: streamQuestionsKey(sourceId) });
    qc.invalidateQueries({ queryKey: ["clip-questions"] });
    if (owner) {
      qc.invalidateQueries({ queryKey: qk.channelKnowledge(owner) });
      qc.invalidateQueries({ queryKey: qk.channelBrief(owner) });
    }
  };
  const ownerOf = (sourceId: number) => query.data?.sources.find((s) => s.id === sourceId)?.project_id;
  const put = (fresh: StreamQuestion) => {
    qc.setQueryData<StreamQuestionsAll>(key, (old) =>
      old
        ? {
            ...old,
            questions: old.questions.map((x) => (x.id === fresh.id ? fresh : x)),
            sources: fresh.gate ? old.sources.map((s) => (s.id === fresh.source_id ? { ...s, paused: fresh.gate!.paused } : s)) : old.sources,
          }
        : old);
    refresh(fresh.source_id, ownerOf(fresh.source_id));
  };
  const answer = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { option: number | null; text: string; skip?: boolean } }) => Q.answer(id, body),
    onSuccess: (fresh) => {
      put(fresh);
      if (fresh.scope === "channel" && fresh.status === "answered" && fresh.brief && !fresh.brief.written) {
        toast.push(fresh.brief.reason ?? "В лор проекта ответ не записан", "info");
      }
    },
    onError: (e) => problem(e, "Ответ не сохранился"),
  });
  const changeLevel = useMutation({
    mutationFn: ({ id, level }: { id: number; level: QuestionLevel }) => Q.setLevel(id, level),
    onSuccess: (fresh) => {
      put(fresh);
      toast.push(fresh.brief?.written === false && fresh.brief.reason ? fresh.brief.reason : levelMovedText(fresh), "info");
    },
    onError: (e) => problem(e, "Не получилось"),
  });
  const choose = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { option_id?: number | null; none?: boolean } }) => Q.choose(id, body),
    onSuccess: (fresh) => {
      qc.setQueryData<StreamQuestionsAll>(key, (old) =>
        old ? { ...old, moments: old.moments.map((x) => (x.id === fresh.id ? fresh : x)) } : old);
      refresh(fresh.source_id, ownerOf(fresh.source_id));
    },
    onError: (e) => problem(e, "Выбор не сохранился"),
  });

  const data = query.data;
  // Раздел карточки запоминается при первом показе: сменил уровень — карточка не уезжает из-под пальца.
  const placed = useRef(new Map<number, Section>());
  useEffect(() => placed.current.clear(), [key]);
  const cards = useMemo<Card[]>(() => {
    if (!data) return [];
    const out: Card[] = [];
    for (const item of data.questions) {
      if (!placed.current.has(item.id)) placed.current.set(item.id, item.level);
      const section = placed.current.get(item.id)!;
      const group = section === "clip" ? `c${item.clip_plan_id ?? 0}` : section === "video" ? `s${item.source_id}` : "";
      out.push({ key: `q${item.id}`, section, group, sourceId: item.source_id, question: item, moment: null });
    }
    for (const item of data.moments) {
      out.push({ key: `m${item.id}`, section: "memes", group: `s${item.source_id}`, sourceId: item.source_id, question: null, moment: item });
    }
    const order = (s: Section) => SECTIONS.findIndex(([x]) => x === s);
    return out
      .filter((card) => filter === "all" || card.section === filter)
      .sort((a, b) => order(a.section) - order(b.section) || b.sourceId - a.sourceId);
  }, [data, filter]);
  const shown = useShowMore(cards, `${scope}|${onlyOpen}|${filter}`);

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;

  const sources = new Map((data?.sources ?? []).map((s) => [s.id, s]));
  const clips = new Map((data?.clips ?? []).map((c) => [c.id, c.name]));
  const countOf = (section: Section) => {
    const bucket = data?.counts?.[section];
    return bucket ? (onlyOpen ? bucket.open : bucket.total) : 0;
  };
  const totalShown = SECTIONS.reduce((sum, [section]) => sum + countOf(section), 0);

  const videoLink = (sourceId: number) => (
    <Link to={videoPath(sourceId, "questions")}>{sources.get(sourceId)?.title || `Видео #${sourceId}`}</Link>
  );
  const groupHead = (card: Card) => {
    const source = sources.get(card.sourceId);
    if (card.section === "clip") {
      const clipId = card.question?.clip_plan_id;
      return (
        <div className="sq-group">
          {clipId ? <Link to={clipPath(clipId)}>Клип «{clips.get(clipId) || `#${clipId}`}»</Link> : <span>Клип удалён</span>}
          <span className="sq-group-from">из видео {videoLink(card.sourceId)}</span>
        </div>
      );
    }
    return (
      <div className="sq-group">
        {projectId == null && source?.project_name ? <span className="sq-group-from">{source.project_name}</span> : null}
        {videoLink(card.sourceId)}
        {card.section === "video" && source?.paused ? <span className="sq-group-pause">монтаж ждёт ответов</span> : null}
      </div>
    );
  };

  let lastSection = "";
  let lastGroup = "";
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
        <div className="sq-tabs sq-levels" role="group" aria-label="Уровень вопросов">
          <button type="button" className={filter === "all" ? "active" : ""} aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
            Все <span className="mono">{totalShown}</span>
          </button>
          {SECTIONS.map(([section, label]) => (
            <button key={section} type="button" className={filter === section ? "active" : ""} aria-pressed={filter === section} onClick={() => setFilter(section)}>
              {label} <span className="mono">{countOf(section)}</span>
            </button>
          ))}
        </div>
        <label className="sq-filter">
          <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} />
          Только без ответа
        </label>
      </div>
      {cards.length ? (
        <div className="sq-list">
          {shown.visible.map((card) => {
            const newSection = card.section !== lastSection;
            const newGroup = newSection || card.group !== lastGroup;
            lastSection = card.section;
            lastGroup = card.group;
            const meta = SECTIONS.find(([s]) => s === card.section)!;
            return (
              <div key={card.key} className="sq-all-item">
                {newSection ? (
                  <h2 className="sq-section">
                    {meta[1]} <span>{meta[2]}</span>
                  </h2>
                ) : null}
                {card.section === "project" ? (
                  <div className="sq-group sq-group-small">
                    {projectId == null && sources.get(card.sourceId)?.project_name ? (
                      <span className="sq-group-from">{sources.get(card.sourceId)?.project_name}</span>
                    ) : null}
                    <span className="sq-group-from">спросил по видео {videoLink(card.sourceId)}</span>
                  </div>
                ) : newGroup ? (
                  groupHead(card)
                ) : null}
                {card.question ? (
                  <QuestionCard
                    item={card.question}
                    inProject={Boolean(sources.get(card.sourceId)?.project_id)}
                    onSave={(body) => answer.mutateAsync({ id: card.question!.id, body })}
                    onLevel={(level) => changeLevel.mutateAsync({ id: card.question!.id, level })}
                  />
                ) : (
                  <MemeCard moment={card.moment!} onChoose={(body) => choose.mutate({ id: card.moment!.id, body })} />
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyState
          icon="💬"
          title={onlyOpen ? "Вопросов без ответа нет" : "Вопросов пока нет"}
          hint={filter === "all" ? "Они появляются, когда агент смотрит стрим и чего-то не понимает." : "На этом уровне пусто. Посмотрите другие."}
        />
      )}
      <ShowMore rest={shown.rest} onMore={shown.more} />
    </div>
  );
}
